# Security: what the system protects against and what it does not

Written in the terms of section 1 of [PLAN.md](PLAN.md): the tablet does not reach the level of a hardened
system such as GrapheneOS, and nothing here makes up for the old kernel.

## Connection paths

| Path | Protection | Who can get in |
|---|---|---|
| USB cable via `adb` | ADB checks the laptop's RSA key; the data itself is unencrypted but only travels through the cable | whoever holds the cable |
| Wi-Fi direct, port 7100 | encrypted and mutually authenticated (below), plus the firewall | only whoever has the shared key |
| Call from outside (optional), laptop port e.g. 7110 | same encryption and key check as port 7100; the laptop runs up to four handshakes at once, a new caller pushes out the oldest waiting one, and only an address that failed is paused for 2 s | only whoever has the shared key; strangers can at most slow the call down |
| Local socket `@tabletd` on the tablet (used by `adb` for the cable link) | the caller's uid is checked (`SO_PEERCRED`): only root, adbd (2000) and `tabletd`'s own uid; `--local-uids` changes the list | Android apps and the Linux session are refused |
| `adb` over Wi-Fi, port 5555 | only the ADB key and the firewall, **unencrypted**, gives a shell with root access | meant for setup only; should be off (see "Status") |

## The encrypted Wi-Fi connection

- **Building blocks:** Monocypher 4.0.3 on the tablet, libsodium (via PyNaCl) on the laptop. Both
  are widely used, reviewed libraries. The project contains no home-made cryptography; it only
  connects these building blocks (`tablet/secure.c`, `host/secure.py`).
- **Pairing:** `scripts/pair.py` generates a random 32-byte key and copies it to the tablet
  exclusively over the USB cable. It never travels over Wi-Fi.
- **Connection setup:** both sides generate a new X25519 key pair for every connection. From the
  shared result and the shared key, BLAKE2b derives two session keys, one per direction.
- **What this achieves:**
  - Only whoever has the shared key can establish a connection, in both directions: the tablet
    checks the laptop, and the laptop checks the tablet.
  - Someone who records the traffic and later steals the key still cannot decrypt old sessions
    (forward secrecy through the one-time keys).
  - Every message is encrypted with XChaCha20-Poly1305 and protected against modification; a
    counter per direction makes replay and reordering detectable. Every error ends the connection.
- **What the tablet processes before authentication:** a 36-byte greeting and at most 128 bytes of
  a first message. The connection is accepted only once this first message is encrypted with the
  correct key. A foreign connection does not displace the authenticated laptop, and a peer that
  stays silent is dropped after 5 seconds.
- **Key files:** on the laptop `~/.config/huawai-companion/key`, on the tablet
  `/data/local/tmp/tabletd-secret/key`, both readable only by their owner. `tabletd` does not
  start if the file is readable by others.

## Further protective measures

- **Firewall** (`scripts/firewall.py`): ports 7100 and 5555 accept only this laptop's addresses in
  the Wi-Fi network; everything else is dropped. The rules last until the next reboot of the tablet
  and so far have to be set while Android is running (`su` only works then).
- **Separate programs:** the remote control (`tabletd`) is a separate process that the start screen
  (`tb-home`) starts only on request. The encrypted port is open only while it runs.
- **Few privileges:** in test mode `tabletd` runs as the shell user; in mini mode it runs as its own
  user 2900 (after opening devices, key and port). `tb-home` does not process any network data.
- **Sound:** travels inside the same encrypted connection as the picture. On the tablet, `tb-audio`
  opens the sound card while still root and then drops to the same unprivileged user as `tabletd`;
  it only plays what `tabletd` hands it through a pipe.
- **Checked transfers:** `tb-fetch` reports the BLAKE2b-256 of exactly the bytes it received;
  `scripts/linux.sh iso-send` compares it with the file on the laptop and deletes the copy on the
  tablet if they differ, so no foreign image can end up as a USB installation medium.
- **Hardened build:** stack protection, RELRO, all warnings treated as errors (P6.5).
- **Testing:** 18 tests for the encrypted connection alone (wrong key, tampered, replayed and
  oversized messages, weak peer keys, timeout). They caught four deliberately introduced bugs. In
  addition, fuzzing: one hour against protocol, input and decoder, plus 20 minutes including the
  encrypted connection (37005 rounds against it alone), both without findings.

## What the system does not protect against

- **The kernel from 2016.** It has known vulnerabilities, and the Wi-Fi driver in the kernel
  processes radio packets before any program of this project sees them. Someone within radio range
  who knows a suitable vulnerability gets past the encryption. This project cannot fix that.
- **Whoever holds the tablet** has a root shell over USB (KingRoot). With it they can also read the
  shared key.
- **With the shared key** someone can impersonate the tablet. If they manage to take over the
  tablet's address in the Wi-Fi network, `hostd` sends them the screen content and accepts touch and
  keyboard input from them, so they can type on the laptop. After any loss of control over the
  tablet: pair again with `scripts/pair.py --new` (or `huawai pair --new`).
- **The on-screen keyboard** lets whoever operates the tablet enter arbitrary text on the laptop,
  including commands in a terminal. This is a deliberate design decision.
- **The firewall** checks only sender addresses. Someone who spoofs the laptop's address in the
  Wi-Fi network gets through the firewall, but not through the encryption.
- **The Wi-Fi password** is stored in the Android configuration on the tablet.
- **Availability:** someone who jams or disrupts the Wi-Fi can interrupt the connection. `hostd`
  then reconnects by itself.

## Status and open points

- `adb` over Wi-Fi (port 5555) is off unless switched on with `scripts/adb-wifi.sh on`; switch it
  off again with `scripts/adb-wifi.sh off` (a reboot of the tablet also switches it off). With it
  off, only port 7100 listens on the tablet's Wi-Fi interface.
- Fixed addresses: `hostd` connects to the tablet's address stored in `hostd.toml`, and the tablet's
  firewall allows only the laptop's addresses at the time it was set. If the router assigns other
  addresses, nothing works until it is set up again. Recommendation: fixed assignments (DHCP
  reservations) in the router for tablet and laptop.
- Mini mode: the launcher (`tablet/tabletd-launch.sh`) has to set the firewall as root before
  `tabletd` listens; it applies the same rules as `scripts/firewall.py`. Wi-Fi is still brought up
  by Android before the launcher takes over; bringing up Wi-Fi without Android has not been built.
