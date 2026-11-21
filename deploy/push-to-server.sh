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
# The server needs python3 with venv, and flock. TDLib comes from
# lib/libtdjson.so, else this machine's installed copy. The bot and TDLib need
# glibc, libssl and libsqlite3 on the server, no older than the ones they were
# built against (compare `ldd --version` on both; install libssl and libsqlite3
# there). The check on the server stops the push before any change if not.
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
git -C "$repo" archive -o "$stage/tui.tar" HEAD tui
tar -xf "$stage/tui.tar" -C "$stage"
rm "$stage/tui.tar"
git -C "$repo" rev-parse HEAD:tui > "$stage/tui-tree"
cp "$repo/deploy/tele-notify.service" "$stage/"

# The server runs this (its variables are expanded there). It is sent
# base64-encoded and run by sh, so the account's login shell doesn't matter.
remote='set -eu
say() {
  printf "%s\n" "$*" || true
}
warn() {
  printf "push-to-server: %s\n" "$*" >&2 || true
}
fail() {
  warn "$*"
  exit 1
}
if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
  XDG_RUNTIME_DIR=/run/user/$(id -u)
  export XDG_RUNTIME_DIR
fi
bin=$HOME/.local/bin
lib=$HOME/.local/lib/tele-notify
units=$HOME/.config/systemd/user
mkdir -p "$bin" "$lib" "$units"

command -v flock >/dev/null || fail "flock is missing on this server (it is part of util-linux); nothing was changed"
exec 9>"$lib/.push.lock"
flock -n 9 || fail "another push to this account is running"

# what is staged but not installed; removed on any exit
tmps=""
cleanup() {
  for t in $tmps; do
    rm -rf "$t"
  done
}
trap cleanup EXIT
trap "exit 130" INT TERM HUP PIPE
d=$(mktemp -d "$HOME/tn-push-XXXXXX")
tmps=$d
tar -xzf - -C "$d"

# 1. everything that can fail; nothing installed changes here
"$d/telenotify" --help >/dev/null || fail "the new bot does not start on this server (glibc, libssl, libsqlite3?); nothing was changed"
if [ -f "$d/libtdjson.so" ]; then
  libs=$(ldd "$d/libtdjson.so") || fail "ldd cannot read the new TDLib; nothing was changed"
  case $libs in
    *"not found"*)
      printf "%s\n" "$libs" | grep "not found" >&2
      fail "the new TDLib misses libraries on this server; nothing was changed" ;;
  esac
fi

new_bot=$(mktemp "$bin/.telenotify.XXXXXX")
tmps="$tmps $new_bot"
install -m 755 "$d/telenotify" "$new_bot"
new_unit=$(mktemp "$units/.tele-notify.service.XXXXXX")
tmps="$tmps $new_unit"
install -m 644 "$d/tele-notify.service" "$new_unit"
new_td=""
if [ -f "$d/libtdjson.so" ]; then
  new_td=$(mktemp "$lib/.libtdjson.so.XXXXXX")
  tmps="$tmps $new_td"
  install -m 755 "$d/libtdjson.so" "$new_td"
fi

# The screen: each build has its own tui-venv.build-XXXXXX (a venv cannot be
# moved) and tui-venv is a symlink to the current one. Rebuilt only when tui/
# changed.
tree=$(cat "$d/tui-tree")
installed_tree=""
if [ -L "$lib/tui-venv" ] && [ -f "$lib/tui-venv/.tui-tree" ]; then
  installed_tree=$(cat "$lib/tui-venv/.tui-tree")
fi
new_venv=""
if [ "$installed_tree" != "$tree" ]; then
  say "building the screen..."
  new_venv=$(mktemp -d "$lib/tui-venv.build-XXXXXX")
  tmps="$tmps $new_venv"
  chmod 755 "$new_venv"
  python3 -m venv "$new_venv"
  "$new_venv/bin/pip" install -q "$d/tui"
  printf "%s\n" "$tree" > "$new_venv/.tui-tree"
fi

changed=0
if [ -n "$new_venv" ]; then
  changed=1
fi
if ! cmp -s "$new_bot" "$bin/telenotify"; then
  changed=1
fi
if [ -n "$new_td" ] && ! cmp -s "$new_td" "$lib/libtdjson.so"; then
  changed=1
fi
if ! cmp -s "$new_unit" "$units/tele-notify.service"; then
  changed=1
fi
if [ "$changed" = 0 ]; then
  say "already up to date: nothing changed, the bot was not restarted"
  exit 0
fi

# 2. copies of all of what is installed now, under temp names (so a full disk
# keeps the old *.prev set), and the new symlinks, made before anything moves
staged=""
copy_prev() {
  staged=""
  if [ -e "$1" ]; then
    staged=$(mktemp "$(dirname "$1")/.$(basename "$1").prev.XXXXXX")
    tmps="$tmps $staged"
    cp -p "$1" "$staged"
  fi
}
copy_prev "$bin/telenotify"
prev_bot=$staged
copy_prev "$lib/libtdjson.so"
prev_td=$staged
copy_prev "$units/tele-notify.service"
prev_unit=$staged
legacy=""
prev_link=""
if [ -L "$lib/tui-venv" ]; then
  prev_link=$lib/.tui-venv.prev.$$.lnk
  tmps="$tmps $prev_link"
  ln -s "$(readlink "$lib/tui-venv")" "$prev_link"
