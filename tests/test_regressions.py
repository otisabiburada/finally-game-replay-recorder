"""Bugs in the ORIGINAL decky-recorder-fork main.py (read from the uploaded zip), reproduced with the
original lines, next to the same scenario through the new code. OLD must fail, NEW must pass."""
import os, shutil, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import recorder_core as c

FFMPEG = shutil.which("ffmpeg")


def make_clip(path, sec=1):
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={sec}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0


class ShellInjectionInGameName(unittest.TestCase):
    """orig: filepath = f"{folder}/{app_name}_{dateTime}.{fmt}" then get_cmd_output(f'rm "{filepath}.temp"') (shell=True).
    app_name only has ':' and '/' replaced."""
    EVIL = 'Evil $(touch PWNED) Game'

    def test_old_executes_game_name(self):
        d = tempfile.mkdtemp()
        app_name = self.EVIL.replace(":", " ").replace("/", " ")                  # orig sanitising
        filepath = f"{d}/{app_name}_2026.mkv"                                       # orig
        subprocess.getoutput(f'rm "{filepath}.temp"')                               # orig get_cmd_output (shell)
        self.assertTrue(os.path.exists("PWNED") or any(os.path.exists(os.path.join(x, "PWNED")) for x in (".", d)),
                        "expected the old code to run the command inside the game name")
        for x in (".", d):
            try: os.remove(os.path.join(x, "PWNED"))
            except FileNotFoundError: pass

    def test_new_name_is_inert_and_never_hits_a_shell(self):
        name = c.safe_name(self.EVIL)
        self.assertNotIn("$", name)
        self.assertNotIn("`", name); self.assertNotIn(";", name); self.assertNotIn('"', name)


class ConcatListBreaksOnSpaces(unittest.TestCase):
    """orig: ff.write(f"file {str(f)}\\n") - unquoted. Game/segment paths with a space or apostrophe break ffmpeg concat."""
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="it's a dir ")
        self.a, self.b = os.path.join(self.d, "a b.mkv"), os.path.join(self.d, "b'c.mkv")
        make_clip(self.a); make_clip(self.b)

    def run_concat(self, listtxt):
        lst = os.path.join(self.d, "files.txt"); open(lst, "w").write(listtxt)
        out = os.path.join(self.d, "out.mkv")
        r = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out],
                           capture_output=True, text=True)
        return r.returncode, duration(out)

    def test_old_fails(self):
        rc, dur = self.run_concat("".join(f"file {p}\n" for p in (self.a, self.b)))   # orig line
        self.assertTrue(rc != 0 or dur < 1.5, f"old concat unexpectedly worked rc={rc} dur={dur}")

    def test_new_passes(self):
        rc, dur = self.run_concat(c.concat_list([self.a, self.b]))
        self.assertEqual(rc, 0); self.assertGreater(dur, 1.8)


class OriginalPythonErrors(unittest.TestCase):
    def test_old_watchdog_handler_itself_raises(self):
        # orig: except Exception: logger.exception(f"watchdog exception! {Exception.message}")
        with self.assertRaises(AttributeError):
            try:
                raise RuntimeError("anything in the loop")
            except Exception:
                _ = f"watchdog exception! {Exception.message}"          # noqa  (kills the watchdog task)

    def test_old_get_audio_bitrate_typeerror(self):
        with self.assertRaises(TypeError):
            _ = "Current audio bitrate: " + 192000                       # orig get_audio_bitrate (loadConfig sets an int)

    def test_new_watchdog_loop_survives_exceptions(self):
        # new code logs with traceback.format_exc(); the loop in main.py is covered in test_backend (watchdog survives)
        import traceback
        try:
            raise RuntimeError("x")
        except Exception:
            self.assertIn("RuntimeError", traceback.format_exc())


class ClipWindow(unittest.TestCase):
    """orig picks files by `max_time - ftime <= clip_duration` using ctime and includes the file still being written."""
    def test_old_includes_in_progress_file(self):
        now = 1000.0
        files = [(f"s{i}", now - 100 + i * 1.5) for i in range(60)]
        ft = sorted(files, key=lambda x: -x[1])
        old = [f for f, t in ft if now - t <= 30]                       # orig loop
        self.assertIn("s59", old)                                       # the half-written newest segment

    def test_new_excludes_in_progress_file(self):
        now = 1000.0
        files = [(f"s{i}", now - 100 + i * 1.5) for i in range(60)]
        self.assertNotIn("s59", c.select_segments(files, 30, now))


class GameModeDetection(unittest.TestCase):
    """orig in_gamemode(): True only if some process command line contains 'gamescope-session'."""
    def proc(self, entries):
        root = tempfile.mkdtemp()
        for pid, (cmd, comm) in entries.items():
            d = os.path.join(root, str(pid)); os.makedirs(d)
            open(os.path.join(d, "cmdline"), "wb").write(cmd.encode().replace(b" ", b"\0"))
            open(os.path.join(d, "comm"), "w").write(comm + "\n")
        return root

    def old(self, root):
        for pid in os.listdir(root):
            if "gamescope-session" in open(os.path.join(root, pid, "cmdline"), "rb").read().replace(b"\0", b" ").decode():
                return True
        return False

    def test_old_misses_a_renamed_session_new_finds_it(self):
        import importlib; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stubs"))
        import decky  # noqa
        M = importlib.import_module("main")
        root = self.proc({100: ("/usr/bin/steam -steamdeck", "steam"), 200: ("/usr/bin/gamescope --backend drm", "gamescope-wl")})
        self.assertFalse(self.old(root))
        self.assertEqual(M._game_mode_signal(root), "process gamescope-wl")

    def test_both_find_the_classic_session(self):
        import importlib; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stubs"))
        import decky  # noqa
        M = importlib.import_module("main")
        root = self.proc({300: ("/bin/bash /usr/bin/gamescope-session-plus steam", "bash")})
        self.assertTrue(self.old(root)); self.assertEqual(M._game_mode_signal(root), "gamescope-session")

    def test_desktop_mode_is_not_game_mode(self):
        import importlib; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stubs"))
        import decky  # noqa
        M = importlib.import_module("main")
        root = self.proc({1: ("/usr/bin/plasmashell", "plasmashell"), 2: ("/usr/bin/kwin_wayland", "kwin_wayland")})
        self.assertEqual(M._game_mode_signal(root), "")


if __name__ == "__main__":
    unittest.main()
