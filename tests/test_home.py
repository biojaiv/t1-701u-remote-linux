"""tb-home: start screen, starting and ending the remote-control program,
power key. The program is replaced by a small Python script that records
what happens to it."""
import os
import pathlib
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOME = ROOT / "build" / "x86" / "tb-home"
W, H = 600, 1024
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
KEY_VOLUMEDOWN, KEY_VOLUMEUP, KEY_POWER = 114, 115, 116
CARD = (24, 33, 56)

FAKE_APP = '''#!/usr/bin/env python3
import os, signal, sys, time
log = os.environ["FAKE_LOG"]
def note(text):
    with open(log, "a") as f:
        f.write(text + "\\n")
note("start " + " ".join(sys.argv[1:]))
def on_term(*_):
    note("term")
    sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
with open(os.environ["FAKE_FB"], "r+b") as fb:     # take over the screen
    fb.write(b"\\xaa" * (600 * 1024 * 4))
if "--exit-soon" in sys.argv:
    time.sleep(0.5)
    note("exit")
    sys.exit(3)
if os.environ.get("FAKE_EXIT_TO_REMOTE") and sys.argv[0].endswith("linux.py"):
    time.sleep(0.5)
    note("linux-exit-50")
    sys.exit(50)
while True:
    time.sleep(0.1)
'''


def ev(ev_type, code, value):
    return struct.pack("<IIHHi", 0, 0, ev_type, code, value)


REPORT = ev(EV_SYN, 0, 0)


def tap(x, y):
    down = (ev(EV_ABS, 0x39, 0) + ev(EV_ABS, 0x35, x) + ev(EV_ABS, 0x36, y) +
            ev(EV_SYN, 2, 0) + REPORT)
    return down + ev(EV_SYN, 2, 0) + REPORT


def key(code, value):
    return ev(EV_KEY, code, value) + REPORT


def expand(c):
    r, g, b = c[0] >> 3, c[1] >> 2, c[2] >> 3
    return bytes([r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2, 255])


