#!/bin/sh
# Lifeline check (docs/PLAN.md, section 8): as long as all five lines
# say OK, every file change on the tablet can still be undone.
# Prints one line per check, exits 0 only if all are OK.
# adb shell on Android 4.4 does not pass exit codes, so the checks echo tokens.
set -u
. "$(dirname "$0")/adb-serial.sh"

fail=0

report() {  # report <ok:0|1> <text>
    if [ "$1" -eq 0 ]; then
        echo "OK      $2"
    else
        echo "ERROR   $2"
        fail=1
    fi
}

ash() {  # run a command on the tablet as root, strip carriage returns
    adb shell "su -c '$1'" 2>/dev/null | tr -d '\r'
}

state=$(adb get-state 2>/dev/null | tr -d '\r')
[ "$state" = "device" ]
report $? "1 adb get-state = device (is: '${state:-no answer}')"

case "$(ash id)" in
    *uid=0*) report 0 "2 su gives uid=0" ;;
    *)       report 1 "2 su gives uid=0" ;;
esac

[ "$(ash '[ -s /data/misc/adb/adb_keys ] && echo NONEMPTY')" = "NONEMPTY" ]
report $? "3 /data/misc/adb/adb_keys exists and is not empty"

usbcfg=$(adb shell 'getprop persist.sys.usb.config' 2>/dev/null | tr -d '\r')
case "$usbcfg" in
    *adb*) report 0 "4 persist.sys.usb.config contains adb (is: '$usbcfg')" ;;
    *)     report 1 "4 persist.sys.usb.config contains adb (is: '$usbcfg')" ;;
esac

[ "$(ash '[ -e /system/xbin/su ] && [ -e /system/bin/sh ] && echo BOTH')" = "BOTH" ]
report $? "5 /system/xbin/su and /system/bin/sh exist"

exit $fail
