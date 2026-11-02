"""Per-user installation (systemd user service, GNOME shortcuts) and the Enter/Esc grab used during dictation."""

from __future__ import annotations

import ast
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .keyboard import clipboard_command

SERVICE = "justyap.service"
UNIT_PATH = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd/user" / SERVICE
UDEV_RULE = "/etc/udev/rules.d/70-justyap-uinput.rules"
MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
SHORTCUTS_ROOT = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
HOTKEY_SHORTCUT = f"{SHORTCUTS_ROOT}/justyap/"
FINISH_SHORTCUT = f"{SHORTCUTS_ROOT}/justyap-finish/"
CANCEL_SHORTCUT = f"{SHORTCUTS_ROOT}/justyap-cancel/"
SESSION_KEYS = {FINISH_SHORTCUT: "Return", CANCEL_SHORTCUT: "Escape"}
SHORTCUT_KEYS = ("name", "command", "binding")
UNIT = """\
[Unit]
Description=justyap: system-wide dictation via Claude Code
# Start with the desktop, not at boot: the island, the paste and the microphone need the session's display and audio.
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart={command} daemon --claude {claude}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
"""


def setup(hotkey: str, paste_keys: str) -> int:
    claude = shutil.which("claude")
    if not claude:
        print("Claude Code is not installed. Install it, run `claude`, and sign in with your claude.ai account.")
        return 1
    _install_service(claude)
    print(f"Started {SERVICE}")

    toggle = f"{_self_command()} toggle --paste-keys {shlex.quote(paste_keys)}"
    if _is_gnome():
        _set_shortcut(HOTKEY_SHORTCUT, "justyap", toggle, hotkey)
        _set_shortcut(FINISH_SHORTCUT, "justyap: paste (Enter while listening)", toggle, "")
        _set_shortcut(CANCEL_SHORTCUT, "justyap: cancel (Esc while listening)", f"{_self_command()} cancel", "")
        print(f"Bound {hotkey} to dictation; while listening, Enter pastes and Esc cancels")
        for conflict in _hotkey_conflicts(hotkey):
            print(f"warning: {hotkey} is also used by {conflict}; pick another with --hotkey")
    else:
        print(f"Bind this command to a hotkey in your desktop's keyboard settings:\n  {toggle}")

    for problem in _missing_requirements():
        print(f"warning: {problem}")
    print("Press the hotkey, speak, then press Enter to paste.")
    return 0


def uninstall() -> int:
    if UNIT_PATH.exists():
        _systemctl("disable", "--now", SERVICE)
        UNIT_PATH.unlink()
        _systemctl("daemon-reload")
    if _is_gnome():
        ours = (HOTKEY_SHORTCUT, *SESSION_KEYS)
        _gsettings("set", MEDIA_KEYS, "custom-keybindings", str([p for p in _shortcut_paths() if p not in ours]))
        for path in ours:
            for key in SHORTCUT_KEYS:
                _gsettings("reset", _schema(path), key)
    print(f"Removed the service and hotkey. To also drop uinput access: sudo rm {UDEV_RULE}")
    return 0


def set_session_keys(active: bool) -> None:
    """Bind Enter/Esc to paste/cancel while dictating, and release them afterwards (GNOME only)."""
    if not _is_gnome():
        return
    installed = _shortcut_paths()
    for path, key in SESSION_KEYS.items():
        if path in installed:
            _gsettings("set", _schema(path), "binding", key if active else "")


def _self_command() -> str:
    return shlex.join([sys.executable, "-m", "justyap"])


def _install_service(claude: str) -> None:
    UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIT_PATH.write_text(UNIT.format(command=_self_command(), claude=shlex.quote(claude)))
    _systemctl("daemon-reload")
    _systemctl("reenable", SERVICE)  # also drops the default.target link older versions created
    _systemctl("restart", SERVICE)


def _missing_requirements() -> list[str]:
    problems = []
    clipboard = clipboard_command()[0]
    if not shutil.which(clipboard):
        problems.append(f"{clipboard} is not installed; run install.sh")
    if not os.access("/dev/uinput", os.W_OK):
        problems.append("no write access to /dev/uinput; run install.sh")
    return problems


def _is_gnome() -> bool:
    return "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper() and shutil.which("gsettings") is not None


def _schema(path: str) -> str:
    return f"{MEDIA_KEYS}.custom-keybinding:{path}"


def _set_shortcut(path: str, name: str, command: str, binding: str) -> None:
    paths = _shortcut_paths()
    if path not in paths:
        _gsettings("set", MEDIA_KEYS, "custom-keybindings", str([*paths, path]))
    for key, value in zip(SHORTCUT_KEYS, (name, command, binding), strict=True):
        _gsettings("set", _schema(path), key, value)


def _shortcut_paths() -> list[str]:
    value = _gsettings("get", MEDIA_KEYS, "custom-keybindings")
    return [] if value.startswith("@as") else ast.literal_eval(value)


def _hotkey_conflicts(hotkey: str) -> list[str]:
    needle = f"'{hotkey.lower()}'"
    return [" ".join(line.split()[:2]) for line in _gsettings("list-recursively").splitlines() if needle in line.lower()]


def _gsettings(*args: str) -> str:
    return subprocess.run(["gsettings", *args], check=True, capture_output=True, text=True).stdout.strip()


def _systemctl(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=True)
