"""tb-detach: the child runs in its own session and survives its caller."""
import os
import pathlib
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DETACH = ROOT / "build" / "x86" / "tb-detach"


class Detach(unittest.TestCase):
    def test_child_has_its_own_session_and_outlives_the_caller(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "out"
            script = f"echo $$ > {out}.pid; sleep 1; echo alive > {out}"
            caller = subprocess.run(
                ["sh", "-c", f"{DETACH} /bin/sh -c '{script}'; kill -HUP 0"],
                capture_output=True, text=True, timeout=10, start_new_session=True)
            pid = int(caller.stdout.split()[0])
            self.assertEqual(os.getsid(pid), pid)            # session leader
            self.assertNotEqual(os.getsid(pid), os.getsid(0))
            deadline = time.monotonic() + 5
            while not out.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(out.read_text().strip(), "alive")

    def test_only_absolute_paths(self):
        self.assertEqual(subprocess.run([str(DETACH), "sh"], capture_output=True).returncode, 2)

    def test_failed_exec_is_still_reported_as_started(self):
        # exec fails after the report: the caller still sees a started child,
        # which then ends with 127 - the launcher's boot.log shows that
        done = subprocess.run([str(DETACH), "/no/such/program"], capture_output=True, timeout=5)
        self.assertEqual(done.returncode, 0)


if __name__ == "__main__":
    unittest.main()
