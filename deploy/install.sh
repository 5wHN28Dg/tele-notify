#!/bin/sh
# Install or update tele-notify for this user, no sudo. Run it from a checkout
# of the repository after pulling: it builds, installs and restarts the bot.
#
#   programs   ~/.local/bin/telenotify, ~/.local/bin/telenotify-tui
#   libraries  ~/.local/lib/tele-notify/ (TDLib, the terminal screen's Python)
#   config     ~/.config/tele-notify/config.json   (never overwritten)
#   data       ~/.local/share/tele-notify/          (state, Telegram login; never touched)
#   service    ~/.config/systemd/user/tele-notify.service
#
# TDLib: put libtdjson.so in lib/ first (deploy/build-tdlib.sh builds it).
set -eu

repo=$(cd "$(dirname "$0")/.." && pwd)
bin=$HOME/.local/bin
lib=$HOME/.local/lib/tele-notify
# The service always uses ~/.config (systemd doesn't see your shell's
# variables), so this script does too.
cfgdir=$HOME/.config/tele-notify
unitdir=$HOME/.config/systemd/user
for v in TELENOTIFY_CONFIG XDG_CONFIG_HOME; do
  if [ -n "$(printenv "$v" || true)" ]; then
    echo "note: \$$v is set; the service ignores it and uses $cfgdir/config.json," >&2
    echo "      so telenotify-tui in this shell may show another config than the bot's" >&2
  fi
done

mkdir -p "$bin" "$lib" "$cfgdir" "$unitdir"
chmod 700 "$cfgdir"

echo "building the bot..."
(cd "$repo" && nimble build -y -d:release)
install -m 755 "$repo/telenotify" "$bin/.telenotify.new"
mv -f "$bin/.telenotify.new" "$bin/telenotify"

if [ -f "$repo/lib/libtdjson.so" ]; then
  install -m 755 "$repo/lib/libtdjson.so" "$lib/.libtdjson.so.new"
  mv -f "$lib/.libtdjson.so.new" "$lib/libtdjson.so"
elif [ ! -f "$lib/libtdjson.so" ]; then
  echo "warning: no TDLib yet: run deploy/build-tdlib.sh, then this script again" >&2
fi

echo "installing the terminal screen..."
[ -x "$lib/tui-venv/bin/python" ] || python3 -m venv "$lib/tui-venv"
rm -rf "$repo/tui/build"
"$lib/tui-venv/bin/pip" install -q --upgrade "$repo/tui"
rm -rf "$repo/tui/build"
ln -sf "$lib/tui-venv/bin/telenotify-tui" "$bin/telenotify-tui"

# The service only changes when the config and TDLib it will use are there,
# so an update never restarts the bot into "config not found" or "no TDLib".
if [ -f "$cfgdir/config.json" ] && { [ -f "$lib/libtdjson.so" ] || grep -q '"tdlib_path" *: *"[^"]' "$cfgdir/config.json"; }; then
  install -m 644 "$repo/deploy/tele-notify.service" "$unitdir/tele-notify.service"
  systemctl --user daemon-reload
  if systemctl --user is-active -q tele-notify; then
    systemctl --user restart tele-notify
    echo "restarted the bot"
  fi
else
  [ -f "$cfgdir/config.json" ] || echo "next: put your config.json in $cfgdir/ (chmod 600) and run this script again;" >&2
  [ -f "$cfgdir/config.json" ] && echo "next: no TDLib: run deploy/build-tdlib.sh, then this script again;" >&2
  echo "      the service was left as it is. A new setup then needs 'telenotify login'" >&2
  echo "      and 'systemctl --user enable --now tele-notify'." >&2
fi

case ":$PATH:" in
  *":$bin:"*) echo "done: run 'telenotify-tui' to open the terminal screen" ;;
  *) echo "done; $bin isn't on your PATH: add it (or run $bin/telenotify-tui)" ;;
esac
