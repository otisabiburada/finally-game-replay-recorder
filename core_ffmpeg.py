"""Pure logic for the ffmpeg engine (GStreamer feeds raw frames, ffmpeg encodes) and engine choice."""

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from core_base import DEFAULT_SIZE, FFMPEG_ENCODER_PREFERENCE, RESOLUTIONS, SEGMENT_SECONDS, staging_path_ok


def parse_ffmpeg_encoders(text: str) -> Set[str]:
    """Encoder names from `ffmpeg -encoders` (lines like ' V....D libx264   description')."""
    out: Set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^\s([VAS][\.FSXBD]{5})\s+(\S+)\s", line)
        if m:
            out.add(m.group(2))
    return out


def parse_ffmpeg_devices(text: str) -> Set[str]:
    """Input-capable device names from `ffmpeg -devices` (lines like ' D  lavfi', ' DE pulse')."""
    out: Set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^\s([D\s][E\s])\s(\S+)\s", line)
        if m and "D" in m.group(1):
            out.add(m.group(2))
    return out


def pick_ffmpeg_encoder(usable: Iterable[str], wanted: str = "auto") -> Optional[str]:
    have = set(usable)
    if wanted != "auto" and wanted in have:
        return wanted
    for e in FFMPEG_ENCODER_PREFERENCE:
        if e in have:
            return e
    return None


def pick_engine(gst_encoders: Iterable[str], gst_aac: Optional[str], ffmpeg_usable: Iterable[str],
                ffmpeg_has_pulse: bool, wanted: str, gst_has_capture: bool = True) -> Tuple[Optional[str], str]:
    """-> (engine, reason). 'gst' = all-GStreamer pipeline, 'ffmpeg' = GStreamer capture + ffmpeg encode."""
    gst_ok = bool(list(gst_encoders)) and gst_aac is not None
    ff_ok = bool(list(ffmpeg_usable)) and gst_has_capture
    if wanted == "gstreamer":
        return ("gst", "forced") if list(gst_encoders) else (None, "GStreamer has no H.264 encoder (engine forced to GStreamer).")
    if wanted == "ffmpeg":
        return ("ffmpeg", "forced") if ff_ok else (None, "ffmpeg has no usable H.264 encoder (engine forced to ffmpeg).")
    if gst_ok:
        return "gst", "GStreamer has an H.264 and an AAC encoder"
    if ff_ok:
        return "ffmpeg", "GStreamer lacks an encoder, using ffmpeg" + ("" if ffmpeg_has_pulse else " (no pulse input: video only)")
    return None, "No usable H.264 encoder in GStreamer or ffmpeg."


def parse_caps_size(text: str) -> Optional[Tuple[int, int]]:
    """Frame size from `gst-launch-1.0 -v` output ('... width=(int)1280, height=(int)800 ...')."""
    best = None
    for m in re.finditer(r"width=\(int\)(\d+),\s*height=\(int\)(\d+)", text):
        w, h = int(m.group(1)), int(m.group(2))
        if w >= 64 and h >= 64:
            best = (w, h)
    return best


def even(n: int) -> int:
    return n - (n % 2)


def target_size(settings: Dict[str, Any], probed: Optional[Tuple[int, int]]) -> Tuple[int, int]:
    """Recording size. A preset is a box the picture must fit into: the screen's aspect ratio is kept (a 16:10 Deck
    screen in the 1280x720 preset becomes 1152x720, not a stretched 1280x720) and nothing is scaled up."""
    nw, nh = probed or DEFAULT_SIZE
    box = RESOLUTIONS.get(settings["resolution"])
    if box:
        scale = min(box[0] / nw, box[1] / nh, 1.0)
        nw, nh = int(round(nw * scale)), int(round(nh * scale))
    return max(64, even(nw)), max(64, even(nh))


