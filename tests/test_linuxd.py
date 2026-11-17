"""tb-linuxd and tb-linux: starting the Linux session and ending it completely.

On the laptop the daemon runs as the normal user without chroot and without
--user, so what is tested is the protocol, the caller check, the environment
and the ending of the session's process group (SIGTERM, then SIGKILL). The
chroot and the change of user need root and are checked on the tablet.
"""
import os
import pathlib
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LINUXD = ROOT / "build" / "x86" / "tb-linuxd"
CLIENT = ROOT / "build" / "x86" / "tb-linux"

SESSION = """#!/bin/sh
echo "leader $$" >> "$STATE"
env | sort > "$STATE.env"
echo "output of the session"
[ -n "$IGNORE_TERM" ] && trap '' TERM
[ -z "$IGNORE_TERM" ] && trap 'echo term >> "$STATE"; exit 3' TERM
sleep 1000 &
echo "child $!" >> "$STATE"
[ -n "$EXIT_SOON" ] && { sleep 0.3; exit 7; }
while :; do sleep 0.1; done
"""


def alive(pid):
    try:
        state = pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


class LinuxdCase(unittest.TestCase):
    allow = None          # default: our own uid
    env = ()
    grace = 400

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.tmp.name)
        self.state = d / "state"
        self.log = d / "session.log"
        self.program = d / "session.sh"
        self.program.write_text(SESSION)
        self.program.chmod(0o755)
        self.name = f"tb-linuxd-test-{os.getpid()}-{id(self)}"
        args = [str(LINUXD), "--name", self.name, "--allow",
                str(os.getuid() if self.allow is None else self.allow),
                "--log", str(self.log), "--grace", str(self.grace),
                "--env", "PATH=/usr/bin:/bin", "--env", f"STATE={self.state}"]
        for item in self.env:
            args += ["--env", item]
        self.daemon = subprocess.Popen(args + ["--", str(self.program)], stderr=subprocess.PIPE)
        self.client_env = dict(os.environ, TB_LINUXD_NAME=self.name)
        time.sleep(0.2)

    def tearDown(self):
        for line in self.lines():
            if line.split()[0] in ("leader", "child"):
                try:
                    os.kill(int(line.split()[1]), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        alive_daemon = self.daemon.poll() is None
        self.daemon.kill()
        _, err = self.daemon.communicate()
        self.tmp.cleanup()
        self.assertNotIn(b"Sanitizer", err)
        self.assertNotIn(b"runtime error", err)
        self.assertTrue(alive_daemon, "tb-linuxd died during the test")

    def lines(self):
        return self.state.read_text().splitlines() if self.state.exists() else []

    def pids(self):
        return {line.split()[0]: int(line.split()[1]) for line in self.lines()
                if line.split()[0] in ("leader", "child")}

    def wait_until(self, condition, what, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.02)
        self.fail(f"{what} did not happen within {timeout} s")

    def start_client(self):
        client = subprocess.Popen([str(CLIENT)], env=self.client_env,
                                  stderr=subprocess.DEVNULL)
        self.wait_until(lambda: len(self.pids()) == 2, "session start")
        return client


class Session(LinuxdCase):
    def test_sigterm_to_the_client_ends_the_whole_session(self):
        client = self.start_client()
        pids = self.pids()
        client.send_signal(signal.SIGTERM)
        self.assertEqual(client.wait(timeout=5), 3)    # the session's own exit status
        self.assertIn("term", self.lines())
        for name, pid in pids.items():
            self.assertFalse(alive(pid), f"{name} still runs after the client returned")

    def test_client_that_dies_ends_the_session_too(self):
        client = self.start_client()
        pids = self.pids()
        client.kill()
        client.wait()
        self.wait_until(lambda: not any(alive(p) for p in pids.values()), "session end")

    def test_session_output_goes_to_the_log(self):
        client = self.start_client()
        client.send_signal(signal.SIGTERM)
        client.wait(timeout=5)
        self.assertIn("output of the session", self.log.read_text())

    def test_environment_is_only_what_was_given(self):
        client = self.start_client()
        client.send_signal(signal.SIGTERM)
        client.wait(timeout=5)
        names = {line.split("=", 1)[0] for line in
                 pathlib.Path(f"{self.state}.env").read_text().splitlines()}
        names -= {"PWD", "SHLVL", "_", "OLDPWD"}    # set by the shell itself
        self.assertEqual(names, {"PATH", "STATE"})

    def test_a_new_session_after_the_first(self):
        client = self.start_client()
        client.send_signal(signal.SIGTERM)
        client.wait(timeout=5)
        self.state.unlink()
        client = self.start_client()
        client.send_signal(signal.SIGTERM)
        self.assertEqual(client.wait(timeout=5), 3)

    def test_second_caller_waits_for_the_first_session(self):
        first = self.start_client()
        second = subprocess.Popen([str(CLIENT)], env=self.client_env,
                                  stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        self.assertEqual(sum(l.startswith("leader") for l in self.lines()), 1)
        first.send_signal(signal.SIGTERM)
        first.wait(timeout=5)
        self.wait_until(lambda: sum(l.startswith("leader") for l in self.lines()) == 2,
                        "second session")
        second.send_signal(signal.SIGTERM)
        self.assertEqual(second.wait(timeout=5), 3)


class SessionEndsByItself(LinuxdCase):
    env = ("EXIT_SOON=1",)

    def test_status_comes_back_and_the_child_is_gone(self):
        client = subprocess.Popen([str(CLIENT)], env=self.client_env,
                                  stderr=subprocess.DEVNULL)
        self.assertEqual(client.wait(timeout=5), 7)
        self.assertFalse(alive(self.pids()["child"]))


class SessionIgnoresTerm(LinuxdCase):
    env = ("IGNORE_TERM=1",)

    def test_sigkill_after_the_grace_period(self):
        client = self.start_client()
        pids = self.pids()
        started = time.monotonic()
        client.send_signal(signal.SIGTERM)
        self.assertEqual(client.wait(timeout=5), 128 + signal.SIGKILL)
        self.assertGreaterEqual(time.monotonic() - started, 0.35)
        for name, pid in pids.items():
            self.assertFalse(alive(pid), f"{name} survived SIGKILL")


class OtherUser(LinuxdCase):
    allow = 54321   # not us

    def test_refused_and_nothing_started(self):
        client = subprocess.run([str(CLIENT)], env=self.client_env, capture_output=True,
                                timeout=5)
        self.assertIn(client.returncode, (125, 126))
        time.sleep(0.3)
        self.assertEqual(self.lines(), [])


class Arguments(unittest.TestCase):
    def run_daemon(self, *args):
        return subprocess.run([str(LINUXD), "--name", f"tb-linuxd-args-{os.getpid()}", *args],
                              capture_output=True, timeout=5)

    def test_relative_program_is_refused(self):
        self.assertEqual(self.run_daemon("--", "session.sh").returncode, 2)

    def test_session_as_root_is_refused(self):
        self.assertEqual(self.run_daemon("--user", "0:0", "--", "/bin/true").returncode, 2)

    def test_session_user_must_not_be_a_caller(self):
        done = self.run_daemon("--user", "2000:2000", "--", "/bin/true")
        self.assertEqual(done.returncode, 2)
        self.assertIn(b"must not be a caller", done.stderr)

    def test_daemon_option_detaches_and_serves(self):
        with tempfile.TemporaryDirectory() as d:
            log = pathlib.Path(d) / "linuxd.log"
            name = f"tb-linuxd-daemon-{os.getpid()}"
            inherited = os.open(os.devnull, os.O_RDONLY)     # like su's terminal descriptors
            started = subprocess.run(
                [str(LINUXD), "--name", name, "--allow", str(os.getuid()), "--daemon", str(log),
                 "--env", "PATH=/usr/bin:/bin", "--", "/bin/sh", "-c", "exit 5"],
                capture_output=True, timeout=5, pass_fds=(inherited,))
            os.close(inherited)
            self.assertEqual(started.returncode, 0)     # returned although the daemon runs
            time.sleep(0.2)
            pid = int(log.read_text().split()[2])
            fds = sorted(int(f) for f in os.listdir(f"/proc/{pid}/fd"))
            self.assertNotIn(inherited, fds)
            self.assertEqual(len(fds), 4, fds)            # stdin, stdout, log, socket
            try:
                done = subprocess.run([str(CLIENT)], env=dict(os.environ, TB_LINUXD_NAME=name),
                                      capture_output=True, timeout=5)
                self.assertEqual(done.returncode, 5)
                self.assertNotIn("Sanitizer", log.read_text())
            finally:
                os.kill(pid, signal.SIGKILL)

    def test_without_daemon_the_client_says_so(self):
        done = subprocess.run([str(CLIENT)], env=dict(os.environ, TB_LINUXD_NAME="no-such-x"),
                              capture_output=True, timeout=5)
        self.assertEqual(done.returncode, 126)
        self.assertIn(b"not running", done.stderr)


if __name__ == "__main__":
    unittest.main()
