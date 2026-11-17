"""hostd's side of the sound: pieces, format per route, the GStreamer path
(with a test tone instead of the laptop's real output) and the setting."""
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import audio, config  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def header(payload):
    return struct.unpack("<IBB", payload[:6])


class Pieces(unittest.TestCase):
    def test_48k_stereo_in_20_ms_pieces(self):
        pcm = struct.pack("<h", 1000) * (48000 * 50 // 1000 * 2)     # 50 ms
        out = list(audio.chunks(48000, 2, pcm))
        self.assertEqual([len(p) - 6 for p in out], [3840, 3840, 1920])
        self.assertEqual({header(p) for p in out}, {(48000, 2, 0)})
        self.assertEqual(b"".join(p[6:] for p in out), pcm)

    def test_24k_mono_piece_size(self):
        pcm = struct.pack("<h", -5) * 2400                            # 100 ms
        out = list(audio.chunks(24000, 1, pcm))
        self.assertEqual([len(p) - 6 for p in out], [960] * 5)
        self.assertEqual(header(out[0]), (24000, 1, 0))

    def test_silence_is_not_sent(self):
        silent = bytes(3840)
        loud = struct.pack("<h", 7) * 1920
        out = list(audio.chunks(48000, 2, silent + loud + silent))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][6:], loud)

    def test_partial_frame_is_cut(self):
        pcm = struct.pack("<hhh", 1, 2, 3)                            # 1.5 stereo frames
        out = list(audio.chunks(48000, 2, pcm))
        self.assertEqual(out[0][6:], struct.pack("<hh", 1, 2))

    def test_no_piece_exceeds_what_tabletd_accepts(self):
        pcm = struct.pack("<h", 3) * (48000 * 2)                      # 1 s
        self.assertTrue(all(len(p) <= 6 + 48000 // 10 * 4 for p in audio.chunks(48000, 2, pcm)))

    def test_format_per_route(self):
        self.assertEqual(audio.format_for("wifi"), (48000, 2))
        self.assertEqual(audio.format_for("adb"), (48000, 2))
        self.assertEqual(audio.format_for("internet"), (24000, 1))


class Pipeline(unittest.TestCase):
    """The GStreamer path with a test tone: pieces arrive in the main loop."""

    def run_tone(self, rate, channels):
        from gi.repository import GLib
        got = []
        sender = audio.AudioSender(got.append, rate, channels,
                                   source="audiotestsrc wave=sine num-buffers=25 "
                                          "samplesperbuffer=480")
        sender.start()
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and sum(len(p) - 6 for p in got) < rate // 10 * 2:
            context.iteration(False)
            time.sleep(0.005)
        sender.stop()
        while context.iteration(False):
            pass
        return got

    def test_tone_arrives_as_48k_stereo(self):
        got = self.run_tone(48000, 2)
        self.assertTrue(got, "no sound piece arrived")
        self.assertEqual({header(p) for p in got}, {(48000, 2, 0)})
        self.assertTrue(all((len(p) - 6) % 4 == 0 for p in got))

    def test_tone_arrives_as_24k_mono(self):
        got = self.run_tone(24000, 1)
        self.assertTrue(got, "no sound piece arrived")
        self.assertEqual({header(p) for p in got}, {(24000, 1, 0)})
        self.assertTrue(all((len(p) - 6) % 2 == 0 for p in got))

    def test_nothing_sent_after_stop(self):
        from gi.repository import GLib
        got = []
        sender = audio.AudioSender(got.append, 48000, 2, source="audiotestsrc wave=sine")
        sender.start()
        time.sleep(0.2)
        sender.stop()
        context = GLib.MainContext.default()
        while context.iteration(False):        # pieces queued before the stop
            pass
        before = len(got)
        time.sleep(0.2)
        while context.iteration(False):
            pass
        self.assertEqual(len(got), before)


class Setting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, XDG_CONFIG_HOME=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_on_and_switched_off_by_the_cli(self):
        self.assertTrue(config.DEFAULTS["audio"])
        done = subprocess.run([sys.executable, "-m", "host.cli", "config", "audio=false"],
                              cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        path = pathlib.Path(self.tmp.name) / "huawai-companion" / "hostd.toml"
        self.assertIs(config.load(path)["audio"], False)
        self.assertIn("audio = false", path.read_text())

    def test_not_a_boolean_is_refused(self):
        path = pathlib.Path(self.tmp.name) / "hostd.toml"
        path.write_text('audio = "loud"\n')
        with self.assertRaises(config.ConfigError):
            config.load(path)


if __name__ == "__main__":
    unittest.main()
