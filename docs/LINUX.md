# Linux on the tablet

## What it is

A complete **Devuan 6 "Excalibur"** (Debian 13 without systemd, armhf) with package management and
security updates. It runs in a chroot on Huawei's kernel 3.10.17, because as far as is known the
signed boot chain does not accept a custom kernel. User interface: X11 directly on the framebuffer,
the matchbox window manager (one program in full screen), its own start screen `tablet-launcher` in
the style of tb-home (clock, date, Wi-Fi, battery, large tiles), the onboard on-screen keyboard,
opened and closed with the **"ABC" tab** at the right edge, and a dark GTK theme. Programs installed
initially: LXTerminal, PCManFM, Mousepad, NetSurf, Ristretto, Parole, Atril, plus the project's own
Transfer, Camera and Settings; more can be added with apt.

On the start screen (tb-home) it appears as the **"Linux"** tile next to the remote control. Back
to the start screen: hold both volume keys, as in the remote control. In the Linux start screen the
first tile, **"Remote control"**, switches directly to the remote control: it ends the session with
exit status 50, which tb-home understands as a request for the remote control.

## Layout

| Part | Where | Who |
|---|---|---|
| Image (ext4, 1.9 GB) | `/data/tabletd/linux/devuan.img`, mounted at `…/linux/root` with `nosuid,nodev` | root |
| `tb-chroot`, `tb-linuxd`, `linux-root.sh` | `/data/tabletd/linux/` (directory for root only) | root |
| `tb-linux` (program behind the tile) | `/data/local/tmp/tb-linux` | shell user, like tb-home |
| Session (X, window manager, programs) | in the chroot | user `tablet`, uid 5000, without root |
| System settings | `linux/overlay/` in the project, unpacked into the system | |

**Why a separate service:** without Android there is no `su`. Therefore `huawai up`, while Android
is still running, starts the root service `tb-linuxd`. It can do exactly one thing for the start
screen: start the fixed session and end it again. Nothing the caller sends is ever executed. When
ending, the process group and **every process of the user `tablet`** receive SIGTERM, then SIGKILL
after 1.5 s. Only then does the service reply, so that nothing draws over the start screen.

**Own `/dev`:** the system does not see Android's `/dev`, but a small tmpfs with `null`, `zero`,
`random` and the like, the framebuffer (group `video`), the input devices (`input`) and the sound
devices (`audio`). No block devices, no Binder, no modem.

**X only for the session:** no TCP, no abstract socket (it would be reachable from Android), access
only with a cookie (`~/.Xauthority`).

**Network:** Android's kernel lets only members of the group `aid_inet` (3003) open internet
sockets. `tablet` and apt's user `_apt` are members. `linux-root.sh mount` takes the name servers
from Android's settings.

## Three quirks of the tablet and how they are solved

- **Display driver `sprdfb`:** rejects `FBIOPUT_VSCREENINFO` when the virtual size changes
  (600×3072, three pages); X wants to set 600×1024 (error "FBIOPUT_VSCREENINFO: Invalid argument").
  Confirmed in the source of `sprdfb_check_var` in six public SC7731 kernels; Huawei's own source is
  not available. `libtablet-fbfix.so` (source `tablet/xfbfix.c`) is loaded only into Xorg and, in
  this one request, sets the virtual size and the x offset to the driver's values. The display runs
  in video mode (tested on the device: changes in the framebuffer appear immediately), so X does not
  need to trigger an update after drawing.
- **Touch (`goodix_ts`):** reports only multi-touch axes (protocol A) without `ABS_X`/`ABS_Y`; the X
  driver evdev ignores such a device. libinput accepts it (special case for Android devices), but
  needs entries in udev's database; there is no udev, so `mounts.sh` writes them to
  `/run/udev/data/` itself.
- **Network for apt:** apt downloads as `_apt` and keeps only that user's primary group. Android's
  kernel allows internet sockets only for the group `aid_inet` (3003), so it is the primary group of
  `_apt` (before that, even name resolution failed).

In addition: onboard 1.4.1 fails under Python 3.13 when inserting characters
(`SystemError … bad format char`); `tablet-onboard` replaces the one faulty conversion.

