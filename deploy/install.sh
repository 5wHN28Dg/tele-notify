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
cfgdir=${XDG_CONFIG_HOME:-$HOME/.config}/tele-notify
unitdir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user

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
"$lib/tui-venv/bin/pip" install -q --upgrade "$repo/tui"
ln -sf "$lib/tui-venv/bin/telenotify-tui" "$bin/telenotify-tui"

install -m 644 "$repo/deploy/tele-notify.service" "$unitdir/tele-notify.service"
systemctl --user daemon-reload
if systemctl --user is-active -q tele-notify; then
  systemctl --user restart tele-notify
  echo "restarted the bot"
fi

if [ ! -f "$cfgdir/config.json" ]; then
  echo "next: put your config.json in $cfgdir/ (chmod 600), run 'telenotify login',"
  echo "then: systemctl --user enable --now tele-notify"
fi
echo "done: run 'telenotify-tui' to open the terminal screen"
