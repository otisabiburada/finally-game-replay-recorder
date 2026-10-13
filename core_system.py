"""Pure logic about the machine: the GPU/power cost meter and the rule for trusting a saved capability probe."""

from typing import Any, Dict, List, Optional, Tuple

CAPS_CACHE_VERSION = 1


def _mean(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


class CostMeter:
    """Rough 'what does recording cost' number: GPU load and power while capturing vs. the minute before it started.
    It cannot know how the game's own load changed in between, so the result is only a hint."""

    def __init__(self, window: float = 60.0, warmup: float = 10.0, min_samples: int = 5, keep: float = 400.0):
        self.window, self.warmup, self.min_samples, self.keep = window, warmup, min_samples, keep
        self.samples: List[Tuple[float, Optional[float], Optional[float], bool]] = []   # (time, gpu %, watts, capturing)

    def add(self, t: float, gpu: Optional[float], watts: Optional[float], capturing: bool) -> None:
        self.samples.append((t, gpu, watts, capturing))
        while self.samples and t - self.samples[0][0] > self.keep:
            self.samples.pop(0)

    def report(self, now: float) -> Dict[str, Any]:
        out: Dict[str, Any] = {k: None for k in ("gpu_now", "watts_now", "base_gpu", "base_watts", "cap_gpu", "cap_watts",
                                                 "delta_gpu", "delta_watts")}
        if not self.samples:
            return out
        _t, out["gpu_now"], out["watts_now"], capturing = self.samples[-1]
        if not capturing:
            return out
        start = self.samples[-1][0]                                  # first sample of the capture run that is still going
        for t, _g, _w, c in reversed(self.samples):
            if not c:
                break
            start = t
        base = [s for s in self.samples if not s[3] and start - self.window <= s[0] < start]
        during = [s for s in self.samples if s[3] and s[0] >= start + self.warmup and s[0] >= now - self.window]
        if len(base) < self.min_samples or len(during) < self.min_samples:
            return out
        for key, idx in (("gpu", 1), ("watts", 2)):
            b = _mean([s[idx] for s in base if s[idx] is not None])
            c = _mean([s[idx] for s in during if s[idx] is not None])
            out["base_" + key], out["cap_" + key] = b, c
            if b is not None and c is not None:
                out["delta_" + key] = c - b
        return out


def caps_cache_ok(cached: Any, fingerprint: List[Any], now: float, max_age: float) -> bool:
    """May a probe saved on disk be reused? Only if it has the current layout, matches the machine's fingerprint
    (tool binaries, GPU video drivers, kernel), is recent, and found something usable."""
    if not isinstance(cached, dict) or cached.get("v") != CAPS_CACHE_VERSION:
        return False
    if cached.get("fingerprint") != fingerprint:
        return False
    try:
        if not (0 <= now - float(cached.get("time", 0)) < max_age):
            return False
    except (TypeError, ValueError):
        return False
    caps = cached.get("caps")
    return bool(isinstance(caps, dict) and caps.get("gst_launch") and caps.get("ffmpeg")
                and (caps.get("encoders") or caps.get("ff_usable")))
