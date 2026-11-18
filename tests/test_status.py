"""Status page (step P5.12, new design): compared pixel by pixel with a model.

The model below redraws the page in Python: same glyph data (read from
tablet/font_aa.h), same integer arithmetic, same layout. It shares no code
with tabletd. The date is computed with Python's datetime, which also checks
tabletd's own calendar arithmetic.
"""
import datetime
import pathlib
import os
import re
import socket
import struct
import threading
import time
import unittest

import numpy as np

from harness import H, TIME, W, DaemonCase, frame, rect

ROOT = pathlib.Path(__file__).resolve().parent.parent

BG_TOP, BG_BOTTOM = (16, 24, 44), (5, 7, 13)
TEXT_MAIN, TEXT_SUB, TEXT_DIM = (240, 244, 250), (201, 209, 220), (154, 164, 178)
LINE, AMBER, GREEN, RED = (30, 41, 59), (251, 191, 36), (74, 222, 128), (248, 113, 113)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


def load_faces():
    text = (ROOT / "tablet" / "font_aa.h").read_text()
    faces = {}
    for name in ("clock", "text"):
        block = re.search(r"glyphs_%s\[\d+\] = \{(.*?)\n\};" % name, text, re.S).group(1)
        faces[name] = {int(cp, 16): tuple(map(int, rest))
                       for cp, *rest in re.findall(
                           r"\{ 0x([0-9a-f]+), (-?\d+), (-?\d+), (\d+), (\d+), (\d+), (\d+) \}",
                           block)}
    data = re.search(r"glyph_pixels\[\d+\] = \{(.*?)\};", text, re.S).group(1)
    pixels = bytes(int(v, 16) for v in re.findall(r"0x([0-9a-f]{2})", data))
    return faces, pixels


FACES, PIXELS = load_faces()


def pack(c):
    return (c[0] >> 3) << 11 | (c[1] >> 2) << 5 | c[2] >> 3


