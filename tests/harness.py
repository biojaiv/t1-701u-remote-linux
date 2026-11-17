"""Starts the x86 build of tabletd against a file framebuffer and talks to it."""
import itertools
import os
import pathlib
import socket
import struct
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TABLETD = ROOT / "build" / "x86" / "tabletd"
W, H = 600, 1024
MAX_PAYLOAD = 1300000

RECT, PRESENT, BACKLIGHT, TIME, PING, AUTH = 0x01, 0x02, 0x03, 0x04, 0x05, 0x06
INFO, ACK, TOUCH, KEY, STATUS, PONG = 0x41, 0x42, 0x43, 0x44, 0x45, 0x46

_counter = itertools.count()


def frame(msg_type, payload=b""):
    return struct.pack("<BI", msg_type, len(payload)) + payload


def rect(x, y, w, h, fmt, data):
    return frame(RECT, struct.pack("<HHHHB", x, y, w, h, fmt) + data)


class Conn:
    def __init__(self, name):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(3)
        self.sock.connect("\0" + name)

    def send(self, data):
        self.sock.sendall(data)

    def _exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise EOFError("connection closed by tabletd")
            buf += chunk
        return buf

    def recv(self):
        """Returns (type, payload) of the next message."""
        msg_type, length = struct.unpack("<BI", self._exact(5))
        return msg_type, self._exact(length)

    def recv_type(self, wanted):
        """Skips other messages (e.g. STATUS) until one of the wanted type."""
        while True:
            msg_type, payload = self.recv()
            if msg_type == wanted:
                return payload

    def closed_by_peer(self, timeout=3):
        """True if tabletd closes the connection within the timeout."""
        self.sock.settimeout(timeout)
        try:
            while True:
                if not self.sock.recv(4096):
                    return True
        except (socket.timeout, TimeoutError):
            return False
        except ConnectionError:
            return True

    def close(self):
        self.sock.close()


class DaemonCase(unittest.TestCase):
    """Base class: one fresh tabletd per test."""

    extra_args = ()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.fb_path = self.dir / "fb.raw"
        self.name = f"tabletd-test-{os.getpid()}-{next(_counter)}"
        self.stderr_path = self.dir / "stderr.txt"
        self.prepare()
        with open(self.stderr_path, "wb") as err:
            self.proc = subprocess.Popen(
                [str(TABLETD), "--fb", str(self.fb_path), "--geom", f"{W}x{H}",
                 "--listen", f"abstract:{self.name}", *self.args()],
                stderr=err)
        self.conns = []

    def prepare(self):
        """Hook: create files the daemon needs before it starts."""

    def args(self):
        return list(self.extra_args)

    def tearDown(self):
        for conn in self.conns:
            conn.close()
        alive = self.proc.poll() is None
        self.proc.kill()
        self.proc.wait()
        log = self.stderr_path.read_text(errors="replace")
        self.tmp.cleanup()
        self.assertNotIn("Sanitizer", log, log)
        self.assertNotIn("runtime error", log, log)
        self.assertTrue(alive, "tabletd died during the test:\n" + log)

    def connect(self, expect_info=True):
        deadline = time.monotonic() + 3
        while True:
            try:
                conn = Conn(self.name)
                break
            except (ConnectionRefusedError, FileNotFoundError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.02)
        self.conns.append(conn)
        if expect_info:
            msg_type, payload = conn.recv()
            self.assertEqual(msg_type, INFO)
            conn.info = payload
        return conn

    def framebuffer(self):
        return self.fb_path.read_bytes()

    def sync(self, conn, seq=1):
        """Waits until tabletd has processed everything sent so far."""
        conn.send(frame(PRESENT, struct.pack("<I", seq)))
        self.assertEqual(conn.recv_type(ACK), struct.pack("<I", seq))
