#!/system/bin/sh
# Boot hook of the tablet project (phase 5, step P5.4, 2026-11-16).
# init's service chown_service (init.sc8830.rc, class main, root, oneshot)
# runs /system/bin/ext_chown.sh -c at every start; the file did not exist on
# this device. P5.4: write one log line only, to see that this runs as root.
echo "$(date '+%Y-%m-%d %H:%M:%S') P5.4 hook: $(id) args: $*" >> /data/tabletd/boot.log
