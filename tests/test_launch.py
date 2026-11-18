"""tabletd-launch.sh (phase 5b): every path of the boot decision.

The script runs here with the laptop's sh and with stand-ins for the tablet's
commands (setprop, stop, start, tb-keyheld, tb-rootd, tb-home). Each stand-in
appends a line to calls.log, so the tests see what was called, in which order
and in which state the mode file was at that moment.
"""
import os
import pathlib
import stat
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "tablet" / "tabletd-launch.sh"

STUBS = {
    "setprop": 'echo "setprop $*" >> "$T/calls.log"; [ "$1" = sys.boot_completed ] && echo "$2" > "$T/boot_completed"; exit 0',
    "getprop": 'if [ "$1" = sys.boot_completed ]; then cat "$T/boot_completed" 2>/dev/null || echo 1; else echo "mtp,adb"; fi',
    "stop": 'echo "stop mode=$(cat "$TB_DIR/mode" 2>/dev/null)" >> "$T/calls.log"',
    "start": 'echo "start" >> "$T/calls.log"; [ -f "$T/android-comes-back" ] && echo 1 > "$T/boot_completed"; exit 0',
    "reboot": 'echo "reboot $*" >> "$T/calls.log"',
    "sync": 'echo "sync mode=$(cat "$TB_DIR/mode" 2>/dev/null)" >> "$T/calls.log"',
    "sleep": 'echo "sleep $*" >> "$T/calls.log"',   # the launcher must not slow the tests
    "tb-rootd": 'echo "tb-rootd" >> "$T/calls.log"',
    # ping prints the resolved address in its first line, as Android's does
    "ping": 'for a; do host=$a; done; case " $* " in *" -n "*) ;; *) echo "PING $host (rev.example.net) 56(84) bytes"; exit 0 ;; esac; [ "$host" = laptop.example.net ] && echo "PING $host (203.0.113.7) 56(84) bytes of data."; [ "$host" = 192.0.2.5 ] && echo "PING 192.0.2.5 (192.0.2.5) 56(84) bytes"; [ "$host" = both.example.net ] && echo "PING $host (203.0.113.9) 56(84) bytes"; exit 0',
    "ping6": 'for a; do host=$a; done; case " $* " in *" -n "*) ;; *) echo "PING $host(rev.example.net) 56 data bytes"; exit 0 ;; esac; [ "$host" = home6.example.net ] && echo "PING $host(2001:db8::80) 56 data bytes"; [ "$host" = both.example.net ] && echo "PING $host(2001:db8::9) 56 data bytes"; [ "$host" = 2001:db8::5 ] && echo "PING 2001:db8::5(2001:db8::5) 56 data bytes"; [ "$host" = junk.example.net ] && echo "PING junk.example.net(2001:db8::;rm) 56 data bytes"; exit 0',
    "iptables": 'echo "iptables $*" >> "$T/calls.log"; case "$1" in -D) exit 1 ;; esac',
    "tb-keyheld": 'echo "tb-keyheld $*" >> "$T/calls.log"; exit "$(cat "$T/keyheld.rc")"',
    # home.plan: one line per start, "SECONDS EXITCODE"; when it is used up,
    # tb-home keeps running until the test ends
    "tb-home": '''echo "tb-home $*" >> "$T/calls.log"
line=$(head -n 1 "$T/home.plan" 2>/dev/null)
if [ -z "$line" ]; then exec /bin/sleep 600; fi
sed -i 1d "$T/home.plan"
set -- $line
/bin/sleep "$1"
exit "$2"''',
}


class LaunchCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = pathlib.Path(self.tmp.name)
        self.data = self.t / "data"
        self.data.mkdir()
        (self.data / "key").write_bytes(b"k" * 32)      # paired (huawai pair)
        bindir = self.t / "bin"
        bindir.mkdir()
        for name, body in STUBS.items():
            path = bindir / name
            path.write_text("#!/bin/sh\n" + body + "\n")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        (self.t / "keyheld.rc").write_text("1\n")        # no key held
        (self.t / "calls.log").write_text("")
        self.env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", T=str(self.t),
                        TB_DIR=str(self.data), TB_SETTLE="0", TB_BOOT_WAIT="2")
        self.proc = None

    def tearDown(self):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, 9)
            self.proc.wait()
        self.tmp.cleanup()

    def launch(self, wait=True, timeout=10):
        self.proc = subprocess.Popen(["sh", str(LAUNCHER)], env=self.env,
                                     start_new_session=True, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        if wait:
            self.proc.wait(timeout=timeout)
            return self.proc.returncode
        return None

    def calls(self):
        return (self.t / "calls.log").read_text().splitlines()

    def wait_for_call(self, prefix, count=1, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if sum(line.startswith(prefix) for line in self.calls()) >= count:
                return
            time.sleep(0.02)
        self.fail(f"'{prefix}' not called {count} times: {self.calls()}")

    def log(self):
        return (self.data / "boot.log").read_text()

    def set_mode(self, mode):
        (self.data / "mode").write_text(mode + "\n")

    def assert_android(self):
        self.assertIn("decision: android", self.log())
        for line in self.calls():
            self.assertFalse(line.startswith(("stop", "setprop", "tb-home", "tb-rootd",
                                              "iptables")), line)


class AndroidPaths(LaunchCase):
    def test_no_mode_file(self):
        self.assertEqual(self.launch(), 0)
        self.assert_android()

    def test_mode_android(self):
        self.set_mode("android")
        self.assertEqual(self.launch(), 0)
        self.assert_android()

    def test_unknown_mode(self):
        self.set_mode("rm -rf /")
        self.assertEqual(self.launch(), 0)
        self.assert_android()
        self.assertIn("unknown mode", self.log())

    def test_empty_mode_file(self):
        (self.data / "mode").write_text("")
        self.assertEqual(self.launch(), 0)
        self.assert_android()

    def test_volume_key_held(self):
        self.set_mode("mini")
        (self.t / "keyheld.rc").write_text("0\n")
        self.assertEqual(self.launch(), 0)
        self.assert_android()
        self.assertIn("volume key held", self.log())
        self.assertFalse((self.data / "unconfirmed").exists())
        self.assertEqual(self.calls(), ["tb-keyheld /dev/input/event0 114 115"])

    def test_key_state_unknown_plays_safe(self):
        self.set_mode("mini")
        (self.t / "keyheld.rc").write_text("2\n")
        self.assertEqual(self.launch(), 0)
        self.assert_android()

    def test_fourth_unconfirmed_boot(self):
        self.set_mode("mini")
        (self.data / "unconfirmed").write_text("3\n")
        self.assertEqual(self.launch(), 0)
        self.assert_android()
        self.assertEqual((self.data / "unconfirmed").read_text().strip(), "4")


class MiniPaths(LaunchCase):
    def test_mini_starts_everything_in_order(self):
        self.set_mode("mini")
        self.launch(wait=False)
        self.wait_for_call("tb-home")
        self.wait_for_call("tb-rootd")
        names = [line.split()[0] for line in self.calls()]
        # the firewall (several iptables calls) between key check and stop
        collapsed = [n for i, n in enumerate(names)
                     if n != "iptables" or (i > 0 and names[i - 1] != "iptables")]
        self.assertEqual(collapsed[:5], ["tb-keyheld", "iptables", "setprop", "stop", "sleep"])
        # tb-rootd runs in the background, tb-home right after it: which of the
        # two reaches the log first is chance (seen under load); both come
        # only after Android was stopped
        self.assertEqual(sorted(collapsed[5:]), ["tb-home", "tb-rootd"])
        self.assertIn("setprop sys.usb.config adb", self.calls())
        home = [line for line in self.calls() if line.startswith("tb-home")][0]
        own, _, remote = home.partition(" -- ")
        self.assertIn("--fb /dev/graphics/fb0", own)
        self.assertIn(f"--confirm {self.data}/unconfirmed", own)
        self.assertIn(f"--tz-file {self.data}/tz", own)
        # /system cannot be written on this tablet (P5.4): programs live in /data/tabletd/bin
        self.assertTrue(remote.startswith(f"{self.data}/bin/tabletd "), remote)
        self.assertIn("--power-key ignore", remote)
        self.assertIn("--secure-listen 7100", remote)
        self.assertIn("--drop 2900:2900", remote)
        self.assertIn(f"--confirm {self.data}/unconfirmed", remote)
        self.assertEqual((self.data / "unconfirmed").read_text().strip(), "1")
        self.assertIn("decision: mini", self.log())
        self.assertEqual((self.data / "mode").read_text().strip(), "mini")

    def test_third_unconfirmed_boot_is_still_mini(self):
        self.set_mode("mini")
        (self.data / "unconfirmed").write_text("2\n")
        self.launch(wait=False)
        self.wait_for_call("tb-home")
        self.assertEqual((self.data / "unconfirmed").read_text().strip(), "3")

    def test_garbage_in_counter_counts_as_zero(self):
        self.set_mode("mini")
        (self.data / "unconfirmed").write_text("9; reboot\n")
        self.launch(wait=False)
        self.wait_for_call("tb-home")
        self.assertEqual((self.data / "unconfirmed").read_text().strip(), "1")

    def test_mini_once_resets_the_mode_before_android_is_stopped(self):
        self.set_mode("mini-once")
        self.launch(wait=False)
        self.wait_for_call("tb-home")
        self.assertEqual((self.data / "mode").read_text().strip(), "android")
        self.assertIn("sync mode=android", self.calls())
        self.assertIn("stop mode=android", self.calls())
        self.assertLess(self.calls().index("sync mode=android"),
                        self.calls().index("stop mode=android"))

    def test_three_quick_ends_start_android(self):
        self.set_mode("mini")
        (self.t / "home.plan").write_text("0 1\n0 1\n0 139\n")
        self.assertEqual(self.launch(), 1)
        calls = self.calls()
        self.assertEqual(sum(line.startswith("tb-home") for line in calls), 3)
        self.assertEqual(calls[-2:], ["setprop sys.usb.config mtp,adb", "start"])
        self.assertIn("ended with 139", self.log())
        self.assertIn("starting android", self.log())

    def test_exit_code_42_switches_the_tablet_off(self):
        self.set_mode("mini")
        (self.t / "home.plan").write_text("0 42\n")
        self.assertEqual(self.launch(), 0)
        self.assertEqual([line.split()[0] for line in self.calls()][-2:], ["sync", "reboot"])
        self.assertIn("reboot -p", self.calls())
        self.assertNotIn("start", self.calls())
        self.assertIn("switching off", self.log())

    def test_two_quick_ends_then_running_stays_mini(self):
        self.set_mode("mini")
        (self.t / "home.plan").write_text("0 1\n0 1\n")
        self.launch(wait=False)
        self.wait_for_call("tb-home", count=3)
        time.sleep(0.3)
        self.assertNotIn("start", self.calls())
        self.assertIsNone(self.proc.poll())


class FirewallAndLinux(LaunchCase):
    def test_firewall_comes_before_anything_listens(self):
        (self.data / "laptops").write_text("192.168.1.79\n192.168.1.128\n")
        self.set_mode("mini")
        self.launch(wait=False)
        self.wait_for_call("tb-home")
        calls = self.calls()
        accepts = [c for c in calls if c.startswith("iptables -A tabletd_in -s")]
        self.assertEqual(accepts, ["iptables -A tabletd_in -s 192.168.1.79 -j ACCEPT",
                                   "iptables -A tabletd_in -s 192.168.1.128 -j ACCEPT"])
        last_firewall = max(i for i, c in enumerate(calls) if c.startswith("iptables"))
        self.assertLess(last_firewall, calls.index(next(c for c in calls if c.startswith("stop"))))
        self.assertIn("iptables -A tabletd_in -j DROP", calls)
        for port in ("7100", "5555"):
            self.assertIn(f"iptables -I INPUT 1 -p tcp --dport {port} -j tabletd_in", calls)

    def test_without_laptop_list_nobody_gets_in(self):
        self.set_mode("mini")
        self.launch(wait=False)
        self.wait_for_call("tb-home")
        calls = self.calls()
        self.assertFalse([c for c in calls if " -j ACCEPT" in c])
        self.assertIn("iptables -A tabletd_in -j DROP", calls)
        self.assertIn("0 laptop address(es)", self.log())

    def test_junk_in_the_laptop_list_is_skipped(self):
        (self.data / "laptops").write_text("192.168.1.79\n; rm -rf /\n0.0.0.0/0\n\n")
        self.set_mode("mini")
        self.launch(wait=False)
        self.wait_for_call("tb-home")
        accepts = [c for c in self.calls() if " -j ACCEPT" in c]
        self.assertEqual(accepts, ["iptables -A tabletd_in -s 192.168.1.79 -j ACCEPT"])

    def test_linux_tile_only_when_linux_is_installed(self):
        self.set_mode("mini")
        self.launch(wait=False)
        self.wait_for_call("tb-home")
        self.assertNotIn("--linux", next(c for c in self.calls() if c.startswith("tb-home")))

    def test_linux_started_and_tile_offered(self):
        linux = self.data / "linux"
        linux.mkdir()
        (linux / "devuan.img").write_text("")
        (linux / "linux-root.sh").write_text(f'echo "linux-root $*" >> "{self.t}/calls.log"\n')
        self.set_mode("mini")
        self.launch(wait=False)
        self.wait_for_call("tb-home")
        calls = self.calls()
        self.assertIn("linux-root daemon", calls)
        self.assertLess(calls.index("linux-root daemon"),
                        calls.index(next(c for c in calls if c.startswith("stop"))))
        home = next(c for c in calls if c.startswith("tb-home"))
        self.assertIn(f"--linux {self.data}/bin/tb-linux", home)


class OncePerBoot(LaunchCase):
    def test_second_start_while_the_first_runs_does_nothing(self):
        self.set_mode("mini")
        self.launch(wait=False)
        self.wait_for_call("tb-home")
        first = self.proc
        second = subprocess.run(["sh", str(LAUNCHER)], env=self.env, timeout=10,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(second.returncode, 0)
        self.assertIn("already running", self.log())
        self.assertEqual(sum(c.startswith("stop") for c in self.calls()), 1)
        self.assertIsNone(first.poll())

    def test_stale_pid_file_does_not_block(self):
        (self.data / "launch.pid").write_text("999999\n")
        self.set_mode("mini")
        self.launch(wait=False)
        self.wait_for_call("tb-home")


class IconAndAndroidTile(LaunchCase):
    def launch_now(self):
        self.proc = subprocess.Popen(["sh", str(LAUNCHER), "now"], env=self.env,
                                     start_new_session=True, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)

    def test_icon_starts_at_once_whatever_the_mode(self):
        self.set_mode("android")
        (self.data / "unconfirmed").write_text("9\n")      # would mean android otherwise
        (self.t / "keyheld.rc").write_text("0\n")          # a key held: ignored here
        self.launch_now()
        self.wait_for_call("tb-home")
        self.assertIn("started from the Tablet System icon", self.log())
        self.assertFalse(any(c.startswith("tb-keyheld") for c in self.calls()))
        self.assertEqual((self.data / "mode").read_text().strip(), "android")

    def test_android_tile_starts_android_again_without_a_restart(self):
        (self.t / "android-comes-back").write_text("")
        (self.t / "home.plan").write_text("0 43\n")
        self.set_mode("mini")
        self.launch(wait=False)
        self.proc.wait(timeout=10)
        calls = self.calls()
        self.assertIn("setprop sys.boot_completed 0", calls)
        self.assertIn("start", calls)
        self.assertLess(calls.index("setprop sys.boot_completed 0"), calls.index("start"))
        self.assertFalse(any(c.startswith("reboot") for c in calls))
        self.assertIn("android is back", self.log())
        # the flag stays for the boot receiver's call, which uses it up and
        # stays in Android; mini mode itself is kept
        self.assertTrue((self.data / "android-once").exists())
        self.assertEqual((self.data / "mode").read_text().strip(), "mini")
        (self.t / "calls.log").write_text("")
        self.assertEqual(self.launch(), 0)
        self.assert_android()
        self.assertFalse((self.data / "android-once").exists())

    def test_icon_works_right_after_coming_back(self):
        """No guard is held after Android is back: the icon starts at once."""
        (self.t / "android-comes-back").write_text("")
        (self.t / "home.plan").write_text("0 43\n")
        self.set_mode("android")
        self.launch_now()
        self.proc.wait(timeout=10)
        self.assertIn("android is back", self.log())
        (self.t / "calls.log").write_text("")
        (self.t / "home.plan").write_text("")
        self.launch_now()
        self.wait_for_call("tb-home")
        self.assertNotIn("already running", self.log().split("android is back")[-1])

    def test_android_tile_restarts_when_android_does_not_come_back(self):
        (self.t / "home.plan").write_text("0 43\n")
        self.set_mode("mini")
        self.launch(wait=False)
        self.wait_for_call("reboot")
        self.assertIn("start", self.calls())
        self.assertIn("did not come back", self.log())
        self.assertTrue((self.data / "android-once").exists())
        self.proc.wait(timeout=5)
        # next boot: android, the flag is used up
        (self.t / "calls.log").write_text("")
        self.assertEqual(self.launch(), 0)
        self.assert_android()
        self.assertFalse((self.data / "android-once").exists())
        self.assertIn("android-once", self.log())


class WithoutKey(LaunchCase):
    """Not paired yet: tabletd would refuse to start with --key and no key
    file, so the launcher leaves the Wi-Fi port and the call out."""

    def test_no_key_means_cable_only(self):
        (self.data / "key").unlink()
        (self.data / "internet").write_text("laptop.example.net 7110\n")
        self.set_mode("mini")
        self.launch(wait=False)
        self.wait_for_call("tb-home")
        home = next(c for c in self.calls() if c.startswith("tb-home"))
        self.assertNotIn("--secure-listen", home)
        self.assertNotIn(" --key ", home)
        self.assertNotIn("--secure-connect", home)
        self.assertIn("--drop 2900:2900", home)
        self.assertIn("no pairing key", self.log())


class Internet(LaunchCase):
    def home_call(self):
        self.set_mode("mini")
        self.launch(wait=False)
        self.wait_for_call("tb-home")
        return next(c for c in self.calls() if c.startswith("tb-home"))

    def test_name_is_resolved_and_passed_on(self):
        (self.data / "internet").write_text("laptop.example.net 7110\n")
        self.assertIn("--secure-connect 203.0.113.7:7110", self.home_call())

    def test_numeric_address_works_too(self):
        (self.data / "internet").write_text("192.0.2.5 7110\n")
        self.assertIn("--secure-connect 192.0.2.5:7110", self.home_call())

    def test_unknown_name_means_no_call(self):
        (self.data / "internet").write_text("nowhere.invalid 7110\n")
        self.assertNotIn("--secure-connect", self.home_call())
        self.assertIn("not resolved", self.log())

    def test_ipv6_name_is_preferred(self):
        (self.data / "internet").write_text("home6.example.net 7110\n")
        self.assertIn("--secure-connect [2001:db8::80]:7110", self.home_call())

    def test_ipv6_literal(self):
        (self.data / "internet").write_text("2001:db8::5 7110\n")
        self.assertIn("--secure-connect [2001:db8::5]:7110", self.home_call())

    def test_several_addresses_in_order(self):
        """The router may give the laptop a new address: several are kept,
        tabletd tries them in turn."""
        (self.data / "internet").write_text("2001:db8::5 home6.example.net 192.0.2.5 7110\n")
        call = self.home_call()
        self.assertIn("--secure-connect [2001:db8::5]:7110 --secure-connect [2001:db8::80]:7110 "
                      "--secure-connect 192.0.2.5:7110", call)

    def test_unresolved_entries_are_left_out(self):
        (self.data / "internet").write_text("nonexistent.example 2001:db8::5 7110\n")
        call = self.home_call()
        self.assertEqual(call.count("--secure-connect"), 1)
        self.assertIn("--secure-connect [2001:db8::5]:7110", call)
        self.assertIn("'nonexistent.example' not resolved", self.log())

    def test_at_most_four_addresses(self):
        (self.data / "internet").write_text(
            "2001:db8::1 2001:db8::2 2001:db8::3 2001:db8::4 2001:db8::5 7110\n")
        call = self.home_call()
        self.assertEqual(call.count("--secure-connect"), 4)
        self.assertNotIn("2001:db8::5", call)
        self.assertIn("more than 4 addresses", self.log())

    def test_port_only_means_no_call(self):
        (self.data / "internet").write_text("7110\n")
        self.assertNotIn("--secure-connect", self.home_call())

    def test_ipv6_wins_when_both_exist(self):
        (self.data / "internet").write_text("both.example.net 7110\n")
        self.assertIn("--secure-connect [2001:db8::9]:7110", self.home_call())

    def test_junk_from_ping6_is_not_used(self):
        (self.data / "internet").write_text("junk.example.net 7110\n")
        self.assertNotIn("--secure-connect", self.home_call())

    def test_bad_port_means_no_call(self):
        (self.data / "internet").write_text("home6.example.net 71a0\n")
        self.assertNotIn("--secure-connect", self.home_call())

    def test_without_the_file_no_call(self):
        self.assertNotIn("--secure-connect", self.home_call())


class AfterPhase5d(LaunchCase):
    """With the no-android flag there is nothing to fall back to."""

    def setUp(self):
        super().setUp()
        (self.data / "no-android").write_text("")
        self.set_mode("mini")

    def test_key_and_counter_are_ignored(self):
        (self.t / "keyheld.rc").write_text("0\n")
        (self.data / "unconfirmed").write_text("99\n")
        self.launch(wait=False)
        self.wait_for_call("tb-home")
        self.assertNotIn("tb-keyheld /dev/input/event0 114 115", self.calls())
        self.assertEqual((self.data / "unconfirmed").read_text().strip(), "99")

    def test_crashing_tabletd_never_starts_android(self):
        (self.t / "home.plan").write_text("0 1\n" * 7)
        self.launch(wait=False)
        self.wait_for_call("tb-home", count=8)
        self.assertNotIn("start", self.calls())
        self.assertIn("sleep 30", self.calls())
        self.assertIn("no android to start", self.log())


if __name__ == "__main__":
    unittest.main()
