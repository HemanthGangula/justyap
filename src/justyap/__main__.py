"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import socket
import sys

from . import SOCKET_PATH, __version__, daemon, keyboard, service

DEFAULT_HOTKEY = "<Super><Alt>h"
DEFAULT_PASTE_KEYS = "ctrl+v"
TERMINAL_PASTE_KEYS = "ctrl+shift+v"  # terminals ignore Ctrl+V, and bash treats it as "type the next key literally"
REQUEST_TIMEOUT = daemon.SETTLE_TIMEOUT + daemon.GRAB_TIMEOUT + 5


def request(command: str) -> dict[str, str]:
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(REQUEST_TIMEOUT)
        client.connect(str(SOCKET_PATH))
        client.sendall(command.encode())
        client.shutdown(socket.SHUT_WR)
        return json.loads(b"".join(iter(lambda: client.recv(65536), b"")))


def report(message: str) -> None:
    print(message, file=sys.stderr)


def ask_daemon(command: str) -> dict[str, str] | None:
    try:
        reply = request(command)
    except (FileNotFoundError, ConnectionRefusedError):
        report("The background service is not running. Run: justyap setup")
        return None
    except TimeoutError:
        report("The background service did not respond.")
        return None
    if "error" in reply:
        report(reply["error"])
        return None
    return reply


def dictate(command: str, paste_keys: str) -> int:
    reply = ask_daemon(command)
    if reply is None:
        return 1
    if "text" not in reply:  # started listening, or there was nothing to stop
        return 0
    focus = reply.get("paste")
    if focus == "terminal" and paste_keys == DEFAULT_PASTE_KEYS:
        paste_keys = TERMINAL_PASTE_KEYS
    try:
        if focus == "copy":  # nowhere to type: the text waits on the clipboard for Ctrl+V
            keyboard.copy_to_clipboard(reply["text"])
        else:  # put the old clipboard back only when sure the paste landed, or the dictation would be lost
            keyboard.paste(reply["text"], paste_keys, restore=focus in ("paste", "terminal"))
    except keyboard.PasteError as error:
        report(str(error))
        return 1
    return 0


def cancel() -> int:
    return 0 if ask_daemon("cancel") else 1


def key_combo(value: str) -> str:
    try:
        keyboard.parse_keys(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="justyap", description="System-wide voice dictation powered by Claude Code's /voice.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    paste_help = "keys that paste in the focused app; terminals get ctrl+shift+v when the default is used (default: %(default)s)"
    toggle_cmd = commands.add_parser("toggle", help="start listening, or stop and paste the text (bind this to a hotkey)")
    toggle_cmd.add_argument("--paste-keys", type=key_combo, default=DEFAULT_PASTE_KEYS, help=paste_help)
    commands.add_parser("start", help="start listening; does nothing if already listening (for hold-to-talk key press)")
    stop_cmd = commands.add_parser("stop", help="stop and paste the text; does nothing if not listening (for key release)")
    stop_cmd.add_argument("--paste-keys", type=key_combo, default=DEFAULT_PASTE_KEYS, help=paste_help)

    setup_cmd = commands.add_parser("setup", help="install the background service and the GNOME hotkey")
    setup_cmd.add_argument("--hotkey", default=DEFAULT_HOTKEY, help="GNOME accelerator (default: %(default)s)")
    setup_cmd.add_argument("--paste-keys", type=key_combo, default=DEFAULT_PASTE_KEYS, help=paste_help)

    commands.add_parser("cancel", help="stop listening and discard the recording (bound to Esc while listening)")
    commands.add_parser("uninstall", help="remove the background service and the hotkey")

    daemon_cmd = commands.add_parser("daemon", help="run the background service (started by systemd)")
    daemon_cmd.add_argument("--claude", default=shutil.which("claude"), help="path to the claude executable")

    grab_cmd = commands.add_parser("grab", help="internal: Claude Code's $EDITOR inside the daemon")
    grab_cmd.add_argument("file")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in ("toggle", "start", "stop"):
            return dictate(args.command, getattr(args, "paste_keys", DEFAULT_PASTE_KEYS))
        if args.command == "setup":
            return service.setup(args.hotkey, args.paste_keys)
        if args.command == "cancel":
            return cancel()
        if args.command == "uninstall":
            return service.uninstall()
        if args.command == "grab":
            daemon.grab(args.file)
            return 0
        if not args.claude:
            parser.error("Claude Code ('claude') is not on PATH; pass --claude")
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        daemon.serve(args.claude)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
