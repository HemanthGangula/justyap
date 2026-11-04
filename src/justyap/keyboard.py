"""Paste text at the cursor: copy it to the clipboard, press the paste keys on a
short-lived virtual keyboard (/dev/uinput), then put the previous clipboard text back.
Works on Wayland and X11."""

from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import struct
import subprocess
import time

KEYCODES = {"ctrl": 29, "shift": 42, "alt": 56, "super": 125, "insert": 110, "v": 47}

EV_SYN, EV_KEY, SYN_REPORT = 0, 1, 0
UI_SET_EVBIT, UI_SET_KEYBIT = 0x40045564, 0x40045565
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
BUS_VIRTUAL = 0x06
UINPUT_DEVICE = struct.pack("80sHHHHI256i", b"justyap", BUS_VIRTUAL, 0, 0, 1, 0, *[0] * 256)
INPUT_EVENT = "llHHi"

DEVICE_SETTLE = 0.2
FLUSH_DELAY = 0.05
RESTORE_DELAY = 0.5  # the app fetches the clipboard after the keypress; putting the old text back sooner pastes that instead

TEXT_TYPES = {"text/plain", "text/plain;charset=utf-8", "UTF8_STRING", "STRING"}
KEEP_AS_IS = ("image/", "text/uri-list", "x-special/gnome-copied-files", "x-kde-passwordManagerHint")


class PasteError(Exception):
    """Pasting failed for a reason the user can fix."""


def parse_keys(combo: str) -> list[int]:
    names = combo.lower().split("+")
    unknown = [name for name in names if name not in KEYCODES]
    if unknown:
        raise ValueError(f"unknown key {unknown[0]!r} in {combo!r}; use {'+'.join(KEYCODES)}")
    return [KEYCODES[name] for name in names]


def clipboard_command() -> list[str]:
    return ["wl-copy"] if os.environ.get("WAYLAND_DISPLAY") else ["xclip", "-selection", "clipboard"]


def paste(text: str, combo: str, restore: bool = False) -> None:
    """Paste, then optionally put back the text that was on the clipboard before."""
    previous = read_clipboard() if restore else None
    copy_to_clipboard(text)
    press(parse_keys(combo))
    if previous is not None:
        time.sleep(RESTORE_DELAY)
        with contextlib.suppress(PasteError):
            copy_to_clipboard(previous)


def read_clipboard() -> str | None:
    """The clipboard's text, or None when it is empty or holds something else (an image, files, a password)."""
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    list_types = ["wl-paste", "--list-types"] if wayland else ["xclip", "-selection", "clipboard", "-o", "-t", "TARGETS"]
    read_text = ["wl-paste", "--no-newline", "--type", "text/plain"] if wayland else ["xclip", "-selection", "clipboard", "-o"]
    try:
        types = subprocess.run(list_types, capture_output=True, check=True, timeout=2).stdout.decode().split()
        if not TEXT_TYPES.intersection(types) or any(t.startswith(KEEP_AS_IS) for t in types):
            return None
        # ponytail: text only, so rich text comes back as plain text
        return subprocess.run(read_text, capture_output=True, check=True, timeout=2).stdout.decode()
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return None


def copy_to_clipboard(text: str) -> None:
    command = clipboard_command()
    if not shutil.which(command[0]):
        raise PasteError(f"{command[0]} is not installed. Run install.sh.")
    try:
        subprocess.run(command, input=text.encode(), check=True, timeout=5)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise PasteError(f"Could not copy to the clipboard: {error}") from error


def press(keys: list[int]) -> None:
    try:
        fd = os.open("/dev/uinput", os.O_WRONLY)
    except PermissionError as error:
        raise PasteError("No access to /dev/uinput. Run install.sh.") from error
    try:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        for key in keys:
            fcntl.ioctl(fd, UI_SET_KEYBIT, key)
        os.write(fd, UINPUT_DEVICE)
        fcntl.ioctl(fd, UI_DEV_CREATE)
        time.sleep(DEVICE_SETTLE)  # compositors ignore a keyboard until they have registered it
        for key in keys:
            _emit(fd, key, 1)
        for key in reversed(keys):
            _emit(fd, key, 0)
        time.sleep(FLUSH_DELAY)
        fcntl.ioctl(fd, UI_DEV_DESTROY)
    finally:
        os.close(fd)


def _emit(fd: int, key: int, value: int) -> None:
    os.write(fd, struct.pack(INPUT_EVENT, 0, 0, EV_KEY, key, value) + struct.pack(INPUT_EVENT, 0, 0, EV_SYN, SYN_REPORT, 0))
