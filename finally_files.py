"""Replay-buffer clips and the recordings folder."""

import recorder_core as core
import glob
import os
import shutil
import time
from typing import Any, Dict, List

from finally_util import VIDEO_EXT, _chown, _makedirs, _run


class FilesMixin:
    async def save_clip(self, game: str = "", seconds: int = 0) -> Dict[str, Any]:
        """Write the last `seconds` (default: clip length setting) of the replay buffer."""
        if self.mode != "replay" or self.state != "buffering":
            return {"ok": False, "error": "Replay buffer is not running."}
        if time.time() - self.last_clip_time < 2:
            return {"ok": False, "error": "Wait a moment before saving another clip."}
        self.last_clip_time = time.time()
        secs = int(core.clamp_num(seconds or self.s["clip_seconds"], 5, self.s["buffer_seconds"], self.s["clip_seconds"]))
        files = []
        for p in glob.glob(os.path.join(self.seg_dir, "seg_*.mkv")):
            try:
                files.append((p, os.path.getmtime(p)))
            except OSError:
                pass
        chosen = core.select_segments(files, secs, time.time())
        if not chosen:
            return {"ok": False, "error": "Buffer is still empty, try again in a few seconds."}
        out_dir = self._out_dir()
        try:
            _makedirs(out_dir)
        except Exception as e:
            return {"ok": False, "error": f"Cannot create output folder: {e}"}
        name = core.safe_name(game or self.game)
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        final = core.unique_path(out_dir, f"{name}_clip{secs}s_{stamp}", self.s["format"])
        # snapshot the segments: splitmuxsink deletes the oldest ones while we copy
        tmp = os.path.join(self.stage_dir, f"clip_{int(time.time())}")
        try:
            _makedirs(tmp)
            kept = []
            for i, p in enumerate(chosen):
                dst = os.path.join(tmp, f"s{i:05d}.mkv")
                try:
                    shutil.copyfile(p, dst)
                    kept.append(dst)
                except OSError:
                    continue
            if not kept:
                return {"ok": False, "error": "Segments vanished while saving, try again."}
            lst = os.path.join(tmp, "list.txt")
            with open(lst, "w", encoding="utf-8") as f:
                f.write(core.concat_list(kept))
            ffmpeg = self.caps.get("ffmpeg") or shutil.which("ffmpeg") or "ffmpeg"
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy"]
            if final.endswith(".mp4"):
                cmd += ["-movflags", "+faststart"]
            rc, _o, err = await _run(cmd + [final], 300)
            if rc != 0 or not os.path.exists(final):
                await self._emit("error", message="Clip failed: " + err.strip()[-160:])
                return {"ok": False, "error": "ffmpeg failed: " + err.strip()[-160:]}
            _chown(final)
            await self._saved(final, "clip")
            return {"ok": True, "name": os.path.basename(final), "seconds": len(kept)}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    async def list_recent(self) -> List[Dict[str, Any]]:
        d = self._out_dir()
        items = []
        try:
            for n in os.listdir(d):
                if n.lower().endswith(VIDEO_EXT) and not n.startswith("."):
                    p = os.path.join(d, n)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    items.append({"name": n, "size": st.st_size, "mtime": st.st_mtime})
        except OSError:
            return []
        items.sort(key=lambda i: -i["mtime"])
        return items[:8]

    async def delete_recording(self, name: str) -> Dict[str, Any]:
        n = str(name)
        if os.path.basename(n) != n or not n.lower().endswith(VIDEO_EXT) or n.startswith("."):
            return {"ok": False, "error": "Invalid file name."}
        p = os.path.join(self._out_dir(), n)
        try:
            os.unlink(p)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        self._play_forget(p)                         # converted copy + any player link for this file
        if self.last_saved.get("name") == n:
            self.last_saved = {}
        await self._emit("deleted", name=n)
        return {"ok": True}
