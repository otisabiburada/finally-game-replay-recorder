"""Backend tests: ffmpeg. Run: python3 tests/run.py area ffmpeg"""
from harness import *   # noqa: F401,F403
from harness import M, U, decky, probe, probe_video, mk_sample, Base, DeckBase, DECK_ELEMENTS


class DeckLike(DeckBase):
    """Tests on the Deck layout; the fixture lives in harness.DeckBase."""

    async def test_old_gstreamer_only_engine_cannot_record_on_this_deck(self):
        await self.p.set_settings({"engine": "gstreamer"})          # = what v1/v2 did
        st = await self.p.start_recording("X")
        self.assertEqual(st["state"], "error"); self.assertIn("no H.264 encoder", st["error"])

    async def test_new_auto_engine_records_with_ffmpeg(self):
        o = await self.p.get_options()
        self.assertEqual((o["engine"], o["can_record"]), ("ffmpeg", True))
        for fmt in ("mkv", "mp4"):
            await self.p.set_settings({"format": fmt})
            st = await self.p.start_recording("Deck Game")
            self.assertEqual(st["state"], "recording", st); self.assertEqual(st["engine"], "ffmpeg")
            await asyncio.sleep(2.5)
            await self.p.stop_recording()
            self.assertTrue(await self.wait_for(lambda: self.p.state == "idle" and self.p.last_saved.get("name", "").endswith(fmt), 30))
            s = probe_video(self.p.last_saved["path"])
            vid = [x for x in s if x["codec_type"] == "video"][0]
            self.assertEqual((vid["codec_name"], vid["width"], vid["height"]), ("h264", 1280, 800))
            self.assertIn("audio", [x["codec_type"] for x in s])
            self.assertGreater(probe(self.p.last_saved["path"])[1], 1.5)
        self.assertEqual(self.pa_modules(), []); self.assertEqual(self.staging_left(), [])
        self.assertFalse(self.leftovers(), "gst-launch/ffmpeg must not outlive the recording")

    async def test_screen_size_is_probed_unless_a_preset_is_chosen(self):
        await self.p.start_recording("A"); await asyncio.sleep(0.3)
        self.assertTrue(any(l for l in self.calls("gst-launch") if "-v -e pipewiresrc num-buffers=1" in l))
        self.assertTrue(any("width=1280,height=800,framerate=30/1" in l for l in self.calls("gst-launch")))
        await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle", 30)
        await self.p.set_settings({"resolution": "960x600"}); self.p.size_cache = (0.0, None)
        n = len([l for l in self.calls("gst-launch") if "num-buffers=1" in l])
        await self.p.start_recording("B"); await asyncio.sleep(0.3)
        self.assertEqual(len([l for l in self.calls("gst-launch") if "num-buffers=1" in l]), n)
        self.assertTrue(any("width=960,height=600" in l for l in self.calls("gst-launch")))
        await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle", 30)

    async def test_replay_buffer_and_clip_with_ffmpeg(self):
        await self.p.set_replay(True)
        self.assertEqual((self.p.state, self.p.engine), ("buffering", "ffmpeg"))
        self.assertTrue(await self.wait_for(lambda: len(glob.glob(os.path.join(self.p.seg_dir, "seg_*.mkv"))) >= 6, 30))
        r = await self.p.save_clip("Deck Game", 30)
        self.assertTrue(r["ok"], r)
        f = self.p.last_saved["path"]; kinds, dur, _ = probe(f)
        self.assertEqual(kinds, {"video", "audio"}); self.assertGreater(dur, 3.0)
        await self.p.set_replay(False)
        self.assertFalse(os.path.exists(self.p.seg_dir)); self.assertFalse(self.leftovers())

    async def test_ffmpeg_engine_errors_are_readable_and_clean(self):
        os.environ["FAKE_GST_FAIL"] = "1"
        st = await self.p.start_recording("X")
        self.assertEqual(st["state"], "error"); self.assertIn("boom no stream", st["error"])
        self.assertEqual(self.pa_modules(), []); self.assertTrue(await self.wait_for(lambda: not self.leftovers(), 10))

    async def test_hung_capture_is_rescued_with_ffmpeg_engine(self):
        os.environ["FAKE_GST_HANG"] = "2.0"
        self.p.s["stall_seconds"] = 3; M.Plugin.STALL_GRACE = 0.3
        await self.p.start_recording("Hung")
        pid = self.p.proc.pid
        self.assertTrue(await self.wait_for(lambda: self.p.last_saved.get("kind") == "recording", 40),
                        "first file rescued\n" + "\n".join(list(self.p.notes)[-14:]) + f"\nstate={self.p.state} err={self.p.last_error}")
        self.assertTrue(await self.wait_for(lambda: self.p.state == "recording" and self.p.proc and self.p.proc.pid != pid, 30))
        self.assertIn("video", {x["codec_type"] for x in probe_video(self.p.last_saved["path"])})
        await self.p.stop_recording()
        self.assertTrue(await self.wait_for(lambda: self.p.state == "idle" and len(self.out_files()) == 2, 40))

    async def test_slow_encoder_gets_a_warning_but_healthy_one_does_not(self):
        M.Plugin.SPEED_SECONDS = 2.0; M.Plugin.STALL_GRACE = 0.5
        try:
            os.environ["FAKE_FEED_FACTOR"] = "0.5"                       # screen delivers frames at half the needed rate
            await self.p.start_recording("Slow")
            self.assertTrue(await self.wait_for(lambda: any("cannot keep up" in w for w in self.p.warnings), 40), self.p.warnings)
            self.assertLess(self.p.speed, 0.9)
            await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle", 40)
            os.environ["FAKE_FEED_FACTOR"] = "1"
            await self.p.start_recording("Fine")
            await asyncio.sleep(7)
            self.assertFalse(any("cannot keep up" in w for w in self.p.warnings), self.p.warnings)
            self.assertGreater(self.p.speed or 0, 0.9)
            await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle", 40)
        finally:
            os.environ.pop("FAKE_FEED_FACTOR", None); M.Plugin.SPEED_SECONDS = 10.0

    async def test_audio_offset_shifts_the_sound(self):
        starts = []
        for off in (0, 600):
            await self.p.set_settings({"audio_offset_ms": off})
            await self.p.start_recording("Sync")
            await asyncio.sleep(2.5)
            await self.p.stop_recording()
            self.assertTrue(await self.wait_for(lambda: self.p.state == "idle" and self.p.last_saved.get("ts", 0) > 0, 40))
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=start_time",
                                "-of", "csv=p=0", self.p.last_saved["path"]], capture_output=True, text=True)
            starts.append(float(r.stdout.strip() or 0))
            self.p.last_saved = {}
        self.assertLess(starts[0], 0.2); self.assertGreater(starts[1], 0.45, starts)

    async def test_status_shows_minutes_left(self):
        st = await self.p.get_status()
        self.assertIn("minutes_left", st); self.assertGreaterEqual(st["minutes_left"], 0)

    async def test_cpu_is_measured_and_priority_is_lowered(self):
        await self.p.start_recording("Cpu")
        self.assertEqual(os.getpriority(os.PRIO_PROCESS, self.p.proc.pid), 10)
        self.assertEqual(os.getpriority(os.PRIO_PROCESS, self.p.src_proc.pid), 10)
        self.assertTrue(await self.wait_for(lambda: (self.p.cpu or 0) > 0, 30), self.p.cpu)
        st = await self.p.get_status()
        self.assertGreater(st["cpu"], 0)
        self.assertTrue(any("-threads 2" in n for n in self.p.notes), "x264 must be limited to 2 threads by default")
        await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle", 40)
        await self.p.set_settings({"low_priority": False})
        await self.p.start_recording("Normal")
        self.assertEqual(os.getpriority(os.PRIO_PROCESS, self.p.proc.pid), 0)
        await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle", 40)

    async def test_self_test_on_this_deck_layout(self):
        res = await self.p.run_self_test()
        by = {r["name"]: r for r in res}
        self.assertTrue(by["Engine"]["ok"] and by["Engine"]["detail"].startswith("ffmpeg"), by["Engine"])
        self.assertTrue(by["test ffmpeg libx264 record"]["ok"], by["test ffmpeg libx264 record"])
        self.assertTrue(by["test ffmpeg libx264 replay"]["ok"], by["test ffmpeg libx264 replay"])
        self.assertTrue(by["Screen capture - exactly like recording"]["ok"])
        self.assertFalse(by["ffmpeg encoder h264_vaapi"]["ok"])      # listed by ffmpeg but no GPU here: honestly reported


if __name__ == "__main__":
    unittest.main()
