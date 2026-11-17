#!/bin/sh
# Stop and start the Android layer on the tablet for tests (phases 1 to 3).
#
# Two things measured in step P0.6 make this script necessary:
#  - Without Android the tablet does not answer MTP, and the laptop's MTP
#    service then resets the USB device every 16 seconds. So the tablet is
#    switched to adb-only USB mode before Android is stopped.
#  - KingRoot's su does not work while Android is stopped. So a small root
#    helper is started on the tablet beforehand; it waits for a request file
#    and then starts Android again. If the helper is gone, "start" falls back
#    to a reboot, which restores the normal state as well.
#
# Usage: scripts/android.sh stop | start | status
set -u

cd "$(dirname "$0")/.." || exit 1
. scripts/adb-serial.sh

HELPER=/data/local/tmp/tabletd-root-helper.sh
REQUEST=/data/local/tmp/tabletd-request
PIDFILE=/data/local/tmp/tabletd-root-helper.pid
TABLETD_LOG=/data/local/tmp/tabletd.log

state() { adb get-state 2>/dev/null | tr -d '\r'; }
prop() { adb shell "getprop $1" 2>/dev/null | tr -d '\r'; }

wait_device() {  # wait_device <seconds>
    i=0
    while [ "$i" -lt "$(($1 * 2))" ]; do
        [ "$(state)" = "device" ] && return 0
        sleep 0.5
        i=$((i + 1))
    done
    return 1
}

helper_alive() {
    [ "$(adb shell "[ -d /proc/\$(cat $PIDFILE 2>/dev/null || echo none) ] && echo alive" 2>/dev/null | tr -d '\r')" = "alive" ]
}

need_device() {
    [ "$(state)" = "device" ] || { echo "android.sh: tablet not reachable" >&2; exit 1; }
}

do_status() {
    need_device
    echo "zygote=$(prop init.svc.zygote) surfaceflinger=$(prop init.svc.surfaceflinger) usb=$(prop sys.usb.state)"
    echo "root-helper=$(helper_alive && echo running || echo absent)"
}

do_stop() {
    need_device
    if [ "$(prop init.svc.zygote)" != "running" ]; then
        echo "android.sh: Android is not running; nothing to stop" >&2
        exit 1
    fi
    case "$(adb shell "su -c id" 2>/dev/null)" in
        *uid=0*) ;;
        *) echo "android.sh: su does not give root" >&2; exit 1 ;;
    esac

    # 1. root helper: performs one fixed action, never anything from a file
    if ! helper_alive; then
        adb push tablet/root-helper.sh "$HELPER" >/dev/null || exit 1
        # This adb call never returns by itself (su keeps the session open),
        # so it is cut off; the helper on the tablet keeps running.
        # su can take several seconds right after Android has started.
        # "sleep 2": with one CPU online the helper often ran only after the su
        # session had ended and was taken along before its trap (2026-11-16)
        timeout 10 adb shell "su -c 'sh $HELPER >/dev/null 2>&1 & sleep 2'" >/dev/null 2>&1
        i=0
        until helper_alive; do
            i=$((i + 1))
            [ "$i" -ge 10 ] && { echo "android.sh: root helper did not start" >&2; exit 1; }
            sleep 1
        done
    fi

    # 2. adb-only USB mode (the connection drops and comes back)
    if [ "$(prop sys.usb.state)" != "adb" ]; then
        timeout 10 adb shell "su -c 'setprop sys.usb.config adb'" >/dev/null 2>&1
        sleep 2
        wait_device 60 || { echo "android.sh: tablet did not return in adb mode" >&2; exit 1; }
    fi

    # 3. stop zygote and surfaceflinger
    timeout 15 adb shell "su -c stop"
    sleep 2
    do_status
}

do_start() {
    need_device
    if [ "$(prop init.svc.zygote)" = "running" ]; then
        echo "android.sh: Android is already running"
        exit 0
    fi
    # tabletd must not keep drawing while Android owns the screen
    old=$(adb shell "cat $TABLETD_LOG 2>/dev/null" | tr -d '\r' | sed -n 's/^tabletd pid \([0-9]*\)$/\1/p' | head -n 1)
    [ -n "$old" ] && adb shell "[ -d /proc/$old ] && kill $old" >/dev/null 2>&1
    adb shell "echo start > $REQUEST"
    i=0
    while [ "$i" -lt 20 ]; do
        sleep 1
        [ "$(prop init.svc.zygote)" = "running" ] && break
        i=$((i + 1))
    done
    if [ "$(prop init.svc.zygote)" != "running" ]; then
        echo "android.sh: root helper did not react, rebooting the tablet" >&2
        adb shell "rm -f $REQUEST" 2>/dev/null
        adb reboot
        sleep 15
    fi
    sleep 6   # the helper switches USB back to its normal mode; the connection may drop
    wait_device 180 || { echo "android.sh: tablet did not come back" >&2; exit 1; }
    i=0
    while [ "$i" -lt 60 ] && [ "$(prop sys.boot_completed)" != "1" ]; do
        sleep 2
        i=$((i + 1))
    done
    do_status
}

case "${1:-}" in
    stop) do_stop ;;
    start) do_start ;;
    status) do_status ;;
    *) echo "usage: $0 stop | start | status" >&2; exit 2 ;;
esac
