"""Shared helpers: paths/ownership, clean subprocess environment, command runner, Game Mode detection."""

import asyncio
import decky
import glob
import os
import pwd
import time
from typing import Dict, List, Optional, Tuple


log = decky.logger


EVENT = "finally_recorder_event"


SINK = "Finally-Rec-Sink"


MARK = "Finally-Rec"


VIDEO_EXT = (".mkv", ".mp4")


DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"


def _home() -> str:
    return getattr(decky, "DECKY_USER_HOME", "") or os.path.expanduser("~")


def _user_ids() -> Tuple[int, int]:
    try:
        pw = pwd.getpwnam(getattr(decky, "DECKY_USER", "") or "deck")
        return pw.pw_uid, pw.pw_gid
    except Exception:
        return 1000, 1000


def _chown(path: str) -> None:
    try:
        uid, gid = _user_ids()
        os.chown(path, uid, gid)
    except Exception:
        pass


def _makedirs(path: str) -> None:
    """mkdir -p, owned by the Deck user (Decky itself usually runs as root)."""
    missing = []
    p = path
    while p and not os.path.isdir(p):
        missing.append(p)
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    os.makedirs(path, exist_ok=True)
    for m in missing:
        _chown(m)


def _clean_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Decky is a PyInstaller app: its library paths would break system binaries (same clean-up
    Finally's overlay launcher uses). Also point at the Deck user's PipeWire/Pulse sockets."""
    env = dict(os.environ)
    orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig is not None:
        env["LD_LIBRARY_PATH"] = orig
    else:
        env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)
    for k in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(k, None)
    uid, _ = _user_ids()
    rt = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    if not os.path.isdir(rt):
        rt = f"/run/user/{uid}"
    env["XDG_RUNTIME_DIR"] = rt
    sock = os.path.join(rt, "pulse", "native")
    if os.path.exists(sock):
        env["PULSE_SERVER"] = f"unix:{sock}"
    env["HOME"] = _home()
    env["PATH"] = env.get("PATH", "") + (":" if env.get("PATH") else "") + DEFAULT_PATH
    if extra:
        env.update(extra)
    return env


async def _run(argv: List[str], timeout: float = 10.0, env: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
    """Run a command (no shell). Returns (returncode, stdout, stderr); rc -1 = not found/timeout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL, env=env or _clean_env())
    except FileNotFoundError:
        return -1, "", f"{argv[0]}: not found"
    except Exception as e:
        return -1, "", f"{argv[0]}: {e}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return -1, "", f"{argv[0]}: timed out after {timeout:.0f}s"
    return proc.returncode if proc.returncode is not None else -1, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _game_mode_signal(proc: str = "/proc") -> str:
    """Name of the process that proves Game Mode ('' = none). The original plugin only looked for
    'gamescope-session' in a command line; a process called gamescope* counts too."""
    for d in glob.glob(os.path.join(proc, "[0-9]*")):
        try:
            with open(os.path.join(d, "cmdline"), "rb") as f:
                if b"gamescope-session" in f.read():
                    return "gamescope-session"
        except Exception:
            continue
        try:
            with open(os.path.join(d, "comm"), "r") as f:
                comm = f.read().strip()
            if comm.startswith("gamescope"):
                return "process " + comm
        except Exception:
            continue
    return ""


_gm_cache: Tuple[float, str] = (0.0, "")


def _in_game_mode() -> bool:
    """Cached for 5 s: the watchdog asks every 2 s and scanning /proc is not free."""
    global _gm_cache
    now = time.time()
    if now - _gm_cache[0] > 5:
        _gm_cache = (now, _game_mode_signal())
    return bool(_gm_cache[1])
