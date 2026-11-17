# Implementation plan: mini OS and laptop remote control for the Huawei MediaPad T1-701u

> This is the original implementation plan of the project, translated from German. It documents how the project was built step by step, including dead ends. For installing and using the project, see the README and docs/INSTALL.md.

Version 4. Work order for the implementing agent (Claude Opus 5.5).
The document can be understood without the preceding conversation history.
Versions 2 and 3 are not published; section 13 lists the changes.
**Working mode:** Decision: development proceeds without the owner's involvement, and the agent makes decisions itself. Steps that need the owner at the device are recorded in the project log (not published) as "open (needs owner)" and skipped, as long as later steps do not depend on them. This does not apply to irreversible actions (phase 5d, removing `su`, flashing the factory package) and not to reboots in phases 5b to 5d: there, the owner must be present at the device.

---

## 0. How to work with this plan

The task is long and has one risky part (phase 5). Therefore stick to this procedure:

1. **One phase per session.** Begin every session by reading this plan and the project log. Then work only on the next open phase. The project skill `tablet-phase` (`.claude/skills/tablet-phase/SKILL.md`) guides through this procedure; in case of contradiction, this plan takes precedence.
2. **The project log is your memory.** Create it in phase 0 using the template in section 11. After every step, record: step number, result, measured value or observation, date. New findings about the device belong there, not only in the chat.
3. **Steps in order.** Every step has a number (e.g. P1.4), an action and a check. Only move on once the check has passed.
4. **Two-attempts rule.** If a step fails twice, stop. Describe to the user what you did and what you observed. Do not invent a third approach on your own.
5. **Stop at the end of every phase.** Report the acceptance criteria one by one (met, not met, not checked) and wait for the user's "continue".
6. **Keep the scope.** Build only what is in the plan. No additional dependencies, no rework of parts that have already been accepted.
7. **Test on the laptop first, then on the tablet.** `tabletd` can also be started on the laptop with a file framebuffer (section 5). Every change goes through `make test` first.
8. **Keep output small.** Never output binary data or entire log files. Use checksums, line counts and the last lines.

You cannot see the tablet's screen. If a check reads "the user sees ...", ask them and assume nothing.

---

## 1. Goal and limits

### Goal

1. **Lean, private system:** After power-on, instead of the Android user interface, a small program of our own runs. The model in spirit is GrapheneOS: little attack surface, no data leakage, readable code.
2. **Laptop remote control with screen transmission:** The tablet shows the laptop screen and controls it by touch, only over the USB cable.

The usable core (MVP) consists of phases 0 to 3. Phase 4 is deferred, phase 5 is the conversion into a mini OS, phase 6 the hardening, phase 7 additionally turns the tablet into a USB installation device.

### Limits that you never present differently to the user

- **Not GrapheneOS level.** The kernel (3.10.17, as of 2016) stays and has known vulnerabilities. There is no security chip and no verified boot chain of our own. Anyone who has the device in hand can modify it.
- **No custom kernel, no custom ROM.** `boot.img` and `recovery.img` carry the identifier `SPRD-SEC` (signed Spreadtrum format). No kernel source code or custom ROM for this model was found.
- **No apps.** The system can do exactly what is in this plan.
- **The native Android base stays.** Kernel, `init`, `adbd`, the shell and the system libraries still come from Huawei. What is removed is the Java layer with all apps.

---

## 2. Working rules

The user's global `~/.claude/CLAUDE.md` applies in full (honesty, no shortcuts, sparing system access). In addition, these rules apply:

**Forbidden, without exception**

| Prohibition | Reason |
|---|---|
| Writing to `boot`, `recovery`, `splnormal`, `ubootnormal`, `ptable`, `boot0`, `boot1`, `logo`, `fbootlogo`, `misc`, `oeminfo` and all radio/NV partitions | Signed or device-specific data; a mistake cannot be undone |
| Deleting `/data/misc/adb/` and `/data/property/` or formatting `userdata` | That is where the ADB authorization and the "ADB on" setting live. Without the Android user interface nobody can re-authorize ADB; the device would be locked out |
| Deleting files under `/system/bin`, `/system/lib`, `/system/xbin`, `/system/etc`, `/system/vendor` | The shell, `toolbox` and `su` depend on them; `init` starts some services as "critical" and boots into recovery if they fail |
| Removing or replacing `su` | The only way to make changes and to recover; the decision lies with the owner (phase 6) |
| `sudo`, package installation or downloads without the owner's yes | The owner's wish |
| Overwriting the running `system` partition as a whole with `dd` | The file system is mounted; the result would be corrupted |
| Inserting an SD card with a `dload` folder into the tablet or leaving it there; copying `UPDATE.APP` to the tablet or a card | According to Huawei's instructions, the device then starts the factory update on its own at power-on. It overwrites `system` and radio/NV partitions and formats `userdata`. This is exclusively a rescue path on the owner's explicit instruction (section 8) |

**Required**

- Before every reboot in phase 5: check the lifeline (`scripts/lifeline.sh`, section 8).
- Before changing any file in `/system`, pull it to `backup/system-files/` and note it in the project log.
- Code, comments and documentation in English.

---

## 3. Verified facts

Observed on the device. Anything not listed here has not been checked.

### Tablet

| Property | Value |
|---|---|
| Model | Huawei MediaPad T1 7.0, `T1-701u`, device name `hwt1701` |
| Firmware | `T1-701uV100R001C100B107`, Android 4.4.2 (SDK 19), EMUI 3.0 |
| Chipset | Spreadtrum SC7731G (`ro.board.platform=sc8830`), ARMv7 32-bit, `armeabi-v7a` |
| Kernel | `3.10.17-g50778d4`, built 2016-02-02 |
| RAM | 972888 kB |
| Framebuffer | `/dev/graphics/fb0` (`crw-rw---- root graphics`), visible 600×1024, virtual 600×3072, 32 bits, line length 2400 bytes |
| Touch | `/dev/input/event3`, name `goodix_ts`, `ABS_MT_POSITION_X` 0–600, `ABS_MT_POSITION_Y` 0–1024, also reports `ABS_MT_TRACKING_ID` |
| Keys | `/dev/input/event0`, name `sci-keypad` (key codes not yet read out) |
| Other inputs | `event1` orientation sensor, `event2` headset, `event4` proximity sensor |
| Root | `/system/xbin/su` from KingRoot (package `com.kingroot.kinguser`); `su -c id` returns uid 0 without prompting; context `u:r:init:s0`. **Only while Android is running:** when it is stopped, `su` ends after about 31 seconds with `Connection to ui timed out` (P0.6) |
| SELinux | `getenforce` reports `Enforcing` |
| Storage | `/data` 4.7 GB (399 MB free), `/system` 1.4 GB (61 MB free, mounted writable), `/cache` 248 MB (empty) |
| ADB | Serial number device-specific, USB ID `12d1:1052` (`mtp,adb`) or `18d1:d002` (`adb` alone). Authorization for the laptop is stored permanently (`/data/misc/adb/adb_keys`, copy in `backup/adb_keys`); before that it applied only per connection |

- **Drawing directly works.** After `stop`, `init.svc.zygote` and `init.svc.surfaceflinger` were `stopped`. An image written to `/dev/graphics/fb0` with `cat` (all three pages filled) was visible to the user. No refresh command was needed. `adbd` kept running.
- **Tools on the tablet are scarce.** There is `toolbox` (among others `dd`, `cat`, `md5`, `getevent`, `getprop`, `stop`, `start`) and `gzip`. Missing: `busybox`, `nc`, `head`, `tr`, `which`, `uname`, `md5sum`.
- **`adb exec-out` does not work** (`error: closed`). Binary data over `adb shell` is unreliable; use `adb push`, `adb pull` or `adb forward`.
- **`adb push` and `adb pull`:** measured 6–9 MB/s.
- **Reading block devices:** worked via `chmod 644` (as root) and `adb pull`, checksums identical. Set the permissions back to 600 afterwards.

### Measured on the device (phases 0 to 2)

Details and evidence are in the project log.

