"""tb-rootd and tb-su (phase 5): who may connect, output, exit status.

On the laptop the daemon runs as the normal user; what is tested is the
protocol and the check of the caller's user id, not root itself.
"""
import os
import pathlib
import subprocess
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROOTD = ROOT / "build" / "x86" / "tb-rootd"
TBSU = ROOT / "build" / "x86" / "tb-su"


class RootdCase(unittest.TestCase):
    allow = (os.getuid(),)

    def setUp(self):
        self.name = f"tb-rootd-test-{os.getpid()}-{id(self)}"
        args = [str(ROOTD), "--name", self.name, "--shell", "/bin/sh"]
        for uid in self.allow:
            args += ["--allow", str(uid)]
        self.daemon = subprocess.Popen(args, stderr=subprocess.PIPE)
        self.env = dict(os.environ, TB_ROOTD_NAME=self.name)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:      # wait until the socket exists
            probe = subprocess.run([str(TBSU), "true"], env=self.env, capture_output=True)
            if b"not running" not in probe.stderr:
                break
            time.sleep(0.02)

    def tearDown(self):
        alive = self.daemon.poll() is None
        self.daemon.kill()
        _, err = self.daemon.communicate()
        self.assertNotIn(b"Sanitizer", err)
        self.assertNotIn(b"runtime error", err)
        self.assertTrue(alive, "tb-rootd died during the test")

    def su(self, *args, timeout=10):
        return subprocess.run([str(TBSU), *args], env=self.env, capture_output=True,
                              timeout=timeout)


class Allowed(RootdCase):
    def test_output_and_status(self):
        done = self.su("echo", "hello")
        self.assertEqual((done.returncode, done.stdout), (0, b"hello\n"))

    def test_c_option_like_su(self):
        done = self.su("-c", "echo a; echo b")
        self.assertEqual(done.stdout, b"a\nb\n")

    def test_exit_status_is_passed_on(self):
        for code in (0, 1, 7, 255):
            self.assertEqual(self.su("-c", f"exit {code}").returncode, code)

    def test_stderr_is_delivered_too(self):
        self.assertIn(b"oops", self.su("-c", "echo oops >&2").stdout)

    def test_large_output(self):
        done = self.su("-c", "head -c 3000000 /dev/zero")
        self.assertEqual((done.returncode, len(done.stdout)), (0, 3000000))

    def test_killed_command_reports_signal(self):
        self.assertEqual(self.su("-c", "kill -9 $$").returncode, 128 + 9)

    def test_unknown_command(self):
        self.assertEqual(self.su("/nonexistent/tool").returncode, 127)

    def test_runs_as_the_daemons_user(self):
        self.assertEqual(self.su("id", "-u").stdout.strip(), str(os.getuid()).encode())

    def test_many_calls_in_a_row(self):
        for i in range(50):
            self.assertEqual(self.su("echo", str(i)).stdout, f"{i}\n".encode())

    def test_command_at_the_length_limit(self):
        long_ok = "echo " + "x" * 4000
        self.assertEqual(len(self.su("-c", long_ok).stdout), 4001)
        done = self.su("-c", "echo " + "x" * 5000)
        self.assertEqual(done.returncode, 2)
        self.assertIn(b"too long", done.stderr)

    def test_oversized_request_on_the_wire_is_dropped(self):
        import socket
        import struct
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect("\0" + self.name)
        sock.sendall(struct.pack("<I", 1 << 20) + b"id")
        sock.settimeout(3)
        try:
            self.assertEqual(sock.recv(16), b"")  # closed without running anything
        except ConnectionResetError:
            pass                                  # also a close, with unread data
        sock.close()
        self.assertEqual(self.su("echo", "still alive").stdout, b"still alive\n")

    def test_caller_that_disconnects_does_not_stop_the_daemon(self):
        marker = f"tb-rootd-sleeper-{os.getpid()}"
        proc = subprocess.Popen([str(TBSU), "-c", f"sleep 30; : {marker}"], env=self.env)
        time.sleep(0.3)
        self.assertEqual(subprocess.run(["pgrep", "-f", marker], capture_output=True).returncode, 0)
        proc.kill()
        proc.wait()
        self.assertEqual(self.su("echo", "ok").stdout, b"ok\n")
        deadline = time.monotonic() + 3          # the abandoned command is killed
        while (subprocess.run(["pgrep", "-f", f"sleep 30; : {marker}"],
                              capture_output=True).returncode == 0
               and time.monotonic() < deadline):
            time.sleep(0.05)
        self.assertNotEqual(subprocess.run(["pgrep", "-f", f"sleep 30; : {marker}"],
                                           capture_output=True).returncode, 0)


class Refused(RootdCase):
    allow = (0,)    # only root may call; the test user may not

    def test_other_user_is_refused(self):
        done = self.su("id")
        self.assertEqual(done.returncode, 126)
        self.assertEqual(done.stdout, b"")
        self.assertIn(b"refused", done.stderr)


class NotRunning(unittest.TestCase):
    def test_clear_message_without_daemon(self):
        env = dict(os.environ, TB_ROOTD_NAME=f"tb-rootd-absent-{os.getpid()}")
        done = subprocess.run([str(TBSU), "id"], env=env, capture_output=True)
        self.assertEqual(done.returncode, 126)
        self.assertIn(b"not running", done.stderr)


if __name__ == "__main__":
    unittest.main()
