#!/system/bin/sh
# Root helper for tests (phases 1 to 3). scripts/android.sh starts it as root
# before Android is stopped, because KingRoot's su stops working then.
# It performs one fixed action when the request file appears: start Android
# again and switch USB back to its normal mode. It never executes file contents.
# must outlive the su session that started it (the session end sends SIGHUP)
trap '' HUP
REQUEST=/data/local/tmp/tabletd-request
PIDFILE=/data/local/tmp/tabletd-root-helper.pid

rm -f $REQUEST
echo $$ > $PIDFILE
chmod 644 $PIDFILE
while :; do
    if [ -f $REQUEST ]; then
        rm -f $REQUEST $PIDFILE
        start
        sleep 3
        # back to the tablet's normal USB mode (adb only since 2026-11-16)
        setprop sys.usb.config $(getprop persist.sys.usb.config)
        exit 0
    fi
    sleep 1
done
