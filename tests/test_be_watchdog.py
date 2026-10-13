"""Backend tests: watchdog. Run: python3 tests/run.py area watchdog"""
from harness import *   # noqa: F401,F403
from harness import M, U, decky, probe, probe_video, mk_sample, Base, DECK_ELEMENTS


class Stall(Base):
    """A hung encoder: process alive, no new data, SIGINT ignored. The original plugin has no check for this -
    `stall_seconds = 0` reproduces that behaviour (state stays 'recording' forever)."""
    SAMPLE_AUDIO = True

    def arm(self, seconds=1.5, hang_after=1.2):
        os.environ["FAKE_GST_HANG"] = str(hang_after)
        self.p.s["stall_seconds"] = seconds
        M.Plugin.STALL_GRACE = 0.3

    async def test_old_behaviour_hung_recording_looks_fine_forever(self):
        os.environ["FAKE_GST_HANG"] = "1.0"
        self.p.s["stall_seconds"] = 0                      # = original: no detection
        await self.p.start_recording("Hung")
        pid = self.p.proc.pid
        await asyncio.sleep(5)
        self.assertEqual(self.p.state, "recording")
        self.assertEqual(self.p.proc.pid, pid)
        self.assertEqual(self.p.last_saved, {})            # nothing was ever rescued

    async def test_manual_stall_saves_what_exists_and_continues_in_new_file(self):
        self.arm()
        await self.p.start_recording("Hung")
        pid = self.p.proc.pid
        self.assertTrue(await self.wait_for(lambda: self.p.last_saved.get("kind") == "recording", 25), "first file must be rescued")
        first = self.p.last_saved["path"]
        self.assertTrue(await self.wait_for(lambda: self.p.state == "recording" and self.p.proc and self.p.proc.pid != pid, 25))
        self.assertTrue(any("froze" in w for w in self.p.warnings), self.p.warnings)
        kinds, dur, _ = probe(first); self.assertIn("video", kinds)
        await self.p.stop_recording()
        self.assertTrue(await self.wait_for(lambda: self.p.state == "idle" and self.p.last_saved["path"] != first, 25))
        self.assertEqual(len(self.out_files()), 2)
        self.assertEqual(self.pa_modules(), [])
        self.assertEqual(self.staging_left(), [])

    async def test_replay_stall_restarts_buffer(self):
        self.arm(1.5, 1.5)
        await self.p.set_replay(True)
        n0 = len(self.calls("gst-launch"))
        self.assertEqual(n0, 1)
        self.assertTrue(await self.wait_for(lambda: len(self.calls("gst-launch")) == 2 and self.p.state == "buffering", 30))
        self.assertTrue(any("froze" in w for w in self.p.warnings), self.p.warnings)
        self.assertEqual(len(self.pa_modules()) > 0, True)

    async def test_repeated_stalls_give_up_instead_of_looping(self):
        self.arm(1.2, 0.6)
        os.environ["FAKE_GST_HANG_ALWAYS"] = "1"
        await self.p.start_recording("Never works")
        self.assertTrue(await self.wait_for(lambda: self.p.state == "error", 60), self.p.state)
        self.assertIn("no data", self.p.last_error.lower())
        self.assertLessEqual(len(self.calls("gst-launch")), 5)
        self.assertEqual(self.pa_modules(), [])

    async def test_healthy_recording_is_not_flagged(self):
        os.environ["FAKE_GST_HANG"] = "4.0"                # keeps growing for 4s, threshold is 3s
        self.p.s["stall_seconds"] = 3; M.Plugin.STALL_GRACE = 0.3
        await self.p.start_recording("Fine")
        pid = self.p.proc.pid
        await asyncio.sleep(2.8)
        self.assertEqual((self.p.state, self.p.proc.pid), ("recording", pid))
        await self.p.stop_recording()


