"""Backend tests: hardware button reader (fake Deck controller = a FIFO fed with HID-style reports). Run: python3 tests/run.py area hotkeys"""
from harness import *   # noqa: F401,F403
from harness import M, U, decky, Base


def report(buttons, header=(1, 0, 9, 0x40)):
    return bytes(header) + (1).to_bytes(4, "little") + buttons.to_bytes(8, "little") + bytes(48)


STEAM, MENU, VIEW = 0x2000, 0x4000, 0x1000


class Hotkeys(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        sysfs = os.path.join(self.root, "sys", "hidraw1", "device")
        os.makedirs(sysfs); os.makedirs(os.path.join(self.root, "dev"))
        with open(os.path.join(sysfs, "uevent"), "w") as f:
            f.write("DRIVER=hid-steam\nHID_ID=0003:000028DE:00001205\nHID_NAME=Valve Software Steam Deck Controller\n")
        self.fifo = os.path.join(self.root, "dev", "hidraw1")
        os.mkfifo(self.fifo)
        self.p.HID_SYSFS, self.p.HID_DEV = os.path.join(self.root, "sys"), os.path.join(self.root, "dev")
        self.p._hid_start()
        self.w = None
        for _ in range(100):                      # the write end can only be opened once the reader has the FIFO open
            try:
                self.w = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK); break
            except OSError:
                await asyncio.sleep(0.05)
        self.assertIsNotNone(self.w, "reader never opened the device")

    async def asyncTearDown(self):
        if self.w is not None:
            os.close(self.w)
        self.p._hid_stop()
        await super().asyncTearDown()

    def send(self, buttons, **kw):
        os.write(self.w, report(buttons, **kw))

    def hotkeys(self):
        return [a[0]["action"] for n, a in decky.events if a and a[0].get("type") == "hotkey"]

    async def test_steam_plus_start_fires_one_clip_event(self):
        self.send(STEAM); self.send(MENU); await asyncio.sleep(0.4)
        self.assertEqual(self.hotkeys(), [], "a single button must not trigger")
        for _ in range(6):
            self.send(STEAM | MENU); await asyncio.sleep(0.03)   # held down: many reports (a FIFO is a byte stream: pace them)
        self.assertTrue(await self.wait_for(lambda: self.hotkeys() == ["clip"], 5), self.hotkeys())
        self.send(0); await asyncio.sleep(0.3)
        self.assertEqual(self.hotkeys(), ["clip"])
        st = await self.p.get_hotkey_status()
        self.assertEqual((st["state"], st["fired"]), ("reading", 1)); self.assertGreaterEqual(st["valid"], 5)
        self.assertIn("hidraw1", st["path"])

    async def test_other_report_types_are_counted_but_ignored(self):
        self.send(STEAM | MENU, header=(1, 0, 4, 0x40)); await asyncio.sleep(0.5)
        st = await self.p.get_hotkey_status()
        self.assertEqual((st["raw"], st["valid"], self.hotkeys()), (1, 0, []))
        self.assertEqual(st["headers"], ["01 00 04"])             # lets a photo of Diagnostics reveal a layout mismatch

    async def test_record_combo_needs_the_setting(self):
        self.send(STEAM | VIEW); await asyncio.sleep(0.4); self.send(0)
        self.assertEqual(self.hotkeys(), [])
        await self.p.set_settings({"hotkey_record": True})
        await asyncio.sleep(2.2)                                   # detector cooldown
        self.send(STEAM | VIEW)
        self.assertTrue(await self.wait_for(lambda: self.hotkeys() == ["record"], 5), self.hotkeys())

    async def test_clip_combo_can_be_disabled(self):
        await self.p.set_settings({"hotkey_clip": False})
        self.send(STEAM | MENU); await asyncio.sleep(0.5)
        self.assertEqual(self.hotkeys(), [])

    async def test_setting_off_stops_the_reader_and_on_restarts_it(self):
        await self.p.set_settings({"hid_hotkeys": False})
        self.assertEqual((await self.p.get_hotkey_status())["state"], "off")
        await self.p.set_settings({"hid_hotkeys": True})
        self.assertTrue(await self.wait_for(lambda: self.p.hid["state"] == "reading", 5), self.p.hid)

    async def test_unplugging_does_not_crash_the_reader(self):
        os.close(self.w); self.w = None
        await asyncio.sleep(0.8)
        self.assertTrue(self.p._hid_thread.is_alive())
        self.assertNotIn("Error", self.p.hid["error"])


class NoController(Base):
    async def test_missing_controller_is_reported_not_raised(self):
        self.p.HID_SYSFS = os.path.join(self.root, "nothing")
        self.p._hid_start()
        self.assertTrue(await self.wait_for(lambda: self.p.hid["state"] == "failed", 5), self.p.hid)
        self.assertIn("no Deck controller", self.p.hid["error"])
        self.p._hid_stop()


if __name__ == "__main__":
    unittest.main()
