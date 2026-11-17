"""tb-fetch (receiving the Linux image over Wi-Fi) with scripts/send-file.py."""
import hashlib
import os
import re
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FETCH = ROOT / "build" / "x86" / "tb-fetch"
SEND = ROOT / "scripts" / "send-file.py"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Fetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.src = self.dir / "image"
        self.src.write_bytes(os.urandom(3 * 1024 * 1024 + 17))
        self.dest = self.dir / "received"
        self.port = free_port()

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, size=None, sender="127.0.0.1"):
        size = self.src.stat().st_size if size is None else size
        proc = subprocess.Popen([str(FETCH), "--listen", str(self.port), "--from", sender,
                                 "--timeout", "10", str(self.dest), str(size)],
                                stderr=subprocess.PIPE)
        time.sleep(0.3)
        return proc

    def send(self):
        return subprocess.run([sys.executable, str(SEND), "127.0.0.1", str(self.port),
                               str(self.src)], capture_output=True, timeout=30)

    def finish(self, proc):
        _, err = proc.communicate(timeout=30)
        self.assertNotIn(b"Sanitizer", err)
        return proc.returncode, err

    def test_file_arrives_unchanged(self):
        proc = self.fetch()
        self.assertEqual(self.send().returncode, 0)
        code, _ = self.finish(proc)
        self.assertEqual(code, 0)
        self.assertEqual(self.dest.read_bytes(), self.src.read_bytes())
        self.assertEqual(self.dest.stat().st_mode & 0o777, 0o600)

    def test_complete_line_carries_the_hash_of_the_received_bytes(self):
        """iso-send compares it with hashlib on the laptop: works above 2 GiB
        and notices content from another local program."""
        proc = self.fetch()
        self.assertEqual(self.send().returncode, 0)
        code, err = self.finish(proc)
        self.assertEqual(code, 0)
        found = re.search(rb"complete \(\d+ bytes, blake2b-256 ([0-9a-f]{64})\)", err)
        self.assertIsNotNone(found, err)
        expected = hashlib.blake2b(self.src.read_bytes(), digest_size=32).hexdigest()
        self.assertEqual(found.group(1).decode(), expected)

    def test_wrong_size_leaves_no_file(self):
        proc = self.fetch(size=self.src.stat().st_size + 1)
        self.send()
        code, err = self.finish(proc)
        self.assertEqual(code, 1)
        self.assertFalse(self.dest.exists())
        self.assertFalse(pathlib.Path(f"{self.dest}.part").exists())

    def test_other_sender_is_refused_then_the_right_one_served(self):
        proc = self.fetch()
        intruder = socket.socket()
        intruder.bind(("127.0.0.2", 0))
        intruder.connect(("127.0.0.1", self.port))
        self.assertEqual(intruder.recv(1), b"")        # closed at once, nothing written
        intruder.close()
        self.assertFalse(pathlib.Path(f"{self.dest}.part").exists())
        self.assertEqual(self.send().returncode, 0)
        code, err = self.finish(proc)
        self.assertEqual(code, 0)
        self.assertIn(b"refused 127.0.0.2", err)
        self.assertEqual(self.dest.read_bytes(), self.src.read_bytes())

    def test_gives_up_when_nobody_comes(self):
        proc = subprocess.Popen([str(FETCH), "--listen", str(self.port), "--from", "127.0.0.1",
                                 "--timeout", "1", str(self.dest), "10"], stderr=subprocess.PIPE)
        code, err = self.finish(proc)
        self.assertEqual(code, 1)
        self.assertIn(b"nobody came", err)


if __name__ == "__main__":
    unittest.main()
