#!/bin/sh
# Autostart of the own system without the laptop. /system cannot be changed
# on this tablet (P5.4), so a small Android app (scripts/build-autostart-apk.py)
# starts the launcher (tablet/tabletd-launch.sh) after Android has booted;
# the launcher decides by the mode file whether the own system takes over.
# Needs the USB cable and Android running (KingRoot's su).
#
#   scripts/autostart.sh install        programs to /data/tabletd/bin, key, laptop
#                                       addresses, the app; mode stays as it is
#                                       (new: android)
#   scripts/autostart.sh mode MODE      android | mini-once | mini
#   scripts/autostart.sh trigger        run the app's receiver now, as at boot
#   scripts/autostart.sh status         mode, counter, last lines of boot.log
#   scripts/autostart.sh internet NAME PORT   the laptop's address from outside (DynDNS
#                                       name or IPv4, forwarded port); "internet off" removes it
#   scripts/autostart.sh update         only replace the programs in /data/tabletd/bin
#                                       (works in the own system too, through tb-su)
#   scripts/autostart.sh remove         uninstall the app, mode android
set -eu

cd "$(dirname "$0")/.."
. scripts/adb-serial.sh

D=/data/tabletd
T=/data/local/tmp
PKG=local.tablet.autostart
APK=build/autostart/tablet-autostart.apk

say() { echo "autostart: $*"; }
fail() { echo "autostart: $*" >&2; exit 1; }
# root on the tablet: KingRoot's su while Android runs, tb-su (tb-rootd) in the own system
ROOT_VIA=""
root() {
    if [ "$ROOT_VIA" = tb-su ]; then
        adb shell "$T/tb-su -c '$*'" | tr -d '\r'
    else
        adb shell "su -c '$*'" | tr -d '\r'
    fi
}

need_root() {
    [ "$(adb get-state 2>/dev/null | tr -d '\r')" = "device" ] || fail "tablet not reachable over USB"
    if [ "$(adb shell getprop init.svc.zygote | tr -d '\r')" != "running" ]; then
        ROOT_VIA=tb-su
    fi
    case "$(root id)" in
        *uid=0*) ;;
        *) fail "no root on the tablet (Android stopped and tb-rootd not reachable?)" ;;
    esac
}

install_bin() {  # install_bin LOCAL NAME MODE
    adb push "$1" "$T/as-$2" >/dev/null
    root "cat $T/as-$2 > $D/bin/$2.new && chmod $3 $D/bin/$2.new && mv $D/bin/$2.new $D/bin/$2 && rm $T/as-$2"
    [ "$(md5sum "$1" | cut -d' ' -f1)" = "$(root "md5 $D/bin/$2" | cut -d' ' -f1)" ] ||
        fail "checksum mismatch for $D/bin/$2"
}

# the launcher runs under the tablet's mksh, which the laptop's tests do not
# have: check it there before it replaces the working one (a syntax error made
# the icon start nothing on 2026-11-17)
install_launcher() {
    adb push tablet/tabletd-launch.sh "$T/as-launch-check" >/dev/null
    case "$(adb shell "sh -n $T/as-launch-check 2>&1; echo rc=\$?; rm $T/as-launch-check" | tr -d '\r')" in
        rc=0) ;;
        *) fail "tabletd-launch.sh fails the tablet's shell check (sh -n); the old launcher stays" ;;
    esac
    install_bin tablet/tabletd-launch.sh tabletd-launch.sh 700
}

cmd_install() {
    need_root
    make arm >/dev/null
    python3 scripts/build-autostart-apk.py >/dev/null
    root "mkdir -p $D/bin && chmod 711 $D && chmod 700 $D/bin"
    for name in tb-detach tb-keyheld tb-rootd tb-su tb-home tabletd tb-linux tb-audio; do
        install_bin "build/arm/$name" "$name" 700
    done
    install_launcher
    say "programs in $D/bin"
    # pairing key for the encrypted Wi-Fi link (from "huawai pair")
    case "$(root "[ -f $T/tabletd-secret/key ] && cat $T/tabletd-secret/key > $D/key && chmod 600 $D/key && echo key-ok")" in
        *key-ok*) say "pairing key copied" ;;
        *) say "WARNING: no pairing key on the tablet yet (run 'huawai up' or 'huawai pair' first," \
               "then install again): without it only the cable works" ;;
    esac
    # this laptop's addresses in the tablet's network, for the launcher's firewall
    host=$(python3 -c 'from host import link; print(link.tablet_wlan_address() or "")')
    if [ -n "$host" ]; then
        python3 - "$host" > build/autostart/laptops <<'EOF'
