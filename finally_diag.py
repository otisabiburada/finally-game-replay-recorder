"""Diagnostics: log tail and the on-device self-test."""

import recorder_core as core
import glob
import os
import re
import shutil
import time
from typing import Any, Dict, List, Tuple

import finally_util as util
from finally_util import MARK, _makedirs, _run, _user_ids


class DiagMixin:
    DRI_GLOB = "/dev/dri/card*"
    PROBE_KMSGRAB_UNPRIVILEGED = False

    async def _probe_capture(self, caps: Dict[str, Any], env: Dict[str, str]) -> List[Tuple[str, bool, str]]:
        """Can GStreamer deliver frames from the screen right now? Tries the plain element, then the recorder's real source
        chain, and lists the PipeWire video nodes. GST_DEBUG makes pipewiresrc say WHY when the state change fails."""
        out: List[Tuple[str, bool, str]] = []
        genv = dict(env, GST_DEBUG="2,pipewiresrc:4", GST_DEBUG_NO_COLOR="1")
        keep = ["do-timestamp=true"] + (["keepalive-time=1000"] if "keepalive-time" in caps.get("src_props", []) else [])
        variants = [("plain: pipewiresrc ! fakesink", ["pipewiresrc", "num-buffers=3", "!", "fakesink"]),
                    ("with videoconvert", ["pipewiresrc", "num-buffers=3", "!", "videoconvert", "!", "fakesink"]),
                    ("exactly like recording", ["pipewiresrc", *keep, "num-buffers=3", "!", "queue", "!", "videoconvert", "!", "fakesink"])]
        for label, args in variants:
            rc, o, e = await _run([caps["gst_launch"], "-v", "-e", *args], 10, genv)
            size = core.parse_caps_size(o + "\n" + e)
            if rc == 0:
                out.append((f"Screen capture - {label}", True, f"{size[0]}x{size[1]}" if size else "ok"))
            else:
                why = core.best_error_lines(e + "\n" + o)
                self._note(f"capture probe '{label}' failed: {why}")
                out.append((f"Screen capture - {label}", False, why))
        game = {True: "a game is running", False: "no game running", None: "game state unknown"}[self.game_running]
        out.append(("Screen capture - context", True, game))
        pwdump = shutil.which("pw-dump", path=env["PATH"])
        if pwdump:
            rc, o, _e = await _run([pwdump], 10, env)
            nodes = core.parse_pw_video_nodes(o) if rc == 0 else []
            out.append(("PipeWire video sources", bool(nodes), "; ".join(nodes)[:200] if nodes else "none listed"))
        return out

    @staticmethod
    def _has_cap_sys_admin() -> bool:
        """kmsgrab needs CAP_SYS_ADMIN (bit 21 of the effective capabilities)."""
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("CapEff:"):
                        return bool(int(line.split()[1], 16) & (1 << 21))
        except (OSError, ValueError, IndexError):
            pass
        return os.geteuid() == 0

    async def _probe_kmsgrab(self, ffmpeg: str) -> Tuple[bool, str]:
        """EXPERIMENT, changes nothing: can ffmpeg grab the screen straight from the GPU (kmsgrab -> VAAPI), which would
        avoid copying frames through the CPU? Reports permissions, plane size (portrait = needs rotating) and CPU cost.
        Known from other users: needs root/CAP_SYS_ADMIN, and older gamescope made ffmpeg crash when the plane format
        changed (UI <-> game switches). That is why it is only measured here, not used."""
        cards = sorted(glob.glob(self.DRI_GLOB))
        if not cards:
            return False, "no DRM card found"
        root = self._has_cap_sys_admin()
        if not root and not self.PROBE_KMSGRAB_UNPRIVILEGED:
            return False, "skipped: the plugin runs as a normal user, kmsgrab needs CAP_SYS_ADMIN (root); not used"
        first_error = ""
        for card in cards:
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "info", "-framerate", "30", "-f", "kmsgrab", "-device", card, "-i", "-",
                   "-vf", "hwmap=derive_device=vaapi,scale_vaapi=format=nv12", "-frames:v", "60", "-c:v", "h264_vaapi", "-f", "null", "-"]
            t0, w0 = os.times(), time.time()
            rc, _o, err = await _run(cmd, 25)
            t1, w1 = os.times(), time.time()
            if rc == 0:
                cpu = (t1.children_user + t1.children_system) - (t0.children_user + t0.children_system)
                m = re.search(r"Video: .*?(\d{3,5})x(\d{3,5})", err)
                size = f"{m.group(1)}x{m.group(2)}" if m else "unknown size"
                portrait = bool(m and int(m.group(2)) > int(m.group(1)))
                return True, (f"{card}: works, plane {size}{' (PORTRAIT: needs rotating)' if portrait else ''}, "
                              f"ffmpeg used {100 * cpu / max(w1 - w0, 0.1):.0f}% of one core, {'root' if root else 'NOT root'}")
            if not first_error:
                line = (err.strip().splitlines() or ["failed"])[-1][:110]
                first_error = f"{card}: {line}; {'root' if root else 'NOT root (needs CAP_SYS_ADMIN)'}"
        return False, first_error

    async def get_log_tail(self) -> List[str]:
        return list(self.notes)[-25:] + ["--- gst-launch stderr ---"] + list(self.stderr_tail)[-15:]

    async def run_self_test(self) -> List[Dict[str, Any]]:
        """Checks the environment, then encodes a few seconds of test video with the same encoder/muxer
        elements and verifies the files with ffprobe. Needs the Deck; cannot be simulated elsewhere."""
        res: List[Dict[str, Any]] = []

        def add(name: str, ok: bool, detail: str = "") -> None:
            res.append({"name": name, "ok": bool(ok), "detail": detail[:300]})

        if self.mode is not None:
            add("Idle", False, "Stop the recording / replay buffer first so the test has the encoder to itself.")
            return res
        caps = await self._capabilities(force=True)
        sig = util._game_mode_signal()
        add("Game Mode", bool(sig), sig or "not in Game Mode (no gamescope-session / gamescope process found)")
        add("GStreamer bundle (optional)", True,
            caps.get("bundle") or "none - not needed when ffmpeg can encode")
        add("gst-launch-1.0", bool(caps["gst_launch"]), caps["gst_launch"] or "missing")
        add("ffmpeg", bool(caps["ffmpeg"]), caps["ffmpeg"] or "missing")
        add("ffprobe", bool(caps["ffprobe"]), caps["ffprobe"] or "missing")
        add("pactl", bool(caps["pactl"]), caps["pactl"] or "missing")
        g = self._read_gpu_busy()
        add("Sensor: GPU load", g is not None, f"{g}% now" if g is not None else "gpu_busy_percent not found - no GPU readout")
        w, wsrc = self._read_power()
        add("Sensor: power", w is not None, f"{w:.1f} W ({wsrc})" if w is not None else "no power sensor found - no watts readout")
        for el in ("pipewiresrc", "videoconvert", "videoscale", "videorate", "fdsink"):
            add(f"element {el}", caps["elements"].get(el, False))
        add("GStreamer H.264 encoder (optional)", bool(caps["encoders"]), ", ".join(caps["encoders"]) or "none (ffmpeg is used instead)")
        add("GStreamer AAC encoder (optional)", bool(caps["aac"]), caps["aac"] or "none (ffmpeg's own AAC is used)")
        uid, _ = _user_ids()
        add("PipeWire socket", os.path.exists(f"/run/user/{uid}/pipewire-0"), f"/run/user/{uid}/pipewire-0")
        add("GPU render node", os.path.exists("/dev/dri/renderD128"), "/dev/dri/renderD128")
        try:
            add("Output folder writable", True, self._out_dir() + f" ({core.format_bytes(shutil.disk_usage(self._ancestor(self._out_dir())).free)} free)")
        except Exception as e:
            add("Output folder writable", False, str(e))
        if caps["pactl"]:
            mid = await self._load_module("module-null-sink", f"sink_name={MARK}-Test")
            if mid is None:
                add("Audio: virtual sink", False, "pactl could not create a null sink")
            else:
                rc, out, _e = await _run([caps["pactl"], "list", "short", "sources"], 5)
                add("Audio: virtual sink", rc == 0 and f"{MARK}-Test.monitor" in out,
                    "created and its monitor source is visible" if f"{MARK}-Test.monitor" in out else "monitor source missing")
                await _run([caps["pactl"], "unload-module", str(mid)], 5)
                self.pa_ids = [i for i in self.pa_ids if i != mid]
        add("Screen source keepalive", "keepalive-time" in caps.get("src_props", []),
            "pipewiresrc can resend frames on a static screen" if "keepalive-time" in caps.get("src_props", [])
            else "not available in this pipewiresrc (static screens may pause the file)")
        # ---- ffmpeg engine: what ffmpeg offers and whether it really works
        add("ffmpeg pulse input", "pulse" in caps.get("ff_devices", []),
            "can record the virtual sink" if "pulse" in caps.get("ff_devices", []) else "missing: ffmpeg would record video only")
        for e in core.FFMPEG_ENCODER_PREFERENCE:
            if e in caps.get("ff_encoders", []):
                add(f"ffmpeg encoder {e}", e in caps["ff_usable"], caps["ff_probe"].get(e, ""))
        eng, why = core.pick_engine(caps["encoders"], caps["aac"], caps.get("ff_usable", []),
                                    "pulse" in caps.get("ff_devices", []), self.s["engine"], bool(caps["elements"].get("pipewiresrc")))
        add("Engine", eng is not None, f"{eng or 'none'}: {why}")
        if caps["ffmpeg"] and "h264_vaapi" in caps.get("ff_usable", []):
            ok, detail = await self._probe_kmsgrab(caps["ffmpeg"])
            add("Zero-copy capture (experiment, not used)", ok, detail)
        if not (caps["gst_launch"] and caps["ffprobe"]) or eng is None:
            return res
        _makedirs(self.stage_dir)
        env = self._gst_env()

        def clean() -> None:
            for old in glob.glob(os.path.join(self.stage_dir, "selftest_*")):
                try:
                    os.unlink(old)
                except OSError:
                    pass

        async def verify(label: str, files: List[str], rc: int, err: str, want_audio: bool) -> None:
            if rc != 0 or not files or not os.path.exists(files[0]):
                add(label, False, (err.strip().splitlines() or ["failed"])[-1])
                return
            prc, pout, _pe = await _run([caps["ffprobe"], "-v", "error", "-show_entries", "stream=codec_type",
                                         "-of", "csv=p=0", files[0]], 10, env)
            kinds = set(pout.split())
            add(label, "video" in kinds and (not want_audio or "audio" in kinds),
                f"{len(files)} file(s), streams: {','.join(sorted(kinds)) or 'none'}")

        if eng == "ffmpeg":
            enc = core.pick_ffmpeg_encoder(caps["ff_usable"], self.s["encoder"])
            for rolling in (False, True):
                label = f"test ffmpeg {enc} {'replay' if rolling else 'record'}"
                clean()
                out = os.path.join(self.stage_dir, "selftest_ff.mkv")
                seg = os.path.join(self.stage_dir, "selftest_ff_seg_%05d.mkv")
                try:
                    argv = core.build_ffmpeg_argv(
                        encoder=enc, settings=dict(self.s), size=(1280, 800), fps=30, rolling=rolling, out_path=out,
                        segment_pattern=seg, buffer_seconds=30, audio_input=["-f", "lavfi", "-i", "sine=frequency=440:duration=3"],
                        video_input=["-f", "lavfi", "-i", "testsrc=size=1280x800:rate=30,format=nv12"], extra_output=["-t", "3"])
                except ValueError as e:
                    add(label, False, str(e))
                    continue
                rc, _o, err = await _run([caps["ffmpeg"], *argv], 40)
                files = sorted(glob.glob(os.path.join(self.stage_dir, "selftest_ff_seg_*.mkv"))) if rolling else [out]
                await verify(label, files, rc, err, True)
            clean()
            for r in await self._probe_capture(caps, env):
                add(*r)
            return res
        for enc in caps["encoders"]:
            if self.s["encoder"] != "auto" and enc != self.s["encoder"]:
                continue
            for rolling in (False, True):
                label = f"test {enc} {'replay' if rolling else 'record'}"
                out = os.path.join(self.stage_dir, f"selftest_{enc}_{int(rolling)}.mkv")
                seg = os.path.join(self.stage_dir, f"selftest_{enc}_seg_%05d.mkv")
                clean()
                try:
                    argv, _w = core.build_gst_argv(
                        encoder=enc, props=set(caps["props"].get(enc, [])), split_props=set(caps["split_props"]),
                        aac=caps["aac"], settings=dict(self.s, resolution="native", fps=0), audio_device="test",
                        rolling=rolling, out_path=out, segment_pattern=seg, buffer_seconds=30,
                        source=["videotestsrc", "num-buffers=90", "is-live=false", "!",
                                "video/x-raw,width=1280,height=800,framerate=30/1"],
                        audio_source=["audiotestsrc", "num-buffers=140", "is-live=false"],
                        split_variant=self.split_variant)
                except ValueError as e:
                    add(label, False, str(e))
                    continue
                rc, _o, err = await _run([caps["gst_launch"], *argv], 40, env)
                files = sorted(glob.glob(os.path.join(self.stage_dir, f"selftest_{enc}_seg_*.mkv"))) if rolling else [out]
                await verify(label, files, rc, err, bool(caps["aac"]))
        clean()
        return res

    @staticmethod
    def _ancestor(path: str) -> str:
        while path and not os.path.isdir(path):
            nxt = os.path.dirname(path)
            if nxt == path:
                break
            path = nxt
        return path or "/"
