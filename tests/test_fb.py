"""RECT drawing: every byte of the file framebuffer is compared."""
import os
import struct
import unittest

from harness import BACKLIGHT, H, STATUS, W, DaemonCase, frame, rect


def expand(pixel565):
    """RGB565 value -> the 4 bytes of the file framebuffer (R, G, B, 0xFF)."""
    r5, g6, b5 = pixel565 >> 11, (pixel565 >> 5) & 0x3F, pixel565 & 0x1F
    return bytes([r5 << 3 | r5 >> 2, g6 << 2 | g6 >> 4, b5 << 3 | b5 >> 2, 0xFF])


class Model:
    """What the framebuffer file must contain."""

    def __init__(self):
        self.buf = bytearray(W * H * 4)

    def draw(self, x, y, w, h, pixels):
        for row in range(h):
            for col in range(w):
                off = ((y + row) * W + x + col) * 4
                self.buf[off:off + 4] = expand(pixels[row * w + col])


def pack565(pixels):
    return struct.pack(f"<{len(pixels)}H", *pixels)


class Drawing(DaemonCase):
    def draw(self, conn, model, x, y, w, h, pixels):
        conn.send(rect(x, y, w, h, 0, pack565(pixels)))
        model.draw(x, y, w, h, pixels)

    def test_starts_black(self):
        self.sync(self.connect())
        self.assertEqual(self.framebuffer(), bytes(W * H * 4))

    def test_small_rect(self):
        conn, model = self.connect(), Model()
        self.draw(conn, model, 10, 20, 3, 2,
                  [0xF800, 0x07E0, 0x001F, 0xFFFF, 0x0000, 0x1234])
        self.sync(conn)
        self.assertEqual(self.framebuffer(), bytes(model.buf))

    def test_colour_expansion(self):
        conn = self.connect()
        conn.send(rect(0, 0, 3, 1, 0, pack565([0xF800, 0x07E0, 0x001F])))
        self.sync(conn)
        self.assertEqual(self.framebuffer()[:12],
                         bytes([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255]))

    def test_corners_and_edges(self):
        conn, model = self.connect(), Model()
        self.draw(conn, model, 0, 0, 1, 1, [0xFFFF])
        self.draw(conn, model, W - 1, 0, 1, 1, [0xF800])
        self.draw(conn, model, 0, H - 1, 1, 1, [0x07E0])
        self.draw(conn, model, W - 1, H - 1, 1, 1, [0x001F])
        self.draw(conn, model, 0, H - 2, W, 2, [0xAAAA] * (W * 2))
        self.draw(conn, model, W - 2, 0, 2, H, [0x5555] * (2 * H))
        self.sync(conn)
        self.assertEqual(self.framebuffer(), bytes(model.buf))

    def test_full_screen_random(self):
        conn, model = self.connect(), Model()
        raw = os.urandom(W * H * 2)
        pixels = list(struct.unpack(f"<{W * H}H", raw))
        self.draw(conn, model, 0, 0, W, H, pixels)
        self.sync(conn)
        self.assertEqual(self.framebuffer(), bytes(model.buf))

    def test_overlapping_rects_last_one_wins(self):
        conn, model = self.connect(), Model()
        self.draw(conn, model, 100, 100, 50, 50, [0xF800] * 2500)
        self.draw(conn, model, 120, 120, 50, 50, [0x001F] * 2500)
        self.sync(conn)
        self.assertEqual(self.framebuffer(), bytes(model.buf))

    def test_rejected_rect_draws_nothing(self):
        conn = self.connect()
        conn.send(rect(W - 1, H - 1, 2, 2, 0, pack565([0xFFFF] * 4)))
        self.assertTrue(conn.closed_by_peer())
        self.sync(self.connect())
        self.assertEqual(self.framebuffer(), bytes(W * H * 4))


class Devices(DaemonCase):
    def prepare(self):
        self.backlight = self.dir / "brightness"
        self.backlight.write_text("0\n")
        self.battery = self.dir / "battery"
        self.battery.mkdir()
        (self.battery / "capacity").write_text("57\n")
        (self.battery / "status").write_text("Charging\n")
        (self.battery / "temp").write_text("-25\n")

        self.counter = self.dir / "unconfirmed"
        self.counter.write_text("3\n")

    def args(self):
        return ["--backlight", str(self.backlight), "--battery", str(self.battery),
                "--confirm", str(self.counter)]

    def test_boot_counter_is_reset_by_the_first_picture_only(self):
        conn = self.connect()
        conn.send(frame(BACKLIGHT, bytes([1])))
        conn.send(struct.pack("<BI", 0x05, 4) + bytes(4))      # PING is not a picture
        conn.recv_type(0x46)
        self.assertEqual(self.counter.read_text(), "3\n")
        self.sync(conn)                                        # PRESENT
        self.assertEqual(self.counter.read_text(), "0\n")
        self.counter.write_text("2\n")                         # as the next boot would
        self.sync(conn, 2)
        self.assertEqual(self.counter.read_text(), "2\n")

    def test_backlight_is_written(self):
        conn = self.connect()
        conn.send(frame(BACKLIGHT, bytes([200])))
        self.sync(conn)
        self.assertEqual(self.backlight.read_text().strip(), "200")

    def test_status_after_connect(self):
        conn = self.connect()
        payload = conn.recv_type(STATUS)
        self.assertEqual(struct.unpack("<BBh", payload), (57, 1, -25))


if __name__ == "__main__":
    unittest.main()
