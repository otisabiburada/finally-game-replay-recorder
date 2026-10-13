"""Starting/stopping the capture processes (GStreamer, or GStreamer + ffmpeg) and the replay-segment check."""

import asyncio
import recorder_core as core
import glob
import os
import shutil
import signal
import time
import traceback

from finally_util import _clean_env, _makedirs, _run


class CaptureMixin:
    async def _start_capture_locked(self, rolling: bool, game: str) -> bool:
        self.last_error = ""
        self.warnings = []
        self.stderr_tail.clear()
        caps = await self._capabilities()
        if not caps.get("gst_launch") or not (caps.get("encoders") or caps.get("ff_usable")):
            caps = await self._capabilities(force=True)          # nothing usable was found: it may predate a ready system
        if not caps.get("gst_launch"):
            await self._fail("gst-launch-1.0 not found. Run the self-test in the Diagnostics section.")
            return False
        engine, why = core.pick_engine(caps["encoders"], caps["aac"], caps.get("ff_usable", []),
                                       "pulse" in caps.get("ff_devices", []), self.s["engine"],
                                       bool(caps["elements"].get("pipewiresrc")))
        if not engine:
            await self._fail(why + " Open Diagnostics for details.")
            return False
        self.engine = engine
        if engine == "gst":
            enc = core.pick_encoder(caps["encoders"], self.s["encoder"])
            needed = ("pipewiresrc", "h264parse", "matroskamux") + (("splitmuxsink",) if rolling else ())
        else:
            enc = core.pick_ffmpeg_encoder(caps["ff_usable"], self.s["encoder"])
            needed = ("pipewiresrc", "videoconvert", "videoscale", "videorate", "fdsink")
        for need in needed:
            if not caps["elements"].get(need):
                await self._fail(f"GStreamer element '{need}' is missing.")
                return False
        if not caps.get("ffmpeg"):
            await self._fail("ffmpeg not found (needed to save the final file).")
            return False
        out_dir = self._out_dir()
        try:
            _makedirs(out_dir)
            _makedirs(self.stage_dir)
        except Exception as e:
            await self._fail(f"Cannot create folders: {e}")
            return False
        if rolling:
            need = core.estimate_buffer_mb(self.s["video_kbps"], self.s["audio_kbps"], self.s["buffer_seconds"])
            have = self._shm_free_mb()
            if need * 1.3 > have:
                await self._fail(f"Replay buffer needs about {need} MB of RAM but only {have} MB is free. "
                                 "Use a shorter buffer or a lower video bitrate.")
                return False
        if not rolling:
            try:
                free_mb = shutil.disk_usage(out_dir).free // (1024 * 1024)
            except Exception:
                free_mb = 10 ** 9
            if free_mb < self.s["min_free_mb"]:
                await self._fail(f"Only {free_mb} MB free in the output folder (limit {self.s['min_free_mb']} MB).")
                return False
        self.encoder = f"{enc} ({'ffmpeg' if engine == 'ffmpeg' else 'gst'})"
        device = await self._setup_audio()
        self.game = core.safe_name(game)
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        self._rec_counter = getattr(self, "_rec_counter", 0) + 1
        self.staging = os.path.join(self.stage_dir, f"rec_{stamp}_{os.getpid()}_{self._rec_counter}.mkv")
        pattern = ""
        if rolling:
            shutil.rmtree(self.seg_dir, ignore_errors=True)
            _makedirs(self.seg_dir)
            pattern = os.path.join(self.seg_dir, "seg_%05d.mkv")
        try:
            if engine == "gst":
                argv, warns = core.build_gst_argv(
                    encoder=enc, props=set(caps["props"].get(enc, [])), split_props=set(caps["split_props"]),
                    aac=caps["aac"], settings=self.s, audio_device=device, rolling=rolling, out_path=self.staging,
                    segment_pattern=pattern, buffer_seconds=self.s["buffer_seconds"], split_variant=self.split_variant,
                    src_props=set(caps.get("src_props", [])))
                if "bitrate" not in caps["props"].get(enc, []):
                    warns.append(f"Could not read the bitrate setting of {enc}; the encoder default quality is used.")
            else:
                warns = []
                probed = None if core.RESOLUTIONS.get(self.s["resolution"]) else await self._probe_size(caps)
                if probed is None and not core.RESOLUTIONS.get(self.s["resolution"]):
                    warns.append(f"Could not read the screen size; assuming {core.DEFAULT_SIZE[0]}x{core.DEFAULT_SIZE[1]}.")
                size = core.target_size(self.s, probed)
                fps = int(self.s["fps"] or core.DEFAULT_FPS)
                self.progress_path = os.path.join(self.stage_dir, f"prog_{self._rec_counter}.txt")
                audio_in = None
                if device:
                    if self.FFMPEG_AUDIO_OVERRIDE:
                        audio_in = list(self.FFMPEG_AUDIO_OVERRIDE)
                    elif "pulse" in caps.get("ff_devices", []):
                        audio_in = ["-f", "pulse", "-thread_queue_size", "1024", "-i", device]
                    else:
                        warns.append("ffmpeg has no pulse input; recording without audio.")
                feeder = core.build_feeder_argv(size, fps, set(caps.get("src_props", [])))
                argv = core.build_ffmpeg_argv(encoder=enc, settings=self.s, size=size, fps=fps, audio_input=audio_in,
                                              rolling=rolling, out_path=self.staging, segment_pattern=pattern,
                                              buffer_seconds=self.s["buffer_seconds"], progress_path=self.progress_path)
        except ValueError as e:
            await self._unload_audio()
            await self._fail(str(e))
            return False
        self.warnings += warns
        try:
            if engine == "gst":
                self._note("gst-launch " + " ".join(argv))
                self.proc = await asyncio.create_subprocess_exec(
                    caps["gst_launch"], *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.DEVNULL, env=self._gst_env(), start_new_session=True)
            else:
                self._note("gst-launch " + " ".join(feeder))
                self._note("ffmpeg " + " ".join(argv))
                rfd, wfd = os.pipe()
                try:
                    self.proc = await asyncio.create_subprocess_exec(
                        caps["ffmpeg"], *argv, stdin=rfd, stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE, env=_clean_env(), start_new_session=True)
                    try:
                        self.src_proc = await asyncio.create_subprocess_exec(
                            caps["gst_launch"], *feeder, stdin=asyncio.subprocess.DEVNULL, stdout=wfd,
                            stderr=asyncio.subprocess.PIPE, env=self._gst_env(), start_new_session=True)
                    except Exception:
                        self.proc.kill()
                        raise
                finally:
                    os.close(rfd)
                    os.close(wfd)
        except Exception as e:
            self.proc = self.src_proc = None
            await self._unload_audio()
            await self._fail(f"Could not start the recorder: {e}")
            return False
        for pr in (self.proc, self.src_proc):
            if pr is not None:
                asyncio.get_event_loop().create_task(self._read_stderr(pr))
                if self.s.get("low_priority"):
                    try:                                    # be nice to the game: lowering our priority is always allowed
                        os.setpriority(os.PRIO_PROCESS, pr.pid, 10)
                    except OSError:
                        pass
        self.cpu, self._cpu_prev = None, None
        # a pipeline that is going to fail (missing element, no pipewire stream) dies within ~2s
        for _ in range(8):
            await asyncio.sleep(0.25)
            if any(pr is not None and pr.returncode is not None for pr in (self.proc, self.src_proc)):
                break
        if any(pr is not None and pr.returncode is not None for pr in (self.proc, self.src_proc)):
            await asyncio.sleep(0.2)
            tail = self._stderr_summary()
            await self._stop_proc(fast=True)
            await self._unload_audio()
            self._caps_invalidate()                                  # maybe the saved probe is outdated: look again next time
            await self._fail("Recorder exited immediately: " + tail)
            return False
        self.started_at = time.time()
        self.final_path = ""
        if not rolling:
            self.final_path = core.unique_path(out_dir, f"{self.game}_{stamp}", self.s["format"])
        await self._set_state("buffering" if rolling else "recording", "replay" if rolling else "manual")
        secs = float(self.s.get("stall_seconds") or 0)
        self.stall = core.StallDetector(secs, self.STALL_GRACE, time.time()) if secs > 0 else None
        self.speed = None
        self.speed_mon = core.SpeedMonitor(self.SPEED_THRESHOLD, self.SPEED_SECONDS, self.STALL_GRACE, time.time()) \
            if self.engine == "ffmpeg" else None
        if rolling:
            self.verify_task = asyncio.get_event_loop().create_task(self._verify_replay(self.proc))
        return True

    async def _verify_replay(self, proc) -> None:
        """~10s after the buffer starts: the first finished segment must be a real mkv with video (and audio
        when expected). If not, retry once with the other splitmuxsink syntax."""
        try:
            ffprobe = self.caps.get("ffprobe")
            if not ffprobe:
                return
            want_audio = bool(self.caps.get("aac")) and (self.s["game_audio"] or self.s["mic_enabled"])
            for _ in range(24):
                await asyncio.sleep(0.5)
                if self.proc is not proc or self.mode != "replay":
                    return
                segs = sorted(glob.glob(os.path.join(self.seg_dir, "seg_*.mkv")))
                if len(segs) >= 2:
                    break
            else:
                return
            rc, out, _e = await _run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type",
                                      "-of", "csv=p=0", segs[0]], 10)
            kinds = set(out.split())
            if rc == 0 and "video" in kinds and (not want_audio or "audio" in kinds):
                self._note(f"replay segments verified ({self.split_variant}): {','.join(sorted(kinds))}")
                return
            self._note(f"replay segment check failed ({self.split_variant}): rc={rc} streams={sorted(kinds)}")
            if self.engine == "ffmpeg" or getattr(self, "_variant_retried", False):
                self.warnings.append("Replay segments look wrong (see Diagnostics). Clips may be silent or broken.")
                return
            self._variant_retried = True
            self.split_variant = "factory" if self.split_variant == "original" else "original"
            await self._restart_replay("segment check failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            self._note("verify error: " + traceback.format_exc(limit=2))

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> None:
        try:
            assert proc.stderr is not None
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                self.stderr_tail.append(line.decode("utf-8", "replace").rstrip()[:300])
        except Exception:
            pass

    def _stderr_summary(self) -> str:
        lines = [l for l in self.stderr_tail if l.strip()]
        errs = [l for l in lines if "ERROR" in l or "error" in l.lower() or "WARNING" in l]
        pick = (errs or lines)[-3:]
        return " | ".join(pick)[:400] or "no output (is the game running?)"

    async def _terminate(self, proc: asyncio.subprocess.Process, steps) -> None:
        for sig, wait in steps:
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(proc.wait(), timeout=wait * self.STOP_SCALE)
                return
            except asyncio.TimeoutError:
                self._note(f"process {proc.pid} did not exit after {sig.name}")

    async def _stop_proc(self, fast: bool = False) -> None:
        """gst-launch -e writes EOS on SIGINT; ffmpeg finishes the file when its input ends.
        `fast` is for a pipeline already known to be hung."""
        proc, src = self.proc, self.src_proc
        self.proc = self.src_proc = None
        if src is not None and src.returncode is None:
            await self._terminate(src, ((signal.SIGINT, 3.0 if fast else 8.0), (signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)))
        if proc is None or proc.returncode is not None:
            return
        if src is not None:
            try:                                           # EOF on stdin -> ffmpeg writes the file tail and exits
                await asyncio.wait_for(proc.wait(), timeout=(3.0 if fast else 20.0) * self.STOP_SCALE)
                return
            except asyncio.TimeoutError:
                self._note("ffmpeg did not finish by itself")
            await self._terminate(proc, ((signal.SIGINT, 4.0), (signal.SIGTERM, 3.0), (signal.SIGKILL, 3.0)))
        else:
            await self._terminate(proc, ((signal.SIGINT, 3.0 if fast else 15.0), (signal.SIGTERM, 5.0), (signal.SIGKILL, 3.0)))

    async def _stop_capture_locked(self, finalize: bool, fast: bool = False) -> None:
        mode, staging, final, game = self.mode, self.staging, self.final_path, self.game
        if self.proc is None and mode is None:
            return
        self.stall = None
        await self._set_state("saving" if (finalize and mode == "manual") else "idle", mode)
        await self._stop_proc(fast)
        await self._unload_audio()
        if self.progress_path:
            try:
                os.unlink(self.progress_path)
            except OSError:
                pass
            self.progress_path = ""
        if mode == "replay":
            shutil.rmtree(self.seg_dir, ignore_errors=True)
            await self._set_state("idle", None)
        elif mode == "manual" and finalize:
            self._spawn(self._finalize(staging, final, game))
        else:
            await self._set_state("idle", None)

    def _spawn(self, coro) -> None:
        t = asyncio.get_event_loop().create_task(coro)
        self.pending.append(t)
        t.add_done_callback(lambda x: self.pending.remove(x) if x in self.pending else None)
