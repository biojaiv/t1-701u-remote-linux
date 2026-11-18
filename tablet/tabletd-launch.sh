#!/system/bin/sh
# tabletd-launch - decides at boot whether Android or the mini mode runs
# (docs/PLAN.md, phase 5b). /system cannot be changed on this tablet
# (P5.4: write-protected below the kernel), so it is not started from a boot
# hook but by the autostart app after Android has started, as root through
# KingRoot's su and away from the app's session (tb-detach):
#     /data/tabletd/bin/tb-detach /system/bin/sh /data/tabletd/bin/tabletd-launch.sh
# Every doubt ends in "android": that is the state the tablet can always be
# reached and repaired in.
#
# Files in /data/tabletd:
#   mode         android | mini | mini-once   (missing or unknown: android)
#   unconfirmed  boots in mini mode without a laptop showing a picture
#   no-android   exists after phase 5d: there is no Android to fall back to
#   boot.log     every decision, with time
#   rotation     none | counterclockwise | clockwise: how the status page is turned
#   laptops      IPv4 addresses (one per line) that may reach ports 7100 and 5555;
#                missing or empty: nobody may (written by "huawai autostart")
#   key          the pairing key for the encrypted Wi-Fi link
#   internet     "ADDRESS... PORT": the laptop at home, reached from another
#                network: up to 4 IPv6 addresses, DynDNS names or IPv4
#                addresses, then the port its router lets through; the tablet
#                calls them in turn while the remote control waits
#   internet.learned  the same, written by tabletd from what hostd sends at
#                every connection (its current addresses); called first
#   android-once exists: this start stays Android (the start screen's "Android" tile)
#   bin/         the programs (tb-home, tabletd, tb-linux, ...)
#
# Argument "now": the "Tablet System" icon in Android was tapped - the own
# system starts at once, whatever the mode file says (no key, no counter).
#   linux/       the Linux system, if installed (docs/LINUX.md)
#
# TB_DIR, TB_SETTLE and TB_BOOT_WAIT exist for the tests on the laptop.

DIR=${TB_DIR:-/data/tabletd}
SETTLE=${TB_SETTLE:-2}
BOOT_WAIT=${TB_BOOT_WAIT:-120}  # seconds Android may take to come back before a restart
KEYS=/dev/input/event0
PATH="$DIR/bin:$PATH"
TABLETD=${TB_TABLETD:-$DIR/bin/tabletd}
CHAIN=tabletd_in
VOLUME_DOWN=114
VOLUME_UP=115

log() {
    echo "$(date) $*" >> "$DIR/boot.log"
}

uptime_s() {
    read up _ < /proc/uptime
    echo "${up%%.*}"
}

# Only the laptops in $DIR/laptops may reach tabletd (7100) and adb over
# Wi-Fi (5555); the same rules as scripts/firewall.py. Set before anything
# listens.
firewall() {
    iptables -N "$CHAIN" 2>/dev/null
    iptables -F "$CHAIN"
    n=0
    if [ -f "$DIR/laptops" ]; then
        while read -r ip _; do
            case "$ip" in
                *[!0-9.]*|'') continue ;;
            esac
            iptables -A "$CHAIN" -s "$ip" -j ACCEPT
            n=$((n + 1))
        done < "$DIR/laptops"
    fi
    iptables -A "$CHAIN" -j DROP
    for port in 7100 5555; do
        while iptables -D INPUT -p tcp --dport "$port" -j "$CHAIN" 2>/dev/null; do :; done
        iptables -I INPUT 1 -p tcp --dport "$port" -j "$CHAIN"
    done
    log "firewall: ports 7100 and 5555 for $n laptop address(es)"
}

mkdir -p "$DIR" 2>/dev/null

# once per boot: the autostart app may be triggered more than once
if [ -f "$DIR/launch.pid" ]; then
    read -r old < "$DIR/launch.pid"
    if [ -n "$old" ] && [ "$old" != "$$" ] && [ -d "/proc/$old" ] &&
        grep -q tabletd-launch "/proc/$old/cmdline" 2>/dev/null; then
        log "already running (pid $old), not started again"
        exit 0
    fi
fi
echo "$$" > "$DIR/launch.pid"

mode=android
[ -f "$DIR/mode" ] && read mode < "$DIR/mode"
case "$mode" in
    android|mini|mini-once) ;;
    *) log "unknown mode '$mode', using android"; mode=android ;;
