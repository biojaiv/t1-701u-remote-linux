# Sourced by the other scripts. adb refuses to guess when the tablet is known
# twice (USB and Wi-Fi), so pick the USB connection unless ANDROID_SERIAL is
# already set.
if [ -z "${ANDROID_SERIAL:-}" ]; then
    _usb=$(adb devices -l 2>/dev/null | awk 'NR > 1 && /usb:/ && $2 == "device" {print $1}')
    if [ -n "$_usb" ] && [ "$(echo "$_usb" | wc -l)" -eq 1 ]; then
        export ANDROID_SERIAL="$_usb"
    fi
    unset _usb
fi
