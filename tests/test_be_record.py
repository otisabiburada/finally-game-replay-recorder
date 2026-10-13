"""Backend tests: record. Run: python3 tests/run.py area record"""
from harness import *   # noqa: F401,F403
from harness import M, U, decky, probe, probe_video, mk_sample, Base, DECK_ELEMENTS


class ManualRecording(Base):
    async def test_record_mkv_and_mp4_end_to_end(self):
        for fmt in ("mkv", "mp4"):
            await self.p.set_settings({"format": fmt})
            st = await self.p.start_recording("Elden Ring: Shadow/Erdtree")
            self.assertEqual(st["state"], "recording", st)
            self.assertTrue(self.pa_modules(), "virtual sink should exist while recording")
            await asyncio.sleep(0.5)
            await self.p.stop_recording()
            self.assertTrue(await self.wait_for(lambda: self.p.state == "idle" and self.p.last_saved.get("name", "").endswith(fmt)))
            f = self.p.last_saved["path"]
            kinds, dur, _ = probe(f)
            self.assertEqual(kinds, {"video", "audio"}); self.assertGreater(dur, 0.5)
            self.assertIn("Elden Ring Shadow Erdtree", os.path.basename(f))
            self.assertEqual(self.pa_modules(), [], "audio modules must be unloaded")
            self.assertEqual(self.staging_left(), [])
        self.assertEqual(len([e for e in decky.events if e[1][0]["type"] == "saved"]), 2)

    async def test_start_failure_reports_real_reason_and_cleans_up(self):
        os.environ["FAKE_GST_FAIL"] = "1"
        st = await self.p.start_recording("X")
        self.assertEqual(st["state"], "error")
        self.assertIn("boom no stream", st["error"])
        self.assertEqual(self.pa_modules(), []); self.assertEqual(self.out_files(), [])
        self.assertTrue(any(e[1][0]["type"] == "error" for e in decky.events))
        os.environ.pop("FAKE_GST_FAIL")
        st = await self.p.start_recording("X")                 # can recover after an error
        self.assertEqual(st["state"], "recording")
        await self.p.stop_recording()

    async def test_double_start_and_stop_when_idle_are_harmless(self):
        await self.p.start_recording("A"); await self.p.start_recording("A")
        self.assertEqual(len(self.calls("gst-launch")), 1)
        await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle")
        await self.p.stop_recording()
        self.assertEqual(self.p.state, "idle")

    async def test_gst_crash_mid_recording_still_saves_file(self):
        await self.p.start_recording("Crashy")
        await asyncio.sleep(0.4)
        self.p.proc.send_signal(signal.SIGKILL)
        self.assertTrue(await self.wait_for(lambda: self.p.last_saved.get("kind") == "recording"), "file should be finalised")
        self.assertIn("stopped unexpectedly", self.p.last_error)
        self.assertEqual(self.pa_modules(), [])
        self.assertEqual(self.p.state, "idle")

    async def test_low_disk_refuses_to_start(self):
        await self.p.set_settings({"min_free_mb": 100000})
        st = await self.p.start_recording("X")
        self.assertEqual(st["state"], "error"); self.assertIn("MB free", st["error"])

    async def test_leaving_game_mode_stops_and_saves(self):
        await self.p.start_recording("GM")
        U._in_game_mode = lambda: False
        self.assertTrue(await self.wait_for(lambda: self.p.last_saved.get("kind") == "recording"))
        self.assertEqual(self.p.state, "idle")

    async def test_watchdog_survives_exception(self):
        orig = M.shutil.disk_usage
        calls = {"n": 0}
        def boom(*a, **k):
            calls["n"] += 1
            if calls["n"] < 3: raise RuntimeError("disk")
            return orig(*a, **k)
        await self.p.start_recording("W")
        M.shutil.disk_usage = boom
        await asyncio.sleep(1.2)
        M.shutil.disk_usage = orig
        self.assertFalse(self.p.watchdog_task.done(), "watchdog task must keep running")
        await self.p.stop_recording()


class Mic(Base):
    async def test_mic_chain_loaded_and_removed(self):
        await self.p.set_settings({"mic_enabled": True, "mic_gain_db": 8, "mic_source": "fake_mic"})
        await self.p.start_recording("M")
        mods = " ".join(self.pa_modules())
        for needle in ("module-null-sink", "module-echo-cancel", "source_master=fake_mic", "module-loopback"):
            self.assertIn(needle, mods)
        self.assertTrue(any("set-source-volume Finally-Rec-Mic 8.0dB" in l for l in self.calls("pactl")))
        await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle")
        self.assertEqual(self.pa_modules(), [])

    async def test_pulse_failure_records_video_only_with_warning(self):
        os.environ["FAKE_PA_FAIL"] = "module-null-sink"
        st = await self.p.start_recording("NoAudio")
        self.assertEqual(st["state"], "recording")
        self.assertTrue(any("without audio" in w for w in st["warnings"]), st["warnings"])
        self.assertNotIn("pulsesrc", self.calls("gst-launch")[-1])
        await self.p.stop_recording()

    async def test_orphan_modules_swept_on_startup(self):
        open(self.pa, "w").write("31\tmodule-null-sink\tsink_name=Finally-Rec-Sink\n32\tmodule-other\tx\n")
        p2 = M.Plugin(); await p2._main()
        self.assertEqual([l.split("\t")[0] for l in self.pa_modules()], ["32"])
        await p2._unload()


if __name__ == "__main__":
    unittest.main()