esac
if [ "${1:-}" = now ]; then
    log "started from the Tablet System icon"
    mode=now
elif [ -f "$DIR/android-once" ]; then
    rm -f "$DIR/android-once"
    log "android-once: this start stays android"
    mode=android
fi

fallback=yes
[ -f "$DIR/no-android" ] && fallback=no

if [ "$mode" = mini-once ]; then
    # written back first: whatever happens next, the following boot is Android
    echo android > "$DIR/mode"
    sync
    mode=mini
    log "mini-once: mode file set back to android"
fi

if [ "$mode" = mini ] && [ "$fallback" = yes ]; then
    tb-keyheld "$KEYS" "$VOLUME_DOWN" "$VOLUME_UP"
    case $? in
        0) log "volume key held"; mode=android ;;
        1) ;;
        *) log "key state unknown"; mode=android ;;
    esac
fi

if [ "$mode" = mini ] && [ "$fallback" = yes ]; then
    count=0
    [ -f "$DIR/unconfirmed" ] && read count < "$DIR/unconfirmed"
    case "$count" in ''|*[!0-9]*) count=0 ;; esac
    count=$((count + 1))
    echo "$count" > "$DIR/unconfirmed"
    if [ "$count" -gt 3 ]; then
        log "unconfirmed=$count, more than 3"
        mode=android
    fi
fi

if [ "$mode" = android ]; then
    log "decision: android"
    exit 0
fi
[ "$mode" = now ] && mode=mini

log "decision: mini (fallback to android: $fallback)"
firewall
linux=""
if [ -f "$DIR/linux/devuan.img" ] && [ -f "$DIR/linux/linux-root.sh" ]; then
    # mount the Linux system and start its daemons (tb-linuxd, tb-sysd)
    if sh "$DIR/linux/linux-root.sh" daemon >> "$DIR/boot.log" 2>&1; then
        linux="--linux $DIR/bin/tb-linux"
    else
        log "linux: could not be started, tile left out"
    fi
fi
# the laptop at home, reached from another network: resolved now, while
# Android's resolver still runs (the static tabletd cannot resolve names).
# $DIR/internet: one or more addresses or names, then the port; the router
# may give the laptop another IPv6 address after a restart, so several can be
# kept and tabletd tries them in turn (at most 4). IPv6 first: behind DS-Lite
# (common on cable connections) the laptop has no public IPv4.
#
# resolve NAME: "[IPV6]", "IPV4" or nothing. Only shell means: Android's
# toolbox has no host/nslookup. With -n, ping6 prints "PING NAME(ADDRESS) ..."
# (without -n the reverse name). The tablet's shell is mksh: an unquoted "*("
# there starts an extended pattern, so every parenthesis in a pattern stays
# escaped.
resolve() {
    case "$1" in
        *[!0-9a-fA-F:]*) ;;
        *:*:*) echo "[$1]"; return ;;             # an IPv6 address already
    esac
    r6=""
    set -- "$1" $(ping6 -n -c 1 -W 2 "$1" 2>/dev/null) x x
    if [ "$2" = PING ]; then
        r6=${3#*\(}
        r6=${r6%%\)*}
    fi
    # only hex digits and colons, and at least two colons
    case "$r6" in *[!0-9a-fA-F:]*) r6="" ;; *:*:*) echo "[$r6]"; return ;; esac
    # IPv4 only when there is no IPv6 address: ping prints "PING NAME (ADDRESS) ..."
    set -- "$1" $(ping -n -c 1 -W 2 "$1" 2>/dev/null) x x x
    if [ "$2" = PING ]; then
        r4=${4#\(}
        r4=${r4%\)}
        case "$r4" in *[!0-9.]*|'') ;; *) echo "$r4" ;; esac
    fi
}
call=""
if [ -f "$DIR/internet" ]; then
    read -r line < "$DIR/internet"
    words=0
    for w in $line; do words=$((words + 1)); port=$w; done
    case "$port" in ''|*[!0-9]*) port="" ;; esac
    if [ "$words" -lt 2 ] || [ -z "$port" ]; then
        log "internet: no valid port in $DIR/internet, no call"
    else
        i=0
        targets=0
        for name in $line; do
            i=$((i + 1))
            [ "$i" -eq "$words" ] && break           # the last word is the port
            [ "$targets" -ge 4 ] && { log "internet: more than 4 addresses, the rest is left out"; break; }
            addr=$(resolve "$name")
            if [ -n "$addr" ]; then
                call="$call --secure-connect $addr:$port"
                targets=$((targets + 1))
                log "internet: calling $name ($addr) on port $port"
            else
                log "internet: '$name' not resolved or no route (no IPv6 in this network?)"
            fi
        done
        [ "$targets" -eq 0 ] && log "internet: no address to call"
    fi
