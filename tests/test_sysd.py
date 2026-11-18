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
        self.dir = d
        self.prepare()
        env = dict(os.environ, PROPS=str(self.props), LUN=str(self.lun))
        self.daemon = subprocess.Popen(
            [str(SYSD), "--name", self.name, "--allow",
             str(os.getuid() if self.allow is None else self.allow),
             "--owner", str(os.getuid()), "--dir", str(self.images), "--lun", str(self.lun),
             "--backlight", str(self.backlight),
             "--setprop", str(d / "setprop"), "--getprop", str(d / "getprop"),
             *self.extra_args()],
            env=env, stderr=subprocess.PIPE)
        time.sleep(0.2)

    def prepare(self):
        """Hook: files the daemon needs before it starts."""

    def extra_args(self):
        return []

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


MOUNTER = """#!/bin/sh
# stands in for the loop device and mount (that needs root): logs, and fails
# to unmount while "busy" exists (a program has a file open)
echo "$*" >> "$MOUNTS/log"
if [ "$1" = umount ] && [ -f "$MOUNTS/busy" ]; then exit 1; fi
exit 0
"""


class TransferCase(SysdCase):
    """The transfer drive: a FAT image that is either the tablet's (mounted
    for the session) or the computer's (offered over USB, writable)."""

    def prepare(self):
        self.mounts = self.dir / "mounts"
        self.mounts.mkdir()
        self.transfer = self.dir / "transfer.img"
        self.transfer.write_bytes(b"\x00" * 8192)
        self.folder = self.dir / "Transfer"
        (self.dir / "mounter").write_text(MOUNTER)
        (self.dir / "mounter").chmod(0o755)
        os.environ["MOUNTS"] = str(self.mounts)
        self.uevents = self.dir / "uevents"
        os.mkfifo(self.uevents)

    def extra_args(self):
        return ["--transfer", str(self.transfer), "--transfer-mount", str(self.folder),
                "--mounter", str(self.dir / "mounter"), "--uevents", str(self.uevents)]

    def usb(self, *states):
        """What the gadget reports: USB_STATE uevents, NUL-separated like netlink."""
        fd = os.open(self.uevents, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, b"".join(f"ACTION=change\0USB_STATE={s}\0SUBSYSTEM=android_usb\0".encode()
                                  for s in states))
        finally:
            os.close(fd)

    def mount_log(self):
        path = self.mounts / "log"
        return path.read_text().splitlines() if path.exists() else []

    def wait_for(self, condition, timeout=6.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.05)
        return False


class Transfer(TransferCase):
    def test_mounted_for_the_tablet_at_start(self):
        self.assertEqual(self.ask("transfer", "status"), (0, "transfer tablet"))
        self.assertEqual(self.mount_log(), [f"mount {self.transfer} {self.folder}"])
        self.assertTrue(self.folder.is_dir())

    def test_give_offers_it_writable_and_unmounts_first(self):
        self.assertEqual(self.ask("transfer", "give"), (0, "ok transfer computer"))
        self.assertEqual(self.mount_log()[-1], f"umount {self.folder}")
        self.assertEqual(self.lun_value("ro"), "0")
        self.assertEqual(self.lun_value("cdrom"), "0")
        self.assertTrue(self.lun_value("file").startswith("/proc/self/fd/"))
        self.assertEqual((self.props / "sys.usb.config").read_text().strip(), "mass_storage,adb")
        self.assertEqual(self.ask("transfer", "status"), (0, "transfer computer"))

    def test_take_waits_for_the_computer_to_eject(self):
        self.ask("transfer", "give")
        code, reply = self.ask("transfer", "take")
        self.assertEqual(code, 1)
        self.assertIn("eject it there first", reply)
        self.assertEqual(self.ask("transfer", "status"), (0, "transfer computer"))
        self.assertEqual(self.ask("transfer", "take", "force"), (0, "ok transfer tablet"))
        self.assertEqual(self.lun_value("file"), "")
        self.assertEqual((self.props / "sys.usb.config").read_text().strip(), "adb")
        self.assertEqual(self.mount_log()[-1], f"mount {self.transfer} {self.folder}")

    def test_eject_on_the_computer_brings_it_back_by_itself(self):
        self.ask("transfer", "give")
        (self.lun / "file").write_text("")          # what the gadget does on an eject
        self.assertTrue(self.wait_for(lambda: self.ask("transfer", "status")[1] == "transfer tablet"),
                        "the drive did not come back")
        self.assertEqual((self.props / "sys.usb.config").read_text().strip(), "adb")
        self.assertEqual(self.mount_log()[-1], f"mount {self.transfer} {self.folder}")

    def test_folder_in_use_keeps_it_on_the_tablet(self):
        (self.mounts / "busy").write_text("")
        code, reply = self.ask("transfer", "give")
        self.assertEqual(code, 1)
        self.assertIn("in use", reply)
        self.assertEqual(self.ask("transfer", "status"), (0, "transfer tablet"))
        self.assertEqual(self.lun_value("file"), "")
        self.assertFalse((self.props / "sys.usb.config").exists())

    def test_installer_and_transfer_exclude_each_other(self):
        self.ask("transfer", "give")
        code, reply = self.ask("on", self.image.name, "disk")
        self.assertEqual(code, 1)
        self.assertIn("transfer drive is with the computer", reply)
        self.ask("transfer", "take", "force")
        self.assertEqual(self.ask("on", self.image.name, "disk")[0], 0)
        code, reply = self.ask("transfer", "give")
        self.assertEqual(code, 1)
        self.assertIn("USB installer", reply)

    def test_wrong_requests(self):
        for words in (("transfer",), ("transfer", "take", "now"), ("transfer", "give", "x")):
            self.assertEqual(self.ask(*words), (1, "error unknown request"), words)


