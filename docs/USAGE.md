# Usage

Laptop: Linux with a Wayland desktop (tested with KDE Plasma 6). Tablet: Huawei MediaPad T1-701u.
By default the tablet still boots into Android; the own system is started from Android when needed,
and Android is paused for that session (see "Limits").

Building and first installation are described in [INSTALL.md](INSTALL.md). This document covers
daily use.

## Starting from the "Tablet System" icon

Android cannot be removed from this tablet (the system partition is write-protected below the
kernel). Android boots normally; Wi-Fi and other settings can be changed there as usual. The
**"Tablet System"** icon in the app menu starts the own system: Android pauses and the start screen
appears with **Remote control**, **Linux** and **Android**. The "Android" tile starts Android again
without restarting the tablet; if Android has not finished starting within 120 seconds (during
development it hung twice at "Starting apps"), the tablet restarts into Android instead. Optionally, preinstalled apps can be disabled to free space and memory
([ANDROID-SLIMMING.md](ANDROID-SLIMMING.md) lists 82 that were disabled on the test device).

| Command (cable) | Effect |
|---|---|
| `scripts/autostart.sh mode android` | boot into Android at power-on (default; own system via the icon) |
| `scripts/autostart.sh mode mini` | own system at every power-on, without the icon |
| `scripts/autostart.sh status` | boot mode, boot log |
| `scripts/autostart.sh update` | copy new programs to the tablet (also from within the own system, through `tb-su`) |
| `scripts/autostart.sh internet NAME PORT` | address of the laptop at home, for use from another network (see below) |

Safeguards in `mini` mode: if the own system starts three times without anyone touching the
screen, the fourth start boots Android; if the start screen crashes three times within one minute,
Android starts at once. Holding a volume key during boot is meant to force Android (**not yet
tested** on the device).

## Sound

While the remote control is connected, the laptop's sound also plays on the tablet's speaker; the
laptop keeps playing it as well. `hostd` takes what the laptop plays (the default output's monitor,
PipeWire or PulseAudio) and sends it inside the encrypted connection: 48 kHz stereo over Wi-Fi and
cable (about 190 KB/s), 24 kHz mono over the internet (about 48 KB/s). Silence is not sent. On the
tablet, `tb-audio` (started by `tabletd`) switches the speaker on and plays it, about 0.1–0.2 s
behind the picture.

- Switch it off: `huawai config audio=false`, then `systemctl --user restart huawai-hostd`.
- Volume: the laptop's volume (the tablet gets what the laptop plays).
- `tabletd.log` on the tablet shows a balance when a session ends: sound played, silence filled in,
  how often the reserve ran dry.
- With `huawai up` (cable path) the sound card is opened for the shell user until the next reboot;
  if that fails, everything works without sound.

## Joining Wi-Fi from a QR code

Many cafés, hotels and stations show a QR code for their Wi-Fi. Install a scanner once (cable,
Android running):

```
scripts/install-qr-scanner.sh
```

It downloads ZXing Barcode Scanner 4.7.8 from the ZXing project's official release (Apache 2.0, the
last version, made for Android 4.4), checks its SHA-256 and installs it. Place its icon next to
"Tablet System" on the home screen. Then: open "Barcode Scanner", point the back camera at the code,
tap **"Connect to network"**; Android stores and joins the network (WPA/WPA2, WEP, open, WPA2-EAP,
hidden networks). After that, "Tablet System" starts the own system as usual. Remove it with
`adb uninstall com.google.zxing.client.android`.

## From another network (internet)

The tablet calls the laptop at home; the connection is the same as on the home Wi-Fi (encrypted,
checked with the pairing key). Only a tablet that holds the pairing key gets in; every other caller
is rejected during the handshake.

If your provider uses **DS-Lite** (no public IPv4 address, common with cable providers), there is no
IPv4 port forwarding to your laptop; only **IPv6** can reach it from outside. The steps below cover
both cases.

### 1. Laptop

- Give the laptop a fixed address in your home network (DHCP reservation in the router, for
  example 192.168.1.50).
- Enable the incoming port, for example 7110:
  ```
  huawai config internet_port=7110
  systemctl --user restart huawai-hostd
  ```
  `hostd` then accepts the tablet's call on this port over IPv4 and IPv6 (port 0 switches it off).
  Ports below 1024 are not accepted.
- **Firewall:** open the port in the laptop's firewall. With ufw: `sudo ufw allow 7110/tcp`.
- **Mullvad VPN (optional):** If you use Mullvad VPN, the `hostd` service runs outside the tunnel
  (`mullvad-exclude`, used automatically when it is installed). Mullvad's firewall still drops
  incoming public IPv6, even with "LAN sharing" enabled. Following Mullvad's own instructions for
  incoming traffic, `scripts/mullvad-incoming.nft` marks only TCP port 7110 as excluded; it is
  loaded at boot by `scripts/huawai-incoming.service`:
  ```
  sudo nft -c -f scripts/mullvad-incoming.nft          # check only
  sudo install -D -m 644 scripts/mullvad-incoming.nft /etc/huawai/mullvad-incoming.nft
  sudo install -m 644 scripts/huawai-incoming.service /etc/systemd/system/
  sudo systemctl daemon-reload && sudo systemctl enable --now huawai-incoming
  ```
  Remove it with `sudo systemctl disable --now huawai-incoming`. The port 7110 is written into
  `scripts/mullvad-incoming.nft`; if you choose a different port, change it there as well.

