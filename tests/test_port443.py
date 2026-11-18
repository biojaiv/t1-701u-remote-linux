"""scripts/port443.nft and scripts/mullvad-incoming.nft on a real network path,
without root: two private network namespaces (a "laptop" and a "tablet")
joined by a virtual cable. In the laptop one both rule files are loaded and a
real hostd listener waits on 7110; in the tablet one a real tabletd calls
port 443. The call must arrive at 7110, finish the handshake, and the rules
must have counted: the redirect, and the Mullvad marks in both directions
(they must see 7110 after the redirect)."""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
NFT = "/usr/sbin/nft"


def namespaces_allowed():
    if not (shutil.which("unshare") and shutil.which("nsenter") and os.path.exists(NFT)):
        return False
    try:
        return subprocess.run(["unshare", "-rn", "true"], capture_output=True,
                              timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


INNER = """\
set -e
unshare -n sleep 60 &
TAB=$!
sleep 0.3
ip link set lo up
ip link add vlap type veth peer name vtab netns $TAB
ip addr add 2001:db8::1/64 dev vlap nodad
ip link set vlap up
nsenter -t $TAB -n --preserve-credentials sh -c \\
    'ip link set lo up; ip addr add 2001:db8::2/64 dev vtab nodad; ip link set vtab up'
{nft} -f {root}/scripts/port443.nft
{nft} -f {root}/scripts/mullvad-incoming.nft
{python} {run} $TAB
{nft} list ruleset | grep counter
kill $TAB
"""

RUN = """\
import os, pathlib, subprocess, sys, time
sys.path.insert(0, {root!r}); sys.path.insert(0, {tests!r})
from host import link
from harness import TABLETD, W, H
d = pathlib.Path({tmp!r})
session = link.Session(port=7999, use_adb=False, psk=(d / "key").read_bytes(), listen_port=7110)
proc = subprocess.Popen(["nsenter", "-t", sys.argv[1], "-n", "--preserve-credentials",
                         str(TABLETD), "--fb", str(d / "fb.raw"), "--geom", f"{{W}}x{{H}}",
                         "--listen", "abstract:port443-test", "--secure-connect",
                         "[2001:db8::1]:443", "--key", str(d / "key")], stderr=subprocess.DEVNULL)
end = time.monotonic() + 15
peer = None
while time.monotonic() < end and peer is None:
    for event in session.pump(0.05):
        if event[0] == "connected":
            peer = session.link.sock.getpeername()[0]
print("CONNECTED", peer)
proc.kill(); proc.wait(); session.close()
"""


@unittest.skipUnless(namespaces_allowed(), "no unprivileged network namespaces or no nft")
class Port443(unittest.TestCase):
    def test_call_to_443_reaches_hostd_on_7110(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            (d / "key").write_bytes(os.urandom(32))
            (d / "key").chmod(0o600)
            (d / "run.py").write_text(RUN.format(root=str(ROOT), tests=str(ROOT / "tests"),
                                                 tmp=tmp))
            (d / "inner.sh").write_text(textwrap.dedent(INNER).format(
                nft=NFT, root=ROOT, python=sys.executable, run=d / "run.py"))
            done = subprocess.run(["unshare", "-rn", "sh", str(d / "inner.sh")],
                                  capture_output=True, text=True, timeout=60)
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        self.assertIn("CONNECTED 2001:db8::2", out, out)

        def packets(rule):
            match = re.search(re.escape(rule) + r" counter packets (\d+)", out)
            self.assertIsNotNone(match, f"rule not found: {rule}\n{out}")
            return int(match.group(1))
        self.assertGreater(packets("tcp dport 443"), 0, "the redirect did not count")
        self.assertGreater(packets("tcp dport 7110"), 0, "incoming not marked for Mullvad")
        self.assertGreater(packets("tcp sport 7110"), 0, "replies not marked for Mullvad")


if __name__ == "__main__":
    unittest.main()
