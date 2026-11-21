#!/bin/sh
# Optional: install kittygram on this server, so tele-notify can read the
# Instagram posts linked in job ads (#114). Instagram shows posts only to
# logged-in visitors; kittygram (codeberg.org/irelephant/kittygram, AGPL), an
# open-source Instagram front end, reads them anonymously, and the bot asks it
# on 127.0.0.1 only.
#
#   sudo sh deploy/install-kittygram.sh [PORT]      (PORT: 8430 if not given)
#
# Then put "instagram_frontend": "http://127.0.0.1:PORT" in the bot's
# config.json and restart the bot. Without that line the bot doesn't use it.
#
# For a Debian or Ubuntu server, run as root: it installs packages (a compiler,
# lua5.1, valkey for kittygram's cache, git), adds a system user `kittygram`
# whose home /opt/kittygram holds everything else, builds OpenResty and
# LuaRocks there from checked downloads, fetches kittygram at a fixed commit,
# and installs the system service kittygram.service (local only, at most
# 300 MB of memory). Running it again checks everything and rebuilds only what
# is missing or out of date; kittygram's settings (config.lua) and its secret
# are kept, apart from the lines below.
#
# kittygram's settings changed from its example: the port, one worker, JSON
# answers on (the bot reads those), a random secret, and nginx listening on
# 127.0.0.1 only.
#
# To remove it: systemctl disable --now kittygram; rm /etc/systemd/system/kittygram.service;
# userdel -r kittygram (deletes /opt/kittygram); apt-get remove valkey-server if
# nothing else uses it.
set -eu

port=${1:-8430}
home=${KG_HOME:-/opt/kittygram}
units=${KG_UNITS:-/etc/systemd/system}

kittygram_repo=https://codeberg.org/irelephant/kittygram.git
kittygram_commit=c0f0ec5620f3f74eaa95059fc2a7f43d6c233079    # 2026-11-08, tested with the bot
openresty_version=1.27.1.2
openresty_sha256=74f076f7e364b2a99a6c5f9bb531c27610c78985abe956b442b192a2295f7548
luarocks_version=3.11.1
luarocks_sha256=c3fb3d960dffb2b2fe9de7e3cb004dc4d0b34bb3d342578af84f84325c669102

say() {
  printf '%s\n' "$*"
}
fail() {
  printf 'install-kittygram: %s\n' "$*" >&2
  exit 1
}

case $port in
  '' | *[!0-9]*) fail "the port must be a number, like 8430" ;;
esac
[ "$port" -ge 1024 ] && [ "$port" -le 65535 ] || fail "the port must be between 1024 and 65535"
[ "$(id -u)" = 0 ] || fail "run it as root (sudo): it installs packages, a user and a system service"
command -v apt-get > /dev/null || fail "this script is for Debian or Ubuntu (it uses apt-get)"

# run as the kittygram user, with its tools first on PATH
as_kg() {
  runuser -u kittygram -- env HOME="$home" \
    PATH="$home/openresty/bin:$home/openresty/nginx/sbin:$home/luarocks51/bin:/usr/local/bin:/usr/bin:/bin" "$@"
}

# download $1 to $2 (as kittygram) and check it against $3
fetch_checked() {
  as_kg curl -fsSL --max-time 300 -o "$2" "$1" || fail "could not download $1"
  if ! printf '%s  %s\n' "$3" "$2" | sha256sum -c - > /dev/null 2>&1; then
    rm -f "$2"
    fail "$1 doesn't match its expected checksum; nothing was built from it"
  fi
}

say "installing packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -q > /dev/null || fail "apt-get update failed"
apt-get install -y -q --no-install-recommends build-essential libpcre2-dev libssl-dev zlib1g-dev \
  libsqlite3-dev git git-lfs valkey-server unzip perl ca-certificates curl lua5.1 liblua5.1-0-dev \
  > /dev/null || fail "installing packages failed"
systemctl enable --now valkey-server > /dev/null 2>&1 || fail "could not start valkey-server"

