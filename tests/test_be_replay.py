"""Backend tests: replay. Run: python3 tests/run.py area replay"""
from harness import *   # noqa: F401,F403
from harness import M, U, decky, probe, probe_video, mk_sample, Base, DECK_ELEMENTS


class Replay(Base):
    async def test_buffer_then_clip(self):
        await self.p.set_replay(True)
        self.assertEqual(self.p.state, "buffering")
        self.assertTrue(await self.wait_for(lambda: len(glob.glob(os.path.join(self.p.seg_dir, "seg_*.mkv"))) >= 6))
        r = await self.p.save_clip("Hades", 30)
        self.assertTrue(r["ok"], r)
        f = self.p.last_saved["path"]; kinds, dur, _ = probe(f)
        self.assertEqual(kinds, {"video", "audio"}); self.assertGreater(dur, 3.0)
        self.assertIn("Hades_clip30s", f)
        r2 = await self.p.save_clip("Hades", 30)
        self.assertFalse(r2["ok"]); self.assertIn("Wait", r2["error"])
        await self.p.set_replay(False)
        self.assertEqual(self.p.state, "idle")
        self.assertFalse(os.path.exists(self.p.seg_dir)); self.assertEqual(self.pa_modules(), [])

    async def test_clip_without_buffer(self):
        r = await self.p.save_clip("x")
        self.assertFalse(r["ok"])

    async def test_manual_record_pauses_and_resumes_replay(self):
        await self.p.set_replay(True)
        await self.p.start_recording("Both")
        self.assertEqual(self.p.mode, "manual")
        await self.p.stop_recording()
        self.assertTrue(await self.wait_for(lambda: self.p.mode == "replay" and self.p.state == "buffering"))
        self.assertTrue(self.p.last_saved.get("kind") == "recording")
        await self.p.set_replay(False)

    async def test_replay_crash_restarts_and_loop_gives_up(self):
        await self.p.set_replay(True)
        n0 = len(self.calls("gst-launch"))
        self.p.proc.send_signal(signal.SIGKILL)
        self.assertTrue(await self.wait_for(lambda: len(self.calls("gst-launch")) > n0 and self.p.state == "buffering"))
        os.environ["FAKE_GST_FAIL"] = "1"
        self.p.proc.send_signal(signal.SIGKILL)
        self.assertTrue(await self.wait_for(lambda: self.p.state == "error", 25))
        self.assertFalse(self.p.s["replay_enabled"] and self.p.state != "error")

    async def test_sleep_resume_restarts_buffer(self):
        await self.p.set_replay(True)
        n0 = len(self.calls("gst-launch"))
        real = M.core.woke_from_sleep
        flag = {"once": True}
        def fake(gap, exp, slack=10.0):
            if flag["once"]: flag["once"] = False; return True
            return real(gap, exp, slack)
        M.core.woke_from_sleep = fake
        try:
            self.assertTrue(await self.wait_for(lambda: len(self.calls("gst-launch")) > n0))
            self.assertTrue(await self.wait_for(lambda: self.p.state == "buffering"))
        finally:
            M.core.woke_from_sleep = real
        self.assertEqual(self.p.mode, "replay")

    async def test_enabled_replay_autostarts_after_restart(self):
        await self.p.set_replay(True)
        await self.p._unload()
        p2 = M.Plugin(); await p2._main()
        M.Plugin._DELAY = 0
        self.assertTrue(await self.wait_for(lambda: p2.state == "buffering", 20))
        await p2._unload()

    async def test_changing_bitrate_restarts_buffer_but_gain_does_not(self):
        await self.p.set_settings({"mic_enabled": True})
        await self.p.set_replay(True)
        n0 = len(self.calls("gst-launch"))
        await self.p.set_settings({"mic_gain_db": 3})
        self.assertEqual(len(self.calls("gst-launch")), n0)
        await self.p.set_settings({"video_kbps": 8000})
        self.assertGreater(len(self.calls("gst-launch")), n0)
        self.assertIn("bitrate=8000", self.calls("gst-launch")[-1])


class ReplayVerifyFallback(Base):
    SAMPLE_AUDIO = False       # segments come out video-only although audio is expected

    async def test_bad_segments_flip_muxer_variant_once_then_warn(self):
        await self.p.set_replay(True)
        self.assertTrue(await self.wait_for(lambda: any("muxer-factory=matroskamux" in l for l in self.calls("gst-launch")), 30))
        self.assertTrue(any("muxer=matroskamux muxer-pad-map" in l for l in self.calls("gst-launch")))
        self.assertTrue(await self.wait_for(lambda: any("look wrong" in w for w in self.p.warnings), 30), self.p.warnings)
        self.assertEqual(len([l for l in self.calls("gst-launch")]), 2)       # no endless flip-flop


if __name__ == "__main__":
    unittest.main()