def build_feeder_argv(size: Tuple[int, int], fps: int, src_props: Optional[Set[str]] = None,
                      source: Optional[List[str]] = None) -> List[str]:
    """gst-launch argv: screen -> raw NV12 frames at a constant rate on stdout (fd 1)."""
    src = source if source is not None else ["pipewiresrc", "do-timestamp=true"] + (
        ["keepalive-time=1000"] if src_props and "keepalive-time" in src_props else [])
    return ["-e", "-q"] + src + ["!", "queue", "max-size-buffers=8", "!", "videoconvert", "!", "videoscale", "!",
            "videorate", "!", f"video/x-raw,format=NV12,width={size[0]},height={size[1]},framerate={fps}/1", "!",
            "fdsink", "fd=1"]


def build_ffmpeg_argv(*, encoder: str, settings: Dict[str, Any], size: Tuple[int, int], fps: int,
                      audio_input: Optional[List[str]], rolling: bool, out_path: str = "",
                      segment_pattern: str = "", buffer_seconds: int = 120, aac_name: str = "aac",
                      vaapi_device: str = "/dev/dri/renderD128", video_input: Optional[List[str]] = None,
                      extra_output: Optional[List[str]] = None, progress_path: str = "") -> List[str]:
    """argv for ffmpeg (without the binary name). Raw video arrives on stdin; audio is a separate input."""
    kbps = int(settings["video_kbps"])
    gop = max(1, int(fps))                                   # one key frame per second -> 1 s segments
    a: List[str] = ["-hide_banner", "-loglevel", "warning", "-y"]
    if progress_path:
        if not staging_path_ok(progress_path):
            raise ValueError("progress path needs quoting; refusing")
        a += ["-progress", progress_path, "-stats_period", "0.5"]    # frame counter for the freeze check
    if encoder == "h264_vaapi":
        a += ["-vaapi_device", vaapi_device]
    a += video_input if video_input is not None else [
        "-f", "rawvideo", "-pix_fmt", "nv12", "-video_size", f"{size[0]}x{size[1]}", "-framerate", str(fps), "-i", "pipe:0"]
    if audio_input:
        off = int(settings.get("audio_offset_ms") or 0)
        if off:
            a += ["-itsoffset", f"{off / 1000:.3f}"]       # applies to the NEXT input, i.e. the audio
        a += audio_input
    a += ["-map", "0:v:0"] + (["-map", "1:a:0"] if audio_input else [])
    if encoder == "h264_vaapi":
        a += ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-g", str(gop)]
    elif encoder == "libx264":
        a += ["-c:v", "libx264", "-threads", str(int(settings.get("encoder_threads") or 2)),
              "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
              "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k", "-g", str(gop)]
    elif encoder == "libopenh264":
        a += ["-c:v", "libopenh264", "-pix_fmt", "yuv420p", "-b:v", f"{kbps}k", "-g", str(gop)]
    else:
        raise ValueError(f"unknown ffmpeg encoder {encoder!r}")
    if audio_input:
        a += ["-c:a", aac_name, "-b:a", f"{int(settings['audio_kbps'])}k", "-ac", "2"]
    a += extra_output or []
    if rolling:
        if not staging_path_ok(segment_pattern):
            raise ValueError("segment path needs quoting; refusing")
        wrap = int(math.ceil(buffer_seconds / SEGMENT_SECONDS)) + 4
        a += ["-f", "segment", "-segment_time", str(SEGMENT_SECONDS), "-segment_format", "matroska",
              "-segment_wrap", str(wrap), "-reset_timestamps", "1", segment_pattern]
    else:
        if not staging_path_ok(out_path):
            raise ValueError("staging path needs quoting; refusing")
        a += ["-f", "matroska", out_path]
    return a


def parse_progress_frame(text: str) -> Optional[int]:
    """Last 'frame=N' from an ffmpeg -progress file (encoded video frames so far)."""
    last = None
    for m in re.finditer(r"^frame=\s*(\d+)\s*$", text, re.M):
        last = int(m.group(1))
    return last


def parse_progress_speed(text: str) -> Optional[float]:
    """Last 'speed=0.97x' from an ffmpeg -progress file (1.0 = exactly real time). 'N/A' -> None."""
    last = None
    for m in re.finditer(r"^speed=\s*([0-9.]+)x\s*$", text, re.M):
        try:
            last = float(m.group(1))
        except ValueError:
            pass
    return last
