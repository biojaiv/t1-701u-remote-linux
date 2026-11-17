#!/bin/sh
# Push the current ARM build and (re)start the start screen tb-home on the
# tablet as the shell user. tb-home starts the remote control (tabletd) when
# its tile is tapped, or at once with --remote. Android must be stopped first
# (scripts/android.sh stop), otherwise both draw on the screen.
#
# --linux adds the "Linux" tile (needs the session daemon: scripts/linux.sh daemon).
#
# Usage: scripts/run.sh [--remote] [--linux] [--rotation R] [-- extra tabletd arguments]
set -eu

cd "$(dirname "$0")/.."
. scripts/adb-serial.sh

T=/data/local/tmp
HOME_LOG=$T/tb-home.log
APP_LOG=$T/tabletd.log
TZ_FILE=$T/tabletd-tz
PORT=7100
start=""
linux=""
rotation=counterclockwise

while [ $# -gt 0 ]; do
    case "$1" in
        --remote) start=--start-remote; shift ;;
        --linux) linux="--linux $T/tb-linux"; shift ;;
        --rotation) rotation=$2; shift 2 ;;
        --) shift; break ;;
        *) echo "usage: $0 [--remote] [--linux] [--rotation R] [-- tabletd arguments]" >&2; exit 2 ;;
    esac
done

# stop running instances (same user, so no root needed); tb-home ends tabletd
for log in $HOME_LOG $APP_LOG; do
    old=$(adb shell "cat $log 2>/dev/null" | tr -d '\r' | sed -n 's/^tb-home pid \([0-9]*\)$/\1/p; s/^tabletd pid \([0-9]*\)$/\1/p' | head -n 1)
    if [ -n "$old" ]; then
        adb shell "[ -d /proc/$old ] && kill $old" >/dev/null 2>&1 || true
    fi
done
sleep 1

scripts/push.sh
INPUTS="--input /dev/input/event3 --input /dev/input/event0"
COMMON="--battery /sys/class/power_supply/battery --rotation $rotation --tz-file $TZ_FILE"
adb shell ": > $APP_LOG; $T/tb-home --fb /dev/graphics/fb0 --touch /dev/input/event3 \
    --keys /dev/input/event0 $COMMON --app-log $APP_LOG --daemon $HOME_LOG $start $linux \
    -- $T/tabletd --fb /dev/graphics/fb0 $INPUTS $COMMON --status-page \
    --power-key ignore --idle-dim 0 --audio $T/tb-audio $*"
sleep 1
adb shell "cat $HOME_LOG" | tr -d '\r'
adb forward tcp:$PORT localabstract:tabletd >/dev/null
echo "run: forwarded tcp:$PORT -> localabstract:tabletd"
