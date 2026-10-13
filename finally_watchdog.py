"""Background supervision: crashes, freezes, sleep/resume, leaving Game Mode, re-arming, orphan recovery."""

import asyncio
import recorder_core as core
import glob
import os
import shutil
import signal
import time
import traceback
from typing import Any, Optional, Tuple

import finally_util as util
from finally_util import _chown, _makedirs


class WatchdogMixin:
    async def _watchdog(self) -> None:
        last_wall = time.time()
        while True:
            try:
                await asyncio.sleep(self.WATCHDOG_INTERVAL)
                now = time.time()
                self._sample_cost(now)
                woke = core.woke_from_sleep(now - last_wall, self.WATCHDOG_INTERVAL)
                last_wall = now
                if woke and self.mode == "replay":
                    await self._restart_replay("resumed from sleep")
                    continue
                if woke and self.mode == "manual":
                    self._note("resumed from sleep during a recording")
                if woke and self.stall:
                    self.stall.reset(now)
                proc = self.proc
                if proc is not None and proc.returncode is not None and self.mode is not None:
                    await self._on_unexpected_exit()
                    continue
                if self.state == "error" and now - self.error_since >= self.ERROR_CLEAR_SECONDS:
                    self.last_error = ""
                    await self._set_state("idle", None)
                self.not_gm_ticks = 0 if util._in_game_mode() else self.not_gm_ticks + 1
                if self.mode is not None and self.not_gm_ticks >= self.LEAVE_TICKS:
                    # the original plugin does this too: capturing while leaving Game Mode gives a black screen
                    self._note("left Game Mode while capturing, stopping")
                    async with self.lock:
                        await self._stop_capture_locked(finalize=(self.mode == "manual"))
                    continue
                if self.state == "buffering" and now - self.started_at > 60:
                    self.rearm_times = []
                if self.state in ("recording", "buffering"):
                    self._sample_cpu(now)
                    if await self._adapt(now, self._check_speed(now)):
                        continue
                if self.stall and self.state in ("recording", "buffering"):
                    marker = self._stall_marker()
                    if marker is not None and self.stall.update(marker, now):
                        await self._on_stall()
                        continue
                if self.mode == "manual":
                    try:
                        free_mb = shutil.disk_usage(self._out_dir()).free // (1024 * 1024)
                    except Exception:
                        free_mb = 10 ** 9
                    if free_mb < max(100, self.s["min_free_mb"] // 4):
                        self.warnings.append("Disk almost full: recording stopped and saved.")
                        async with self.lock:
                            await self._stop_capture_locked(finalize=True)
                elif self.mode is None and self.s["replay_enabled"] and self.state == "idle" \
                        and not self.pending and self.not_gm_ticks == 0 and not self._idle_pause():
                    self.rearm_times = [x for x in self.rearm_times if now - x < 600]
                    if len(self.rearm_times) >= 4 and not self.paused:
                        self.s["replay_enabled"] = False
                        self._save_settings()
                        self.rearm_times = []
                        await self._fail("The replay buffer could not start 4 times in a row and was turned off. See Diagnostics.")
                    else:
                        async with self.lock:
                            # checked again under the lock: a start that was already in progress (state is still 'idle'
                            # for ~1 s) must not be counted as a failed attempt
                            if self.mode is None and self.state == "idle":
                                if self.paused:              # a game started again: not a failure, so not counted
                                    self.paused = False
                                else:
                                    self.rearm_times.append(now)
                                await self._start_capture_locked(True, "")
            except asyncio.CancelledError:
                raise
            except Exception:
                self._note("watchdog error: " + traceback.format_exc(limit=2))

    def _read_progress(self) -> Tuple[Optional[int], Optional[float]]:
        """(encoded video frames, speed in x real time) from the tail of ffmpeg's -progress file."""
        try:
            with open(self.progress_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 4096))
                text = f.read().decode("utf-8", "replace")
        except OSError:
            return None, None
        return core.parse_progress_frame(text), core.parse_progress_speed(text)

    def _sample_cpu(self, now: float) -> None:
        """CPU use of our capture/encode processes, in % of one core (100 = one core; the Deck has 8 threads)."""
        total, seen = 0, False
        for pr in (self.proc, self.src_proc):
            if pr is None or pr.returncode is not None:
                continue
            try:
                with open(f"/proc/{pr.pid}/stat") as f:
                    ticks = core.parse_stat_ticks(f.read())
            except OSError:
                continue
            if ticks is not None:
                total += ticks
                seen = True
        if not seen:
            self.cpu, self._cpu_prev = None, None
            return
        prev = self._cpu_prev
        if prev is None:
            self._cpu_prev = (now, total)
        elif now - prev[0] >= 1.5:
            self.cpu = round(core.cpu_percent(total - prev[1], now - prev[0], os.sysconf("SC_CLK_TCK")))
            self._cpu_prev = (now, total)

    def _check_speed(self, now: float) -> Optional[str]:
        """Warning text (once per capture) when the ffmpeg encoder cannot keep up with real time."""
        if self.engine != "ffmpeg" or not self.progress_path or self.speed_mon is None:
            return None
        _frame, speed = self._read_progress()
        self.speed = speed
        if self.speed_mon.update(speed, now):
            return (f"The encoder cannot keep up ({speed:.2f}x real time): the video will play too fast or skip. "
                    "Set the frame rate to 30, lower the resolution or the bitrate.")
        return None

    def _stall_marker(self) -> Any:
        """Something that changes while data flows: file size (manual) or (segments, total bytes) (replay)."""
        try:
            if self.engine == "ffmpeg" and self.progress_path:
                # encoded video frames: unaffected by audio still flowing, by muxer buffering and by static screens
                frame, _speed = self._read_progress()
                return -1 if frame is None else frame
            if self.mode == "manual":
                return os.path.getsize(self.staging)
            n = total = 0
            newest = 0.0
            with os.scandir(self.seg_dir) as it:
                for e in it:
                    if e.name.startswith("seg_"):
                        st = e.stat()
                        n += 1
                        total += st.st_size
                        newest = max(newest, st.st_mtime)
            return (n, total, newest)
        except OSError:
            return None

    async def _on_stall(self) -> None:
        mode, game = self.mode, self.game
        det = self.stall
        grew = bool(det and det.ever_grew)
        now = time.time()
        self.stall_times = [x for x in self.stall_times if now - x < 120] + [now]
        self.nogrow_streak = 0 if grew else self.nogrow_streak + 1
        self._note(f"capture stalled ({mode}): no new data for {self.s['stall_seconds']}s, grew before: {grew}")
        async with self.lock:
            if self.mode != mode or self.stall is not det:
                return                                  # stopped/changed while we waited for the lock
            giving_up = len(self.stall_times) > 3 or self.nogrow_streak >= 2
            await self._stop_capture_locked(finalize=(mode == "manual"), fast=True)
            if giving_up:
                self.nogrow_streak = 0
                if mode == "replay":
                    self.s["replay_enabled"] = False
                    self._save_settings()
                await self._fail("Capture keeps delivering no data (static screen or a capture problem). Stopped; "
                                 "see Diagnostics. You can turn off the freeze check in Settings.")
                return
            msg = "Capture froze (no new data), restarted" + (" - the recording continues in a new file." if mode == "manual" else ".")
            ok = await self._start_capture_locked(mode == "replay", game if mode == "manual" else "")
            if ok:
                self.warnings.append(msg)
                await self._emit("warning", message=msg)

    async def _on_unexpected_exit(self) -> None:
        mode, staging, final, game = self.mode, self.staging, self.final_path, self.game
        tail = self._stderr_summary()
        self._note(f"gst-launch exited unexpectedly ({mode}): {tail}")
        async with self.lock:
            src, self.proc, self.src_proc = self.src_proc, None, None
            if src is not None and src.returncode is None:
                await self._terminate(src, ((signal.SIGKILL, 2.0),))
            await self._unload_audio()
            if mode == "manual":
                await self._set_state("saving", "manual")
                self._spawn(self._finalize(staging, final, game))
                self.last_error = "Recording stopped unexpectedly: " + tail
                await self._emit("error", message=self.last_error)
            else:
                now = time.time()
                self.restart_times = [t for t in self.restart_times if now - t < 120] + [now]
                if len(self.restart_times) > 4:
                    await self._fail("Replay buffer keeps crashing: " + tail)
                    self.s["replay_enabled"] = False
                    self._save_settings()
                    return
                shutil.rmtree(self.seg_dir, ignore_errors=True)
                await self._set_state("idle", None)
                if self.s["replay_enabled"]:
                    await asyncio.sleep(1)
                    await self._start_capture_locked(True, "")

    async def _cleanup_orphans(self, recover: bool) -> None:
        await self._sweep_audio_orphans()
        shutil.rmtree(self.seg_dir, ignore_errors=True)
        shutil.rmtree(os.path.join(self.stage_dir, "playback"), ignore_errors=True)
        if not os.path.isdir(self.stage_dir):
            return
        for p in sorted(glob.glob(os.path.join(self.stage_dir, "rec_*.mkv"))):
            try:
                if time.time() - os.path.getmtime(p) < 10:
                    continue
                if recover and os.path.getsize(p) > 4096:
                    out_dir = self._out_dir()
                    _makedirs(out_dir)
                    dst = core.unique_path(out_dir, "Recovered_" + os.path.splitext(os.path.basename(p))[0], "mkv")
                    ok, _ = await self._remux(p, dst)
                    if ok:
                        _chown(dst)
                        self._note(f"recovered unfinished recording -> {dst}")
                os.unlink(p)
            except Exception:
                self._note("orphan recovery failed for " + p)
        for pth in glob.glob(os.path.join(self.stage_dir, "prog_*.txt")):
            try:
                os.unlink(pth)
            except OSError:
                pass
        for d in glob.glob(os.path.join(self.stage_dir, "clip_*")):
            shutil.rmtree(d, ignore_errors=True)
