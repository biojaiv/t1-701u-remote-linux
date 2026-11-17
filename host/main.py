#!/usr/bin/env python3
"""hostd - the laptop side: mirrors the screen to the tablet and takes touch.

Modes (switch with a volume key on the tablet):
  mirror   the laptop screen, touch moves the pointer
  monitor  system monitor with four macro buttons
  macros   up to eight macro buttons

Usage: python3 -m host.main [--mode mirror] [--rotation counterclockwise]
"""
import argparse
import os
import struct
import sys
import time
import traceback

from gi.repository import GLib

from . import capture, config, encode, keyboard, link, modes, pointer, portal, secure

MODES = ("mirror", "monitor", "macros")
KEY_VOLUMEDOWN, KEY_VOLUMEUP = 114, 115
# the main loop runs often only while something happens: frames on their way,
# touches or a held key; otherwise it saves the laptop's CPU (measured
# 2026-11-16: about 40 % of a core while mirroring)
PUMP_BUSY_MS, PUMP_QUIET_MS, PUMP_ALONE_MS = 4, 20, 100
BUSY_AFTER_INPUT_S = 1.0
MONITOR_PERIOD_S = 1.0
PORTAL_RETRY_S = 3


class Hostd:
    def __init__(self, mode, rotation, port, use_adb=True, wifi=None, learn_address=False,
                 internet_port=0, audio=True):
        self.mode, self.rotation = mode, rotation
        self.audio_wanted = audio
        self.audio = None            # AudioSender while connected and the tablet plays sound
        self.learn_address = learn_address
        # two frames in flight: sending the next overlaps with drawing this one
        if wifi or internet_port:
            # the cable stays the second choice, on another local port; the
            # tablet may also call from outside on internet_port
            self.session = link.Session(port=port, host=wifi, psk=secure.load_key(),
                                        max_in_flight=2, use_adb=use_adb,
                                        adb_port=port + 1, listen_port=internet_port or None)
        else:
            self.session = link.Session(port=port, use_adb=use_adb, max_in_flight=2)
        self.encoder = None
        self.frame = None            # newest frame not yet sent
        self.macros = modes.load_macros()
        self.screens = {}            # created once the tablet's size is known
        self.portal = self.capture = self.gestures = self.view = None
        self.portal_size = self.pipewire_fd = None
        self.keyboard = None             # on-screen keyboard over the mirrored picture
        self.keyboard_fingers = set()    # slots that landed on the keyboard
        self.key_repeat = keyboard.KeyRepeat()   # Backspace and arrows while held
        self.last_input = 0.0            # monotonic time of the last touch or key
        self.last_mirror_frame = None   # PipeWire sends nothing while the screen is still
        self.next_render = 0.0
        self.acked = self.sent_bytes_mark = 0
        self.stats_at = time.monotonic()

    # -- tablet connection --------------------------------------------------

    def on_connected(self, width, height):
        print(f"tablet connected over {self.session.route}: {width}x{height}", flush=True)
        if self.session.route == "adb" and self.learn_address:
            self.learn_wlan_address()
        # over Wi-Fi bytes are scarcer than laptop CPU time
        # COPY (scrolling: the tablet moves what it shows) if this tabletd knows it
        copy = bool(self.session.link and self.session.link.flags & link.INFO_COPY)
        self.encoder = encode.Encoder(width, height,
                                      strong=self.session.route in ("wifi", "internet"),
                                      ordered=True, copy=copy)
        canvas = (height, width)
        self.screens = {"monitor": modes.MonitorScreen(self.macros, canvas),
                        "macros": modes.MacroScreen(self.macros, canvas)}
        # a "Back" button in the menus if the tablet understands HOME
        back = bool(self.session.link and self.session.link.flags & link.INFO_HOME)
        for screen in self.screens.values():
            screen.back_enabled = back
        now = time.time()
        offset = time.localtime(now).tm_gmtoff
        self.session.send_message(link.TIME, struct.pack("<Qi", int(now), offset))
        self.enter_mode(self.mode)
        if self.mode == "mirror":
            self.frame = self.last_mirror_frame   # the tablet starts from nothing
            self.ensure_gestures()
        self.start_audio()

    def start_audio(self):
        """The laptop's sound to the tablet, if it plays sound (INFO_AUDIO)."""
        flags = self.session.link.flags if self.session.link else 0
        if not self.audio_wanted or not flags & link.INFO_AUDIO or self.audio:
            return
        try:
            from . import audio            # GStreamer only when needed
            rate, channels = audio.format_for(self.session.route)
            self.audio = audio.AudioSender(
                lambda payload: self.session.send_message(link.AUDIO, payload), rate, channels,
                on_error=lambda _text: setattr(self, "audio", None))
            self.audio.start()
            print(f"audio: {rate} Hz, {channels} channel(s) to the tablet", flush=True)
        except Exception as exc:     # noqa: BLE001 - no sound must not stop the picture
            print(f"audio: not started: {exc}", flush=True)
            self.audio = None

    def stop_audio(self):
        if self.audio:
            self.audio.stop()
            self.audio = None

    def learn_wlan_address(self):
        """Over the cable: ask the tablet for its Wi-Fi address and remember it.

        A new address from the router is picked up this way; the next
        connection attempt then uses Wi-Fi again.
        """
        address = link.tablet_wlan_address()
        if address and address != self.session.host:
            if self.session.psk is None:
                try:
                    self.session.psk = secure.load_key()
                except (OSError, secure.SecureError) as exc:
                    print(f"pairing key: {exc}", flush=True)
                    return
            print(f"tablet's Wi-Fi address is now {address}", flush=True)
            self.session.host = address
            try:
                config.update(wifi_host=address)
            except (OSError, config.ConfigError) as exc:
                print(f"config: {exc}", flush=True)

    def on_disconnected(self, reason):
        print(f"tablet disconnected: {reason}", flush=True)
        self.stop_audio()
        self.encoder = None
        if self.gestures:
            self.gestures.reset()
        self.stop_mirror()   # no screen capture while nobody is looking

    # -- modes ----------------------------------------------------------------

    def enter_mode(self, mode):
        self.mode = mode
        self.frame = None
        self.next_render = 0.0
        if self.gestures:
            self.gestures.reset()
        self.key_repeat.stop()
        if mode == "mirror":
            self.start_portal()
        else:
            self.stop_mirror()   # no screen capture while it is not shown
        print(f"mode: {mode}", flush=True)

    def next_mode(self, step):
        self.enter_mode(MODES[(MODES.index(self.mode) + step) % len(MODES)])

    # -- mirror: portal and capture ---------------------------------------------

    def start_portal(self):
        if self.portal or self.capture:
            return
        self.portal = portal.Portal(self.on_portal_ready, self.on_portal_closed,
                                    self.on_portal_error)
        self.portal.start()

    def stop_mirror(self):
        if self.capture:
            self.capture.stop()
            self.capture = None
        self.key_repeat.stop()
        if self.portal:
            self.portal.release_all()     # nothing may stay pressed on the laptop
            self.portal.close()
            self.portal = None
        if self.pipewire_fd is not None:
            try:
                os.close(self.pipewire_fd)
            except OSError:
                pass
            self.pipewire_fd = None
        self.gestures = self.view = self.keyboard = None
        self.keyboard_fingers.clear()
        self.last_mirror_frame = None

    def on_portal_ready(self, fd, node, size):
        print(f"portal: node {node}, size {size}", flush=True)
        self.portal_size, self.pipewire_fd = size, fd
        fb_size = (self.session.width or 600, self.session.height or 1024)
        self.capture = capture.Capture(capture.pipewire_source(fd, node), fb_size,
                                       self.rotation, self.on_capture_frame,
                                       self.on_capture_error, eager=False)
        self.keyboard = keyboard.Keyboard(fb_size[1], fb_size[0])
        self.capture.overlay = self.keyboard.draw
        self.capture.start()

    def on_portal_closed(self):
        print("portal session closed; retrying", flush=True)
        self.stop_mirror()
        GLib.timeout_add_seconds(PORTAL_RETRY_S, self.retry_portal)

    def on_portal_error(self, text):
        print(f"portal: {text}", flush=True)
        self.stop_mirror()
        GLib.timeout_add_seconds(PORTAL_RETRY_S * 10, self.retry_portal)

    def on_capture_error(self, text):
        print(f"capture: {text}", flush=True)
        self.stop_mirror()
        GLib.timeout_add_seconds(PORTAL_RETRY_S, self.retry_portal)

    def retry_portal(self):
        if self.mode == "mirror":
            self.start_portal()
        return False

    def on_capture_frame(self, _frame):
        """A new picture exists; it is converted only when it can be sent."""
        if self.mode == "mirror":
            self.ensure_gestures()

    def ensure_gestures(self):
        """Touch needs the tablet's size, the screen's pixel size and the portal."""
        if (self.gestures is None and self.capture and self.capture.source_px
                and self.portal and self.session.connected):
            fb_size = (self.session.width, self.session.height)
            stream = self.portal_size or self.capture.source_px
            mapper = pointer.Mapper(fb_size, self.rotation, self.capture.source_px, stream)
            if self.view is None:
                self.view = pointer.View(self.capture.source_px, self.on_view_change)
            mapper.set_view(*self.view.rect())
            self.gestures = pointer.Gestures(mapper, self.portal, self.view)
            print(f"pointer: screen {self.capture.source_px} px, stream size {stream}", flush=True)

    def on_view_change(self, x, y, width, height):
        """Pinch or pan: show another part of the screen and aim touches at it."""
        if self.capture:
            self.capture.set_viewport(x, y, width, height)
        if self.gestures:
            self.gestures.mapper.set_view(x, y, width, height)

    # -- events from the tablet ---------------------------------------------------

    def on_touch(self, slot, state, x_f, y_f):
        now = time.monotonic()
        if self.mode == "mirror":
            if self.gestures and not self.on_keyboard_touch(slot, state, x_f, y_f, now):
                self.gestures.touch(slot, state, x_f, y_f, now)
        elif state == pointer.DOWN and slot == 0:
            if self.rotation == "counterclockwise":
                x_c, y_c = self.session.height - 1 - y_f, x_f
            else:
                x_c, y_c = y_f, self.session.width - 1 - x_f
            hit = self.screens[self.mode].tap(x_c, y_c)
            if hit == modes.BACK:
                # like holding both volume keys: the remote control ends and
                # the tablet shows its start screen
                self.session.send_message(link.HOME)
            elif hit is not None:
                self.next_render = 0.0   # show the pressed button at once

    def on_keyboard_touch(self, slot, state, x_f, y_f, now):
        """True if the touch belongs to the on-screen keyboard (not to the desktop)."""
        if slot in self.keyboard_fingers:
            if state == pointer.UP:
                self.keyboard_fingers.discard(slot)
                self.key_repeat.stop(slot)
            return True
        if state != pointer.DOWN or not self.keyboard or self.gestures.fingers:
            return False
        x_c, y_c = self.gestures.mapper.to_canvas(x_f, y_f)
        if not self.keyboard.covers(x_c, y_c):
            return False
        self.keyboard_fingers.add(slot)
        keysym = self.keyboard.touch(x_c, y_c, now)
        if keysym is not None and self.portal:
            self.portal.key(keysym)
            self.key_repeat.start(slot, keysym, now)
        self.capture.refresh()
        return True

    def on_key(self, code, state):
        if state == 1 and code in (KEY_VOLUMEUP, KEY_VOLUMEDOWN):
            self.next_mode(1 if code == KEY_VOLUMEUP else -1)

    # -- main loop --------------------------------------------------------------------

    def pump(self):
        for event in self.session.pump(0):
            kind = event[0]
            if kind == "connected":
                self.on_connected(event[1], event[2])
            elif kind == "disconnected":
                self.on_disconnected(event[1])
            elif kind == "ack":
                self.acked += 1
                if self.encoder:
                    self.encoder.confirm()
            elif kind == "touch":
                self.last_input = time.monotonic()
                self.on_touch(*event[1:])
            elif kind == "key":
                self.last_input = time.monotonic()
                self.on_key(*event[1:])
        now = time.monotonic()
        if self.gestures:
            self.gestures.tick(now)
        repeat = self.key_repeat.due(now)
        if repeat is not None and self.portal and self.keyboard:
            self.portal.key(repeat)
            self.keyboard.keep_pressed(now)
        if self.keyboard and self.keyboard.expire(now) and self.capture:
            self.capture.refresh()
        if self.encoder and self.mode != "mirror" and now >= self.next_render:
            screen = self.screens[self.mode]
            self.frame = modes.to_frame(screen.render(), self.rotation)
            self.next_render = now + (0.1 if screen.pressed is not None else MONITOR_PERIOD_S)
        if (self.mode == "mirror" and self.capture and self.capture.dirty
                and self.encoder and self.session.can_send):
            self.frame = self.last_mirror_frame = self.capture.render()
        if self.frame is not None and self.encoder and self.session.can_send:
            payloads = self.encoder.encode(self.frame)
            self.frame = None
            if payloads:
                try:
                    self.session.send_frame(payloads)
                except link.LinkError:
                    pass
        if now - self.stats_at >= 10:
            sent = self.session.bytes_sent - self.sent_bytes_mark
            moves = self.encoder.moves if self.encoder else 0
            print(f"{self.acked / (now - self.stats_at):5.1f} frames/s, "
                  f"{sent / 1e6 / (now - self.stats_at):5.2f} MB/s ({self.mode}), "
                  f"moves {moves}", flush=True)
            self.acked, self.sent_bytes_mark, self.stats_at = 0, self.session.bytes_sent, now
        return True

    def pump_interval_ms(self, now):
        """How long the main loop may sleep before the next round."""
        if self.encoder is None:
            return PUMP_ALONE_MS                       # no tablet connected
        busy = (self.session.in_flight is not None or self.frame is not None
                or (self.capture is not None and self.capture.dirty)
                or self.key_repeat.keysym is not None
                or (self.gestures is not None and self.gestures.fingers)
                or now - self.last_input < BUSY_AFTER_INPUT_S)
        return PUMP_BUSY_MS if busy else PUMP_QUIET_MS

    def run_pump(self):
        """One round, then the next one after an interval fitting the activity.
        An error in a round must not end the loop (before, hostd then kept
        running and did nothing): it is logged and the connection dropped, so
        the next round starts afresh."""
        try:
            self.pump()
        except Exception:     # noqa: BLE001 - whatever it is, the loop must go on
            traceback.print_exc()
            try:
                for event in self.session.drop("error in hostd, see the traceback"):
                    self.on_disconnected(event[1])
            except Exception:     # noqa: BLE001
                traceback.print_exc()
        GLib.timeout_add(self.pump_interval_ms(time.monotonic()), self.run_pump)
        return False


