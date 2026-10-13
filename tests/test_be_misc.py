"""Backend tests: misc. Run: python3 tests/run.py area misc"""
from harness import *   # noqa: F401,F403
from harness import M, U, decky, probe, probe_video, mk_sample, Base, DECK_ELEMENTS


class Files(Base):
    async def test_delete_rejects_traversal(self):
        os.makedirs(self.p._out_dir(), exist_ok=True)
        victim = os.path.join(decky.DECKY_USER_HOME, "keep.mkv"); open(victim, "w").write("x")
        for bad in ("../keep.mkv", "/etc/passwd", "a/b.mkv", ".hidden.mkv", "notes.txt", ""):
            self.assertFalse((await self.p.delete_recording(bad))["ok"], bad)
        self.assertTrue(os.path.exists(victim))
        good = os.path.join(self.p._out_dir(), "g.mkv"); open(good, "w").write("x")
        self.assertTrue((await self.p.delete_recording("g.mkv"))["ok"]); self.assertFalse(os.path.exists(good))

    async def test_list_recent_sorted(self):
        os.makedirs(self.p._out_dir(), exist_ok=True)
        for i, n in enumerate(("a.mkv", "b.mp4", "c.txt")):
            fp = os.path.join(self.p._out_dir(), n); open(fp, "w").write("x" * (i + 1)); os.utime(fp, (1000 + i,) * 2)
        self.assertEqual([r["name"] for r in await self.p.list_recent()], ["b.mp4", "a.mkv"])

    async def test_settings_persist_and_survive_garbage_file(self):
        await self.p.set_settings({"format": "mp4", "video_kbps": 9000, "bogus": 1})
        p2 = M.Plugin(); await p2._main()
        self.assertEqual((p2.s["format"], p2.s["video_kbps"], "bogus" in p2.s), ("mp4", 9000, False))
        await p2._unload()
        open(self.p.settings_file, "w").write("{not json")
        p3 = M.Plugin(); await p3._main()
        self.assertEqual(p3.s["format"], "mkv"); await p3._unload()


class Recovery(Base):
    async def test_unfinished_recording_recovered_on_start(self):
        os.makedirs(self.p.stage_dir, exist_ok=True)
        orphan = os.path.join(self.p.stage_dir, "rec_2026-01-01_00-00-00_1.mkv")
        shutil.copyfile(self.sample, orphan); os.utime(orphan, (time.time() - 100,) * 2)
        p2 = M.Plugin(); await p2._main()
        got = [f for f in os.listdir(p2._out_dir()) if f.startswith("Recovered_")]
        self.assertEqual(len(got), 1); self.assertFalse(os.path.exists(orphan))
        await p2._unload()


class Diagnostics(Base):
    async def test_self_test_passes_with_fakes(self):
        res = await self.p.run_self_test()
        bad = [r for r in res if not r["ok"] and not r["name"] in ("PipeWire socket", "GPU render node", "Game Mode", "ffmpeg encoder h264_vaapi", "Sensor: GPU load", "Sensor: power")]
        self.assertEqual(bad, [], bad)
        self.assertTrue(any(r["name"].startswith("test vaapih264enc replay") and r["ok"] for r in res))

    async def test_self_test_and_start_flag_a_system_without_capture_elements(self):
        os.environ["FAKE_ELEMENTS"] = "pipewiresrc,h264parse,matroskamux"      # no encoder AND no videoconvert/fdsink
        res = await self.p.run_self_test()
        self.assertFalse(next(r for r in res if r["name"] == "element videoconvert")["ok"])
        st = await self.p.start_recording("x")
        self.assertEqual(st["state"], "error"); self.assertIn("missing", st["error"])

    async def test_options(self):
        o = await self.p.get_options()
        self.assertIn("vaapih264enc", o["encoders"]); self.assertEqual(o["sources"], ["fake_mic"])
        self.assertIsInstance(o["pulse"], bool)


if __name__ == "__main__":
    unittest.main()
