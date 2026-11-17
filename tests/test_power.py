"""Power key and idle dimming (steps P5.13, P5.14).

Short press: display off and on again. Held for three seconds: tabletd ends
with exit status 42, which tells the launcher to switch the tablet off.
Without laptop and touch the display goes dark after the idle time.
"""
import os
import pathlib
import struct
import subprocess
import tempfile
import time
import unittest

from harness import BACKLIGHT, H, TABLETD, W, DaemonCase, frame

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT, SYN_MT_REPORT = 0, 2
KEY_POWER = 116
REPORT = struct.pack("<IIHHi", 0, 0, EV_SYN, SYN_REPORT, 0)


def key(code, value):
    return struct.pack("<IIHHi", 0, 0, EV_KEY, code, value) + REPORT


def tap():
    return (struct.pack("<IIHHi", 0, 0, EV_ABS, 0x35, 10) +
            struct.pack("<IIHHi", 0, 0, EV_ABS, 0x36, 10) +
            struct.pack("<IIHHi", 0, 0, EV_SYN, SYN_MT_REPORT, 0) + REPORT +
            struct.pack("<IIHHi", 0, 0, EV_SYN, SYN_MT_REPORT, 0) + REPORT)


def feed(path, data):
    fd = os.open(path, os.O_WRONLY)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


class PowerCase(DaemonCase):
    idle = "0"   # no idle dimming unless a test wants it

    def prepare(self):
        self.backlight = self.dir / "brightness"
        self.backlight.write_text("200\n")
        self.keys, self.touch = self.dir / "keys", self.dir / "touch"
        os.mkfifo(self.keys)
        os.mkfifo(self.touch)

    def args(self):
        return ["--backlight", str(self.backlight), "--input", str(self.keys),
                "--input", str(self.touch), "--idle-dim", self.idle]

    def wait_backlight(self, value, timeout=4.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.backlight.read_text().strip() == str(value):
                return
            time.sleep(0.02)
        self.fail(f"backlight is {self.backlight.read_text()!r}, expected {value}")


class ShortPress(PowerCase):
    def test_display_off_and_on(self):
        self.sync(self.connect())
        feed(self.keys, key(KEY_POWER, 1) + key(KEY_POWER, 0))
        self.wait_backlight(0)
        feed(self.keys, key(KEY_POWER, 1) + key(KEY_POWER, 0))
        self.wait_backlight(200)

    def test_switching_on_uses_the_brightness_the_laptop_set(self):
        conn = self.connect()
        conn.send(frame(BACKLIGHT, bytes([77])))
        self.sync(conn)
        self.wait_backlight(77)
        feed(self.keys, key(KEY_POWER, 1) + key(KEY_POWER, 0))
        self.wait_backlight(0)
        feed(self.keys, key(KEY_POWER, 1) + key(KEY_POWER, 0))
        self.wait_backlight(77)

    def test_touch_wakes_the_display(self):
        self.sync(self.connect())
        feed(self.keys, key(KEY_POWER, 1) + key(KEY_POWER, 0))
        self.wait_backlight(0)
        feed(self.touch, tap())
        self.wait_backlight(200)


class IdleDim(PowerCase):
    idle = "1"

    def test_dark_after_idle_time_and_touch_wakes(self):
        self.wait_backlight(0, timeout=4)
        feed(self.touch, tap())
        self.wait_backlight(200)
        self.wait_backlight(0, timeout=4)

    def test_no_dimming_while_a_laptop_is_connected(self):
        conn = self.connect()
        for i in range(5):
            time.sleep(0.5)
            self.sync(conn, i)
        self.assertEqual(self.backlight.read_text().strip(), "200")


class LongPress(unittest.TestCase):
    """Not a DaemonCase: here tabletd is supposed to end."""

    def run_daemon(self, events_then_wait):
        with tempfile.TemporaryDirectory() as tmp:
            keys = pathlib.Path(tmp) / "keys"
            os.mkfifo(keys)
            proc = subprocess.Popen(
                [str(TABLETD), "--fb", tmp + "/fb.raw", "--geom", f"{W}x{H}",
                 "--listen", f"abstract:tabletd-test-power-{os.getpid()}-{id(self)}",
                 "--input", str(keys), "--idle-dim", "0"], stderr=subprocess.PIPE)
            try:
                time.sleep(0.3)
                start = time.monotonic()
                events_then_wait(keys)
                code = proc.wait(timeout=6)
                return code, time.monotonic() - start, proc.stderr.read().decode()
            except subprocess.TimeoutExpired:
                return None, None, ""
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                proc.stderr.close()

    def test_holding_for_three_seconds_ends_with_42(self):
        code, seconds, log = self.run_daemon(lambda keys: feed(keys, key(KEY_POWER, 1)))
        self.assertEqual(code, 42, log)
        self.assertGreater(seconds, 2.8)
        self.assertLess(seconds, 4.0)
        self.assertNotIn("Sanitizer", log)

    def test_released_after_one_second_keeps_running(self):
        def events(keys):
            feed(keys, key(KEY_POWER, 1))
            time.sleep(1)
            feed(keys, key(KEY_POWER, 0))

        code, _, _ = self.run_daemon(events)
        self.assertIsNone(code)


if __name__ == "__main__":
    unittest.main()
