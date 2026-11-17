"""hostd end to end without the portal: macro and monitor screens, driven by
touch and key events that go through the real tabletd (x86 build)."""
import os
import pathlib
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

from harness import H, TABLETD, W

ROOT = pathlib.Path(__file__).resolve().parent.parent
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT, SYN_MT_REPORT = 0, 2
ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x35, 0x36, 0x39
KEY_VOLUMEDOWN = 114


def ev(ev_type, code, value):
    return struct.pack("<IIHHi", 0, 0, ev_type, code, value)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class HostdCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(self.tmp.name)
        self.fb, self.marker = tmp / "fb.raw", tmp / "macro-ran"
        self.touch, self.keys = tmp / "touch", tmp / "keys"
        os.mkfifo(self.touch)
        os.mkfifo(self.keys)
        config = tmp / "config" / "huawai-companion"
        config.mkdir(parents=True)
        (config / "macros.toml").write_text(
            f'[[macro]]\nlabel = "Mark"\ncommand = ["touch", "{self.marker}"]\n'
            '[[macro]]\nlabel = "Nothing"\ncommand = ["true"]\n')
        port = free_port()
        self.daemon = subprocess.Popen(
            [str(TABLETD), "--fb", str(self.fb), "--geom", f"{W}x{H}", "--listen", f"tcp:{port}",
             "--input", str(self.touch), "--input", str(self.keys)], stderr=subprocess.DEVNULL)
        env = dict(os.environ, XDG_CONFIG_HOME=str(tmp / "config"))
        self.log = open(tmp / "hostd.log", "w+")
        self.hostd = subprocess.Popen(
            [sys.executable, "-m", "host.main", "--mode", "macros", "--no-adb",
             "--port", str(port)], cwd=ROOT, env=env, stdout=self.log, stderr=subprocess.STDOUT)

    def tearDown(self):
        for proc in (self.hostd, self.daemon):
            proc.terminate()
            proc.wait(timeout=5)
        self.log.close()
        self.tmp.cleanup()

    def wait_until(self, condition, what, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.05)
        self.log.seek(0)
        self.fail(f"{what} did not happen within {timeout} s; hostd log:\n{self.log.read()}")

    def feed(self, path, data):
        fd = os.open(path, os.O_WRONLY)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    def screen(self):
        return self.fb.read_bytes() if self.fb.exists() else b""

    def test_macro_by_touch_and_mode_switch_by_key(self):
        # 1. the macro screen is drawn
        self.wait_until(lambda: any(self.screen()[::4097]), "first picture")
        macro_picture = self.screen()

        # 2. a tap on the first button runs the first macro on the laptop.
        #    First button of the macro screen: canvas x 14..253, y 74..593.
        x_c, y_c = 130, 300
        x_f, y_f = y_c, 1023 - x_c            # counterclockwise
        self.feed(self.touch,
                  ev(EV_ABS, ABS_MT_TRACKING_ID, 0) + ev(EV_ABS, ABS_MT_POSITION_X, x_f) +
                  ev(EV_ABS, ABS_MT_POSITION_Y, y_f) + ev(EV_SYN, SYN_MT_REPORT, 0) +
                  ev(EV_SYN, SYN_REPORT, 0) +
                  ev(EV_SYN, SYN_MT_REPORT, 0) + ev(EV_SYN, SYN_REPORT, 0))
        self.wait_until(self.marker.exists, "macro started by touch")

        # 3. the volume-down key switches from macros to monitor. The test must
        #    never pass through mirror mode: that would ask the real desktop
        #    portal for a screen share and could put a dialog on the screen.
        time.sleep(0.8)                      # let the pressed-button flash end
        macro_picture = self.screen()
        self.feed(self.keys, ev(EV_KEY, KEY_VOLUMEDOWN, 1) + ev(EV_SYN, SYN_REPORT, 0) +
                  ev(EV_KEY, KEY_VOLUMEDOWN, 0) + ev(EV_SYN, SYN_REPORT, 0))

        def log_text():
            self.log.flush()
            self.log.seek(0)
            return self.log.read()

        self.wait_until(lambda: "mode: monitor" in log_text(), "mode switch")
        self.wait_until(lambda: self.screen() != macro_picture, "monitor picture")
        self.log.seek(0)
        log = self.log.read()
        self.assertIn("mode: monitor", log)
        self.assertNotIn("mode: mirror", log)
        self.assertNotIn("portal", log)
        self.assertIsNone(self.hostd.poll(), log)
        self.assertNotIn("Traceback", log)


if __name__ == "__main__":
    unittest.main()
