"""Backend tests: sensors (GPU/power readout) and the capability probe saved on disk. Run: python3 tests/run.py area system"""
from harness import *   # noqa: F401,F403
from harness import M, Base, DeckBase, DECK_ELEMENTS


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Sensors(Base):
    def tree(self, gpu="37", amd="9800000", bat="12100000"):
        r = os.path.join(self.root, "sys")
        shutil.rmtree(r, ignore_errors=True)
        if gpu is not None: put(f"{r}/drm/card1/device/gpu_busy_percent", gpu + "\n")
        put(f"{r}/hwmon/hwmon2/name", "acpitz\n"); put(f"{r}/hwmon/hwmon2/power1_average", "1\n")
        if amd is not None:
            put(f"{r}/hwmon/hwmon3/name", "amdgpu\n"); put(f"{r}/hwmon/hwmon3/power1_average", amd + "\n")
        if bat is not None: put(f"{r}/power_supply/BAT1/power_now", bat + "\n")
        self.p.SYS_DRM, self.p.SYS_HWMON, self.p.SYS_POWER = f"{r}/drm", f"{r}/hwmon", f"{r}/power_supply"

    async def test_reads_gpu_load_and_prefers_the_apu_power_sensor(self):
        self.tree()
        self.assertEqual(self.p._read_gpu_busy(), 37)
        self.assertEqual(self.p._read_power(), (9.8, "APU"))          # not the acpitz one, not the battery

    async def test_falls_back_to_the_battery_then_to_nothing(self):
        self.tree(amd=None); self.assertEqual(self.p._read_power(), (12.1, "battery"))
        self.tree(amd=None, bat=None); self.assertEqual(self.p._read_power(), (None, ""))
        r = os.path.join(self.root, "sys")
        put(f"{r}/power_supply/BAT1/current_now", "1500000\n"); put(f"{r}/power_supply/BAT1/voltage_now", "7500000\n")
        self.assertEqual(self.p._read_power(), (11.25, "battery"))   # 1.5 A * 7.5 V

    async def test_junk_and_missing_files_never_raise(self):
        self.tree(gpu="abc", amd="n/a", bat="")
        self.assertEqual((self.p._read_gpu_busy(), self.p._read_power()), (None, (None, "")))
        self.tree(gpu="250"); self.assertEqual(self.p._read_gpu_busy(), 100)
        self.p.SYS_DRM = self.p.SYS_HWMON = self.p.SYS_POWER = os.path.join(self.root, "nope")
        self.assertEqual((self.p._read_gpu_busy(), self.p._read_power()), (None, (None, "")))

    async def test_status_and_self_test_expose_the_readout(self):
        self.tree()
        self.p._sample_cost(time.time())
        st = await self.p.get_status()
        self.assertEqual((st["gpu"], st["watts"], st["power_src"]), (37, 9.8, "APU"))
        by = {r["name"]: r for r in await self.p.run_self_test()}
        self.assertTrue(by["Sensor: GPU load"]["ok"]); self.assertIn("37%", by["Sensor: GPU load"]["detail"])
        self.assertTrue(by["Sensor: power"]["ok"]); self.assertIn("9.8 W (APU)", by["Sensor: power"]["detail"])
        self.tree(gpu=None, amd=None, bat=None)
        by = {r["name"]: r for r in await self.p.run_self_test()}
        self.assertFalse(by["Sensor: GPU load"]["ok"]); self.assertFalse(by["Sensor: power"]["ok"])

    async def test_the_watchdog_keeps_sampling(self):
        self.tree()
        await asyncio.sleep(1.0)
        self.assertEqual(self.p.gpu, 37)
        self.assertGreater(len(self.p.cost.samples), 2)


