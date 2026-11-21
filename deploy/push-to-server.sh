#!/bin/sh
# Update tele-notify on a server over ssh: build here, send, check on the server
# that the new bot runs there, then install as the service user and restart the
# bot if it runs. Run it from a clean checkout after pulling.
#
#   deploy/push-to-server.sh [--allow-dirty] USER@HOST [ssh options...]
#   e.g. deploy/push-to-server.sh telenotify@my.server -i ~/.ssh/key -o IdentitiesOnly=yes
#
# It refuses uncommitted changes in src/, tui/, deploy/ or the build files (the
# bot is built from the working tree, the screen from the last commit, so they
# would not match); --allow-dirty pushes anyway.
#
# USER is the account the bot's systemd *user* service runs as (lingering on),
# with the layout deploy/install.sh makes: ~/.local/bin/telenotify,
# ~/.local/lib/tele-notify/ (TDLib, the screen's Python), ~/.config/systemd/user/.
# Its config and data (~/.config/tele-notify, ~/.local/share/tele-notify) and the
# service's drop-ins (~/.config/systemd/user/tele-notify.service.d/) are never
# touched.
#
# On the server, everything that can fail (does the new bot start there, does
# TDLib find its libraries, building the screen's Python) happens before
# anything installed changes; then the bot, TDLib, the screen and the unit are
# swapped together. The screen lives in ~/.local/lib/tele-notify/tui-venv.build-*
# directories, tui-venv being a symlink to the current one. What was installed
# before is kept beside each as *.prev (a re-run that changes nothing keeps
# them). A running bot is restarted, and if the restart fails or the bot isn't
# still up 10 s later, the *.prev set is put back and restarted; once the swap
# has begun, this finishes even if the connection drops. To roll back by hand,
# on the server (any login shell):
#   sh -c 'cd ~ && for f in .local/bin/telenotify .local/lib/tele-notify/libtdjson.so .local/lib/tele-notify/tui-venv .config/systemd/user/tele-notify.service; do if [ -e $f.prev ] || [ -L $f.prev ]; then rm -f $f; mv -T $f.prev $f; fi; done; systemctl --user daemon-reload; systemctl --user restart tele-notify'
#
# The server needs python3 with venv, flock and GNU coreutils (mv -T). TDLib
# comes from lib/libtdjson.so, else this machine's installed copy. The bot and
# TDLib need glibc, libssl and libsqlite3 on the server, no older than the ones
# they were built against (compare `ldd --version` on both; install libssl and
# libsqlite3 there). The check on the server stops the push before any change
# if not.
set -eu

usage="usage: deploy/push-to-server.sh [--allow-dirty] USER@HOST [ssh options...]"
allow_dirty=0
if [ "${1:-}" = --allow-dirty ]; then
  allow_dirty=1
  shift
fi
if [ $# -lt 1 ]; then
  echo "$usage" >&2
  exit 2
fi
target=$1
shift

repo=$(cd "$(dirname "$0")/.." && pwd)

dirty=$(git -C "$repo" status --porcelain -- src tui deploy '*.nimble' config.nims)
if [ -n "$dirty" ]; then
  if [ "$allow_dirty" = 0 ]; then
    echo "refusing to push uncommitted changes (commit them, or pass --allow-dirty):" >&2
    printf '%s\n' "$dirty" >&2
    exit 1
  fi
  echo "warning: pushing uncommitted changes; the screen comes from the last commit" >&2
fi
echo "deploying $(git -C "$repo" log -1 --format='%h %s')"

stage=$(mktemp -d "${TMPDIR:-/tmp}/tn-push-XXXXXX")
trap 'rm -rf "$stage"' EXIT
trap 'exit 130' INT TERM HUP

# Test hook, only the tests set it: TELENOTIFY_PREBUILT names a directory with a
# ready telenotify (and optionally libtdjson.so), and the build is skipped.
if [ -n "${TELENOTIFY_PREBUILT:-}" ]; then
  install -m 755 "$TELENOTIFY_PREBUILT/telenotify" "$stage/telenotify"
  if [ -f "$TELENOTIFY_PREBUILT/libtdjson.so" ]; then
    cp "$TELENOTIFY_PREBUILT/libtdjson.so" "$stage/"
  fi
else
  echo "building the bot..."
  (cd "$repo" && nimble build -y -d:release)
  install -m 755 "$repo/telenotify" "$stage/telenotify"
  if [ -f "$repo/lib/libtdjson.so" ]; then
    cp "$repo/lib/libtdjson.so" "$stage/"
  elif [ -f "$HOME/.local/lib/tele-notify/libtdjson.so" ]; then
    cp "$HOME/.local/lib/tele-notify/libtdjson.so" "$stage/"
  else
    echo "note: no TDLib here; the server keeps the one it has" >&2
  fi
fi
sh "$repo/deploy/make-package.sh" "$stage"

# The server runs deploy/install-staged.sh (its variables are expanded there). It
# is sent base64-encoded and run by sh, so the account's login shell doesn't matter.

script=$(base64 < "$repo/deploy/install-staged.sh" | tr -d '\n')
echo "sending to $target..."
tar -C "$stage" -czf - . | ssh "$@" "$target" "sh -c 'eval \"\$(echo $script | base64 -d)\"'"
echo "done"
