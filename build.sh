#!/bin/sh
# Builds everything from source and, with --test, runs the test suite.
#
#   ./build.sh          check the build tools, build tablet and laptop programs
#   ./build.sh --test   the same, then run all tests (about 2-3 minutes)
#   ./build.sh --deps   only list missing packages (Debian/Ubuntu names)
#
# Needs no root. If something is missing, it prints one apt command to install
# it; nothing is installed by this script.
set -eu
cd "$(dirname "$0")"

missing=""
later=""
need_prog() {  # need_prog PROGRAM PACKAGE
    command -v "$1" >/dev/null 2>&1 || missing="$missing $2"
}
want_prog() {  # want_prog PROGRAM PACKAGE: not for building, but for installing
    command -v "$1" >/dev/null 2>&1 || later="$later $2"
}
need_py() {    # need_py MODULE PACKAGE
    python3 -c "import $1" >/dev/null 2>&1 || missing="$missing $2"
}

need_prog make make
need_prog gcc gcc
need_prog arm-linux-gnueabihf-gcc gcc-arm-linux-gnueabihf
need_prog python3 python3
if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' ||
        { echo "build: Python 3.11 or newer is needed (tomllib)" >&2; exit 1; }
    need_py numpy python3-numpy
    need_py PIL python3-pil
    need_py nacl python3-nacl
    need_py lz4 python3-lz4
    need_py psutil python3-psutil
    need_py gi python3-gi
    need_py dbus python3-dbus
fi
# not needed to build or test, only to install on the tablet:
# the Android starter app (scripts/build-autostart-apk.py) needs the JDK's
# keytool and jarsigner, everything else adb
want_prog keytool default-jdk-headless
want_prog jarsigner default-jdk-headless
want_prog adb adb
if [ -n "$later" ]; then
    list=$(printf '%s\n' $later | sort -u | tr '\n' ' ')
    echo "build: note: for installing on the tablet you will also need: $list"
fi

if [ -n "$missing" ]; then
    # one package name per entry, no duplicates
    list=$(printf '%s\n' $missing | sort -u | tr '\n' ' ')
    echo "build: missing packages: $list"
    echo "build: install them with:  sudo apt install $list"
    exit 1
fi
[ "${1:-}" = --deps ] && { echo "build: all build tools present"; exit 0; }

make all
echo "build: done; tablet programs in build/arm/, test programs in build/x86/"
if [ "${1:-}" = --test ]; then
    make test
fi
echo "build: next step: scripts/install.sh (see docs/INSTALL.md)"
