"""The tablet calls the laptop (tabletd --secure-connect, hostd's listener):
the route used from another network through the laptop's router. The
handshake and the proof of the key are the same as on the Wi-Fi link."""
import ipaddress
import os
import pathlib
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import encode, link  # noqa: E402
from harness import H, TABLETD, W  # noqa: E402
import numpy as np  # noqa: E402


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Calling(unittest.TestCase):
    tablet_key_differs = False
    target = "127.0.0.1:{port}"      # what tabletd calls

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.tmp.name)
        self.key = os.urandom(32)
        key_file = d / "key"
        key_file.write_bytes(os.urandom(32) if self.tablet_key_differs else self.key)
        key_file.chmod(0o600)
        self.port = free_port()
        self.err = open(d / "stderr.txt", "wb")
        self.session = link.Session(port=free_port(), use_adb=False, psk=self.key,
                                    listen_port=self.port, max_in_flight=2)
        self.proc = subprocess.Popen(
            [str(TABLETD), "--fb", str(d / "fb.raw"), "--geom", f"{W}x{H}",
             "--listen", f"abstract:tabletd-inet-{os.getpid()}-{self.port}",
             *self.connect_args(), "--key", str(key_file)],
            stderr=self.err)

    def connect_args(self):
        targets = self.target if isinstance(self.target, tuple) else (self.target,)
        args = []
        for target in targets:
            args += ["--secure-connect", target.format(port=self.port, closed=self.closed)]
        return args

    closed = None

    def tearDown(self):
        self.session.close()
        alive = self.proc.poll() is None
        self.proc.kill()
        self.proc.wait()
        self.err.close()
        log = (pathlib.Path(self.tmp.name) / "stderr.txt").read_text(errors="replace")
        self.tmp.cleanup()
        self.assertNotIn("Sanitizer", log, log)
        self.assertTrue(alive, "tabletd died")

    def pump_until(self, kind, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.session.pump(0.05):
                if event[0] == kind:
                    return event
        return None


class Works(Calling):
    def test_tablet_calls_and_a_frame_arrives(self):
        event = self.pump_until("connected")
        self.assertIsNotNone(event, "the tablet's call was not accepted")
        self.assertEqual(self.session.route, "internet")
        self.assertEqual(event[1:], (W, H))
        self.assertTrue(self.session.link.flags & 0x02, "not encrypted")
        enc = encode.Encoder(W, H, ordered=True, copy=bool(self.session.link.flags & 0x04))
        frame = np.full((H, W), 0x07E0, dtype=np.uint16)
        self.session.send_frame(enc.encode(frame))
        self.assertIsNotNone(self.pump_until("ack"), "no ACK")

    def test_tablet_calls_again_after_the_laptop_hung_up(self):
        self.assertIsNotNone(self.pump_until("connected"))
        self.session.link.close()
        self.session.link = None
        self.assertIsNotNone(self.pump_until("connected", timeout=15), "no second call")


class TabletVanishes(Calling):
    """The tablet disappears without closing the connection (another network,
    a new address; here: tabletd stopped with SIGSTOP, so nothing answers and
    nothing is closed). hostd must let go of that link and take the tablet's
    next call; before, it kept the dead link and refused every new call."""

    def events_for(self, seconds, until):
        seen = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not until(seen):
            seen += self.session.pump(0.05)
        return seen

    def freeze_first_tablet(self):
        self.assertIsNotNone(self.pump_until("connected"), "first call not accepted")
        os.kill(self.proc.pid, signal.SIGSTOP)
        self.addCleanup(lambda: self.proc.poll() is None and os.kill(self.proc.pid, signal.SIGCONT))

    def test_silent_link_is_dropped(self):
        self.freeze_first_tablet()
        start = time.monotonic()
        seen = self.events_for(10, lambda ev: any(e[0] == "disconnected" for e in ev))
        dropped = [e for e in seen if e[0] == "disconnected"]
        self.assertTrue(dropped, "hostd kept the silent link")
        self.assertIn("silent", dropped[0][1])
        self.assertLess(time.monotonic() - start, 8)

    def test_new_call_replaces_the_dead_link(self):
        self.freeze_first_tablet()
        d = pathlib.Path(self.tmp.name)
        second = subprocess.Popen(
            [str(TABLETD), "--fb", str(d / "fb2.raw"), "--geom", f"{W}x{H}",
             "--listen", f"abstract:tabletd-inet2-{os.getpid()}-{self.port}",
             "--secure-connect", f"[::1]:{self.port}", "--key", str(d / "key")],
            stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (second.kill(), second.wait()))
        seen = self.events_for(4, lambda ev: [e[0] for e in ev][-1:] == ["connected"])
        kinds = [e[0] for e in seen]
        self.assertIn("disconnected", kinds, "the dead link was not given up")
        self.assertEqual(kinds[-1:], ["connected"], "the new call was not taken within 4 s")
        self.assertEqual(self.session.link.sock.getpeername()[0], "::1")   # the second tablet


class OverIPv6(Calling):
    """Behind DS-Lite (no public IPv4) only IPv6 reaches the laptop from outside."""
    target = "[::1]:{port}"

    def test_tablet_calls_over_ipv6(self):
        event = self.pump_until("connected")
        self.assertIsNotNone(event, "no call over IPv6")
        self.assertEqual(self.session.route, "internet")
        peer = self.session.link.sock.getpeername()[0]
        self.assertEqual(peer, "::1")


class FromHome(Calling):
    """At home the tablet may reach the laptop's public IPv6 from inside:
    that call is Wi-Fi, not Internet."""

    def setUp(self):
        self._saved = link.local_networks
        link.local_networks = lambda: [ipaddress.ip_network("127.0.0.0/8"),
                                       ipaddress.ip_network("10.1.2.3/32")]
        super().setUp()

    def tearDown(self):
        super().tearDown()
        link.local_networks = self._saved

    def test_call_from_the_home_network_counts_as_wifi(self):
        self.assertIsNotNone(self.pump_until("connected"))
        self.assertEqual(self.session.route, "wifi")


class StrangersOnThePort(Calling):
    """Strangers on the internet port (silent ones holding a handshake, one
    sending garbage) must not keep the tablet's own call out. They come
    from ::1, the tablet from 127.0.0.1: the pause after the garbage caller
    hits only its own address."""

    def test_tablet_gets_in_despite_strangers(self):
        stop = threading.Event()
        strangers = []

        def attack():
            n = 0
            while not stop.is_set():
                try:
                    sock = socket.create_connection(("::1", self.port), timeout=1)
                    if n % 3 == 2:
                        sock.sendall(os.urandom(200))     # a noisy one now and then
                    strangers.append(sock)
                except OSError:
                    pass
                n += 1
                stop.wait(0.3)

        attacker = threading.Thread(target=attack, daemon=True)
        attacker.start()
        try:
            event = self.pump_until("connected", timeout=15)
            self.assertIsNotNone(event, "the tablet's call was kept out")
            self.assertEqual(self.session.link.sock.getpeername()[0], "::ffff:127.0.0.1")
        finally:
            stop.set()
            attacker.join(timeout=5)
            for sock in strangers:
                sock.close()


class LocalNetworks(unittest.TestCase):
    nets = [ipaddress.ip_network(n) for n in
            ("192.168.1.0/24", "2001:db8:1:2::/64", "10.64.0.2/32", "2001:db8:1:2::77/128")]

    def test_home_addresses(self):
        for peer in ("192.168.1.42", "::ffff:192.168.1.42", "2001:db8:1:2::abcd",
                     "2001:db8:1:2::abcd%wlan0"):
            self.assertTrue(link.is_local(peer, self.nets), peer)

    def test_outside_addresses(self):
        for peer in ("192.168.2.42", "203.0.113.5", "::ffff:203.0.113.5", "2001:db8:9::1",
                     "10.64.0.2", "::1", "junk", ""):
            self.assertFalse(link.is_local(peer, self.nets), peer)

    def test_real_interfaces_are_read(self):
        nets = link.local_networks()
        self.assertIsInstance(nets, list)
        for net in nets:
            self.assertIsInstance(net, (ipaddress.IPv4Network, ipaddress.IPv6Network))


class SilentCaller(unittest.TestCase):
    """A caller that never answers (a scanner) must not hold up hostd: the
    handshake runs in a thread; before, pump() blocked for up to 3 s."""

    def test_pump_does_not_wait_for_a_silent_caller(self):
        port = free_port()
        session = link.Session(port=free_port(), use_adb=False, psk=os.urandom(32),
                               listen_port=port)
        callers = []
        try:
            slowest = 0.0
            deadline = time.monotonic() + 4.5
            while time.monotonic() < deadline:
                if len(callers) < 3:
                    callers.append(socket.create_connection(("::1", port), timeout=2))
                start = time.monotonic()
                session.pump(0.0)
                slowest = max(slowest, time.monotonic() - start)
            self.assertLess(slowest, 0.5, f"pump blocked for {slowest:.2f} s")
            self.assertFalse(session.connected)
        finally:
            start = time.monotonic()
            session.close()
            closing = time.monotonic() - start
            for sock in callers:
                sock.close()
        self.assertLess(closing, 1.0, f"close took {closing:.2f} s")

    def test_close_during_a_handshake_ends_the_thread(self):
        port = free_port()
        session = link.Session(port=free_port(), use_adb=False, psk=os.urandom(32),
                               listen_port=port)
        caller = socket.create_connection(("127.0.0.1", port), timeout=2)
        try:
            deadline = time.monotonic() + 2
            while not session._incoming and time.monotonic() < deadline:
                session.pump(0.0)
                time.sleep(0.01)
            self.assertTrue(session._incoming, "the caller was not taken")
            thread = session._incoming[0][0]
            session.close()
            self.assertFalse(thread.is_alive(), "handshake thread still running")
        finally:
            caller.close()


class SeveralAddresses(Calling):
    """The laptop's address from the router can change: the tablet keeps
    several and moves on to the next after a failed call."""
    target = ("127.0.0.1:{closed}", "[::1]:{port}")

    def setUp(self):
        self.closed = free_port()                   # nobody listens there
        super().setUp()

    def test_second_address_after_the_first_fails(self):
        event = self.pump_until("connected", timeout=15)
        self.assertIsNotNone(event, "the second address was not tried")
        self.assertEqual(self.session.link.sock.getpeername()[0], "::1")
        log = (pathlib.Path(self.tmp.name) / "stderr.txt").read_text()
        # the failed first address is named, with the reason, then the second one is used
        self.assertIn(f"call to 127.0.0.1:{self.closed} failed: connect: Connection refused", log)


class BadTarget(unittest.TestCase):
    def test_malformed_addresses_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            key = pathlib.Path(d) / "key"
            key.write_bytes(os.urandom(32))
            key.chmod(0o600)
            for target in ("[::1:7110", "::1:7110", "[::1]", "[zz::1]:7110", "1.2.3:7110",
                           "1.2.3.4:0", "[::1]:70000", "[::1]:7110x", "1.2.3.4:71 10",
                           "1.2.3.4:", "[]:7110"):
                done = subprocess.run([str(TABLETD), "--fb", str(pathlib.Path(d) / "fb"),
                                       "--geom", f"{W}x{H}", "--listen", "abstract:x-bad-target",
                                       "--secure-connect", target, "--key", str(key)],
                                      capture_output=True, timeout=5)
                self.assertEqual(done.returncode, 2, target)


class WrongKey(Calling):
    tablet_key_differs = True

    def test_refused_and_never_connected(self):
        self.assertIsNone(self.pump_until("connected", timeout=8))


class Stranger(Calling):
    """Someone from the Internet calls the laptop's port without the key."""

    def test_stranger_is_dropped_and_the_tablet_still_gets_in(self):
        stranger = socket.create_connection(("127.0.0.1", self.port))
        stranger.sendall(os.urandom(36))
        event = self.pump_until("connected", timeout=15)
        stranger.close()
        self.assertIsNotNone(event, "the tablet did not get in after the stranger")


if __name__ == "__main__":
    unittest.main()


class TooManyAddresses(unittest.TestCase):
    def test_at_most_four(self):
        with tempfile.TemporaryDirectory() as d:
            key = pathlib.Path(d) / "key"
            key.write_bytes(os.urandom(32))
            key.chmod(0o600)
            args = []
            for port in range(7111, 7116):
                args += ["--secure-connect", f"127.0.0.1:{port}"]
            done = subprocess.run([str(TABLETD), "--fb", str(pathlib.Path(d) / "fb"), "--geom",
                                   f"{W}x{H}", "--listen", "abstract:x-too-many", *args,
                                   "--key", str(key)], capture_output=True, timeout=5)
            self.assertEqual(done.returncode, 2)
