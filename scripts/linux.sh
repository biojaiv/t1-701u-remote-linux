#!/bin/sh
# The Linux system on the tablet (Devuan 6 in a chroot on Huawei's kernel),
# operated from the laptop. Needs the USB cable. Root comes from KingRoot's su
# while Android runs, from tb-su while the own system runs.
#
#   scripts/linux.sh install          everything below in order: tools, image, overlay,
#                                     mount, stage2, base, packages (long; can be repeated)
#   scripts/linux.sh tools            copy tb-chroot, tb-linuxd, linux-root.sh (after changes)
#   scripts/linux.sh send             copy the image (1.9 GB) over Wi-Fi to /data/local/tmp
#                                     (no root needed; adb push broke off on a loose cable)
#   scripts/linux.sh image            move it into the root-only directory (sends it first
#                                     if it is not on the tablet yet)
#   scripts/linux.sh overlay          copy the configuration files from linux/overlay
#   scripts/linux.sh mount | umount | stop | status
#   scripts/linux.sh daemon [ROTATION]   start the session daemon (rotation of the desktop:
#                                     counterclockwise, clockwise or none)
#   scripts/linux.sh task NAME        stage2 | base | packages | ui | upgrade; waits until done
#
# Installation images (USB-Installer, phase 7); no root needed:
#   scripts/linux.sh iso-send FILE    copy an image over the USB cable (adb forward + tb-fetch,
#                                     because adb push breaks off above 2 GiB)
#   scripts/linux.sh iso-list | iso-delete NAME
#   scripts/linux.sh usb on NAME [disk|cd] | usb off | usb status
#
# Transfer drive (a FAT image, the session's ~/Transfer or a USB drive for this
# laptop; one side at a time):
#   scripts/linux.sh transfer create [MB]   create it once on the tablet (default 512 MB)
#   scripts/linux.sh transfer give | take [force] | status
set -eu

cd "$(dirname "$0")/.."
. scripts/adb-serial.sh

D=/data/tabletd/linux
T=/data/local/tmp
ISO=/data/tabletd/iso
ISO_PORT=7102
IMAGE=build/linux/devuan.img

say() { echo "linux: $*"; }
fail() { echo "linux: $*" >&2; exit 1; }

need_device() {
    [ "$(adb get-state 2>/dev/null | tr -d '\r')" = "device" ] || fail "tablet not reachable over USB"
}

# root on the tablet: KingRoot's su while Android runs, tb-su (tb-rootd, started
# by the own system) while it is stopped; no single quotes in $*
ROOT_VIA=""
root() {
    if [ -z "$ROOT_VIA" ]; then
        ROOT_VIA=su
        [ "$(adb shell getprop init.svc.zygote | tr -d '\r')" = running ] || ROOT_VIA=tb-su
    fi
    if [ "$ROOT_VIA" = tb-su ]; then
        adb shell "$T/tb-su -c '$*'" | tr -d '\r'
    else
        adb shell "su -c '$*'" | tr -d '\r'
    fi
}

need_root() {
    need_device
    case "$(root id)" in
        *uid=0*) ;;
        *) fail "no root on the tablet (Android running: su; own system: tb-su)" ;;
    esac
}

remote_md5() {  # remote_md5 PATH [root]
    if [ "${2:-}" = root ]; then root "md5 $1"; else adb shell "md5 $1" | tr -d '\r'; fi | cut -d' ' -f1
}

push_checked() {  # push_checked LOCAL REMOTE
    adb push "$1" "$2" >/dev/null || fail "copying $1 failed"
    [ "$(md5sum "$1" | cut -d' ' -f1)" = "$(remote_md5 "$2")" ] || fail "checksum mismatch for $2"
}

# copy a file into the root-only directory and check it there
install_file() {  # install_file LOCAL NAME MODE
    push_checked "$1" "$T/linux-$2"
    root "cat $T/linux-$2 > $D/$2.new && chmod $3 $D/$2.new && mv $D/$2.new $D/$2 && rm $T/linux-$2"
    [ "$(md5sum "$1" | cut -d' ' -f1)" = "$(remote_md5 "$D/$2" root)" ] || fail "checksum mismatch for $D/$2"
    say "installed $D/$2"
}

