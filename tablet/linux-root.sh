#!/system/bin/sh
# Root side of the Linux system (Devuan) on the tablet. scripts/linux.sh runs
# it with su while Android runs; KingRoot's su does not work without Android.
# It lives with the image in /data/tabletd/linux, which only root can enter.
#
#   linux-root.sh mount            mount the image, then the system's own /dev, /proc, ...
#   linux-root.sh umount           stop the session daemon and unmount everything
#   linux-root.sh daemon           start tb-linuxd (the session daemon) if it does not run
#   linux-root.sh stop             stop tb-linuxd and every process of the session user
#   linux-root.sh status
#   linux-root.sh task NAME        start a fixed long job inside the system in the
#                                  background: stage2, base, packages, ui, upgrade
#   linux-root.sh task-state NAME  "running", "done N" (exit status) or "none"
#   linux-root.sh overlay FILE     unpack a tar archive (from the laptop) into the system
set -u

D=/data/tabletd/linux
IMG=$D/devuan.img
R=$D/root
# logs: readable for the shell user too, because without Android there is no su
LOG=/data/tabletd/log
# installation images: the shell user writes them (tb-fetch), the session
# user's group reads them, tb-sysd offers them over USB
ISO=/data/tabletd/iso
# the transfer drive (FAT image): the session's ~/Transfer, or a USB drive for
# the computer (scripts/linux.sh transfer, tb-sysd)
TRANSFER=/data/tabletd/transfer.img
DAEMON_LOG=$LOG/linuxd.log
SYSD_LOG=$LOG/sysd.log
CAMERA_LOG=$LOG/camera.log
SESSION_LOG=$LOG/session.log
SESSION_UID=5000

mounted() { grep -q " $1 " /proc/mounts; }

in_system() { "$D/tb-chroot" "$R" "$@"; }

pid_from_log() {  # pid_from_log LOGFILE PROGRAM: pid if that program still runs
    w1= w2= pid=
    [ -f "$1" ] && read -r w1 w2 pid rest < "$1"
    if [ "$w1 $w2" != "$2 pid" ]; then
        # no pid in the log (emptied, or lost): look among the processes, so
        # a running daemon is never started a second time
        for exe in /proc/[0-9]*/exe; do
            case "$(readlink "$exe" 2>/dev/null)" in
                "$D/$2"|"$D/$2 (deleted)") exe=${exe#/proc/}; echo "${exe%/exe}"; return 0 ;;
            esac
        done
        return 0
    fi
    # "(deleted)": the program file was replaced (scripts/linux.sh tools) while
    # it ran; it still runs. Missing that started a second tb-linuxd, which
    # emptied the running session's logs first (2026-11-18)
    case "$(readlink /proc/$pid/exe 2>/dev/null)" in
        "$D/$2"|"$D/$2 (deleted)") echo "$pid" ;;
    esac
}

daemon_pid() { pid_from_log "$DAEMON_LOG" tb-linuxd; }
sysd_pid() { pid_from_log "$SYSD_LOG" tb-sysd; }
camera_pid() { pid_from_log "$CAMERA_LOG" tb-camera; }

session_processes() {   # number of processes of the session user
    n=0
    for s in /proc/[0-9]*/status; do
        while read -r key real rest; do
            if [ "$key" = "Uid:" ]; then
                [ "$real" = "$SESSION_UID" ] && n=$((n + 1))
                break
            fi
        done 2>/dev/null < "$s"
    done
    echo "$n"
}

kill_session_user() {
    mounted "$R" && mounted "$R/proc" && in_system /usr/bin/pkill -KILL -u "$SESSION_UID"
    return 0
}