**Keyboard:** the "ABC" tab at the right edge (`tablet-keyboard-tab`) opens onboard and closes it
again; it is blue while the keyboard is open. The tab never takes the input focus, so the text field
that was tapped keeps it. onboard's own automatic showing and its docking are off: with matchbox,
the docked keyboard mapped and unmapped its window about six times a second after it was closed
(recorded with `xev`; seen as fast blinking), while a floating keyboard at the same place (the
bottom 205 pixels, full width) maps and unmaps exactly once. The keyboard lies over the program, so
a text field at the very bottom of a window can be covered by it.

**One tap:** the file manager (PCManFM) opens files and folders with a single tap (a double tap is
rarely recognised on the touch screen); the session sets this at every start, together with a
"Transfer" bookmark in its side pane. GTK's own file dialogs (Open, Save) have no such setting; a
small GTK module (`tablet/gtktouch.c`, `libtablet-gtktouch.so`, loaded through `GTK_MODULES`) sets
their file lists to open with one tap.

## Building and setting up

General setup of the project is described in [INSTALL.md](INSTALL.md).

1. Build the image on the laptop, without sudo (about 6 minutes, downloads around 70 MB from
   Devuan): `scripts/build-linux-image.sh`. The Release file is verified against the Excalibur key
   `9F8D 6C74 DE66 1075 FD17 1BE3 B398 2868 D104 092C`, the packages against the Release file.
2. Copy it to the tablet and set it up, **Android must be running**, battery not low:
   `scripts/linux.sh install`. This copies the image (1.9 GB) over Wi-Fi, runs the second stage of
   debootstrap on the tablet, creates users and groups and installs the user interface over the
   tablet's Wi-Fi. Long steps continue in the background on the tablet even if the USB connection
   drops; the script waits and shows the progress.
3. After that, `huawai up` also starts the system: mount, start the service, show the tile.