cmd_tools() {
    need_root
    make arm >/dev/null
    # /data/tabletd: root's, others may pass through; the Linux directory: root only
    root "[ -d /data/tabletd ] || { mkdir /data/tabletd && chmod 711 /data/tabletd; }; mkdir -p $D && chmod 700 $D"
    install_file build/arm/tb-chroot tb-chroot 700
    install_file build/arm/tb-linuxd tb-linuxd 700
    install_file build/arm/tb-sysd tb-sysd 700
    # the camera service needs the Android NDK to build (make android); without
    # it the Linux system simply has no camera
    if make android >/dev/null 2>&1; then
        install_file build/android/tb-camera tb-camera 700
    else
        say "no Android NDK: tb-camera not built, the camera stays off (see Makefile)"
    fi
    install_file tablet/linux-root.sh linux-root.sh 700
    push_checked build/arm/tb-linux "$T/tb-linux"
    adb shell "chmod 755 $T/tb-linux"
    say "installed $T/tb-linux (start screen tile)"
    # the same program as client ("tb-sysd ask ..."), for the shell user
    push_checked build/arm/tb-sysd "$T/tb-sysd"
    push_checked build/arm/tb-fetch "$T/tb-fetch"
    adb shell "chmod 755 $T/tb-sysd $T/tb-fetch"
    root "mkdir -p $ISO && chown 2000:5000 $ISO && chmod 750 $ISO"
    say "image folder $ISO ready"
}

# The tablet waits with tb-fetch on port 5555 (its firewall lets only the
# laptop's addresses in, tb-fetch checks the address too); the laptop sends.
cmd_send() {
    need_device
    [ -f "$IMAGE" ] || fail "$IMAGE missing (see docs/LINUX.md, building the image)"
    local_sum=$(md5sum "$IMAGE" | cut -d' ' -f1)
    if [ "$(remote_md5 "$T/devuan.img")" = "$local_sum" ]; then
        say "image already in $T, checksum matches"
        return 0
    fi
    host=$(python3 -c 'from host import link; print(link.tablet_wlan_address() or "")')
    [ -n "$host" ] || fail "the tablet has no Wi-Fi address"
    src=$(ip route get "$host" | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')
    size=$(stat -c %s "$IMAGE")
    push_checked build/arm/tb-fetch "$T/tb-fetch"
    adb shell "chmod 755 $T/tb-fetch; rm -f $T/devuan.img $T/devuan.img.part"
    say "sending $((size / 1048576)) MB over Wi-Fi from $src to $host"
    adb shell "$T/tb-fetch --listen 5555 --from $src --timeout 120 $T/devuan.img $size \
        > $T/tb-fetch.log 2>&1 < /dev/null" &
    waiter=$!
    sleep 2
    python3 scripts/send-file.py "$host" 5555 "$IMAGE" || { kill "$waiter" 2>/dev/null; fail "sending failed"; }
    wait "$waiter" || true
    # the adb call may return before tb-fetch has written everything out (fsync of 1.9 GB)
    i=0
    until adb shell "cat $T/tb-fetch.log" | grep -qE "complete|removed|refused|nobody|error"; do
        i=$((i + 1))
        [ "$i" -ge 60 ] && fail "tb-fetch did not finish; see $T/tb-fetch.log"
        sleep 2
    done
    adb shell "cat $T/tb-fetch.log" | tr -d '\r' | grep -v ' MB$'
    [ "$(remote_md5 "$T/devuan.img")" = "$local_sum" ] || fail "checksum of the image on the tablet does not match"
    say "image in $T, checksum matches ($local_sum)"
}

cmd_image() {
    need_root
    if [ "$(root "[ -f $D/devuan.img ] && echo yes")" = "yes" ]; then
        say "image already in $D, not copied again"
        return 0
    fi
    cmd_send
    root "mv $T/devuan.img $D/devuan.img && chown 0:0 $D/devuan.img && chmod 600 $D/devuan.img"
    say "image in place"
}

cmd_overlay() {
    need_root
    make arm >/dev/null
    chmod 755 linux/overlay/usr/local/bin/* linux/overlay/usr/local/lib/tablet/*.sh
    # the files from linux/overlay plus what is built for the system
    rm -rf build/linux/overlay-stage
    cp -a linux/overlay build/linux/overlay-stage
    install -m 644 build/arm/libtablet-fbfix.so build/arm/libtablet-gtktouch.so \
        build/linux/overlay-stage/usr/local/lib/tablet/
    tar --owner=0 --group=0 --numeric-owner -C build/linux/overlay-stage -cf build/linux/overlay.tar .
    push_checked build/linux/overlay.tar "$T/linux-overlay.tar"
    root "sh $D/linux-root.sh overlay $T/linux-overlay.tar; rm $T/linux-overlay.tar"
}

cmd_task() {  # cmd_task NAME: start the job, wait until it has ended
    need_root
    root "sh $D/linux-root.sh task $1"
    polls=0
    while :; do
        sleep 15
        polls=$((polls + 1))
        state=$(root "sh $D/linux-root.sh task-state $1")
        last=$(root "[ -f $D/root/var/log/tablet-$1.log ] && $D/tb-chroot $D/root /usr/bin/tail -n 1 /var/log/tablet-$1.log || echo NO-LOG" | cut -c1-100)
        say "$1: $state | $last"
        # a job that never started leaves no log behind (seen once, 2026-11-16 18:02)
        [ "$last" = "NO-LOG" ] && [ "$polls" -ge 4 ] && fail "task $1 did not start (no log after 60 s)"
        case "$state" in
            "done 0") return 0 ;;
            done*) fail "task $1 failed; log: /var/log/tablet-$1.log in the system" ;;
            none) fail "task $1 did not start" ;;
        esac
    done
}

cmd_daemon() {
    need_root
    rotation=${1:-counterclockwise}
    case "$rotation" in counterclockwise|clockwise|none) ;; *) fail "unknown rotation $rotation" ;; esac
    root "echo $rotation > $D/rotation && sh $D/linux-root.sh daemon"
}

valid_image_name() {   # the same rules as tb-sysd
    case "$1" in
        .*|*/*) return 1 ;;
    esac
    printf '%s' "$1" | grep -Eq '^[A-Za-z0-9._+-]{1,96}\.([iI][sS][oO]|[iI][mM][gG])$'
}

