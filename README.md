# t1-701u-remote-linux

Give an old **Huawei MediaPad T1 7.0 (model T1-701u)** a second life as a touch remote control and
second screen for a Linux laptop, with its own start screen and an optional full Linux system
(Devuan) on the tablet.

> **Unofficial hobby project.** Not affiliated with, endorsed or reviewed by Huawei Technologies
> Co., Ltd., Unisoc (Spreadtrum), KingRoot or Mullvad. Huawei and MediaPad are trademarks of their
> respective owners and are used here only to name the device this project is made for.

## ⚠ Warning – read before you start

- **For one device model only:** Huawei MediaPad T1 7.0, **T1-701u** (Spreadtrum SC7731G,
  Android 4.4.2). On any other device the scripts will at best fail and at worst damage the system.
- **Root is required and is your responsibility.** The project expects a rooted tablet with a
  working `su` (tested with KingRoot, a closed-source third-party tool that roots the device through
  kernel exploits). This project does **not** contain, download or link any rooting tool or exploit.
- **You change your device at your own risk.** The scripts pause Android, disable preinstalled apps,
  set firewall rules, install a starter app and can run a Linux system from the internal storage.
  Possible results include **data loss, loss of warranty and, in the worst case, an unusable
  device.** Make a backup of everything you care about first. The project deliberately does not
  touch the boot loader, the boot/recovery partitions or the radio/NV partitions.
- **Security:** the link between tablet and laptop is encrypted and authenticated with a pairing key
  (Monocypher: X25519, ChaCha20-Poly1305). Whoever holds the tablet with that key can control your
  laptop's keyboard and mouse. ADB on Android 4.4 is not encrypted. See [docs/SECURITY.md](docs/SECURITY.md).
  No guarantee is given for the security of any part.
- The software is provided "as is", without warranty of any kind (see [LICENSE](LICENSE)).

## What it does

- **Remote control:** the laptop's screen (KDE Plasma on Wayland, through the desktop portal) on the
  tablet; one-finger click/drag/right click, two-finger scroll and zoom, an on-screen keyboard,
  and the laptop's **sound** on the tablet's speaker.
  Works over USB (adb), over Wi-Fi (encrypted) and optionally from another network over the internet
  (IPv4 port forwarding or IPv6).
- **Own start screen on the tablet** (`tb-home`): clock, date, Wi-Fi, battery and large tiles:
  "Remote control", "Linux", "Android".
- **Linux on the tablet** (optional): Devuan 6 "excalibur" (armhf) in a chroot with X11 on the
  framebuffer, a touch-friendly launcher, on-screen keyboard, files, editor, browser, images,
  music & video, PDF, camera (experimental), settings and a **USB installer** that turns the
  tablet into a USB stick with an ISO image for installing another computer.
- **Starts from Android with one tap** on a "Tablet System" icon, or automatically at boot.
- **Wi-Fi from QR codes:** an optional installer for ZXing Barcode Scanner, which joins public Wi-Fi
  networks from their QR codes (downloaded from the official release and checked, not shipped).
- Monitor and macro screens (laptop CPU/memory/network at a glance, eight configurable buttons).

## What you need

| | |
|---|---|
| Tablet | Huawei MediaPad T1 7.0 T1-701u, Android 4.4.2, rooted (`su` in `/system/xbin/su`), USB debugging enabled |
| Laptop | Linux with Python ≥ 3.11, a Wayland desktop with `xdg-desktop-portal` remote desktop support (developed with KDE Plasma 6), `systemd --user` |
| Build tools | `gcc`, `make`, `gcc-arm-linux-gnueabihf`, `adb`, a JDK (`keytool`, `jarsigner`); `./build.sh --deps` lists what is missing |
| For Linux on the tablet | about 2 GB free on the tablet's `/data`, `curl`, `gpg`, `fakeroot`, `dpkg-deb`, `e2fsprogs` on the laptop, Wi-Fi on the tablet |

## Quick start

```sh
git clone https://github.com/biojaiv/t1-701u-remote-linux.git
cd t1-701u-remote-linux
./build.sh --test        # build everything and run the tests
scripts/install.sh       # laptop side: hostd service and the "huawai" command
huawai up                # tablet connected by USB, Android running: pair and start
```

The full procedure, including the optional parts, is in **[docs/INSTALL.md](docs/INSTALL.md)**.

## Documentation

| Document | Content |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | building, installing, pairing, optional parts, uninstalling |
| [docs/USAGE.md](docs/USAGE.md) | daily use, gestures, access from another network, troubleshooting |
| [docs/LINUX.md](docs/LINUX.md) | the Linux system on the tablet: design, quirks of the device, maintenance |
| [docs/SECURITY.md](docs/SECURITY.md) | threat model, encryption, what is exposed where |
| [docs/ANDROID-SLIMMING.md](docs/ANDROID-SLIMMING.md) | which preinstalled apps can be disabled and why |
| [docs/ANDROID-SERVICES.md](docs/ANDROID-SERVICES.md) | the stock Android init services of this model |
| [docs/PLAN.md](docs/PLAN.md) | the original step-by-step implementation plan, with measurements and dead ends |

## Layout

| Folder | Content |
|---|---|
| `tablet/` | C programs for the tablet (`tabletd`, `tb-home`, `tb-linuxd`, `tb-sysd`, …) and its launcher script |
| `host/` | the laptop side (`hostd`, `huawai` CLI) in Python |
| `linux/` | settings and programs for the Linux system on the tablet |
| `scripts/` | build, install and maintenance scripts |
| `tests/` | unit, integration and fuzz tests (run on the laptop, x86 builds) |
| `third_party/` | Monocypher 4.0.3 and tinyalsa 1.1.1 (unchanged, own licenses), DejaVu font license |

## License

MIT, see [LICENSE](LICENSE). Monocypher (CC0-1.0 or BSD-2-Clause) and tinyalsa (BSD-3-Clause)
keep their own licenses.
No firmware, vendor files or binaries of Huawei, Spreadtrum/Unisoc or KingRoot are part of this
repository.
