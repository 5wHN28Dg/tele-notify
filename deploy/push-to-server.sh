#!/bin/sh
# Update tele-notify on a server over ssh: build here, send, install as the
# service user, restart the bot if it runs. Run it from a checkout after pulling.
#
#   deploy/push-to-server.sh USER@HOST [ssh options...]
#   e.g. deploy/push-to-server.sh telenotify@my.server -i ~/.ssh/key -o IdentitiesOnly=yes
#
# USER is the account the bot's systemd *user* service runs as (lingering on),
# with the layout deploy/install.sh makes: ~/.local/bin/telenotify,
# ~/.local/lib/tele-notify/ (TDLib, the screen's Python), ~/.config/systemd/user/.
# Its config and data (~/.config/tele-notify, ~/.local/share/tele-notify) are
# never touched. The previous bot is kept as ~/.local/bin/telenotify.prev:
#   mv ~/.local/bin/telenotify.prev ~/.local/bin/telenotify && systemctl --user restart tele-notify
# Drop-ins (~/.config/systemd/user/tele-notify.service.d/) are kept.
#
# The server needs python3 with venv. TDLib comes from lib/libtdjson.so, else
# this machine's installed copy; the bot's binary needs a glibc no newer than
# the server's (check with `ldd --version` on both).
set -eu

if [ $# -lt 1 ]; then
  echo "usage: deploy/push-to-server.sh USER@HOST [ssh options...]" >&2
  exit 2
fi
target=$1
shift

repo=$(cd "$(dirname "$0")/.." && pwd)
stage=$(mktemp -d "${TMPDIR:-/tmp}/tn-push-XXXXXX")
trap 'rm -rf "$stage"' EXIT

if [ -n "${TELENOTIFY_PREBUILT:-}" ]; then       # tests: a bot built elsewhere
  cp "$TELENOTIFY_PREBUILT/telenotify" "$stage/telenotify"
  [ ! -f "$TELENOTIFY_PREBUILT/libtdjson.so" ] || cp "$TELENOTIFY_PREBUILT/libtdjson.so" "$stage/"
else
  echo "building the bot..."
  (cd "$repo" && nimble build -y -d:release)
  cp "$repo/telenotify" "$stage/telenotify"
  if [ -f "$repo/lib/libtdjson.so" ]; then
    cp "$repo/lib/libtdjson.so" "$stage/"
  elif [ -f "$HOME/.local/lib/tele-notify/libtdjson.so" ]; then
    cp "$HOME/.local/lib/tele-notify/libtdjson.so" "$stage/"
  else
    echo "note: no TDLib here; the server keeps the one it has" >&2
  fi
fi
(cd "$repo" && git archive HEAD tui) | tar -x -C "$stage"
cp "$repo/deploy/tele-notify.service" "$stage/"

# the server runs this (its variables are expanded there)
remote='set -eu
d=$(mktemp -d "$HOME/tn-push-XXXXXX")
trap "rm -rf \"$d\"" EXIT
tar -xzf - -C "$d"
bin=$HOME/.local/bin
lib=$HOME/.local/lib/tele-notify
units=$HOME/.config/systemd/user
mkdir -p "$bin" "$lib" "$units"
if [ -f "$bin/telenotify" ]; then cp -p "$bin/telenotify" "$bin/telenotify.prev"; fi
install -m 755 "$d/telenotify" "$bin/.telenotify.new" && mv -f "$bin/.telenotify.new" "$bin/telenotify"
if [ -f "$d/libtdjson.so" ]; then
  install -m 755 "$d/libtdjson.so" "$lib/.libtdjson.so.new" && mv -f "$lib/.libtdjson.so.new" "$lib/libtdjson.so"
fi
[ -x "$lib/tui-venv/bin/python" ] || python3 -m venv "$lib/tui-venv"
"$lib/tui-venv/bin/pip" install -q --upgrade "$d/tui"
ln -sf "$lib/tui-venv/bin/telenotify-tui" "$bin/telenotify-tui"
install -m 644 "$d/tele-notify.service" "$units/tele-notify.service"
systemctl --user daemon-reload
if systemctl --user is-active -q tele-notify; then
  systemctl --user restart tele-notify
  echo "restarted the bot"
else
  echo "the bot is not running: start it with systemctl --user start tele-notify"
fi'

echo "sending to $target..."
tar -C "$stage" -czf - . | ssh "$@" "$target" "$remote"
echo "done"
