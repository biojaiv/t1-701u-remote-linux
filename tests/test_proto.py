"""Protocol version 1: handshake, replies, and rejection of bad messages."""
import struct
import time
import unittest

from harness import (ACK, AUTH, BACKLIGHT, H, INFO, MAX_PAYLOAD, PING, PONG,
                     PRESENT, RECT, TIME, W, DaemonCase, frame, rect)


class Handshake(DaemonCase):
    def test_info(self):
        conn = self.connect()
        version, width, height, flags = struct.unpack("<HHHB", conn.info[:7])
        # bit 2: MSG_COPY understood (since 2026-11-16); bit 1 would mean encrypted
        self.assertEqual((version, width, height, flags), (1, W, H, 0x04 | 0x10))
        self.assertEqual(len(conn.info), 7 + 16)

    def test_nonce_differs_between_connections(self):
        first = self.connect().info[7:]
        second = self.connect().info[7:]
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, bytes(16))

    def test_ping_pong(self):
        conn = self.connect()
        conn.send(frame(PING, struct.pack("<I", 0xDEADBEEF)))
        self.assertEqual(conn.recv_type(PONG), struct.pack("<I", 0xDEADBEEF))

    def test_present_ack(self):
        conn = self.connect()
        for seq in (0, 1, 0xFFFFFFFF):
            conn.send(frame(PRESENT, struct.pack("<I", seq)))
            self.assertEqual(conn.recv_type(ACK), struct.pack("<I", seq))

    def test_time_auth_backlight_are_accepted(self):
        conn = self.connect()
        conn.send(frame(TIME, struct.pack("<Qi", 1794800000, 3600)))
        conn.send(frame(AUTH, bytes(32)))
        conn.send(frame(BACKLIGHT, b"\x80"))
        self.sync(conn)

    def test_messages_split_into_single_bytes(self):
        conn = self.connect()
        for byte in frame(PING, struct.pack("<I", 7)):
            conn.send(bytes([byte]))
            time.sleep(0.002)
        self.assertEqual(conn.recv_type(PONG), struct.pack("<I", 7))

    def test_many_messages_in_one_write(self):
        conn = self.connect()
        conn.send(b"".join(frame(PING, struct.pack("<I", i)) for i in range(50)))
        for i in range(50):
            self.assertEqual(conn.recv_type(PONG), struct.pack("<I", i))

    def test_new_connection_replaces_old(self):
        old = self.connect()
        new = self.connect()
        self.assertTrue(old.closed_by_peer())
        self.sync(new)


class BadMessages(DaemonCase):
    """Each bad message closes the connection; tabletd takes a new one."""

    def check_rejected(self, data):
        conn = self.connect()
        conn.send(data)
        self.assertTrue(conn.closed_by_peer(), "connection stayed open")
        self.sync(self.connect())

    def test_unknown_type(self):
        self.check_rejected(frame(0x7F, b"abcd"))

    def test_tablet_to_laptop_type(self):
        self.check_rejected(frame(INFO, bytes(23)))

    def test_length_above_maximum(self):
        self.check_rejected(struct.pack("<BI", RECT, MAX_PAYLOAD + 1))

    def test_length_field_all_ones(self):
        self.check_rejected(struct.pack("<BI", RECT, 0xFFFFFFFF))

    def test_wrong_length_for_type(self):
        for msg_type, good in ((PRESENT, 4), (BACKLIGHT, 1), (TIME, 12),
                               (PING, 4), (AUTH, 32)):
            for bad in (good - 1, good + 1):
                with self.subTest(type=msg_type, length=bad):
                    self.check_rejected(frame(msg_type, bytes(bad)))

    def test_rect_header_only(self):
        self.check_rejected(frame(RECT, struct.pack("<HHHHB", 0, 0, 1, 1, 0)))

    def test_rect_zero_width_or_height(self):
        self.check_rejected(rect(0, 0, 0, 1, 0, bytes(2)))
        self.check_rejected(rect(0, 0, 1, 0, 0, bytes(2)))

    def test_rect_outside_screen(self):
        self.check_rejected(rect(W - 1, 0, 2, 1, 0, bytes(4)))
        self.check_rejected(rect(0, H - 1, 1, 2, 0, bytes(4)))
        self.check_rejected(rect(W, 0, 1, 1, 0, bytes(2)))
        self.check_rejected(rect(0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF, 0, bytes(2)))

    def test_rect_data_length_mismatch(self):
        self.check_rejected(rect(0, 0, 2, 2, 0, bytes(7)))
        self.check_rejected(rect(0, 0, 2, 2, 0, bytes(9)))

    def test_rect_unknown_format(self):
        self.check_rejected(rect(0, 0, 1, 1, 9, bytes(2)))


class Silence(DaemonCase):
    def test_client_dropped_after_five_seconds(self):
        conn = self.connect()
        start = time.monotonic()
        self.assertTrue(conn.closed_by_peer(timeout=8))
        self.assertGreater(time.monotonic() - start, 4.0)
        self.sync(self.connect())

    def test_ping_keeps_connection_open(self):
        conn = self.connect()
        for i in range(7):
            time.sleep(1)
            conn.send(frame(PING, struct.pack("<I", i)))
            self.assertEqual(conn.recv_type(PONG), struct.pack("<I", i))


class Daemon(unittest.TestCase):
    def test_daemon_mode_detaches_and_logs(self):
        import os
        import pathlib
        import signal
        import subprocess
        import tempfile

        from harness import TABLETD, Conn

        with tempfile.TemporaryDirectory() as tmp:
            log = pathlib.Path(tmp) / "log.txt"
            name = f"tabletd-test-daemon-{os.getpid()}"
            done = subprocess.run(
                [str(TABLETD), "--fb", tmp + "/fb.raw", "--geom", f"{W}x{H}",
                 "--listen", f"abstract:{name}", "--daemon", str(log)], timeout=5)
            self.assertEqual(done.returncode, 0)   # the starter returns at once
            pid = None
            try:
                deadline = time.monotonic() + 3
                while pid is None and time.monotonic() < deadline:
                    first = log.read_text().splitlines()[:1]
                    if first and first[0].startswith("tabletd pid "):
                        pid = int(first[0].split()[2])
                    else:
                        time.sleep(0.02)
                self.assertIsNotNone(pid)
                deadline = time.monotonic() + 3
                while True:
                    try:
                        conn = Conn(name)
                        break
                    except (ConnectionRefusedError, FileNotFoundError):
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.02)
                self.assertEqual(conn.recv()[0], INFO)
                conn.close()
                self.assertNotEqual(os.getsid(pid), os.getsid(0))
            finally:
                if pid:
                    os.kill(pid, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