class TransferEnd(TransferCase):
    def test_terminating_unmounts_and_takes_it_back(self):
        self.ask("transfer", "give")
        self.daemon.terminate()
        self.assertEqual(self.daemon.wait(timeout=10), 0)
        self.alive = True                            # ended on purpose
        _, self.err = self.daemon.communicate()
        self.assertEqual(self.lun_value("file"), "")
        self.assertEqual(self.mount_log()[-1], f"umount {self.folder}")


class NoTransfer(SysdCase):
    def test_not_configured(self):
        self.assertEqual(self.ask("transfer", "give"), (1, "error no transfer drive configured"))
        self.assertEqual(self.ask("transfer", "status"), (0, "transfer none"))

    def test_options_go_together(self):
        done = subprocess.run([str(SYSD), "--name", "x-transfer-alone", "--transfer", "/tmp/x"],
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(done.returncode, 2)


class FolderIsALink(TransferCase):
    """The session user owns ~ and could replace ~/Transfer by a link to an
    Android system file: tb-sysd (root) must not follow it."""

    def prepare(self):
        super().prepare()
        self.victim = self.dir / "victim"
        self.victim.mkdir()
        self.folder.symlink_to(self.victim)

    def test_link_is_refused(self):
        self.assertEqual(self.ask("transfer", "status"), (0, "transfer none"))
        self.assertFalse(any(line.startswith("mount") for line in self.mount_log()))
        code, reply = self.ask("transfer", "take")
        self.assertEqual(code, 1)
        self.assertIn("not a plain folder", reply)
        self.assertTrue(self.folder.is_symlink())


NEWFS = """#!/bin/sh
# stands in for Android's newfs_msdos: logs, writes the image file
echo "$*" >> "$MOUNTS/newfs"
for last; do :; done
printf 'FAT32 image' > "$last"
"""


class Create(TransferCase):
    def prepare(self):
        super().prepare()
        self.transfer.unlink()                      # no drive yet
        (self.dir / "newfs").write_text(NEWFS)
        (self.dir / "newfs").chmod(0o755)

    def extra_args(self):
        return super().extra_args() + ["--newfs", str(self.dir / "newfs")]

    def test_create_makes_and_mounts_it(self):
        self.assertEqual(self.ask("transfer", "status"), (0, "transfer none"))
        self.assertEqual(self.ask("transfer", "create", "64"), (0, "ok transfer tablet"))
        self.assertTrue(self.transfer.exists())
        self.assertEqual(self.transfer.stat().st_mode & 0o777, 0o600)
        self.assertFalse(pathlib.Path(f"{self.transfer}.new").exists())
        self.assertEqual((self.mounts / "newfs").read_text().split(),
                         ["-F", "32", "-L", "TABLET", "-S", "512", "-s", "131072", "-h", "64",
                          "-u", "32", "-o", "0", "-C", "64m", f"{self.transfer}.new"])
        self.assertEqual(self.mount_log()[-1], f"mount {self.transfer} {self.folder}")
        code, reply = self.ask("transfer", "create", "64")
        self.assertEqual(code, 1)
        self.assertIn("already", reply)

    def test_bad_sizes(self):
        for size in ("63", "1025", "abc", "-5", "100x"):
            code, reply = self.ask("transfer", "create", size)
            self.assertEqual(code, 1, size)
            self.assertIn("64 to 1024", reply, size)
        self.assertFalse(self.transfer.exists())


class ComputerLetsGo(TransferCase):
    """KDE's "Safely remove" powers the USB device off without an eject; the
    gadget then reports DISCONNECTED. Seen on the tablet: the drive stayed
    with the computer, which mounted it again at once."""

    def test_disconnect_after_the_computer_took_it_brings_it_back(self):
        self.ask("transfer", "give")
        # our own switch, then the computer enumerates it
        self.usb("DISCONNECTED", "CONNECTED", "CONFIGURED")
        time.sleep(0.5)
        self.assertEqual(self.ask("transfer", "status"), (0, "transfer computer"))
        self.usb("DISCONNECTED")                     # "Safely remove", or the cable pulled
        self.assertTrue(self.wait_for(lambda: self.ask("transfer", "status")[1] == "transfer tablet"),
                        "the drive did not come back")
        self.assertEqual(self.lun_value("file"), "")
        self.assertEqual((self.props / "sys.usb.config").read_text().strip(), "adb")
        self.assertEqual(self.mount_log()[-1], f"mount {self.transfer} {self.folder}")

    def test_quick_reconnect_in_one_batch_counts(self):
        self.ask("transfer", "give")
        self.usb("CONFIGURED")
        time.sleep(0.5)
        self.usb("DISCONNECTED", "CONNECTED", "CONFIGURED")   # the computer re-enumerated it
        self.assertTrue(self.wait_for(lambda: self.ask("transfer", "status")[1] == "transfer tablet"))

    def test_disconnect_before_the_computer_took_it_does_not(self):
        self.ask("transfer", "give")
        self.usb("DISCONNECTED")                     # only our own switch
        time.sleep(1.0)
        self.assertEqual(self.ask("transfer", "status"), (0, "transfer computer"))
