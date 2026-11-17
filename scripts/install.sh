#!/bin/sh
# Sets up the laptop side for the current user. Needs no root: it only
# checks the system packages and names the missing ones.
#   - checks programs and libraries (huawai doctor)
#   - creates ~/.config/huawai-companion/hostd.toml if it does not exist
#   - installs and starts the user service huawai-hostd
#   - links the huawai command into ~/.local/bin
# Usage: scripts/install.sh
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
BIN_DIR="$HOME/.local/bin"

echo "== checking this laptop"
if ! python3 "$ROOT/host/cli.py" doctor; then
    echo "install: install the missing parts first (lines marked FEHLT), then run again" >&2
    exit 1
fi

echo "== settings"
python3 - "$ROOT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from host import config
if config.path().exists():
    config.load()   # refuses a broken file
    print(f"{config.path()} exists, left as it is")
else:
    config.save(config.DEFAULTS)
    print(f"{config.path()} created (Wi-Fi address is filled in by 'huawai up')")
PY

echo "== service"
mkdir -p "$UNIT_DIR"
sed "s|^WorkingDirectory=.*|WorkingDirectory=$ROOT|" "$ROOT/scripts/huawai-hostd.service" \
    > "$UNIT_DIR/huawai-hostd.service"
systemctl --user daemon-reload
systemctl --user enable huawai-hostd >/dev/null 2>&1
systemctl --user restart huawai-hostd
echo "huawai-hostd: $(systemctl --user is-active huawai-hostd), starts at every login"

echo "== command"
mkdir -p "$BIN_DIR"
ln -sf "$ROOT/scripts/huawai" "$BIN_DIR/huawai"
case ":$PATH:" in
    *":$BIN_DIR:"*) echo "huawai is available as a command" ;;
    *) echo "huawai linked to $BIN_DIR; that folder is not in PATH, call $BIN_DIR/huawai" ;;
esac

echo "== next steps"
echo "  1. tablet on USB, Android running:   huawai up     (pairs and sets the firewall on first use)"
echo "  2. check:                            huawai status"
echo "  3. back to Android:                  huawai down"
