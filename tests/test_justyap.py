import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from justyap import keyboard
from justyap.daemon import LISTEN_TIMEOUT, mic_level

FAKE_CLAUDE = f"""#!{sys.executable}
import os, shlex, subprocess, sys, tempfile, tty
tty.setraw(0)
print("fake claude ready", flush=True)
heard = b""
while chunk := os.read(0, 1024):
    heard += chunk
    if b" " in chunk and not os.environ.get("FAKE_NO_METER"):
        print("\u2585", end="", flush=True)
    if b"\\x1b" in chunk:
        heard = b""
    if b"\\x07" in chunk:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("  hello world  " if heard.count(b" ") > 3 else "   ")
        prompt = f.name
        subprocess.run([*shlex.split(os.environ["EDITOR"]), prompt], check=True)
        if open(prompt).read():
            sys.exit("prompt was not emptied")
        heard = b""
"""


class KeyboardTest(unittest.TestCase):
    def test_parse_keys(self):
        self.assertEqual(keyboard.parse_keys("Ctrl+Shift+V"), [29, 42, 47])
        with self.assertRaises(ValueError):
            keyboard.parse_keys("ctrl+q")

    def test_uinput_device_matches_kernel_struct(self):
        self.assertEqual(len(keyboard.UINPUT_DEVICE), 1116)

    def test_paste_puts_back_text_but_leaves_images_alone(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        tmp = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        clip, types = tmp / "clip", tmp / "types"
        (tmp / "wl-copy").write_text(f"#!/bin/sh\ncat > {clip}\necho text/plain > {types}\n")
        (tmp / "wl-paste").write_text(f'#!/bin/sh\n[ "$1" = --list-types ] && exec cat {types}\nexec cat {clip}\n')
        for tool in ("wl-copy", "wl-paste"):
            (tmp / tool).chmod(0o755)
        stack.enter_context(mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "test", "PATH": f"{tmp}:{os.environ['PATH']}"}))
        stack.enter_context(mock.patch.object(keyboard, "RESTORE_DELAY", 0))
        pasted = []
        stack.enter_context(mock.patch.object(keyboard, "press", lambda _keys: pasted.append(clip.read_text())))

        clip.write_text("my copied link")
        types.write_text("text/plain\n")
        keyboard.paste("hello world", "ctrl+v")
        self.assertEqual((pasted, clip.read_text()), (["hello world"], "my copied link"))

        types.write_text("image/png\n")
        keyboard.paste("hello again", "ctrl+v")
        self.assertEqual(clip.read_text(), "hello again")


class MicLevelTest(unittest.TestCase):
    def test_reads_latest_meter_glyph(self):
        self.assertEqual(mic_level("listening\u2581\u2583x\u2587"), 1.0)
        self.assertAlmostEqual(mic_level("\u2581"), 1 / 7)
        self.assertIsNone(mic_level("no meter here"))


class DaemonCase(unittest.TestCase):
    """Drives the real daemon against a fake `claude` that behaves like the voice TUI."""

    meter = True

    def setUp(self):
        workdir = tempfile.TemporaryDirectory()
        self.addCleanup(workdir.cleanup)
        tmp = Path(workdir.name)
        fake = tmp / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        self.socket_path = tmp / "justyap.sock"
        env = {**os.environ, "FAKE_NO_METER": "" if self.meter else "1", "XDG_RUNTIME_DIR": str(tmp), "XDG_DATA_HOME": str(tmp)}
        for desktop_var in ("DISPLAY", "XDG_CURRENT_DESKTOP"):  # no island, no real GNOME shortcuts
            env.pop(desktop_var, None)
        self.daemon = subprocess.Popen(
            [sys.executable, "-m", "justyap", "daemon", "--claude", str(fake)], env=env, stderr=subprocess.DEVNULL
        )
        self.addCleanup(self.daemon.wait)
        self.addCleanup(self.daemon.kill)
        deadline = time.monotonic() + 10
        while (reply := self.request("toggle")) != {"state": "listening"}:
            self.assertIn("still starting", reply.get("error", ""), reply)
            self.assertLess(time.monotonic(), deadline, "daemon never became ready")
            time.sleep(0.2)

    def request(self, command):
        try:
            with socket.socket(socket.AF_UNIX) as client:
                client.connect(str(self.socket_path))
                client.sendall(command.encode())
                client.shutdown(socket.SHUT_WR)
                return json.loads(b"".join(iter(lambda: client.recv(65536), b"")))
        except (FileNotFoundError, ConnectionRefusedError):
            return {"error": "socket not up, still starting"}


class DaemonTest(DaemonCase):
    def test_dictation_round_trip(self):
        time.sleep(0.5)
        self.assertEqual(self.request("toggle"), {"text": "hello world"})

    def test_quick_double_tap_reports_no_speech(self):
        self.assertEqual(self.request("toggle"), {"error": "No speech detected."})

    def test_cancel_discards_and_allows_a_new_dictation(self):
        time.sleep(0.5)
        self.assertEqual(self.request("cancel"), {"state": "cancelled"})
        self.assertEqual(self.request("cancel"), {"state": "idle"})
        self.assertEqual(self.request("toggle"), {"state": "listening"})

    def test_unknown_command(self):
        self.assertIn("Unknown command", self.request("bogus")["error"])


class ClaudeNotListeningTest(DaemonCase):
    meter = False

    def test_gives_up_when_the_mic_meter_never_appears(self):
        time.sleep(LISTEN_TIMEOUT + 1.5)
        self.assertEqual(self.request("cancel"), {"state": "idle"})


if __name__ == "__main__":
    unittest.main()
