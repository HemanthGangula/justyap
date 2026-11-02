"""Background service that turns Claude Code's built-in /voice into system-wide dictation.

A hidden `claude` session runs in a pseudo-terminal. Simulated key-repeat of a held
Space key records speech; Ctrl+G then opens the prompt in $EDITOR, which is
`justyap grab`, so the transcript is handed back without scraping the screen.
Enter is never sent, so nothing is ever submitted to Claude.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import pty
import re
import select
import shlex
import signal
import socket
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

from . import DATA_DIR, SOCKET_PATH, TRANSCRIPT_PATH, service

log = logging.getLogger("justyap")

VOICE_SETTINGS = json.dumps({"voice": {"enabled": True, "mode": "hold", "autoSubmit": False}})
TERMINAL_SIZE = struct.pack("HHHH", 40, 120, 0, 0)

KEY_REPEAT_INTERVAL = 0.033
MAX_HOLD = 110.0
STARTUP_IDLE = 1.0
LISTEN_TIMEOUT = 5.0
SETTLE_MIN = 0.3
SETTLE_IDLE = 0.4
SETTLE_TIMEOUT = 15.0
GRAB_TIMEOUT = 5.0
RESPAWN_DELAY = 2.0
SCREEN_LIMIT = 16_384

HOLD_KEY = b" "
OPEN_EDITOR = b"\x07"
DISCARD_RECORDING = b"\x1b"
ACCEPT_TRUST = (b"\x1b[B", b"\r")  # Down to "Yes, I trust this folder", then Enter
TRUST_KEY_GAP = 0.3
TRUST_PROMPT = "Itrustthisfolder"
MIC_METER = "▁▂▃▄▅▆▇"
SYSTEM_PYTHON = "/usr/bin/python3"
OVERLAY_SCRIPT = Path(__file__).with_name("overlay.py")
VOICE_ERRORS = (
    "No speech detected.",
    "No audio detected from microphone.",
    "Microphone access is denied.",
    "Voice connection failed.",
    "Voice mode requires a Claude.ai account.",
    "Voice mode is disabled by your organization's policy.",
    "Voice input is failing repeatedly and has been paused.",
    "Voice mode requires SoX for audio recording.",
    "Voice stream error.",
)
NOT_LISTENING = "Claude Code didn't start listening. See the README."
NO_TRANSCRIPT = "Claude Code did not return the transcript. See the README."
ANSI = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-_])")


def squash(text: str) -> str:
    """Drop all whitespace: the TUI moves the cursor instead of printing spaces."""
    return "".join(text.split())


def mic_level(text: str) -> float | None:
    """Latest reading of Claude Code's microphone meter (the recording cursor), from 0 to 1."""
    for char in reversed(text):
        if char in MIC_METER:
            return (MIC_METER.index(char) + 1) / len(MIC_METER)
    return None


def grab(path: str) -> None:
    """Act as Claude Code's $EDITOR: hand the prompt to the daemon and leave it empty."""
    prompt = Path(path)
    staging = TRANSCRIPT_PATH.with_suffix(".tmp")
    staging.write_text(prompt.read_text())
    staging.replace(TRANSCRIPT_PATH)
    prompt.write_text("")


