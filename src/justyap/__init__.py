"""System-wide voice dictation for Linux, powered by Claude Code's built-in /voice."""

import os
from pathlib import Path

__version__ = "0.1.2"

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "justyap"
SOCKET_PATH = RUNTIME_DIR / "justyap.sock"
TRANSCRIPT_PATH = RUNTIME_DIR / "justyap.txt"