fi
setprop sys.usb.config adb      # no MTP: nothing would answer it
stop                            # zygote and surfaceflinger
sleep "$SETTLE"
tb-rootd &                      # root for adb shell; KingRoot's su needs Android
rootd_pid=$!

ends=0
# the encrypted Wi-Fi port and the call from outside need the pairing key
# (huawai pair); tabletd refuses to start with --key and no key file, so
# without one only the cable (adb) works
secure=""
if [ -f "$DIR/key" ]; then
    secure="--secure-listen 7100 --key $DIR/key --learn-file $DIR/internet.learned $call"
else
    log "no pairing key in $DIR/key: no Wi-Fi port, no call from outside (cable only)"
fi

first_end=0
while :; do
    began=$(uptime_s)
    # start screen (root: it switches the backlight); it runs the remote
    # control as a separate program, which drops to its own user (2900)
    # after opening the devices, the key and the network port
    tb-home --fb /dev/graphics/fb0 --touch /dev/input/event3 --keys "$KEYS" \
            --battery /sys/class/power_supply/battery \
            --backlight /sys/class/backlight/sprd_backlight/brightness \
            --rotation-file "$DIR/rotation" --tz-file "$DIR/tz" \
            --confirm "$DIR/unconfirmed" --app-log "$DIR/tabletd.log" $linux --android-tile \
            -- "$TABLETD" --fb /dev/graphics/fb0 \
               --input /dev/input/event3 --input "$KEYS" \
               --battery /sys/class/power_supply/battery \
               --status-page --rotation-file "$DIR/rotation" --tz-file "$DIR/tz" \
               --power-key ignore --idle-dim 0 --confirm "$DIR/unconfirmed" \
               --audio "$DIR/bin/tb-audio" \
               $secure --drop 2900:2900
    rc=$?
    now=$(uptime_s)
    log "tb-home ended with $rc after $((now - began)) s"

    if [ "$rc" -eq 42 ]; then       # power key held for three seconds
        log "switching off"
        sync
        reboot -p
        exit 0
    fi
    if [ "$rc" -eq 43 ]; then       # the "Android" tile: back to Android
        # Starting Android again (instead of a whole restart) hung twice at
        # "Starting apps" during development, cause unknown. So: start it,
        # wait for the end of its boot (sys.boot_completed, set to 0 first);
        # if it does not come within BOOT_WAIT seconds, restart as before.
        log "back to android: starting it again"
        : > "$DIR/android-once"   # the boot receiver must not start the own system
        kill "$rootd_pid" 2>/dev/null
        setprop sys.boot_completed 0
        start
        waited=0
        while [ "$(getprop sys.boot_completed)" != 1 ] && [ "$waited" -lt "$BOOT_WAIT" ]; do
            sleep 1
            waited=$((waited + 1))
        done
        if [ "$(getprop sys.boot_completed)" = 1 ]; then
            # android-once stays: the boot receiver's call that follows uses it
            # up and stays in Android. Holding the guard instead blocked the
            # "Tablet System" icon for a minute (user, 2026-11-17).
            log "android is back after $waited s"
            exit 0
        fi
        log "android did not come back within $BOOT_WAIT s: restart"
        sync
        reboot
        exit 0
    fi

    # three ends within one minute: give up on mini mode
    if [ "$ends" -eq 0 ] || [ $((now - first_end)) -gt 60 ]; then
        ends=1
        first_end=$now
    else
        ends=$((ends + 1))
    fi
    if [ "$ends" -ge 3 ]; then
        if [ "$fallback" = yes ]; then
            log "tb-home ended 3 times within a minute: starting android"
            setprop sys.usb.config "$(getprop persist.sys.usb.config)"
            start
            exit 1
        fi
        log "tb-home ended 3 times within a minute; no android to start, waiting"
        sleep 30
        ends=0
    fi
    sleep 1
done