TILE_CENTRE = (W // 2, H // 2 + 20 + 90)        # rotation none: canvas = framebuffer
TILE_PLAIN = (190, H // 2 + 20 + 100)           # inside the tile, away from icon and text


class HomeCase(unittest.TestCase):
    extra = ()
    app_args = ()
    wait_for_home = True
    with_linux = False
    plain = TILE_PLAIN

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.tmp.name)
        self.fb, self.log = d / "fb.raw", d / "app.log"
        self.touch, self.keys = d / "touch", d / "keys"
        os.mkfifo(self.touch)
        os.mkfifo(self.keys)
        self.battery = d / "battery"
        self.battery.mkdir()
        (self.battery / "capacity").write_text("57\n")
        (self.battery / "status").write_text("Charging\n")
        self.backlight = d / "brightness"
        self.backlight.write_text("200\n")
        app = d / "app.py"
        app.write_text(FAKE_APP)
        app.chmod(0o755)
        linux = []
        if self.with_linux:
            linux_app = d / "linux.py"
            linux_app.write_text(FAKE_APP.replace('note("start "', 'note("linux-start "'))
            linux_app.chmod(0o755)
            linux = ["--linux", str(linux_app)]
        env = dict(os.environ, FAKE_LOG=str(self.log), FAKE_FB=str(self.fb))
        self.err = open(d / "stderr.txt", "wb")
        self.proc = subprocess.Popen(
            [str(HOME), "--fb", str(self.fb), "--geom", f"{W}x{H}", "--touch", str(self.touch),
             "--keys", str(self.keys), "--battery", str(self.battery), "--backlight",
             str(self.backlight), "--iface", "lo", *linux, *self.extra, "--", str(app),
             "--arg-one", *self.app_args], env=env, stderr=self.err)
        if self.wait_for_home:
            self.wait_until(self.home_shown, "start screen")

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.err.close()
        log = (pathlib.Path(self.tmp.name) / "stderr.txt").read_text(errors="replace")
        for script in ("app.py", "linux.py"):
            subprocess.run(["pkill", "-f", str(pathlib.Path(self.tmp.name) / script)])
        self.tmp.cleanup()
        self.assertNotIn("Sanitizer", log, log)
        self.assertNotIn("runtime error", log, log)

    def wait_until(self, condition, what, timeout=6.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.05)
        self.fail(f"{what} did not happen within {timeout} s")

    def feed(self, path, data):
        fd = os.open(path, os.O_WRONLY)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    def pixel(self, x, y):
        if not self.fb.exists():
            return None
        with open(self.fb, "rb") as f:
            f.seek((y * W + x) * 4)
            return f.read(4)

    def log_lines(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def taken_over(self):
        return self.pixel(*self.plain) == b"\xaa" * 4

    def home_shown(self):
        return self.pixel(*self.plain) == expand(CARD)

    def start_by_tap(self):
        self.feed(self.touch, tap(*TILE_CENTRE))
        self.wait_until(lambda: any(l.startswith("start") for l in self.log_lines()), "start")
        self.wait_until(self.taken_over, "program draws")


class Programs(HomeCase):
    def test_tap_starts_the_program_with_its_arguments(self):
        self.start_by_tap()
        self.assertEqual(self.log_lines()[0], "start --arg-one")

    def test_home_does_not_draw_over_a_running_program(self):
        self.start_by_tap()
        (self.battery / "capacity").write_text("12\n")   # would redraw the start screen
        time.sleep(6)
        self.assertTrue(self.taken_over())

    def test_both_volume_keys_end_the_program(self):
        self.start_by_tap()
        self.feed(self.keys, key(KEY_VOLUMEUP, 1) + key(KEY_VOLUMEDOWN, 1))
        self.wait_until(lambda: "term" in self.log_lines(), "program ended")
        self.feed(self.keys, key(KEY_VOLUMEUP, 0) + key(KEY_VOLUMEDOWN, 0))
        self.wait_until(self.home_shown, "start screen back")

    def test_one_volume_key_does_nothing(self):
        self.start_by_tap()
        self.feed(self.keys, key(KEY_VOLUMEUP, 1))
        time.sleep(1.5)
        self.feed(self.keys, key(KEY_VOLUMEUP, 0))
        self.assertNotIn("term", self.log_lines())

    def test_touches_while_running_start_nothing_more(self):
        self.start_by_tap()
        self.feed(self.touch, tap(*TILE_CENTRE))
        time.sleep(1)
        self.assertEqual(sum(l.startswith("start") for l in self.log_lines()), 1)

    def test_start_again_after_return(self):
        self.start_by_tap()
        self.feed(self.keys, key(KEY_VOLUMEUP, 1) + key(KEY_VOLUMEDOWN, 1))
        self.wait_until(self.home_shown, "start screen back")
        self.feed(self.keys, key(KEY_VOLUMEUP, 0) + key(KEY_VOLUMEDOWN, 0))
        self.feed(self.touch, tap(*TILE_CENTRE))
        self.wait_until(lambda: sum(l.startswith("start") for l in self.log_lines()) == 2,
                        "second start")

    def test_tap_beside_the_tile_starts_nothing(self):
        self.feed(self.touch, tap(20, 200))
        time.sleep(1)
        self.assertEqual(self.log_lines(), [])


class LinuxTile(HomeCase):
    """--linux adds a second tile; the two sit side by side."""
    with_linux = True
    plain = (60, H // 2 + 20 + 100)               # left tile, left of its icon
    LEFT = (40 + 120, H // 2 + 20 + 90)
    RIGHT = (40 + 240 + 40 + 120, H // 2 + 20 + 90)

    def test_right_tile_starts_linux(self):
        self.feed(self.touch, tap(*self.RIGHT))
        self.wait_until(lambda: "linux-start" in [l.strip() for l in self.log_lines()],
                        "Linux program start")
        self.wait_until(self.taken_over, "program draws")
        self.assertFalse(any(l.startswith("start") for l in self.log_lines()))

    def test_left_tile_still_starts_the_remote_control(self):
        self.feed(self.touch, tap(*self.LEFT))
        self.wait_until(lambda: any(l.startswith("start") for l in self.log_lines()), "start")
        self.assertEqual(self.log_lines()[0], "start --arg-one")

    def test_volume_keys_end_linux_too(self):
        self.feed(self.touch, tap(*self.RIGHT))
        self.wait_until(self.taken_over, "program draws")
        self.feed(self.keys, key(KEY_VOLUMEUP, 1) + key(KEY_VOLUMEDOWN, 1))
        self.wait_until(lambda: "term" in self.log_lines(), "program ended")
        self.feed(self.keys, key(KEY_VOLUMEUP, 0) + key(KEY_VOLUMEDOWN, 0))
        self.wait_until(self.home_shown, "start screen back")

    def test_gap_between_the_tiles_starts_nothing(self):
        self.feed(self.touch, tap(300, H // 2 + 20 + 90))
        time.sleep(1)
        self.assertEqual(self.log_lines(), [])


class LinuxAsksForTheRemoteControl(HomeCase):
    """The Linux session ends with status 50: tb-home starts the remote control."""
    with_linux = True
    plain = LinuxTile.plain

    def setUp(self):
        os.environ["FAKE_EXIT_TO_REMOTE"] = "1"
        try:
            super().setUp()
        finally:
            del os.environ["FAKE_EXIT_TO_REMOTE"]

    def test_remote_control_starts_after_linux_ends_with_50(self):
        self.feed(self.touch, tap(*LinuxTile.RIGHT))
        self.wait_until(lambda: "linux-exit-50" in self.log_lines(), "Linux program ends")
        self.wait_until(lambda: "start --arg-one" in self.log_lines(), "remote control start")
        self.wait_until(self.taken_over, "remote control draws")


class AndroidTile(HomeCase):
    """--android-tile: a third tile that ends tb-home with 43 (restart into Android)."""
    with_linux = True
    extra = ("--android-tile",)
    # three tiles in portrait: (600 - 48 - 48) / 3 = 168 wide, gap 24, starting at 24
    plain = (30, H // 2 + 20 + 100)
    THIRD = (24 + 2 * (168 + 24) + 84, H // 2 + 20 + 90)

    def test_android_tile_ends_with_43(self):
        self.feed(self.touch, tap(*self.THIRD))
        self.assertEqual(self.proc.wait(timeout=5), 43)
        self.assertEqual(self.log_lines(), [])


class ProgramEndsByItself(HomeCase):
    app_args = ("--exit-soon",)

    def test_start_screen_comes_back(self):
        self.start_by_tap()
        self.wait_until(lambda: "exit" in self.log_lines(), "program ended")
        self.wait_until(self.home_shown, "start screen back")


class StartRemote(HomeCase):
    extra = ("--start-remote",)
    wait_for_home = False   # the program takes the screen at once

    def test_program_runs_without_a_tap(self):
        self.wait_until(lambda: any(l.startswith("start") for l in self.log_lines()), "start")


class Termination(HomeCase):
    def test_sigterm_ends_the_program_too(self):
        self.start_by_tap()
        self.proc.send_signal(signal.SIGTERM)
        self.assertEqual(self.proc.wait(timeout=5), 0)
        self.assertIn("term", self.log_lines())


class PowerKey(HomeCase):
    def test_short_press_switches_the_display(self):
        self.feed(self.keys, key(KEY_POWER, 1) + key(KEY_POWER, 0))
        self.wait_until(lambda: self.backlight.read_text().strip() == "0", "display off")
        self.feed(self.touch, tap(*TILE_CENTRE))           # wakes, does not start
        self.wait_until(lambda: self.backlight.read_text().strip() == "200", "display on")
        time.sleep(0.5)
        self.assertEqual(self.log_lines(), [])

    def test_long_press_ends_home_and_program_with_42(self):
        self.start_by_tap()
        self.feed(self.keys, key(KEY_POWER, 1))
        self.assertEqual(self.proc.wait(timeout=6), 42)
        self.assertIn("term", self.log_lines())


if __name__ == "__main__":
    unittest.main()