class ClaudeSession:
    """A `claude` process in a pseudo-terminal, restarted whenever it exits."""

    def __init__(self, claude: str) -> None:
        self.claude = claude
        self.start()

    def start(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            self._exec()
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, TERMINAL_SIZE)
        self.screen = ""
        self.last_output = time.monotonic()
        self._ready = False
        self._trust_answered = False
        log.info("started Claude Code (pid %d)", self.pid)

    def _exec(self) -> None:
        editor = f"{shlex.quote(sys.executable)} -m justyap grab"
        try:
            os.chdir(DATA_DIR)
            os.environ.update(TERM="xterm-256color", EDITOR=editor, VISUAL=editor)
            os.execv(self.claude, [self.claude, "--settings", VOICE_SETTINGS])
        finally:
            os._exit(127)

    @property
    def ready(self) -> bool:
        # ponytail: "ready" = startup output went quiet; switch to a prompt marker if claude ever pauses mid-startup
        if not self._ready and self.idle() > STARTUP_IDLE:
            self._ready = True
            log.info("Claude Code is ready")
        return self._ready

    def idle(self) -> float:
        return time.monotonic() - self.last_output

    def write(self, data: bytes) -> None:
        with contextlib.suppress(OSError):  # claude exited; the next read() restarts it
            os.write(self.fd, data)

    def poll(self, timeout: float) -> None:
        if select.select([self.fd], [], [], timeout)[0]:
            self.read()

    def read(self) -> str:
        try:
            data = os.read(self.fd, 65536)
        except OSError:
            data = b""
        if not data:
            self._restart()
            return ""
        self.last_output = time.monotonic()
        text = squash(ANSI.sub(b"", data).decode(errors="replace"))
        self.screen = (self.screen + text)[-SCREEN_LIMIT:]
        if not self._trust_answered and TRUST_PROMPT in self.screen:
            log.warning("accepting Claude Code's folder-trust prompt for %s", DATA_DIR)
            self._trust_answered = True
            for key in ACCEPT_TRUST:  # sent together, Claude Code 2.1.316 drops the Enter
                time.sleep(TRUST_KEY_GAP)
                self.write(key)
        return text

    def _restart(self) -> None:
        os.close(self.fd)
        _, status = os.waitpid(self.pid, 0)
        log.error(
            "Claude Code exited with status %d; restarting. Last output: %s",
            os.waitstatus_to_exitcode(status),
            self.screen[-300:],
        )
        time.sleep(RESPAWN_DELAY)
        self.start()


class Overlay:
    """The floating island (overlay.py). Purely visual, so failures are logged, never raised."""

    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None

    def show(self) -> None:
        if not os.environ.get("DISPLAY") or not os.access(SYSTEM_PYTHON, os.X_OK):
            return
        try:
            self.process = subprocess.Popen(
                [SYSTEM_PYTHON, str(OVERLAY_SCRIPT)], stdin=subprocess.PIPE, env={**os.environ, "GDK_BACKEND": "x11"}
            )
        except OSError as error:
            log.warning("could not show the overlay: %s", error)

    def send(self, line: str) -> None:
        if not self.process:
            return
        try:
            self.process.stdin.write(f"{line}\n".encode())
            self.process.stdin.flush()
        except OSError:
            self.hide()

    def hide(self) -> None:
        # ponytail: not waited on, so an error can stay on screen; subprocess reaps it on the next Popen
        if not self.process:
            return
        process, self.process = self.process, None
        try:
            process.stdin.close()
        except OSError:
            process.kill()


