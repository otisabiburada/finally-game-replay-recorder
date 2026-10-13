"""Backend tests: adaptive cost control (pause without a game, automatic step-down) and the zero-copy probe.
Run: python3 tests/run.py area adapt"""
from harness import *   # noqa: F401,F403
from harness import M, Base, DeckBase


class IdlePause(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        M.Plugin.NO_GAME_SECONDS = 0.6

    async def asyncTearDown(self):
        M.Plugin.NO_GAME_SECONDS = 5.0
        await super().asyncTearDown()

    async def test_buffer_waits_without_a_game_and_resumes_with_one(self):
        await self.p.set_replay(True)
        self.assertEqual(self.p.state, "buffering")
        await self.p.report_game("")
        self.assertTrue(await self.wait_for(lambda: self.p.state == "idle" and self.p.paused, 10), self.p.state)
        self.assertTrue(self.p.s["replay_enabled"], "pausing must not switch the feature off")
        self.assertFalse(os.path.exists(self.p.seg_dir)); self.assertEqual(self.pa_modules(), [])
        n = len(self.calls("gst-launch"))
        await asyncio.sleep(1.5)
        self.assertEqual(len(self.calls("gst-launch")), n, "no capture while paused")
        st = await self.p.get_status(); self.assertTrue(st["paused"])
        await self.p.report_game("Hades")
        self.assertTrue(await self.wait_for(lambda: self.p.state == "buffering", 10), self.p.state)
        self.assertFalse(self.p.paused); self.assertEqual(self.p.rearm_times, [], "resuming for a game is not a failure")

    async def test_starting_the_buffer_by_hand_is_not_counted_as_a_failed_retry(self):
        """Regression: while the buffer was still starting (state 'idle' for ~1 s) the watchdog counted a retry."""
        for _ in range(5):
            await self.p.set_replay(True)
            await asyncio.sleep(0.6)
            await self.p.set_replay(False)
        await self.p.set_replay(True)
        await asyncio.sleep(1.0)
        self.assertEqual(self.p.rearm_times, [])
        self.assertTrue(self.p.s["replay_enabled"])

    async def test_many_quick_game_switches_never_turn_replay_off(self):
        await self.p.set_replay(True)
        for _ in range(6):
            await self.p.report_game("")
            self.assertTrue(await self.wait_for(lambda: self.p.paused, 10))
            await self.p.report_game("Game")
            self.assertTrue(await self.wait_for(lambda: self.p.state == "buffering", 10))
        self.assertTrue(self.p.s["replay_enabled"])

    async def test_setting_off_or_unknown_game_keeps_the_buffer_running(self):
        await self.p.set_replay(True)
        await self.p.set_settings({"buffer_only_in_game": False})
        await self.p.report_game(""); await asyncio.sleep(1.5)
        self.assertEqual(self.p.state, "buffering")
        await self.p.set_settings({"buffer_only_in_game": True})
        await self.p.report_game("Game")
        self.assertEqual(self.p.state, "buffering")

    async def test_never_told_about_a_game_means_keep_running(self):
        await self.p.set_replay(True); await asyncio.sleep(1.5)
        self.assertEqual((self.p.state, self.p.game_running), ("buffering", None))

    async def test_manual_recording_ignores_the_idle_pause(self):
        await self.p.report_game(""); await asyncio.sleep(0.8)
        await self.p.start_recording("Desktop")
        await asyncio.sleep(1.5)
        self.assertEqual(self.p.state, "recording")
        await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle", 20)

    async def test_buffer_does_not_start_at_boot_on_the_home_screen(self):
        await self.p.set_settings({"replay_enabled": True})
        await self.p._unload()
        os.environ["FAKE_GST_FAIL"] = ""            # (keep fakes healthy)
        p2 = M.Plugin(); await p2._main()
        await p2.report_game("")
        await asyncio.sleep(1.2)
        self.assertNotEqual(p2.state, "buffering")
        await p2._unload()


class StepDown(DeckBase):
    """A feeder that only delivers half the frames (FAKE_FEED_FACTOR) = an encoder/capture that cannot keep up."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        M.Plugin.SPEED_SECONDS = 2.0; M.Plugin.STALL_GRACE = 0.5
        os.environ["FAKE_FEED_FACTOR"] = "0.4"
        await self.p.set_settings({"fps": 0, "resolution": "native", "video_kbps": 12000})

    async def asyncTearDown(self):
        os.environ.pop("FAKE_FEED_FACTOR", None); M.Plugin.SPEED_SECONDS = 10.0
        await super().asyncTearDown()

    async def test_slow_capture_steps_quality_down_by_itself(self):
        await self.p.start_recording("Heavy")
        self.assertTrue(await self.wait_for(lambda: self.p.s["fps"] == 30, 45), self.p.s)
        self.assertTrue(await self.wait_for(lambda: self.p.state == "recording" and any("30 fps" in w for w in self.p.warnings), 20), self.p.warnings)
        self.assertTrue(any("framerate=30/1" in l for l in self.calls("gst-launch")), "restarted at the new frame rate")
        self.assertTrue(self.p.last_saved.get("kind") == "recording" or await self.wait_for(lambda: self.p.last_saved.get("kind") == "recording", 20),
                        "the recording so far was saved, not lost")
        self.assertTrue(await self.wait_for(lambda: self.p.s["resolution"] == "960x600", 45), "still slow -> next step")
        self.assertEqual(self.p.s["fps"], 30)
        await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle", 40)

    async def test_switch_off_means_warning_only(self):
        await self.p.set_settings({"auto_step_down": False})
        await self.p.start_recording("Heavy")
        self.assertTrue(await self.wait_for(lambda: any("cannot keep up" in w for w in self.p.warnings), 30), self.p.warnings)
        self.assertEqual((self.p.s["fps"], self.p.s["resolution"]), (0, "native"))
        await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle", 40)


class Probe(Base):
    async def test_zero_copy_probe_fails_politely_without_a_gpu_device(self):
        self.p.DRI_GLOB = os.path.join(self.root, "no-such-dir", "card*")
        ok, detail = await self.p._probe_kmsgrab("ffmpeg")
        self.assertEqual((ok, detail), (False, "no DRM card found"))

    async def test_zero_copy_probe_reports_the_ffmpeg_error_and_privilege(self):
        card = os.path.join(self.root, "card9"); open(card, "w").close()
        self.p.DRI_GLOB = os.path.join(self.root, "card*")
        self.p.PROBE_KMSGRAB_UNPRIVILEGED = True            # force the real attempt even if this sandbox lacks CAP_SYS_ADMIN
        ok, detail = await self.p._probe_kmsgrab("ffmpeg")
        self.assertFalse(ok); self.assertIn("card9", detail)

    async def test_zero_copy_probe_is_skipped_without_the_capability(self):
        """Seen on the real Deck: 'NOT root'. Do not even try (the plugin would only spend 25 s to say the same)."""
        card = os.path.join(self.root, "card9"); open(card, "w").close()
        self.p.DRI_GLOB = os.path.join(self.root, "card*")
        self.p._has_cap_sys_admin = staticmethod(lambda: False)
        ok, detail = await self.p._probe_kmsgrab("ffmpeg")
        self.assertFalse(ok); self.assertTrue(detail.startswith("skipped"), detail); self.assertIn("CAP_SYS_ADMIN", detail)


class CaptureLadder(DeckBase):
    """The Deck photo: 'Screen capture (pipewiresrc) - Failed to set pipeline to PAUSED.' with no reason. The self-test
    now tries the plain element, the element + videoconvert, and the recorder's exact chain, and shows the real reason."""

    def by_name(self, res):
        return {r["name"]: r for r in res}

    async def test_plain_failure_shows_the_reason_and_the_recording_chain_still_passes(self):
        os.environ["FAKE_CAPTURE_FAIL_PLAIN"] = "1"
        try:
            by = self.by_name(await self.p.run_self_test())
        finally:
            os.environ.pop("FAKE_CAPTURE_FAIL_PLAIN", None)
        plain = by["Screen capture - plain: pipewiresrc ! fakesink"]
        self.assertFalse(plain["ok"]); self.assertIn("no more input formats", plain["detail"]); self.assertNotIn("Failed to set pipeline", plain["detail"])
        for k in ("Screen capture - with videoconvert", "Screen capture - exactly like recording"):
            self.assertTrue(by[k]["ok"], by[k]); self.assertEqual(by[k]["detail"], "1280x800")
        self.assertIn("game state unknown", by["Screen capture - context"]["detail"])
        self.assertTrue(any("capture probe 'plain" in n for n in self.p.notes), "the reason is also in the log")

    async def test_size_probe_uses_the_recording_chain_not_the_plain_one(self):
        os.environ["FAKE_CAPTURE_FAIL_PLAIN"] = "1"
        try:
            self.p.size_cache = (0.0, None)
            size = await self.p._probe_size(await self.p._capabilities())
        finally:
            os.environ.pop("FAKE_CAPTURE_FAIL_PLAIN", None)
        self.assertEqual(size, (1280, 800))

    async def test_when_everything_fails_every_line_says_why(self):
        os.environ["FAKE_GST_FAIL"] = "1"
        try:
            by = self.by_name(await self.p.run_self_test())
        finally:
            os.environ.pop("FAKE_GST_FAIL", None)
        fails = [r for k, r in by.items() if k.startswith("Screen capture - ") and k != "Screen capture - context"]
        self.assertEqual(len(fails), 3)
        for r in fails:
            self.assertFalse(r["ok"]); self.assertIn("boom no stream", r["detail"])


if __name__ == "__main__":
    unittest.main()