if ! id kittygram > /dev/null 2>&1; then
  useradd --system --create-home --home-dir "$home" --shell /usr/sbin/nologin kittygram ||
    fail "could not add the kittygram user"
fi
chmod 700 "$home"
as_kg mkdir -p "$home/src"

# OpenResty (nginx with LuaJIT), the web server kittygram runs in
if ! "$home/openresty/bin/openresty" -v 2>&1 | grep -qF "openresty/$openresty_version"; then
  say "building OpenResty $openresty_version (a few minutes)..."
  tarball=$home/src/openresty-$openresty_version.tar.gz
  fetch_checked "https://openresty.org/download/openresty-$openresty_version.tar.gz" "$tarball" "$openresty_sha256"
  as_kg sh -c "cd '$home/src' && rm -rf 'openresty-$openresty_version' && tar xzf '$tarball' &&
    cd 'openresty-$openresty_version' && ./configure --prefix='$home/openresty' -j2 > ../openresty-build.log 2>&1 &&
    nice make -j2 >> ../openresty-build.log 2>&1 && make install >> ../openresty-build.log 2>&1" ||
    fail "building OpenResty failed; see $home/src/openresty-build.log"
fi

# LuaRocks for Lua 5.1, which installs kittygram's Lua modules
if ! "$home/luarocks51/bin/luarocks" --version 2> /dev/null | grep -qF "luarocks $luarocks_version"; then
  say "building LuaRocks $luarocks_version..."
  tarball=$home/src/luarocks-$luarocks_version.tar.gz
  fetch_checked "https://luarocks.org/releases/luarocks-$luarocks_version.tar.gz" "$tarball" "$luarocks_sha256"
  as_kg sh -c "cd '$home/src' && rm -rf 'luarocks-$luarocks_version' && tar xzf '$tarball' &&
    cd 'luarocks-$luarocks_version' && ./configure --prefix='$home/luarocks51' --lua-version=5.1 \
      --with-lua-bin=/usr/bin --with-lua-interpreter=lua5.1 --with-lua-include=/usr/include/lua5.1 \
      > ../luarocks-build.log 2>&1 &&
    make >> ../luarocks-build.log 2>&1 && make install >> ../luarocks-build.log 2>&1" ||
    fail "building LuaRocks failed; see $home/src/luarocks-build.log"
fi

# kittygram itself, at the tested commit
app=$home/app
if [ ! -d "$app/.git" ]; then
  say "fetching kittygram..."
  as_kg git clone -q "$kittygram_repo" "$app" || fail "could not fetch kittygram from $kittygram_repo"
fi
if [ "$(as_kg git -C "$app" rev-parse HEAD)" != "$kittygram_commit" ]; then
  as_kg git -C "$app" fetch -q origin || fail "could not fetch kittygram's updates"
  as_kg git -C "$app" checkout -q "$kittygram_commit" || fail "kittygram has no commit $kittygram_commit"
  rm -f "$app/lua_modules/.tele-notify-commit"
fi
as_kg git -C "$app" lfs pull > /dev/null 2>&1 || say "  (kittygram's pictures weren't fetched; the bot doesn't need them)"

# settings: kept between runs, apart from these lines
if [ ! -f "$app/nginx.conf" ]; then
  as_kg cp "$app/nginx.example.conf" "$app/nginx.conf"
fi
as_kg sed -i 's/^\([[:space:]]*\)listen \${{PORT}};/\1listen 127.0.0.1:${{PORT}};/' "$app/nginx.conf"
grep -q 'listen 127\.0\.0\.1:\${{PORT}};' "$app/nginx.conf" ||
  fail "$app/nginx.conf doesn't listen on 127.0.0.1 only; fix its listen line by hand"
if [ ! -f "$app/config.lua" ]; then
  as_kg cp "$app/config.example.lua" "$app/config.lua"