class CapsOnDisk(Base):
    """The probe (gst-inspect x20, ffmpeg encoder test-encodes) costs seconds; after a reboot it should be skipped."""

    def private_bin(self):
        """A private copy of the fake tools: a test can 'update' them without touching the shared ones."""
        d = os.path.join(self.root, "bin"); shutil.copytree(os.path.join(HERE, "fakebin"), d)
        os.environ["PATH"] = d + ":" + os.environ["PATH"]
        return d

    async def boot(self):
        p = M.Plugin(); await p._main(); return p

    async def test_a_second_boot_reuses_the_saved_probe(self):
        self.private_bin()
        p2 = await self.boot()
        c1 = await p2._capabilities()
        self.assertFalse(c1["from_cache"]); self.assertTrue(os.path.exists(p2._caps_path()))
        n = len(self.calls("gst-inspect")); self.assertGreater(n, 5)
        p3 = await self.boot()                                   # "reboot": new plugin object, same settings folder
        c2 = await p3._capabilities()
        self.assertTrue(c2["from_cache"]); self.assertEqual(len(self.calls("gst-inspect")), n, "no probing at all")
        self.assertEqual((c2["ff_usable"], c2["encoders"], c2["src_props"]), (c1["ff_usable"], c1["encoders"], c1["src_props"]))
        await p2._unload(); await p3._unload()

    async def test_a_changed_tool_or_kernel_or_age_forces_a_new_probe(self):
        d = self.private_bin()
        p2 = await self.boot(); await p2._capabilities()
        n = len(self.calls("gst-inspect"))
        with open(os.path.join(d, "gst-inspect-1.0"), "a") as f: f.write("# SteamOS update\n")        # tool changed
        p3 = await self.boot(); c = await p3._capabilities()
        self.assertFalse(c["from_cache"]); self.assertGreater(len(self.calls("gst-inspect")), n)
        n = len(self.calls("gst-inspect"))
        real = os.uname
        class K:                                                    # kernel updated
            release = "99.0-new"
        os.uname = lambda: K()
        try:
            p4 = await self.boot(); c = await p4._capabilities()
        finally:
            os.uname = real
        self.assertFalse(c["from_cache"]); self.assertGreater(len(self.calls("gst-inspect")), n)
        p5 = await self.boot(); p5.CAPS_MAX_AGE = 0                  # too old
        self.assertFalse((await p5._capabilities())["from_cache"])
        for p in (p2, p3, p4, p5): await p._unload()

    async def test_force_corrupt_files_and_failed_probes(self):
        self.private_bin()
        p2 = await self.boot(); await p2._capabilities()
        n = len(self.calls("gst-inspect"))
        self.assertFalse((await p2._capabilities(force=True))["from_cache"])           # the self-test always looks again
        self.assertGreater(len(self.calls("gst-inspect")), n)
        with open(p2._caps_path(), "w") as f: f.write("{not json")
        p3 = await self.boot(); self.assertFalse((await p3._capabilities())["from_cache"])
        with open(p3._caps_path()) as f: self.assertEqual(json.load(f)["v"], 1)          # repaired
        os.unlink(p3._caps_path())
        os.environ["FAKE_ELEMENTS"] = "pipewiresrc"                                       # nothing usable at all
        p4 = await self.boot()
        async def nope(ffmpeg, enc): return False, "no"
        p4._probe_ff_encoder = nope
        c = await p4._capabilities()
        self.assertEqual((c["encoders"], c["ff_usable"]), ([], []))
        self.assertFalse(os.path.exists(p4._caps_path()), "a failed probe must never be saved")
        for p in (p2, p3, p4): await p._unload()

    async def test_a_failed_start_forgets_the_saved_probe(self):
        self.private_bin()
        self.p.caps, self.p.caps_time = {}, 0.0                  # the harness pre-fills this to save time; this test needs a real probe
        await self.p.start_recording("ok"); await self.p.stop_recording(); await self.wait_for(lambda: self.p.state == "idle", 20)
        self.assertTrue(os.path.exists(self.p._caps_path()))
        os.environ["FAKE_GST_FAIL"] = "1"
        st = await self.p.start_recording("x")
        self.assertIn("Recorder exited immediately", st["error"])
        self.assertFalse(os.path.exists(self.p._caps_path()), "the saved probe may be outdated")


class DeckLayoutStart(DeckBase):
    async def test_starting_again_does_not_probe_again_when_gstreamer_has_no_encoder(self):
        """Regression (found while reading the code): on the Deck GStreamer has no encoders, which the start code read as
        'nothing found' and re-probed on EVERY start."""
        await self.p.start_recording("A"); await self.p.stop_recording()
        self.assertTrue(await self.wait_for(lambda: self.p.state == "idle", 40))
        t0, n = self.p.caps_time, len(self.calls("gst-inspect"))
        await self.p.start_recording("B"); await self.p.stop_recording()
        self.assertTrue(await self.wait_for(lambda: self.p.state == "idle", 40))
        self.assertEqual(self.p.caps_time, t0); self.assertEqual(len(self.calls("gst-inspect")), n)


if __name__ == "__main__":
    unittest.main()
