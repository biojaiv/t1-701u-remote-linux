"""hostd.toml, the huawai command's config subcommand, and reading the
tablet's Wi-Fi address from netcfg output."""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from host import config, link  # noqa: E402

NETCFG = """lo       UP                                   127.0.0.1/8   0x00000049 00:00:00:00:00:00
ip6tnl0  DOWN                                   0.0.0.0/0   0x00000080 00:00:00:00:00:00
wlan0    UP                               192.168.1.110/24  0x00001043 02:00:00:00:00:01
p2p0     UP                                     0.0.0.0/0   0x00001003 de:09:4c:45:17:63
"""


class Config(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = pathlib.Path(self.tmp.name) / "hostd.toml"

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_without_file(self):
        self.assertEqual(config.load(self.file), config.DEFAULTS)

    def test_save_and_load(self):
        config.save({"wifi_host": "10.0.0.7", "rotation": "clockwise"}, self.file)
        values = config.load(self.file)
        self.assertEqual(values["wifi_host"], "10.0.0.7")
        self.assertEqual(values["rotation"], "clockwise")
        self.assertEqual(values["mode"], "mirror")
        self.assertIn("# Address of the tablet", self.file.read_text())

    def test_update_keeps_other_values(self):
        config.save({"rotation": "clockwise"}, self.file)
        config.update(self.file, wifi_host="192.168.1.5")
        self.assertEqual(config.load(self.file)["rotation"], "clockwise")

    def test_bad_values_are_refused(self):
        for text in ('wifi_host = "tablet.local"\n', 'rotation = "upside"\n',
                     'mode = "game"\n', 'cable_fallback = "yes"\n', 'colour = "red"\n',
                     "this is = = not toml\n"):
            self.file.write_text(text)
            with self.subTest(text=text), self.assertRaises(config.ConfigError):
                config.load(self.file)

    def test_command_line_config(self):
        env = dict(os.environ, XDG_CONFIG_HOME=self.tmp.name)
        cli = [sys.executable, str(ROOT / "host" / "cli.py"), "config"]
        done = subprocess.run(cli + ["wifi_host=192.168.1.42", "cable_fallback=false"],
                              env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        values = config.load(pathlib.Path(self.tmp.name) / "huawai-companion" / "hostd.toml")
        self.assertEqual((values["wifi_host"], values["cable_fallback"]), ("192.168.1.42", False))
        done = subprocess.run(cli + ["wifi_host=nonsense"], env=env, capture_output=True,
                              text=True, timeout=30)
        self.assertNotEqual(done.returncode, 0)


class WlanAddress(unittest.TestCase):
    def test_address_of_wlan0(self):
        self.assertEqual(link.parse_wlan_address(NETCFG), "192.168.1.110")

    def test_no_address_while_not_connected(self):
        self.assertIsNone(link.parse_wlan_address(NETCFG.replace("192.168.1.110", "0.0.0.0")))
        self.assertIsNone(link.parse_wlan_address(NETCFG.replace("wlan0    UP", "wlan0    DOWN")))
        self.assertIsNone(link.parse_wlan_address(""))


if __name__ == "__main__":
    unittest.main()
