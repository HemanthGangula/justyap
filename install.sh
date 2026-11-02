#!/bin/sh
# Installs justyap for the current user. Safe to re-run.
set -eu

DIR=$(cd "$(dirname "$0")" && pwd)
RULE=/etc/udev/rules.d/70-justyap-uinput.rules

if command -v apt-get >/dev/null; then
    sudo apt-get install -y wl-clipboard xclip python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-atspi-2.0
else
    echo "Install wl-clipboard (Wayland) or xclip (X11), and PyGObject with GTK 3, with your package manager."
fi

# Let the logged-in desktop user create a virtual keyboard (no group change, no re-login).
echo 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' | sudo tee "$RULE" >/dev/null
sudo udevadm control --reload-rules
sudo udevadm trigger --sysname-match=uinput

if command -v uv >/dev/null; then
    uv tool install --force --reinstall "$DIR"
elif command -v pipx >/dev/null; then
    pipx install --force "$DIR"
else
    echo "Install uv (https://docs.astral.sh/uv/) or pipx, then re-run this script." >&2
    exit 1
fi

"$(command -v justyap || echo "$HOME/.local/bin/justyap")" setup "$@"
