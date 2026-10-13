"""Adaptive cost control: the replay buffer waits while no game is running, and the picture gets cheaper by itself
when the encoder cannot keep up. Both exist to keep the recorder from taking performance away from the game."""

import time
from typing import Any, Dict, Optional

import recorder_core as core


class AdaptMixin:
    NO_GAME_SECONDS = 5.0      # no game for this long before the replay buffer is paused (ignores brief transitions)

    def _adapt_init(self) -> None:
        self.game_running: Optional[bool] = None       # None = the panel has not told us yet -> treat as running
        self.no_game_since = 0.0
        self.paused = False

    async def report_game(self, name: str = "") -> Dict[str, Any]:
        """Called by the panel whenever the running game changes ('' = none)."""
        if str(name or "").strip():
            self.game_running, self.no_game_since = True, 0.0
        else:
            if self.game_running is not False:
                self.no_game_since = time.time()
            self.game_running = False
        return {"ok": True}

    def _idle_pause(self) -> bool:
        """True while the replay buffer should stay off because no game has been running for a while."""
        return bool(self.s.get("buffer_only_in_game") and self.game_running is False and self.no_game_since
                    and time.time() - self.no_game_since >= self.NO_GAME_SECONDS)

    async def _adapt(self, now: float, slow: Optional[str]) -> bool:
        """One watchdog step. True = the capture was restarted/stopped here, so the caller should skip the rest."""
        if slow:
            step = core.step_down(self.s) if self.s.get("auto_step_down") else None
            if step:
                label, patch = step
                self.s.update(patch)
                self._save_settings()
                await self._restart_for_settings(f"The encoder could not keep up, so recording switched to {label}.")
                return True
            self.warnings.append(slow)
            self._note(slow)
            await self._emit("warning", message=slow)
        if self.mode == "replay" and self._idle_pause():
            self._note("replay buffer paused: no game is running")
            async with self.lock:
                if self.mode == "replay":
                    await self._stop_capture_locked(finalize=False)
                    self.paused = True
            return True
        return False

    async def _restart_for_settings(self, why: str) -> None:
        """Restart the running capture with the (just changed) settings. A recording continues in a new file."""
        mode, game = self.mode, self.game
        async with self.lock:
            if self.mode != mode:
                return
            await self._stop_capture_locked(finalize=(mode == "manual"), fast=True)
            if await self._start_capture_locked(mode == "replay", game if mode == "manual" else ""):
                self.warnings.append(why)
                self._note(why)
                await self._emit("warning", message=why)
