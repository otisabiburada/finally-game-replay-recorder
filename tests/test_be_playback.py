"""Backend tests: watching a saved clip (remux + local HTTP server). Run: python3 tests/run.py area playback"""
from harness import *   # noqa: F401,F403
from harness import M, Base, mk_sample, probe, probe_video
import hashlib
import urllib.error
import urllib.request


def fetch(url, method="GET", headers=None, data=None):
    """-> (status, headers, body); never raises on HTTP errors."""
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class Playback(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.out = self.p._out_dir()
        os.makedirs(self.out, exist_ok=True)

    def clip(self, name, audio=True, sec=2):
        path = os.path.join(self.out, name)
        mk_sample(path, audio, sec)
        return path

    async def get(self, *a, **k):
        return await asyncio.to_thread(fetch, *a, **k)

    async def test_mp4_is_served_as_is_with_ranges(self):
        f = self.clip("Game_clip30s.mp4")
        r = await self.p.prepare_playback("Game_clip30s.mp4")
        self.assertTrue(r["ok"], r); self.assertEqual(r["format"], "mp4"); self.assertTrue(r["url"].startswith("http://127.0.0.1:"))
        body = open(f, "rb").read()
        st, h, got = await self.get(r["url"])
        self.assertEqual((st, h["Content-Type"], h["Accept-Ranges"]), (200, "video/mp4", "bytes"))
        self.assertEqual(hashlib.md5(got).hexdigest(), hashlib.md5(body).hexdigest())
        st, h, got = await self.get(r["url"], headers={"Range": "bytes=10-109"})
        self.assertEqual((st, len(got), h["Content-Range"]), (206, 100, f"bytes 10-109/{len(body)}")); self.assertEqual(got, body[10:110])
        st, h, got = await self.get(r["url"], headers={"Range": f"bytes={len(body) - 50}-"})
        self.assertEqual((st, got), (206, body[-50:]))
        st, h, got = await self.get(r["url"], headers={"Range": "bytes=-30"})
        self.assertEqual((st, got), (206, body[-30:]))
        st, h, got = await self.get(r["url"], method="HEAD")
        self.assertEqual((st, h["Content-Length"], got), (200, str(len(body)), b""))
        st, *_ = await self.get(r["url"], headers={"Range": f"bytes={len(body) + 5}-"}); self.assertEqual(st, 416)
        st, *_ = await self.get(r["url"], headers={"Range": "bytes=oops"}); self.assertEqual(st, 416)

    async def test_server_only_serves_known_tokens_and_methods(self):
        self.clip("a.mp4")
        r = await self.p.prepare_playback("a.mp4")
        base = r["url"].rsplit("/play/", 1)[0]
        for bad in ("/play/nope-nope-nope.mp4", "/play/../../etc/passwd", "/etc/passwd", "/play/", "/", "/play/%2e%2e%2fx.mp4"):
            st, *_ = await self.get(base + bad); self.assertEqual(st, 404, bad)
        st, *_ = await self.get(r["url"], method="POST", data=b"x"); self.assertEqual(st, 405)
        self.assertEqual(self.p.play_server.sockets[0].getsockname()[0], "127.0.0.1")

    async def test_mkv_is_repackaged_to_playable_mp4_and_cached(self):
        self.clip("Rec.mkv")
        r = await self.p.prepare_playback("Rec.mkv")
        self.assertTrue(r["ok"], r); self.assertEqual(r["format"], "mp4"); self.assertIn("Repackaged", r["note"])
        st, h, got = await self.get(r["url"])
        served = os.path.join(self.root, "served.mp4"); open(served, "wb").write(got)
        kinds, dur, fmt = probe(served)
        self.assertEqual(kinds, {"video", "audio"}); self.assertIn("mp4", fmt); self.assertGreater(dur, 1.5)
        cached = os.path.join(self.p._play_dir(), "Rec.mp4")
        m1 = os.path.getmtime(cached); await asyncio.sleep(0.05)
        r2 = await self.p.prepare_playback("Rec.mkv")
        self.assertTrue(r2["ok"]); self.assertNotEqual(r2["url"], r["url"], "each prepare gets its own token")
        self.assertEqual(os.path.getsize(cached), len(got))
        self.assertFalse([f for f in os.listdir(self.p._play_dir()) if ".part" in f], "no half-written files left")

    async def test_cache_keeps_only_the_newest_few(self):
        for i in range(5):
            self.clip(f"c{i}.mkv", sec=1)
            self.assertTrue((await self.p.prepare_playback(f"c{i}.mkv"))["ok"])
            await asyncio.sleep(0.05)
        left = sorted(os.listdir(self.p._play_dir()))
        self.assertEqual(len(left), 3); self.assertIn("c4.mp4", left)

    async def test_bad_names_are_refused(self):
        os.makedirs(os.path.join(self.root, "x"), exist_ok=True)
        victim = os.path.join(self.root, "secret.mkv"); open(victim, "wb").write(b"x" * 10)
        for bad in ("../secret.mkv", victim, "a/b.mkv", ".hidden.mkv", "notes.txt", "", "missing.mkv"):
            self.assertFalse((await self.p.prepare_playback(bad))["ok"], bad)
        self.assertEqual(self.p.play_tokens, {})

    async def test_delete_removes_the_copy_the_links_and_the_last_saved_entry(self):
        self.clip("Rec.mkv"); self.clip("Orig.mp4")
        r1 = await self.p.prepare_playback("Rec.mkv"); r2 = await self.p.prepare_playback("Orig.mp4")
        cached = os.path.join(self.p._play_dir(), "Rec.mp4"); self.assertTrue(os.path.exists(cached))
        self.p.last_saved = {"name": "Rec.mkv", "path": os.path.join(self.out, "Rec.mkv")}
        decky.events.clear()
        self.assertTrue((await self.p.delete_recording("Rec.mkv"))["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.out, "Rec.mkv")) or os.path.exists(cached))
        self.assertEqual(self.p.last_saved, {})
        self.assertEqual((await self.get(r1["url"]))[0], 404, "the old link must stop working")
        self.assertEqual((await self.get(r2["url"]))[0], 200, "other clips are untouched")
        self.assertIn(("deleted", "Rec.mkv"), [(a[0]["type"], a[0].get("name")) for n, a in decky.events if a])
        self.assertTrue((await self.p.delete_recording("Orig.mp4"))["ok"])
        self.assertEqual((await self.get(r2["url"]))[0], 404, "an mp4 served as it is is forgotten too")
        self.assertFalse((await self.p.delete_recording("Orig.mp4"))["ok"], "already gone")

    async def test_webm_fallback_is_a_real_vp9_opus_file(self):
        self.clip("w.mp4", sec=2)
        r = await self.p.prepare_playback("w.mp4", "webm")
        self.assertTrue(r["ok"], r); self.assertEqual(r["format"], "webm")
        st, h, got = await self.get(r["url"]); self.assertEqual((st, h["Content-Type"]), (200, "video/webm"))
        served = os.path.join(self.root, "served.webm"); open(served, "wb").write(got)
        codecs = {s["codec_name"] for s in probe_video(served)}
        self.assertTrue({"vp9", "opus"} <= codecs, codecs)

    async def test_webm_is_refused_politely_without_the_encoders(self):
        self.clip("w.mp4", sec=1)
        await self.p._capabilities(); self.p.caps["ff_vp9"] = False
        r = await self.p.prepare_playback("w.mp4", "webm")
        self.assertFalse(r["ok"]); self.assertIn("cannot make WebM", r["error"])

    async def test_server_stops_on_unload_and_cache_is_removed(self):
        self.clip("u.mkv", sec=1)
        r = await self.p.prepare_playback("u.mkv")
        port = self.p.play_port
        await self.p._play_stop()
        with self.assertRaises(OSError):
            await asyncio.to_thread(urllib.request.urlopen, f"http://127.0.0.1:{port}/play/x", None, 2)
        self.assertFalse(os.path.exists(self.p._play_dir()))

    async def test_clip_shorter_buffer_options_are_accepted(self):
        st = await self.p.set_settings({"buffer_seconds": 15, "clip_seconds": 15})
        self.assertEqual((st["buffer_seconds"], st["clip_seconds"]), (15, 15))
        st = await self.p.set_settings({"buffer_seconds": 7})
        self.assertEqual(st["buffer_seconds"], 15, "below the minimum is clamped to 15")
        await self.p.set_settings({"buffer_seconds": 15, "clip_seconds": 15})
        await self.p.set_replay(True)
        self.assertEqual(self.p.state, "buffering")
        self.assertTrue(any("max-files=19" in l for l in self.calls("gst-launch")), self.calls("gst-launch"))
        self.assertTrue(await self.wait_for(lambda: len(glob.glob(os.path.join(self.p.seg_dir, "seg_*.mkv"))) >= 5, 20))
        r = await self.p.save_clip("Short", 15)
        self.assertTrue(r["ok"], r)
        await self.p.set_replay(False)


if __name__ == "__main__":
    unittest.main()