fi
chmod 600 "$app/config.lua"
# a random secret for kittygram's signed tokens, through a file (not on a
# command line, where other users could see it)
edits=$(as_kg mktemp "$home/src/.config-edits.XXXXXX")
printf 's/secret = "CHANGE_THIS!",/secret = "%s",/\n' "$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')" > "$edits"
as_kg sed -i -f "$edits" "$app/config.lua"
rm -f "$edits"
as_kg sed -i \
  -e "/^config(\"production\", {/,/^})/{
s/port = \"[0-9]*\"/port = \"$port\"/
s/num_workers = \"[0-9]*\"/num_workers = \"1\"/
s/allow_json = false/allow_json = true/
}" "$app/config.lua"
sed -n '/^config("production", {/,/^})/p' "$app/config.lua" | grep -q "port = \"$port\"" ||
  fail "could not set the port in $app/config.lua; its production block may have changed"
sed -n '/^config("production", {/,/^})/p' "$app/config.lua" | grep -q 'allow_json = true' ||
  fail "could not turn JSON answers on in $app/config.lua"

# kittygram's Lua modules, again when the commit changed
if [ "$(cat "$app/lua_modules/.tele-notify-commit" 2> /dev/null || true)" != "$kittygram_commit" ]; then
  say "installing kittygram's Lua modules..."
  as_kg sh -c "cd '$app' && luarocks init --lua-version=5.1 --lua-versions=5.1 > '$home/src/rocks.log' 2>&1 &&
    CC='gcc -std=gnu99' ./luarocks build >> '$home/src/rocks.log' 2>&1" ||
    fail "installing kittygram's Lua modules failed; see $home/src/rocks.log"
  as_kg sh -c "printf '%s\n' '$kittygram_commit' > '$app/lua_modules/.tele-notify-commit'"
fi
as_kg sh -c "cd '$app' && eval \"\$(./luarocks path)\" && lapis migrate production" > "$home/src/migrate.log" 2>&1 ||
  fail "kittygram's database setup failed; see $home/src/migrate.log"

# the service
cat > "$home/start.sh" << EOF
#!/bin/sh
# Start kittygram with its project tree's Lua paths (./config.lua and its modules).
set -eu
cd $app
eval "\$(./luarocks path)"
exec lapis serve production
EOF
chmod 755 "$home/start.sh"
cat > "$units/kittygram.service.tmp" << EOF
# kittygram (codeberg.org/irelephant/kittygram): an Instagram front end, used by
# tele-notify to read Instagram posts linked in job ads (#114). Local only:
# 127.0.0.1:$port. Installed by tele-notify's deploy/install-kittygram.sh.
[Unit]
Description=kittygram Instagram front end (local, for tele-notify)
After=network-online.target valkey-server.service
Wants=network-online.target valkey-server.service

[Service]
User=kittygram
Group=kittygram
WorkingDirectory=$app
Environment=PATH=$home/openresty/bin:$home/openresty/nginx/sbin:$app/lua_modules/bin:/usr/bin:/bin
ExecStart=$home/start.sh
Restart=on-failure
RestartSec=10
Nice=10
MemoryMax=300M
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=$app
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF
mv -f "$units/kittygram.service.tmp" "$units/kittygram.service"
systemctl daemon-reload || fail "systemctl daemon-reload failed"
systemctl enable kittygram > /dev/null 2>&1 || fail "could not enable kittygram.service"
systemctl restart kittygram || fail "could not start kittygram; see journalctl -u kittygram"

say "waiting for kittygram to answer..."
tries=0
until curl -fsS -o /dev/null --max-time 5 "http://127.0.0.1:$port/"; do
  tries=$((tries + 1))
  [ "$tries" -lt "${KG_WAIT_TRIES:-20}" ] || fail "kittygram doesn't answer on 127.0.0.1:$port; see journalctl -u kittygram"
  sleep 1
done

say "kittygram is running on 127.0.0.1:$port."
say "Put this in the bot's config.json, then restart the bot:"
say "  \"instagram_frontend\": \"http://127.0.0.1:$port\""