do_mount() {
    if ! mounted "$R"; then
        [ -f "$IMG" ] || { echo "linux-root: no image $IMG" >&2; exit 1; }
        mkdir -p "$R"
        mount -t ext4 -o loop,noatime,nosuid,nodev "$IMG" "$R" || exit 1
    fi
    if [ -x "$R/usr/local/lib/tablet/mounts.sh" ]; then
        in_system /usr/local/lib/tablet/mounts.sh up || exit 1
    fi
    # the image folder, read-only inside the system as /media/iso
    mkdir -p "$ISO" "$R/media/iso"
    chown 2000:5000 "$ISO"
    chmod 750 "$ISO"
    if ! mounted "$R/media/iso"; then
        mount -o bind "$ISO" "$R/media/iso" && mount -o remount,bind,ro "$ISO" "$R/media/iso"
    fi
    # name servers for apt and programs: Android's current ones
    dns1=$(getprop net.dns1)
    dns2=$(getprop net.dns2)
    if [ -n "$dns1" ]; then
        { echo "nameserver $dns1"; [ -n "$dns2" ] && echo "nameserver $dns2"; } > "$R/etc/resolv.conf"
    fi
    echo "linux-root: mounted"
}

do_umount() {
    do_stop
    if mounted "$R"; then
        mounted "$R/media/iso" && umount "$R/media/iso"
        # normally tb-sysd unmounted it when it was stopped
        mounted "$R/home/tablet/Transfer" && umount "$R/home/tablet/Transfer"
        [ -x "$R/usr/local/lib/tablet/mounts.sh" ] && in_system /usr/local/lib/tablet/mounts.sh down
        umount "$R" || { echo "linux-root: $R is busy" >&2; exit 1; }
    fi
    echo "linux-root: unmounted"
}

do_daemon() {
    start_sysd
    start_camera
    if [ -n "$(daemon_pid)" ]; then
        echo "linux-root: tb-linuxd runs (pid $(daemon_pid))"
        return 0
    fi
    # (re)do the system's own mounts: they also write the device entries that
    # libinput needs, and an overlay may have changed mounts.sh since
    do_mount >/dev/null
    rotation=counterclockwise
    [ -f "$D/rotation" ] && read -r rotation < "$D/rotation"
    # root writes, the shell group (2000) reads; tb-linuxd keeps an existing file's mode
    mkdir -p "$LOG"
    chown 0:2000 "$LOG"
    chmod 750 "$LOG"
    for f in "$DAEMON_LOG" "$SESSION_LOG"; do
        : > "$f"
        chown 0:2000 "$f"
        chmod 640 "$f"
    done
    # groups: video 44, audio 29 (Debian), input 1004 and aid_inet 3003 (setup.sh)
    "$D/tb-linuxd" --root "$R" --user "$SESSION_UID:$SESSION_UID" --groups 44,29,1004,3003 \
        --env PATH=/usr/local/bin:/usr/bin:/bin --env HOME=/home/tablet --env USER=tablet \
        --env LOGNAME=tablet --env SHELL=/bin/bash --env LANG=de_DE.UTF-8 \
        --env "ROTATION=$rotation" --log "$SESSION_LOG" --daemon "$DAEMON_LOG" \
        -- /usr/local/bin/tablet-session || exit 1
    # it writes its pid only after setsid(): until then, ending this su session
    # could take it along (see do_task)
    i=0
    until [ -n "$(daemon_pid)" ]; do
        i=$((i + 1))
        [ "$i" -gt 25 ] && { echo "linux-root: tb-linuxd did not start" >&2; exit 1; }
        sleep 0.2
    done
    echo "linux-root: tb-linuxd runs (pid $(daemon_pid))"
}

# tb-sysd: USB installation medium and brightness for the session (see sysd.c)
start_sysd() {
    [ -n "$(sysd_pid)" ] && return 0
    mkdir -p "$LOG" "$ISO"
    : > "$SYSD_LOG"
    chown 0:2000 "$SYSD_LOG"
    chmod 640 "$SYSD_LOG"
    "$D/tb-sysd" --dir "$ISO" --transfer "$TRANSFER" --transfer-mount "$R/home/tablet/Transfer" \
        --daemon "$SYSD_LOG" || return 1
    i=0
    until [ -n "$(sysd_pid)" ]; do
        i=$((i + 1))
        [ "$i" -gt 25 ] && { echo "linux-root: tb-sysd did not start" >&2; return 1; }
        sleep 0.2
    done
    echo "linux-root: tb-sysd runs (pid $(sysd_pid))"
}

