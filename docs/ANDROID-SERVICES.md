# Android services of the tablet (step P5.3)

All services defined in the init scripts (`init.rc`, `init.sc8830.rc`, `init.board.rc`) of the
T1-701u's stock Android 4.4.2, with their state in two situations: "running" = Android is running,
"paused" = after `scripts/android.sh stop`. The table was generated with `scripts/init-services.py` from
the device's `*.rc` files (the script reads your own copies from `device/init/`; they are not shipped) and two dumps of
`adb shell getprop` (the `init.svc.*` properties). Program command lines are cut off after 60
characters. "–" in the state columns means the service had no `init.svc.*` property in that dump
(typically: not started since boot).

Column "critical": if such a service crashes more than four times within four minutes, `init`
reboots into recovery.

The script prints German column headers; the table below is translated.

| Service | Program | Class | User | critical | Flags | File | running | paused |
|---|---|---|---|---|---|---|---|---|
| `GPSenseEngine` | `/system/bin/GPSenseEngine` | main | root | no | disabled | init.board.rc | – | – |
| `adbd` | `/sbin/adbd` | core | root | no | disabled | init.rc | running | running |
| `applogcat` | `/system/bin/logcat -b main -b system -b events -v threadtime` | late_start | root | no | disabled | init.rc | stopped | stopped |
| `autotest` | `/system/bin/autotest` | main | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `batterysrv` | `/bin/batterysrv` | core | system | no | oneshot | init.sc8830.rc | running | running |
| `batterytemp` | `/system/bin/batterytemp 480 500` | main | system | no | – | init.board.rc | running | running |
| `bdtd` | `/system/bin/bdt` | core | root | no | disabled, oneshot | init.board.rc | – | – |
| `boardid` | `/system/bin/boardid` | main | root | no | oneshot | init.board.rc | stopped | stopped |
| `bootanim` | `/system/bin/bootanimation` | main | graphics | no | disabled, oneshot | init.rc | stopped | stopped |
| `bt_aging` | `/system/bin/bt_aging` | core | root | no | disabled, oneshot | init.board.rc | – | – |
| `btld` | `/system/bin/btld -lpm 0 -hb 3000000` | default | root | no | disabled, oneshot | init.board.rc | – | – |
| `charge` | `/bin/charge` | charger | root | no | oneshot | init.sc8830.rc | – | – |
| `chown_service` | `/system/bin/ext_chown.sh -c` | main | root | no | oneshot | init.sc8830.rc | – | – |
| `cmd_services` | `/system/bin/cmd_services` | main | root | no | disabled, oneshot | init.rc | stopped | stopped |
| `console` | `/system/bin/sh` | core | shell | no | disabled | init.rc | – | – |
| `data_off` | `/system/bin/ext_data.sh -d` | default | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `data_on` | `/system/bin/ext_data.sh -u` | default | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `datarecovery` | `/system/bin/datarecovery` | sprd | root | no | disabled, oneshot | init.rc | – | – |
| `debuggerd` | `/system/bin/debuggerd` | main | root | no | – | init.rc | running | running |
| `dhcpcd_bnep0` | `/system/bin/dhcpcd -ABKL` | main | root | no | disabled, oneshot | init.board.rc | – | – |
| `dhcpcd_bt-pan` | `/system/bin/dhcpcd -BKLG` | default | root | no | disabled, oneshot | init.board.rc | – | – |
| `dhcpcd_p2p` | `/system/bin/dhcpcd -ABKL` | default | root | no | disabled, oneshot | init.board.rc | – | – |
| `dhcpcd_wlan0` | `/system/bin/dhcpcd -ABKL` | default | root | no | disabled, oneshot | init.board.rc | – | – |
| `drm` | `/system/bin/drmserver` | main | drm | no | – | init.rc | running | running |
| `dumpstate` | `/system/bin/dumpstate -s` | main | root | no | disabled, oneshot | init.rc | – | – |
| `engpcclientw` | `/system/bin/engpc -t 0 -a /dev/ttyGS0 -d /dev/vser` | core | root | no | oneshot, disabled | init.board.rc | running | running |
| `flash_recovery` | `/system/etc/install-recovery.sh` | main | root | no | oneshot | init.rc | stopped | stopped |
| `fuse_internalsd` | `/system/bin/sdcard -u 1023 -g 1023 -w 1051 -d /mnt/media_rw/` | late_start | root | no | disabled | init.sc8830.rc | – | – |
| `fuse_sdcard0` | `/system/bin/sdcard -u 1023 -g 1023 -d /mnt/media_rw/sdcard0 ` | late_start | root | no | disabled | init.sc8830.rc | – | – |
| `fuse_sdcard1` | `/system/bin/sdcard -u 1023 -g 1023 -w 1023 -d /mnt/media_rw/` | late_start | root | no | disabled | init.sc8830.rc | – | – |
| `fuse_sdcard1rw` | `/system/bin/sdcard -u 1023 -g 1023 /mnt/media_rw/sdcard1 /st` | late_start | root | no | disabled | init.sc8830.rc | – | – |
| `fuse_usbdisk` | `/system/bin/sdcard -u 1023 -g 1023 -d /mnt/media_rw/usbdisk ` | late_start | root | no | disabled | init.sc8830.rc | – | – |
| `healthd` | `/sbin/healthd` | core | root | yes | – | init.rc | running | running |
| `healthd-charger` | `/sbin/healthd -n` | charger | root | yes | – | init.rc | – | – |
| `hfag` | `/system/bin/sdptool add --channel=10 HFAG` | default | bluetooth | no | disabled, oneshot | init.board.rc | – | – |
| `hsag` | `/system/bin/sdptool add --channel=11 HSAG` | default | bluetooth | no | disabled, oneshot | init.board.rc | – | – |
| `hw_dmd_server` | `/system/bin/device_monitor` | main | root | no | oneshot | init.sc8830.rc | running | running |
| `hwnffserver` | `/system/bin/hwnffserver -t` | main | root | no | – | init.sc8830.rc | running | running |
| `hwpged` | `/system/bin/hwpged` | main | root | no | – | init.sc8830.rc | running | running |
| `inputfreq` | `/system/bin/inputfreq.sh` | default | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `installd` | `/system/bin/installd` | main | root | no | – | init.rc | running | running |
| `iprenew_bnep0` | `/system/bin/dhcpcd -n` | main | root | no | disabled, oneshot | init.board.rc | – | – |
| `iprenew_bt-pan` | `/system/bin/dhcpcd -n` | default | root | no | disabled, oneshot | init.board.rc | – | – |
| `iprenew_p2p` | `/system/bin/dhcpcd -n` | main | root | no | disabled, oneshot | init.board.rc | – | – |
| `iprenew_wlan0` | `/system/bin/dhcpcd -n` | main | root | no | disabled, oneshot | init.board.rc | – | – |
| `jankservice` | `/system/bin/jankservice` | main | root | no | – | init.rc | running | running |
| `keystore` | `/system/bin/keystore /data/misc/keystore` | main | keystore | no | – | init.rc | running | running |
| `kill_l_phone` | `/system/bin/ext_kill.sh -l` | default | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `kill_td_phone` | `/system/bin/ext_kill.sh -t` | default | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `kill_w_phone` | `/system/bin/ext_kill.sh -w` | default | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `kmsglogcat` | `/system/bin/kmsgcat -r 4096 -n 25 -f /data/android_logs/kmsg` | late_start | root | no | disabled | init.rc | stopped | stopped |
| `legacy_tempsd` | `/system/bin/sdcard -u 1023 -g 1023 -w 1051 -l /data/media/te` | default | root | no | disabled | init.sc8830.rc | – | – |
| `logserver` | `/system/bin/logserver` | main | root | no | oneshot | init.rc | running | running |
| `lril-daemon` | `/system/bin/rild_sp -l /system/lib/libreference-ril_sp.so -m` | core | root | no | disabled | init.sc8830.rc | – | – |
| `mdnsd` | `/system/bin/mdnsd` | main | mdnsr | no | disabled, oneshot | init.rc | – | – |
| `media` | `/system/bin/mediaserver` | main | media | no | – | init.rc | running | running |
| `modem_control` | `/system/bin/modem_control` | core | root | no | oneshot | init.sc8830.rc | stopped | stopped |
| `modemd` | `/system/bin/modemd` | core | system | no | oneshot | init.sc8830.rc | running | running |
| `mtpd` | `/system/bin/mtpd` | main | vpn | no | disabled, oneshot | init.rc | – | – |
| `netd` | `/system/bin/netd` | main | root | no | – | init.rc | running | running |
| `nvitemd_l` | `/system/bin/nvitemd l 0` | core | system | no | disabled, oneshot | init.sc8830.rc | – | – |
| `nvitemd_td` | `/system/bin/nvitemd t 0` | core | system | no | disabled, oneshot | init.sc8830.rc | – | – |
| `nvitemd_w` | `/system/bin/nvitemd w 0` | core | system | no | disabled, oneshot | init.sc8830.rc | running | running |
| `oeminfo_nvm` | `/sbin/oeminfo_nvm_server` | core | root | no | – | init.rc | running | running |
| `opush` | `/system/bin/sdptool add --channel=12 OPUSH` | default | bluetooth | no | disabled, oneshot | init.board.rc | – | – |
| `p2p_supplicant` | `/system/bin/wpa_supplicant \` | main | root | no | disabled, oneshot | init.board.rc | running | running |
| `pbap` | `/system/bin/sdptool add --channel=19 PBAP` | default | bluetooth | no | disabled, oneshot | init.board.rc | – | – |
| `phoneserver_l` | `/system/bin/phoneserver -m l` | core | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `phoneserver_t` | `/system/bin/phoneserver -m t` | core | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `phoneserver_w` | `/system/bin/phoneserver -m w` | core | root | no | disabled, oneshot | init.sc8830.rc | running | running |
| `poweroffalarm` | `/system/bin/poweroff_alarm` | default | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `racoon` | `/system/bin/racoon` | main | root | no | disabled, oneshot | init.rc | – | – |
| `recoveryfreq` | `/system/bin/recoveryfreq.sh` | default | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `refnotify_l` | `/system/bin/refnotify -t 2` | main | system | no | disabled | init.sc8830.rc | – | – |
| `refnotify_td` | `/system/bin/refnotify -t 0` | main | system | no | disabled | init.sc8830.rc | – | – |
| `refnotify_w` | `/system/bin/refnotify -t 1` | main | system | no | disabled | init.sc8830.rc | running | running |
| `renice_kmsgcat` | `/system/bin/sh /system/etc/renice-loggers.sh /system/bin/kms` | default | root | no | oneshot, disabled | init.rc | – | – |
| `renice_logcat` | `/system/bin/sh /system/etc/renice-loggers.sh /system/bin/log` | default | root | no | oneshot, disabled | init.rc | – | – |
| `renice_sleeplog` | `/system/bin/sh /system/etc/renice-loggers.sh /system/bin/sle` | default | root | no | oneshot, disabled | init.rc | – | – |
| `ril-daemon` | `/system/bin/rild` | main | root | no | – | init.rc | running | running |
| `rillogcat` | `/system/bin/logcat -b radio -v threadtime -r4096 -n 25 -f /d` | late_start | root | no | disabled | init.rc | stopped | stopped |
| `rootmonitor` | `/system/bin/rootmonitor` | late_start | root | no | oneshot | init.rc | stopped | stopped |
| `sdcard` | `/system/bin/sdcard -u 1023 -g 1023 -l /data/share /mnt/shell` | late_start | root | no | – | init.sc8830.rc | running | running |
| `servicemanager` | `/system/bin/servicemanager` | core | system | yes | – | init.rc | running | running |
| `set_mac` | `/system/bin/ext_chown.sh -e` | default | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `shutdownanim` | `/system/bin/shutdownanimation` | main | graphics | no | disabled, oneshot | init.rc | – | – |
| `sleeplogcat` | `/system/bin/sleeplogcat -t 10 -p /data/android_logs/` | late_start | root | no | disabled | init.rc | stopped | stopped |
| `slog` | `/system/bin/slog -t` | default | root | no | disabled | init.sc8830.rc | – | – |
| `smd_symlink` | `/system/bin/ext_symlink.sh -u` | default | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `sprd_res_monitor` | `/system/bin/sprd_res_monitor` | main | root | no | disabled | init.rc | – | – |
| `srid` | `/system/bin/srid` | main | root | no | oneshot | init.rc | running | running |
| `srtd` | `/system/xbin/srtd` | main | root | no | oneshot | init.sc8830.rc | – | – |
| `sshd` | `/system/bin/start-ssh` | main | root | no | disabled | init.rc | – | – |
| `surfaceflinger` | `/system/bin/surfaceflinger` | main | system | no | – | init.rc | running | stopped |
| `theme_init` | `/system/bin/theme_init.sh` | default | root | no | disabled, oneshot | init.sc8830.rc | – | – |
| `tpmd` | `/system/bin/tpmd` | core | root | no | disabled | init.board.rc | running | running |
| `tril-daemon` | `/system/bin/rild_sp -l /system/lib/libreference-ril_sp.so -m` | core | root | no | disabled | init.sc8830.rc | – | – |
| `tril-daemon1` | `/system/bin/rild_sp -l /system/lib/libreference-ril_sp.so -m` | core | root | no | disabled | init.sc8830.rc | – | – |
| `tril-daemon2` | `/system/bin/rild_sp -l /system/lib/libreference-ril_sp.so -m` | core | root | no | disabled | init.sc8830.rc | – | – |
| `ueventd` | `/sbin/ueventd` | core | root | yes | – | init.rc | running | running |
| `vold` | `/system/bin/vold` | core | root | no | – | init.rc | running | running |
| `wcnd` | `/system/bin/wcnd` | core | system | no | oneshot | init.sc8830.rc | running | running |
| `wifi_aging` | `/system/bin/wifi_aging` | core | root | no | disabled, oneshot | init.board.rc | – | – |
| `wpa_supplicant` | `/system/bin/wpa_supplicant \` | default | root | no | disabled, oneshot | init.board.rc | – | – |
| `wril-daemon` | `/system/bin/rild_sp -l /system/lib/libreference-ril_sp.so -m` | core | root | no | disabled | init.sc8830.rc | running | running |
| `wril-daemon1` | `/system/bin/rild_sp -l /system/lib/libreference-ril_sp.so -m` | core | root | no | disabled | init.sc8830.rc | – | – |
| `wril-daemon2` | `/system/bin/rild_sp -l /system/lib/libreference-ril_sp.so -m` | core | root | no | disabled | init.sc8830.rc | – | – |
| `zram` | `/system/xbin/zram.sh` | default | root | no | disabled, oneshot | init.sc8830.rc | stopped | stopped |
| `zygote` | `/system/bin/app_process -Xzygote /system/bin --zygote --star` | main | root | yes | – | init.rc | running | stopped |
