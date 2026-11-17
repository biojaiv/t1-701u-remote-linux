#!/usr/bin/env python3
"""Pairs laptop and tablet for the encrypted Wi-Fi link.

Creates the shared 32-byte key (or reuses the existing one) and copies it to
the tablet. The key only ever travels over the USB cable: the script refuses
to run if the tablet is not connected by USB, and it addresses the USB
connection explicitly even if adb also knows the tablet over Wi-Fi.

  laptop: ~/.config/huawai-companion/key            (mode 600)
  tablet: /data/local/tmp/tabletd-secret/key        (directory 700, file 600)

Usage: pair.py [--new]     --new replaces an existing key on both sides
"""
import argparse
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import link, secure  # noqa: E402

TABLET_DIR = "/data/local/tmp/tabletd-secret"
TABLET_KEY = TABLET_DIR + "/key"


def usb_serial():
    """The one tablet on USB that adb can use (the shared rule in host.link)."""
    serial = link.usb_serial()
    if serial is None:
        sys.exit("pair: need exactly one tablet on USB in state 'device' (adb devices -l)")
    return serial


def adb(serial, *args, data=None):
    result = subprocess.run(["adb", "-s", serial, *args], capture_output=True,
                            timeout=30, input=data)
    if result.returncode != 0:
        sys.exit(f"pair: adb {' '.join(args[:2])} failed: {result.stderr.decode().strip()}")
    return result.stdout.decode().replace("\r", "")


def laptop_key(new):
    path = secure.key_path()
    if path.exists() and not new:
        return secure.load_key(path), "existing"
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    key = os.urandom(secure.KEY_LEN)
    tmp = path.with_suffix(".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    os.replace(tmp, path)
    return key, "new"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--new", action="store_true", help="create a new key")
    args = parser.parse_args()

    serial = usb_serial()
    key, kind = laptop_key(args.new)
    adb(serial, "shell", f"mkdir -p {TABLET_DIR} && chmod 700 {TABLET_DIR}")
    with tempfile.TemporaryDirectory() as tmp:   # mode 700
        local = pathlib.Path(tmp) / "key"
        fd = os.open(local, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(key)
        adb(serial, "push", str(local), TABLET_KEY)
    adb(serial, "shell", f"chmod 600 {TABLET_KEY}")
    listing = adb(serial, "shell", f"ls -l {TABLET_KEY}; ls -ld {TABLET_DIR}")
    remote_md5 = adb(serial, "shell", f"md5 {TABLET_KEY}").split()[0]
    if remote_md5 != hashlib.md5(key).hexdigest():
        sys.exit("pair: the key on the tablet differs from the laptop's")
    modes = [line.split()[0] for line in listing.splitlines() if line.strip()]
    if modes != ["-rw-------", "drwx------"]:
        sys.exit(f"pair: unexpected permissions on the tablet: {modes}")
    print(f"pair: {kind} key on the laptop ({secure.key_path()}), copied over USB to "
          f"{TABLET_KEY}; permissions and content checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
