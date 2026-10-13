"""What this system can do: GStreamer/ffmpeg capabilities, encoder probes, screen size, GStreamer environment."""

import asyncio
import glob
import json
import recorder_core as core
import os
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

from finally_util import _clean_env, _home, _makedirs, _run


class CapsMixin:
    CAPS_MAX_AGE = 7 * 86400          # a saved probe is reused for at most a week

    async def _capabilities(self, force: bool = False) -> Dict[str, Any]:
        """What this system can do. Memory cache (10 min) -> probe saved on disk (if the machine is unchanged) -> real probe."""
        if self.caps and not force and time.time() - self.caps_time < 600:
            return self.caps
        fp = self._caps_fingerprint()
        if not force:
            cached = self._caps_load(fp)
            if cached:
                cached["from_cache"] = True
                self.caps, self.caps_time = cached, time.time()
                return cached
        caps = await self._probe_capabilities()
        caps["from_cache"] = False
        self.caps, self.caps_time = caps, time.time()
        self._caps_save(fp, caps)
        return caps

    # ------------------------------------------------------------------ the probe saved on disk
    def _caps_path(self) -> str:
        return os.path.join(os.path.dirname(self.settings_file), "caps.json")

    def _caps_fingerprint(self) -> List[Any]:
        """Anything that could change what works: the tools, the GPU video drivers, the kernel (a SteamOS update changes them)."""
        env = self._gst_env()
        fp: List[Any] = []
        for name in ("gst-launch-1.0", "gst-inspect-1.0", "ffmpeg", "ffprobe", "pactl"):
            p = shutil.which(name, path=env["PATH"])
            try:
                st = os.stat(p) if p else None
            except OSError:
                st = None
            fp.append([name, p, st.st_mtime_ns if st else 0, st.st_size if st else 0])
        b = self._bundle_dir()
        fp.append(["bundle", b, int(os.path.getmtime(b) * 1e9) if b and os.path.isdir(b) else 0])
        for d in sorted(glob.glob("/usr/lib/dri/*_drv_video.so") + glob.glob("/usr/lib/x86_64-linux-gnu/dri/*_drv_video.so")):
            try:
                st = os.stat(d)
                fp.append(["va", d, st.st_mtime_ns, st.st_size])
            except OSError:
                pass
        fp.append(["render", os.path.exists("/dev/dri/renderD128")])
        fp.append(["kernel", os.uname().release])
        return json.loads(json.dumps(fp))

    def _caps_load(self, fp: List[Any]) -> Optional[Dict[str, Any]]:
        try:
            with open(self._caps_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data["caps"] if core.caps_cache_ok(data, fp, time.time(), self.CAPS_MAX_AGE) else None

    def _caps_save(self, fp: List[Any], caps: Dict[str, Any]) -> None:
        if not (caps.get("gst_launch") and caps.get("ffmpeg") and (caps.get("encoders") or caps.get("ff_usable"))):
            return                                                  # never save a failed/empty probe
        try:
            _makedirs(os.path.dirname(self._caps_path()))
            tmp = self._caps_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": core.CAPS_CACHE_VERSION, "time": time.time(), "fingerprint": fp, "caps": caps}, f)
            os.replace(tmp, self._caps_path())
        except OSError:
            pass

    def _caps_invalidate(self) -> None:
        """Something that the probe said would work did not: forget it, memory and disk."""
        self.caps, self.caps_time = {}, 0.0
        try:
            os.unlink(self._caps_path())
        except OSError:
            pass

    async def _probe_capabilities(self) -> Dict[str, Any]:
        env = self._gst_env()
        gst = shutil.which("gst-launch-1.0", path=env["PATH"])
        caps: Dict[str, Any] = {"gst_launch": gst, "inspect": shutil.which("gst-inspect-1.0", path=env["PATH"]),
                                "ffmpeg": shutil.which("ffmpeg", path=env["PATH"]),
                                "ffprobe": shutil.which("ffprobe", path=env["PATH"]),
                                "pactl": shutil.which("pactl", path=env["PATH"]),
                                "elements": {}, "props": {}, "split_props": [], "src_props": []}
        if caps["inspect"]:
            names = ["pipewiresrc", "pulsesrc", "h264parse", "matroskamux", "splitmuxsink", "aacparse", "videorate",
                     "audioconvert", "audioresample", "videoconvert", "videoscale", "fdsink", "queue"]
            names += list(core.ENCODER_PREFERENCE) + list(core.AAC_PREFERENCE)
            results = await asyncio.gather(*[_run([caps["inspect"], n], 15, env) for n in names])
            for n, (rc, out, _err) in zip(names, results):
                caps["elements"][n] = (rc == 0)
                if rc == 0:
                    props = core.parse_gst_props(out)
                    if n in core.ENCODER_PREFERENCE:
                        caps["props"][n] = sorted(props)
                    if n == "pipewiresrc":
                        caps["src_props"] = sorted(props)
                    if n == "splitmuxsink":
                        caps["split_props"] = sorted(props)
        caps["ff_encoders"], caps["ff_usable"], caps["ff_devices"], caps["ff_probe"] = [], [], [], {}
        if caps["ffmpeg"]:
            (rc1, enc_out, _), (rc2, dev_out, _) = await asyncio.gather(
                _run([caps["ffmpeg"], "-hide_banner", "-encoders"], 15), _run([caps["ffmpeg"], "-hide_banner", "-devices"], 15))
            have = core.parse_ffmpeg_encoders(enc_out) if rc1 == 0 else set()
            caps["ff_vp9"], caps["ff_opus"] = "libvpx-vp9" in have, "libopus" in have
            caps["ff_devices"] = sorted(core.parse_ffmpeg_devices(dev_out)) if rc2 == 0 else []
            caps["ff_encoders"] = [e for e in core.FFMPEG_ENCODER_PREFERENCE if e in have]
            probes = await asyncio.gather(*[self._probe_ff_encoder(caps["ffmpeg"], e) for e in caps["ff_encoders"]])
            for e, (ok, detail) in zip(caps["ff_encoders"], probes):
                caps["ff_probe"][e] = detail
                if ok:
                    caps["ff_usable"].append(e)
        caps["bundle"] = self._bundle_dir()
        caps["encoders"] = [e for e in core.ENCODER_PREFERENCE if caps["elements"].get(e)]
        caps["aac"] = core.pick_aac([e for e in core.AAC_PREFERENCE if caps["elements"].get(e)])
        return caps

    async def _probe_ff_encoder(self, ffmpeg: str, enc: str) -> Tuple[bool, str]:
        """Really encode 10 test frames: a listed encoder can still fail (e.g. VAAPI without a working GPU driver)."""
        src = ["-f", "lavfi", "-i", "testsrc=size=1280x800:rate=30", "-frames:v", "10"]
        if enc == "h264_vaapi":
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-vaapi_device", "/dev/dri/renderD128"] + src + \
                  ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-f", "null", "-"]
        else:
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"] + src + ["-c:v", enc, "-pix_fmt", "yuv420p", "-f", "null", "-"]
        rc, _o, err = await _run(cmd, 25)
        return rc == 0, "works" if rc == 0 else (err.strip().splitlines() or ["failed"])[-1][:160]

    async def _probe_size(self, caps: Dict[str, Any]) -> Optional[Tuple[int, int]]:
        """Native screen size from the live PipeWire stream (cached 60 s)."""
        if time.time() - self.size_cache[0] < 60 and self.size_cache[1]:
            return self.size_cache[1]
        rc, out, err = await _run([caps["gst_launch"], "-v", "-e", "pipewiresrc", "num-buffers=1", "!", "videoconvert", "!", "fakesink"], 8, self._gst_env())
        size = core.parse_caps_size(out + "\n" + err)
        self.size_cache = (time.time(), size)
        return size

    def _bundle_dir(self) -> Optional[str]:
        """The GStreamer bundle built by backend/Dockerfile (same layout the original decky-recorder uses:
        <dir>/gstreamer-1.0 holds the plugins, <dir> holds their libraries). Looked for in this plugin and,
        as a convenience, in an already-installed original 'Decky Recorder'."""
        plug = os.path.dirname(os.path.abspath(__file__))
        orig = os.path.join(_home(), "homebrew", "plugins", "Decky Recorder")
        for base in (plug, orig):
            for sub in ("bin", os.path.join("backend", "out")):
                d = os.path.join(base, sub)
                if os.path.isdir(os.path.join(d, "gstreamer-1.0")):
                    return d
        return None

    def _gst_env(self) -> Dict[str, str]:
        extra: Dict[str, str] = {"GST_VAAPI_ALL_DRIVERS": "1"}     # same as the original plugin
        bundle = self._bundle_dir()
        env = _clean_env(extra)
        if bundle:
            env["GST_PLUGIN_PATH"] = os.path.join(bundle, "gstreamer-1.0")
            env["LD_LIBRARY_PATH"] = bundle + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        return env
