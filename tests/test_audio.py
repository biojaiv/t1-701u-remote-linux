"""Sound from the laptop (MSG_AUDIO) through tabletd to its player (--audio).

The real player is tb-audio (ALSA, only on the tablet). Here a small script
stands in for it: it reports "ready" on fd 3 like tb-audio and writes what it
gets into a file, so the conversion to 48 kHz stereo can be checked bit for
bit. Also: tabletd never waits for the player, and a player that is missing,
not ready or gone takes nothing else down."""
import os
import pathlib
import socket
import stat
import struct
import time
import unittest

from harness import INFO, PING, PONG, DaemonCase, frame

AUDIO = 0x08
INFO_AUDIO = 0x08


def audio(rate, channels, samples, reserved=0):
    return frame(AUDIO, struct.pack("<IBB", rate, channels, reserved) + samples)


def pcm(*values):
    return struct.pack(f"<{len(values)}h", *values)


class PlayerCase(DaemonCase):
    """tabletd with a stand-in player; PLAYER is the script's body."""

    PLAYER = 'echo ready >&3\nexec 3>&-\nexec cat > "$TB_AUDIO_OUT"\n'

    def prepare(self):
        self.out = self.dir / "played.raw"
        self.player = self.dir / "player"
        self.player.write_text("#!/bin/sh\n" + self.PLAYER)
        self.player.chmod(self.player.stat().st_mode | stat.S_IXUSR)
        os.environ["TB_AUDIO_OUT"] = str(self.out)

    def args(self):
        return ["--audio", str(self.player)]

    def flags(self, conn):
        return conn.info[6]

    def ping(self, conn, counter=7, timeout=3.0):
        """The connection still works: PING is answered with PONG in time."""
        conn.send(frame(PING, struct.pack("<I", counter)))
        conn.sock.settimeout(timeout)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg_type, payload = conn.recv()
            if msg_type == PONG:
                return struct.unpack("<I", payload)[0] == counter
        return False

    def played(self, size, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.out.exists() and self.out.stat().st_size >= size:
                break
            time.sleep(0.02)
        return self.out.read_bytes() if self.out.exists() else b""


class Announce(PlayerCase):
    def test_ready_player_is_announced(self):
        conn = self.connect()
        self.assertTrue(self.flags(conn) & INFO_AUDIO)


class Conversion(PlayerCase):
    def test_48k_stereo_passes_unchanged(self):
        conn = self.connect()
        samples = pcm(1, -2, 300, -400, 32767, -32768)
        conn.send(audio(48000, 2, samples))
        self.assertTrue(self.ping(conn))
        self.assertEqual(self.played(len(samples)), samples)

    def test_24k_mono_becomes_48k_stereo(self):
        conn = self.connect()
        conn.send(audio(24000, 1, pcm(5, -6)))
        self.assertTrue(self.ping(conn))
        # every mono sample on both sides, every 24 kHz frame twice
        self.assertEqual(self.played(32), pcm(5, 5, 5, 5, -6, -6, -6, -6))

    def test_48k_mono_and_24k_stereo(self):
        conn = self.connect()
        conn.send(audio(48000, 1, pcm(9)))
        conn.send(audio(24000, 2, pcm(1, 2)))
        self.assertTrue(self.ping(conn))
        self.assertEqual(self.played(20), pcm(9, 9, 1, 2, 1, 2))


class Invalid(PlayerCase):
    """A broken AUDIO message is a protocol error like any other: the client
    is dropped, tabletd goes on (the next caller gets INFO again)."""

    def assert_dropped(self, message):
        conn = self.connect()
        conn.send(message)
        conn.sock.settimeout(3)
        with self.assertRaises((EOFError, ConnectionResetError, BrokenPipeError)):
            while True:
                conn.recv()
        self.assertTrue(self.connect().info)

    def test_unknown_rate(self):
        self.assert_dropped(audio(44100, 2, pcm(1, 2)))

    def test_three_channels(self):
        self.assert_dropped(audio(48000, 3, pcm(1, 2, 3)))

    def test_half_frame(self):
        self.assert_dropped(audio(48000, 2, pcm(1)))

    def test_reserved_byte_set(self):
        self.assert_dropped(audio(48000, 2, pcm(1, 2), reserved=1))

    def test_more_than_100_ms(self):
        self.assert_dropped(audio(48000, 2, pcm(*([0] * (2 * 4801)))))

    def test_header_only(self):
        self.assert_dropped(frame(AUDIO, struct.pack("<IBB", 48000, 2, 0)))


class SlowPlayer(PlayerCase):
    """A player that never reads: the pipe fills up, tabletd must not wait."""

    PLAYER = "echo ready >&3\nexec 3>&-\nexec sleep 60\n"

    def test_full_pipe_does_not_block(self):
        conn = self.connect()
        chunk = audio(48000, 2, pcm(*([1000] * 9600)))     # 100 ms, 19200 bytes
        start = time.monotonic()
        for _ in range(30):                                # 3 s of sound, pipe holds ~0.3 s
            conn.send(chunk)
        self.assertTrue(self.ping(conn, timeout=2.0), "tabletd stopped answering")
        self.assertLess(time.monotonic() - start, 2.5)


class PlayerGone(PlayerCase):
    PLAYER = "echo ready >&3\nexec 3>&-\nexit 0\n"

    def test_player_ending_takes_nothing_else_down(self):
        conn = self.connect()
        time.sleep(0.3)                                    # the player has ended
        for _ in range(5):
            conn.send(audio(48000, 2, pcm(1, 2) * 100))
        self.assertTrue(self.ping(conn))


class NotReady(PlayerCase):
    PLAYER = "exit 1\n"

    def test_no_ready_means_no_audio_flag(self):
        conn = self.connect()
        self.assertFalse(self.flags(conn) & INFO_AUDIO)
        conn.send(audio(48000, 2, pcm(1, 2)))              # still valid, just not played
        self.assertTrue(self.ping(conn))


class NoPlayer(DaemonCase):
    def test_without_audio_option_no_flag_and_audio_ignored(self):
        conn = self.connect()
        self.assertFalse(conn.info[6] & INFO_AUDIO)
        conn.send(audio(48000, 2, pcm(1, 2)))
        conn.send(frame(PING, struct.pack("<I", 3)))
        conn.sock.settimeout(3)
        while True:
            msg_type, payload = conn.recv()
            if msg_type == PONG:
                break
        self.assertEqual(struct.unpack("<I", payload)[0], 3)


if __name__ == "__main__":
    unittest.main()