# tb-camera: the cameras for the session (Android's camera driver, see
# tablet/camera/camera.c); only if it was installed (built with the NDK)
start_camera() {
    [ -x "$D/tb-camera" ] || return 0
    [ -n "$(camera_pid)" ] && return 0
    "$D/tb-camera" --daemon "$CAMERA_LOG" || return 0
    chown 0:2000 "$CAMERA_LOG" 2>/dev/null
    i=0
    until [ -n "$(camera_pid)" ]; do
        i=$((i + 1))
        [ "$i" -gt 25 ] && { echo "linux-root: tb-camera did not start" >&2; return 0; }
        sleep 0.2
    done
    echo "linux-root: tb-camera runs (pid $(camera_pid))"
}

do_stop() {
    cpid=$(camera_pid)
    [ -n "$cpid" ] && kill "$cpid" && echo "linux-root: tb-camera stopped"
    pid=$(daemon_pid)
    [ -n "$pid" ] && kill "$pid"
    kill_session_user
    [ -n "$pid" ] && echo "linux-root: tb-linuxd stopped"
    spid=$(sysd_pid)
    [ -n "$spid" ] && kill "$spid" && echo "linux-root: tb-sysd stopped"
    return 0
}

do_status() {
    if mounted "$R"; then echo "image: mounted"; else echo "image: not mounted"; fi
    if mounted "$R/proc"; then echo "system mounts: up"; else echo "system mounts: down"; fi
    pid=$(daemon_pid)
    if [ -n "$pid" ]; then echo "daemon: runs (pid $pid)"; else echo "daemon: not running"; fi
    spid=$(sysd_pid)
    if [ -n "$spid" ]; then echo "tb-sysd: runs (pid $spid)"; else echo "tb-sysd: not running"; fi
    echo "session processes: $(session_processes)"
}

# long jobs run detached inside the system (they survive the end of the su
# session and a dropped USB connection); output in /var/log/tablet-NAME.log
do_task() {
    name=$1
    case "$name" in
        stage2) set -- /debootstrap/debootstrap --second-stage ;;
        base|packages|ui) set -- /usr/local/lib/tablet/setup.sh "$name" ;;
        upgrade) set -- /bin/sh -c 'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade && apt-get clean' ;;
        *) echo "linux-root: unknown task $name" >&2; exit 2 ;;
    esac
    mounted "$R" || { echo "linux-root: the system is not mounted" >&2; exit 1; }
    rm -f "$R/var/log/tablet-$name.rc" "$R/var/log/tablet-$name.log"
    echo "running" > "$R/var/log/tablet-$name.state"
    in_system /usr/bin/setsid -f /bin/sh -c \
        '"$@" > /var/log/tablet-'"$name"'.log 2>&1; echo $? > /var/log/tablet-'"$name"'.rc' \
        task "$@" < /dev/null > /dev/null 2>&1
    # With one CPU online the detached child often runs only after this script
    # has ended, and KingRoot's su then kills it before its setsid(). The log
    # exists once the child has its own session, so wait for it.
    i=0
    until [ -f "$R/var/log/tablet-$name.log" ]; do
        i=$((i + 1))
        if [ "$i" -gt 50 ]; then
            rm -f "$R/var/log/tablet-$name.state"
            echo "linux-root: task $name did not start" >&2
            exit 1
        fi
        sleep 0.2
    done
    echo "linux-root: task $name started"
}

do_task_state() {
    if [ -f "$R/var/log/tablet-$1.rc" ]; then
        read -r rc < "$R/var/log/tablet-$1.rc"
        echo "done $rc"
    elif [ -f "$R/var/log/tablet-$1.state" ]; then
        echo "running"
    else
        echo "none"
    fi
}

do_overlay() {
    mounted "$R" || { echo "linux-root: the system is not mounted" >&2; exit 1; }
    in_system /bin/tar -x -f - -C / --no-overwrite-dir < "$1" || exit 1
    echo "linux-root: overlay unpacked"
}

case "${1:-}" in
    mount) do_mount ;;
    umount) do_umount ;;
    daemon) do_daemon ;;
    stop) do_stop ;;
    status) do_status ;;
    task) do_task "${2:?task name}" ;;
    task-state) do_task_state "${2:?task name}" ;;
    overlay) do_overlay "${2:?archive}" ;;
    *) echo "usage: $0 mount | umount | daemon | stop | status | task NAME | task-state NAME | overlay FILE" >&2
       exit 2 ;;
esac
