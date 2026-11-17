"""Monitor and macro screens (step P3.8): rendering, hit test, macro file."""
import pathlib
import sys
import tempfile
import time
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import modes, theme  # noqa: E402
from host.pointer import Mapper  # noqa: E402

CANVAS = (1024, 600)


class MacroFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "macros.toml"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_gives_defaults(self):
        self.assertEqual(modes.load_macros(self.path), modes.DEFAULT_MACROS)

    def test_file_is_read(self):
        self.path.write_text('[[macro]]\nlabel = "A"\ncommand = ["true"]\n'
                             '[[macro]]\nlabel = "B"\ncommand = ["echo", "x y"]\n')
        self.assertEqual(modes.load_macros(self.path),
                         [("A", ["true"]), ("B", ["echo", "x y"])])

    def test_at_most_eight(self):
        self.path.write_text("".join(f'[[macro]]\nlabel = "M{i}"\ncommand = ["true"]\n'
                                     for i in range(12)))
        self.assertEqual(len(modes.load_macros(self.path)), 8)

    def test_broken_files_are_rejected(self):
        for text in ("this is not toml = = =", "[[macro]]\nlabel = 'A'\n",
                     "[[macro]]\nlabel = 'A'\ncommand = 'rm -rf x'\n",
                     "[[macro]]\nlabel = 'A'\ncommand = []\n", "title = 'nothing'\n"):
            self.path.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                modes.load_macros(self.path)


class Screens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.marker = pathlib.Path(self.tmp.name) / "ran"
        self.macros = [("One", ["touch", str(self.marker)]), ("Two", ["true"]),
                       ("Three", ["true"]), ("Four", ["true"]), ("Five", ["true"])]

    def tearDown(self):
        self.tmp.cleanup()

    def wait_for_marker(self):
        deadline = time.monotonic() + 3
        while not self.marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.marker.exists()

    def test_macro_screen_renders_and_runs_the_tapped_macro(self):
        screen = modes.MacroScreen(self.macros, CANVAS)
        image = screen.render()
        self.assertEqual(image.size, CANVAS)
        rects = screen.rects()
        self.assertEqual(len(rects), 5)
        for x0, y0, x1, y1 in rects:
            self.assertTrue(0 <= x0 < x1 <= CANVAS[0] and 0 <= y0 < y1 <= CANVAS[1])
        x0, y0, x1, y1 = rects[0]
        self.assertEqual(screen.tap((x0 + x1) // 2, (y0 + y1) // 2, now=10.0), 0)
        self.assertTrue(self.wait_for_marker())
        self.assertEqual(screen.current_pressed(now=10.1), 0)
        self.assertIsNone(screen.current_pressed(now=10.6))

    def test_tap_between_buttons_runs_nothing(self):
        screen = modes.MacroScreen(self.macros, CANVAS)
        self.assertIsNone(screen.tap(3, 3, now=1.0))
        time.sleep(0.1)
        self.assertFalse(self.marker.exists())

    def test_monitor_screen_uses_first_four_macros(self):
        screen = modes.MonitorScreen(self.macros)
        image = screen.render()
        self.assertEqual(image.size, CANVAS)
        self.assertEqual(len(screen.rects()), 4)
        x0, y0, x1, y1 = screen.rects()[0]
        self.assertEqual(screen.tap((x0 + x1) // 2, (y0 + y1) // 2), 0)
        self.assertTrue(self.wait_for_marker())

    def test_a_macro_that_cannot_start_does_not_raise(self):
        self.assertFalse(modes.run_macro(["/nonexistent/program"]))


class Rotation(unittest.TestCase):
    def test_frame_layout_matches_the_touch_mapping(self):
        """A pixel drawn at a canvas point must be found where Mapper says."""
        image = Image.new("RGB", CANVAS, "black")
        image.putpixel((700, 123), (255, 255, 255))
        for rotation in ("counterclockwise", "clockwise"):
            frame = modes.to_frame(image, rotation)
            self.assertEqual(frame.shape, (1024, 600))
            self.assertEqual(frame.dtype, np.uint16)
            ys, xs = np.nonzero(frame)
            self.assertEqual(len(xs), 1)
            mapper = Mapper((600, 1024), rotation, CANVAS, CANVAS)
            self.assertEqual(mapper.to_canvas(int(xs[0]), int(ys[0])), (700, 123))


if __name__ == "__main__":
    unittest.main()


class BackButton(unittest.TestCase):
    """The "Back" button in the menus' header (only when the tablet
    understands HOME): a tap on it returns BACK and runs no macro."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.marker = pathlib.Path(self.tmp.name) / "ran"
        self.macros = [("One", ["touch", str(self.marker)])] * 8

    def tearDown(self):
        self.tmp.cleanup()

    def centre(self):
        x0, y0, x1, y1 = theme.BACK_BOX
        return (x0 + x1) // 2, (y0 + y1) // 2

    def test_tap_on_back(self):
        for screen in (modes.MacroScreen(self.macros, CANVAS), modes.MonitorScreen(self.macros)):
            screen.back_enabled = True
            self.assertEqual(screen.tap(*self.centre()), modes.BACK)
            self.assertEqual(screen.current_pressed(), modes.BACK)
        time.sleep(0.2)
        self.assertFalse(self.marker.exists())

    def test_no_button_without_home(self):
        screen = modes.MacroScreen(self.macros, CANVAS)
        self.assertIsNone(screen.tap(*self.centre()))

    def test_button_is_drawn(self):
        screen = modes.MacroScreen(self.macros, CANVAS)
        plain = np.asarray(screen.render())
        screen.back_enabled = True
        with_back = np.asarray(screen.render())
        x0, y0, x1, y1 = theme.BACK_BOX
        self.assertFalse(np.array_equal(plain[y0:y1, x0:x1], with_back[y0:y1, x0:x1]))
