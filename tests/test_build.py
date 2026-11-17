"""Checks that the x86 build of tabletd exists and reports its version."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TABLETD = ROOT / "build" / "x86" / "tabletd"


class BuildTest(unittest.TestCase):
    def test_version(self):
        out = subprocess.run([str(TABLETD), "--version"],
                             capture_output=True, text=True, timeout=5)
        self.assertEqual(out.returncode, 0)
        self.assertRegex(out.stdout, r"^tabletd \S+ proto 1\n$")

    def test_unknown_arguments_fail(self):
        out = subprocess.run([str(TABLETD)],
                             capture_output=True, text=True, timeout=5)
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()
