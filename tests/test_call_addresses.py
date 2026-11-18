"""hostd's side of MSG_ADDRESSES: which addresses it sends (host/addresses.py),
when it sends them, the settings internet_call_port and internet_interface,
and the way through the encrypted link into a real tabletd's learn file."""
import ipaddress
import os
import pathlib
import struct
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import addresses, config, link, main  # noqa: E402
from test_internet import Calling  # noqa: E402

ROUTES = """default via fe80::1 dev wlan0 proto ra metric 600 pref medium
default via fe80::1 dev eth0 proto ra metric 100 pref medium
"""
ADDRS = """\
2: eth0    inet6 2001:db8:a::50/128 scope global dynamic noprefixroute \\       valid_lft 3600sec
2: eth0    inet6 2001:db8:a:0:1234:5678:9abc:def0/64 scope global dynamic mngtmpaddr \\       valid_lft 86400sec
2: eth0    inet6 2001:db8:a:0:1111:2222:3333:4444/64 scope global temporary dynamic \\       valid_lft 86400sec
2: eth0    inet6 2001:db8:a:0:5555::1/64 scope global deprecated dynamic \\       valid_lft 600sec
2: eth0    inet6 2001:db8:a:0:6666::1/64 scope global tentative \\       valid_lft forever
2: eth0    inet6 fd12:3456::9/64 scope global dynamic \\       valid_lft forever
3: wlan0    inet6 2001:db8:a:0:aaaa::1/64 scope global dynamic \\       valid_lft 86400sec
"""


class Selection(unittest.TestCase):
    def test_preferred_interface_has_the_lowest_metric(self):
        self.assertEqual(addresses.preferred_interface(ROUTES), "eth0")
        self.assertEqual(addresses.preferred_interface(
            "default via fe80::1 dev eth1 proto ra\ndefault via fe80::1 dev eth0 metric 50\n"),
            "eth0")                                  # no metric counts as 1024
        self.assertIsNone(addresses.preferred_interface(""))

    def test_only_usable_global_addresses(self):
        self.assertEqual(addresses.global_addresses(ADDRS, "eth0"),
                         ["2001:db8:a::50", "2001:db8:a:0:1234:5678:9abc:def0"])
        self.assertEqual(addresses.global_addresses(ADDRS, "wlan0"), ["2001:db8:a:0:aaaa::1"])
        self.assertEqual(addresses.global_addresses(ADDRS, "eth9"), [])

    def test_at_most_four(self):
        many = "".join(f"2: eth0    inet6 2001:db8::{i}/64 scope global dynamic \\\n"
                       for i in range(1, 7))
        self.assertEqual(len(addresses.global_addresses(many, "eth0")), 4)

    def test_interface_setting_wins_over_the_routes(self):
        calls = []

        def ip(*args):
            calls.append(args)
            return ROUTES if "route" in args else ADDRS
        self.assertEqual(addresses.call_addresses("wlan0", ip), ["2001:db8:a:0:aaaa::1"])
        self.assertFalse(any("route" in c for c in calls), "routes asked although the card was set")
        self.assertEqual(len(addresses.call_addresses("", ip)), 2)
        self.assertEqual(addresses.call_addresses("", lambda *a: ""), [])

    def test_payload(self):
        data = addresses.payload(443, ["2001:db8::5", "2001:db8::6"])
        self.assertEqual(data[:3], struct.pack("<HB", 443, 2))
        self.assertEqual(data[3:19], ipaddress.IPv6Address("2001:db8::5").packed)
        self.assertEqual(len(data), 3 + 32)
        for port, addrs in ((0, ["2001:db8::5"]), (443, []), (443, ["2001:db8::5"] * 5)):
            with self.subTest(port=port, n=len(addrs)), self.assertRaises(ValueError):
                addresses.payload(port, addrs)


class FakeLink:
    def __init__(self, flags):
        self.flags = flags


class FakeSession:
    def __init__(self, flags):
        self.link = FakeLink(flags)
        self.sent = []

    def send_message(self, msg_type, payload=b""):
        self.sent.append((msg_type, payload))


class Sending(unittest.TestCase):
    def hostd(self, flags, internet_port=7110, call_port=443):
        hostd = object.__new__(main.Hostd)
        hostd.session = FakeSession(flags)
        hostd.internet_port = internet_port
        hostd.call_port = call_port or internet_port
        hostd.internet_interface = "eth0"
        return hostd

    def setUp(self):
        self._saved = addresses.call_addresses
        self.asked = []
        addresses.call_addresses = lambda interface="": (self.asked.append(interface)
                                                         or ["2001:db8::5"])

    def tearDown(self):
        addresses.call_addresses = self._saved

    def test_sent_when_calls_are_on_and_the_tablet_keeps_them(self):
        hostd = self.hostd(link.INFO_ADDRESSES)
        hostd.send_call_addresses()
        self.assertEqual(hostd.session.sent,
                         [(link.ADDRESSES, addresses.payload(443, ["2001:db8::5"]))])
        self.assertEqual(self.asked, ["eth0"])

    def test_not_sent_to_an_older_tabletd(self):
        hostd = self.hostd(link.INFO_HOME)
        hostd.send_call_addresses()
        self.assertEqual(hostd.session.sent, [])

    def test_not_sent_when_calls_are_off(self):
        hostd = self.hostd(link.INFO_ADDRESSES, internet_port=0, call_port=0)
        hostd.send_call_addresses()
        self.assertEqual(hostd.session.sent, [])

    def test_nothing_sent_without_an_address(self):
        addresses.call_addresses = lambda interface="": []
        hostd = self.hostd(link.INFO_ADDRESSES)
        hostd.send_call_addresses()
        self.assertEqual(hostd.session.sent, [])


class Settings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = pathlib.Path(self.tmp.name) / "hostd.toml"

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults(self):
        values = config.load(self.file)
        self.assertEqual((values["internet_call_port"], values["internet_interface"]), (0, ""))

    def test_good_values(self):
        for port in ("0", "443", "1024", "65535"):
            with self.subTest(port=port):
                config.update(self.file, internet_call_port=port, internet_interface="eth0")
                self.assertEqual(config.load(self.file)["internet_call_port"], int(port))
        self.assertIn('internet_interface = "eth0"', self.file.read_text())

    def test_bad_values(self):
        for change in ({"internet_call_port": "80"}, {"internet_call_port": "70000"},
                       {"internet_call_port": "x"}, {"internet_interface": "eth0; rm -rf"},
                       {"internet_interface": "a" * 16}):
            with self.subTest(change=change), self.assertRaises(config.ConfigError):
                config.update(self.file, **change)


class ThroughTheEncryptedLink(Calling):
    """hostd sends ADDRESSES over the encrypted call; tabletd keeps them."""

    def connect_args(self):
        self.learned = pathlib.Path(self.tmp.name) / "internet.learned"
        return super().connect_args() + ["--learn-file", str(self.learned)]

    def test_kept_by_the_tablet(self):
        self.assertIsNotNone(self.pump_until("connected"))
        self.assertTrue(self.session.link.flags & link.INFO_ADDRESSES)
        self.session.send_message(link.ADDRESSES,
                                  addresses.payload(443, ["2001:db8::5", "2001:db8::6"]))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.session.pump(0.05)
            if self.learned.exists() and self.learned.read_text():
                break
        self.assertEqual(self.learned.read_text(), "2001:db8::5 2001:db8::6 443\n")
        self.assertTrue(self.session.connected, "the session ended")


if __name__ == "__main__":
    unittest.main()
