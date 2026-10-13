"""Pure logic for the all-GStreamer pipeline (encoder choice, gst-launch argv)."""

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from core_base import AAC_PREFERENCE, ENCODER_PREFERENCE, RESOLUTIONS, SEGMENT_SECONDS, staging_path_ok


def pick_encoder(available: Iterable[str], wanted: str = "auto") -> Optional[str]:
    have = set(available)
    if wanted != "auto" and wanted in have:
        return wanted
    for e in ENCODER_PREFERENCE:
        if e in have:
            return e
    return None


def pick_aac(available: Iterable[str]) -> Optional[str]:
    have = set(available)
    for e in AAC_PREFERENCE:
        if e in have:
            return e
    return None


def parse_gst_props(inspect_output: str) -> Set[str]:
    """Property names from `gst-inspect-1.0 <element>` output."""
    props: Set[str] = set()
    in_props = False
    for line in inspect_output.splitlines():
        if re.match(r"^Element Properties:", line):
            in_props = True
            continue
        if in_props:
            if re.match(r"^\S", line) and not line.startswith(" "):
                break                      # next top-level section
            m = re.match(r"^\s{2}([a-z][a-z0-9\-]*)\s{2,}:", line)
            if m:
                props.add(m.group(1))
    return props


def video_chain(encoder: str, props: Set[str], s: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Tokens (gst-launch syntax, '!'-separated) from after pipewiresrc up to and including h264parse.
    Returns (tokens, warnings)."""
    warns: List[str] = []
    toks: List[str] = []
    res = RESOLUTIONS.get(s["resolution"])
    fps = s["fps"]
    kbps = str(int(s["video_kbps"]))
    if fps:
        toks += ["videorate", "drop-only=true", f"max-rate={fps}", "!"]
    if encoder == "vaapih264enc":
        post = ["vaapipostproc"]
        if res:
            post += [f"width={res[0]}", f"height={res[1]}"]
        toks += post + ["!", "queue", "!", "vaapih264enc"]
        if "rate-control" in props:
            toks.append("rate-control=vbr")
        if "bitrate" in props:
            toks.append(f"bitrate={kbps}")
        if "keyframe-period" in props:
            toks.append("keyframe-period=30")
    elif encoder == "vah264enc":
        toks += ["vapostproc", "!", "queue", "!", "vah264enc"]
        if res:
            warns.append("Resolution scaling is not applied with the 'va' encoder (unverified caps); recording native size.")
        if "rate-control" in props:
            toks.append("rate-control=vbr")
        if "bitrate" in props:
            toks.append(f"bitrate={kbps}")
        if "key-int-max" in props:
            toks.append("key-int-max=30")
    elif encoder == "x264enc":
        toks += ["queue", "!", "videoconvert", "!"]
        if res:
            toks += ["videoscale", "!", f"video/x-raw,width={res[0]},height={res[1]}", "!"]
        toks += ["x264enc", "tune=zerolatency", "speed-preset=ultrafast", f"bitrate={kbps}", "key-int-max=30"]
    else:
        raise ValueError(f"unknown encoder {encoder!r}")
    toks += ["!", "h264parse"]
    return toks, warns


def audio_chain(aac: str, audio_kbps: int, device: str, source: Optional[List[str]] = None) -> List[str]:
    bitrate = int(audio_kbps) * 1000
    src = source if source is not None else ["pulsesrc", f"device={device}"]
    return src + ["!", "audio/x-raw,channels=2", "!", "audioconvert", "!",
                  "audioresample", "!", aac, f"bitrate={bitrate}", "!", "aacparse", "!", "queue"]


def build_gst_argv(*, encoder: str, props: Set[str], split_props: Set[str], aac: Optional[str],
                   settings: Dict[str, Any], audio_device: Optional[str], rolling: bool,
                   out_path: str, segment_pattern: str = "", buffer_seconds: int = 120,
                   source: Optional[List[str]] = None,
                   audio_source: Optional[List[str]] = None,
                   split_variant: str = "original",
                   src_props: Optional[Set[str]] = None) -> Tuple[List[str], List[str]]:
    """argv for gst-launch-1.0 (without the binary name). Never goes through a shell."""
    if source is not None:
        src = source
    else:
        src = ["pipewiresrc", "do-timestamp=true"]
        if src_props and "keepalive-time" in src_props:
            # resend the last frame once a second: a static screen keeps producing data (same option GNOME Shell's
            # screen recorder uses), so the freeze check and the muxer never wait on an idle screen
            src.append("keepalive-time=1000")
    chain, warns = video_chain(encoder, props, settings)
    argv: List[str] = ["-e"] + src + ["!"] + chain + ["!"]
    if rolling:
        if not staging_path_ok(segment_pattern):
            raise ValueError("segment path needs quoting; refusing")
        max_files = int(math.ceil(buffer_seconds / SEGMENT_SECONDS)) + 4
        argv += ["splitmuxsink", "name=mux"]
        if split_variant == "original":
            # exactly what the working decky-recorder fork passes to splitmuxsink
            argv += ["muxer=matroskamux", "muxer-pad-map=x-pad-map,audio=vid"]
        elif split_variant == "factory":
            argv += ["muxer-factory=matroskamux"]
        else:
            raise ValueError(f"unknown split variant {split_variant!r}")
        argv += [f"location={segment_pattern}", f"max-size-time={SEGMENT_SECONDS * 1000000000}",
                 f"max-files={max_files}"]
        if "send-keyframe-requests" in split_props:
            argv.append("send-keyframe-requests=true")
    else:
        if not staging_path_ok(out_path):
            raise ValueError("staging path needs quoting; refusing")
        argv += ["matroskamux", "name=mux", "!", "filesink", f"location={out_path}"]
    if audio_device and aac:
        argv += audio_chain(aac, settings["audio_kbps"], audio_device, audio_source) + ["!", "mux.audio_0"]
    elif audio_device and not aac:
        warns.append("No AAC encoder found; recording without audio.")
    return argv, warns
