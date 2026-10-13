"""Hardware button detection straight from the Steam Deck controller (hidraw), independent of Steam's UI focus.

Why: Steam's own UI only receives controller events while IT has focus (reported for other Decky plugins), so a
shortcut registered through SteamClient can fail inside a game. The Deck's controller HID node can be read by several
programs at once, so the backend watches it and tells the frontend (event 'hotkey'); the frontend does the action
because it knows the running game. Status is shown in Diagnostics - UNVERIFIED on a real Deck until it says 'reading'."""

import asyncio
import os
import re
import select
import threading
import time
from typing import Any, Dict, List

import recorder_core as core


class HotkeyMixin:
    HID_SYSFS = "/sys/class/hidraw"
    HID_DEV = "/dev"
    HID_RETRY_SECONDS = 10.0

    def _hid_init(self) -> None:
        self.hid: Dict[str, Any] = {"state": "off", "path": "", "raw": 0, "valid": 0, "last_buttons": 0,
                                    "headers": [], "error": "", "fired": 0}
        self._hid_thread: Any = None
        self._hid_stop_flag = threading.Event()

    async def get_hotkey_status(self) -> Dict[str, Any]:
        return dict(self.hid)

    def _hid_candidates(self) -> List[str]:
        """hidraw nodes that belong to the Deck controller (USB 28de:1205)."""
        out: List[str] = []
        try:
            names = sorted(os.listdir(self.HID_SYSFS))
        except OSError:
            return out
        for n in names:
            try:
                with open(os.path.join(self.HID_SYSFS, n, "device", "uevent")) as f:
                    uev = f.read()
            except OSError:
                continue
            if re.search(r"HID_ID=0003:000028DE:00001205", uev, re.I):
                out.append(os.path.join(self.HID_DEV, n))
        return out

    def _hid_start(self) -> None:
        self._hid_stop()
        if not self.s.get("hid_hotkeys"):
            self.hid["state"] = "off"
            return
        self._hid_stop_flag = threading.Event()
        self.hid.update(state="searching", error="")
        loop = asyncio.get_event_loop()
        self._hid_thread = threading.Thread(target=self._hid_loop, args=(loop, self._hid_stop_flag), daemon=True,
                                            name="finally-hid")
        self._hid_thread.start()

    def _hid_stop(self) -> None:
        self._hid_stop_flag.set()
        th = self._hid_thread
        self._hid_thread = None
        if th is not None and th.is_alive():
            th.join(timeout=3)

    def _hid_loop(self, loop: asyncio.AbstractEventLoop, stop: threading.Event) -> None:
        det = core.HotkeyDetector()
        fds: Dict[int, str] = {}
        try:
            while not stop.is_set():
                if not fds:
                    paths = self._hid_candidates()
                    err = "" if paths else "no Deck controller found (USB 28de:1205)"
                    for p in paths:
                        try:
                            fds[os.open(p, os.O_RDONLY | os.O_NONBLOCK)] = p
                        except OSError as e:
                            err = f"cannot open {p}: {e.strerror}"
                    if not fds:
                        self.hid.update(state="failed", error=err)
                        stop.wait(self.HID_RETRY_SECONDS)
                        continue
                    self.hid.update(state="reading", error="", path=", ".join(fds.values()))
                ready, _w, _x = select.select(list(fds), [], [], 0.5)
                for fd in ready:
                    try:
                        data = os.read(fd, 128)
                    except BlockingIOError:
                        continue
                    except OSError:
                        data = b""
                    if not data:                                  # unplugged / closed
                        os.close(fd)
                        fds.pop(fd, None)
                        continue
                    self.hid["raw"] += 1
                    head = data[:3].hex(" ")
                    if head not in self.hid["headers"] and len(self.hid["headers"]) < 4:
                        self.hid["headers"].append(head)
                    buttons = core.parse_deck_report(data)
                    if buttons is None:
                        continue
                    self.hid["valid"] += 1
                    self.hid["last_buttons"] = buttons
                    action = det.update(buttons, time.time(), self.s.get("hotkey_clip", True), self.s.get("hotkey_record", False))
                    if action:
                        self.hid["fired"] += 1
                        asyncio.run_coroutine_threadsafe(self._emit("hotkey", action=action), loop)
        except Exception as e:                                    # never let the reader die silently
            self.hid.update(state="failed", error=f"{type(e).__name__}: {e}"[:160])
        finally:
            for fd in list(fds):
                try:
                    os.close(fd)
                except OSError:
                    pass