**Why 1.9 GB and Wi-Fi:** `adb push` of a 3 GB image aborted after about 2 GiB (368 s at around
5.8 MB/s); presumably Android's adbd from 2014 cannot write larger files (inferred from its age, not
proven). After that, the transfer aborted twice because the cable was loose. Therefore the image is
smaller than 2 GiB, and `scripts/linux.sh send` transfers it over Wi-Fi: the tablet waits once with
`tb-fetch` on port 5555 (the firewall and `tb-fetch` admit only the laptop's addresses), the laptop
sends, then the checksum is compared. Measured: 1.9 GB in 487 s.

**Working without a cable:** `scripts/adb-wifi.sh on` (once over the cable, Android running)
switches ADB to Wi-Fi, but only if the firewall restricts port 5555 to the laptop's addresses. ADB of
Android 4.4 is **not encrypted**; therefore use it only for setup, then `scripts/adb-wifi.sh off`.
A reboot of the tablet also switches ADB back to USB.

## USB installer

Copy an ISO to the tablet over the cable: `scripts/linux.sh iso-send FILE` (stored in
`/data/tabletd/iso`, inside the system at `/media/iso`). On the tablet: tile "USB installer", tap the
image, "Share", plug the cable into the other computer and choose the tablet in its boot menu;
"Stop" switches back. From the laptop: `scripts/linux.sh usb on NAME [disk|cd]`, `usb off`,
`usb status`. Tested: Kali 2026.3 netinstall as a removable drive, all files identical. Not yet
tested: booting a computer from it. Space: about 1.9 GB are free on `/data`.

## Transfer drive

To move files between the tablet and a computer over the USB cable. The drive is a FAT32 image on
the tablet (`/data/tabletd/transfer.img`, label `TABLET`) and belongs to one side at a time, because
a FAT file system must not be written from two sides:

- **On the tablet** it is the folder `~/Transfer` (in Files).
- **"Give to computer"** in the app **Transfer** (tile on the Linux start screen) unmounts the folder
  and offers the drive over USB, writable; the computer shows a USB drive named `TABLET` (Dolphin:
  "Devices"). Programs that have files from `Transfer` open must be closed first.
- **Eject it on the computer** when done (Dolphin: "Safely remove"); it then comes back to the tablet
  by itself. That also happens when the cable is pulled out, but then, as with a USB stick, files
  the computer had not written out yet are missing. "Take back" in the app does the same from the
  tablet; it refuses while the computer still uses the drive.
- Next to `TABLET` the computer may list an empty drive of 0 bytes: a second, unused slot of the
  tablet's USB gadget. It can be ignored.

Create it once: "Create drive" in the app (512 MB), or `scripts/linux.sh transfer create [MB]` from
the laptop (64 to 1024 MB; 256 MB of `/data` always stay free). It is made on the tablet with
Android's `newfs_msdos`. From the laptop: `scripts/linux.sh transfer give | take [force] | status`.
`take force` takes it back without the computer's eject: files the computer has not written out
yet are lost, as with a USB stick pulled out too early. The drive and the USB installer use the same
USB slot: only one of them at a time.

## Camera

The sensors of both cameras are run by Android's camera driver (`camera.sc8830.so`); the sensor
drivers live there, not in the kernel, so Linux camera programs see no usable camera on
`/dev/video0`. `tb-camera` (`tablet/camera/camera.c`) loads that driver itself, as Android's media
server does, but without Android's camera service. It runs outside the Linux system as the media
user, is started with it (`linux-root.sh daemon`), restarts its camera process if the driver
crashes, and serves one program at a time over the abstract socket `tabletd-camera` (only the session
user, root and the shell user). The app **Camera** (`tablet-camera`) shows the preview (about 3 to 8
pictures per second), takes photos (JPEG, 1600×1200, from the driver), switches between front and
back, turns the picture by 90° per tap (remembered per camera), and saves to `~/Pictures` or a folder
chosen in the app with one tap per folder. The front camera's preview is mirrored; its photos are not.

Building it needs the Android NDK r25c (the last one for Android 4.4): `make android` (or
`NDK=/path make android`); `scripts/linux.sh tools` builds and installs it when the NDK is there,
otherwise the Linux system has no camera. The driver's interface comes from the AOSP 4.4.2 headers in
`third_party/aosp-4.4.2-headers/`. `tablet/camera/probe.c` and `camtest.c` are small test programs
for the driver (list the cameras; count preview frames), not installed.

## Maintenance

| Command | Effect |
|---|---|
| `scripts/linux.sh task upgrade` | security and other updates (apt) |
| `scripts/linux.sh status` | mounted? service? session processes? |
| `scripts/linux.sh stop` | stop service and session |
| `scripts/linux.sh umount` | unmount everything |
| `scripts/linux.sh overlay` | apply changed settings from `linux/overlay` |
| `scripts/linux.sh tools` | apply changed `tb-*` programs |

Logs on the tablet: `/data/tabletd/log/linuxd.log` (service) and `/data/tabletd/log/session.log`
(session, with the warnings and errors of the X server). Root writes them, the shell group may read
them, because without Android there is no `su`. The full X log is inside the system in the home
directory of the user `tablet`: `~tablet/.local/share/xorg/Xorg.0.log`.

## Limits

- **Kernel:** Huawei's 3.10.17 from 2016, without updates. No seccomp, no namespaces except `mnt`
  (tested on the device). Browsers therefore run without a sandbox. The system gets updates, the
  kernel never does.
- **chroot is not a security boundary for root.** That is why the session runs without root and the
  image is mounted with `nosuid`: `sudo` and other setuid programs have no effect. Administration is
  done from the laptop.
- **No graphics acceleration:** no videos, no animations, heavy programs are slow.
- **Start at power-on:** not directly; changing `/system` for that (phase 5 of [PLAN.md](PLAN.md)) is on
  hold. The own system, including the Linux tile, is started after Android has booted, either from
  the "Tablet System" icon or, with `scripts/autostart.sh mode mini`, automatically (see
  [USAGE.md](USAGE.md)).
- **Root only with Android:** everything that needs root (setup, updates, `overlay`) works only
  while Android is running. Resuming Android after pausing it hung twice at "Starting apps"
  during development; the "Android" tile therefore waits up to 120 seconds for Android to finish
  starting and restarts the tablet if it does not.
- **When stopping**, the service sometimes reports "processes left after SIGKILL": a process takes
  longer than 3 s; afterwards it is gone (checked). Cause not investigated.
- **Tested on the device:** picture the right way round in landscape, touch at the right position,
  start screen with tiles, keyboard with the "ABC" tab without blinking, one tap in file dialogs,
  non-ASCII characters (umlauts) arrive, speed "usable", back with both volume keys, switching to the
  remote control and starting again, both cameras with preview, switching and photos.
  **Not yet tested:** sound, NetSurf with real web pages, continuous operation.
