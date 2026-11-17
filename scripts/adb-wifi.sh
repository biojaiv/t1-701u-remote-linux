#!/bin/sh
# ADB over Wi-Fi for long setup work when the USB cable keeps dropping out.
#
#   scripts/adb-wifi.sh on    over USB, Android running: adbd listens on TCP 5555
#                             instead of USB, then the laptop connects over Wi-Fi
#   scripts/adb-wifi.sh off   over Wi-Fi: adbd back to USB
#
# Safety: "on" refuses unless the tablet's firewall chain tabletd_in admits
# port 5555 only for the laptop's addresses (scripts/firewall.py). ADB keeps
# asking for the laptop's key. ADB in Android 4.4 is NOT encrypted: whoever
# reads the Wi-Fi traffic sees the commands. The setting is not persistent:
# a reboot of the tablet switches ADB back to USB.
set -eu

cd "$(dirname "$0")/.."
T=/data/local/tmp
PORT=5555

fail() { echo "adb-wifi: $*" >&2; exit 1; }

# Android's init restarts adbd itself (ctl.restart), so nothing of ours has
# to survive the end of the adb session. A detached script that stops and
# starts adbd was tried first; on the single online CPU it was sometimes killed
# with its su session before it ran, which could leave adbd stopped entirely.
# If init did not know ctl.restart, nothing would happen and adbd stays as it is.
switch() {  # switch PORT (0 = USB)
    timeout 10 adb shell "su -c 'setprop service.adb.tcp.port $1; setprop ctl.restart adbd'" \
        >/dev/null 2>&1 || true
}

case "${1:-}" in
    on)
        . scripts/adb-serial.sh
        [ -n "${ANDROID_SERIAL:-}" ] || fail "the tablet must be connected over USB for this"
        host=$(python3 -c 'from host import link; print(link.tablet_wlan_address() or "")')
        [ -n "$host" ] || fail "the tablet has no Wi-Fi address"
        rules=$(adb shell "su -c 'iptables -S tabletd_in; iptables -S INPUT'" | tr -d '\r')
        # expected (scripts/firewall.py): INPUT sends port 5555 to tabletd_in, which
        # accepts only single addresses and drops everything else at its end
        echo "$rules" | grep -q -- "^-A INPUT -p tcp -m tcp --dport $PORT -j tabletd_in$" ||
            fail "firewall does not send port $PORT to tabletd_in"
        [ "$(echo "$rules" | grep -- '^-A tabletd_in' | tail -n 1)" = "-A tabletd_in -j DROP" ] ||
            fail "tabletd_in does not end with DROP"
        if echo "$rules" | grep -- '^-A tabletd_in' | grep -v -- '-j DROP$' | grep -vqE -- '^-A tabletd_in -s [0-9.]+/32 -j ACCEPT$'; then
            fail "tabletd_in has rules other than single laptop addresses"
        fi
        switch "$PORT"
        i=0
        until adb connect "$host:$PORT" 2>/dev/null | grep -q "connected to"; do
            i=$((i + 1))
            [ "$i" -ge 15 ] && fail "no ADB over Wi-Fi; a reboot of the tablet brings USB back"
            sleep 2
        done
        sleep 1
        echo "adb-wifi: connected to $host:$PORT (state: $(adb -s "$host:$PORT" get-state 2>&1))"
        echo "adb-wifi: for the scripts: export ANDROID_SERIAL=$host:$PORT"
        ;;
    off)
        [ -n "${ANDROID_SERIAL:-}" ] || fail "set ANDROID_SERIAL to the Wi-Fi connection (HOST:$PORT)"
        switch 0
        sleep 3
        adb disconnect "$ANDROID_SERIAL" >/dev/null 2>&1 || true
        echo "adb-wifi: ADB is back on USB"
        ;;
    *) echo "usage: $0 on | off" >&2; exit 2 ;;
esac
