"""Finally Game Recorder - video recorder + replay buffer for Decky Loader (Game Mode).

* Record: manual start/stop, saved as mkv or mp4.
* Replay buffer: always keeps the last N seconds in RAM (/dev/shm); "Save clip" writes the last X seconds.
* Capture: GStreamer (pipewiresrc from gamescope). Encoding by GStreamer if it has an encoder, otherwise by the
  system ffmpeg (VAAPI hardware if it works, else libx264).
* Audio: game audio (+ optional microphone) through a private virtual sink.

Layout (all mixed into Plugin below, so Decky still sees one `Plugin` class):
  recorder_core.py   pure logic, no Decky/subprocess (unit-tested)
  finally_util.py      paths, clean subprocess env, command runner, Game Mode detection
  finally_adapt.py     adaptive cost control: pause buffer without a game, step quality down when too slow
  finally_audio.py     virtual sink + microphone chain
  finally_caps.py      what this system can do: GStreamer/ffmpeg probing, GStreamer environment
  finally_capture.py   start/stop of the capture processes
  finally_save.py      staging file -> final recording
  finally_watchdog.py  crash / freeze / sleep / Game Mode supervision, orphan recovery
  finally_files.py     replay clips and the recordings folder
  finally_sensors.py   GPU load + power readout and the rough 'what recording costs' comparison
  finally_playback.py  watch a saved clip: remux + tiny 127.0.0.1 HTTP server for the player page
  finally_hotkeys.py   reads the Deck's controller directly for Steam+Start / Steam+Select (works inside games)
  finally_diag.py      log tail and the on-device self-test
  main.py            lifecycle, settings, status/events and the public API

Nothing here goes through a shell: every external command is an argv list.
"""

import asyncio
import os
import sys
import time
import traceback
from typing import Any, Deque, Dict, List, Optional, Tuple
import collections
import glob
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # our sibling modules (before importing them)

import decky  # noqa: E402
import recorder_core as core  # noqa: E402
import finally_util as util  # noqa: E402
from finally_util import (EVENT, _chown, _game_mode_signal, _in_game_mode, _home, _makedirs, _run, _user_ids, log)  # noqa: E402,F401
from finally_adapt import AdaptMixin  # noqa: E402
from finally_audio import AudioMixin  # noqa: E402
from finally_caps import CapsMixin  # noqa: E402
from finally_capture import CaptureMixin  # noqa: E402
from finally_diag import DiagMixin  # noqa: E402
from finally_files import FilesMixin  # noqa: E402
from finally_hotkeys import HotkeyMixin  # noqa: E402
from finally_playback import PlaybackMixin  # noqa: E402
from finally_sensors import SensorsMixin  # noqa: E402
from finally_save import SaveMixin  # noqa: E402
from finally_watchdog import WatchdogMixin  # noqa: E402


