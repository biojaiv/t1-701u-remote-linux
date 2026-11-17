"""Screen capture: PipeWire stream -> frames in the tablet's framebuffer layout.

GStreamer delivers the screen at its full size as RGB. Cutting out the
visible part (zoom), scaling it into the landscape canvas with black borders,
turning it by 90 degrees and packing it to RGB565 happens here in Python.

This differs from plan section 6.7, where GStreamer did all of that. The
reason is the zoom: changing a crop element inside the running pipeline
crashed GStreamer (segmentation fault on 2026-11-16). Keeping the last full
picture here also means a zoom can be redrawn at once while the screen is
still; PipeWire only sends a frame when the screen changes.

Frames are numpy arrays of shape (fb_height, fb_width), dtype uint16.
"""
import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

Gst.init(None)

MAX_RATE = 30
STREAM_ENDED = "capture stream ended"


def pipewire_source(fd, node):
    return f"pipewiresrc name=src fd={fd} path={node} do-timestamp=true"


def pipeline_text(source):
    return (f"{source} ! videorate drop-only=true max-rate={MAX_RATE} ! videoconvert ! "
            f"video/x-raw,format=RGB ! "
            f"appsink name=sink max-buffers=1 drop=true sync=false emit-signals=true")


def fit(source_px, canvas):
    """Size and position of the picture inside the canvas: (w, h, left, top)."""
    scale = min(canvas[0] / source_px[0], canvas[1] / source_px[1])
    pic_w = max(1, round(source_px[0] * scale))
    pic_h = max(1, round(source_px[1] * scale))
    return pic_w, pic_h, (canvas[0] - pic_w) // 2, (canvas[1] - pic_h) // 2


class Capture:
    """Runs the pipeline; on_frame(array) is called in the GLib main loop.

    If frames arrive faster than the main loop takes them, only the newest
    one is kept.
    """

    def __init__(self, source, fb_size, rotation, on_frame, on_error=None, eager=True):
        """eager: on_frame(frame) gets every picture, ready for the tablet.
        Otherwise on_frame(None) only says that a new picture is there, and
        the caller fetches it with render() when it can actually send; that
        saves converting pictures the tablet would never see."""
        if rotation not in ("counterclockwise", "clockwise"):
            raise ValueError(f"unknown rotation {rotation!r}")
        self.fb_w, self.fb_h = fb_size
        self.rotation = rotation
        self.on_frame, self.on_error, self.eager = on_frame, on_error, eager
        self.dirty = False         # a picture newer than the last render() exists
        self.source_px = None      # (width, height) of the captured screen
        self.viewport = None       # visible part in source pixels; None = everything
        self.overlay = None        # overlay(canvas) may draw over the picture
        self._full = None          # last full picture, shape (height, width, 3)
        self._pending = None       # picture from the GStreamer thread, not yet taken
        self._scheduled = False
        self.pipeline = Gst.parse_launch(pipeline_text(source))
        self.pipeline.get_by_name("sink").connect("new-sample", self._on_sample)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)
        bus.connect("message::eos", self._on_bus_eos)

    def start(self):
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        self.pipeline.set_state(Gst.State.NULL)

    def set_viewport(self, x, y, width, height):
        """Shows only this part of the screen (source pixels), scaled up.

        Takes effect at once, also while the screen is still.
        """
        self.viewport = (int(x), int(y), int(width), int(height))
        self.refresh()

    def refresh(self):
        """Draws the last picture again (the zoom or the overlay changed)."""
        if self._full is not None:
            self.dirty = True
            self.on_frame(self.render() if self.eager else None)

    def render(self):
        """The last full picture as a frame for the tablet."""
        self.dirty = False
        full = self._full
        src_h, src_w = full.shape[:2]
        x, y, w, h = self.viewport or (0, 0, src_w, src_h)
        x, y = min(max(0, x), src_w - 1), min(max(0, y), src_h - 1)
        w, h = max(1, min(w, src_w - x)), max(1, min(h, src_h - y))
        canvas_w, canvas_h = self.fb_h, self.fb_w
        # the picture area is always the one of the whole screen, so that the
        # borders do not jump while zooming
        pic_w, pic_h, left, top = fit((src_w, src_h), (canvas_w, canvas_h))
        part = Image.fromarray(full[y:y + h, x:x + w])
        if part.size == (2 * pic_w, 2 * pic_h):
            part = part.reduce(2)   # the common case (1920x1200 -> 960x600), 6x faster
        elif part.size != (pic_w, pic_h):
            part = part.resize((pic_w, pic_h), Image.BILINEAR)
        rgb = np.asarray(part, dtype=np.uint16)
        canvas = np.zeros((canvas_h, canvas_w), dtype=np.uint16)
        canvas[top:top + pic_h, left:left + pic_w] = (
            (rgb[..., 0] >> 3) << 11 | (rgb[..., 1] >> 2) << 5 | rgb[..., 2] >> 3)
        if self.overlay:
            self.overlay(canvas)
        turned = np.rot90(canvas, 1 if self.rotation == "counterclockwise" else -1)
        return np.ascontiguousarray(turned)

    def _on_bus_error(self, _bus, message):
        error, debug = message.parse_error()
        if self.on_error:
            self.on_error(f"{error.message} ({debug})")

    def _on_bus_eos(self, _bus, _message):
        self._deliver()   # a last picture may still be waiting
        if self.on_error:
            self.on_error(STREAM_ENDED)

    def _on_sample(self, sink):
        # called in a GStreamer thread: only copy the picture, nothing else
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        structure = sample.get_caps().get_structure(0)
        ok_w, width = structure.get_int("width")
        ok_h, height = structure.get_int("height")
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not (ok and ok_w and ok_h and width > 0 and height > 0):
            return Gst.FlowReturn.OK
        try:
            # One plane: height rows of equal length, possibly padded beyond
            # width * 3 bytes. (The GstVideo bindings, which would name the
            # row length directly, are not installed.)
            stride = len(mapped.data) // height
            if stride < width * 3:
                return Gst.FlowReturn.ERROR
            rows = np.frombuffer(mapped.data, dtype=np.uint8,
                                 count=stride * height).reshape(height, stride)
            picture = rows[:, :width * 3].reshape(height, width, 3).copy()
        finally:
            buffer.unmap(mapped)
        self._pending = picture
        if not self._scheduled:
            self._scheduled = True
            GLib.idle_add(self._deliver)
        return Gst.FlowReturn.OK

    def _deliver(self):
        self._scheduled = False
        picture, self._pending = self._pending, None
        if picture is not None:
            self._full = picture
            self.source_px = (picture.shape[1], picture.shape[0])
            self.dirty = True
            self.on_frame(self.render() if self.eager else None)
        return False
