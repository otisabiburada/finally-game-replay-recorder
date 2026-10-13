"""Virtual PulseAudio/PipeWire sink: game audio + optional microphone chain."""

import recorder_core as core
import decky
import os
import shutil
from typing import Optional, Tuple

from finally_util import MARK, SINK, _home, _run


class AudioMixin:
    async def _pactl(self, *args: str) -> Tuple[int, str]:
        pactl = (self.caps.get("pactl") if self.caps else None) or "pactl"
        rc, out, err = await _run([pactl, *args], 6)
        if rc != 0:
            self._note(f"pactl {' '.join(args[:2])} failed: {err.strip()[:160]}")
        return rc, out.strip()

    async def _load_module(self, *args: str) -> Optional[int]:
        rc, out = await self._pactl("load-module", *args)
        if rc == 0 and out.split()[:1] and out.split()[0].isdigit():
            mid = int(out.split()[0])
            self.pa_ids.append(mid)
            return mid
        return None

    async def _setup_audio(self) -> Optional[str]:
        """Builds Finally-Rec-Sink fed by the game audio (and mic). Returns the device to record, or None."""
        s = self.s
        if not (s["game_audio"] or s["mic_enabled"]):
            return None
        await self._unload_audio()
        rc, default_sink = await self._pactl("get-default-sink")
        if rc != 0 or not default_sink:
            self.warnings.append("No audio server reachable; recording without audio.")
            return None
        if await self._load_module("module-null-sink", f"sink_name={SINK}",
                                   f"sink_properties=device.description={MARK}") is None:
            self.warnings.append("Could not create the recording sink; recording without audio.")
            return None
        if s["game_audio"]:
            if await self._load_module("module-loopback", f"source={default_sink}.monitor", f"sink={SINK}") is None:
                self.warnings.append("Game audio could not be routed.")
        if s["mic_enabled"]:
            ok = await self._attach_mic(default_sink)
            if not ok:
                self.warnings.append("Microphone could not be attached.")
        return f"{SINK}.monitor"

    async def _attach_mic(self, default_sink: str) -> bool:
        s = self.s
        src = s["mic_source"]
        if not src:
            rc, src = await self._pactl("get-default-source")
            if rc != 0 or not src:
                return False
        mic = src
        if s["mic_cleanup"]:
            rn = None
            for p in (os.path.join(_home(), "homebrew", "data", "decky-recorder", "librnnoise_ladspa.so"),
                      os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "librnnoise_ladspa.so")):
                if os.path.exists(p):
                    rn = p
            if rn and s["noise_reduction"] > 0:
                # same module chain the original decky-recorder uses for rnnoise
                if await self._load_module("module-null-sink", f"sink_name={MARK}-Denoise", "rate=48000") is not None \
                        and await self._load_module("module-ladspa-sink", f"sink_name={MARK}-Denoise_in",
                                                    f"sink_master={MARK}-Denoise", "label=noise_suppressor_mono",
                                                    f"plugin={rn}", f"control={s['noise_reduction']},20,0,0,0") is not None \
                        and await self._load_module("module-loopback", f"source={src}", f"sink={MARK}-Denoise_in",
                                                    "channels=1", "source_dont_move=true", "sink_dont_move=true") is not None:
                    mic = f"{MARK}-Denoise.monitor"
                    await self._pactl("set-source-volume", mic, f"{s['mic_gain_db']}dB")
                    return await self._load_module("module-loopback", f"source={mic}", f"sink={SINK}") is not None
                self._note("rnnoise chain failed, falling back")
            ec = await self._load_module("module-echo-cancel", "use_master_format=1", f"source_master={src}",
                                         f"sink_master={default_sink}", f"source_name={MARK}-Mic",
                                         f"sink_name={MARK}-Echo", "aec_method=webrtc",
                                         'aec_args="analog_gain_control=0 digital_gain_control=1"')
            if ec is not None:
                mic = f"{MARK}-Mic"
            else:
                self._note("echo-cancel unavailable, using the raw microphone")
        await self._pactl("set-source-volume", mic, f"{s['mic_gain_db']}dB")
        return await self._load_module("module-loopback", f"source={mic}", f"sink={SINK}") is not None

    async def _apply_mic_gain(self) -> None:
        for name in (f"{MARK}-Mic", f"{MARK}-Denoise.monitor"):
            rc, _ = await self._pactl("set-source-volume", name, f"{self.s['mic_gain_db']}dB")
            if rc == 0:
                return

    async def _unload_audio(self) -> None:
        ids, self.pa_ids = list(reversed(self.pa_ids)), []
        pactl = (self.caps.get("pactl") if self.caps else None) or shutil.which("pactl") or "pactl"
        for mid in ids:
            await _run([pactl, "unload-module", str(mid)], 5)

    async def _sweep_audio_orphans(self) -> None:
        pactl = (self.caps.get("pactl") if self.caps else None) or shutil.which("pactl") or "pactl"
        rc, out, _ = await _run([pactl, "list", "short", "modules"], 5)
        if rc != 0:
            return
        for mid in reversed(core.parse_pactl_modules(out, MARK)):
            await _run([pactl, "unload-module", str(mid)], 5)