### 2. Router

- **IPv4 (public IPv4 address):** forward the TCP port (here 7110) to the laptop's fixed address.
- **IPv6-only connection (DS-Lite):** there is nothing to forward; instead, open the port for the
  laptop in the router's IPv6 firewall. Depending on the router, this is called "IPv6 host
  exposure", "IPv6 port opening" or similar: allow TCP port 7110 to the laptop.

### 3. Tablet

With the USB cable connected and Android running:

```
scripts/autostart.sh internet ADDRESS-OR-NAME PORT
```

for example `scripts/autostart.sh internet laptop.example.net 7110` or
`scripts/autostart.sh internet 2001:db8:1234:5678::50 7110`. `scripts/autostart.sh internet off`
removes the entry. The address is stored on the tablet in `/data/tabletd/internet`.

- Prefer a stable address. If the laptop has several IPv6 addresses (SLAAC, temporary privacy
  addresses, DHCPv6), choose one that does not change and check that it is reachable from outside.
- If your provider changes the IPv6 prefix, the address has to be entered again. Alternatively use
  a DynDNS name with an AAAA record that the laptop keeps up to date.
- The tablet resolves the name when the own system starts, IPv6 first; IPv4 is used only when there
  is no IPv6 address.

### Using it

On the remote network: choose the Wi-Fi network in Android, then "Tablet System", then
"Remote control".

### Limits

- If your home connection is IPv6-only, the remote network must give the tablet IPv6 (mobile phone
  hotspots usually do, many hotel Wi-Fi networks do not).
- With Mullvad VPN: whether Mullvad lets incoming IPv6 through with the rule above and
  `mullvad-exclude` has **not been tested** from an outside network.

## Setting up (once)

See [INSTALL.md](INSTALL.md) for the full procedure. In short:

1. Run `scripts/install.sh` in the project folder. It checks programs and libraries, creates
   `~/.config/huawai-companion/hostd.toml`, sets up the user service `huawai-hostd` and makes the
   `huawai` command available. It does not need `sudo`; if something is missing, it names the
   package.
2. Connect the tablet by USB while Android is running: `huawai up`. The first time, this sets the
   tablet's firewall, pairs laptop and tablet (key only over the cable), pauses Android and starts
   the own system.

## Daily use

| Command | Effect |
|---|---|
| `huawai up` | pause Android, start the tablet's start screen (cable needed) |
| `huawai up --remote` | the same, the remote control opens at once |
| `huawai down` | stop the own system, start Android again |
| `huawai status` | what is running where |
| `huawai doctor` | are programs missing on the laptop? |
| `huawai pair --new` | create a new key (after losing the tablet) |
| `huawai config` | show settings; `huawai config rotation=clockwise` changes them |

`hostd` runs as a service and connects by itself: first directly and encrypted over Wi-Fi,
otherwise over the cable. If the router changes the tablet's address, `hostd` picks up the new one
the next time the tablet is connected by cable.

## On the tablet

- **Start screen:** clock, date, Wi-Fi address, battery and the tiles "Remote control" and "Linux"
  ("Linux" appears once it is set up; see [LINUX.md](LINUX.md)).
- **Linux:** tap the tile; after about 20 seconds the Linux start screen with the programs appears.
  The keyboard appears by itself in text fields. The "Remote control" tile there switches directly
  to the remote control.
- **Remote control:** tap the tile. Without the laptop it shows "Ready – waiting for the laptop".
- **Back to start:** hold both volume keys for about one second. In the laptop's menus (monitor,
  macros) the **"‹ Back"** button at the top left does the same.
- **In the remote control:**
  - one finger: tap = click, drag = drag with the button held, long press = right click;
  - two fingers: spread/pinch = zoom in/out, move together = scroll (when zoomed in: move the
    visible area);
  - "ABC" tab at the right edge: on-screen keyboard;
  - one volume key: switches between mirroring, laptop monitor and macros.
- **Macros:** in `~/.config/huawai-companion/macros.toml` on the laptop, for example

  ```toml
  [[macro]]
  label = "Quieter"
  command = ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"]
  ```

## Troubleshooting

- `huawai status` shows whether the start screen, the remote control and the encrypted port are
  running.
- `journalctl --user -u huawai-hostd -n 30` shows what `hostd` is doing.
- Tablet hangs: hold the power key for 10 to 15 seconds. Android then starts normally.
- If a KDE sharing dialog appears on the laptop again, confirm it; after that it stays away.

## Limits

- **Boot mode:** in the default mode `android`, the tablet runs Android again after a restart, and
  the own system has to be started again (icon or `huawai up`). Starting the own system directly at
  power-on by changing `/system` (phase 5 of [PLAN.md](PLAN.md)) is on hold because `/system` can no longer
  be written; `scripts/autostart.sh mode mini` starts it through a small Android app instead.
- **Power key in test mode:** switching the screen off needs root and only works in mini mode
  (phase 5); a short press currently has no visible effect.
- **Security:** see [SECURITY.md](SECURITY.md).
- **Windows** is not supported yet (postponed).
