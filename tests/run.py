#!/usr/bin/env python3
"""One entry point for all checks. Output is deliberately tiny (one line per area, details only for failures).

  python3 tests/run.py quick          lint + core/regression/API tests + TypeScript check      (~5 s)  <- after every edit
  python3 tests/run.py area NAME      one backend area: record | replay | ffmpeg | watchdog | misc | hotkeys | playback | adapt | system  (each well under 2 min)
  python3 tests/run.py full           ALL of the above, in the BACKGROUND; returns at once
  python3 tests/run.py status         one-line progress / result of the background run (+ failures only)
  python3 tests/run.py stop           stop the background run (by pid file - never use `pkill -f`)
  python3 tests/run.py map            every source file: size + purpose (read THIS instead of opening files)

Why: the whole backend suite takes minutes (real ffmpeg, one CPU core). A foreground run longer than the tool limit
looks like a hang. Run `quick` often, the matching `area` when you touched that area, `full` only before packaging.
"""
import json, os, signal, subprocess, sys, time, unittest, warnings

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE = "/tmp/finally_tests"
os.makedirs(STATE, exist_ok=True)
AREAS = {"record": "test_be_record", "replay": "test_be_replay", "ffmpeg": "test_be_ffmpeg",
         "watchdog": "test_be_watchdog", "misc": "test_be_misc", "hotkeys": "test_be_hotkeys", "playback": "test_be_playback", "adapt": "test_be_adapt", "system": "test_be_system"}
FAST = ["test_core_base", "test_core_engine", "test_regressions", "test_api_surface"]
warnings.simplefilter("ignore")


