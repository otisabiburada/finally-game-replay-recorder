"""Turning the staging file into the final recording (remux, error handling, 'saved' event)."""

import recorder_core as core
import os
import shutil
import time
import traceback
from typing import Tuple

from finally_util import _chown, _run


class SaveMixin:
    async def _remux(self, src: str, dst: str) -> Tuple[bool, str]:
        ffmpeg = self.caps.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg"
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", src, "-c", "copy"]
        if dst.endswith(".mp4"):
            cmd += ["-movflags", "+faststart"]
        rc, _o, err = await _run(cmd + [dst], 600)
        return rc == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0, err.strip()[-200:]

    async def _save_problem(self, message: str) -> None:
        """A save failed. If another capture is already running (stall restart) only tell the user, so the
        state of the running capture is not overwritten with 'error'."""
        if self.mode is None:
            await self._fail(message)
        else:
            self._note("save problem: " + message)
            self.warnings.append(message)
            await self._emit("error", message=message)

    async def _finalize(self, staging: str, final: str, game: str) -> None:
        try:
            if not os.path.exists(staging) or os.path.getsize(staging) == 0:
                await self._save_problem("Recording produced no data. Check the Diagnostics self-test.")
                return
            ok, err = await self._remux(staging, final)
            if not ok:
                # keep the raw mkv rather than lose the recording
                keep = core.unique_path(os.path.dirname(final), os.path.splitext(os.path.basename(final))[0] + "_raw", "mkv")
                shutil.move(staging, keep)
                _chown(keep)
                await self._save_problem(f"Could not convert to {self.s['format']} ({err}); raw file kept: {os.path.basename(keep)}")
                return
            os.unlink(staging)
            _chown(final)
            await self._saved(final, "recording")
        except Exception:
            await self._save_problem("Saving failed: " + traceback.format_exc(limit=1))
        finally:
            if self.state == "saving":
                await self._set_state("idle", None)
            if self.resume_replay:
                self.resume_replay = False
                if self.s["replay_enabled"]:
                    async with self.lock:
                        if self.mode is None:
                            await self._start_capture_locked(True, "")

    async def _saved(self, path: str, kind: str) -> None:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        self.last_saved = {"name": os.path.basename(path), "path": path, "size": size, "kind": kind,
                           "ts": time.time()}
        self._note(f"saved {kind}: {path} ({core.format_bytes(size)})")
        await self._emit("saved", **self.last_saved)
