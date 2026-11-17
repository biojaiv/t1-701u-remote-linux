"""LZ4 block decoder: compared against the lz4 module, then fed damaged input."""
import os
import pathlib
import random
import struct
import subprocess
import unittest

import lz4.block

from harness import H, W, DaemonCase, rect

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLI = ROOT / "build" / "x86" / "lz4cli"


def decode_many(cases):
    """cases: list of (block, expected_length). Returns list of bytes or None."""
    stdin = b"".join(struct.pack("<II", len(block), length) + block
                     for block, length in cases)
    proc = subprocess.run([str(CLI)], input=stdin, capture_output=True, timeout=120)
    if proc.returncode != 0 or proc.stderr:
        raise AssertionError(f"lz4cli failed ({proc.returncode}):\n"
                             + proc.stderr.decode(errors="replace")[-3000:])
    out, pos, results = proc.stdout, 0, []
    for _, length in cases:
        status = out[pos]
        pos += 1
        if status == 0:
            results.append(out[pos:pos + length])
            pos += length
        else:
            results.append(None)
    if pos != len(out):
        raise AssertionError("lz4cli wrote more than expected")
    return results


def compress(data):
    return lz4.block.compress(data, store_size=False)


def picture():
    """Something like a desktop: flat areas, a gradient, some noise."""
    rows = []
    rng = random.Random(1)
    for y in range(256):
        row = [0x18C3] * 100 + [(y * 31 // 255) << 11 | x % 64 << 5 for x in range(100)]
        row += [rng.getrandbits(16) for _ in range(20)] + [0xFFFF] * 36
        rows.append(struct.pack("<256H", *row))
    return b"".join(rows)


class RoundTrip(unittest.TestCase):
    def check(self, samples):
        results = decode_many([(compress(data), len(data)) for data in samples])
        for data, result in zip(samples, results):
            self.assertEqual(result, data, f"sample of {len(data)} bytes")

    def test_empty(self):
        self.check([b""])

    def test_random(self):
        self.check([os.urandom(n) for n in (1, 2, 13, 255, 4096, 100000)])

    def test_uniform(self):
        self.check([bytes([v]) * n for v in (0, 0xFF, 0x5A)
                    for n in (1, 4, 5, 18, 19, 20, 273, 274, 70000)])

    def test_picture(self):
        self.check([picture(), picture()[:12345]])

    def test_full_screen_of_one_colour(self):
        self.check([b"\x1f\x00" * (W * H)])

    def test_lengths_around_the_extension_limits(self):
        rng = random.Random(2)
        samples = []
        for lit in (0, 1, 14, 15, 16, 269, 270, 271, 525, 526):
            for match in (4, 5, 18, 19, 20, 273, 274, 275, 530):
                literal = bytes(rng.getrandbits(8) for _ in range(lit))
                samples.append(literal + b"abcdefgh" * 2 + b"x" * match + literal)
        self.check(samples)

    def test_overlapping_matches(self):
        self.check([b"ab" * 500, b"abc" * 333, b"a" + b"b" * 1000,
                    bytes(range(256)) * 40])


class Damaged(unittest.TestCase):
    """No damaged block may crash the decoder or write outside its buffers."""

    def setUp(self):
        self.data = picture()[:6000] + b"tail" * 50 + os.urandom(64)
        self.block = compress(self.data)
        self.assertLess(len(self.block), len(self.data))

    def test_every_byte_changed(self):
        cases = []
        for pos in range(len(self.block)):
            for change in (0x01, 0x80, 0xFF):
                damaged = bytearray(self.block)
                damaged[pos] ^= change
                cases.append((bytes(damaged), len(self.data)))
            for value in (0x00, 0xFF):
                damaged = bytearray(self.block)
                damaged[pos] = value
                cases.append((bytes(damaged), len(self.data)))
        results = decode_many(cases)
        # A changed literal byte still decodes (to other pixels), so only part
        # of the damage can be detected; the point is that nothing crashes.
        self.assertGreater(sum(result is None for result in results), 0)
        for result in results:
            self.assertTrue(result is None or len(result) == len(self.data))

    def test_truncated_blocks_are_errors(self):
        cases = [(self.block[:n], len(self.data)) for n in range(len(self.block))]
        self.assertTrue(all(result is None for result in decode_many(cases)))

    def test_wrong_expected_length_is_an_error(self):
        cases = [(self.block, len(self.data) + delta)
                 for delta in (-len(self.data), -100, -1, 1, 100)]
        self.assertTrue(all(result is None for result in decode_many(cases)))

    def test_hand_made_bad_blocks(self):
        bad = [
            (b"", 1),                              # no token
            (b"\x10", 1),                          # literal announced, missing
            (b"\x00\x00\x00", 4),                  # offset 0
            (b"\x10A\x02\x00", 5),                 # offset beyond output start
            (b"\x10A\x01", 5),                     # offset cut in half
            (b"\xf0" + b"\xff" * 20, 100),         # endless length extension
            (b"\x1fA\x01\x00" + b"\xff" * 8, 30),  # match extension never ends
            (b"\x1fA\x01\x00\xff\xff\x00", 30),    # match longer than output
            (b"\x40ABCD", 3),                      # literals longer than output
        ]
        self.assertTrue(all(result is None for result in decode_many(bad)))

    def test_random_garbage(self):
        rng = random.Random(3)
        cases = [(bytes(rng.getrandbits(8) for _ in range(rng.randint(1, 200))),
                  rng.randint(0, 4000)) for _ in range(5000)]
        for (_, length), result in zip(cases, decode_many(cases)):
            self.assertTrue(result is None or len(result) == length)


class CompressedRects(DaemonCase):
    def test_compressed_rect_is_drawn_like_a_raw_one(self):
        pixels = picture()[:200 * 100 * 2]
        raw, packed = self.connect(), None
        raw.send(rect(50, 60, 200, 100, 0, pixels))
        self.sync(raw)
        expected = self.framebuffer()
        packed = self.connect()
        packed.send(rect(50, 60, 200, 100, 0, bytes(len(pixels))))   # wipe
        packed.send(rect(50, 60, 200, 100, 1, compress(pixels)))
        self.sync(packed)
        self.assertEqual(self.framebuffer(), expected)

    def test_full_screen_compressed(self):
        conn = self.connect()
        conn.send(rect(0, 0, W, H, 1, compress(b"\xe0\x07" * (W * H))))
        self.sync(conn)
        self.assertEqual(self.framebuffer(), bytes([0, 255, 0, 255]) * (W * H))

    def check_rejected(self, data):
        conn = self.connect()
        conn.send(data)
        self.assertTrue(conn.closed_by_peer())
        self.sync(self.connect())
        self.assertEqual(self.framebuffer(), bytes(W * H * 4))

    def test_damaged_block_closes_the_connection(self):
        self.check_rejected(rect(0, 0, 10, 10, 1, b"\x10A\x02\x00"))

    def test_decoded_size_must_match_the_rect(self):
        block = compress(bytes(10 * 10 * 2))
        self.check_rejected(rect(0, 0, 10, 11, 1, block))
        self.check_rejected(rect(0, 0, 10, 9, 1, block))


if __name__ == "__main__":
    unittest.main()
