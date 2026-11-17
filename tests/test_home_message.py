"""MSG_HOME, the "Back" button of hostd's menus: tabletd ends cleanly (status
0), and tb-home, which started it, shows its start screen, as after holding
both volume keys."""
import os
import struct
import subprocess
import tempfile
import pathlib
import time
import unittest

from harness import H, INFO, TABLETD, W, Conn, frame

HOME = 0x09
INFO_HOME = 0x10


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.tmp.name)
        self.name = f"tabletd-home-{os.getpid()}"
        self.err = open(d / "stderr.txt", "wb")
        self.proc = subprocess.Popen([str(TABLETD), "--fb", str(d / "fb.raw"), "--geom", f"{W}x{H}",
                                      "--listen", f"abstract:{self.name}"], stderr=self.err)

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        self.err.close()
        log = (pathlib.Path(self.tmp.name) / "stderr.txt").read_text(errors="replace")
        self.tmp.cleanup()
        self.assertNotIn("Sanitizer", log, log)

    def connect(self):
        deadline = time.monotonic() + 3
        while True:
            try:
                conn = Conn(self.name)
                break
            except (ConnectionRefusedError, FileNotFoundError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.02)
        msg_type, payload = conn.recv()
        self.assertEqual(msg_type, INFO)
        return conn, payload

    def test_home_is_announced_and_ends_tabletd_cleanly(self):
        conn, info = self.connect()
        self.assertTrue(info[6] & INFO_HOME)
        conn.send(frame(HOME))
        self.assertEqual(self.proc.wait(timeout=5), 0)

    def test_home_with_a_payload_is_a_protocol_error(self):
        conn, _ = self.connect()
        conn.send(frame(HOME, b"x"))
        conn.sock.settimeout(3)
        with self.assertRaises((EOFError, ConnectionResetError)):
            while True:
                conn.recv()
        self.assertIsNone(self.proc.poll(), "a broken HOME must not end tabletd")


if __name__ == "__main__":
    unittest.main()
