"""Pure logic shared by both engines: settings, names, replay-segment selection, freeze/speed monitors."""

import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


SEGMENT_SECONDS = 1            # replay buffer segment length


MIN_BUFFER, MAX_BUFFER = 15, 600


RESOLUTIONS: Dict[str, Optional[Tuple[int, int]]] = {
    "native": None,
    "1280x800": (1280, 800),
    "1280x720": (1280, 720),
    "960x600": (960, 600),
    "854x480": (854, 480),
}


FORMATS = ("mkv", "mp4")


ENGINES = ("auto", "gstreamer", "ffmpeg")


FFMPEG_ENCODER_PREFERENCE = ("h264_vaapi", "libx264", "libopenh264")   # hardware first


DEFAULT_SIZE = (1280, 800)     # Steam Deck screen, used if the real size cannot be probed


DEFAULT_FPS = 60


FPS_CHOICES = (0, 30)          # 0 = native


ENCODER_PREFERENCE = ("vaapih264enc", "vah264enc", "x264enc")   # the fork's own (proven) encoder first, software last


AAC_PREFERENCE = ("avenc_aac", "fdkaacenc", "voaacenc")


DEFAULTS: Dict[str, Any] = {
    "format": "mkv",
    "output_folder": "",           # "" -> <home>/Videos
    "resolution": "native",
    "fps": 0,
    "video_kbps": 12000,
    "audio_kbps": 192,
    "encoder": "auto",
    "engine": "auto",              # auto: GStreamer encoder if present, else GStreamer capture + ffmpeg encode
    "game_audio": True,
    "mic_enabled": False,
    "mic_source": "",              # "" -> system default source
    "mic_gain_db": 6.0,
    "mic_cleanup": True,           # echo-cancel (webrtc) on the mic
    "noise_reduction": 50,         # only used when the rnnoise ladspa plugin is present
    "replay_enabled": False,
    "buffer_seconds": 120,
    "clip_seconds": 30,
    "min_free_mb": 1024,
    "hotkey_clip": True,           # Steam + Start = save clip (same combo as the original plugin)
    "hotkey_record": False,        # Steam + Select = start/stop recording (button index unverified)
    "stall_seconds": 5,            # restart capture if no new data for this long; 0 = off
    "audio_offset_ms": 0,          # ffmpeg engine: delay (+) or advance (-) the sound against the picture
    "low_priority": True,          # run the capture/encode processes at a lower CPU priority than the game
    "encoder_threads": 2,          # libx264 only: threads (fewer = less CPU taken from the game)
    "buffer_only_in_game": True,   # replay buffer pauses while no game is running (saves power/GPU at the Steam home screen)
    "auto_step_down": True,        # encoder too slow -> automatically 30 fps, then smaller picture, then lower bitrate
    "hid_hotkeys": True,           # read the Deck's controller directly, so shortcuts also work inside games
}


