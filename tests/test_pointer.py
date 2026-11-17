"""Coordinate mapping (plan section 6.8) and touch gestures (steps P3.5, P3.7)."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host.pointer import (BTN_LEFT, BTN_RIGHT, DOWN, MOVE, UP, Gestures,  # noqa: E402
                          Mapper, View)

FB = (600, 1024)
SOURCE = (1920, 1200)
STREAM = (1746, 1091)


class Mapping(unittest.TestCase):
    def test_picture_is_960_wide_with_32_pixel_borders(self):
        mapper = Mapper(FB, "counterclockwise", SOURCE, STREAM)
        self.assertEqual((mapper.pic_w, mapper.pic_h, mapper.left, mapper.top),
                         (960, 600, 32, 0))

    def test_counterclockwise_formulas_from_the_plan(self):
        mapper = Mapper(FB, "counterclockwise", SOURCE, STREAM)
        # x_c = 1023 - y_f, y_c = x_f
        self.assertEqual(mapper.to_canvas(0, 1023), (0, 0))
        self.assertEqual(mapper.to_canvas(0, 0), (1023, 0))
        self.assertEqual(mapper.to_canvas(599, 1023), (0, 599))
        self.assertEqual(mapper.to_canvas(123, 456), (1023 - 456, 123))

    def test_clockwise_formulas_from_the_plan(self):
        mapper = Mapper(FB, "clockwise", SOURCE, STREAM)
        # x_c = y_f, y_c = 599 - x_f
        self.assertEqual(mapper.to_canvas(599, 0), (0, 0))
        self.assertEqual(mapper.to_canvas(0, 0), (0, 599))
        self.assertEqual(mapper.to_canvas(123, 456), (456, 599 - 123))

    def test_borders_are_rejected(self):
        mapper = Mapper(FB, "counterclockwise", SOURCE, STREAM)
        self.assertIsNone(mapper.to_stream(300, 1023))        # x_c = 0
        self.assertIsNone(mapper.to_stream(300, 1023 - 31))   # x_c = 31
        self.assertIsNotNone(mapper.to_stream(300, 1023 - 32))
        self.assertIsNotNone(mapper.to_stream(300, 1023 - 991))
        self.assertIsNone(mapper.to_stream(300, 1023 - 992))

    def test_stream_coordinates_use_the_reported_size(self):
        mapper = Mapper(FB, "counterclockwise", SOURCE, STREAM)
        x, y = mapper.to_stream(0, 1023 - 32)                 # picture top left
        self.assertEqual((x, y), (0.0, 0.0))
        x, y = mapper.to_stream(300, 1023 - 32 - 480)         # picture centre
        self.assertAlmostEqual(x, 1746 / 2)
        self.assertAlmostEqual(y, 1091 / 2)
        x, y = mapper.to_stream(599, 1023 - 991)              # bottom right pixel
        self.assertAlmostEqual(x, 959 / 960 * 1746)
        self.assertAlmostEqual(y, 599 / 600 * 1091)
        self.assertLess(x, 1746)
        self.assertLess(y, 1091)

    def test_other_aspect_ratio_gets_top_and_bottom_borders(self):
        mapper = Mapper(FB, "counterclockwise", (2560, 1080), (2560, 1080))
        self.assertEqual((mapper.pic_w, mapper.pic_h, mapper.left, mapper.top),
                         (1024, 432, 0, 84))


class Recorder:
    def __init__(self):
        self.log = []

    def move(self, x, y):
        self.log.append(("move", round(x), round(y)))

    def button(self, code, pressed):
        self.log.append(("button", code, pressed))

    def scroll(self, steps):
        self.log.append(("scroll", steps))



def fb(x_c, y_c):
    """Framebuffer position of a canvas point (counterclockwise)."""
    return y_c, 1023 - x_c


class GestureCase(unittest.TestCase):
    def setUp(self):
        self.rec = Recorder()
        # stream size equal to the picture size: stream coordinates = canvas - border
        self.g = Gestures(Mapper(FB, "counterclockwise", (960, 600), (960, 600)), self.rec)

    def send(self, slot, state, x_c, y_c, now):
        self.g.touch(slot, state, *fb(x_c, y_c), now)

    def test_tap_is_a_left_click(self):
        self.send(0, DOWN, 132, 100, 0.0)
        self.send(0, UP, 132, 100, 0.1)
        self.assertEqual(self.rec.log, [("move", 100, 100),
                                        ("button", BTN_LEFT, True),
                                        ("button", BTN_LEFT, False)])

    def test_small_wobble_is_still_a_tap(self):
        self.send(0, DOWN, 132, 100, 0.0)
        self.send(0, MOVE, 137, 104, 0.05)
        self.send(0, UP, 137, 104, 0.1)
        self.assertEqual([e for e in self.rec.log if e[0] == "button"],
                         [("button", BTN_LEFT, True), ("button", BTN_LEFT, False)])

    def test_drag_presses_at_the_start_and_releases_at_the_end(self):
        self.send(0, DOWN, 132, 100, 0.0)
        self.send(0, MOVE, 232, 100, 0.1)
        self.send(0, MOVE, 332, 150, 0.2)
        self.send(0, UP, 332, 150, 0.3)
        self.assertEqual(self.rec.log, [
            ("move", 100, 100), ("button", BTN_LEFT, True), ("move", 200, 100),
            ("move", 300, 150), ("move", 300, 150), ("button", BTN_LEFT, False)])

    def test_long_press_is_a_right_click_without_left_click(self):
        self.send(0, DOWN, 132, 100, 0.0)
        self.g.tick(0.3)
        self.assertEqual(len(self.rec.log), 1)
        self.g.tick(0.61)
        self.send(0, MOVE, 300, 300, 0.8)
        self.send(0, UP, 300, 300, 0.9)
        self.assertEqual(self.rec.log, [("move", 100, 100),
                                        ("button", BTN_RIGHT, True),
                                        ("button", BTN_RIGHT, False)])

    def test_long_drag_is_not_a_right_click(self):
        self.send(0, DOWN, 132, 100, 0.0)
        self.send(0, MOVE, 332, 100, 0.2)
        self.g.tick(1.0)
        self.assertNotIn(("button", BTN_RIGHT, True), self.rec.log)

    def test_touch_in_the_border_does_nothing(self):
        self.send(0, DOWN, 10, 100, 0.0)
        self.send(0, MOVE, 500, 100, 0.1)
        self.send(0, UP, 500, 100, 0.2)
        self.assertEqual(self.rec.log, [])

    def test_two_fingers_scroll(self):
        self.send(0, DOWN, 400, 300, 0.0)
        self.send(1, DOWN, 500, 300, 0.05)
        self.rec.log.clear()
        for step in range(1, 5):                   # both fingers 100 px up
            self.send(0, MOVE, 400, 300 - step * 25, 0.1)
            self.send(1, MOVE, 500, 300 - step * 25, 0.1)
        self.assertEqual(sum(e[1] for e in self.rec.log if e[0] == "scroll"), 3)
        self.assertTrue(all(e[0] == "scroll" for e in self.rec.log))
        self.send(0, UP, 400, 200, 0.2)
        self.send(1, MOVE, 500, 100, 0.3)          # leftover finger: ignored
        self.send(1, UP, 500, 100, 0.4)
        self.assertTrue(all(e[0] == "scroll" for e in self.rec.log))
        self.rec.log.clear()
        self.send(0, DOWN, 132, 100, 1.0)          # gestures work again
        self.send(0, UP, 132, 100, 1.1)
        self.assertEqual(len(self.rec.log), 3)

    def test_scrolling_down_gives_the_opposite_sign(self):
        self.send(0, DOWN, 400, 300, 0.0)
        self.send(1, DOWN, 500, 300, 0.0)
        self.send(0, MOVE, 400, 400, 0.1)
        self.send(1, MOVE, 500, 400, 0.1)
        self.assertEqual(sum(e[1] for e in self.rec.log if e[0] == "scroll"), -3)

    def test_second_finger_during_drag_releases_the_button(self):
        self.send(0, DOWN, 132, 100, 0.0)
        self.send(0, MOVE, 300, 100, 0.1)
        self.send(1, DOWN, 500, 300, 0.2)
        self.assertEqual(self.rec.log[-1], ("button", BTN_LEFT, False))

    def test_reset_releases_a_held_button(self):
        self.send(0, DOWN, 132, 100, 0.0)
        self.send(0, MOVE, 300, 100, 0.1)
        self.g.reset()
        self.assertEqual(self.rec.log[-1], ("button", BTN_LEFT, False))
        self.g.reset()
        self.assertEqual(self.rec.log.count(("button", BTN_LEFT, False)), 1)


class Zoom(unittest.TestCase):
    """Pinch to zoom, two-finger pan while zoomed (added on the user's request)."""

    def setUp(self):
        self.rec = Recorder()
        self.mapper = Mapper(FB, "counterclockwise", (1920, 1200), (1920, 1200))
        self.changes = []

        def on_change(x, y, w, h):
            self.changes.append((x, y, w, h))
            self.mapper.set_view(x, y, w, h)

        self.view = View((1920, 1200), on_change)
        self.g = Gestures(self.mapper, self.rec, self.view)

    def send(self, slot, state, x_c, y_c, now=0.0):
        self.g.touch(slot, state, *fb(x_c, y_c), now)

    def pinch(self, centre, start, end, steps=10):
        cx, cy = centre
        self.send(0, DOWN, cx - start / 2, cy)
        self.send(1, DOWN, cx + start / 2, cy)
        for i in range(1, steps + 1):
            d = start + (end - start) * i / steps
            self.send(0, MOVE, round(cx - d / 2), cy)
            self.send(1, MOVE, round(cx + d / 2), cy)
        self.send(0, UP, round(cx - end / 2), cy)
        self.send(1, UP, round(cx + end / 2), cy)

    def test_view_starts_with_the_whole_screen(self):
        self.assertEqual(self.view.rect(), (0, 0, 1920, 1200))

    def test_spreading_zooms_in_around_the_fingers(self):
        centre = (32 + 240, 150)                      # a quarter in from the top left
        before = self.mapper.to_source(*centre)
        self.pinch(centre, 100, 200)
        x, y, w, h = self.view.rect()
        self.assertEqual((w, h), (960, 600))
        after = self.mapper.to_source(*centre)        # same screen point under the fingers
        self.assertAlmostEqual(after[0], before[0], delta=6)
        self.assertAlmostEqual(after[1], before[1], delta=6)
        self.assertEqual(self.changes[-1], (x, y, w, h))
        self.assertFalse(any(e[0] in ("button", "scroll") for e in self.rec.log))

    def test_pinching_back_shows_everything_again(self):
        self.pinch((500, 300), 100, 250)
        self.assertGreater(self.view.zoom, 2)
        self.pinch((500, 300), 250, 90)
        self.assertEqual(self.view.rect(), (0, 0, 1920, 1200))
        self.assertEqual(self.view.zoom, 1.0)

    def test_zoom_is_limited(self):
        self.pinch((500, 300), 50, 900)
        self.assertEqual(self.view.zoom, 4.0)
        self.assertEqual(self.view.rect()[2:], (480, 300))

    def test_view_never_leaves_the_screen(self):
        self.pinch((40, 10), 100, 300)                # zoom at the very corner
        x, y, w, h = self.view.rect()
        self.assertTrue(0 <= x and x + w <= 1920 and 0 <= y and y + h <= 1200)

    def test_touch_hits_the_zoomed_picture(self):
        self.view.set(2.0, 480, 300)                  # the middle of the screen
        self.assertEqual(self.mapper.to_stream(*fb(32, 0)), (480.0, 300.0))
        x, y = self.mapper.to_stream(*fb(32 + 480, 300))
        self.assertAlmostEqual(x, 960)
        self.assertAlmostEqual(y, 600)

    def test_two_fingers_together_pan_while_zoomed(self):
        self.view.set(2.0, 480, 300)
        self.send(0, DOWN, 400, 300)
        self.send(1, DOWN, 500, 300)
        self.send(0, MOVE, 500, 300)                  # both 100 canvas pixels to the right
        self.send(1, MOVE, 600, 300)
        x, y, w, h = self.view.rect()
        self.assertEqual((x, y), (480 - 100, 300))    # content follows the fingers
        self.assertFalse(any(e[0] == "scroll" for e in self.rec.log))

    def test_two_fingers_together_scroll_when_not_zoomed(self):
        self.send(0, DOWN, 400, 300)
        self.send(1, DOWN, 500, 300)
        self.send(0, MOVE, 400, 200)
        self.send(1, MOVE, 500, 200)
        self.assertEqual(sum(e[1] for e in self.rec.log if e[0] == "scroll"), 3)
        self.assertEqual(self.view.rect(), (0, 0, 1920, 1200))

    def test_tap_after_zoom_clicks_the_zoomed_position(self):
        self.view.set(2.0, 480, 300)
        self.send(0, DOWN, 32 + 480, 300, 0.0)
        self.send(0, UP, 32 + 480, 300, 0.1)
        self.assertEqual(self.rec.log[0], ("move", 960, 600))


if __name__ == "__main__":
    unittest.main()
