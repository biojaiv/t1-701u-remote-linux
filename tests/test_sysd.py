"""tb-sysd: USB installation medium (phase 7) and display brightness.

On the laptop, setprop/getprop are small scripts that keep the properties in
files and behave like Huawei's init when sys.usb.config changes (empty the
drive, reset ro and cdrom, set sys.usb.state); the drive's sysfs directory
is a plain directory. Checked: order of the steps, which files are accepted,
who may ask, and that the kernel gets the daemon's own open file.
"""
import os
import pathlib
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SYSD = ROOT / "build" / "x86" / "tb-sysd"

SETPROP = """#!/bin/sh
echo "$2" > "$PROPS/$1"
echo "setprop $1 $2" >> "$PROPS/log"
if [ "$1" = sys.usb.config ]; then      # like init.sc8830.usb.rc
    printf '' > "$LUN/file"; echo 0 > "$LUN/cdrom"; echo 0 > "$LUN/ro"
    echo "$2" > "$PROPS/sys.usb.state"
fi
"""
GETPROP = """#!/bin/sh
cat "$PROPS/$1" 2>/dev/null || echo
"""


class SysdCase(unittest.TestCase):
    allow = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.tmp.name)
        self.props, self.lun, self.images = d / "props", d / "lun", d / "iso"
        for p in (self.props, self.lun, self.images):
            p.mkdir()
        (self.props / "persist.sys.usb.config").write_text("adb\n")
        (self.props / "sys.usb.state").write_text("adb\n")
        for attr in ("file", "ro", "cdrom"):
            (self.lun / attr).write_text("")
        for name, body in (("setprop", SETPROP), ("getprop", GETPROP)):
            (d / name).write_text(body)
            (d / name).chmod(0o755)
        self.backlight = d / "brightness"
        self.backlight.write_text("200\n")
        self.image = self.images / "debian-13-netinst.iso"
        self.image.write_bytes(b"\x00" * 4096)
        self.name = f"tb-sysd-test-{os.getpid()}-{id(self)}"
        env = dict(os.environ, PROPS=str(self.props), LUN=str(self.lun))
        self.daemon = subprocess.Popen(
            [str(SYSD), "--name", self.name, "--allow",
             str(os.getuid() if self.allow is None else self.allow),
             "--owner", str(os.getuid()), "--dir", str(self.images), "--lun", str(self.lun),
             "--backlight", str(self.backlight),
             "--setprop", str(d / "setprop"), "--getprop", str(d / "getprop")],
            env=env, stderr=subprocess.PIPE)
        time.sleep(0.2)

    def stop(self):
        """Ends the daemon and returns its log (stderr)."""
        self.alive = self.daemon.poll() is None
        self.daemon.kill()
        _, self.err = self.daemon.communicate()
        return self.err

    def tearDown(self):
        if not hasattr(self, "err"):
            self.stop()
        self.tmp.cleanup()
        self.assertNotIn(b"Sanitizer", self.err)
        self.assertTrue(self.alive, "tb-sysd died during the test")

    def ask(self, *words):
        done = subprocess.run([str(SYSD), "ask", *words], capture_output=True, text=True,
                              timeout=30, env=dict(os.environ, TB_SYSD_NAME=self.name))
        return done.returncode, done.stdout.strip()

    def lun_value(self, attr):
        return (self.lun / attr).read_text().strip()


