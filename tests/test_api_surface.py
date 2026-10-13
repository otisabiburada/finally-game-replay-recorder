"""The frontend calls backend methods by NAME (callable<...>("name")). After any refactor these must still
exist on `Plugin` as coroutines, and no mixin may silently shadow another's method."""
import ast, inspect, os, re, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, os.path.join(HERE, ".."))
import decky  # noqa: F401  (stub)
import main as M


class ApiSurface(unittest.TestCase):
    def test_every_frontend_call_exists_and_is_async(self):
        src = ""
        for root, _d, files in os.walk(os.path.join(HERE, "..", "src")):
            for f in files:
                if f.endswith((".ts", ".tsx")):
                    src += open(os.path.join(root, f), encoding="utf-8").read()
        names = set(re.findall(r'callable<[^>]*(?:<[^>]*>[^>]*)*>\("([a-z_]+)"\)', src))
        self.assertGreaterEqual(len(names), 10, names)
        for n in names:
            self.assertTrue(hasattr(M.Plugin, n), f"frontend calls '{n}' but Plugin has no such method")
            self.assertTrue(inspect.iscoroutinefunction(getattr(M.Plugin, n)), f"'{n}' must be async")

    def test_decky_lifecycle_hooks(self):
        for n in ("_main", "_unload"):
            self.assertTrue(inspect.iscoroutinefunction(getattr(M.Plugin, n)), n)

    def test_no_method_is_defined_in_two_places(self):
        seen = {}
        for base in M.Plugin.__mro__:
            if base is object:
                continue
            for name, val in vars(base).items():
                if callable(val) or isinstance(val, staticmethod):
                    if name.startswith("__"):
                        continue
                    self.assertNotIn(name, seen, f"'{name}' defined in both {seen.get(name)} and {base.__name__}")
                    seen[name] = base.__name__

    def test_no_source_file_grows_back_into_a_monolith(self):
        """Big files cost time and tokens on every read. Split by responsibility before raising this limit."""
        limit = 18 * 1024
        root = os.path.join(HERE, "..")
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ("node_modules", "dist", "__pycache__", "fakebin")]
            for f in files:
                if f.endswith((".py", ".ts", ".tsx")) and f != "pnpm-lock.yaml":
                    size = os.path.getsize(os.path.join(base, f))
                    self.assertLess(size, limit, f"{os.path.relpath(os.path.join(base, f), root)} is {size // 1024} KB - split it")

    def test_modules_stay_small(self):
        for f in ("main.py", "finally_audio.py", "finally_caps.py", "finally_capture.py", "finally_save.py", "finally_watchdog.py", "finally_files.py", "finally_diag.py", "finally_util.py"):
            n = len(open(os.path.join(HERE, "..", f)).read().splitlines())
            self.assertLess(n, 520, f"{f} has {n} lines - split it")


if __name__ == "__main__":
    unittest.main()