- **USB dropouts:** Without Android, the MTP interface does not respond. The KDE service `kmtpd5` on the laptop then resets the device every 16 seconds. In the USB mode `adb` alone (`setprop sys.usb.config adb`, rule in `/init.usb.rc`), the connection stayed up for 15 minutes without a single dropout, and the battery charged.
- **USB modes** according to `/init.usb.rc` and `/init.sc8830.usb.rc`: `adb`, `mtp`, `mtp,adb`, `mass_storage`, `mass_storage,adb`, `rndis`, `rndis,adb`, `vser`, `vser,adb`, `none` and others. The kernel has two mass storage drives (`lun`, `lun1`) with `file`, `ro`, `cdrom`.
- **Permissions:** `adb shell` (uid 2000) has the groups `graphics`, `input`, `inet`. `tabletd` can open the framebuffer and input devices without root; only the brightness needs root.
- **Framebuffer:** `yoffset` 0, red at bit 0, green at bit 8, blue at bit 16, no alpha field, `mmap` works.
- **Input:** `sci-keypad` delivers `KEY_HOME` 102, `KEY_VOLUMEDOWN` 114, `KEY_VOLUMEUP` 115, `KEY_POWER` 116. `goodix_ts` does not report `ABS_MT_SLOT` (suggests protocol A); real touches have not been recorded yet.
- **Power:** `/sys/power/autosleep` does not exist; `/sys/power/wake_lock` and `/proc/wakelocks` exist. Battery under `/sys/class/power_supply/battery/` (`capacity`, `status`, `temp`, `voltage_now`), brightness under `/sys/class/backlight/sprd_backlight/brightness` (0–255).
- **Kernel:** no seccomp (`/proc/self/status` without a `Seccomp` line). Static glibc programs from the Debian compiler run.
- **Hook point:** `init.rc` contains `service flash_recovery /system/etc/install-recovery.sh` (`class main`, `oneshot`, no `user`). The file comes from KingRoot and starts its services; its own log `/data/local/tmp/krperm1.txt` shows `uid=0(root) … context=u:r:init:s0` at every boot. Backup: `backup/system-files/etc/install-recovery.sh`.
- **Throughput:** 5.6 to 5.8 MB/s over `adb forward`; scrolling text 22 frames per second, incompressible full frames 4.7.
- **Factory package:** matches the device's regional variant (`BOOT` and `RECOVERY` identical to the backup). The actual boot loader is in `boot0`/`boot1`; `splnormal` and `ubootnormal` are empty.

### Backup

- `backup/` contains 29 partition images (2.3 GB), all except `userdata` and `cache`.
- `backup/md5_report.txt`: size, checksum and comparison result per partition, all `OK`. `backup/partmap.txt`: name → block device.
- **Restoring was never tested, and there is no tool for it yet.**
- **Factory firmware:**
  - Source: the factory package (`T1-701u_V100R001C100B107`, an `UPDATE.APP` in a `dload/` folder) was obtained by the owner for their own device; this project does not ship, mirror or link firmware. Check sizes and checksums against the source you trust; a matching checksum proves an error-free transfer, **not** authenticity.
  - Contents according to `unzip -l` (not extracted): `dload/UPDATE.APP` (1592704856 bytes, dated 2016-02-03), a copyright notice and Huawei's instructions for the SD card update (Chinese, four pages). Which customer variant a package belongs to is visible in its file names; whether it matches your device is clarified by P0.4.
  - Read from the instructions (general Huawei document from 2012, not checked on the device): the `dload` folder belongs in the root directory of a FAT32 card; the switched-off device starts the update **on its own** with the card inserted; according to a screenshot, it also writes radio and NV partitions (`WDSP`, `WFIXNV1`) and **formats `userdata`**. The instructions name a file `au_temp.cfg` as mandatory, which is missing from the package; whether the automatic start happens without it is open.
  - A Spreadtrum `.pac` package for this model was not found. See P0.8, P0.9 and section 8.

### Laptop

| Property | Value |
|---|---|
| System | a Debian 13 based distribution, KDE Plasma 6.3.6 on Wayland |
| Screen | `eDP-1`, 1920×1200 pixels, scaling 1.1, logical size 1746×1091 |
| Portal | `xdg-desktop-portal` 1.20.3, `xdg-desktop-portal-kde` 6.3.5 |
| ScreenCast portal | Version 5, `AvailableSourceTypes` = 3 (monitor and window, **no** virtual screen), `AvailableCursorModes` = 7 |
| RemoteDesktop portal | Version 2, `AvailableDeviceTypes` = 7 (keyboard, pointer, touchscreen) |
| PipeWire | 1.6.9-2~bpo13+1 |
| Python | `gi` (PyGObject 3.50), `dbus` (dbus-python 1.4), `lz4`, `numpy` 2.2, `PIL` 11.1, `psutil` 7.0 |
| GStreamer | 1.26.2 with `videoconvert`, `videoscale`, `videoflip`, `videorate`, `appsink`; **`pipewiresrc` missing** |
| Other tools | `adb`, `gcc` (x86 only), `ffmpeg`, `wpctl`, `pactl`, `loginctl`, `qdbus6`, `liblz4.so.1` (1.10.0) |
| ARM compiler | `arm-linux-gnueabihf-gcc` 14.2.0 (Debian), installed |
| Missing | `fastboot`, `scrcpy`, `krfb`, `ydotool`, `shellcheck`; `/dev/uinput` only for root; kernel log (`dmesg`, `journalctl -k`) not readable without root |

### Existing code

- `src/companion.py`: prototype of a system monitor with four touch buttons via `adb push` and `dd`. The rendering was checked on the laptop; transfer and touch were never tested on the device. Reuse the drawing functions (`render`, `Monitor`) in phase 3, not the transport.

---

## 4. Preparation by the user

You cannot do these items yourself. Ask about them at the start of phase 0 and record the answers in the project log.

1. **Tablet:** charge on the power adapter to at least 50 %, switch on, connect via USB, remove the SIM card.
2. **ARM compiler** (for phase 0): recommended `sudo apt install gcc-arm-linux-gnueabihf`. Alternative without `sudo`: Zig archive into `tools/`; this requires the user to approve the download.
3. **GStreamer plugin** (only for phase 3): `sudo apt install gstreamer1.0-pipewire=1.6.9-2~bpo13+1`. The version must be specified. The default candidate is 1.4.2-1 and does not match the installed PipeWire 1.6.9.
4. **Git:** Ask whether a Git repository should be created in the project folder. If yes, put `backup/` and `tools/` into `.gitignore`; commit only on request.
5. **Orientation:** Ask how the tablet should stand (landscape, connector on the left or right). This determines the rotation direction.
6. **Factory firmware:** Ask whether the user wants to download the full package for B107 themselves (section 3, Backup). At first it serves only for comparison (P0.9) and is not flashed anywhere. No download without an explicit yes; the source is a third-party site, tell the user so. **Decision: yes.** Download status: see section 3, Backup.

---

## 5. Architecture

```
Laptop (KDE/Wayland)                            Tablet (kernel 3.10 from Huawei)
┌───────────────────────────┐    USB / ADB      ┌───────────────────────────────┐
│ hostd (Python, no root)   │    forwarding     │ tabletd (C, static, ARM)      │
│  ├ capture: portal +      │◄─────────────────►│  ├ framebuffer output         │
│  │  PipeWire + GStreamer  │                   │  ├ touch and keys             │
│  ├ tiles + LZ4            │                   │  ├ brightness, battery        │
│  ├ pointer via portal     │                   │  └ status page without laptop │
│  └ monitor, macros        │                   │ started by tabletd-launch     │
└───────────────────────────┘                   └───────────────────────────────┘
```

### Decisions

- **A program of our own on the tablet.** `tabletd` is statically linked, written in C, target at most about 1000 lines in a few files (at the time of writing: 1301 lines including status page and key logic, excluding the generated font data; the deviation is recorded in the project log). It works in framebuffer coordinates (600×1024, portrait) and knows nothing about rotation until phase 5.
- **Transport:** `adb forward tcp:7100 localabstract:tabletd` (abstract Unix socket, no TCP port is open on the tablet). Confirmed in P1.6; the TCP fallback (`--listen tcp:7100`, bound to `127.0.0.1`) stays in the program but is not needed.
- **USB without MTP.** As long as Android is stopped, the tablet runs in the USB mode `adb` alone. For the tests of phases 1 to 3, `scripts/android.sh stop` takes care of this; in mini mode (phase 5), the launcher does.
- **As few privileges as possible.** In phases 1 to 3, `tabletd` runs as the shell user. Because `su` does not work without Android, `scripts/android.sh stop` first starts a small root helper (`tablet/root-helper.sh`) that, on request, performs exactly one fixed action: restart Android.
- **Hardware behind a thin layer.** `tabletd` gets the switches `--fb FILE --geom 600x1024` and `--input FILE`. With them, the same program runs as an x86 build on the laptop against a file and a FIFO. `make test` and the fuzzing build on this.
- **Capture via the desktop portal.** A combined session of `RemoteDesktop` (input) and `ScreenCast` (image). No `sudo`, no `/dev/uinput`; the user confirms a KDE dialog once.
- **Image processing in Python** (changed): GStreamer delivers the full image as RGB; cropping (zoom), scaling, rotating and converting to RGB565 is done by `host/capture.py`. Originally the pipeline was supposed to do all of this (6.7); changing the crop region in the running stream made GStreamer crash.
- **Flow control.** The laptop sends a new frame only after the tablet has acknowledged the previous one. Intermediate frames are dropped. That way nothing piles up in the USB channel.
- **Android stays unchanged until phase 5.** Until then, a reboot undoes everything.