class On(SysdCase):
    def test_disk_mode_switches_first_then_sets_the_drive(self):
        code, reply = self.ask("on", self.image.name, "disk")
        self.assertEqual((code, reply), (0, f"ok on {self.image.name} disk"))
        self.assertEqual((self.props / "sys.usb.state").read_text().strip(), "mass_storage,adb")
        # set after init's reset, so they survived it
        self.assertEqual(self.lun_value("ro"), "1")
        self.assertEqual(self.lun_value("cdrom"), "0")
        fdpath = self.lun_value("file")
        self.assertRegex(fdpath, r"^/proc/self/fd/\d+$")
        fd = fdpath.rsplit("/", 1)[1]
        self.assertEqual(os.readlink(f"/proc/{self.daemon.pid}/fd/{fd}"), str(self.image))

    def test_cd_mode(self):
        self.assertEqual(self.ask("on", self.image.name, "cd")[0], 0)
        self.assertEqual(self.lun_value("cdrom"), "1")

    def test_status(self):
        self.assertEqual(self.ask("status"), (0, "off"))
        self.ask("on", self.image.name, "disk")
        self.assertEqual(self.ask("status"), (0, f"on {self.image.name} disk"))

    def test_off_ejects_and_returns_to_the_normal_usb_mode(self):
        self.ask("on", self.image.name, "disk")
        self.assertEqual(self.ask("off"), (0, "ok off"))
        self.assertEqual(self.lun_value("file"), "")
        self.assertEqual((self.props / "sys.usb.state").read_text().strip(), "adb")
        self.assertEqual(self.ask("status"), (0, "off"))
        self.assertEqual(len([f for f in os.listdir(f"/proc/{self.daemon.pid}/fd")
                              if os.path.realpath(f"/proc/{self.daemon.pid}/fd/{f}")
                              == str(self.image)]), 0)


class Refused(SysdCase):
    def assert_refused(self, *words, contains="error"):
        code, reply = self.ask(*words)
        self.assertEqual(code, 1, reply)
        self.assertIn(contains, reply)
        self.assertEqual((self.props / "sys.usb.state").read_text().strip(), "adb")
        self.assertEqual(self.lun_value("file"), "")

    def test_path_in_the_name(self):
        self.assert_refused("on", "../etc/passwd.iso", "disk", contains="invalid image name")

    def test_wrong_extension(self):
        (self.images / "notes.txt").write_text("x")
        self.assert_refused("on", "notes.txt", "disk", contains="invalid image name")

    def test_symbolic_link(self):
        (self.images / "link.iso").symlink_to(self.image)
        self.assert_refused("on", "link.iso", "disk", contains="no such image")

    def test_directory(self):
        (self.images / "folder.iso").mkdir()
        self.assert_refused("on", "folder.iso", "disk", contains="no such image")

    def test_empty_file(self):
        (self.images / "empty.iso").write_bytes(b"")
        self.assert_refused("on", "empty.iso", "disk", contains="no such image")

    def test_missing_file(self):
        self.assert_refused("on", "nothing.iso", "disk", contains="no such image")

    def test_unknown_mode(self):
        self.assert_refused("on", self.image.name, "floppy", contains="mode")

    def test_unknown_request(self):
        self.assert_refused("format", "everything", contains="unknown request")


class Log(SysdCase):
    def test_refused_request_is_logged_whole(self):
        """The log shows what a caller tried, also when it was refused (the
        request used to be cut after the first word)."""
        self.ask("on", "../etc/passwd.iso", "disk")
        self.ask("brightness", "120")
        err = self.stop().decode(errors="replace")
        self.assertIn(f"uid {os.getuid()}: on ../etc/passwd.iso disk -> error", err)
        self.assertIn(f"uid {os.getuid()}: brightness 120 -> ", err)


class Brightness(SysdCase):
    def test_read(self):
        self.assertEqual(self.ask("brightness"), (0, "brightness 200"))

    def test_set(self):
        self.assertEqual(self.ask("brightness", "120"), (0, "brightness 120"))
        self.assertEqual(self.backlight.read_text().strip(), "120")

    def test_never_fully_dark_and_not_above_the_maximum(self):
        self.assertEqual(self.ask("brightness", "0"), (0, "brightness 10"))
        self.assertEqual(self.ask("brightness", "999"), (0, "brightness 255"))

    def test_not_a_number(self):
        self.assertEqual(self.ask("brightness", "12a")[0], 1)
        self.assertEqual(self.backlight.read_text().strip(), "200")


class OtherCaller(SysdCase):
    allow = 54321

    def test_refused_without_any_change(self):
        code, _ = self.ask("on", self.image.name, "disk")
        self.assertEqual(code, 126)
        self.assertEqual((self.props / "sys.usb.state").read_text().strip(), "adb")
        self.assertFalse((self.props / "log").exists())


class Client(unittest.TestCase):
    def test_without_daemon(self):
        done = subprocess.run([str(SYSD), "ask", "status"], capture_output=True, timeout=5,
                              env=dict(os.environ, TB_SYSD_NAME="no-such-sysd"))
        self.assertEqual(done.returncode, 126)


if __name__ == "__main__":
    unittest.main()
