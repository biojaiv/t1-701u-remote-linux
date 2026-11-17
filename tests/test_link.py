"""host/link.py Session: flow control, keep-alive and reconnection (step P2.4).

tabletd runs on a local TCP port here; on the tablet adb forwards that port.
"""
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import encode, link  # noqa: E402

from harness import H, TABLETD, W  # noqa: E402


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class SessionCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.port = free_port()
        self.proc = None
        self.start_daemon()
        self.session = link.Session(port=self.port, use_adb=False)

    def start_daemon(self):
        self.proc = subprocess.Popen(
            [str(TABLETD), "--fb", self.tmp.name + "/fb.raw", "--geom", f"{W}x{H}",
             "--listen", f"tcp:{self.port}"], stderr=subprocess.DEVNULL)

    def stop_daemon(self):
        self.proc.kill()
        self.proc.wait()

    def tearDown(self):
        self.session.close()
        self.stop_daemon()
        self.tmp.cleanup()

    def wait_for(self, kind, timeout=6.0):
        """Pumps until an event of this kind arrives; returns it."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.session.pump(0.05):
                if event[0] == kind:
                    return event
        self.fail(f"no '{kind}' event within {timeout} s")

    def test_connect_and_frame(self):
        self.assertEqual(self.wait_for("connected"), ("connected", W, H))
        enc = encode.Encoder(W, H)
        seq = self.session.send_frame(enc.encode(np.zeros((H, W), dtype=np.uint16)))
        self.assertFalse(self.session.can_send)
        with self.assertRaises(link.LinkError):
            self.session.send_frame([])
        self.assertEqual(self.wait_for("ack"), ("ack", seq))
        self.assertTrue(self.session.can_send)

    def test_two_frames_in_flight(self):
        self.session.max_in_flight = 2
        self.wait_for("connected")
        first = self.session.send_frame([])
        self.assertTrue(self.session.can_send)
        second = self.session.send_frame([])
        self.assertFalse(self.session.can_send)
        self.assertEqual(self.session.in_flight, first)
        acks = []
        deadline = time.monotonic() + 5
        while len(acks) < 2 and time.monotonic() < deadline:
            acks += [e for e in self.session.pump(0.05) if e[0] == "ack"]
        self.assertEqual(acks, [("ack", first), ("ack", second)])
        self.assertIsNone(self.session.in_flight)

    def test_falls_back_to_the_cable_route(self):
        """Direct route refused: the adb route (here: adb_forward replaced by a
        no-op, tabletd local) takes over and says so."""
        self.session.close()
        with socket.socket() as closed:
            closed.bind(("127.0.0.1", 0))
            dead_port = closed.getsockname()[1]
        original = link.adb_forward
        link.adb_forward = lambda *args: None
        try:
            self.session = link.Session(port=dead_port, host="127.0.0.1", psk=bytes(32),
                                        use_adb=True, adb_port=self.port)
            self.assertEqual(self.wait_for("connected"), ("connected", W, H))
            self.assertEqual(self.session.route, "adb")
        finally:
            link.adb_forward = original

    def test_idle_connection_stays_open(self):
        self.wait_for("connected")
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            for event in self.session.pump(0.1):
                self.assertNotEqual(event[0], "disconnected", event)
        self.assertTrue(self.session.connected)

    def test_reconnects_after_tabletd_restart(self):
        self.wait_for("connected")
        self.stop_daemon()
        self.assertEqual(self.wait_for("disconnected")[0], "disconnected")
        self.assertFalse(self.session.connected)
        self.start_daemon()
        self.assertEqual(self.wait_for("connected"), ("connected", W, H))
        seq = self.session.send_frame([])
        self.assertEqual(self.wait_for("ack"), ("ack", seq))

    def test_lost_frame_is_not_waited_for_forever(self):
        self.wait_for("connected")
        self.session.send_frame([])
        self.wait_for("ack")
        self.stop_daemon()
        try:
            self.session.send_frame([])
        except link.LinkError:
            pass
        self.wait_for("disconnected", timeout=8)
        self.assertIsNone(self.session.in_flight)
        self.start_daemon()
        self.wait_for("connected")
        self.assertTrue(self.session.can_send)


if __name__ == "__main__":
    unittest.main()
