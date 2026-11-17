#!/bin/sh
# Installs a QR code scanner on the tablet that joins Wi-Fi networks from the
# Wi-Fi QR codes found in cafés, hotels and stations ("WIFI:S:name;T:WPA;P:...;;"):
# ZXing Barcode Scanner 4.7.8, the last release of the ZXing project's own app
# (Apache 2.0, https://github.com/zxing/zxing, release BS-4.7.8). It needs
# Android 4.4 (minSdkVersion 19), supports WPA/WPA2, WEP, open, WPA2-EAP and
# hidden networks, and after a scan offers "Connect to network": Android then
# stores and joins the network. This project does not ship the APK; the script
# downloads exactly this file from the official release and refuses it if the
# SHA-256 differs from the one recorded here.
#
# Checked when it was added: v1 signature valid for all files (old SHA-1 scheme,
# which current JDKs only verify with legacy algorithms allowed), certificate
# "CN=Daniel Switkin, OU=ZXing Team, O=Google" (2008), SHA-256 fingerprint
# 00:DE:94:16:A1:1F:BF:28:8A:48:AE:28:32:60:06:B1:21:58:3B:DE:DC:D2:97:35:32:6A:3D:66:25:13:7D:D5,
# no native code.
#
# Usage: scripts/install-qr-scanner.sh     (tablet on USB, Android running)
# Remove: adb uninstall com.google.zxing.client.android
set -eu
cd "$(dirname "$0")/.."
. scripts/adb-serial.sh

URL=https://github.com/zxing/zxing/releases/download/BS-4.7.8/BarcodeScanner-4.7.8.apk
SHA256=4d8c739e115e7aaf824dda3f2a55667459c48c2f140d15eadfd6d3d6271bb0b3
DIR=build/qr
APK=$DIR/BarcodeScanner-4.7.8.apk

say() { echo "qr-scanner: $*"; }
fail() { echo "qr-scanner: $*" >&2; exit 1; }

[ "$(adb get-state 2>/dev/null | tr -d '\r')" = device ] || fail "tablet not reachable over USB"
[ "$(adb shell getprop init.svc.zygote | tr -d '\r')" = running ] || fail "Android must be running"

mkdir -p "$DIR"
if [ ! -f "$APK" ] || [ "$(sha256sum "$APK" | cut -d' ' -f1)" != "$SHA256" ]; then
    say "downloading $URL"
    curl -fsSL --proto '=https' --proto-redir '=https' -o "$APK.part" "$URL"
    mv "$APK.part" "$APK"
fi
got=$(sha256sum "$APK" | cut -d' ' -f1)
if [ "$got" != "$SHA256" ]; then
    rm -f "$APK"
    fail "SHA-256 mismatch ($got): file removed, nothing installed"
fi
say "checksum matches"
adb install -r "$APK" | tr -d '\r' | tail -n 1
say "installed: the app \"Barcode Scanner\" is in Android's app list; place it next to"
say "\"Tablet System\" on the home screen by long-pressing and dragging it"
