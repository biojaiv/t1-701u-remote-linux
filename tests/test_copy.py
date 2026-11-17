"""MSG_COPY (moving an area, for scrolling): every byte of the file
framebuffer is compared with a model, for all four directions with overlap,
and invalid areas end the connection."""
import random
import struct
import unittest

from harness import H, W, DaemonCase, frame, rect

COPY = 0x07


def expand(p):
    r5, g6, b5 = p >> 11, (p >> 5) & 0x3F, p & 0x1F
    return bytes([r5 << 3 | r5 >> 2, g6 << 2 | g6 >> 4, b5 << 3 | b5 >> 2, 0xFF])


def copy_msg(sx, sy, w, h, dx, dy):
    return frame(COPY, struct.pack("<6H", sx, sy, w, h, dx, dy))


class Model:
    def __init__(self):
        self.px = [[0] * W for _ in range(H)]

    def draw(self, x, y, w, h, pixels):
        for r in range(h):
            for c in range(w):
                self.px[y + r][x + c] = pixels[r * w + c]

    def copy(self, sx, sy, w, h, dx, dy):
        block = [row[sx:sx + w] for row in self.px[sy:sy + h]]
        for r in range(h):
            self.px[dy + r][dx:dx + w] = block[r]

    def bytes(self):
        out = bytearray(W * H * 4)
        for y in range(H):
            for x in range(W):
                if self.px[y][x]:
                    out[(y * W + x) * 4:(y * W + x) * 4 + 4] = expand(self.px[y][x])
        return bytes(out)


class Copy(DaemonCase):
    def setUp(self):
        super().setUp()
        self.conn = self.connect()
        self.model = Model()
        rng = random.Random(7)
        pixels = [rng.randrange(1, 65536) for _ in range(W * H)]
        self.conn.send(rect(0, 0, W, H, 0, struct.pack(f"<{W * H}H", *pixels)))
        self.model.draw(0, 0, W, H, pixels)

    def check(self, *area):
        self.conn.send(copy_msg(*area))
        self.model.copy(*area)
        self.sync(self.conn)
        self.assertEqual(self.framebuffer(), self.model.bytes())

    def test_info_announces_copy(self):
        self.assertEqual(self.conn.info[6] & 0x04, 0x04)

    def test_right_overlapping(self):
        self.check(5, 3, W - 20, H - 10, 12, 3)

    def test_left_overlapping(self):
        self.check(12, 3, W - 20, H - 10, 5, 3)

    def test_down_overlapping(self):
        self.check(2, 4, W - 10, H - 20, 2, 11)

    def test_up_overlapping(self):
        self.check(2, 11, W - 10, H - 20, 2, 4)

    def test_diagonal_and_several_in_one_frame(self):
        self.conn.send(copy_msg(0, 0, 10, 10, 30, 20))
        self.model.copy(0, 0, 10, 10, 30, 20)
        self.check(30, 20, 10, 10, 31, 21)

    def assert_dropped(self, *area):
        self.conn.send(copy_msg(*area))
        self.assertTrue(self.conn.closed_by_peer())

    def test_source_outside(self):
        self.assert_dropped(W - 5, 0, 10, 10, 0, 0)

    def test_target_outside(self):
        self.assert_dropped(0, 0, 10, 10, 0, H - 5)

    def test_empty_area(self):
        self.assert_dropped(0, 0, 0, 10, 0, 0)

    def test_wrong_length(self):
        self.conn.send(frame(COPY, struct.pack("<5H", 0, 0, 1, 1, 0)))
        self.assertTrue(self.conn.closed_by_peer())


if __name__ == "__main__":
    unittest.main()
