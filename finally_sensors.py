"""GPU load and power draw from the kernel's system files, so the panel can show what recording costs.
Paths follow the standard amdgpu / power_supply layout; whether every file exists on a Deck is shown in the self-test."""

import glob
import os
from typing import Optional, Tuple

import recorder_core as core


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


class SensorsMixin:
    SYS_DRM = "/sys/class/drm"
    SYS_HWMON = "/sys/class/hwmon"
    SYS_POWER = "/sys/class/power_supply"

    def _sensors_init(self) -> None:
        self.gpu: Optional[int] = None
        self.watts: Optional[float] = None
        self.power_src = ""
        self.cost = core.CostMeter()
        self.cost_report = {}

    def _read_gpu_busy(self) -> Optional[int]:
        for p in sorted(glob.glob(os.path.join(self.SYS_DRM, "card*", "device", "gpu_busy_percent"))):
            v = _read(p)
            if v is not None and v.isdigit():
                return max(0, min(100, int(v)))
        return None

    def _read_power(self) -> Tuple[Optional[float], str]:
        """(watts, which sensor): the GPU/APU package power if exposed, else the battery's discharge rate."""
        for d in sorted(glob.glob(os.path.join(self.SYS_HWMON, "hwmon*"))):
            if _read(os.path.join(d, "name")) != "amdgpu":
                continue
            for f in ("power1_average", "power1_input"):
                v = _read(os.path.join(d, f))
                if v is not None and v.isdigit():
                    return int(v) / 1e6, "APU"
        for d in sorted(glob.glob(os.path.join(self.SYS_POWER, "BAT*"))):
            v = _read(os.path.join(d, "power_now"))
            if v is not None and v.isdigit():
                return int(v) / 1e6, "battery"
            cur, volt = _read(os.path.join(d, "current_now")), _read(os.path.join(d, "voltage_now"))
            if cur and volt and cur.lstrip("-").isdigit() and volt.isdigit():
                return abs(int(cur)) * int(volt) / 1e12, "battery"
        return None, ""

    def _sample_cost(self, now: float) -> None:
        self.gpu = self._read_gpu_busy()
        self.watts, self.power_src = self._read_power()
        self.cost.add(now, self.gpu, self.watts, self.mode is not None)
        self.cost_report = self.cost.report(now)