### Transfer protocol, version 1

Every message has the same frame. All numbers little-endian.

```
u8  type
u32 len        payload length, at most 1 300 000
u8  payload[len]
```

After the connection is established, the tablet sends `INFO`. There is only one client; a new connection replaces the old one.

| Type | Direction | Name | Payload |
|---|---|---|---|
| 0x01 | Laptop → Tablet | `RECT` | u16 x, y, w, h; u8 fmt; image data |
| 0x02 | Laptop → Tablet | `PRESENT` | u32 seq |
| 0x03 | Laptop → Tablet | `BACKLIGHT` | u8 brightness 0–255 |
| 0x04 | Laptop → Tablet | `TIME` | u64 Unix seconds (UTC); i32 time zone offset in seconds |
| 0x05 | Laptop → Tablet | `PING` | u32 counter |
| 0x06 | Laptop → Tablet | `AUTH` | 32 bytes HMAC-SHA256 over the nonce (phase 6) |
| 0x41 | Tablet → Laptop | `INFO` | u16 protocol version; u16 width; u16 height; u8 flags (bit 0: authentication required); 16 bytes nonce |
| 0x42 | Tablet → Laptop | `ACK` | u32 seq from `PRESENT` |
| 0x43 | Tablet → Laptop | `TOUCH` | u8 slot; u8 state (0 released, 1 pressed, 2 moved); u16 x; u16 y |
| 0x44 | Tablet → Laptop | `KEY` | u16 Linux key code; u8 state |
| 0x45 | Tablet → Laptop | `STATUS` | u8 battery in percent; u8 charging; i16 temperature in tenths of a degree |
| 0x46 | Tablet → Laptop | `PONG` | u32 counter |

- **Image formats (`fmt`):** 0 = raw RGB565, 1 = RGB565 as an LZ4 block. RGB565 means: one u16 little-endian per pixel, red in bits 15–11, green 10–5, blue 4–0, row by row without padding bytes. The tablet converts to the framebuffer's 32-bit format itself (bit positions from `FBIOGET_VSCREENINFO`).
- **Strict checking on the tablet.** The connection is dropped on: unknown type, `len` above the maximum, `len` does not match the type, rectangle extends beyond the screen, w or h equal to 0, decompressed length not equal to w × h × 2, error in the LZ4 stream. No crash, no partial image beyond the bounds.
- **Changed by decision of the owner:** in mirror mode, `hostd` draws an on-screen keyboard and types what is tapped on it. The protocol stays as it is (the tablet only sends touches and keys), but whoever controls the tablet can use it to enter arbitrary text on the laptop. The following original rule therefore now applies only to the "Monitor" and "Macros" views.
- **The tablet cannot trigger anything on the laptop except pointer/touch events and macros from a list that is stored on the laptop.** There is no message that carries commands or text, and `hostd` never executes anything that originates from the tablet.
- **Heartbeat:** `hostd` sends `PING` every second while idle. If `tabletd` hears nothing for 5 seconds, the laptop is considered disconnected.

### Performance target

- A full frame in RGB565 is 600 × 1024 × 2 = 1.23 MB. At 6–9 MB/s this gives 5–7 frames per second in the worst case (full-screen video, incompressible).
- With tiles and LZ4, considerably more can be expected during normal desktop work. **Target: at least 15 frames per second when scrolling text.** This is an estimate; the measured value from phase 2 is what counts.
- The laptop screen (1920×1200) fits exactly into 960×600 with factor 0.5. In the 1024×600 image, 32 pixels on the left and right each remain black.

---

## 6. Technical pitfalls

Read the matching subsection before you write the respective part. These points are the most likely sources of error.

### 6.1 Compiling for the tablet

- With the Debian compiler: `arm-linux-gnueabihf-gcc -static -O2 -march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard`. With Zig: `zig cc -target arm-linux-musleabihf -mcpu=cortex_a7 -static`.
- Both toolchains now use 64-bit timestamps on 32-bit ARM. As a result, `struct input_event` from the system headers is no longer reliably 16 bytes in size, but kernel 3.10 delivers 16 bytes. **Define the structure yourself:**

```c
struct raw_input_event {   /* layout used by 32-bit kernel 3.10 */
    uint32_t sec, usec;
    uint16_t type, code;
    int32_t  value;
};
_Static_assert(sizeof(struct raw_input_event) == 16, "input event size");
```

- Do not use functions that load additional libraries with static glibc (`getpwnam`, `getaddrinfo`). Specify user and group IDs as numbers.
- Whether static programs from these toolchains run on kernel 3.10 is not proven. P0.5 checks exactly that.

### 6.2 Framebuffer

```c
struct fb_var_screeninfo v;  struct fb_fix_screeninfo f;
ioctl(fd, FBIOGET_VSCREENINFO, &v);
ioctl(fd, FBIOGET_FSCREENINFO, &f);
uint8_t *fb = mmap(NULL, f.smem_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
/* pixel (x, y) of the visible page: */
uint8_t *p = fb + (size_t)(v.yoffset + y) * f.line_length + (size_t)x * (v.bits_per_pixel / 8);
```

- The framebuffer has three pages stacked vertically. The visible one is the page starting at row `v.yoffset`. Anyone who always writes from row 0 may be drawing into an invisible page.
- Never change the geometry (`FBIOPUT_VSCREENINFO` only if a step explicitly requires it).
- Compose the color from RGB565 using `v.red.offset`, `v.green.offset`, `v.blue.offset`; set the fourth byte to 0xFF.
- If `mmap` fails, work with `pwrite`; plain writing is proven on the device.
- If the image does not update despite successful writes, call `ioctl(fd, FBIOPAN_DISPLAY, &v)` after every `PRESENT`. This was not necessary in the test with `cat`.

### 6.3 Touch and keys

- The driver reports `ABS_MT_TRACKING_ID`, so presumably the slot protocol (type B). Look at it in P1.1 with `getevent -l /dev/input/event3` before you write the parser, and store the output in `docs/`.
- Type B: `ABS_MT_SLOT` selects the finger, `ABS_MT_TRACKING_ID` ≥ 0 means touched down, −1 means lifted, `SYN_REPORT` closes the packet. If `ABS_MT_SLOT` is missing, slot 0 applies.
- Type A (if it is that after all): positions followed by `SYN_MT_REPORT`; a `SYN_REPORT` without a preceding position means "all fingers gone".
- Send `TOUCH` only on `SYN_REPORT`, not on every single value.
- Query a held key at startup (for phase 5):

```c
unsigned long bits[KEY_MAX / (8 * sizeof(long)) + 1] = {0};
ioctl(fd, EVIOCGKEY(sizeof(bits)), bits);
int held = (bits[code / (8 * sizeof(long))] >> (code % (8 * sizeof(long)))) & 1;
```

### 6.4 Permissions and networking on Android kernels

- Android kernels allow network sockets only to processes with the group `inet` (3003). An abstract Unix socket does not need this.
- Order in `tabletd`: as root, open all devices and files (framebuffer, input devices, brightness, battery), create the socket, **then** drop privileges. Open file descriptors remain usable.
- When dropping privileges: first `setgroups`, then `setgid`, then `setuid`; afterwards check that `setuid(0)` fails.

### 6.5 LZ4 block format (for the decoder on the tablet)

Write the decoder yourself (about 50 lines); nothing is downloaded. On the laptop, `lz4.block.compress(data, store_size=False)` compresses.

- A block consists of sequences. Each begins with a token byte: upper 4 bits = literal length, lower 4 bits = match length minus 4.
- If a length value is 15, additional bytes follow that are added until a byte smaller than 255 comes.
- After the token (and the additional bytes of the literal length) come the literals, then 2 bytes of offset (little-endian, 1–65535), then, if applicable, the additional bytes of the match length.
- The match copies from "output position minus offset". Source and destination may overlap, therefore copy byte by byte forwards.
- The last sequence ends after the literals, without an offset.
- Mandatory checks: literals lie completely within the input and fit into the output; offset is not 0 and not larger than the amount written so far; match fits into the output; at the end, the output is exactly as long as expected. Every violation is an error.

### 6.6 Desktop portal (call order)

Service `org.freedesktop.portal.Desktop`, object `/org/freedesktop/portal/desktop`, with `dbus-python` and the GLib main loop.

