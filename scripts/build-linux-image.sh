#!/bin/sh
# Builds the Linux system's image for the tablet on the laptop, without sudo:
# Devuan 6 "excalibur" for armhf, first stage of debootstrap under fakeroot,
# then an ext4 image with only the features kernel 3.10 knows.
#
# Trust: the release index is checked against the Excalibur signing key, whose
# fingerprint is fixed below (published on https://www.devuan.org/os/keyring);
# debootstrap then checks every package against the signed index.
# The second stage runs on the tablet (scripts/linux.sh install).
#
# Size: below 2 GiB. Copying a 3 GB image with adb push broke off after about
# 2 GiB (368 s at about 5.8 MB/s); presumably Android 4.4's adbd cannot write
# larger files (inferred from the timing, not proven).
#
# Needs: curl, gpg, fakeroot, dpkg-deb, /usr/sbin/mke2fs (e2fsprogs >= 1.43).
# Result: build/linux/devuan.img (1900 MiB, sparse on the laptop)
#
# Usage: scripts/build-linux-image.sh [--image-only]
#        --image-only: only step 5, from the existing build/linux/rootfs
set -eu

cd "$(dirname "$0")/.."
L=$PWD/build/linux
MIRROR=https://pkgmaster.devuan.org/merged
SUITE=excalibur
KEY_FPR=9F8D6C74DE661075FD171BE3B3982868D104092C
SIZE=1900M
INCLUDE=ca-certificates,iproute2,iptables,procps,nano,less,locales,tzdata,e2fsprogs

image() {
    # ext4 image; metadata_csum_seed and orphan_file are newer than kernel 3.10
    rm -f "$L/devuan.img"
    fakeroot -i "$L/fakeroot.state" -s "$L/fakeroot.state" -- /usr/sbin/mke2fs -q -t ext4 \
        -O ^metadata_csum,^metadata_csum_seed,^orphan_file -L devuan -d "$L/rootfs" "$L/devuan.img" "$SIZE"
    /usr/sbin/e2fsck -fn "$L/devuan.img" >/dev/null
    echo "build-linux-image: $L/devuan.img ($(du -h "$L/devuan.img" | cut -f1) used of $SIZE)"
}

if [ "${1:-}" = "--image-only" ]; then
    [ -d "$L/rootfs" ] && [ -f "$L/fakeroot.state" ] || { echo "build-linux-image: no rootfs yet" >&2; exit 1; }
    image
    exit 0
fi

mkdir -p "$L/download" "$L/tools"
export GNUPGHOME="$L/gnupg"
mkdir -p "$GNUPGHOME"
chmod 700 "$GNUPGHOME"

# 1. signing key from Devuan's key server, checked against the fixed fingerprint
curl -fsS --proto =https -o "$L/download/key.html" \
    "https://keyring.devuan.org/pks/lookup?op=get&search=0x$KEY_FPR"
sed -n '/-----BEGIN PGP PUBLIC KEY BLOCK-----/,/-----END PGP PUBLIC KEY BLOCK-----/p' \
    "$L/download/key.html" > "$L/download/key.asc"
gpg --quiet --import "$L/download/key.asc"
gpg --with-colons --fingerprint "$KEY_FPR" | grep -q "^fpr:::::::::$KEY_FPR:" ||
    { echo "build-linux-image: wrong key" >&2; exit 1; }
gpg --export "$KEY_FPR" > "$L/excalibur-keyring.gpg"

# 2. signed release index and package list
curl -fsS --proto =https -o "$L/download/InRelease" "$MIRROR/dists/$SUITE/InRelease"
gpg --status-fd 1 --verify "$L/download/InRelease" 2>/dev/null | grep -q "VALIDSIG $KEY_FPR" ||
    { echo "build-linux-image: release index not validly signed" >&2; exit 1; }
gpg --decrypt "$L/download/InRelease" 2>/dev/null > "$L/download/Release.verified"
curl -fsS --proto =https -o "$L/download/Packages.xz" "$MIRROR/dists/$SUITE/main/binary-armhf/Packages.xz"
want=$(awk '/^SHA256:/{s=1;next} /^[A-Z]/{s=0} s && $3=="main/binary-armhf/Packages.xz"{print $1}' \
    "$L/download/Release.verified")
[ "$want" = "$(sha256sum "$L/download/Packages.xz" | cut -d' ' -f1)" ] ||
    { echo "build-linux-image: package list does not match" >&2; exit 1; }
xz -dkf "$L/download/Packages.xz"

# 3. Devuan's debootstrap (knows excalibur), checked against the package list
entry=$(awk '$0=="Package: debootstrap"{s=1} s&&/^(Filename|SHA256):/{print $2} s&&/^$/{exit}' \
    "$L/download/Packages")
file=$(echo "$entry" | sed -n 1p)
sum=$(echo "$entry" | sed -n 2p)
curl -fsS --proto =https -o "$L/download/debootstrap.deb" "$MIRROR/$file"
echo "$sum  $L/download/debootstrap.deb" | sha256sum -c --quiet
rm -rf "$L/tools/debootstrap"
dpkg-deb -x "$L/download/debootstrap.deb" "$L/tools/debootstrap"

# 4. first stage: download, check and unpack the base packages (owners faked)
rm -rf "$L/rootfs" "$L/fakeroot.state"
fakeroot -s "$L/fakeroot.state" -- env PATH="/usr/sbin:/sbin:$PATH" \
    DEBOOTSTRAP_DIR="$L/tools/debootstrap/usr/share/debootstrap" \
    "$L/tools/debootstrap/usr/sbin/debootstrap" --foreign --arch=armhf --variant=minbase \
    --keyring="$L/excalibur-keyring.gpg" --include="$INCLUDE" "$SUITE" "$L/rootfs" "$MIRROR" \
    > "$L/debootstrap-stage1.log" 2>&1 ||
    { tail -5 "$L/debootstrap-stage1.log" >&2; exit 1; }
grep -q "Valid Release signature (key id $KEY_FPR)" "$L/debootstrap-stage1.log"

# 5. the image
image