class Robustness(Base):
    async def test_keepalive_passed_only_when_available(self):
        await self.p.start_recording("K"); await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle")
        self.assertIn("pipewiresrc do-timestamp=true keepalive-time=1000 !", self.calls("gst-launch")[-1])
        os.environ["FAKE_NO_KEEPALIVE"] = "1"
        p2 = M.Plugin(); await p2._main(); p2.s["stall_seconds"] = 0
        await p2.start_recording("K")
        self.assertNotIn("keepalive", self.calls("gst-launch")[-1])
        await p2.stop_recording(); await self.wait_for(lambda: p2.state == "idle"); await p2._unload()

    async def test_replay_refuses_when_ram_is_too_small(self):
        await self.p.set_settings({"buffer_seconds": 600, "video_kbps": 30000})
        self.p._shm_free_mb = lambda: 300
        st = await self.p.set_replay(True)
        self.assertEqual(st["state"], "error"); self.assertIn("MB of RAM", st["error"])
        self.assertEqual(self.calls("gst-launch"), []); self.assertEqual(self.pa_modules(), [])

    async def test_error_state_clears_by_itself(self):
        self.p.ERROR_CLEAR_SECONDS = 1.0
        os.environ["FAKE_GST_FAIL"] = "1"
        self.assertEqual((await self.p.start_recording("x"))["state"], "error")
        self.assertTrue(await self.wait_for(lambda: self.p.state == "idle", 6))
        self.assertEqual(self.p.last_error, "")

    async def test_replay_that_cannot_start_gives_up_after_four_retries(self):
        self.p.ERROR_CLEAR_SECONDS = 0.5
        os.environ["FAKE_GST_FAIL"] = "1"
        await self.p.set_replay(True)
        self.assertTrue(await self.wait_for(lambda: not self.p.s["replay_enabled"], 40))
        self.assertIn("4 times", self.p.last_error)
        n = len(self.calls("gst-launch")); await asyncio.sleep(2)
        self.assertEqual(len(self.calls("gst-launch")), n, "must not keep retrying")
        self.assertLessEqual(n, 6)

    async def test_replay_that_starts_late_is_picked_up(self):
        self.p.ERROR_CLEAR_SECONDS = 0.5
        os.environ["FAKE_GST_FAIL"] = "1"
        await self.p.set_replay(True)                       # e.g. PipeWire not ready right after boot
        os.environ.pop("FAKE_GST_FAIL")
        self.assertTrue(await self.wait_for(lambda: self.p.state == "buffering", 20))
        self.assertTrue(self.p.s["replay_enabled"])

    async def test_one_blip_outside_game_mode_does_not_stop_recording(self):
        await self.p.start_recording("Blip")
        calls = {"n": 0}
        def blip():
            calls["n"] += 1
            return not (1 <= calls["n"] <= 2)
        U._in_game_mode = blip
        await asyncio.sleep(1.5)
        self.assertEqual(self.p.state, "recording")
        self.assertGreater(calls["n"], 4)
        await self.p.stop_recording()

    async def test_mic_gain_applies_live_during_recording(self):
        await self.p.set_settings({"mic_enabled": True, "mic_source": "fake_mic"})
        await self.p.start_recording("Live")
        n0 = len(self.calls("gst-launch"))
        st = await self.p.set_settings({"mic_gain_db": 3})
        self.assertTrue(any("set-source-volume Finally-Rec-Mic 3.0dB" in l for l in self.calls("pactl")))
        self.assertEqual(len(self.calls("gst-launch")), n0)
        self.assertFalse(any("next recording" in w for w in self.p.warnings))
        await self.p.stop_recording()

    async def test_self_test_checks_audio_sink_and_cleans_up(self):
        res = await self.p.run_self_test()
        by = {r["name"]: r for r in res}
        self.assertTrue(by["Audio: virtual sink"]["ok"], by["Audio: virtual sink"])
        self.assertTrue(by["Screen source keepalive"]["ok"])
        self.assertEqual(self.pa_modules(), [])

    async def test_start_does_not_rerun_gst_inspect_each_time(self):
        await self.p.start_recording("A"); await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle")
        t0 = self.p.caps_time
        await self.p.start_recording("B"); await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle")
        self.assertEqual(self.p.caps_time, t0)


if __name__ == "__main__":
    unittest.main()
