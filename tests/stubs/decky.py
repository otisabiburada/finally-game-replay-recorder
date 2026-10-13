"""Minimal stand-in for Decky's `decky` module (tests only)."""
import logging, os, tempfile
logger = logging.getLogger("decky-stub")
_root = os.environ.get("FINALLY_TEST_ROOT") or tempfile.mkdtemp(prefix="finallytest_")
DECKY_PLUGIN_SETTINGS_DIR = os.path.join(_root, "settings")
DECKY_PLUGIN_LOG_DIR = os.path.join(_root, "logs")
DECKY_USER_HOME = os.path.join(_root, "home")
DECKY_USER = "root"
events = []
async def emit(name, *args):
    events.append((name, args))