cmd_iso_send() {
    need_device
    file=$1
    [ -f "$file" ] || fail "no such file: $file"
    name=$(basename "$file")
    valid_image_name "$name" || fail "the name must be letters, digits . _ + - and end in .iso or .img"
    size=$(stat -c %s "$file")
    free_k=$(adb shell "df /data" | tr -d '\r' | awk 'NR == 2 {print $4}')
    say "sending $name ($((size / 1048576)) MB) over the cable; free on /data: $free_k"
    adb shell "[ -d $ISO ] && [ -w $ISO ]" >/dev/null 2>&1 ||
        fail "$ISO missing or not writable; run 'scripts/linux.sh tools' once (Android running)"
    adb forward "tcp:$ISO_PORT" "tcp:$ISO_PORT" >/dev/null
    adb shell "rm -f $ISO/$name.part; $T/tb-fetch --listen $ISO_PORT --from 127.0.0.1 \
        --timeout 60 $ISO/$name $size > $T/tb-fetch.log 2>&1 < /dev/null" &
    waiter=$!
    sleep 2
    python3 scripts/send-file.py 127.0.0.1 "$ISO_PORT" "$file" ||
        { kill "$waiter" 2>/dev/null; adb forward --remove "tcp:$ISO_PORT"; fail "sending failed"; }
    wait "$waiter" || true
    adb forward --remove "tcp:$ISO_PORT" >/dev/null 2>&1 || true
    i=0
    until adb shell "cat $T/tb-fetch.log" | grep -qE "complete|removed|refused|nobody|error"; do
        i=$((i + 1))
        [ "$i" -ge 90 ] && fail "tb-fetch did not finish; see $T/tb-fetch.log"
        sleep 2
    done
    adb shell "cat $T/tb-fetch.log" | tr -d '\r' | grep -v ' MB$'
    adb shell "cat $T/tb-fetch.log" | grep -q complete || fail "transfer incomplete"
    # tb-fetch hashes exactly the bytes it received (any size). A mismatch also
    # means another program on the tablet answered on the port first: the file
    # is removed, it must never be offered as an installation medium.
    theirs=$(adb shell "cat $T/tb-fetch.log" | tr -d '\r' | sed -n 's/.*blake2b-256 \([0-9a-f]\{64\}\)).*/\1/p')
    ours=$(python3 -c 'import hashlib, sys
h = hashlib.blake2b(digest_size=32)
with open(sys.argv[1], "rb") as f:
    for block in iter(lambda: f.read(1 << 20), b""):
        h.update(block)
print(h.hexdigest())' "$file")
    if [ -z "$theirs" ] || [ "$theirs" != "$ours" ]; then
        adb shell "rm -f $ISO/$name"
        fail "checksum of $name on the tablet does not match: file removed"
    fi
    say "$name on the tablet, checksum matches (BLAKE2b-256)"
    adb shell "chmod 640 $ISO/$name"
}

cmd_usb() {
    need_device
    case "${1:-}" in
        on) [ -n "${2:-}" ] || fail "usage: usb on NAME [disk|cd]"
            # USB re-connects while switching; the answer may get lost, status tells
            adb shell "$T/tb-sysd ask on $2 ${3:-disk}" 2>/dev/null | tr -d '\r' || true
            sleep 4
            adb wait-for-device
            adb shell "$T/tb-sysd ask status" | tr -d '\r' ;;
        off) adb shell "$T/tb-sysd ask off" 2>/dev/null | tr -d '\r' || true
            sleep 4
            adb wait-for-device
            adb shell "$T/tb-sysd ask status" | tr -d '\r' ;;
        status) adb shell "$T/tb-sysd ask status" | tr -d '\r' ;;
        *) fail "usage: usb on NAME [disk|cd] | usb off | usb status" ;;
    esac
}