class Quiet(unittest.TextTestResult):
    """No dots, no per-test lines; remembers failures as short text."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.short = []

    def _add(self, kind, test, err):
        tb = self._exc_info_to_string(err, test).strip().splitlines()
        self.short.append(f"{kind} {test.id().split('.', 1)[-1]}\n    " + "\n    ".join(tb[-6:])[:900])

    def addFailure(self, test, err):
        super().addFailure(test, err); self._add("FAIL", test, err)

    def addError(self, test, err):
        super().addError(test, err); self._add("ERROR", test, err)


def run_modules(mods):
    os.chdir(HERE)
    sys.path[:0] = [HERE, ROOT]
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(m) for m in mods)
    with open(os.devnull, "w") as null:
        res = unittest.TextTestRunner(stream=null, resultclass=Quiet, verbosity=0).run(suite)
    return res


def lint() -> str:
    r = subprocess.run([sys.executable, os.path.join(HERE, "lint_names.py")] + [
        os.path.join(ROOT, f) for f in sorted(os.listdir(ROOT)) if f.endswith(".py")], capture_output=True, text=True)
    return "" if r.returncode == 0 else r.stdout.strip()


def tsc() -> str:
    tc = os.path.join(ROOT, "typecheck")
    if not os.path.isdir(tc):
        return ""
    try:
        r = subprocess.run(["tsc", "-p", "."], cwd=tc, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return "tsc not installed (skipped)"
    return "" if r.returncode == 0 else "\n".join(r.stdout.strip().splitlines()[:12])


def report(name, res, secs):
    ok = res.wasSuccessful()
    print(f"{'OK  ' if ok else 'FAIL'} {name}: {res.testsRun} tests, {secs:.0f}s")
    for s in res.short:
        print(s)
    return ok


def do_quick() -> bool:
    t = time.time(); good = True
    e = lint(); print(("OK   lint" if not e else "FAIL lint\n" + e)); good &= not e
    res = run_modules(FAST); good &= report("core+regressions+api", res, time.time() - t)
    e = tsc(); print(("OK   tsc" if not e else "FAIL tsc\n" + e)); good &= (not e) or "skipped" in e
    return good


def do_area(name) -> bool:
    t = time.time()
    return report(f"area {name}", run_modules([AREAS[name]]), time.time() - t)


def do_full_worker() -> bool:
    """Runs in the background; writes status.json after every step."""
    st = {"state": "running", "start": time.time(), "step": "quick", "done": [], "failed": [], "text": ""}

    def save():
        with open(os.path.join(STATE, "status.json"), "w") as f:
            json.dump(st, f)
    save()
    import io, contextlib
    steps = [("quick", do_quick)] + [(f"area {a}", (lambda a=a: do_area(a))) for a in AREAS]
    for label, fn in steps:
        st["step"] = label; save()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = fn()
        st["text"] += buf.getvalue()
        (st["done"] if ok else st["failed"]).append(label); save()
    st["state"] = "done"; st["step"] = ""; st["end"] = time.time(); save()
    return not st["failed"]


def do_status():
    p = os.path.join(STATE, "status.json")
    if not os.path.exists(p):
        print("no background run yet (python3 tests/run.py full)"); return
    st = json.load(open(p))
    el = int((st.get("end") or time.time()) - st["start"])
    if st["state"] == "running":
        print(f"RUNNING {el}s - now: {st['step']} - finished: {len(st['done']) + len(st['failed'])} step(s), failed so far: {st['failed'] or 'none'}")
    else:
        print(f"{'ALL GREEN' if not st['failed'] else 'FAILED: ' + ', '.join(st['failed'])} in {el}s ({', '.join(st['done'])})")
    if st["state"] == "done" or st["failed"]:
        print("\n".join(l for l in st["text"].splitlines() if not l.startswith("OK")))


def do_map():
    """Size and one-line purpose of every file, so nothing has to be opened just to find out what it is."""
    import ast, re
    rows = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "dist", "__pycache__", ".git", "fakebin", "stubs")]
        for f in sorted(files):
            if not f.endswith((".py", ".ts", ".tsx")):
                continue
            path = os.path.join(base, f)
            text = open(path, encoding="utf-8").read()
            if f.endswith(".py"):
                try:
                    doc = ast.get_docstring(ast.parse(text)) or ""
                except SyntaxError:
                    doc = ""
            else:
                m = re.match(r"\s*(?://\s*(.+)|/\*\*?\s*(.+?)\s*\*/)", text)
                doc = (m.group(1) or m.group(2)) if m else ""
            rows.append((os.path.relpath(path, ROOT), len(text.encode()), doc.strip().splitlines()[0][:95] if doc.strip() else ""))
    total = sum(r[1] for r in rows)
    for rel, size, doc in sorted(rows):
        print(f"{rel:34s}{size/1024:6.1f} KB  {doc}")
    print(f"{'TOTAL':34s}{total/1024:6.1f} KB  (~{total // 4000}k tokens if everything were read - don't)")


def main():
    a = sys.argv[1:] or ["quick"]
    cmd = a[0]
    if cmd == "quick":
        sys.exit(0 if do_quick() else 1)
    if cmd == "area" and len(a) == 2 and a[1] in AREAS:
        sys.exit(0 if do_area(a[1]) else 1)
    if cmd == "full":
        pidf = os.path.join(STATE, "full.pid")
        if os.path.exists(pidf):
            try:
                os.kill(int(open(pidf).read()), 0)
                print("a full run is already active - use `status` (or `stop`)"); return
            except (OSError, ValueError):
                pass
        pr = subprocess.Popen([sys.executable, os.path.abspath(__file__), "_full"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, start_new_session=True)
        open(pidf, "w").write(str(pr.pid))
        print(f"started in background (pid {pr.pid}); check with: python3 tests/run.py status")
        return
    if cmd == "_full":
        sys.exit(0 if do_full_worker() else 1)
    if cmd == "status":
        return do_status()
    if cmd == "map":
        return do_map()
    if cmd == "stop":
        pidf = os.path.join(STATE, "full.pid")
        try:
            os.killpg(int(open(pidf).read()), signal.SIGTERM); print("stopped")
        except (OSError, ValueError):
            print("nothing running")
        return
    print(__doc__)


if __name__ == "__main__":
    main()
