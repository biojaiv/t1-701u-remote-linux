"""On-screen keyboard of hostd: layout, hit test, layers, keysyms, drawing."""
import pathlib
import sys
import unittest

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import keyboard  # noqa: E402
from host.keyboard import LAYERS, Keyboard  # noqa: E402

W, H = 1024, 600


def centre(kb, label, layer=None):
    for name, _, x0, y0, x1, y1 in kb.keys(layer):
        if name == label:
            return (x0 + x1) // 2, (y0 + y1) // 2
    raise KeyError(label)


class Layout(unittest.TestCase):
    def test_every_layer_fills_the_width_without_gaps_or_overlaps(self):
        kb = Keyboard(W, H)
        for layer in LAYERS:
            rows = {}
            for label, what, x0, y0, x1, y1 in kb.keys(layer):
                self.assertTrue(label)
                self.assertGreater(x1 - x0, 40, f"{label} too narrow for a finger")
                self.assertTrue(kb.top <= y0 < y1 <= H)
                rows.setdefault(y0, []).append((x0, x1))
            self.assertEqual(len(rows), 5)
            for spans in rows.values():
                self.assertEqual(spans[0][0], 0)
                self.assertEqual(spans[-1][1], W)
                for (_, end), (start, _) in zip(spans, spans[1:]):
                    self.assertEqual(end, start)

    def test_keysyms(self):
        self.assertEqual(keyboard.keysym("a"), 0x61)
        self.assertEqual(keyboard.keysym("ü"), 0xFC)
        self.assertEqual(keyboard.keysym("ß"), 0xDF)
        self.assertEqual(keyboard.keysym("€"), 0x20AC)
        self.assertEqual(keyboard.keysym("→"), 0x01002192)
        self.assertEqual(keyboard.keysym(" "), 0x20)


