#!/bin/sh
# Build the tablet programs for ARM, copy them to the tablet, check them.
# Usage: scripts/push.sh
set -eu

cd "$(dirname "$0")/.."
. scripts/adb-serial.sh

make arm >/dev/null

state=$(adb get-state 2>/dev/null || true)
if [ "$state" != "device" ]; then
    echo "push: tablet not reachable (adb get-state: '${state:-none}')" >&2
    exit 1
fi

for name in tabletd tb-home tb-linux tb-audio; do
    bin=build/arm/$name
    dest=/data/local/tmp/$name
    adb push "$bin" "$dest.new" >/dev/null
    adb shell "chmod 755 $dest.new && mv $dest.new $dest"
    # toolbox on the tablet has md5 but no md5sum; compare against the local sum
    local_sum=$(md5sum "$bin" | cut -d' ' -f1)
    remote_sum=$(adb shell "md5 $dest" | tr -d '\r' | cut -d' ' -f1)
    if [ "$local_sum" != "$remote_sum" ]; then
        echo "push: checksum mismatch for $name (local $local_sum, tablet $remote_sum)" >&2
        exit 1
    fi
    echo "push: OK $dest md5=$local_sum"
done