def main():
    try:
        settings = config.load()
    except config.ConfigError as exc:
        sys.exit(f"hostd: {exc}")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=MODES, default=settings["mode"])
    parser.add_argument("--rotation", choices=config.ROTATIONS, default=settings["rotation"],
                        help="how the picture is turned to fit the upright framebuffer")
    parser.add_argument("--port", type=int, default=link.DEFAULT_PORT)
    parser.add_argument("--wifi", metavar="HOST", default=settings["wifi_host"] or None,
                        help="connect directly and encrypted over Wi-Fi (needs pairing); "
                             "default from hostd.toml")
    parser.add_argument("--cable-only", action="store_true", help="ignore wifi_host")
    parser.add_argument("--no-adb", action="store_true",
                        help="tabletd runs on this machine (tests)")
    args = parser.parse_args()

    wifi = None if args.cable_only else args.wifi
    use_adb = not args.no_adb and (wifi is None or settings["cable_fallback"])
    internet_port = settings["internet_port"]
    if (wifi or internet_port) and not secure.key_path().exists():
        print("hostd: no pairing key yet (huawai pair); using the cable only", flush=True)
        wifi, internet_port = None, 0
    if internet_port:
        print(f"hostd: the tablet may call from outside on port {internet_port}", flush=True)
    hostd = Hostd(args.mode, args.rotation, args.port, use_adb=use_adb, wifi=wifi,
                  learn_address=not args.no_adb and not args.cable_only and
                  secure.key_path().exists(), internet_port=internet_port,
                  audio=settings["audio"])
    GLib.timeout_add(PUMP_BUSY_MS, hostd.run_pump)
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        hostd.stop_audio()
        hostd.stop_mirror()
        hostd.session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