def clamp_num(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return max(lo, min(hi, v))


def sanitize_settings(raw: Any) -> Dict[str, Any]:
    """Always returns a complete, valid settings dict, whatever junk is in `raw`."""
    raw = raw if isinstance(raw, dict) else {}
    s = dict(DEFAULTS)
    if raw.get("format") in FORMATS:
        s["format"] = raw["format"]
    if isinstance(raw.get("output_folder"), str):
        s["output_folder"] = raw["output_folder"].strip()[:400]
    if raw.get("resolution") in RESOLUTIONS:
        s["resolution"] = raw["resolution"]
    if raw.get("fps") in FPS_CHOICES:
        s["fps"] = raw["fps"]
    s["video_kbps"] = int(clamp_num(raw.get("video_kbps"), 1000, 60000, DEFAULTS["video_kbps"]))
    s["audio_kbps"] = int(clamp_num(raw.get("audio_kbps"), 64, 320, DEFAULTS["audio_kbps"]))
    enc = raw.get("encoder", "auto")
    s["encoder"] = enc if enc == "auto" or enc in ENCODER_PREFERENCE or enc in FFMPEG_ENCODER_PREFERENCE else "auto"
    s["engine"] = raw.get("engine") if raw.get("engine") in ENGINES else "auto"
    for k in ("game_audio", "mic_enabled", "mic_cleanup", "replay_enabled", "hotkey_clip", "hotkey_record"):
        if isinstance(raw.get(k), bool):
            s[k] = raw[k]
    if isinstance(raw.get("mic_source"), str):
        s["mic_source"] = raw["mic_source"].strip()[:200]
    s["mic_gain_db"] = round(clamp_num(raw.get("mic_gain_db"), -20, 30, DEFAULTS["mic_gain_db"]), 1)
    s["noise_reduction"] = int(clamp_num(raw.get("noise_reduction"), 0, 100, DEFAULTS["noise_reduction"]))
    s["buffer_seconds"] = int(clamp_num(raw.get("buffer_seconds"), MIN_BUFFER, MAX_BUFFER, DEFAULTS["buffer_seconds"]))
    s["clip_seconds"] = int(clamp_num(raw.get("clip_seconds"), 5, MAX_BUFFER, DEFAULTS["clip_seconds"]))
    st = int(clamp_num(raw.get("stall_seconds"), 0, 60, DEFAULTS["stall_seconds"]))
    s["stall_seconds"] = 0 if st == 0 else max(3, st)
    s["encoder_threads"] = int(clamp_num(raw.get("encoder_threads"), 1, 8, DEFAULTS["encoder_threads"]))
    for k in ("low_priority", "hid_hotkeys", "buffer_only_in_game", "auto_step_down"):
        if isinstance(raw.get(k), bool):
            s[k] = raw[k]
    s["audio_offset_ms"] = int(clamp_num(raw.get("audio_offset_ms"), -1000, 1000, 0))
    s["min_free_mb"] = int(clamp_num(raw.get("min_free_mb"), 100, 100000, DEFAULTS["min_free_mb"]))
    return s


_BAD = re.compile(r"[^\w\s.\-()&']", re.UNICODE)


def safe_name(name: Any, fallback: str = "Recording", maxlen: int = 60) -> str:
    """A game title as a safe file-name stem: no separators, quotes, shell characters or dots-only."""
    text = _BAD.sub(" ", str(name if name is not None else ""))
    text = re.sub(r"\s+", " ", text).strip(" .-_")
    text = text[:maxlen].strip(" .-_")
    return text or fallback


def unique_path(folder: str, stem: str, ext: str) -> str:
    base = os.path.join(folder, f"{stem}.{ext}")
    if not os.path.exists(base):
        return base
    i = 2
    while os.path.exists(os.path.join(folder, f"{stem}_{i}.{ext}")):
        i += 1
    return os.path.join(folder, f"{stem}_{i}.{ext}")


def staging_path_ok(path: str) -> bool:
    """gst-launch parses its argv: a staging path must not need quoting."""
    return bool(path) and re.fullmatch(r"[A-Za-z0-9_./+%\-]+", path) is not None


class SpeedMonitor:
    """True once when the encoder has been slower than `threshold` x real time for `seconds` in a row."""

    def __init__(self, threshold: float, seconds: float, grace: float, now: float):
        self.threshold, self.seconds, self.grace, self.start = threshold, seconds, grace, now
        self.slow_since: Optional[float] = None
        self.fired = False

    def update(self, speed: Optional[float], now: float) -> bool:
        if speed is None or now - self.start < self.grace or self.fired:
            return False
        if speed >= self.threshold:
            self.slow_since = None
            return False
        if self.slow_since is None:
            self.slow_since = now
        if now - self.slow_since >= self.seconds:
            self.fired = True
            return True
        return False


def minutes_left(free_bytes: float, reserve_mb: float, video_kbps: float, audio_kbps: float) -> int:
    """Recording time that still fits on the disk, keeping `reserve_mb` free."""
    usable = max(0.0, float(free_bytes) - float(reserve_mb) * 1024 * 1024)
    per_second = (float(video_kbps) + float(audio_kbps)) * 1000 / 8
    return int(usable / per_second / 60) if per_second > 0 else 0


def estimate_buffer_mb(video_kbps: int, audio_kbps: int, seconds: int) -> int:
    return int(math.ceil((video_kbps + audio_kbps) * seconds / 8 / 1024))


def select_segments(files: Sequence[Tuple[str, float]], seconds: int, now: float) -> List[str]:
    """files = [(path, mtime)]. Returns paths oldest->newest that cover the last `seconds`.

    A segment's mtime is when it was finished, so every segment finished inside the window is taken
    (the clip can be up to one segment longer than asked, never shorter). Independent of the real segment
    length (splitmuxsink cuts on keyframes). The newest file is still being written, so it is left out
    whenever an older, finished one exists."""
    ordered = sorted(files, key=lambda t: (t[1], t[0]))
    if len(ordered) > 1:
        ordered = ordered[:-1]
    return [p for p, m in ordered if now - m <= seconds]


def concat_list(paths: Sequence[str]) -> str:
    """ffmpeg concat-demuxer list with proper quoting (' -> '\\'')."""
    lines = []
    for p in paths:
        lines.append("file '" + p.replace("'", "'\\''") + "'")
    return "\n".join(lines) + "\n"


def parse_pactl_modules(text: str, marker: str) -> List[int]:
    """Module ids from `pactl list short modules` whose line mentions `marker`."""
    ids = []
    for line in text.splitlines():
        parts = line.split("\t") if "\t" in line else line.split(None, 2)
        if len(parts) >= 2 and marker in line and parts[0].strip().isdigit():
            ids.append(int(parts[0]))
    return ids


def parse_source_names(text: str) -> List[str]:
    """Names from `pactl list short sources`, minus monitors and our own virtual devices."""
    out = []
    for line in text.splitlines():
        parts = line.split("\t") if "\t" in line else line.split()
        if len(parts) < 2:
            continue
        name = parts[1]
        if ".monitor" in name or name.endswith("monitor") or "Finally-Rec" in name:
            continue
        out.append(name)
    return out


def woke_from_sleep(wall_gap: float, expected: float, slack: float = 10.0) -> bool:
    """Wall clock jumped much further than our own sleep: the Deck suspended in between."""
    return wall_gap > expected + slack


class StallDetector:
    """Detects a capture that is still 'running' but no longer produces data (hung encoder / pipewire).

    `marker` is anything that changes while data flows (file size, or (segment count, total size)).
    Not evaluated until `grace` seconds after start (pipeline preroll)."""

    def __init__(self, seconds: float, grace: float, now: float):
        self.seconds, self.grace = float(seconds), float(grace)
        self.start = self.last_change = now
        self.last: Any = None
        self.ever_grew = False

    def reset(self, now: float) -> None:
        """e.g. after a suspend: the clock jumped, do not count that as a stall."""
        self.start = self.last_change = now

    def update(self, marker: Any, now: float) -> bool:
        if self.last is None:
            self.last, self.last_change = marker, now
        elif marker != self.last:
            self.last, self.last_change = marker, now
            self.ever_grew = True
        return (now - self.start) >= self.grace and (now - self.last_change) >= self.seconds


def format_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ----------------------------------------------------------------------------- hardware buttons / CPU use
# Button bits of the Steam Deck controller state (same values the original decky-recorder used for Steam=13,
# Start=14, Select=12 and that SDL's Steam Deck driver documents). UNVERIFIED on a real Deck until Diagnostics says so.
BTN_VIEW, BTN_STEAM, BTN_MENU = 0x1000, 0x2000, 0x4000


def parse_deck_report(data: bytes) -> Optional[int]:
    """Button bitmask from a Deck HID state report (01 00 09 ..., buttons = 8 bytes at offset 8), else None."""
    if len(data) < 16 or data[0] != 0x01 or data[1] != 0x00 or data[2] != 0x09:
        return None
    return int.from_bytes(data[8:16], "little")


class HotkeyDetector:
    """Steam + Menu(Start) = 'clip', Steam + View(Select) = 'record'. Fires once when the combo is pressed."""

    def __init__(self, cooldown: float = 2.0):
        self.cooldown = cooldown
        self.held: Optional[str] = None
        self.last = -1e9

    def update(self, buttons: int, now: float, clip: bool = True, record: bool = False) -> Optional[str]:
        active = None
        if buttons & BTN_STEAM:
            if clip and buttons & BTN_MENU:
                active = "clip"
            elif record and buttons & BTN_VIEW:
                active = "record"
        fire = None
        if active and active != self.held and now - self.last >= self.cooldown:
            fire, self.last = active, now
        self.held = active
        return fire


def parse_stat_ticks(text: str) -> Optional[int]:
    """utime + stime (clock ticks) from /proc/<pid>/stat. The command name may contain spaces/brackets."""
    try:
        fields = text[text.rindex(")") + 2:].split()
        return int(fields[11]) + int(fields[12])
    except (ValueError, IndexError):
        return None


def cpu_percent(tick_delta: float, seconds: float, hz: float) -> float:
    """CPU use in percent of ONE core (100 = one full core; the Deck has 8 threads)."""
    return 0.0 if seconds <= 0 or hz <= 0 else 100.0 * tick_delta / hz / seconds


def step_down(settings: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Next cheaper setting when the encoder cannot keep up: (label, settings patch), or None when nothing is left.
    Order = least visible quality loss first: frame rate, then picture size, then bitrate."""
    if settings.get("fps") in (0, 60):
        return "30 fps", {"fps": 30}
    if settings.get("resolution") == "native":
        return "960x600", {"resolution": "960x600"}
    if int(settings.get("video_kbps", 0)) > 5000:
        return "5 Mbps", {"video_kbps": 5000}
    return None


_GST_PREFIX = re.compile(r"^\d+:\d{2}:\d{2}\.\d+\s+\d+\s+0x[0-9a-f]+\s+(?:[A-Z]+\s+)?")
_GENERIC = re.compile(r"(Failed to set pipeline to|doesn't want to pause|Setting pipeline to|Freeing pipeline|Execution ended)", re.I)


def best_error_lines(text: str, limit: int = 3) -> str:
    """The informative lines of a tool's stderr (not the generic 'Failed to set pipeline to PAUSED' at the end)."""
    lines = [_GST_PREFIX.sub("", l).strip() for l in text.splitlines() if l.strip()]
    good = [l for l in lines if re.search(r"ERROR|WARN|error|fail|could not|cannot|no more|refused|denied|not found", l, re.I)
            and not _GENERIC.search(l)]
    pick = good[-limit:] or [l for l in lines if not _GENERIC.search(l)][-limit:] or lines[-1:]
    return " | ".join(pick)[:300] if pick else "no output"


def parse_pw_video_nodes(text: str) -> List[str]:
    """'id:name (media.class)' for every PipeWire video node, from `pw-dump` JSON."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out: List[str] = []
    for o in data if isinstance(data, list) else []:
        props = ((o.get("info") or {}).get("props") or {}) if isinstance(o, dict) else {}
        mc = str(props.get("media.class", ""))
        if mc.startswith("Video/"):
            out.append(f"{o.get('id')}:{props.get('node.name') or props.get('node.description') or '?'} ({mc})")
    return out
