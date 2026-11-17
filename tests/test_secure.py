"""Encrypted Wi-Fi channel: handshake, records, and every way to get it wrong.

tabletd (x86, sanitizers) listens only on 127.0.0.1 here. The laptop side is
host/secure.py on PyNaCl/libsodium, the tablet side Monocypher, so a working
connection also shows that the two libraries agree.
"""
import os
import pathlib
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import encode, link, secure  # noqa: E402

from harness import H, TABLETD, W  # noqa: E402

import numpy as np  # noqa: E402


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def frame(msg_type, payload=b""):
    return struct.pack("<BI", msg_type, len(payload)) + payload


class SecureCase(unittest.TestCase):
    key_mode = 0o600
    key_size = 32

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.key = os.urandom(32)
        self.key_file = self.dir / "key"
        self.key_file.write_bytes(self.key[:self.key_size] + bytes(max(0, self.key_size - 32)))
        self.key_file.chmod(self.key_mode)
        self.port = free_port()
        self.fb = self.dir / "fb.raw"
        self.err = open(self.dir / "stderr.txt", "wb")
        self.proc = subprocess.Popen(
            [str(TABLETD), "--fb", str(self.fb), "--geom", f"{W}x{H}",
             "--listen", f"abstract:tabletd-sec-{os.getpid()}-{self.port}",
             "--secure-listen", f"127.0.0.1:{self.port}", "--key", str(self.key_file)],
            stderr=self.err)
        self.links = []

    def tearDown(self):
        for item in self.links:
            item.close()
        alive = self.proc.poll() is None
        self.proc.kill()
        self.proc.wait()
        self.err.close()
        log = (self.dir / "stderr.txt").read_text(errors="replace")
        self.tmp.cleanup()
        self.assertNotIn("Sanitizer", log, log)
        self.assertNotIn("runtime error", log, log)
        if self.expect_running():
            self.assertTrue(alive, "tabletd died:\n" + log)

    def expect_running(self):
        return True

    def log(self):
        return (self.dir / "stderr.txt").read_text(errors="replace")

    def raw(self):
        """A bare TCP connection to the encrypted port, once it is listening."""
        deadline = time.monotonic() + 3
        while True:
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
                self.links.append(sock)
                return sock
            except ConnectionRefusedError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.02)

    def connect(self, psk=None):
        self.raw().close()          # waits until the port is open
        conn = link.Link(host="127.0.0.1", port=self.port, psk=psk or self.key, timeout=3)
        self.links.append(conn)
        return conn

    def closed(self, sock, timeout=7):
        sock.settimeout(timeout)
        try:
            while True:
                if not sock.recv(4096):
                    return True
        except (socket.timeout, TimeoutError):
            return False
        except ConnectionError:
            return True


class Works(SecureCase):
    def test_info_says_encrypted(self):
        conn = self.connect()
        self.assertEqual((conn.width, conn.height), (W, H))
        self.assertEqual(conn.flags & 0x02, 0x02)

    def test_ping_and_present(self):
        conn = self.connect()
        conn.ping(77)
        conn.present(5)

    def test_full_screen_spans_many_records(self):
        conn = self.connect()
        enc = encode.Encoder(W, H)
        picture = np.random.default_rng(1).integers(0, 65536, (H, W), dtype=np.uint16)
        for payload in enc.encode(picture):
            conn.send(link.RECT, payload)
        conn.present(1)
        r5, g6, b5 = picture >> 11, (picture >> 5) & 0x3F, picture & 0x1F
        want = np.stack([r5 << 3 | r5 >> 2, g6 << 2 | g6 >> 4, b5 << 3 | b5 >> 2,
                         np.full_like(picture, 255)], axis=-1).astype(np.uint8).tobytes()
        self.assertEqual(self.fb.read_bytes(), want)

    def test_session_reconnects_with_a_fresh_handshake(self):
        self.raw().close()
        session = link.Session(port=self.port, host="127.0.0.1", psk=self.key)
        self.links.append(session)
        events = []
        deadline = time.monotonic() + 5
        while not any(e[0] == "connected" for e in events) and time.monotonic() < deadline:
            events += session.pump(0.05)
        seq = session.send_frame([])
        while not any(e == ("ack", seq) for e in events) and time.monotonic() < deadline:
            events += session.pump(0.05)
        self.assertIn(("ack", seq), events)

    def test_plain_socket_still_works_beside_it(self):
        self.connect()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.links.append(sock)
        sock.connect(f"\0tabletd-sec-{os.getpid()}-{self.port}")
        sock.settimeout(3)
        self.assertEqual(sock.recv(1), bytes([0x41]))    # INFO, unencrypted