1. **Response pattern.** Each of the methods below immediately returns a request path; the actual result arrives as the signal `Response(u code, a{sv} results)` of the interface `org.freedesktop.portal.Request`. Code 0 = success, 1 = cancelled by the user, 2 = error.
2. **Subscribe to the signal before the call.** The path is predictable: `/org/freedesktop/portal/desktop/request/SENDER/TOKEN`. `SENDER` is your own bus name without the leading colon, with dots replaced by underscores. You pass `TOKEN` as the option `handle_token`.
3. `RemoteDesktop.CreateSession({handle_token, session_handle_token})` → `results["session_handle"]` (arrives as a string, convert it into an object path).
4. `RemoteDesktop.SelectDevices(session, {handle_token, types: UInt32(2 | 4), persist_mode: UInt32(2), restore_token: …})`. Type 2 = pointer, 4 = touchscreen. Pass `restore_token` only if one is stored.
5. `ScreenCast.SelectSources(session, {handle_token, types: UInt32(1), multiple: False, cursor_mode: UInt32(2)})`. **No `persist_mode` here**; in a combined session it belongs in step 4. Read from the source code of `xdg-desktop-portal-kde` 6.3.5, not checked on the device: in the combined session, KDE starts the stream with the cursor as metadata, regardless of `cursor_mode`. The mouse pointer may therefore be missing from the image (P3.3 records this). With `multiple: False`, if there is exactly one screen, that screen is transmitted; with several connected screens, the entire workspace.
6. `RemoteDesktop.Start(session, "", {handle_token})` → `results["streams"]` is a list of (node ID, properties with `size` and `position`), plus `results["restore_token"]`. Store the token under `~/.config/huawai-companion/restore_token`, file permissions 600.
7. `ScreenCast.OpenPipeWireRemote(session, {})` → file descriptor (call `.take()`).
8. Input: `NotifyPointerMotionAbsolute(session, {}, UInt32(node), Double(x), Double(y))` and `NotifyPointerButton(session, {}, Int32(272), UInt32(1 or 0))`. 272 is the left button, 273 the right one.
9. Handle the session's `Closed` signal: rebuild the session. If a stored token is rejected, delete the token and retry once without it.

- **Coordinates:** x and y refer to the stream. Use `size` from step 6 as the reference, not the pixel size of the frames. At scaling 1.1, the two differ (1746×1091 versus 1920×1200). KDE had bugs here; P3.6 tests this specifically.
- The options need the signature `a{sv}`; wrap numbers explicitly as `dbus.UInt32`.
- **Real touch events:** `NotifyTouchDown(session, {}, UInt32(node), UInt32(slot), Double(x), Double(y))`, `NotifyTouchMotion` and `NotifyTouchUp(session, {}, UInt32(slot))` are implemented in KDE 6.3.5 (read from the source code). KDE does not evaluate the node ID here; what x and y then refer to has not been checked. Use only in the trial P3.10.

### 6.7 GStreamer pipeline

Starting point (check the properties beforehand with `gst-inspect-1.0 pipewiresrc`):

```
pipewiresrc fd=FD path=NODE do-timestamp=true
  ! videorate drop-only=true max-rate=30
  ! videoconvert
  ! videoscale add-borders=true
  ! video/x-raw,width=1024,height=600,pixel-aspect-ratio=1/1
  ! videoflip method=counterclockwise
  ! videoconvert
  ! video/x-raw,format=RGB16
  ! appsink name=sink max-buffers=1 drop=true sync=false
```

- `RGB16` in GStreamer corresponds to the protocol's RGB565.
- `method` depends on the orientation (`counterclockwise` or `clockwise`); use the user's answer from section 4.
- PipeWire only delivers frames when something changes. If the screen stays still, nothing arrives; that is not an error.
- When reading out the buffer, mind the row length: at a width of 600 pixels it is 1200 bytes without padding, at other widths there may be padding.
- If the pipeline delivers no frames, try `always-copy=true` on `pipewiresrc`.

### 6.8 Rotation and pointer position

The landscape image (1024×600, "canvas") is rotated for the framebuffer (600×1024). Touch coordinates arrive in framebuffer coordinates (x_f, y_f).

| Rotation of the image | Back-calculation touch → canvas |
|---|---|
| counterclockwise | x_c = 1023 − y_f, y_c = x_f |
| clockwise | x_c = y_f, y_c = 599 − x_f |

From the canvas to the laptop (image 960×600, 32-pixel border each on the left and right):

- If x_c is below 32 or above 991, discard the touch.
- x_laptop = (x_c − 32) / 960 × width from `size`, y_laptop = y_c / 600 × height from `size`.

Whether the touch axes really lie this way has not been checked; P1.8 clarifies it.

---

## 7. Phases

### Phase 0: Connection, tools, open questions

| No. | Action | Check |
|---|---|---|
| P0.1 | Clarify the items from section 4 with the user, create the project log | File exists, answers recorded |
| P0.2 | `adb devices`; if the list is empty, ask the user to charge the tablet and switch it on | Serial number appears with state `device`; `adb shell su -c id` shows uid 0 |
| P0.3 | Look for the cause of the earlier connection drop: `su -c "ls /sys/fs/pstore /proc/last_kmsg"`, fetch existing files via `adb pull` into `docs/` and look at the last lines before the shutdown | Cause named or noted as "cannot be determined" |
| P0.4 | Complete the device data: `getevent -pl` (all devices), `ls /sys/class/power_supply/*`, `ls /sys/class/backlight/* /sys/class/leds/*`, `cat /proc/cpuinfo`, `grep Seccomp /proc/self/status`, `ls -Z /system/bin/sh`, `cat /sys/power/autosleep /sys/power/state /sys/power/wake_lock` (as root; note missing files), `getprop` filtered for `cust`, `usb.config` and `build.display` | Key codes of the volume and power keys, paths for battery and brightness, the three `/sys/power` values, the customer variant (CUST) and the value of `persist.sys.usb.config` are in the project log |
| P0.5 | Set up the compiler (as chosen by the user), build a "hello" program statically, copy it to `/data/local/tmp/`, run it as the shell user and as root | Output appears in both cases |
| P0.6 | Measure charging without Android: read the battery level and the three `/sys/power` values from P0.4, `su -c stop`, wait 15 minutes, check battery level, `/sys/power` values and `adb devices` again, `su -c start` | Result noted: charges, holds or drops; connection stayed or dropped; `/sys/power` values before and after |
| P0.7 | Create `Makefile` and directories according to section 10; write `scripts/push.sh` (build, copy, make executable) and `scripts/lifeline.sh` (section 8); copy `adb_keys` from the tablet to `backup/` and point the user to `~/.android/adbkey` (section 8) | `make` runs for both targets (x86 and ARM); `scripts/lifeline.sh` shows OK five times on running Android; `backup/adb_keys` is not empty |
| P0.8 | Only look at recovery: ask the user whether an SD card is available (note for later) and whether one is in the tablet; if one is inserted, have it removed before the reboot (section 2, `dload`); `adb reboot recovery`; the user reads out the menu items and selects exclusively the reboot. Select nothing else, in particular no "wipe". Afterwards, ask the user to try the key combination for recovery at power-on (not proven, presumably volume up + power) | Menu items verbatim in the project log; noted whether there is an item for applying a package from the SD card and whether the key combination works; Android then boots normally |
| P0.9 | The full package is in your own backup folder (section 3). Extract `dload/UPDATE.APP` into a separate empty subdirectory, execute nothing from it; at first only look at the format of `UPDATE.APP` (header data, contained sections) and note it. Do not download a third-party tool for taking it apart; if a short script of your own is not enough, stop and ask. Compare `boot` and `recovery` from the package by checksum with `backup/boot.img` and `backup/recovery.img` (if the lengths differ, compare only the common length and note that) | Result noted: identical, different or format not readable. Identical means: the package matches the device, and our own backup of these two partitions is plausible |

Acceptance phase 0:
- Root access and a self-built program work on the tablet.
- The charging behavior with Android stopped has been measured.
- If the connection drops again in P0.6: stop. The cause must be clarified before phase 1 begins.

### Phase 1: `tabletd` basic function

Read 6.1 to 6.4 beforehand.