class Behaviour(unittest.TestCase):
    def setUp(self):
        self.kb = Keyboard(W, H)
        tab = self.kb.tab_rect()
        self.tab = ((tab[0] + tab[2]) // 2, (tab[1] + tab[3]) // 2)

    def show(self):
        self.assertIsNone(self.kb.touch(*self.tab, 0.0))
        self.assertTrue(self.kb.visible)

    def test_hidden_keyboard_takes_only_the_tab(self):
        self.assertFalse(self.kb.covers(500, 550))
        self.assertIsNone(self.kb.touch(500, 550, 0.0))
        self.assertTrue(self.kb.covers(*self.tab))

    def test_tab_shows_and_hides(self):
        self.show()
        self.assertTrue(self.kb.covers(500, 550))
        self.assertFalse(self.kb.covers(500, 100))
        self.kb.touch(*self.tab, 1.0)
        self.assertFalse(self.kb.visible)

    def test_letters_digits_and_special_keys(self):
        self.show()
        self.assertEqual(self.kb.touch(*centre(self.kb, "a"), 1.0), 0x61)
        self.assertEqual(self.kb.touch(*centre(self.kb, "7"), 1.0), 0x37)
        self.assertEqual(self.kb.touch(*centre(self.kb, "ö"), 1.0), 0xF6)
        self.assertEqual(self.kb.touch(*centre(self.kb, "␣"), 1.0), 0x20)
        self.assertEqual(self.kb.touch(*centre(self.kb, "⌫"), 1.0), keyboard.BACKSPACE)
        self.assertEqual(self.kb.touch(*centre(self.kb, "⏎"), 1.0), keyboard.RETURN)
        self.assertEqual(self.kb.touch(*centre(self.kb, "←"), 1.0), keyboard.LEFT)

    def test_shift_holds_for_one_key(self):
        self.show()
        self.assertIsNone(self.kb.touch(*centre(self.kb, "⇧"), 1.0))
        self.assertEqual(self.kb.layer, "upper")
        self.assertEqual(self.kb.touch(*centre(self.kb, "A"), 1.1), 0x41)
        self.assertEqual(self.kb.layer, "lower")
        self.assertEqual(self.kb.touch(*centre(self.kb, "a"), 1.2), 0x61)

    def test_shift_twice_goes_back(self):
        self.show()
        self.kb.touch(*centre(self.kb, "⇧"), 1.0)
        self.kb.touch(*centre(self.kb, "⇧"), 1.1)
        self.assertEqual(self.kb.layer, "lower")

    def test_symbol_layer_stays_until_left(self):
        self.show()
        self.kb.touch(*centre(self.kb, "#+="), 1.0)
        self.assertEqual(self.kb.touch(*centre(self.kb, "@"), 1.1), 0x40)
        self.assertEqual(self.kb.touch(*centre(self.kb, "€"), 1.2), 0x20AC)
        self.assertEqual(self.kb.layer, "symbols")
        self.kb.touch(*centre(self.kb, "abc"), 1.3)
        self.assertEqual(self.kb.layer, "lower")

    def test_hide_key(self):
        self.show()
        self.assertIsNone(self.kb.touch(*centre(self.kb, "▼"), 1.0))
        self.assertFalse(self.kb.visible)

    def test_pressed_key_flashes_and_expires(self):
        self.show()
        self.kb.touch(*centre(self.kb, "a"), 1.0)
        self.assertIsNotNone(self.kb.pressed)
        self.assertFalse(self.kb.expire(1.05))
        self.assertTrue(self.kb.expire(1.2))
        self.assertIsNone(self.kb.pressed)
        self.assertFalse(self.kb.expire(1.3))


class Drawing(unittest.TestCase):
    def test_hidden_keyboard_draws_only_the_tab(self):
        kb = Keyboard(W, H)
        canvas = np.zeros((H, W), dtype=np.uint16)
        kb.draw(canvas)
        x0, y0, x1, y1 = kb.tab_rect()
        self.assertTrue(canvas[y0:y1, x0:x1].any())
        canvas[y0:y1, x0:x1] = 0
        self.assertFalse(canvas.any())

    def test_visible_keyboard_covers_the_bottom_and_shows_the_pressed_key(self):
        kb = Keyboard(W, H)
        kb.visible = True
        plain = np.zeros((H, W), dtype=np.uint16)
        kb.draw(plain)
        self.assertTrue(plain[kb.top:, :].all())
        self.assertFalse(plain[:kb.top, :W - 40].any())
        kb.touch(*centre(kb, "g"), 1.0)
        pressed = np.zeros((H, W), dtype=np.uint16)
        kb.draw(pressed)
        ys, xs = np.nonzero(pressed != plain)
        x, y = centre(kb, "g")
        self.assertTrue(abs(int(xs.mean()) - x) < 10 and abs(int(ys.mean()) - y) < 10)

    def test_all_layers_render(self):
        kb = Keyboard(W, H)
        kb.visible = True
        for layer in LAYERS:
            kb.layer = layer
            canvas = np.zeros((H, W), dtype=np.uint16)
            kb.draw(canvas)
            self.assertTrue(canvas[kb.top:, :].all())



class Repeat(unittest.TestCase):
    def test_backspace_repeats_after_the_delay_while_held(self):
        r = keyboard.KeyRepeat(delay=0.45, every=0.07)
        r.start(3, keyboard.BACKSPACE, 10.0)
        self.assertIsNone(r.due(10.2))
        self.assertEqual(r.due(10.45), keyboard.BACKSPACE)
        self.assertIsNone(r.due(10.50))
        self.assertEqual(r.due(10.52), keyboard.BACKSPACE)

    def test_lifting_that_finger_stops_it(self):
        r = keyboard.KeyRepeat()
        r.start(3, keyboard.BACKSPACE, 0.0)
        r.stop(4)                          # another finger: no effect
        self.assertEqual(r.due(1.0), keyboard.BACKSPACE)
        r.stop(3)
        self.assertIsNone(r.due(2.0))

    def test_letters_do_not_repeat(self):
        r = keyboard.KeyRepeat()
        r.start(0, keyboard.keysym("a"), 0.0)
        self.assertIsNone(r.due(5.0))

    def test_arrows_repeat(self):
        for key in (keyboard.LEFT, keyboard.RIGHT, keyboard.UP, keyboard.DOWN):
            r = keyboard.KeyRepeat()
            r.start(0, key, 0.0)
            self.assertEqual(r.due(1.0), key)

    def test_no_burst_after_a_stall(self):
        r = keyboard.KeyRepeat(delay=0.45, every=0.07)
        r.start(0, keyboard.BACKSPACE, 0.0)
        self.assertEqual(r.due(3.0), keyboard.BACKSPACE)   # 2.5 s late: one repeat
        self.assertIsNone(r.due(3.01))


if __name__ == "__main__":
    unittest.main()


class SmallScreen(unittest.TestCase):
    """A client that reports a screen smaller than the keyboard (64x64)
    made hostd's pump raise and stop on 2026-11-17."""

    def test_draw_fits_any_canvas(self):
        import numpy as np
        for width, height in ((64, 64), (16, 300), (600, 100), (600, 1024), (1, 1)):
            board = keyboard.Keyboard(width, height)
            for visible in (False, True):
                board.visible = visible
                canvas = np.zeros((height, width), dtype=np.uint16)
                board.draw(canvas)
                self.assertEqual(canvas.shape, (height, width))
