"""Turns TOUCH messages from the tablet into pointer actions on the laptop.

Two parts, both free of D-Bus so that they can be tested alone:

Mapper   framebuffer coordinates -> position in the screen-cast stream
         (plan section 6.8).
Gestures one finger: tap = left click, move = drag with the left button,
         holding still for 0.6 s = right click.
         Two fingers: spreading or pinching zooms the picture; moving both
         together scrolls the laptop's content, or, while zoomed in, moves
         the visible part of the screen.
View     which part of the screen is shown (zoom 1 = everything).

Deviation from the wording of step P3.5: the left button is not pressed the
moment the finger lands but when it starts to move (or, for a tap, when it
lifts). Otherwise a long press could not become a right click without a left
click happening first.
"""

BTN_LEFT, BTN_RIGHT = 272, 273
UP, DOWN, MOVE = 0, 1, 2

LONG_PRESS_S = 0.6
MOVE_THRESHOLD = 12      # canvas pixels a finger may wander and still "hold still"
SCROLL_STEP = 30         # canvas pixels of two-finger travel per wheel step
PINCH_THRESHOLD = 30     # change of finger distance that makes a gesture a zoom
PAN_THRESHOLD = 20       # travel of both fingers that makes it a scroll or pan
MAX_ZOOM = 4.0


class View:
    """The visible part of the screen in source pixels: x, y, w, h.

    on_change(x, y, w, h) is called whenever it moves.
    """

    def __init__(self, source_px, on_change=None):
        self.src_w, self.src_h = source_px
        self.zoom = 1.0
        self.x = self.y = 0.0
        self.on_change = on_change

    @property
    def w(self):
        return self.src_w / self.zoom

    @property
    def h(self):
        return self.src_h / self.zoom

    def rect(self):
        """Whole source pixels, even sizes (video scalers prefer them)."""
        w = max(2, int(round(self.w / 2)) * 2)
        h = max(2, int(round(self.h / 2)) * 2)
        x = min(max(0, int(round(self.x))), self.src_w - w)
        y = min(max(0, int(round(self.y))), self.src_h - h)
        return x, y, w, h

    def set(self, zoom, x, y):
        zoom = min(max(zoom, 1.0), MAX_ZOOM)
        if zoom < 1.03:
            zoom = 1.0   # snap back to the whole screen
        before = self.rect()
        self.zoom = zoom
        self.x = min(max(0.0, x), self.src_w - self.w)
        self.y = min(max(0.0, y), self.src_h - self.h)
        if self.rect() != before and self.on_change:
            self.on_change(*self.rect())


class Mapper:
    """Maps a framebuffer position to stream coordinates, or None in the border.

    fb_size      (600, 1024): the tablet's framebuffer, portrait
    rotation     "counterclockwise" or "clockwise": how the picture was turned
                 to fit the framebuffer
    source_px    pixel size of the captured screen, e.g. (1920, 1200); decides
                 how large the picture is inside the landscape canvas
    stream_size  the `size` the portal reported for the stream, e.g.
                 (1746, 1091); pointer coordinates refer to this
    """

    def __init__(self, fb_size, rotation, source_px, stream_size):
        if rotation not in ("counterclockwise", "clockwise"):
            raise ValueError(f"unknown rotation {rotation!r}")
        self.fb_w, self.fb_h = fb_size
        self.rotation = rotation
        self.canvas_w, self.canvas_h = self.fb_h, self.fb_w
        scale = min(self.canvas_w / source_px[0], self.canvas_h / source_px[1])
        self.pic_w = max(1, round(source_px[0] * scale))
        self.pic_h = max(1, round(source_px[1] * scale))
        self.left = (self.canvas_w - self.pic_w) // 2
        self.top = (self.canvas_h - self.pic_h) // 2
        self.stream_w, self.stream_h = stream_size
        self.src_w, self.src_h = source_px
        self.view = (0, 0, self.src_w, self.src_h)   # visible part, source pixels

    def set_view(self, x, y, width, height):
        self.view = (x, y, width, height)

    def to_source(self, x_c, y_c):
        """Canvas point -> source pixel (may lie outside the picture)."""
        vx, vy, vw, vh = self.view
        return (vx + (x_c - self.left) / self.pic_w * vw,
                vy + (y_c - self.top) / self.pic_h * vh)

    def to_canvas(self, x_f, y_f):
        if self.rotation == "counterclockwise":
            return self.canvas_w - 1 - y_f, x_f
        return y_f, self.canvas_h - 1 - x_f

    def to_stream(self, x_f, y_f):
        x_c, y_c = self.to_canvas(x_f, y_f)
        x, y = x_c - self.left, y_c - self.top
        if not (0 <= x < self.pic_w and 0 <= y < self.pic_h):
            return None
        sx, sy = self.to_source(x_c, y_c)
        return sx / self.src_w * self.stream_w, sy / self.src_h * self.stream_h


