#!/bin/sh
# Install the newest tele-notify release if it isn't the one installed (#123).
# Run every night by tele-notify-update.timer, as the account running the bot.
#
# It asks GitHub for the latest release, downloads its package
# (tele-notify-<tag>.tar.gz) and checksum (.sha256), checks the checksum, and
# runs the package's own install-staged.sh, which checks the new bot on this
# machine, swaps it in, keeps the previous set, restarts the bot and rolls back
# if it doesn't stay up. TDLib is left as it is. A failure is logged, and also
# shown as an alert in the terminal screen and through your notification bot
# (`telenotify alert`).
#
# Needs curl, python3, sha256sum and tar.
set -eu

repo_slug=${TN_REPO:-5wHN28Dg/tele-notify}
api=${TN_API:-https://api.github.com}
lib=$HOME/.local/lib/tele-notify
bin=$HOME/.local/bin

say() {
  printf '%s\n' "$*" || true
}
fail() {
  printf 'self-update: %s\n' "$*" >&2 || true
  if [ -x "$bin/telenotify" ]; then
    "$bin/telenotify" alert "Self-update: $*" >/dev/null 2>&1 || true
  fi
  exit 1
}

work=$(mktemp -d "${TMPDIR:-/tmp}/tn-update-XXXXXX")
trap 'rm -rf "$work"' EXIT
trap 'exit 130' INT TERM HUP

curl -fsSL --max-time 30 -H "Accept: application/vnd.github+json" \
  -o "$work/release.json" "$api/repos/$repo_slug/releases/latest" ||
  fail "could not ask GitHub for the latest release; will try again tomorrow"

installed=$(cat "$lib/version" 2>/dev/null || true)

# The release's tag, package and checksum addresses, and whether it is newer
# than what's installed. A push from a checkout records `git describe`
# (v4.1.0-3-gabc1234: three commits after v4.1.0), so only a later tag than
# the one it's based on is newer: a push isn't replaced by an older release.
python3 - "$work/release.json" "$installed" > "$work/release.txt" <<'EOF' || fail "GitHub's answer about the latest release isn't readable"
import json, re, sys
r = json.load(open(sys.argv[1]))
tag = r.get("tag_name") or ""
if not re.fullmatch(r"v[0-9]+(\.[0-9]+)*", tag):
    sys.exit("no release tag")
urls = {a.get("name"): a.get("browser_download_url") or "" for a in r.get("assets") or []}
pkg = urls.get(f"tele-notify-{tag}.tar.gz", "")
sha = urls.get(f"tele-notify-{tag}.tar.gz.sha256", "")
if not (pkg.startswith("https://") and sha.startswith("https://")):
    sys.exit("the release has no package")
def parts(t):
    return tuple(int(x) for x in t[1:].split("."))
m = re.fullmatch(r"(v[0-9]+(?:\.[0-9]+)*)(-[0-9]+-g[0-9a-f]+)?(-dirty)?", sys.argv[2].strip())
# nothing installed yet, or a build from before the first release: install
newer = m is None or parts(tag) > parts(m.group(1))
print(tag)
print(pkg)
print(sha)
print("newer" if newer else "not newer")
EOF
tag=$(sed -n 1p "$work/release.txt")
pkg_url=$(sed -n 2p "$work/release.txt")
sha_url=$(sed -n 3p "$work/release.txt")

if [ "$(sed -n 4p "$work/release.txt")" != newer ]; then
  say "up to date (installed: $installed; latest release: $tag)"
  exit 0
fi

curl -fsSL --max-time 300 -o "$work/package.tar.gz" "$pkg_url" ||
  fail "could not download $tag; will try again tomorrow"
curl -fsSL --max-time 30 -o "$work/package.sha256" "$sha_url" ||
  fail "could not download $tag's checksum; will try again tomorrow"
expected=$(cut -d ' ' -f 1 "$work/package.sha256")
actual=$(sha256sum "$work/package.tar.gz" | cut -d ' ' -f 1)
if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
  fail "the $tag package doesn't match its checksum; nothing was installed"
fi
tar -xzOf "$work/package.tar.gz" ./install-staged.sh > "$work/install-staged.sh" ||
  fail "the $tag package has no installer; nothing was installed"

say "installing $tag (installed: ${installed:-unknown})"
if sh "$work/install-staged.sh" < "$work/package.tar.gz"; then
  say "installed $tag"
else
  fail "installing $tag failed, so the previous version is kept (journalctl --user -u tele-notify-update)"
fi
