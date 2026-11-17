"""Capture pipeline geometry, tested with a PNG instead of the PipeWire stream.

The picture has a coloured block in three corners. After scaling, bordering
and turning, each block must be where pointer.Mapper says that corner is,
so that a touch on the tablet lands on the pixel the user sees.
"""
import pathlib
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# GStreamer is a hard requirement of hostd: a failing import must fail the test
# run, not skip it (a skip once hid a missing binding).
from gi.repository import GLib  # noqa: E402

from host import capture  # noqa: E402
from host.pointer import Mapper  # noqa: E402

FB = (600, 1024)
SOURCE = (1920, 1200)
RED, GREEN, BLUE, GREY = 0xF800, 0x07E0, 0x001F, (0x80 >> 3) << 11 | (0x80 >> 2) << 5 | 0x80 >> 3


def fb_point(mapper, x_src, y_src):
    """Framebuffer pixel that shows the given source pixel."""
    x_c = mapper.left + x_src * mapper.pic_w // SOURCE[0]
    y_c = mapper.top + y_src * mapper.pic_h // SOURCE[1]
    if mapper.rotation == "counterclockwise":
        return y_c, mapper.canvas_w - 1 - x_c     # x_f, y_f
    return mapper.canvas_h - 1 - y_c, x_c


class Geometry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.png = pathlib.Path(self.tmp.name) / "screen.png"
        img = Image.new("RGB", SOURCE, (0x80, 0x80, 0x80))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, 199, 199], fill=(255, 0, 0))                       # top left
        draw.rectangle([SOURCE[0] - 200, 0, SOURCE[0] - 1, 199], fill=(0, 255, 0))  # top right
        draw.rectangle([0, SOURCE[1] - 200, 199, SOURCE[1] - 1], fill=(0, 0, 255))  # bottom left
        img.save(self.png)

    def tearDown(self):
        self.tmp.cleanup()

    def grab(self, rotation):
        frames, errors = [], []
        loop = GLib.MainLoop()
        source = f"filesrc location={self.png} ! pngdec ! imagefreeze num-buffers=3"

        def on_frame(frame):
            frames.append(frame)
            loop.quit()

        cap = capture.Capture(source, FB, rotation, on_frame,
                              lambda text: text == capture.STREAM_ENDED or (errors.append(text), loop.quit()))
        cap.start()
        GLib.timeout_add_seconds(10, loop.quit)
        loop.run()
        cap.stop()
        self.assertEqual(errors, [])
        self.assertTrue(frames, "no frame within 10 seconds")
        return frames[0]

    def check(self, rotation):
        frame = self.grab(rotation)
        self.assertEqual(frame.shape, (FB[1], FB[0]))
        self.assertEqual(frame.dtype.itemsize, 2)
        mapper = Mapper(FB, rotation, SOURCE, SOURCE)
        for (x_src, y_src), colour in (((100, 100), RED), ((SOURCE[0] - 100, 100), GREEN),
                                       ((100, SOURCE[1] - 100), BLUE),
                                       ((SOURCE[0] // 2, SOURCE[1] // 2), GREY)):
            x_f, y_f = fb_point(mapper, x_src, y_src)
            self.assertEqual(int(frame[y_f, x_f]), colour,
                             f"source pixel {(x_src, y_src)} at framebuffer {(x_f, y_f)}")
            # and a touch on that framebuffer pixel maps back to that source area
            x, y = mapper.to_stream(x_f, y_f)
            self.assertAlmostEqual(x, x_src, delta=4)
            self.assertAlmostEqual(y, y_src, delta=4)
        return frame, mapper

    def test_counterclockwise(self):
        frame, _ = self.check("counterclockwise")
        # 32 canvas columns of border on each side = 32 framebuffer rows
        self.assertFalse(frame[:32, :].any())
        self.assertFalse(frame[-32:, :].any())
        self.assertTrue(frame[32:-32, :].all())

    def test_viewport_shows_the_chosen_part(self):
        """Zoom: after set_viewport the top left quarter fills the picture."""
        seen, errors = [], []
        loop = GLib.MainLoop()
        source = f"filesrc location={self.png} ! pngdec ! imagefreeze num-buffers=1"
        mapper = Mapper(FB, "counterclockwise", SOURCE, SOURCE)
        # canvas point (32 + 150, 150): unzoomed it shows source (300, 300) = grey,
        # zoomed into the top left quarter it shows source (150, 150) = red
        x_f, y_f = 150, mapper.canvas_w - 1 - (mapper.left + 150)
        state = {"zoomed": False}

        def on_frame(frame):
            colour = int(frame[y_f, x_f])
            seen.append(colour)
            if not state["zoomed"]:
                state["zoomed"] = True          # set_viewport redraws at once
                cap.set_viewport(0, 0, 960, 600)
                loop.quit()

        cap = capture.Capture(source, FB, "counterclockwise", on_frame,
                              lambda text: text == capture.STREAM_ENDED or (errors.append(text), loop.quit()))
        cap.start()
        GLib.timeout_add_seconds(10, loop.quit)
        loop.run()
        cap.stop()
        self.assertEqual(errors, [])
        self.assertEqual(cap.source_px, SOURCE)
        self.assertEqual(seen, [GREY, RED])

    def test_zoom_works_while_the_screen_is_still(self):
        """One single picture from the screen, then a zoom: it must be redrawn."""
        frames = []
        loop = GLib.MainLoop()
        source = f"filesrc location={self.png} ! pngdec ! imagefreeze num-buffers=1"
        cap = capture.Capture(source, FB, "counterclockwise",
                              lambda frame: (frames.append(frame), loop.quit()),
                              lambda text: None)
        cap.start()
        GLib.timeout_add_seconds(10, loop.quit)
        loop.run()
        self.assertEqual(len(frames), 1)
        cap.set_viewport(SOURCE[0] - 480, 0, 480, 300)        # top right corner, zoom 4
        self.assertEqual(len(frames), 2)
        mapper = Mapper(FB, "counterclockwise", SOURCE, SOURCE)
        # the green block (200 px) now fills 400 of the 960 picture columns
        x_f, y_f = 100, mapper.canvas_w - 1 - (mapper.left + 800)
        self.assertEqual(int(frames[1][y_f, x_f]), GREEN)
        self.assertFalse(frames[1][:32, :].any())             # borders stay black
        cap.set_viewport(0, 0, *SOURCE)
        self.assertTrue((frames[2] == frames[0]).all())
        cap.stop()

    def test_viewport_outside_the_screen_is_clamped(self):
        frames = []
        loop = GLib.MainLoop()
        source = f"filesrc location={self.png} ! pngdec ! imagefreeze num-buffers=1"
        cap = capture.Capture(source, FB, "counterclockwise",
                              lambda frame: (frames.append(frame), loop.quit()),
                              lambda text: None)
        cap.start()
        GLib.timeout_add_seconds(10, loop.quit)
        loop.run()
        for rect in ((-50, -50, 100, 100), (5000, 5000, 10, 10), (1900, 1190, 500, 500),
                     (0, 0, 0, 0), (10, 10, -5, -5)):
            cap.set_viewport(*rect)
            self.assertEqual(frames[-1].shape, (FB[1], FB[0]))
        cap.stop()

    def test_clockwise(self):
        frame, _ = self.check("clockwise")
        self.assertFalse(frame[:32, :].any())
        self.assertFalse(frame[-32:, :].any())


if __name__ == "__main__":
    unittest.main()
