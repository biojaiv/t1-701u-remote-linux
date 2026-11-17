"""Tile encoder: what the tablet shows must equal the frame, byte for byte."""
import pathlib
import struct
import sys
import unittest

import lz4.block
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import encode, link  # noqa: E402

from harness import H, RECT, W, DaemonCase, frame  # noqa: E402


def apply(screen, payloads):
    """Independent re-implementation of what tabletd does with RECT payloads."""
    for payload in payloads:
        x, y, w, h, fmt = struct.unpack("<HHHHB", payload[:9])
        data = payload[9:]
        if fmt == link.FMT_RGB565_LZ4:
            data = lz4.block.decompress(data, uncompressed_size=w * h * 2)
        assert len(data) == w * h * 2
        screen[y:y + h, x:x + w] = np.frombuffer(data, dtype="<u2").reshape(h, w)


def area_of(payloads):
    return sum(struct.unpack("<HHHHB", p[:9])[2] * struct.unpack("<HHHHB", p[:9])[3]
               for p in payloads)


class Tiles(unittest.TestCase):
    def test_merge_covers_every_changed_tile_once(self):
        rng = np.random.default_rng(1)
        for share in (0.02, 0.1, 0.3, 0.5, 0.9):
            mask = rng.random((16, 10)) < share
            covered = np.zeros_like(mask, dtype=int)
            for row, col, rows, cols in encode.merge_tiles(mask):
                covered[row:row + rows, col:col + cols] += 1
            self.assertTrue(np.array_equal(covered, mask.astype(int)))

    def test_block_of_tiles_becomes_one_rect(self):
        mask = np.zeros((16, 10), dtype=bool)
        mask[3:7, 2:5] = True
        self.assertEqual(encode.merge_tiles(mask), [(3, 2, 4, 3)])

    def test_changed_tiles_finds_single_pixels(self):
        old = np.zeros((H, W), dtype=np.uint16)
        for y, x in ((0, 0), (63, 59), (64, 60), (H - 1, W - 1), (500, 301)):
            new = old.copy()
            new[y, x] = 1
            mask = encode.changed_tiles(old, new)
            self.assertEqual(mask.sum(), 1)
            self.assertTrue(mask[y // 64, x // 60])


class Reconstruction(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(2)
        self.enc = encode.Encoder(W, H)
        self.screen = np.full((H, W), 0xDEAD, dtype=np.uint16)   # unknown content

    def step(self, new):
        payloads = self.enc.encode(new)
        apply(self.screen, payloads)
        self.enc.confirm()
        self.assertTrue(np.array_equal(self.screen, new))
        return payloads

    def test_first_frame_is_sent_completely(self):
        payloads = self.step(self.rng.integers(0, 65536, (H, W), dtype=np.uint16))
        self.assertEqual(area_of(payloads), W * H)

    def test_unchanged_frame_sends_nothing(self):
        first = np.zeros((H, W), dtype=np.uint16)
        self.step(first)
        self.assertEqual(self.step(first.copy()), [])

    def test_small_change_sends_one_tile(self):
        base = np.zeros((H, W), dtype=np.uint16)
        self.step(base)
        new = base.copy()
        new[130, 70] = 0xFFFF
        payloads = self.step(new)
        self.assertEqual(len(payloads), 1)
        self.assertEqual(struct.unpack("<HHHHB", payloads[0][:9])[:4], (60, 128, 60, 64))

    def test_large_change_sends_full_frame(self):
        base = np.zeros((H, W), dtype=np.uint16)
        self.step(base)
        new = base.copy()
        new[:700, :] = 7
        payloads = self.step(new)
        self.assertEqual(len(payloads), 1)
        self.assertEqual(area_of(payloads), W * H)

    def test_random_sequences(self):
        current = self.rng.integers(0, 65536, (H, W), dtype=np.uint16)
        self.step(current)
        for _ in range(40):
            new = current.copy()
            for _ in range(int(self.rng.integers(1, 6))):
                x, y = int(self.rng.integers(0, W)), int(self.rng.integers(0, H))
                w, h = int(self.rng.integers(1, 250)), int(self.rng.integers(1, 250))
                if self.rng.random() < 0.5:
                    new[y:y + h, x:x + w] = self.rng.integers(0, 65536)
                else:
                    block = new[y:y + h, x:x + w]
                    block[...] = self.rng.integers(0, 65536, block.shape, dtype=np.uint16)
            self.step(new)
            current = new

    def test_unconfirmed_frame_may_or_may_not_have_been_drawn(self):
        base = np.zeros((H, W), dtype=np.uint16)
        lost = base.copy()
        lost[0, 0] = 1
        lost[700, 300] = 2
        back = base.copy()             # equals the confirmed frame again
        other = base.copy()
        other[1000, 500] = 3
        for target in (back, other, lost):
            for drawn in (False, True):
                with self.subTest(drawn=drawn):
                    self.enc.reset()
                    self.step(base)
                    payloads = self.enc.encode(lost)   # sent, never confirmed
                    if drawn:
                        apply(self.screen, payloads)
                    apply(self.screen, self.enc.encode(target))
                    self.enc.confirm()
                    self.assertTrue(np.array_equal(self.screen, target))
                    self.assertEqual(self.step(target.copy()), [])

    def test_two_unconfirmed_frames_in_a_row(self):
        base = np.zeros((H, W), dtype=np.uint16)
        first, second = base.copy(), base.copy()
        first[10, 10] = 1
        second[900, 550] = 2
        self.step(base)
        apply(self.screen, self.enc.encode(first))     # drawn, not confirmed
        self.enc.encode(second)                        # not drawn, not confirmed
        apply(self.screen, self.enc.encode(base))
        self.enc.confirm()
        self.assertTrue(np.array_equal(self.screen, base))

    def test_incompressible_data_is_sent_raw(self):
        noise = self.rng.integers(0, 65536, (H, W), dtype=np.uint16)
        payloads = self.step(noise)
        self.assertEqual(payloads[0][8], link.FMT_RGB565)
        flat = np.zeros((H, W), dtype=np.uint16)
        payloads = self.step(flat)
        self.assertEqual(payloads[0][8], link.FMT_RGB565_LZ4)
        self.assertLess(len(payloads[0]), 20000)

    def test_strong_compression_reconstructs_and_is_smaller_for_text(self):
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import bench
        text = bench.scroll_frames(W, H, 1)[0]
        fast = encode.Encoder(W, H).encode(text)
        strong_enc = encode.Encoder(W, H, strong=True)
        strong = strong_enc.encode(text)
        apply(self.screen, strong)
        self.assertTrue(np.array_equal(self.screen, text))
        self.assertLess(sum(map(len, strong)), 0.8 * sum(map(len, fast)))
        noise = self.rng.integers(0, 65536, (H, W), dtype=np.uint16)
        strong_enc.confirm()
        apply(self.screen, strong_enc.encode(noise))   # incompressible: sent raw
        self.assertTrue(np.array_equal(self.screen, noise))

    def test_reset_forces_full_frame(self):
        base = np.zeros((H, W), dtype=np.uint16)
        self.step(base)
        self.enc.reset()
        self.assertEqual(area_of(self.enc.encode(base)), W * H)


class Ordered(unittest.TestCase):
    """ordered=True: compared with the last frame sent, frames applied in order."""

    def test_sequence_without_waiting_for_acknowledgements(self):
        rng = np.random.default_rng(5)
        enc = encode.Encoder(W, H, ordered=True)
        screen = np.full((H, W), 0xDEAD, dtype=np.uint16)
        current = rng.integers(0, 65536, (H, W), dtype=np.uint16)
        apply(screen, enc.encode(current))
        for _ in range(30):
            new = current.copy()
            x, y = int(rng.integers(0, W - 100)), int(rng.integers(0, H - 100))
            new[y:y + 100, x:x + 100] = rng.integers(0, 65536)
            apply(screen, enc.encode(new))        # no confirm() in between
            self.assertTrue(np.array_equal(screen, new))
            current = new
        self.assertEqual(enc.encode(current.copy()), [])


class ThroughTabletd(DaemonCase):
    """The same, but drawn by the real tabletd into its file framebuffer."""

    def expected(self, frame565):
        r5, g6, b5 = frame565 >> 11, (frame565 >> 5) & 0x3F, frame565 & 0x1F
        out = np.empty((H, W, 4), dtype=np.uint8)
        out[..., 0] = r5 << 3 | r5 >> 2
        out[..., 1] = g6 << 2 | g6 >> 4
        out[..., 2] = b5 << 3 | b5 >> 2
        out[..., 3] = 0xFF
        return out.tobytes()

    def test_sequence(self):
        rng = np.random.default_rng(3)
        conn, enc = self.connect(), encode.Encoder(W, H)
        current = np.zeros((H, W), dtype=np.uint16)
        for seq in range(1, 13):
            new = current.copy()
            x, y = int(rng.integers(0, W - 80)), int(rng.integers(0, H - 80))
            new[y:y + 80, x:x + 80] = rng.integers(0, 65536, (80, 80), dtype=np.uint16)
            if seq == 6:
                new[:] = 0x07E0
            for payload in enc.encode(new):
                conn.send(frame(RECT, payload))
            self.sync(conn, seq)
            enc.confirm()
            self.assertEqual(self.framebuffer(), self.expected(new), f"frame {seq}")
            current = new


class Moves(unittest.TestCase):
    """Scrolling: the encoder sends COPY plus the newly visible strip."""

    def scene(self, seed=3):
        rng = np.random.default_rng(seed)
        return rng.integers(0, 65536, (H, W), dtype=np.uint16)

    def replay(self, parts, base):
        """What the tablet shows after these parts, starting from base."""
        shown = base.copy()
        for part in parts:
            if isinstance(part, tuple):
                kind, payload = part
                self.assertEqual(kind, link.COPY)
                encode.apply_move(shown, struct.unpack("<6H", payload))
            else:
                x, y, w, h, fmt = struct.unpack("<HHHHB", part[:9])
                data = part[9:]
                raw = data if fmt == link.FMT_RGB565 else lz4.block.decompress(
                    data, uncompressed_size=w * h * 2)
                shown[y:y + h, x:x + w] = np.frombuffer(raw, "<u2").reshape(h, w)
        return shown

    def check_scroll(self, axis, shift):
        old = self.scene()
        new = np.roll(old, shift, axis=1 if axis == "x" else 0)
        # a fixed border that does not scroll (window frame, panel)
        new[:, :30] = old[:, :30]
        enc = encode.Encoder(W, H, ordered=True, copy=True)
        enc.encode(old)
        parts = enc.encode(new)
        self.assertTrue(any(isinstance(p, tuple) for p in parts), "no COPY sent")
        np.testing.assert_array_equal(self.replay(parts, old), new)
        sent_bytes = sum(len(p[1]) if isinstance(p, tuple) else len(p) for p in parts)
        self.assertLess(sent_bytes, 0.5 * W * H * 2, "COPY saved too little")

    def test_columns_move_right(self):
        self.check_scroll("x", 40)

    def test_columns_move_left(self):
        self.check_scroll("x", -40)

    def test_rows_move_down(self):
        self.check_scroll("y", 64)

    def test_rows_move_up(self):
        self.check_scroll("y", -64)

    def test_text_page_with_scroll_bar_that_moves_differently(self):
        # white page with lines of "text"; in framebuffer coordinates the
        # laptop's vertical scrolling moves columns; the scroll bar is a strip
        # of rows whose thumb moves on its own
        rng = np.random.default_rng(5)
        page = np.full((H, 4 * W), 0xFFFF, dtype=np.uint16)
        for x in range(0, 4 * W - 14, 22):
            page[40:H - 60, x:x + 14] = rng.integers(0, 65535, (H - 100, 14), dtype=np.uint16)
        def view(offset, thumb):
            frame = page[:, offset:offset + W].copy()
            frame[H - 30:, :] = 0x8410               # scroll bar track
            frame[H - 30:, thumb:thumb + 80] = 0x2104  # thumb
            frame[:, :24] = 0x0000                    # window edge, fixed
            return frame
        old, new = view(300, 100), view(337, 112)
        enc = encode.Encoder(W, H, ordered=True, copy=True)
        enc.encode(old)
        parts = enc.encode(new)
        self.assertTrue(any(isinstance(p, tuple) for p in parts), "no COPY sent")
        np.testing.assert_array_equal(self.replay(parts, old), new)

    def test_no_copy_without_permission(self):
        old = self.scene()
        new = np.roll(old, 40, axis=1)
        enc = encode.Encoder(W, H, ordered=True)
        enc.encode(old)
        self.assertFalse(any(isinstance(p, tuple) for p in enc.encode(new)))

    def test_unrelated_change_sends_no_copy_and_stays_exact(self):
        old = self.scene(1)
        new = self.scene(2)
        enc = encode.Encoder(W, H, ordered=True, copy=True)
        enc.encode(old)
        parts = enc.encode(new)
        self.assertFalse(any(isinstance(p, tuple) for p in parts))
        np.testing.assert_array_equal(self.replay(parts, old), new)

    def test_next_frame_builds_on_the_moved_picture(self):
        old = self.scene()
        mid = np.roll(old, 40, axis=1)
        last = mid.copy()
        last[100:120, 200:260] = 7
        enc = encode.Encoder(W, H, ordered=True, copy=True)
        enc.encode(old)
        shown = self.replay(enc.encode(mid), old)
        shown = self.replay(enc.encode(last), shown)
        np.testing.assert_array_equal(shown, last)


class ScaledScrolling(unittest.TestCase):
    """Scrolling in a picture scaled by a factor that is not a whole number
    (KDE 1745x1091 -> tablet): approximate COPY while it moves, exact pixels
    once it stops."""

    @staticmethod
    def frames():
        from PIL import Image
        rng = np.random.default_rng(11)
        src_w, src_h, out_w, out_h = 436, 1745, 240, 960     # factor 1745/960
        page = np.full((src_h * 3, src_w, 3), 245, dtype=np.uint8)
        for y in range(20, src_h * 3 - 30, 26):              # lines of "text"
            for x in range(16, src_w - 60, 70):
                page[y:y + 14, x:x + rng.integers(20, 60)] = rng.integers(0, 120, 3)
        out = []
        for offset in list(range(0, 37 * 12, 37)) + [37 * 11] * 2:   # scroll, then stop
            img = Image.fromarray(page[offset:offset + src_h]).resize((out_w, out_h),
                                                                    Image.BILINEAR)
            rgb = np.asarray(img, dtype=np.uint16)
            out.append(((rgb[..., 0] >> 3) << 11 | (rgb[..., 1] >> 2) << 5 | rgb[..., 2] >> 3)
                       .astype(np.uint16))
        return out

    def run_encoder(self, copy):
        frames = self.frames()
        h, w = frames[0].shape
        enc = encode.Encoder(w, h, strong=True, ordered=True, copy=copy)
        shown = None
        total = 0
        for frame in frames:
            parts = enc.encode(frame)
            total += sum(len(p[1]) if isinstance(p, tuple) else len(p) for p in parts)
            shown = Moves.replay(self, parts, shown if shown is not None
                                 else np.zeros_like(frame))
        return frames, shown, total, enc.moves

    def test_exact_picture_after_the_scrolling_stops(self):
        frames, shown, _, moves = self.run_encoder(copy=True)
        self.assertGreater(moves, 5, "approximate moves were not used")
        np.testing.assert_array_equal(shown, frames[-1])

    def test_fewer_bytes_while_scrolling(self):
        _, _, with_copy, _ = self.run_encoder(copy=True)
        _, _, without, _ = self.run_encoder(copy=False)
        self.assertLess(with_copy, 0.7 * without, f"{with_copy} vs {without} bytes")


if __name__ == "__main__":
    unittest.main()
