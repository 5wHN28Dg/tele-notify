#!/bin/sh
# Build libtdjson.so (TDLib, Telegram's official client library) and put it
# in lib/ next to the telenotify program.
#
# This is shell, not Nim, on purpose: it only drives cmake/make for a C++
# library. TDLib is big; compiling needs about 4 GB of RAM per parallel
# job and takes 20-40 minutes. Lower JOBS if memory is tight.
#
#   JOBS=2 deploy/build-tdlib.sh            # default JOBS=2
#   TD_REF=v1.8.0 deploy/build-tdlib.sh     # a specific TDLib tag/commit
#
# Needs: git, cmake, a C++ compiler, gperf, OpenSSL and zlib headers
# (Ubuntu: sudo apt install git cmake g++ gperf libssl-dev zlib1g-dev).
# If gperf is missing it is built from GNU's source into the build folder.
set -eu

JOBS=${JOBS:-2}
TD_REF=${TD_REF:-master}
BUILD=${BUILD:-$HOME/.cache/tele-notify-build}
REPO=$(cd "$(dirname "$0")/.." && pwd)

mkdir -p "$BUILD"
cd "$BUILD"

if ! command -v gperf >/dev/null 2>&1 && [ ! -x "$BUILD/prefix/bin/gperf" ]; then
  echo "building gperf (needed by TDLib's code generator)..."
  curl -sSfL -o gperf-3.1.tar.gz https://ftp.gnu.org/pub/gnu/gperf/gperf-3.1.tar.gz
  echo "588546b945bba4b70b6a3a616e80b4ab466e3f33024a352fc2198112cdbb3ae2  gperf-3.1.tar.gz" | sha256sum -c -
  tar xzf gperf-3.1.tar.gz
  (cd gperf-3.1 && ./configure -q --prefix="$BUILD/prefix" && make -s && make -s install)
fi
PATH="$BUILD/prefix/bin:$PATH"

if [ ! -d td ]; then
  git clone https://github.com/tdlib/td.git
fi
(cd td && git fetch -q --tags origin && git checkout -q "$TD_REF" && git log --oneline -1)

mkdir -p td/build
cd td/build
cmake -DCMAKE_BUILD_TYPE=Release -DTD_ENABLE_LTO=OFF .. >/dev/null
cmake --build . --target tdjson -j "$JOBS"

mkdir -p "$REPO/lib"
cp -L libtdjson.so "$REPO/lib/libtdjson.so"
echo "done: $REPO/lib/libtdjson.so"