def cdiv(a, b):
    """Integer division as C does it (towards zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


class Page:
    def __init__(self, rotation):
        self.rotation = rotation
        self.cw, self.ch = (W, H) if rotation == "none" else (H, W)
        self.c = np.zeros((self.ch, self.cw), dtype=np.int64)

    def gradient(self):
        bayer = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]])
        d = bayer[np.arange(self.ch)[:, None] & 3, np.arange(self.cw)[None, :] & 3]
        rows = np.array([[t + cdiv((b - t) * y, self.ch - 1) for t, b in zip(BG_TOP, BG_BOTTOM)]
                         for y in range(self.ch)])
        r = np.minimum(rows[:, 0:1] + d // 2, 255)
        g = np.minimum(rows[:, 1:2] + d // 4, 255)
        b = np.minimum(rows[:, 2:3] + d // 2, 255)
        self.c[:, :] = (r >> 3) << 11 | (g >> 2) << 5 | b >> 3

    def rect(self, x, y, w, h, colour):
        self.c[max(0, y):max(0, y + h), max(0, x):max(0, x + w)] = pack(colour)

    def blend(self, x, y, colour, a):
        if a == 0 or not (0 <= x < self.cw and 0 <= y < self.ch):
            return
        v = int(self.c[y, x])
        r, g, b = v >> 11, (v >> 5) & 0x3F, v & 0x1F
        bg = (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)
        self.c[y, x] = pack(tuple((f * a + k * (15 - a)) // 15 for f, k in zip(colour, bg)))

    @staticmethod
    def glyph(face, char):
        """As in font.c: unknown characters become "?", or nothing without one."""
        table = FACES[face]
        return table.get(ord(char)) or table.get(ord("?"))

    def width(self, face, text):
        return sum(g[4] for g in (self.glyph(face, ch) for ch in text) if g)

    def text(self, face, x, y, colour, text):
        for ch in text:
            g = self.glyph(face, ch)
            if not g:
                continue
            gx, gy, w, h, adv, off = g
            stride = (w + 1) // 2
            for j in range(h):
                for i in range(w):
                    byte = PIXELS[off + j * stride + i // 2]
                    self.blend(x + gx + i, y + gy + j, colour, byte & 0x0F if i % 2 else byte >> 4)
            x += adv
        return x

    def framebuffer_bytes(self):
        k = {"none": 0, "counterclockwise": 1, "clockwise": -1}[self.rotation]
        fb = np.rot90(self.c, k)
        r5, g6, b5 = fb >> 11, (fb >> 5) & 0x3F, fb & 0x1F
        rgba = np.stack([r5 << 3 | r5 >> 2, g6 << 2 | g6 >> 4, b5 << 3 | b5 >> 2,
                         np.full_like(fb, 255)], axis=-1)
        return rgba.astype(np.uint8).tobytes()

    @classmethod
    def expected(cls, rotation, address, cap, charging, when=None, call=None):
        """when: datetime as the tablet should show it, or None (no time yet).
        call: the line about the call to the laptop at home, or None."""
        p = cls(rotation)
        m = 28
        p.gradient()
        p.text("text", m, 50, TEXT_DIM, f"Wi-Fi  {address}" if address else "No Wi-Fi")
        if cap is not None:
            level = GREEN if cap > 50 else AMBER if cap >= 20 else RED
            label = f"{cap} %"
            tx = p.cw - m - p.width("text", label)
            p.text("text", tx, 50, TEXT_SUB, label)
            ix = tx - 12 - 44
            p.rect(ix, 31, 40, 2, TEXT_DIM)
            p.rect(ix, 49, 40, 2, TEXT_DIM)
            p.rect(ix, 31, 2, 20, TEXT_DIM)
            p.rect(ix + 38, 31, 2, 20, TEXT_DIM)
            p.rect(ix + 40, 37, 4, 8, TEXT_DIM)
            p.rect(ix + 4, 35, 32 * cap // 100, 12, level)
            if charging:
                p.text("text", ix - 8 - p.width("text", "⚡"), 50, AMBER, "⚡")
        else:
            p.text("text", p.cw - m - p.width("text", "Battery ?"), 50, TEXT_DIM, "Battery ?")
        p.rect(m, 72, p.cw - 2 * m, 1, LINE)
        clock = when.strftime("%H:%M") if when else "--:--"
        date = (f"{WEEKDAYS[when.weekday()]}, {when.day} {MONTHS[when.month - 1]}" if when
                else "Time comes from the laptop")
        p.text("clock", (p.cw - p.width("clock", clock)) // 2, p.ch // 2 + 40, TEXT_MAIN, clock)
        p.text("text", (p.cw - p.width("text", date)) // 2, p.ch // 2 + 100, TEXT_SUB, date)
        if call:
            p.text("text", (p.cw - p.width("text", call)) // 2, p.ch - 88, TEXT_DIM, call)
        state = " Ready – waiting for the laptop"
        x = (p.cw - p.width("text", "•") - p.width("text", state)) // 2
        x = p.text("text", x, p.ch - 48, AMBER, "•")
        p.text("text", x, p.ch - 48, TEXT_DIM, state)
        return p.framebuffer_bytes()


class StatusCase(DaemonCase):
    rotation = "none"
    with_battery = True

    def prepare(self):
        self.battery = self.dir / "battery"
        self.battery.mkdir()
        (self.battery / "capacity").write_text("57\n")
        (self.battery / "status").write_text("Charging\n")
        (self.battery / "temp").write_text("250\n")

    def args(self):
        args = ["--status-page", "--rotation", self.rotation, "--iface", "lo", "--idle-dim", "0"]
        if self.with_battery:
            args += ["--battery", str(self.battery)]
        return args

    def wait_for_page(self, expected, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.fb_path.exists() and self.framebuffer() == expected:
                return
            time.sleep(0.05)
        got = np.frombuffer(self.framebuffer(), dtype=np.uint8).reshape(-1, 4)
        want = np.frombuffer(expected, dtype=np.uint8).reshape(-1, 4)
        differing = int(np.any(got != want, axis=1).sum())
        self.fail(f"status page differs from the model in {differing} pixels")


class Upright(StatusCase):
    def test_page_without_time(self):
        self.wait_for_page(Page.expected("none", "127.0.0.1", 57, True))

    def test_battery_change_is_shown(self):
        self.wait_for_page(Page.expected("none", "127.0.0.1", 57, True))
        (self.battery / "capacity").write_text("9\n")
        (self.battery / "status").write_text("Discharging\n")
        self.wait_for_page(Page.expected("none", "127.0.0.1", 9, False), timeout=12)

    def test_time_and_date_from_the_laptop(self):
        conn = self.connect()
        # 2026-11-16 11:34:20 UTC, one hour offset: Monday, 16 November, 12:34
        utc = datetime.datetime(2026, 11, 16, 11, 34, 20, tzinfo=datetime.timezone.utc)
        conn.send(frame(TIME, struct.pack("<Qi", int(utc.timestamp()), 3600)))
        conn.send(rect(0, 0, W, H, 0, b"\xff\xff" * (W * H)))    # the laptop's picture
        self.sync(conn)
        self.assertEqual(self.framebuffer(), b"\xff" * (W * H * 4))
        conn.close()
        self.conns.remove(conn)
        local = utc.astimezone(datetime.timezone(datetime.timedelta(hours=1)))
        self.wait_for_page(Page.expected("none", "127.0.0.1", 57, True, local))

    def test_dates_around_new_year_and_leap_day(self):
        for when in (datetime.datetime(2027, 12, 31, 23, 59, 30), datetime.datetime(2028, 2, 29, 8, 0),
                     datetime.datetime(2028, 3, 1, 0, 0, 5)):
            with self.subTest(when=when):
                conn = self.connect()
                utc = when.replace(tzinfo=datetime.timezone.utc)
                conn.send(frame(TIME, struct.pack("<Qi", int(utc.timestamp()), 0)))
                self.sync(conn)
                conn.close()
                self.conns.remove(conn)
                self.wait_for_page(Page.expected("none", "127.0.0.1", 57, True, when))

    def test_page_is_not_drawn_over_a_connected_laptop(self):
        conn = self.connect()
        conn.send(rect(0, 0, W, H, 0, b"\x1f\x00" * (W * H)))
        self.sync(conn)
        (self.battery / "capacity").write_text("10\n")          # would trigger a redraw
        blue = self.framebuffer()
        for i in range(6):
            time.sleep(1)
            self.sync(conn, 10 + i)
        self.assertEqual(self.framebuffer(), blue)


class Counterclockwise(StatusCase):
    rotation = "counterclockwise"

    def test_page(self):
        self.wait_for_page(Page.expected("counterclockwise", "127.0.0.1", 57, True))


class Clockwise(StatusCase):
    rotation = "clockwise"

    def test_page(self):
        self.wait_for_page(Page.expected("clockwise", "127.0.0.1", 57, True))


class NoBattery(StatusCase):
    with_battery = False

    def test_unknown_battery(self):
        self.wait_for_page(Page.expected("none", "127.0.0.1", None, False))


class NoWifi(StatusCase):
    def args(self):
        return [a if a != "lo" else "wlan-does-not-exist" for a in super().args()]

    def test_no_address(self):
        (self.battery / "capacity").write_text("35\n")
        self.wait_for_page(Page.expected("none", "", 35, True))


class CallState(StatusCase):
    """Away from home the waiting screen is the only place to see why the
    laptop does not come: the reason of the last call is shown above
    "Ready". The laptop side here: a closed port, one that hangs up at once,
    one that never answers the handshake."""
    answer = None      # None: nothing listens; "close": hang up; "silent": say nothing

    def prepare(self):
        super().prepare()
        key = self.dir / "key"
        key.write_bytes(os.urandom(32))
        key.chmod(0o600)
        self.server = socket.socket(socket.AF_INET6)
        self.server.bind(("::1", 0))
        self.target_port = self.server.getsockname()[1]
        self.held = []
        if self.answer is None:
            self.server.close()              # nobody there: refused
            return
        self.server.listen(4)

        def serve():
            while True:
                try:
                    conn, _ = self.server.accept()
                except OSError:
                    return
                if self.answer == "close":
                    conn.close()
                else:
                    self.held.append(conn)    # open, but no handshake ever comes
        threading.Thread(target=serve, daemon=True).start()

    def tearDown(self):
        self.server.close()
        for conn in self.held:
            conn.close()
        super().tearDown()

    def args(self):
        return super().args() + ["--secure-connect", f"[::1]:{self.target_port}",
                                 "--key", str(self.dir / "key")]


class CallRefused(CallState):
    def test_refused_is_shown(self):
        self.wait_for_page(Page.expected("none", "127.0.0.1", 57, True,
                                         call="Home: refused - hostd running?"))


class CallHungUp(CallState):
    answer = "close"

    def test_hang_up_is_shown(self):
        self.wait_for_page(Page.expected("none", "127.0.0.1", 57, True,
                                         call="Home: the laptop hung up"))


class CallSilent(CallState):
    answer = "silent"
    rotation = "counterclockwise"         # the long line in landscape, too

    def test_silent_laptop_is_shown(self):
        self.wait_for_page(Page.expected("counterclockwise", "127.0.0.1", 57, True,
                                         call="Home: handshake too slow"), timeout=14)


class RotationFile(StatusCase):
    def args(self):
        (self.dir / "rotation").write_text("clockwise\n")
        return ["--status-page", "--rotation-file", str(self.dir / "rotation"),
                "--battery", str(self.battery), "--iface", "lo", "--idle-dim", "0"]

    def test_rotation_is_read_from_the_file(self):
        self.wait_for_page(Page.expected("clockwise", "127.0.0.1", 57, True))


if __name__ == "__main__":
    unittest.main()
