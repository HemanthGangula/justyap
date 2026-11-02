<div align="center">

# justyap

**Just yap. It types.**

System-wide voice dictation for Linux, powered by the speech-to-text already built into Claude Code.

[![CI](https://github.com/HemanthGangula/justyap/actions/workflows/ci.yml/badge.svg)](https://github.com/HemanthGangula/justyap/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Platform: Linux](https://img.shields.io/badge/platform-Linux-informational)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-informational)

![justyap demo: press your shortcut, speak, press Enter, and the text is pasted into a WhatsApp chat; with nowhere to type, it waits on the clipboard for Ctrl+V](docs/demo.gif)

</div>

Press a hotkey, speak, press Enter. justyap pastes what you said wherever your cursor is:
a chat, your editor, a browser form, the terminal.

- **No new account, no API key, no extra bill.** It uses your existing Claude Code sign-in.
  Claude Code's dictation doesn't count against your messages or tokens.
- **Every app.** Wayland and X11. With nowhere to type, the text waits on your clipboard.
- **Honest feedback.** An island at the bottom of the screen follows Claude Code's own
  microphone meter, so it only moves when your voice is actually being heard.
- **Small and dependency-free.** Under 1,000 lines of Python, standard library only.

## Requirements

- Linux. Tested on Ubuntu 24.04 with GNOME on Wayland. Other desktops work with a manually bound hotkey.
- [Claude Code](https://code.claude.com) 2.1.69 or newer, signed in with a paid Claude plan (Pro or Max).
  Dictation isn't available on the Free plan or with an API key.
- [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io).

## Install

```sh
git clone https://github.com/HemanthGangula/justyap
cd justyap
./install.sh
```

The installer asks for `sudo` once to install `wl-clipboard`, `xclip` and GTK with apt, and to add a
udev rule that lets your desktop session create a virtual keyboard (`/dev/uinput`). It then installs
the `justyap` command and starts a user service that runs with your desktop session.

## Usage

1. Press **Super+Alt+H** and speak. The island appears and follows your voice.
2. Press **Enter** to paste, or **Esc** to cancel. Pressing the hotkey again also pastes.

If nothing that accepts text has focus, the island shows **Copied to clipboard** instead, and
the text is ready for Ctrl+V.

Enter and Esc are only captured while you're dictating. Recording stops on its own after
15 seconds of silence or about 2 minutes; press Enter to paste what was captured. If anything
goes wrong, the island turns red and says why.

## Configuration

Run `setup` again with the option you want to change:

```sh
justyap setup --hotkey F9                  # a single key
justyap setup --hotkey '<Ctrl><Shift>h'    # any GNOME accelerator
justyap setup --paste-keys ctrl+shift+v    # for terminals
```

| Option | Default | Notes |
| --- | --- | --- |
| `--hotkey` | `<Super><Alt>h` | Any key or combination GNOME doesn't already use. `Pause`, `Insert` and `F9` are good single keys. `setup` warns about conflicts. |
| `--paste-keys` | `ctrl+v` | The keys that paste in your apps. Terminals usually need `ctrl+shift+v`. |

The dictation language follows Claude Code's `language` setting (`/config` in Claude Code).
On desktops other than GNOME, `setup` prints the command to bind in your keyboard settings.

## How it works

![How justyap works: the shortcut runs justyap toggle, which asks the background service over a Unix socket to hold Space in a hidden claude session; Claude Code sends the audio to Anthropic's speech-to-text, hands the transcript to justyap grab as its $EDITOR, and justyap pastes it into the focused app](docs/architecture.svg)

A user service keeps a real `claude` session running in a hidden pseudo-terminal, with `/voice`
enabled for that session only. The hotkey holds Space in it, which is Claude Code's push-to-talk.
On Enter, justyap releases Space and collects the transcript through Claude Code's own
"open in editor" hook (Ctrl+G with `$EDITOR` set to `justyap grab`), so nothing is scraped from the
screen and nothing is ever submitted as a prompt. The text is then pasted with the clipboard and a
short-lived virtual keyboard.

## FAQ

**Is this allowed?**
justyap runs the official `claude` program exactly as you would. It never reads your credentials,
never calls Anthropic's servers itself, and never sends a prompt. It's an independent project.

**Where does my audio go?**
Where it goes when you use `/voice` in Claude Code: to Anthropic's speech-to-text service, through
Claude Code. justyap never records or stores audio.

**How does it compare to Wispr Flow?**

| | justyap | Wispr Flow Pro |
| --- | --- | --- |
| Price | Included with Claude Pro or Max | $15/month, or $12/month billed yearly |
| Linux | Yes | Waitlist |
| macOS, Windows | No | Yes |
| Open source | Yes (MIT) | No |
| AI rewriting | No, plain transcription | Yes |

Wispr Flow details as of November 2026.

## Troubleshooting

Run `justyap toggle` in a terminal, speak, and run it again to see errors directly.
Logs: `journalctl --user -u justyap -f`.

| Problem | Fix |
| --- | --- |
| "The background service is not running" | Run `justyap setup`, then check the logs |
| "Voice mode requires a Claude.ai account" | Run `claude` and `/login` with your claude.ai account |
| "No access to /dev/uinput" | Re-run `./install.sh` |
| "No speech detected" | Check the input device in Settings → Sound |
| Pastes everywhere except the terminal | `justyap setup --paste-keys ctrl+shift+v` |
| No island appears | `sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0` |
| Enter and Esc don't paste or cancel | GNOME only; re-run `justyap setup` |

**After a Claude Code update.** justyap drives Claude Code's terminal interface, which isn't a
public API, so an update can break it. The island then says "Claude Code didn't start listening"
or "Claude Code did not return the transcript". Update with `git pull && ./install.sh`, and if
that doesn't help, [open an issue](https://github.com/HemanthGangula/justyap/issues) with your
`claude --version` and `journalctl --user -u justyap -n 50`. Last tested with Claude Code 2.1.316.

## Uninstall

```sh
justyap uninstall
uv tool uninstall justyap    # or: pipx uninstall justyap
sudo rm /etc/udev/rules.d/70-justyap-uinput.rules
```

## Limitations

- The island appears on the primary monitor.
- Text arrives when you stop speaking, not word by word.
- Pasting replaces your clipboard contents.

## Contributing

Issues and pull requests are welcome.

```sh
uv run python -m unittest discover -s tests -v
uvx ruff check && uvx ruff format --check
```

The tests run the real service against a fake `claude`, so they need no account or microphone.
To try a change live, stop the installed service with `systemctl --user stop justyap` and run
`uv run python -m justyap daemon` from your checkout.

## License

[MIT](LICENSE). justyap is not affiliated with or endorsed by Anthropic or Wispr.
