#!/bin/sh
# Install a staged tele-notify on this machine, for the account running the
# bot's systemd *user* service: the bot, TDLib (if staged), the terminal screen
# and the unit, with the checks, swaps, .prev copies and health-check rollback
# described in deploy/push-to-server.sh. The staged files come as a gzipped tar
# on standard input (made by deploy/push-to-server.sh, or a release package for
# deploy/self-update.sh):
#   sh deploy/install-staged.sh < package.tar.gz
set -eu
say() {
  printf "%s\n" "$*" || true
}
warn() {
  printf "tele-notify install: %s\n" "$*" >&2 || true
}
# the version this install brings (a release tag, or a commit for a push), kept
# once installed so deploy/self-update.sh knows what is here
record_version() {
  if [ -f "$d/version" ]; then
    cp "$d/version" "$lib/version.tmp" && mv -f "$lib/version.tmp" "$lib/version" || warn "could not record the version"
  fi
}
# exit 3: this package doesn't work on this machine (the new bot doesn't start,
# or doesn't stay up), so deploy/self-update.sh won't try it again; any other
# failure (another push running, pip or the network, systemctl) may pass next time
fail() {
  warn "$1"
  exit "${2:-1}"
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
"$d/telenotify" --help >/dev/null || fail "the new bot does not start on this server (glibc, libssl, libsqlite3?); nothing was changed" 3
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

# the nightly self-update (#123): its script and timer come with each install,
# and the timer is turned on the first time only, so one you turned off
# (systemctl --user disable --now tele-notify-update.timer) stays off. Not part
# of the bot's set, so a failure here only warns.
if [ -f "$d/self-update.sh" ]; then
  first_update=0
  [ -f "$units/tele-notify-update.timer" ] || first_update=1
  if cp "$d/self-update.sh" "$lib/.self-update.sh.tmp" && mv -f "$lib/.self-update.sh.tmp" "$lib/self-update.sh"; then
    for u in tele-notify-update.service tele-notify-update.timer; do
      if [ -f "$d/$u" ]; then
        cp "$d/$u" "$units/.$u.tmp" && mv -f "$units/.$u.tmp" "$units/$u" || warn "could not install $u"
      fi
    done
    if ! systemctl --user daemon-reload; then
      warn "could not reload the self-update's units"
      first_update=2
    elif [ "$first_update" = 1 ]; then
      if systemctl --user enable --now tele-notify-update.timer; then
        say "nightly self-update is on"
      else
        first_update=2
      fi
    fi
    # turning it on the first time failed: without the timer file the next
    # install tries again
    if [ "$first_update" = 2 ] && [ ! -e "$units/timers.target.wants/tele-notify-update.timer" ]; then
      rm -f "$units/tele-notify-update.timer"
      warn "could not turn the nightly self-update on; the next install tries again"
    fi
  else
    warn "could not install self-update.sh"
  fi
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
  record_version
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
# the staged files stay on the cleanup list: once moved into place they are
# gone from their temp names, and if a step fails, cleanup removes them. The
# new build is removed by the rollback (it is installed otherwise).
tmps="$d $new_bot $new_unit $new_td $new_link $prev_link $prev_bot $prev_td $prev_unit"

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
  if [ -n "$new_venv" ] && [ "$(readlink -f "$lib/tui-venv" || true)" != "$(readlink -f "$new_venv")" ]; then
    rm -rf "$new_venv" || warn "could not delete the failed screen build $new_venv"
  fi
  if [ -n "$legacy" ]; then
    rmdir "$legacy" 2>/dev/null || true
  fi
  systemctl --user daemon-reload || true
  if [ "$running" = 1 ]; then
    systemctl --user reset-failed tele-notify || true
    systemctl --user restart tele-notify || warn "restarting the previous bot failed too"
  fi
  fail "rolled back to the previous bot, TDLib, screen and unit; see journalctl --user -u tele-notify" "${2:-1}"
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
  record_version
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
  record_version
  exit 0
fi
rollback "the new bot did not stay up (state $state, restarts $before -> $after)" 3
