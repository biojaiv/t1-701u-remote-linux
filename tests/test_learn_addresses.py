"""MSG_ADDRESSES: hostd sends its current IPv6 addresses at every connection;
tabletd keeps them (--learn-file) and calls them first at its next start, so
the tablet has the laptop's addresses without copying them by hand before
leaving. Only global IPv6 (2000::/3) is kept; content that does not fit is
ignored without ending the session."""
import ipaddress
import os
import pathlib
import struct
import subprocess
import tempfile
import time
import unittest

from harness import H, TABLETD, W, DaemonCase, frame

ADDRESSES = 0x0A
INFO_ADDRESSES = 0x20


def addresses(port, *addrs, count=None):
    return frame(ADDRESSES, struct.pack("<HB", port, len(addrs) if count is None else count)
                 + b"".join(ipaddress.IPv6Address(a).packed for a in addrs))


class Learning(DaemonCase):
    def prepare(self):
        self.learned = self.dir / "internet.learned"

    def args(self):
        return ["--learn-file", str(self.learned)]

    def log(self):
        return self.stderr_path.read_text(errors="replace")

    def test_announced_in_info(self):
        conn = self.connect()
        self.assertTrue(conn.info[6] & INFO_ADDRESSES)

    def test_addresses_are_kept(self):
        conn = self.connect()
        conn.send(addresses(443, "2001:db8::5", "2001:db8:1::7"))
        self.sync(conn)
        self.assertEqual(self.learned.read_text(), "2001:db8::5 2001:db8:1::7 443\n")
        self.assertEqual(self.learned.stat().st_mode & 0o777, 0o600)

    def test_same_addresses_are_not_written_again(self):
        conn = self.connect()
        conn.send(addresses(7110, "2001:db8::5"))
        self.sync(conn, 1)
        conn.send(addresses(7110, "2001:db8::5"))
        self.sync(conn, 2)
        conn.send(addresses(7110, "2001:db8::6"))
        self.sync(conn, 3)
        self.assertEqual(self.log().count("kept the laptop's addresses"), 2)
        self.assertEqual(self.learned.read_text(), "2001:db8::6 7110\n")

    def test_not_global_is_ignored_and_the_session_stays(self):
        conn = self.connect()
        conn.send(addresses(7110, "2001:db8::5"))
        self.sync(conn, 1)
        for bad in (addresses(7110, "fd00::5"),             # unique local
                    addresses(7110, "fe80::1"),             # link-local
                    addresses(7110, "2001:db8::5", "::1"),  # one of them loopback
                    addresses(0, "2001:db8::9"),            # no port
                    addresses(7110, "2001:db8::9", count=2)):   # count does not match
            with self.subTest(bad=bad.hex()):
                conn.send(bad)
                self.sync(conn, 2)                         # still connected
        self.assertEqual(self.learned.read_text(), "2001:db8::5 7110\n")

    def test_wrong_length_is_a_protocol_error(self):
        conn = self.connect()
        for bad in (frame(ADDRESSES, b"\x00\x01\x01" + b"\x20" * 15),     # 15 bytes
                    frame(ADDRESSES, b"\x00\x01\x05" + b"\x20" * 80)):    # 5 addresses
            with self.subTest(length=len(bad)):
                conn.send(bad)
                self.assertTrue(conn.closed_by_peer())
                conn = self.connect()


class NotLearning(DaemonCase):
    def test_without_learn_file_not_announced_and_ignored(self):
        conn = self.connect()
        self.assertFalse(conn.info[6] & INFO_ADDRESSES)
        conn.send(addresses(7110, "2001:db8::5"))
        self.sync(conn)


class AtStart(unittest.TestCase):
    """What was kept is called first at the next start, before the given
    addresses, each address once, at most 4 in all."""

    def start(self, learned, *given, key=True):
        d = pathlib.Path(self.tmp.name)
        (d / "internet.learned").write_text(learned)
        args = [str(TABLETD), "--fb", str(d / "fb.raw"), "--geom", f"{W}x{H}",
                "--listen", f"abstract:learn-start-{os.getpid()}-{time.monotonic_ns()}",
                "--learn-file", str(d / "internet.learned")]
        for target in given:
            args += ["--secure-connect", target]
        if key:
            args += ["--key", str(d / "key")]
        proc = subprocess.Popen(args, stderr=subprocess.PIPE, text=True)
        time.sleep(0.6)
        proc.kill()
        return [line for line in proc.communicate()[1].splitlines()
                if "calling the laptop at" in line or "left out" in line or "not usable" in line]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        key = pathlib.Path(self.tmp.name) / "key"
        key.write_bytes(os.urandom(32))
        key.chmod(0o600)

    def tearDown(self):
        self.tmp.cleanup()

    def test_kept_ones_come_first_without_repeats(self):
        lines = self.start("2001:db8::5 2001:db8::6 443\n",
                           "[2001:db8::6]:443", "[2001:db8::7]:7110")
        self.assertEqual(lines, ["tabletd: calling the laptop at [2001:db8::5]:443",
                                 "tabletd: calling the laptop at [2001:db8::6]:443",
                                 "tabletd: calling the laptop at [2001:db8::7]:7110"])

    def test_at_most_four(self):
        lines = self.start("2001:db8::1 2001:db8::2 2001:db8::3 443\n",
                           "[2001:db8::7]:7110", "[2001:db8::8]:7110")
        self.assertIn("tabletd: more than 4 addresses, [2001:db8::8]:7110 left out", lines)
        self.assertEqual(sum("calling the laptop at" in line for line in lines), 4)

    def test_kept_ones_alone_make_the_tablet_call(self):
        lines = self.start("2001:db8::5 443\n")
        self.assertEqual(lines, ["tabletd: calling the laptop at [2001:db8::5]:443"])

    def test_unusable_file_is_not_used(self):
        for text in ("2001:db8::5\n", "fd00::5 443\n", "2001:db8::5 99999\n", "rubbish 443\n",
                     "2001:db8::1 2001:db8::2 2001:db8::3 2001:db8::4 2001:db8::5 443\n"):
            with self.subTest(text=text):
                lines = self.start(text, "[2001:db8::7]:7110")
                self.assertIn("tabletd: the kept addresses are not usable, ignored", lines)
                self.assertEqual([line for line in lines if "calling" in line],
                                 ["tabletd: calling the laptop at [2001:db8::7]:7110"])

    def test_not_used_without_the_key(self):
        lines = self.start("2001:db8::5 443\n", key=False)
        self.assertEqual(lines, [])


if __name__ == "__main__":
    unittest.main()
