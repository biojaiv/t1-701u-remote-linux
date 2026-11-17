"""Who may use tabletd's local (abstract) socket. Every process on the device
can reach an abstract socket; a caller there would replace the laptop's
session, receive every touch and draw on the screen. tabletd accepts root,
adbd (2000) and its own uid by default; --local-uids replaces that list.
The tests run as one uid, so refusal is shown with a list without it."""
import os
import time
import unittest

from harness import DaemonCase


class OwnUidAccepted(DaemonCase):
    def test_default_list_accepts_tabletd_s_own_uid(self):
        conn = self.connect()
        self.assertTrue(conn.info)


class OtherUidRefused(DaemonCase):
    extra_args = ("--local-uids", "0,2000")    # not the uid the tests run as

    def test_caller_is_closed_without_info(self):
        if os.getuid() in (0, 2000):
            self.skipTest("tests run as an allowed uid")
        conn = self.connect(expect_info=False)
        conn.sock.settimeout(3)
        self.assertEqual(conn.sock.recv(1), b"", "a refused caller got data")
        deadline = time.monotonic() + 3
        while "refused" not in self.stderr_path.read_text() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIn(f"local connection from uid {os.getuid()} refused",
                      self.stderr_path.read_text())


class OwnUidInList(DaemonCase):
    """Listed explicitly, the same caller is accepted: the refusal above comes
    from the list, not from anything else."""

    def args(self):
        return ["--local-uids", f"0,{os.getuid()}"]

    def test_listed_uid_gets_info(self):
        conn = self.connect()
        self.assertTrue(conn.info)


if __name__ == "__main__":
    unittest.main()
