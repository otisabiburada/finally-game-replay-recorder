"""Watching a saved clip inside the Steam UI.

The Steam UI is a browser: it plays a clip through <video src="http://127.0.0.1:PORT/play/TOKEN.mp4">. This mixin
  * prepares the file (mp4 as is; mkv is remuxed to mp4 without re-encoding; optional slower WebM/VP9 conversion),
  * serves it from a tiny HTTP server bound to 127.0.0.1 only, one random token per file, with Range support
    (needed for seeking). Pure asyncio on purpose: Decky's bundled Python may lack http.server.
UNVERIFIED on a real Deck: whether Steam's browser can decode H.264/AAC. The player shows the exact reason if not."""

import asyncio
import os
import re
import secrets
import shutil
from typing import Any, Dict, Optional, Tuple

from finally_util import VIDEO_EXT, _run

MIME = {"mp4": "video/mp4", "webm": "video/webm"}
REQ = re.compile(r"^(GET|HEAD) /play/([A-Za-z0-9_\-]{8,64})\.(mp4|webm) HTTP/1\.[01]$")


class PlaybackMixin:
    PLAY_CACHE_KEEP = 3
    PLAY_MAX_TOKENS = 8

    def _play_init(self) -> None:
        self.play_tokens: Dict[str, Tuple[str, str]] = {}
        self.play_server: Optional[asyncio.AbstractServer] = None
        self.play_port = 0

    def _play_dir(self) -> str:
        return os.path.join(self.stage_dir, "playback")

    # ------------------------------------------------------------------ public
    async def prepare_playback(self, name: str, mode: str = "mp4") -> Dict[str, Any]:
        """-> {ok, url, format, note} or {ok: False, error}. mode: 'mp4' (default) or 'webm' (slower, widest support)."""
        n = str(name)
        if os.path.basename(n) != n or not n.lower().endswith(VIDEO_EXT) or n.startswith("."):
            return {"ok": False, "error": "Invalid file name."}
        src = os.path.join(self._out_dir(), n)
        if not os.path.isfile(src) or os.path.getsize(src) == 0:
            return {"ok": False, "error": "File not found."}
        caps = await self._capabilities()
        ffmpeg = caps.get("ffmpeg")
        note = ""
        try:
            if mode == "webm":
                if not (ffmpeg and caps.get("ff_vp9") and caps.get("ff_opus")):
                    return {"ok": False, "error": "This ffmpeg cannot make WebM (libvpx-vp9 / libopus missing)."}
                path, fmt = await self._play_convert(ffmpeg, src, "webm")
                note = "Converted to WebM (VP9) for playback."
            elif n.lower().endswith(".mp4"):
                path, fmt = src, "mp4"
            else:
                if not ffmpeg:
                    return {"ok": False, "error": "ffmpeg not found."}
                path, fmt = await self._play_convert(ffmpeg, src, "mp4")
                note = "Repackaged as mp4 for playback (no quality loss)."
        except RuntimeError as e:
            return {"ok": False, "error": str(e)}
        port = await self._play_serve()
        token = secrets.token_urlsafe(12)
        self.play_tokens[token] = (path, MIME[fmt])
        while len(self.play_tokens) > self.PLAY_MAX_TOKENS:
            self.play_tokens.pop(next(iter(self.play_tokens)))
        return {"ok": True, "url": f"http://127.0.0.1:{port}/play/{token}.{fmt}", "format": fmt, "note": note,
                "name": n, "size": os.path.getsize(path)}

    # ------------------------------------------------------------------ preparing files
    async def _play_convert(self, ffmpeg: str, src: str, fmt: str) -> Tuple[str, str]:
        d = self._play_dir()
        os.makedirs(d, exist_ok=True)
        out = os.path.join(d, os.path.splitext(os.path.basename(src))[0] + "." + fmt)
        if os.path.exists(out) and os.path.getsize(out) > 0 and os.path.getmtime(out) >= os.path.getmtime(src):
            os.utime(out, None)                                            # reuse; keep it "recent" for the cache trim
            return out, fmt
        tmp = out + ".part." + fmt
        if fmt == "mp4":
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", src, "-c", "copy", "-movflags", "+faststart", tmp]
            limit = 120
        else:
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", src, "-vf", "scale=-2:'min(720,ih)'", "-c:v", "libvpx-vp9",
                   "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-b:v", "3M", "-c:a", "libopus", "-b:a", "128k", tmp]
            limit = 600
        rc, _o, err = await _run(cmd, limit)
        if rc != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise RuntimeError("Could not prepare the clip for playback: " + (err.strip().splitlines() or ["ffmpeg failed"])[-1][:160])
        os.replace(tmp, out)
        self._play_trim(keep=out)
        return out, fmt

    def _play_trim(self, keep: str) -> None:
        files = []
        for f in os.listdir(self._play_dir()):
            p = os.path.join(self._play_dir(), f)
            if os.path.isfile(p) and not f.endswith((".part.mp4", ".part.webm")):
                files.append((os.path.getmtime(p), p))
        files.sort(reverse=True)
        for _m, p in files[self.PLAY_CACHE_KEEP:]:
            if p != keep:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    # ------------------------------------------------------------------ the tiny server
    async def _play_serve(self) -> int:
        if self.play_server is None:
            self.play_server = await asyncio.start_server(self._play_handle, "127.0.0.1", 0)
            self.play_port = self.play_server.sockets[0].getsockname()[1]
        return self.play_port

    def _play_forget(self, source_path: str) -> None:
        """A recording was deleted: drop its converted copies and every player link that points at it or them."""
        stem = os.path.splitext(os.path.basename(source_path))[0]
        gone = {source_path}
        for ext in ("mp4", "webm"):
            cp = os.path.join(self._play_dir(), f"{stem}.{ext}")
            if os.path.exists(cp):
                gone.add(cp)
                try:
                    os.unlink(cp)
                except OSError:
                    pass
        for tok in [t for t, (p, _m) in self.play_tokens.items() if p in gone]:
            self.play_tokens.pop(tok, None)

    async def _play_stop(self) -> None:
        srv, self.play_server = self.play_server, None
        self.play_tokens.clear()
        if srv is not None:
            srv.close()
            try:
                await asyncio.wait_for(srv.wait_closed(), timeout=3)
            except Exception:
                pass
        shutil.rmtree(self._play_dir(), ignore_errors=True)

    @staticmethod
    async def _play_reply(writer: asyncio.StreamWriter, status: str, headers: Dict[str, str]) -> None:
        head = f"HTTP/1.1 {status}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "Connection: close\r\n\r\n"
        writer.write(head.encode("latin-1"))
        await writer.drain()

    async def _play_handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            lines = raw.decode("latin-1").split("\r\n")
            m = REQ.match(lines[0])
            hdrs = {l.split(":", 1)[0].strip().lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
            if not m:
                status = "405 Method Not Allowed" if lines[0].split(" ")[0] not in ("GET", "HEAD") else "404 Not Found"
                await self._play_reply(writer, status, {"Content-Length": "0"})
                return
            method, token = m.group(1), m.group(2)
            entry = self.play_tokens.get(token)
            if not entry or not os.path.isfile(entry[0]):
                await self._play_reply(writer, "404 Not Found", {"Content-Length": "0"})
                return
            path, mime = entry
            size = os.path.getsize(path)
            start, end, status = 0, size - 1, "200 OK"
            rng = hdrs.get("range", "")
            if rng:
                rm = re.match(r"^bytes=(\d*)-(\d*)$", rng)
                if not rm or (rm.group(1) == "" and rm.group(2) == ""):
                    await self._play_reply(writer, "416 Range Not Satisfiable", {"Content-Range": f"bytes */{size}", "Content-Length": "0"})
                    return
                if rm.group(1) == "":                                   # suffix: the last N bytes
                    start = max(0, size - int(rm.group(2)))
                else:
                    start = int(rm.group(1))
                    if rm.group(2):
                        end = min(size - 1, int(rm.group(2)))
                if start >= size or start > end:
                    await self._play_reply(writer, "416 Range Not Satisfiable", {"Content-Range": f"bytes */{size}", "Content-Length": "0"})
                    return
                status = "206 Partial Content"
            length = end - start + 1
            h = {"Content-Type": mime, "Content-Length": str(length), "Accept-Ranges": "bytes", "Cache-Control": "no-store",
                 "Access-Control-Allow-Origin": "*"}
            if status.startswith("206"):
                h["Content-Range"] = f"bytes {start}-{end}/{size}"
            await self._play_reply(writer, status, h)
            if method == "GET":
                with open(path, "rb") as f:
                    f.seek(start)
                    left = length
                    while left > 0:
                        chunk = f.read(min(256 * 1024, left))
                        if not chunk:
                            break
                        writer.write(chunk)
                        await writer.drain()
                        left -= len(chunk)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, OSError):
            pass                                                         # the player closed the connection (normal when seeking)
        finally:
            try:
                writer.close()
            except Exception:
                pass