class Plugin(AdaptMixin, AudioMixin, CapsMixin, CaptureMixin, SaveMixin, WatchdogMixin, FilesMixin, HotkeyMixin, PlaybackMixin, SensorsMixin, DiagMixin):
    WATCHDOG_INTERVAL = 2.0
    FFMPEG_AUDIO_OVERRIDE: Optional[List[str]] = None   # tests only: replaces the pulse input
    WARM_CAPS_AFTER: Optional[float] = 5.0   # seconds after load to probe the system in the background (None = off, tests)
    STOP_SCALE = 1.0           # multiplies the waits before escalating SIGINT -> SIGTERM -> SIGKILL (tests use < 1)
    SPEED_THRESHOLD = 0.9      # ffmpeg engine: below this x real time the encoder is too slow
    SPEED_SECONDS = 10.0       # ... for this long before warning
    STALL_GRACE = 8.0          # seconds after start before a lack of data counts as a stall
    ERROR_CLEAR_SECONDS = 20.0 # an error state falls back to idle after this long
    LEAVE_TICKS = 3            # consecutive watchdog ticks outside Game Mode before capture is stopped

    async def _main(self):
        self.lock = asyncio.Lock()
        self.s: Dict[str, Any] = core.sanitize_settings({})
        self.state = "idle"                  # idle | recording | buffering | saving | error
        self.mode: Optional[str] = None      # manual | replay
        self.proc: Optional[asyncio.subprocess.Process] = None       # the process that writes the file
        self.src_proc: Optional[asyncio.subprocess.Process] = None   # ffmpeg engine: gst-launch feeding raw frames
        self.engine = ""
        self.size_cache: Tuple[float, Optional[Tuple[int, int]]] = (0.0, None)
        self.stderr_tail: Deque[str] = collections.deque(maxlen=60)
        self.notes: Deque[str] = collections.deque(maxlen=120)
        self.pa_ids: List[int] = []
        self.staging: str = ""
        self.final_path: str = ""
        self.game: str = ""
        self.started_at = 0.0
        self.last_saved: Dict[str, Any] = {}
        self.last_error = ""
        self.warnings: List[str] = []
        self.encoder = ""
        self.resume_replay = False
        self.split_variant = "original"
        self.verify_task: Optional[asyncio.Task] = None
        self.last_clip_time = 0.0
        self._variant_retried = False
        self.progress_path = ""
        self.speed: Optional[float] = None
        self.cpu: Optional[int] = None
        self._cpu_prev: Optional[Tuple[float, int]] = None
        self._hid_init()
        self._adapt_init()
        self._sensors_init()
        self._play_init()
        self.speed_mon: Optional[core.SpeedMonitor] = None
        self.error_since = 0.0
        self.not_gm_ticks = 0
        self.rearm_times: List[float] = []
        self.stall: Optional[core.StallDetector] = None
        self.stall_times: List[float] = []
        self.nogrow_streak = 0
        self.caps: Dict[str, Any] = {}
        self.caps_time = 0.0
        self.restart_times: List[float] = []
        self.pending: List[asyncio.Task] = []
        self.watchdog_task: Optional[asyncio.Task] = None
        uid, _ = _user_ids()
        cache = os.path.join(_home(), ".cache", "finally-game-recorder")
        if not core.staging_path_ok(cache):
            cache = f"/tmp/finally-game-recorder-{uid}"
        self.stage_dir = os.path.join(cache, "staging")
        self.seg_dir = os.environ.get("FINALLY_REC_SEG_DIR") or f"/dev/shm/finally-game-recorder-{uid}"
        self.settings_file = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "recorder.json")
        self._load_settings()
        try:
            await self._cleanup_orphans(recover=True)
        except Exception:
            self._note("orphan cleanup failed: " + traceback.format_exc(limit=2))
        self.watchdog_task = asyncio.get_event_loop().create_task(self._watchdog())
        self._hid_start()
        self.warm_task = asyncio.get_event_loop().create_task(self._warm_caps()) if self.WARM_CAPS_AFTER is not None else None
        if self.s["replay_enabled"]:
            asyncio.get_event_loop().create_task(self._delayed_replay_start())

    async def _unload(self):
        self._note("unload")
        self._hid_stop()
        if getattr(self, "warm_task", None):
            self.warm_task.cancel()
        await self._play_stop()
        for task in (self.watchdog_task, self.verify_task):
            if task:
                task.cancel()
        for task in (self.watchdog_task, self.verify_task):
            if task:
                try:
                    await task
                except BaseException:
                    pass
        try:
            async with self.lock:
                await self._stop_capture_locked(finalize=(self.mode == "manual"))
        except Exception:
            self._note("unload stop failed: " + traceback.format_exc(limit=2))
        # let a recording that is being converted finish, so a reload never loses the file
        if self.pending:
            try:
                await asyncio.wait_for(asyncio.gather(*list(self.pending), return_exceptions=True), timeout=120)
            except Exception:
                self._note("unload: pending save did not finish in time")
        await self._unload_audio()

    def _note(self, msg: str) -> None:
        line = time.strftime("%H:%M:%S ") + msg
        self.notes.append(line)
        try:
            log.info(msg)
        except Exception:
            pass

    def _load_settings(self) -> None:
        raw = {}
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            pass
        except Exception:
            self._note("settings unreadable, using defaults")
        self.s = core.sanitize_settings(raw)

    def _save_settings(self) -> None:
        try:
            _makedirs(os.path.dirname(self.settings_file))
            tmp = self.settings_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.s, f, indent=1)
            os.replace(tmp, self.settings_file)
        except Exception:
            self._note("could not save settings: " + traceback.format_exc(limit=1))

    def _out_dir(self) -> str:
        d = self.s["output_folder"] or os.path.join(_home(), "Videos")
        return d

    async def get_settings(self) -> Dict[str, Any]:
        return dict(self.s)

    async def set_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Merge + validate. Settings that change a running capture restart it."""
        old = dict(self.s)
        merged = dict(self.s)
        if isinstance(patch, dict):
            merged.update(patch)
        self.s = core.sanitize_settings(merged)
        self._save_settings()
        capture_keys = ("resolution", "fps", "video_kbps", "audio_kbps", "encoder", "game_audio", "mic_enabled",
                        "mic_source", "mic_gain_db", "mic_cleanup", "noise_reduction", "buffer_seconds")
        changed = [k for k in capture_keys if old.get(k) != self.s.get(k)]
        if old.get("hid_hotkeys") != self.s.get("hid_hotkeys"):
            self._hid_start()
        if self.state == "buffering" and changed and self.s["replay_enabled"]:
            if changed == ["mic_gain_db"]:
                await self._apply_mic_gain()
            else:
                await self._restart_replay("settings changed")
        elif self.state == "recording" and changed:
            if changed == ["mic_gain_db"]:
                await self._apply_mic_gain()
            else:
                self.warnings = list(self.warnings) + ["Setting changes apply to the next recording."]
        return dict(self.s)

    async def get_options(self) -> Dict[str, Any]:
        caps = await self._capabilities()
        sources: List[str] = []
        if caps.get("pactl"):
            rc, out, _ = await _run([caps["pactl"], "list", "short", "sources"], 5)
            if rc == 0:
                sources = core.parse_source_names(out)
        rnn = [os.path.join(_home(), "homebrew", "data", "decky-recorder", "librnnoise_ladspa.so"),
               os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "librnnoise_ladspa.so")]
        locations = [{"label": "Videos (internal)", "path": os.path.join(_home(), "Videos")}]
        for m in sorted(glob.glob("/run/media/*/*") + glob.glob("/run/media/mmcblk*")):
            if os.path.isdir(m) and os.access(m, os.W_OK):
                locations.append({"label": f"SD/USB: {os.path.basename(m)}", "path": os.path.join(m, "Recordings")})
        cur = self._out_dir()
        if all(l["path"] != cur for l in locations):
            locations.append({"label": cur, "path": cur})
        eng, why = core.pick_engine(caps.get("encoders", []), caps.get("aac"), caps.get("ff_usable", []),
                                    "pulse" in caps.get("ff_devices", []), self.s["engine"], bool(caps.get("elements", {}).get("pipewiresrc")))
        return {"encoders": list(caps.get("encoders", [])) + list(caps.get("ff_usable", [])), "engine": eng or "none",
                "engine_reason": why, "pulse": "pulse" in caps.get("ff_devices", []), "aac": caps.get("aac"), "sources": sources,
                "rnnoise": any(os.path.exists(p) for p in rnn), "locations": locations,
                "have_gst": bool(caps.get("gst_launch")), "have_ffmpeg": bool(caps.get("ffmpeg")),
                "can_record": eng is not None}

    def _snapshot(self) -> Dict[str, Any]:
        elapsed = int(time.time() - self.started_at) if self.state in ("recording", "buffering") else 0
        size = 0
        if self.state == "recording" and self.staging:
            try:
                size = os.path.getsize(self.staging)
            except OSError:
                size = 0
        elif self.state == "buffering":
            try:
                size = sum(os.path.getsize(p) for p in glob.glob(os.path.join(self.seg_dir, "seg_*.mkv")))
            except OSError:
                size = 0
        free = 0
        try:
            d = self._out_dir()
            while d and not os.path.isdir(d):
                d = os.path.dirname(d)
            free = shutil.disk_usage(d or "/").free
        except Exception:
            pass
        return {"state": self.state, "mode": self.mode, "elapsed": elapsed, "size": size,
                "free": free, "paused": self.paused,
                "gpu": self.gpu, "watts": None if self.watts is None else round(self.watts, 1), "power_src": self.power_src,
                "cost": self.cost_report, "speed": self.speed, "cpu": self.cpu,
                "minutes_left": core.minutes_left(free, self.s["min_free_mb"], self.s["video_kbps"], self.s["audio_kbps"]),
                "encoder": self.encoder, "engine": self.engine, "game": self.game, "last_saved": self.last_saved,
                "error": self.last_error, "warnings": self.warnings, "replay": bool(self.s["replay_enabled"]),
                "buffer_fill": min(elapsed, self.s["buffer_seconds"]) if self.state == "buffering" else 0,
                "ts": time.time()}

    async def get_status(self) -> Dict[str, Any]:
        return self._snapshot()

    async def _emit(self, etype: str, **data: Any) -> None:
        try:
            await decky.emit(EVENT, {**data, "type": etype})
        except Exception:
            pass

    def _shm_free_mb(self) -> int:
        try:
            return shutil.disk_usage("/dev/shm").free // (1024 * 1024)
        except Exception:
            return 10 ** 9

    async def _set_state(self, state: str, mode: Optional[str] = None) -> None:
        if state == "error":
            self.error_since = time.time()
        self.state, self.mode = state, mode
        await self._emit("state", status=self._snapshot())

    async def _fail(self, message: str) -> None:
        self.last_error = message
        self._note("ERROR: " + message)
        await self._set_state("error", None)
        await self._emit("error", message=message)

    async def start_recording(self, game: str = "") -> Dict[str, Any]:
        async with self.lock:
            if self.state in ("recording", "saving"):
                return self._snapshot()
            self.resume_replay = False
            if self.mode == "replay":
                self.resume_replay = True
                await self._stop_capture_locked(finalize=False)
            ok = await self._start_capture_locked(False, game)
            if not ok and self.resume_replay:
                self.resume_replay = False
                if self.s["replay_enabled"]:
                    await self._start_capture_locked(True, "")
        return self._snapshot()

    async def stop_recording(self) -> Dict[str, Any]:
        async with self.lock:
            if self.mode == "manual":
                await self._stop_capture_locked(finalize=True)
        return self._snapshot()

    async def set_replay(self, enabled: bool) -> Dict[str, Any]:
        self.s["replay_enabled"] = bool(enabled)
        self.paused = False
        self._save_settings()
        async with self.lock:
            if enabled and self.mode is None:
                await self._start_capture_locked(True, "")
            elif not enabled and self.mode == "replay":
                await self._stop_capture_locked(finalize=False)
        return self._snapshot()

    async def _restart_replay(self, why: str) -> None:
        self._note(f"restarting replay buffer: {why}")
        async with self.lock:
            if self.mode == "replay":
                await self._stop_capture_locked(finalize=False)
            if self.s["replay_enabled"] and self.mode is None:
                await self._start_capture_locked(True, "")

    async def _warm_caps(self) -> None:
        """Probe the system shortly after load (instant when the saved probe is still valid), so the panel opens fast."""
        try:
            await asyncio.sleep(self.WARM_CAPS_AFTER or 0)
            await self._capabilities()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._note("warm-up probe failed: " + traceback.format_exc(limit=1))

    async def _delayed_replay_start(self) -> None:
        await asyncio.sleep(6)          # PipeWire/Pulse may not be ready right after boot
        async with self.lock:
            if self.s["replay_enabled"] and self.mode is None and util._in_game_mode() and not self._idle_pause():
                await self._start_capture_locked(True, "")