import ipaddress, subprocess, sys
tablet = ipaddress.IPv4Address(sys.argv[1])
out = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"],
                     capture_output=True, text=True, check=True).stdout
for line in out.splitlines():
    interface = ipaddress.ip_interface(line.split()[3])
    if tablet in interface.network:
        print(interface.ip)
EOF
        adb push build/autostart/laptops "$T/as-laptops" >/dev/null
        root "cat $T/as-laptops > $D/laptops && chmod 600 $D/laptops && rm $T/as-laptops"
        say "laptop addresses: $(tr '\n' ' ' < build/autostart/laptops)"
    else
        say "the tablet has no Wi-Fi address: laptop list unchanged"
    fi
    root "[ -f $D/mode ] || echo android > $D/mode; cat $D/mode" | sed 's/^/autostart: mode /'
    adb install -r "$APK" | tr -d '\r' | tail -n 1
    say "installed; KingRoot may ask once on the screen whether the app may use root"
}

cmd_update() {
    need_root
    make arm >/dev/null
    for name in tb-detach tb-keyheld tb-rootd tb-su tb-home tabletd tb-linux tb-audio; do
        install_bin "build/arm/$name" "$name" 700
    done
    install_launcher
    say "programs in $D/bin updated; they take effect when started next (tile, boot)"
}

cmd_internet() {
    need_root
    if [ "${1:-}" = off ]; then
        root "rm -f $D/internet"
        say "the tablet no longer calls the laptop from outside"
        return 0
    fi
    name=${1:-}
    port=${2:-}
    printf '%s' "$name" | grep -Eq '^[A-Za-z0-9.:-]{1,253}$' || fail "internet: NAME is a host name, IPv4 or IPv6"
    case "$port" in ''|*[!0-9]*) fail "internet: PORT is a number" ;; esac
    [ "$port" -ge 1024 ] && [ "$port" -le 65535 ] || fail "internet: PORT from 1024 to 65535"
    root "echo $name $port > $D/internet && chmod 600 $D/internet && cat $D/internet" |
        sed 's/^/autostart: internet /'
    say "takes effect at the next start of the own system (icon or boot)"
}

cmd_mode() {
    need_root
    case "${1:-}" in android|mini-once|mini) ;; *) fail "mode: android | mini-once | mini" ;; esac
    root "echo $1 > $D/mode && echo 0 > $D/unconfirmed && cat $D/mode" | sed 's/^/autostart: mode /'
}

cmd_trigger() {
    need_root
    # explicit broadcast also to a package that was never started (flag 0x20);
    # that clears Android's "stopped" state, so BOOT_COMPLETED reaches it later
    adb shell "am broadcast -f 32 -n $PKG/.BootReceiver" | tr -d '\r' | tail -n 1
}

cmd_status() {
    need_root
    root "echo mode: \$(cat $D/mode 2>/dev/null); echo unconfirmed: \$(cat $D/unconfirmed 2>/dev/null); cat $D/boot.log 2>/dev/null"
    adb shell "pm list packages $PKG" | tr -d '\r'
}

cmd_remove() {
    need_root
    root "echo android > $D/mode"
    adb uninstall "$PKG" | tr -d '\r'
}

case "${1:-}" in
    install) cmd_install ;;
    mode) cmd_mode "${2:-}" ;;
    trigger) cmd_trigger ;;
    update) cmd_update ;;
    internet) cmd_internet "${2:-}" "${3:-}" ;;
    status) cmd_status ;;
    remove) cmd_remove ;;
    *) sed -n '2,/^set -eu/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 2 ;;
esac
