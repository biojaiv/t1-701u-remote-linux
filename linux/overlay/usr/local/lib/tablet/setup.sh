#!/bin/sh
# One-time setup of the Linux system on the tablet, after debootstrap's
# second stage. Runs as root inside the chroot; may be run again (each step
# checks whether it is already done).
#
#   setup.sh base       users, groups, language, time zone
#   setup.sh packages   window system, touch interface and programs (apt, network)
#   setup.sh ui         defaults of the touch interface (gsettings schemas)
set -eu

USER_NAME=tablet
USER_ID=5000

group() {  # group NAME GID
    getent group "$1" >/dev/null || groupadd -g "$2" "$1"
}

base() {
    # Android's kernel lets only members of these groups open network sockets
    # (ANDROID_PARANOID_NETWORK); the ids are fixed by Android
    group aid_inet 3003
    group aid_net_raw 3004
    group input 1004
    # apt downloads as its own user _apt and keeps only its primary group when
    # it drops privileges, so aid_inet must be that primary group
    usermod -g aid_inet -a -G aid_inet,aid_net_raw _apt

    if ! getent passwd "$USER_NAME" >/dev/null; then
        groupadd -g "$USER_ID" "$USER_NAME"
        useradd -u "$USER_ID" -g "$USER_ID" -G video,audio,input,aid_inet \
            -m -d "/home/$USER_NAME" -s /bin/bash -c "Tablet" "$USER_NAME"
        passwd -l "$USER_NAME" >/dev/null    # no password: nobody logs in, the session is started
    fi

    echo tablet > /etc/hostname
    printf '127.0.0.1\tlocalhost tablet\n::1\t\tlocalhost ip6-localhost ip6-loopback\n' > /etc/hosts

    sed -i 's/^# *\(de_DE.UTF-8 UTF-8\)/\1/; s/^# *\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen
    locale-gen >/dev/null
    echo 'LANG=de_DE.UTF-8' > /etc/default/locale

    ln -sf /usr/share/zoneinfo/Europe/Berlin /etc/localtime
    echo Europe/Berlin > /etc/timezone
    echo "base: done"
}

packages() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    # window system on the framebuffer, touch interface
    apt-get install -y xserver-xorg-core xserver-xorg-video-fbdev xserver-xorg-input-libinput \
        xauth x11-xserver-utils x11-utils xinput xfonts-base fonts-dejavu-core dbus dbus-x11 \
        matchbox-window-manager matchbox-desktop onboard at-spi2-core gir1.2-atspi-2.0
    # programs
    apt-get install -y lxterminal pcmanfm mousepad netsurf-gtk
    # pictures, music and video (GStreamer, no GPU: small videos only), PDF,
    # camera attempt (guvcview, v4l-utils) and sound tools for testing
    apt-get install -y ristretto parole gstreamer1.0-plugins-good gstreamer1.0-alsa \
        gstreamer1.0-libav atril guvcview v4l-utils alsa-utils
    apt-get clean
    echo "packages: done"
}

# defaults for onboard and others: 90_tablet.gschema.override (there is no
# dconf, so the compiled defaults are the settings)
ui() {
    /usr/lib/arm-linux-gnueabihf/glib-2.0/glib-compile-schemas /usr/share/glib-2.0/schemas
    echo "ui: done"
}

case "${1:-}" in
    base) base ;;
    packages) packages ;;
    ui) ui ;;
    *) echo "usage: $0 base | packages | ui" >&2; exit 2 ;;
esac