elif [ -d "$lib/tui-venv" ]; then
  # made by deploy/install.sh: a real directory (always rebuilt above). It is
  # moved to a build directory; its programs still work through the symlink.
  legacy=$(mktemp -d "$lib/tui-venv.build-XXXXXX")
  tmps="$tmps $legacy"
  prev_link=$lib/.tui-venv.prev.$$.lnk
  tmps="$tmps $prev_link"
  ln -s "$(basename "$legacy")" "$prev_link"
fi
new_link=""
if [ -n "$new_venv" ]; then
  new_link=$lib/.tui-venv.$$.lnk
  tmps="$tmps $new_link"
  ln -s "$(basename "$new_venv")" "$new_link"
fi
state=$(systemctl --user is-active tele-notify || true)
running=0
case $state in
  active|activating|reloading) running=1 ;;
esac

# From here on, finish whatever happens to the connection: swap, restart,
# check, and roll back if needed.
trap "" HUP PIPE INT TERM
tmps=$d

restore() {
  if [ -e "$1.prev" ] || [ -L "$1.prev" ]; then
    mv -fT "$1.prev" "$1" || warn "could not restore $1"
  else
    rm -f "$1" || warn "could not remove $1 (there was none before)"
  fi
}
rollback() {
  warn "$1; rolling back..."
  restore "$bin/telenotify"
  restore "$lib/libtdjson.so"
  restore "$units/tele-notify.service"
  if [ -L "$lib/tui-venv" ] || [ ! -e "$lib/tui-venv" ]; then
    restore "$lib/tui-venv"
  fi
  ln -sf "$lib/tui-venv/bin/telenotify-tui" "$bin/telenotify-tui" || true
  systemctl --user daemon-reload || true
  if [ "$running" = 1 ]; then
    systemctl --user reset-failed tele-notify || true
    systemctl --user restart tele-notify || warn "restarting the previous bot failed too"
  fi
  fail "rolled back to the previous bot, TDLib, screen and unit; see journalctl --user -u tele-notify"
}
put_prev() {
  if [ -n "$1" ]; then
    mv -f "$1" "$2.prev"
  else
    rm -f "$2.prev"
  fi
}

# 3. the previous set in place, then the swap
put_prev "$prev_bot" "$bin/telenotify"
put_prev "$prev_td" "$lib/libtdjson.so"
put_prev "$prev_unit" "$units/tele-notify.service"
if [ -d "$lib/tui-venv.prev" ] && [ ! -L "$lib/tui-venv.prev" ]; then
  rm -rf "$lib/tui-venv.prev"
fi
if [ -n "$prev_link" ]; then
  mv -fT "$prev_link" "$lib/tui-venv.prev"
else
  rm -f "$lib/tui-venv.prev"
fi

mv -f "$new_bot" "$bin/telenotify" || rollback "installing the bot failed"
if [ -n "$new_td" ]; then
  mv -f "$new_td" "$lib/libtdjson.so" || rollback "installing TDLib failed"
fi
mv -f "$new_unit" "$units/tele-notify.service" || rollback "installing the unit failed"
if [ -n "$new_venv" ]; then
  if [ -n "$legacy" ]; then
    mv -T "$lib/tui-venv" "$legacy" || rollback "moving the old screen failed"
  fi
  mv -fT "$new_link" "$lib/tui-venv" || rollback "installing the screen failed"
fi
ln -sf "$lib/tui-venv/bin/telenotify-tui" "$bin/telenotify-tui" || rollback "linking telenotify-tui failed"
# old screen builds: delete those neither tui-venv nor tui-venv.prev uses
current=$(readlink -f "$lib/tui-venv" || true)
previous=$(readlink -f "$lib/tui-venv.prev" || true)
for v in "$lib"/tui-venv.build-*; do
  real=$(readlink -f "$v" || true)
  if [ -d "$v" ] && [ ! -L "$v" ] && [ "$real" != "$current" ] && [ "$real" != "$previous" ]; then
    rm -rf "$v" || warn "could not delete the old screen build $v"
  fi
done
say "installed"

# 4. restart a running bot and check that it stays up
systemctl --user daemon-reload || rollback "systemctl --user daemon-reload failed"
if [ "$running" = 0 ]; then
  say "the bot is not running: start it with systemctl --user start tele-notify"
  exit 0
fi
systemctl --user restart tele-notify || rollback "restarting the bot failed"
before=$(systemctl --user show -p NRestarts --value tele-notify || true)
say "restarted the bot; checking that it stays up..."
sleep 10 || true
after=$(systemctl --user show -p NRestarts --value tele-notify || true)
state=$(systemctl --user is-active tele-notify || true)
if [ "$state" = active ] && [ "$after" = "$before" ]; then
  say "the bot is running"
  exit 0
fi
rollback "the new bot did not stay up (state $state, restarts $before -> $after)"'

script=$(printf '%s' "$remote" | base64 | tr -d '\n')
echo "sending to $target..."
tar -C "$stage" -czf - . | ssh "$@" "$target" "sh -c 'eval \"\$(echo $script | base64 -d)\"'"
echo "done"
