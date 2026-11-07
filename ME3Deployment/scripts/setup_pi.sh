#!/bin/sh
# Project-local dependencies only. No sudo or system package changes.
set -eu
cd "$(dirname "$0")/.."
mkdir -p evidence/m1 .tmp/pip-cache .deps
export PIP_CACHE_DIR="$PWD/.tmp/pip-cache"
python3 -m venv .venv
if ! test -f /usr/include/portaudio.h || ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
    mkdir -p .deps/debs .deps/root
    (cd .deps/debs && apt-get download libportaudio2 portaudio19-dev)
    for package in .deps/debs/*.deb; do dpkg-deb -x "$package" .deps/root; done
    triplet="$(gcc -dumpmachine)"
    local_lib="$PWD/.deps/root/usr/lib/$triplet"
    export CFLAGS="-I$PWD/.deps/root/usr/include"
    export LDFLAGS="-L$local_lib -Wl,-rpath,$local_lib"
    export LD_LIBRARY_PATH="$local_lib"
    ldd "$local_lib/libportaudio.so.2"
fi
.venv/bin/python -m pip install --disable-pip-version-check --no-input -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -m pip freeze > requirements-pi.lock.txt
