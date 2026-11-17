# Installation

Read the warning in the [README](../README.md) first. Everything below runs as your normal user on
the laptop; `sudo` is only needed to install missing packages, and the scripts never call it.

## 1. Prepare the tablet

1. Huawei MediaPad T1 7.0 **T1-701u** with Android 4.4.2. Charge it.
2. Root it with a tool of your choice so that `su` works for the shell (`adb shell su -c id` prints
   `uid=0`). The project was developed with KingRoot. This project does not provide a rooting tool.
3. Enable USB debugging (Settings → About tablet → tap "Build number" seven times → Developer
   options → USB debugging), connect the tablet by USB and accept the laptop's key on the tablet.
4. **Back up** your data. Optionally also back up the partitions with your own tools; the project
   does not write to them, but a backup is the only way back if something else goes wrong.

## 2. Build on the laptop

```sh
git clone https://github.com/biojaiv/t1-701u-remote-linux.git
cd t1-701u-remote-linux
./build.sh --deps        # lists missing packages and the apt command to install them
./build.sh --test        # builds build/arm/ (tablet) and build/x86/ (tests), runs all tests
```

On Debian/Ubuntu the packages are typically:

```sh
sudo apt install make gcc gcc-arm-linux-gnueabihf adb default-jdk-headless \
    python3 python3-numpy python3-pil python3-nacl python3-lz4 python3-psutil python3-gi \
    python3-dbus gir1.2-gstreamer-1.0 gstreamer1.0-tools gstreamer1.0-plugins-base \
    gstreamer1.0-pipewire
```

The tablet programs are linked statically for ARMv7 and need nothing from Android except the kernel.

## 3. Install the laptop side

```sh
scripts/install.sh
```

This checks programs and libraries (`huawai doctor`), creates `~/.config/huawai-companion/hostd.toml`,
installs and starts the user service `huawai-hostd` and links the `huawai` command into
`~/.local/bin` (make sure that is in your `PATH`).

## 4. First start and pairing

With Android running and the tablet connected by USB:

```sh
scripts/lifeline.sh      # must show five OK lines: adb, su, adb key, USB config, su/sh present
huawai up
```

The first `huawai up` copies the programs to the tablet, sets its firewall when the tablet is on
Wi-Fi (only the laptop may connect), creates the pairing key (it only ever travels over the cable), pauses Android and starts
the tablet's own start screen. On the laptop, the desktop asks once for permission to share the
screen and input devices; allow it. Tap "Remote control" on the tablet.

`huawai down` stops the own system and starts Android again. `huawai status` shows what runs where.
See [USAGE.md](USAGE.md) for daily use.

## 5. Optional: start from an Android icon or at boot

```sh
scripts/autostart.sh install
```

Builds a small starter app (signed with a key created in `build/autostart/`), installs it and copies
the programs to `/data/tabletd/bin`. Your root tool may ask on the tablet whether the app may use
root. Afterwards a **"Tablet System"** icon starts the own system with one tap, using the Wi-Fi
Android has set up. `scripts/autostart.sh mode mini` starts it at every boot instead;
`scripts/autostart.sh mode android` switches back. Details in [USAGE.md](USAGE.md).

## 6. Optional: Linux on the tablet

```sh
scripts/build-linux-image.sh     # about 6 minutes, downloads about 70 MB from Devuan, no sudo
scripts/linux.sh install         # Android running; copies 1.9 GB over Wi-Fi, sets up the system
```

The download is checked against Devuan's signing key. Afterwards the start screen shows a
**"Linux"** tile. Copy an ISO image for the USB installer with `scripts/linux.sh iso-send FILE`.
Everything about it, including updates, is in [LINUX.md](LINUX.md).

## 7. Optional: more free space on Android

[ANDROID-SLIMMING.md](ANDROID-SLIMMING.md) lists preinstalled apps that can be disabled
(`pm disable`, reversible with `pm enable`) and the ones that must stay.

## 8. Optional: Wi-Fi from QR codes

`scripts/install-qr-scanner.sh` installs a QR scanner that joins Wi-Fi networks from their QR codes,
see [USAGE.md](USAGE.md).

## 9. Optional: access from another network

Needs a firewall opening on the laptop and the router. See "From another network (internet)" in
[USAGE.md](USAGE.md); for Mullvad VPN users there is `scripts/mullvad-incoming.nft`.

## Uninstalling

```sh
huawai down                                   # Android back
scripts/autostart.sh remove                   # starter app away, mode android
systemctl --user disable --now huawai-hostd
rm ~/.config/systemd/user/huawai-hostd.service ~/.local/bin/huawai
rm -r ~/.config/huawai-companion
```

On the tablet, `/data/tabletd` and `/data/local/tmp/tb-*` hold the programs, the key and (if
installed) the Linux image; remove them with `adb shell su -c 'rm -r /data/tabletd'` after
`scripts/linux.sh umount`. Disabled apps come back with `adb shell su -c 'pm enable PACKAGE'`.
