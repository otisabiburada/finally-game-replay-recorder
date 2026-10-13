"""Shared fixtures for the backend tests: fake gst/pactl on PATH, real ffmpeg/ffprobe, a throw-away Decky home.

Quiet by design: asyncio debug mode off (no 'took 0.1 seconds' lines) and ResourceWarnings ignored."""
import asyncio, copy, glob, json, os, shutil, signal, subprocess, sys, tempfile, time, unittest, warnings

warnings.simplefilter("ignore", ResourceWarning)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, os.path.join(HERE, ".."))
import decky  # stub
import main as M
import finally_util as U

M.Plugin.WATCHDOG_INTERVAL = 0.2
M.Plugin.STOP_SCALE = 0.2
M.Plugin.WARM_CAPS_AFTER = None   # no background probing in tests (it would race with assertions)          # a deliberately wedged fake process is killed after ~1/5 of the real waits
DECK_ELEMENTS = "pipewiresrc,pulsesrc,h264parse,matroskamux,splitmuxsink,aacparse,videoconvert,videoscale,videorate,fdsink,queue"
_CAPS_CACHE = {}                   # probing real ffmpeg encoders takes ~1 s; identical for identical fake environments

def mk_sample(path, audio=True, sec=1):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=320x200:rate=10:duration={sec}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={sec}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"] + (["-c:a", "aac"] if audio else []) + [path]
    subprocess.run(cmd, check=True)


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration,format_name",
                        "-of", "json", path], capture_output=True, text=True)
    j = json.loads(r.stdout or "{}")
    return {s["codec_type"] for s in j.get("streams", [])}, float(j.get("format", {}).get("duration", 0) or 0), j.get("format", {}).get("format_name", "")


def probe_video(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height",
                        "-of", "json", path], capture_output=True, text=True)
    return json.loads(r.stdout or "{}").get("streams", [])


class Base(unittest.IsolatedAsyncioTestCase):
    def _setupAsyncioRunner(self):                      # IsolatedAsyncioTestCase turns debug on -> very noisy output
        self._asyncioRunner = asyncio.Runner(debug=False)

    def caps_key(self):
        return (os.environ.get("FAKE_ELEMENTS", ""), os.environ.get("FAKE_NO_KEEPALIVE", ""))

    def seed_caps(self):
        c = _CAPS_CACHE.get(self.caps_key())
        if c:
            self.p.caps, self.p.caps_time = copy.deepcopy(c), time.time()

    SAMPLE_AUDIO = True

    async def asyncSetUp(self):
        self.root = tempfile.mkdtemp(prefix="finallyt_")
        decky.DECKY_USER_HOME = os.path.join(self.root, "home")
        decky.DECKY_PLUGIN_SETTINGS_DIR = os.path.join(self.root, "settings")
        decky.events.clear()
        os.makedirs(decky.DECKY_USER_HOME)
        self.sample = os.path.join(self.root, "sample.mkv"); mk_sample(self.sample, self.SAMPLE_AUDIO)
        self.log = os.path.join(self.root, "calls.log"); open(self.log, "w").close()
        self.pa = os.path.join(self.root, "pa_state")
        self._env = dict(os.environ)
        os.environ.update(PATH=os.path.join(HERE, "fakebin") + ":" + self._env["PATH"], FAKE_LOG=self.log,
                          FAKE_SAMPLE=self.sample, FAKE_PA_STATE=self.pa)
        for k in ("FAKE_GST_FAIL", "FAKE_PA_FAIL", "FAKE_ELEMENTS", "FAKE_GST_HANG", "FAKE_GST_HANG_ALWAYS", "FAKE_NO_KEEPALIVE"):
            os.environ.pop(k, None)
        self._gm = U._in_game_mode; U._in_game_mode = lambda: True
        os.environ["FINALLY_REC_SEG_DIR"] = os.path.join(self.root, "seg")
        self.p = M.Plugin()
        await self.p._main()
        self.seed_caps()
        self.p.s["stall_seconds"] = 0          # most tests have short, non-growing fake files

    async def asyncTearDown(self):
        if os.environ.get("DUMP_TASKS"):
            await asyncio.sleep(float(os.environ["DUMP_TASKS"]))
            for t in asyncio.all_tasks():
                if t is not asyncio.current_task():
                    print("TASK", t.get_coro().__qualname__, "done" if t.done() else "pending", file=sys.stderr)
                    t.print_stack(limit=3, file=sys.stderr)
        try:
            await self.p._unload()
        except Exception:
            pass
        U._in_game_mode = self._gm
        if self.p.caps and self.caps_key() not in _CAPS_CACHE:
            _CAPS_CACHE[self.caps_key()] = copy.deepcopy(self.p.caps)
        os.environ.clear(); os.environ.update(self._env)
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.p.seg_dir, ignore_errors=True)

    def calls(self, prefix=""):
        with open(self.log) as f:
            return [l for l in f.read().splitlines() if l.startswith(prefix)]

    def pa_modules(self):
        if not os.path.exists(self.pa):
            return []
        with open(self.pa) as f:
            return [l for l in f.read().splitlines() if l.strip()]

    async def wait_for(self, cond, timeout=15.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if cond():
                return True
            await asyncio.sleep(0.1)
        return False

    def out_files(self):
        return sorted(glob.glob(os.path.join(self.p._out_dir(), "*")))

    def staging_left(self):
        return glob.glob(os.path.join(self.p.stage_dir, "rec_*"))




class DeckBase(Base):
    """The element list from the photo of the real Deck: pipewiresrc etc. present, NO H.264/AAC encoder in GStreamer.
    Real ffmpeg (libx264) does the encoding; the fake gst-launch only emits raw frames."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        os.environ["FAKE_ELEMENTS"] = DECK_ELEMENTS
        M.Plugin.FFMPEG_AUDIO_OVERRIDE = ["-f", "lavfi", "-i", "sine=frequency=440"]
        self.p.caps = {}
        self.seed_caps()
        await self.p.set_settings({"fps": 30, "video_kbps": 3000})

    async def asyncTearDown(self):
        M.Plugin.FFMPEG_AUDIO_OVERRIDE = None
        await super().asyncTearDown()

    def leftovers(self):
        """Fake gst-launch / ffmpeg processes that belong to THIS test (matched through the FAKE_LOG path in their
        environment or the test root in their argv)."""
        for d in glob.glob("/proc/[0-9]*"):
            try:
                argv = [a.decode("utf-8", "replace") for a in open(d + "/cmdline", "rb").read().split(b"\0") if a]
                env = open(d + "/environ", "rb").read().decode("utf-8", "replace")
            except OSError:
                continue
            if not argv:
                continue
            fake_gst = any(a.endswith("fakebin/gst-launch-1.0") for a in argv[:2]) and self.root in env
            ffm = os.path.basename(argv[0]) == "ffmpeg" and any(self.root in a for a in argv)
            if fake_gst or ffm:
                return True
        return False