cmd_transfer() {
    need_device
    case "${1:-}" in
        create)
            # tb-sysd makes it on the tablet with Android's newfs_msdos (no root
            # needed here, works with Android stopped too)
            adb shell "$T/tb-sysd ask transfer create ${2:-512}" | tr -d '\r' ;;
        give)
            # USB re-connects while switching; the answer may get lost, status tells
            adb shell "$T/tb-sysd ask transfer give" 2>/dev/null | tr -d '\r' || true
            sleep 4
            adb wait-for-device
            adb shell "$T/tb-sysd ask transfer status" | tr -d '\r' ;;
        take)
            adb shell "$T/tb-sysd ask transfer take ${2:-}" 2>/dev/null | tr -d '\r' || true
            sleep 4
            adb wait-for-device
            adb shell "$T/tb-sysd ask transfer status" | tr -d '\r' ;;
        status) adb shell "$T/tb-sysd ask transfer status" | tr -d '\r' ;;
        *) fail "usage: transfer create [MB] | give | take [force] | status" ;;
    esac
}

cmd_install() {
    cmd_tools
    cmd_image
    root "sh $D/linux-root.sh mount"
    cmd_overlay
    root "sh $D/linux-root.sh mount"          # now with the system's own /dev, /proc, ...
    if [ "$(root "[ -f $D/root/debootstrap/debootstrap ] && echo yes")" = "yes" ]; then
        cmd_task stage2
        root "sh $D/linux-root.sh mount"      # debootstrap unmounts /proc and /sys at its end
    fi
    cmd_task base
    cmd_task packages
    cmd_task ui
    root "sh $D/linux-root.sh status"
}

case "${1:-}" in
    install) cmd_install ;;
    tools) cmd_tools ;;
    send) cmd_send ;;
    image) cmd_image ;;
    overlay) cmd_overlay ;;
    mount|umount|stop|status) need_root; root "sh $D/linux-root.sh $1" ;;
    daemon) cmd_daemon "${2:-}" ;;
    task) cmd_task "${2:?task name}" ;;
    iso-send) cmd_iso_send "${2:?file}" ;;
    iso-list) need_device; adb shell "ls -l $ISO" | tr -d '\r' ;;
    iso-delete) need_device; valid_image_name "${2:?name}" || fail "invalid name"
                adb shell "rm $ISO/$2" | tr -d '\r' ;;
    usb) shift; cmd_usb "$@" ;;
    transfer) shift; cmd_transfer "$@" ;;
    *) sed -n '2,/^set -eu/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 2 ;;
esac
