#!/bin/sh
# Stage what deploy/install-staged.sh installs, from a checkout: the built bot
# (./telenotify), the terminal screen's source at the last commit, the unit, the
# self-update script and its timer, the installer itself and the version. Used
# by deploy/push-to-server.sh and by the release workflow, so a push and a
# release install the same things. TDLib is not staged here (push-to-server.sh
# adds it; a release leaves the server's).
#
#   deploy/make-package.sh STAGE_DIR [VERSION]
#
# VERSION defaults to `git describe --tags --always` (a release passes its tag).
set -eu

if [ $# -lt 1 ]; then
  echo "usage: deploy/make-package.sh STAGE_DIR [VERSION]" >&2
  exit 2
fi
stage=$1
repo=$(cd "$(dirname "$0")/.." && pwd)
version=${2:-$(git -C "$repo" describe --tags --always)}

mkdir -p "$stage"
git -C "$repo" archive -o "$stage/tui.tar" HEAD tui
tar -xf "$stage/tui.tar" -C "$stage"
rm "$stage/tui.tar"
git -C "$repo" rev-parse HEAD:tui > "$stage/tui-tree"
for f in tele-notify.service tele-notify-update.service tele-notify-update.timer; do
  cp "$repo/deploy/$f" "$stage/"
done
cp "$repo/deploy/install-staged.sh" "$repo/deploy/self-update.sh" "$stage/"
printf '%s\n' "$version" > "$stage/version"