class Refused(SecureCase):
    def test_wrong_key(self):
        with self.assertRaises(link.LinkError):
            self.connect(psk=os.urandom(32))
        self.assertIn("wrong key", self.log())

    def test_wrong_key_does_not_disturb_the_active_client(self):
        good = self.connect()
        with self.assertRaises(link.LinkError):
            self.connect(psk=os.urandom(32))
        good.ping(1)

    def test_silent_connection_is_dropped_and_the_client_keeps_running(self):
        good = self.connect()
        idle = self.raw()
        start = time.monotonic()
        for i in range(3):          # keep the good one alive meanwhile
            time.sleep(1)
            good.ping(i)
        self.assertTrue(self.closed(idle))
        self.assertGreater(time.monotonic() - start, 4.5)
        good.ping(9)

    def test_wrong_magic(self):
        sock = self.raw()
        sock.sendall(b"XXXX" + os.urandom(32))
        self.assertTrue(self.closed(sock, 3))

    def test_low_order_point_is_refused(self):
        sock = self.raw()
        sock.sendall(b"HWC1" + bytes(32))
        self.assertTrue(self.closed(sock, 3))
        self.assertIn("bad hello", self.log())

    def test_oversized_first_record(self):
        sock = self.raw()
        channel = secure.client_handshake(sock, self.key)
        sock.sendall(channel.seal(bytes(200)))
        self.assertTrue(self.closed(sock, 3))
        self.assertIn("bad first record", self.log())

    def test_tampered_record_closes_the_connection(self):
        conn = self.connect()
        record = bytearray(conn.channel.seal(frame(link.PING, bytes(4))))
        record[10] ^= 0x01
        conn.sock.sendall(bytes(record))
        self.assertTrue(self.closed(conn.sock, 3))
        self.assertIn("record failed authentication", self.log())

    def test_replayed_record_from_an_earlier_session(self):
        first = self.connect()
        replay = first.channel.seal(frame(link.PING, bytes(4)))
        first.close()
        second = self.connect()
        second.sock.sendall(replay)
        self.assertTrue(self.closed(second.sock, 3))

    def test_record_length_out_of_range(self):
        for body in (0, 16, secure.MAX_PLAIN + secure.MAC_LEN + 1):
            with self.subTest(body=body):
                conn = self.connect()
                conn.sock.sendall(struct.pack("<I", body))
                self.assertTrue(self.closed(conn.sock, 3))

    def test_bad_frame_inside_a_valid_record(self):
        conn = self.connect()
        conn.sock.sendall(conn.channel.seal(frame(0x7F, b"abcd")))
        self.assertTrue(self.closed(conn.sock, 3))
        self.connect().ping(1)


class KeyFileMode(SecureCase):
    key_mode = 0o644

    def expect_running(self):
        return False

    def test_readable_key_file_stops_tabletd(self):
        self.assertEqual(self.proc.wait(timeout=5), 1)
        self.assertIn("private 32-byte file", self.log())


class KeyFileSize(SecureCase):
    key_size = 33

    def expect_running(self):
        return False

    def test_wrong_size_stops_tabletd(self):
        self.assertEqual(self.proc.wait(timeout=5), 1)


class LaptopKeyFile(unittest.TestCase):
    def test_load_key_checks_mode_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "key"
            path.write_bytes(os.urandom(32))
            path.chmod(0o600)
            self.assertEqual(len(secure.load_key(path)), 32)
            path.chmod(0o640)
            with self.assertRaises(secure.SecureError):
                secure.load_key(path)
            path.chmod(0o600)
            path.write_bytes(os.urandom(31))
            with self.assertRaises(secure.SecureError):
                secure.load_key(path)


if __name__ == "__main__":
    unittest.main()