class Daemon:
    """Serves `toggle` and `cancel` on a Unix socket: toggle starts recording, then returns the text."""

    def __init__(self, claude: str) -> None:
        self.server = _listen()
        self.session = ClaudeSession(claude)
        self.overlay = Overlay()
        self.recording_since: float | None = None
        self.heard_meter = False
        self._session_keys(False)

    def run(self) -> None:
        try:
            while True:
                self._step()
        finally:
            self.overlay.hide()
            self._session_keys(False)

    def _step(self) -> None:
        holding = self.recording_since is not None and time.monotonic() - self.recording_since < MAX_HOLD
        if holding:
            self.session.write(HOLD_KEY)
        timeout = KEY_REPEAT_INTERVAL if holding else None if self.session.ready else STARTUP_IDLE
        readable = select.select([self.session.fd, self.server], [], [], timeout)[0]
        if self.session.fd in readable:
            level = mic_level(self.session.read())
            if self.recording_since is not None and level is not None:
                self.heard_meter = True
                self.overlay.send(f"level {level:.2f}")
        if self.recording_since is not None and not self.heard_meter and time.monotonic() - self.recording_since > LISTEN_TIMEOUT:
            self._discard(self._voice_error(NOT_LISTENING))
        if self.server in readable:
            self._serve_one()

    def _serve_one(self) -> None:
        conn, _ = self.server.accept()
        with conn:
            conn.settimeout(2)
            try:
                command = conn.recv(64).decode().strip()
            except OSError:
                return
            handler = {"toggle": self.toggle, "cancel": self.cancel}.get(command)
            reply = handler() if handler else {"error": f"Unknown command: {command!r}"}
            try:
                conn.sendall(json.dumps(reply).encode())
            except OSError:
                log.warning("client disconnected before the reply")

    def toggle(self) -> dict[str, str]:
        if not self.session.ready:
            return {"error": "Claude Code is still starting. Try again in a moment."}
        if self.recording_since is None:
            self.session.screen = ""
            self.recording_since = time.monotonic()
            self.heard_meter = False
            self.overlay.show()
            self._session_keys(True)
            return {"state": "listening"}
        self.recording_since = None
        self.overlay.send("processing")
        reply = self._collect()
        if "text" in reply:
            self.overlay.send("done")  # the island shows it only when nothing focused could take the paste
        self._end_dictation(reply.get("error"))
        return reply

    def cancel(self) -> dict[str, str]:
        if self.recording_since is None:
            return {"state": "idle"}
        self._discard()
        return {"state": "cancelled"}

    def _discard(self, error: str | None = None) -> None:
        self.recording_since = None
        self.session.write(DISCARD_RECORDING)
        self._collect()  # also clears whatever already reached the prompt
        self._end_dictation(error)

    def _end_dictation(self, error: str | None = None) -> None:
        if error:
            log.warning("dictation failed: %s", error)
            self.overlay.send(f"error {error}")
        self.overlay.hide()
        self._session_keys(False)

    def _session_keys(self, active: bool) -> None:
        try:
            service.set_session_keys(active)
        except (OSError, subprocess.CalledProcessError) as error:
            log.warning("could not %s Enter/Esc: %s", "bind" if active else "release", error)

    def _collect(self) -> dict[str, str]:
        session = self.session
        released = time.monotonic()
        # ponytail: transcript is final once the screen is quiet; claude's "processing…" redraws every ~60 ms
        while time.monotonic() - released < SETTLE_TIMEOUT:
            session.poll(0.05)
            if time.monotonic() - released > SETTLE_MIN and session.idle() > SETTLE_IDLE:
                break

        TRANSCRIPT_PATH.unlink(missing_ok=True)
        session.write(OPEN_EDITOR)
        deadline = time.monotonic() + GRAB_TIMEOUT
        while not TRANSCRIPT_PATH.exists() and time.monotonic() < deadline:
            session.poll(0.05)
        if not TRANSCRIPT_PATH.exists():
            log.error("no transcript from Claude Code. Last output: %s", session.screen[-300:])
            return {"error": NO_TRANSCRIPT}

        text = TRANSCRIPT_PATH.read_text().strip()
        TRANSCRIPT_PATH.unlink()
        return {"text": text} if text else {"error": self._voice_error(VOICE_ERRORS[0])}

    def _voice_error(self, default: str) -> str:
        return next((e for e in VOICE_ERRORS if squash(e) in self.session.screen), default)


def _listen() -> socket.socket:
    if SOCKET_PATH.exists():
        with socket.socket(socket.AF_UNIX) as probe:
            if probe.connect_ex(str(SOCKET_PATH)) == 0:
                raise SystemExit("justyap daemon is already running")
        SOCKET_PATH.unlink()
    server = socket.socket(socket.AF_UNIX)
    umask = os.umask(0o177)
    try:
        server.bind(str(SOCKET_PATH))
    finally:
        os.umask(umask)
    server.listen()
    return server


def serve(claude: str) -> None:
    if not os.access(claude, os.X_OK):
        raise SystemExit(f"Claude Code not found or not executable: {claude}")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    Daemon(claude).run()