| No. | Action | Check |
|---|---|---|
| P1.1 | Look at the touch protocol (6.3), output of a few taps to `docs/touch-sample.txt` (a recording of the author's device, not published) | Type A or B determined and noted |
| P1.2 | `tablet/proto.c`: read and write frames, length check per type; `tests/test_proto.py` | `make test` green on the laptop |
| P1.3 | `tablet/fb.c`: real framebuffer and file framebuffer; draw `RECT` with `fmt` 0, check bounds | Test writes a rectangle into the file and compares every byte |
| P1.4 | `tablet/input.c`: parser for the determined touch protocol and for keys; test feeds events like those in `docs/touch-sample` through a FIFO | Expected `TOUCH` messages arrive |
| P1.5 | `tablet/main.c`: `poll` loop over socket and input devices, `INFO`, `PRESENT` → `ACK`, `PING` → `PONG`, disconnect on protocol error | Integration test on the laptop green, also with deliberately wrong messages |
| P1.6 | Start on the tablet: `scripts/android.sh stop` (switches USB to `adb`, starts the root helper, stops Android), then `scripts/run.sh` (copies and starts `tabletd` as the shell user, sets up the forwarding) | Test client receives `INFO` with 600×1024 |
| P1.7 | `tests/testbild.py` sends an image with labeled color areas ("RED", "GREEN", "BLUE") and an arrow pointing up | User confirms: each area has the color of its label, the arrow points to the top edge of the tablet |
| P1.8 | User taps four markers in the corners one after the other | Coordinates are each at most 40 pixels from (0,0), (599,0), (0,1023), (599,1023) |
| P1.9 | `su -c start` | Android appears normally again |

Acceptance phase 1:
- `make test` is green.
- P1.7 and P1.8 are confirmed by the user.
- A faulty message drops the connection, `tabletd` keeps running and accepts a new one.

### Phase 2: Fast transfer

Read 6.5 beforehand.

| No. | Action | Check |
|---|---|---|
| P2.1 | LZ4 decoder in `tablet/lz4dec.c`; test compares against `lz4.block` with random, empty, uniform and real image data | All cases identical |
| P2.2 | Test the decoder with corrupted inputs (every byte changed once, truncated blocks), x86 build with `-fsanitize=address,undefined` | No sanitizer finding; every error case cleanly returns "error" |
| P2.3 | `host/encode.py`: comparison with the last acknowledged frame in tiles of 60×64 pixels; merge adjacent changed tiles into rectangles; send a full frame from 60 % changed area upwards; per rectangle, LZ4 only if smaller than raw | Test: reconstructed image matches byte for byte |
| P2.4 | `host/link.py`: set up `adb forward`, connect, send and receive frames, at most one unacknowledged frame in flight, automatic reconnection | Unplug and replug the cable: the connection comes back without intervention |
| P2.5 | `tests/bench.py`: three image sequences (still image, scrolling text, noise as a stand-in for video), 20 seconds each; measures acknowledged frames per second and MB/s | Three measured values in the project log |
| P2.6 | 30-minute soak test with scrolling text; before and after, `VmRSS` from `/proc/PID/status` of `tabletd` | No disconnection; memory grows by at most 1 MB |

Acceptance phase 2:
- Measured values are available and have been reported to the user.
- Scrolling text reaches at least 15 frames per second. If this is missed: name the bottleneck (USB throughput, encoding on the laptop, decoding on the tablet), do not tweak the measuring method, decide with the user.

### Phase 3: Mirroring and control (`hostd`)

Read 6.6 to 6.8 beforehand. Prerequisite: `gstreamer1.0-pipewire` is installed (section 4, item 3); check with `gst-inspect-1.0 pipewiresrc`.

| No. | Action | Check |
|---|---|---|
| P3.1 | `host/portal.py`: build the session according to 6.6; output node ID, `size` and token | User sees the KDE dialog, confirms; values appear |
| P3.2 | Second start with stored token | No dialog any more |
| P3.3 | `host/capture.py`: pipeline according to 6.7; write one frame as PNG into the scratchpad and look at it yourself | The PNG shows the laptop screen rotated, 600×1024, with black borders; noted whether the mouse pointer is visible in the image (see 6.6 step 5). If it is missing, tell the user and ask whether it bothers them; do not build a pointer of your own without consultation |
| P3.4 | Put capture, encoding and connection together (`host/main.py`, mode `mirror`) | User sees their screen on the tablet, the right way up |
| P3.5 | `host/pointer.py`: convert `TOUCH` according to 6.8; touch down = move pointer and press left button, move = move pointer, lift = release button | User can move a window by touch |
| P3.6 | Hit test: user taps one small target each near the four screen corners and in the middle (e.g. a window's close button) | All five hit; with a uniform deviation, check the reference (`size` versus pixel size) |
| P3.7 | Gestures: press longer than 0.6 seconds without movement = right click; two fingers vertically = scrolling (`NotifyPointerAxisDiscrete`) | User confirms both |
| P3.8 | Modes `monitor` and `macros`: take over the drawing functions from `src/companion.py`; macros from `~/.config/huawai-companion/macros.toml`; switching with a volume key (`KEY`) | User switches through all three modes; a macro button changes the volume |
| P3.9 | Ask the user whether `hostd` should start automatically at login (systemd user service); set it up only on yes | Answer noted |
| P3.10 | Trial (requested by the owner), at most one hour: switch `--touch=native` in `host/pointer.py` that passes `TOUCH` on as real touch events (6.6). The hit test from P3.6 is repeated | Result noted: hits are correct or not, swiping scrolls or not. If the hits are not correct, the pointer emulation from P3.5 and P3.7 stays; the switch is removed |

Acceptance phase 3:
- Mirroring, hit test and reconnection after unplugging the cable are confirmed by the user.
- After restarting `hostd`, no permission dialog appears.
- The measured frame rate during real desktop work is reported.

This completes the usable core. Up to this point, nothing on the tablet has been changed permanently.

### Phase 4: Second screen (deferred)

- The portal of this system reports no virtual screen (`AvailableSourceTypes` = 3). The value is hard-coded in `xdg-desktop-portal-kde` 6.3.5 and says less than version 2 of this plan assumed.
- Read from the source code of 6.3.5, **not checked on the device:** the selection dialog of a pure `ScreenCast` session with `types: UInt32(1)` additionally offers the entry "New Virtual Output" and then creates a virtual screen in KWin with a fixed 1920×1080. The combined session from 6.6 shows a different dialog and cannot do this.
- Input is open: the absolute pointer position needs a stream of the same session; a pure `RemoteDesktop` session has none. Whether real touch events (6.6) land on the virtual screen has not been checked.
- The other route, `krfb-virtualmonitor`, is not installed and, according to several KDE bug reports, prone to crashing under Wayland.
- **Still deferred, with a cheap trial.** The owner requested the trial; it runs after phase 3 has been accepted:

| No. | Action | Check |
|---|---|---|
| P4.1 | Small script of our own `tests/virtual_probe.py` (a one-off probe, not kept): pure `ScreenCast` session (`CreateSession`, `SelectSources` with `types: UInt32(1)`, `Start`), output the stream size, end after 30 seconds | User reports whether the dialog offers a virtual output and whether a second screen then appears in the KDE display settings |

If the dialog offers nothing: phase 4 stays closed; tell the user that it stays with mirroring. If it does offer it: stop and decide with the user whether phase 4 is worked out (image size 1920×1080 onto 1024×576, input unresolved). Do not continue building without this decision.

### Phase 5: From program to mini OS

**Separate session, user at the device, every step individually.** The owner has permitted reprogramming the device, even at the risk of bricking it completely. The safeguards apply nevertheless, because a locked-out device ends the project. Read section 2 and section 8 beforehand.

Basic idea: Android is not removed and replaced, but **stopped** at boot. The same `stop` that has already worked by hand is executed automatically by a boot script, which then starts `tabletd`. If anything fails, Android starts as usual.

**5a. Read the boot sequence (change nothing)**

| No. | Action | Check |
|---|---|---|
| P5.1 | Fetch all `/init*.rc` and `/*.rc` into `device/init/` (your own copies, not shipped). In the USB files, look for the line `on property:sys.usb.config=VALUE` whose VALUE corresponds to the actual `persist.sys.usb.config` from P0.4, and check that `start adbd` is there | Files are available; noted which rule starts `adbd` and that it works without the Android user interface |
| P5.2 | Find out which script or program under `/system` is executed at boot **as root**. Expectation: a service `flash_recovery` that executes `/system/etc/install-recovery.sh`. In the AOSP original of Android 4.4.2 it reads like that (`class main`, `oneshot`, no `user`, therefore root, no `seclabel`); whether Huawei adopted it is only shown by P5.1. KingRoot presumably uses the same file, which then already contains foreign lines: look at them, change none of them. Search the `.rc` files for `/system/etc/` and `/system/bin/*.sh` | Hook point with file, service name, user and class in the project log; existing content of the file is in `backup/system-files/` |
| P5.3 | Create a list of all services from the `.rc` files: name, program, class, "critical" yes/no, running according to `getprop init.svc.NAME` | Table in `docs/ANDROID-SERVICES.md` |

If there is no hook point that runs as root: **stop** and report to the user. The fallback route (replacing programs in `/system/bin` with launchers) has pitfalls with user permissions and SELinux labeling and is not started without consultation.

**5b. Switch on in stages**

The launcher `tabletd-launch` is a shell script under `/system/bin/` that is called from the hook point in the background with a single line. Its logic:

1. Read the mode from `/data/tabletd/mode`. If the file is missing or the content is unknown: `android`.
2. For `mini-once`: **first** write the file back to `android`, then continue as with `mini`. The next boot is thus automatically Android again.
3. If a volume key is held (helper program `tb-keyheld`, 6.3): `android`.
4. Increase the counter `/data/tabletd/unconfirmed` by 1. If it is above 3: `android`.
5. For `android`: do nothing, exit.
6. For `mini`: `setprop sys.usb.config adb` (without MTP, see section 3; `persist.sys.usb.config` stays unchanged so that a boot in Android mode has `mtp,adb` again), `stop`, wait 2 seconds, start the root service `tb-rootd` and then `tabletd`. If `tabletd` exits three times within one minute, call `start` (Android boots up) and exit. According to the AOSP source code, `stop` without an argument stops only `zygote` and `surfaceflinger`; all other services keep running until P5.20 deals with them one by one. A wake lock is not necessary according to P0.6: the device did not fall asleep without Android.
7. Write every decision with a timestamp to `/data/tabletd/boot.log`.

**Root access in mini mode (decision).** KingRoot's `su` needs the running Android user interface and fails in mini mode. Without a replacement there would be no lifeline there. Therefore a small service of our own is added: `tb-rootd` (C, static, target below 150 lines), started by the launcher as root, listens on the abstract socket `tabletd-root`, accepts only connections from users 0 and 2000 (`SO_PEERCRED`), executes the passed command line with `/system/bin/sh -c` and returns output and exit code. The counterpart `tb-su` calls it from `adb shell`. It thereby grants exactly what `su` already grants under Android (root for anyone with ADB access), no more. It is not started in Android mode. KingRoot's `su` stays untouched. `scripts/lifeline.sh` then checks root via `su` or, when Android is stopped, via `tb-su`.

`tabletd` resets the counter to 0 as soon as a laptop is connected and the first `PRESENT` has arrived. If that fails to happen three times, Android comes up on its own on the fourth boot.

| No. | Action | Check |
|---|---|---|
| P5.4 | Pull the original file of the hook point to `backup/system-files/`; append a line that only writes a log entry (date, `id`, SELinux context); reboot | Android boots normally; the log entry shows uid 0 |
| P5.5 | Install `tabletd-launch`, `tb-keyheld`, `tb-rootd` and `tb-su`, mode `android`; reboot. Beforehand, on running Android: start `tb-rootd` by hand as root and check that `tb-su id` from `adb shell` returns uid 0 and that a call as another user is rejected | Android boots normally; `boot.log` shows the decision `android`; `tb-su` probe passed |
| P5.6 | Set mode `mini-once`; reboot | User sees the `tabletd` display; `adb devices` shows the device; `scripts/lifeline.sh` is green; `hostd` connects |
| P5.7 | Leave it in this state for 15 minutes | No reboot, no shutdown, ADB stays reachable |
| P5.8 | Reboot without any further change | Android boots (one-time mode has reset itself) |
| P5.9 | Set `mini-once`, hold a volume key at power-on | Android boots |
| P5.10 | Mode `mini`, three boots without a connected laptop, then a fourth | First three: `tabletd`; fourth: Android |
| P5.11 | Mode `mini`, ten cold boots with laptop | Ten times `tabletd` with connection; time from power-on to image measured |

If no ADB comes up in P5.6: do nothing except reboot; the one-time mode brings Android back. Then stop and report.

**5c. Operation without a laptop**

| No. | Action | Check |
|---|---|---|
| P5.12 | Status page in `tabletd` when no laptop is connected: time (from `TIME`, otherwise "--:--"), battery, connection state. Embedded 8×16 bitmap font, rotation from `/data/tabletd/rotation` | User confirms a legible display in the chosen orientation |
| P5.13 | Power key: short = display off/on (brightness 0 and back), 3 seconds = clean power-off (`sync`, then power off). For this, a small root helper in the launcher remains responsible; `tabletd` itself no longer has root privileges | User confirms both functions; after the power-off, the device boots normally |
| P5.14 | Dim the display after 5 minutes without laptop and without touch; touch wakes it | User confirms |
| P5.15 | If P0.6 showed that there is no charging without Android: look for the cause using `docs/ANDROID-SERVICES.md` (which service is missing after `stop`) and decide with the user | Charging in mini mode proven, or phase 5 ends here with a clear statement |

**5d. Remove the Android layer**

Prerequisites, all to be met: P5.4 to P5.15 passed; `scripts/lifeline.sh` green; the user has seen the deletion list and confirmed it with yes.

**Read on the device:** Huawei's `init.rc` marks `zygote` as "critical". If the framework is missing after 5d, `zygote` crashes immediately at every boot; after more than four crashes in four minutes, `init` boots the device into recovery. The launcher must therefore stop `zygote` with `stop` within seconds at every power-on. If the launcher fails even once after 5d, the device ends up in recovery, and from there only the never-tested factory package leads back. Tell the user this explicitly before P5.16. P5.10 and P5.11 (repeated boots) must have run without errors beforehand, and P5.19 (delete the framework) is only executed once `boot.log` shows for each of these boots how many seconds passed between boot and `stop`.

After this step there is no tested way back to Android any more. The factory package via recovery (section 8) is a possible but never tested route; do not present it to the user as a safety net. The counter from 5b loses its purpose; disable the threshold in `tabletd-launch` as soon as 5d is complete.

| No. | Action | Check |
|---|---|---|
| P5.16 | Write the file list of `/system/app`, `/system/priv-app`, `/system/framework`, `/data/app` to `docs/`; tell the user the scope and consequences | Explicit yes from the user |
| P5.17 | Remove the KingRoot app (`com.kingroot.kinguser`), `su` stays; reboot | `su -c id` still returns uid 0; lifeline green. If `su` no longer works afterwards: copy the app back from `backup/` and stop |
| P5.18 | Empty `/system/app` and `/system/priv-app`; reboot | `tabletd` and ADB come up; lifeline green |
| P5.19 | Empty `/system/framework`, `/data/app`, `/data/data`, `/data/dalvik-cache`. Do **not** touch: `/data/misc`, `/data/property`, `/data/tabletd`, `/data/local`; reboot | as P5.18 |
| P5.20 | Stop native services that are not needed in the launcher via `stop NAME`. Always add only one service at a time, observe for 10 minutes, take notes. Services marked "critical" and everything concerning charging, USB or display stay. If a service leads to a reboot or loss of ADB, it runs again | `docs/ANDROID-SERVICES.md` shows per service: stopped or needed, with observation |
| P5.21 | 24 hours on the power adapter | Device runs, ADB reachable, battery level and temperature noted at the beginning and end |

Acceptance phase 5:
- Ten cold boots in a row end in `tabletd`.
- `ps` shows neither `zygote` nor `system_server` nor `surfaceflinger`; the process list is in `docs/`.
- Switching on and off by key works.
- The lifeline is green.

### Phase 6: Hardening within the limits of what is possible

Document every measure with its actual benefit. None of them makes up for the old kernel.

| No. | Measure | Implementation | Evidence | Benefit |
|---|---|---|---|---|
| P6.1 | Radio off | Never start Wi-Fi, Bluetooth and mobile network services (P5.20) | `netcfg` shows only `lo` active; `/sys/class/rfkill/*/state`; no radio services in `ps` | No remote attack surface. Whether the mobile radio chip really does not transmit cannot be proven without measuring equipment; write it down that way |
| P6.2 | No open ports | Abstract socket or `127.0.0.1` | `/proc/net/tcp` and `/proc/net/udp` without listening entries except, if applicable, port 7100 on loopback | Reachable only over the cable |
| P6.3 | Drop privileges | According to 6.4, own user ID (e.g. 2900) | `/proc/PID/status` shows Uid and Gid not equal to 0 | A bug in the protocol code does not immediately give root |
| P6.4 | System call filter | Presumably dropped: P0.4 showed no `Seccomp` line in `/proc/self/status`. Cross-check once with `prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, …)` and note the result | Return value of the call noted | Would have been a further restriction; not feasible without kernel support |
| P6.5 | Secure compilation | `-fstack-protector-strong -Wall -Wextra -Werror`, `-static-pie` if the toolchain supports it, `-Wl,-z,relro,-z,now` | Build without warnings; `readelf -h` shows the type | Makes exploiting memory errors harder |
| P6.6 | Fuzzing | x86 build with AddressSanitizer; random and mutated message sequences against parser, drawing routine and LZ4 decoder, at least one hour | No finding, or findings fixed and recorded as tests | Finds bugs before the device does |
| P6.7 | `/system` read-only | At the end of the launcher, `mount -o remount,ro /system` | `mount` shows `ro` | Protection against accidental changes |
| P6.8 | Checklist | On connecting, `hostd` compares the checksums of the files under `/system/bin/tabletd*` with a list on the laptop | A modified file is reported | Notices changes, does not prevent them |
| P6.9 | Pairing (`AUTH`) | Shared key in `/data/tabletd/key`; SHA-256 and HMAC implemented ourselves, verified with the official test vectors | Wrong key: no frame accepted | Only useful if `su` is removed; as long as root via ADB is open, the key can be read out |
| P6.10 | Root access | Present both consequences to the user: with `su`, anyone with USB access has root; without `su`, no change or recovery is ever possible again | User's decision noted; remove only on explicit yes | Up to the owner |

Acceptance phase 6:
- Every row is implemented and proven, or marked as not feasible with a reason.
- `docs/SECURITY.md` describes what the device protects against and what not, in the language of section 1.

### Phase 7: USB installation device

Owner's request: the tablet should additionally behave like a USB stick from which a computer can install an operating system. Owner's decision: a separate phase, **no attempt before that**. It requires phase 5, because switching the USB mode needs root and the launcher runs as root there.

Basis, read on the device: the kernel has the mass storage function with two drives; Huawei's boot rules know `mass_storage,adb` (USB ID `12d1:1038`), ADB is preserved in that mode. From research, not checked on the device: the CD emulation of this kernel function ends at about 2.2 GB; larger images must be offered as a removable drive (`cdrom` = 0, `ro` = 1). This works with images that also boot as a USB stick (common for Linux distributions), not with an unmodified Windows ISO.

Limits to be named to the user as follows: USB 2.0, therefore slow; internally about one image fits (`/data` has 4.7 GB), it becomes sensible with an SD card; whether a particular computer boots from it is only shown by trying it on that computer. An earlier attempt by the owner exists as scripts (not published); its result is unknown.

| No. | Action | Check |
|---|---|---|
| P7.1 | Ask the user: SD card available and wanted? Otherwise it stays with internal storage. Decide the storage location of the images (`/data/tabletd/images/` or the SD card) | Answer and storage location noted |
| P7.2 | Read the rules for `mass_storage` and `mass_storage,adb` in `device/init/init.sc8830.usb.rc` (your own copy) completely; read the permissions and meaning of `lun/file`, `lun/ro`, `lun/cdrom` on the device | Procedure for switching on and off is in the project log |
| P7.3 | `tb-usbstick` (shell script, runs via `tb-rootd`): `on IMAGE` sets `ro` = 1, `cdrom` = 0, `file` = IMAGE and switches to `mass_storage,adb`; `off` empties `file` and switches back to `adb`. Accepts only files from the storage location | A drive appears on the laptop whose size according to `lsblk -b` matches the file size; ADB and lifeline stay green; after `off` the drive is gone |
| P7.4 | Check the content: mount the drive on the laptop read-only (via the user's file manager or `udisksctl`, no `sudo` without a yes) and compare three files with the image | Checksums equal |
| P7.5 | Selection on the tablet: the status page (P5.12) gets a list of the images; tapping switches on, tapping again switches off; the display names the active image | User confirms switching on and off by touch |
| P7.6 | Boot attempt on one of the user's computers | User reports: boots or does not boot, with the computer's model. "Does not boot" is a permissible result and is documented as such |
| P7.7 | Only with an SD card and only on request: offer the card itself as a writable drive (tablet as card reader) | User can write an image to the card from the laptop; explicit warning beforehand that the card's content is lost in the process |

Acceptance phase 7:
- Switching on and off works from the tablet; ADB stays reachable in both states.
- The result of the boot attempt is noted.
- `docs/SECURITY.md` names the new attack surface: a connected computer can read the offered image, nothing else.

---

### Extensions after phase 3 (owner's requests)

Order according to the owner's decision: Wi-Fi, then Windows, design; Android is removed only at the very end (5d). The owner accepts the risk of Wi-Fi and requires it to be made as secure as possible.

| No. | Content | Status |
|---|---|---|
| W1 | Wireless: direct, encrypted and mutually authenticated connection over Wi-Fi (port 7100), pairing only via USB, firewall restricted to the laptop addresses; cable as a second route. Design and limits in `docs/SECURITY.md` | implemented and tested by the owner |
| W2 | Windows as a second system for `hostd` (screen capture, pointer, keyboard under Windows) | deferred (no Windows computer available) |
| W3 | (implemented) Design: waiting page, monitor, macro and keyboard views in a common look | implemented |
| W5 | Own system on the device: home screen `tb-home` with programs as separate processes; the first and for now only program is the "Remote control" (`tabletd`). Back to the home screen with both volume keys | implemented and tested by the owner; until phase 5 it starts via `huawai up` |
| W6 | Linux side complete: settings file, command `huawai`, installation `scripts/install.sh`, guide `docs/USAGE.md` | implemented |
| W4 | Wi-Fi in mini mode: the launcher brings up Wi-Fi without Android (`wpa_supplicant`, `dhcpcd`), sets the firewall as root and starts `tabletd` with `--secure-listen` | open, belongs to phase 5 and requires the hook point (P5.4 is on hold) |

---

## 8. Lifeline and recovery

**Lifeline** means: `adbd` is running, the ADB authorization is valid, root is reachable (under Android via `su`, with Android stopped via `tb-su`, see phase 5b). As long as this holds, every file change in `/system` and `/data` can be undone.

`scripts/lifeline.sh` checks and prints OK or ERROR per line:

1. `adb get-state` returns `device`.
2. `adb shell su -c id` contains `uid=0`. If Android is stopped, `adb shell tb-su id` instead (from P5.5; until then, the check only applies under Android). The check is only meaningful after boot has completed; 45 seconds after a reboot it still failed once.
3. `/data/misc/adb/adb_keys` exists and is not empty.
4. `getprop persist.sys.usb.config` contains `adb`.
5. `/system/xbin/su` and `/system/bin/sh` exist.

In addition, once in phase 0: copy `adb_keys` from the tablet to `backup/`. Point out to the user that `~/.android/adbkey` on the laptop is the matching key and must not be deleted.

**Restoring individual files:** from `backup/system-files/` via `adb push` to `/data/local/tmp/`, then copy to the target location as root, set permissions and owner as before.

**Second rescue route, not tested:** the manufacturer's recovery together with the factory package on an SD card. It would work without ADB and would thus be the only route that could bring back a locked-out device. Proven so far: the package has been downloaded and contains `dload/UPDATE.APP` along with Huawei's instructions (section 3). According to these instructions, the update starts on its own from a FAT32 card at power-on and ends with a device in factory state: `userdata` is formatted, so the ADB authorization is gone, but the Android user interface is back, through which the user can switch ADB on again. Open questions: whether the automatic start happens on this device and without `au_temp.cfg`, whether the recovery checks a signature, whether the package matches the customer variant, and what overwriting the NV partitions does (own backup available in `backup/`). P0.8 clarifies whether the recovery is reachable and which menu items it has, P0.9 whether the package matches the device. **Flashing itself is not carried out as a test:** it overwrites `system`, thereby removes root and the hook point, and deletes all data. Before 5d, tell the user the status of these two steps. A forum report from 2017 describes a non-booting third-party recovery and failed attempts via `fastboot` for this model; the write prohibition for `recovery` from section 2 remains.

**Rescue below Android (Spreadtrum download mode):** the open-source tool `spd_dump` can read and write partitions, but for that it needs the loader programs FDL1 and FDL2 from a `.pac` package for the device. None was found for this model, and the chipset is not in the list of tested ones. Not a usable route today; do not try it, an FDL from another device can damage the hardware according to the tool's documentation.

**Restoring entire partitions:** there is no tested procedure for this yet. Do not build one on the side. If the user wants it, that is a separate task: a small static tool that receives a data stream via `adb forward`, writes it to a block device and compares the checksum; to be tested exclusively on the empty `cache` partition.

---

## 9. Risks

| Risk | Consequence | Countermeasure |
|---|---|---|
| ADB does not come up in mini mode | Device locked out | One-time mode (P5.6), counter, volume key; everything tested several times before 5d |
| Deleting in 5d hits something needed | Boot loop or recovery | Only the four named directories, reboot and lifeline after every step |
| `zygote` is "critical": without the framework and without a timely `stop`, the device boots into recovery | After 5d no way back except the untested factory package | Measure the time until `stop` in `boot.log` (P5.10, P5.11); delete the framework only afterwards; tell the user before 5d |
| Charging does not work without Android | Device shuts down in continuous operation | P0.6 measures it, P5.15 must solve it before 5d |
| The earlier connection drop recurs | Work on the device unreliable | P0.3 and P0.6; stop if it recurs |
| Pointer position wrong at scaling 1.1 | Touch misses | P3.6 |
| USB throughput is not enough | Choppy transfer | P2.5 measures; set a new target with the user |
| PipeWire plugin in the wrong version | Installation fails or pulls in a downgrade | Specify the version explicitly (section 4) |
| Old battery in continuous charging operation | Swelling, fire hazard | Point this out to the user; do not leave unattended in a flammable environment |
| Portal permission lost after a KDE update | Dialog appears again | Error case handled (6.6, step 9) |
| Device falls asleep as soon as Android is stopped | ADB drops; possible cause of the earlier connection drop, not proven | P0.4 and P0.6 read `/sys/power/*`; conditional wake lock in the launcher (5b, item 6) |
| Mouse pointer missing from the transmitted image | Mouse operation on the laptop cannot be followed on the tablet | P3.3 determines it; decision lies with the user |
| Factory package from a third-party site is not genuine or does not match | False sense of safety, in the worst case damage when flashing | P0.9 compares with our own backup; flashing is not part of this plan |

---

## 10. Directory structure

```
t1-701u-remote-linux/
├── docs/PLAN.md             this document
├── .claude/skills/tablet-phase/SKILL.md   procedure of a work session
├── Makefile                 targets: all, test, arm, x86, push
├── backup/                  partition images (do not modify), system-files/, adb_keys,
│                            firmware/ (factory package, for comparison only)
├── tablet/                  tabletd.h main.c proto.c fb.c input.c lz4dec.c font.c  (tabletd)
│                            keyheld.c (tb-keyheld), rootd.c (tb-rootd), tbsu.c (tb-su),
│                            tabletd-launch.sh, root-helper.sh (tests only)
├── host/                    main.py link.py portal.py capture.py encode.py pointer.py modes.py
│                            keyboard.py
├── tests/                   harness.py, test_*.py, lz4cli.c, testbild.py, fbcompare.py,
│                            bench.py, soak.py, fuzz.py
├── scripts/                 push.sh, run.sh, android.sh, lifeline.sh, updateapp.py, init-services.py,
│                            genfont.py, huawai-hostd.service
├── tools/                   compiler, if stored locally
├── docs/                    ANDROID-SERVICES.md, init/, SECURITY.md, measured values
│                            (the project log is not published)
└── src/companion.py         old prototype
```

`backup/` is a directory the user creates themselves; the project ships no backups.

---

## 11. Template for the project log

```markdown
# Status

## Current state
- Current phase:
- Next step:
- Open questions for the user:

## User's answers (section 4)
- Compiler:
- Git:
- Orientation of the tablet:

## Findings about the device
| Date | What | Value / observation | How checked |
|---|---|---|---|

## Steps
| Step | Date | Result (passed / failed / not checked) | Note |
|---|---|---|---|

## Changes on the tablet
| Date | File | What was changed | Backup at |
|---|---|---|---|
```

---

## 12. Sources

- Desktop portal RemoteDesktop (methods, `restore_token`): https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.impl.portal.RemoteDesktop.html
- KDE portal, fix for the absolute pointer position with scaling: https://invent.kde.org/plasma/xdg-desktop-portal-kde/-/merge_requests/210
- Portal input in krfb as example code: https://lxr.kde.org/source/network/krfb/events/xdp/xdpevents.cpp
- Crashes of `krfb-virtualmonitor` under Wayland: https://bugs.kde.org/show_bug.cgi?id=458636 and https://bugs.kde.org/show_bug.cgi?id=462401
- Zig cross-compilation, CPU specification for older ARM devices: https://ziggit.dev/t/cross-compilation-rpi-1b-target/1093
- Android boot classes, documented only for newer versions: https://sx.ix5.org/info/android-init-stages/
- AOSP 4.4.2, `init.rc` (service `flash_recovery`, "critical" services) and `init.usb.rc` (start of `adbd`): https://android.googlesource.com/platform/system/core/+/refs/tags/android-4.4.2_r1/rootdir/init.rc and https://android.googlesource.com/platform/system/core/+/refs/tags/android-4.4.2_r1/rootdir/init.usb.rc
- AOSP 4.4.2, `stop` without an argument: https://android.googlesource.com/platform/system/core/+/refs/tags/android-4.4.2_r1/toolbox/stop.c
- `xdg-desktop-portal-kde` 6.3.5, selection dialog with virtual output and touch methods: https://invent.kde.org/plasma/xdg-desktop-portal-kde/-/raw/v6.3.5/src/screenchooserdialog.cpp , https://invent.kde.org/plasma/xdg-desktop-portal-kde/-/raw/v6.3.5/src/screencast.h , https://invent.kde.org/plasma/xdg-desktop-portal-kde/-/raw/v6.3.5/src/remotedesktop.cpp
- `spd_dump` (Spreadtrum download mode): https://github.com/ilyakurdyukov/spreadtrum_flash
- Limit of the CD emulation in the mass storage gadget at about 2.2 GB: https://groups.google.com/g/usbarmory/c/pm9QBj2R-0I and https://docs.pikvm.org/msd_legacy/

Not proven by measurement on the device and to be checked there: the hook point in P5.2 and the start of `adbd` without the Android user interface (both only looked up for the AOSP original, not for Huawei's version), the reachability of an abstract socket by `adbd`, whether static programs run on kernel 3.10, the orientation of the touch axes, everything marked in 6.6 and phase 4 as "read from the source code".

---

## 13. Changes

### Version 3 compared with version 2

Basis: internet research. Nothing was checked on the device in the process.

| Location | Change | Reason |
|---|---|---|
| Section 3, Backup | Statement "firmware B107 not found" replaced | Full package is listed on a third-party site; unchecked |
| Section 4 | Item 6 new (firmware download only with the user's yes) | Consequence of the above |
| 6.6 | Notes on mouse pointer, multiple screens and touch methods | Source code of `xdg-desktop-portal-kde` 6.3.5 |
| P0.4, P0.6 | `/sys/power` values, customer variant, USB setting | Narrow down the earlier connection drop; determine the firmware variant |
| P0.8, P0.9 | new: look at recovery, compare package with backup | Second rescue route; until now, every route without ADB was missing |
| P3.3, P3.10 | Pointer check; trial with real touch events | Source code as above |
| Phase 4 | Reasoning corrected, trial P4.1 | `AvailableSourceTypes` = 3 does not rule out the virtual output |
| P5.1, P5.2, 5b item 6 | Expectations backed by AOSP source code; conditional wake lock | AOSP 4.4.2 |
| 5d, section 8, section 9 | Rescue routes and new risks | as above |
| Section 0, section 10 | Reference to the project skill `tablet-phase` | Requested by the owner |
| Section 4 item 6, P3.10, P4.1 | Owner's decisions recorded: download the factory package yes, both trials wanted | Owner's answer |
| Section 2, section 3, P0.8, P0.9, section 8 | Factory package downloaded and described; new prohibition for SD cards with `dload` | Content of the package and Huawei's instructions in it |
| P0.7 | `scripts/lifeline.sh` and the copy of `adb_keys` added | Both were only in section 8 and had no step number; phase 5 requires the script |

### Version 4 compared with version 3

Basis: measurements on the device in phases 0 to 2 and the owner's decisions. Version 3 is not published.

| Location | Change | Reason |
|---|---|---|
| Header | Working mode "without the owner's involvement" with its limits | Owner's instruction |
| Section 3 | Rows root, ADB, compiler corrected; new subsection with the measurement results | P0.2 to P2.5 |
| Section 5 | Transport fixed; USB mode `adb` without MTP; `tabletd` as the shell user; root helper for tests | P0.6, P1.6 |
| P1.6 | Procedure via `scripts/android.sh` and `scripts/run.sh` instead of `su -c stop` and root | `su` does not work without Android |
| 5b item 6 | `setprop sys.usb.config adb` before `stop`; wake lock dropped | P0.6: the dropouts came from the laptop's MTP service, the device did not sleep |
| 5b, P5.5, section 8 | Own root service `tb-rootd` with `tb-su`; lifeline checks root depending on the state | KingRoot's `su` fails in mini mode |
| P6.4 | Presumably dropped | Kernel without seccomp |
| Phase 7 | New: USB installation device | Owner's request and decision; research on the 2.2 GB limit of the CD emulation |
| 5d, section 9 | Note and risk: `zygote` is "critical" on this device | `device/init/init.rc` (your own copy), line 572 |
| Section 10 | Directory structure adapted to the actual state | — |
| Section 5 | On-screen keyboard in `hostd` (lifts the rule "only pointer and macros" for mirror mode); two-finger zoom; image processing in Python instead of GStreamer | Owner's requests and decision; GStreamer crash when changing settings in the running stream |
| P3.10 | Trial carried out: real touch events do not respond; switch removed | Owner's feedback |
| P5.12 to P5.14 (implementation) | Status page behind the switch `--status-page`; power-off via exit code 42 from `tabletd`, which the launcher answers with `sync` and `reboot -p`; the power key is not reported to the laptop | Preparatory work on the laptop, not yet set up on the device |
| Section 12 | Sources on the CD limit added | — |