class Gestures:
    """State machine over TOUCH messages.

    backend needs move(x, y), button(code, pressed) and scroll(steps).
    Call touch() for every TOUCH message and tick() a few times per second;
    both take the current time in seconds (monotonic).
    """

    def __init__(self, mapper, backend, view=None):
        self.mapper, self.backend, self.view = mapper, backend, view
        self.fingers = {}          # slot -> (x_c, y_c)
        # idle | pending | drag | done | two | scroll | pan | pinch
        self.state = "idle"
        self.start = self.down_at = None
        self.scroll_ref = 0.0
        self.two_start = None      # (distance, centre, zoom, view x, view y, source point)

    def _move(self, x_f, y_f):
        pos = self.mapper.to_stream(x_f, y_f)
        if pos is not None:
            self.backend.move(*pos)
        return pos is not None

    def _release_left(self):
        if self.state == "drag":
            self.backend.button(BTN_LEFT, False)

    def _scroll_axis(self):
        """Mean position of all fingers along the laptop's vertical axis."""
        return sum(pos[1] for pos in self.fingers.values()) / len(self.fingers)

    def _two(self):
        """Distance between the first two fingers and their centre."""
        (ax, ay), (bx, by) = list(self.fingers.values())[:2]
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5, ((ax + bx) / 2, (ay + by) / 2)

    def _two_fingers(self):
        distance, centre = self._two()
        if self.state not in ("two", "scroll", "pan", "pinch"):
            self._release_left()
            self.state = "two"
            self.scroll_ref = self._scroll_axis()
            anchor = self.mapper.to_source(*centre)
            view = self.view
            self.two_start = (distance, centre, view.zoom if view else 1.0,
                              view.x if view else 0.0, view.y if view else 0.0, anchor)
            return
        d0, c0, zoom0, vx0, vy0, anchor = self.two_start
        if self.state == "two":
            travel = ((centre[0] - c0[0]) ** 2 + (centre[1] - c0[1]) ** 2) ** 0.5
            spread = abs(distance - d0)
            if spread <= PINCH_THRESHOLD and travel <= PAN_THRESHOLD:
                return
            # The two fingers are reported one after the other, so a move of
            # both shows up as a small change of distance first. Whichever is
            # larger decides: fingers apart/together, or both the same way.
            if self.view and spread > travel:
                self.state = "pinch"
            else:
                self.state = "pan" if self.view and self.view.zoom > 1.0 else "scroll"
        if self.state == "pinch":
            zoom = min(max(zoom0 * distance / max(d0, 1.0), 1.0), MAX_ZOOM)
            # the screen point that was between the fingers stays between them
            width, height = self.view.src_w / zoom, self.view.src_h / zoom
            self.view.set(zoom,
                          anchor[0] - (centre[0] - self.mapper.left) / self.mapper.pic_w * width,
                          anchor[1] - (centre[1] - self.mapper.top) / self.mapper.pic_h * height)
        elif self.state == "pan":
            self.view.set(self.view.zoom,
                          vx0 - (centre[0] - c0[0]) / self.mapper.pic_w * self.view.w,
                          vy0 - (centre[1] - c0[1]) / self.mapper.pic_h * self.view.h)
        else:
            travel = self._scroll_axis() - self.scroll_ref
            steps = int(travel / SCROLL_STEP)
            if steps:
                # fingers moving up push the content up: scroll down
                self.backend.scroll(-steps)
                self.scroll_ref += steps * SCROLL_STEP

    def touch(self, slot, state, x_f, y_f, now):
        canvas = self.mapper.to_canvas(x_f, y_f)
        if state == UP:
            self.fingers.pop(slot, None)
        else:
            self.fingers[slot] = canvas
        count = len(self.fingers)

        if count >= 2:
            self._two_fingers()
            return
        if self.state in ("two", "scroll", "pan", "pinch"):
            if count == 0:
                self.state = "idle"
            return   # one finger left over from a two-finger gesture does nothing

        if state == DOWN and self.state == "idle":
            if self._move(x_f, y_f):
                self.state, self.start, self.down_at = "pending", canvas, now
            else:
                self.state = "done"   # landed in the black border
        elif state == MOVE and self.state == "pending":
            dx, dy = canvas[0] - self.start[0], canvas[1] - self.start[1]
            if dx * dx + dy * dy > MOVE_THRESHOLD ** 2:
                self.backend.button(BTN_LEFT, True)   # pressed at the start point
                self.state = "drag"
                self._move(x_f, y_f)
        elif state == MOVE and self.state == "drag":
            self._move(x_f, y_f)
        elif state == UP:
            if self.state == "pending":
                self.backend.button(BTN_LEFT, True)
                self.backend.button(BTN_LEFT, False)
            elif self.state == "drag":
                self._move(x_f, y_f)
                self.backend.button(BTN_LEFT, False)
            self.state = "idle"

    def tick(self, now):
        if self.state == "pending" and now - self.down_at >= LONG_PRESS_S:
            self.backend.button(BTN_RIGHT, True)
            self.backend.button(BTN_RIGHT, False)
            self.state = "done"   # nothing more until the finger lifts

    def reset(self):
        """Connection lost: let go of everything."""
        self._release_left()
        self.fingers.clear()
        self.state = "idle"
