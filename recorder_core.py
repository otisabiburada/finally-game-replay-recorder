"""Pure logic of Finally Game Recorder (no Decky, no subprocesses) - facade over three small modules:

  core_base.py    settings, file names, replay-segment selection, freeze/speed monitors, small parsers
  core_gst.py     all-GStreamer pipeline (encoder choice, gst-launch argv)
  core_ffmpeg.py  ffmpeg engine (raw-frame feeder + ffmpeg argv) and engine choice
  core_system.py  GPU/power cost meter, capability-cache rule

Everything is re-exported here, so `import recorder_core as core` keeps working everywhere.
"""
from core_base import *    # noqa: F401,F403
from core_gst import *     # noqa: F401,F403
from core_ffmpeg import *  # noqa: F401,F403
from core_system import *  # noqa: F401,F403
