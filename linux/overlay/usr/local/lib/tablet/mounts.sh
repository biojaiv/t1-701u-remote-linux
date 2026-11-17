#!/bin/sh
# Mounts inside the Linux system on the tablet. Runs as root inside the chroot
# (tb-chroot), called by linux-root.sh after the image itself is mounted.
#
# /dev is the system's own small tmpfs with only the devices it needs, not a
# copy of Android's /dev: no block devices, no binder, no modem. The owners
# are this system's groups (video, input, audio), so the session user gets
# exactly screen, touch/keys and sound.
#
# Usage: mounts.sh up | down
set -eu
umask 022

node() {  # node NAME SYSFS-DEV-FILE GROUP MODE
    [ -r "$2" ] || return 0
    dev=$(cat "$2")
    rm -f "$1"
    mknod "$1" c "${dev%%:*}" "${dev##*:}"
    chmod "$4" "$1"
    # before setup.sh has run, the groups do not exist yet: root only until then
    chgrp "$3" "$1" 2>/dev/null || true
}

up() {
    mountpoint -q /proc || mount -t proc -o nosuid,nodev,noexec proc /proc
    mountpoint -q /sys || mount -t sysfs -o nosuid,nodev,noexec,ro sysfs /sys
    mountpoint -q /dev || mount -t tmpfs -o nosuid,mode=0755,size=512k dev /dev
    mkdir -p /dev/pts /dev/shm /dev/input /dev/snd
    chmod 755 /dev/input /dev/snd

    for n in null:1:3 zero:1:5 full:1:7 random:1:8 urandom:1:9 tty:5:0; do
        name=${n%%:*}; rest=${n#*:}
        [ -c "/dev/$name" ] || mknod -m 0666 "/dev/$name" c "${rest%%:*}" "${rest##*:}"
    done
    node /dev/fb0 /sys/class/graphics/fb0/dev video 0660
    for d in /sys/class/input/event*; do
        node "/dev/input/${d##*/}" "$d/dev" input 0660
    done
    for d in /sys/class/sound/*; do
        case ${d##*/} in controlC*|pcmC*|timer) node "/dev/snd/${d##*/}" "$d/dev" audio 0660 ;; esac
    done

    if ! mountpoint -q /dev/pts; then
        # a private instance if the kernel can (it cannot: Huawei's 3.10 has a
        # single devpts, shared with Android; its terminals stay root/shell-only)
        mount -t devpts -o newinstance,ptmxmode=0666,mode=0620,gid=5,nosuid,noexec devpts /dev/pts
    fi
    rm -f /dev/ptmx
    if [ -c /dev/pts/ptmx ]; then
        ln -s pts/ptmx /dev/ptmx
    else
        mknod -m 0666 /dev/ptmx c 5 2
    fi
    ln -sfn /proc/self/fd /dev/fd
    ln -sfn /proc/self/fd/0 /dev/stdin
    ln -sfn /proc/self/fd/1 /dev/stdout
    ln -sfn /proc/self/fd/2 /dev/stderr
    mountpoint -q /dev/shm || mount -t tmpfs -o nosuid,nodev,mode=1777,size=64M shm /dev/shm
    mountpoint -q /tmp || mount -t tmpfs -o nosuid,nodev,mode=1777,size=128M tmp /tmp
    mountpoint -q /run || mount -t tmpfs -o nosuid,nodev,mode=0755,size=16M run /run
    mkdir -p /run/lock /run/dbus /tmp/.X11-unix
    chmod 1777 /run/lock /tmp/.X11-unix
    udev_data
}

# There is no udev here. libinput (the X touch driver) only takes devices that
# udev's database marks as input devices, so write those entries ourselves,
# in the format libudev reads (/run/udev/data/c<major>:<minor>).
udev_data() {
    mkdir -p /run/udev/data
    for d in /sys/class/input/event*; do
        [ -r "$d/dev" ] || continue
        name=$(cat "$d/device/name" 2>/dev/null || echo)
        props="E:ID_INPUT=1"
        case "$name" in
            *_ts|*touch*) props="$props
E:ID_INPUT_TOUCHSCREEN=1" ;;
            *keypad*|*keyboard*|*-keys*) props="$props
E:ID_INPUT_KEY=1" ;;
            *) continue ;;   # sensors and the like: not for X
        esac
        printf 'I:1000000\n%s\n' "$props" > "/run/udev/data/c$(cat "$d/dev")"
    done
}

down() {
    for m in /run /tmp /dev/shm /dev/pts /dev /sys /proc; do
        if mountpoint -q "$m"; then
            umount "$m" || umount -l "$m"
        fi
    done
}

case "${1:-}" in
    up) up ;;
    down) down ;;
    *) echo "usage: $0 up | down" >&2; exit 2 ;;
esac
