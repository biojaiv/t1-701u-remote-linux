"""On-screen keyboard that hostd draws over the mirrored picture.

The tablet still sends nothing but
touches; hostd turns touches on this keyboard into key presses on the laptop.
That lifts a rule of the original plan: with the keyboard, whoever controls
the tablet can type on the laptop.

Everything here works in canvas coordinates (landscape, e.g. 1024x600). A
small tab at the right edge shows and hides the keyboard.
"""
import numpy as np
from PIL import Image, ImageDraw

from . import theme

ROW_H = 56
TAB_W, TAB_H = 32, 220
MIN_CANVAS_W = 240      # narrower: keys too small to draw or hit
FLASH_S = 0.15

BACKSPACE, TAB, RETURN, ESCAPE = 0xFF08, 0xFF09, 0xFF0D, 0xFF1B
LEFT, UP, RIGHT, DOWN = 0xFF51, 0xFF52, 0xFF53, 0xFF54
# keys that repeat while the finger stays on them (hostd repeats them itself,
# each repeat a complete press and release)
REPEATING = {BACKSPACE, LEFT, UP, RIGHT, DOWN}
REPEAT_DELAY_S, REPEAT_EVERY_S = 0.45, 0.07

# a key is (label, what, width); what is a keysym (int), a character, or one
# of the actions "shift", "symbols", "letters", "hide"
SPECIAL = {"⌫": BACKSPACE, "⏎": RETURN, "Esc": ESCAPE, "Tab": TAB, "␣": " ",
           "←": LEFT, "↑": UP, "→": RIGHT, "↓": DOWN,
           "⇧": "shift", "#+=": "symbols", "abc": "letters", "▼": "hide"}
WIDE = {"⌫": 1.5, "⏎": 1.5, "⇧": 1.5, "#+=": 1.5, "abc": 1.5, "▼": 1.5, "␣": 4.0}

BOTTOM = ["#+=", "Esc", "Tab", "␣", "←", "↓", "↑", "→", "▼"]
LAYERS = {
    "lower": [list("1234567890ß") + ["⌫"], list("qwertzuiopü+"),
              list("asdfghjklöä") + ["⏎"], ["⇧"] + list("yxcvbnm,.-"), BOTTOM],
    "upper": [list("!\"§$%&/()=?") + ["⌫"], list("QWERTZUIOPÜ*"),
              list("ASDFGHJKLÖÄ") + ["⏎"], ["⇧"] + list("YXCVBNM;:_"), BOTTOM],
    # only characters a German layout has as plain keys: KDE drops a keysym
    # the layout lacks, and ^ ` ´ exist there only as dead keys
    "symbols": [list("1234567890ß") + ["⌫"], list("@€#~<>|\\°'µ²"),
                list("{}[]/*+=%&$") + ["⏎"], ["⇧"] + list("!?\"§;:_,.-"),
                ["abc"] + BOTTOM[1:]],
}


def keysym(char):
    """X keysym of a character: Latin-1 directly, everything else as Unicode."""
    code = ord(char)
    if char == "€":
        return 0x20AC   # EuroSign: keyboard layouts list it under this old name
    return code if 0x20 <= code <= 0xFF else 0x01000000 | code


def _paste(canvas, x0, y0, image):
    """Copies image to (x0, y0), cut to the canvas: a screen smaller than
    the keyboard (a test client reported 64x64) must not stop hostd."""
    height, width = image.shape[:2]
    top, left = max(y0, 0), max(x0, 0)
    bottom, right = min(y0 + height, canvas.shape[0]), min(x0 + width, canvas.shape[1])
    if bottom > top and right > left:
        canvas[top:bottom, left:right] = image[top - y0:bottom - y0, left - x0:right - x0]


class Keyboard:
    def __init__(self, canvas_w, canvas_h):
        self.canvas_w, self.canvas_h = canvas_w, canvas_h
        self.height = ROW_H * 5
        self.top = canvas_h - self.height
        self.visible = False
        self.layer = "lower"
        self.pressed = None          # index of the key shown as pressed
        self.pressed_until = 0.0
        self._cache = {}
        self._font = theme.font(24)
        self._small = theme.font(16, "bold")

    # -- geometry -----------------------------------------------------------

    def tab_rect(self):
        """The show/hide tab: right edge, above the keyboard."""
        top = max(0, (self.top - TAB_H) // 2)
        return self.canvas_w - TAB_W, top, self.canvas_w, top + TAB_H

    def keys(self, layer=None):
        """List of (label, what, x0, y0, x1, y1) for a layer, canvas coordinates."""
        result = []
        for row_index, row in enumerate(LAYERS[layer or self.layer]):
            units = sum(WIDE.get(label, 1.0) for label in row)
            unit_w = self.canvas_w / units
            x = 0.0
            y0 = self.top + row_index * ROW_H
            for label in row:
                width = WIDE.get(label, 1.0) * unit_w
                what = SPECIAL.get(label, label)
                result.append((label, what, round(x), y0, round(x + width), y0 + ROW_H))
                x += width
        return result

    def in_tab(self, x, y):
        x0, y0, x1, y1 = self.tab_rect()
        return x0 <= x < x1 and y0 <= y < y1

    def covers(self, x, y):
        """True if a touch at this point belongs to the keyboard, not the picture."""
        return self.in_tab(x, y) or (self.visible and y >= self.top)

    # -- behaviour ------------------------------------------------------------

    def touch(self, x, y, now):
        """A finger landed at this canvas point. Returns the keysym to type, or None.

        Layer keys, the tab and the hide key change the keyboard and return None.
        """
        if self.in_tab(x, y):
            self.visible = not self.visible
            self.pressed = None
            return None
        if not (self.visible and y >= self.top):
            return None
        for index, (_, what, x0, y0, x1, y1) in enumerate(self.keys()):
            if x0 <= x < x1 and y0 <= y < y1:
                break
        else:
            return None
        self.pressed, self.pressed_until = index, now + FLASH_S
        if what == "shift":
            self.layer = "lower" if self.layer == "upper" else "upper"
        elif what == "symbols":
            self.layer = "symbols"
        elif what == "letters":
            self.layer = "lower"
        elif what == "hide":
            self.visible = False
        else:
            if self.layer == "upper":
                self.layer = "lower"      # shift holds for one key
            return what if isinstance(what, int) else keysym(what)
        self.pressed = None               # the layout changed: no key to flash
        return None

    def keep_pressed(self, now):
        """A repeating key is still held: keep its highlight on."""
        if self.pressed is not None:
            self.pressed_until = now + FLASH_S

    def expire(self, now):
        """True if the pressed-key highlight just ended (redraw needed)."""
        if self.pressed is not None and now >= self.pressed_until:
            self.pressed = None
            return True
        return False

    # -- drawing ----------------------------------------------------------------

    @staticmethod
    def _pack(image):
        rgb = np.asarray(image, dtype=np.uint16)
        return ((rgb[..., 0] >> 3) << 11 | (rgb[..., 1] >> 2) << 5 | rgb[..., 2] >> 3).astype(np.uint16)

    def _keys_image(self):
        key = (self.layer, self.pressed)
        if key not in self._cache:
            image = Image.new("RGB", (self.canvas_w, self.height), theme.CARD_DARK)
            draw = ImageDraw.Draw(image)
            draw.line([(0, 0), (self.canvas_w, 0)], fill=theme.CARD_EDGE, width=1)
            for index, (label, what, x0, y0, x1, y1) in enumerate(self.keys()):
                y0, y1 = y0 - self.top, y1 - self.top
                action = isinstance(what, str) and len(what) > 1 or isinstance(what, int)
                pressed = index == self.pressed
                fill = theme.ACCENT_DARK if pressed else \
                    (20, 28, 48) if action else theme.CARD
                edge = theme.ACCENT if pressed else theme.CARD_EDGE
                draw.rounded_rectangle([x0 + 3, y0 + 4, x1 - 3, y1 - 2], 9, fill=edge)
                draw.rounded_rectangle([x0 + 3, y0 + 5, x1 - 3, y1 - 2], 9, fill=fill)
                draw.text(((x0 + x1) / 2, (y0 + y1) / 2 + 1), label,
                          fill=theme.TEXT_DIM if action and not pressed else theme.TEXT_MAIN,
                          font=self._small if len(label) > 1 else self._font, anchor="mm")
            if len(self._cache) > 40:
                self._cache.clear()
            self._cache[key] = self._pack(image)
        return self._cache[key]

    def _tab_image(self):
        key = ("tab", self.visible)
        if key not in self._cache:
            image = Image.new("RGB", (TAB_W, TAB_H), theme.CARD_DARK)
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle([1, 1, TAB_W + 12, TAB_H - 2], 12, fill=theme.CARD_EDGE)
            draw.rounded_rectangle([2, 2, TAB_W + 12, TAB_H - 3], 12, fill=theme.CARD)
            draw.rounded_rectangle([7, 16, 10, TAB_H - 16], 2,
                                   fill=theme.ACCENT if not self.visible else theme.TEXT_DIM)
            for i, char in enumerate("▼" if self.visible else "ABC"):
                draw.text((TAB_W / 2 + 3, TAB_H / 2 + (i - (0 if self.visible else 1)) * 26), char,
                          fill=theme.TEXT_MAIN, font=self._small, anchor="mm")
            self._cache[key] = self._pack(image)
        return self._cache[key]

    def draw(self, canvas):
        """Paints tab and keyboard into a canvas array of shape (canvas_h, canvas_w)."""
        if self.canvas_w < MIN_CANVAS_W or self.canvas_h < self.height + TAB_H // 4:
            return     # no room for a usable keyboard: paint nothing rather than fail
        x0, y0, _, _ = self.tab_rect()
        _paste(canvas, x0, y0, self._tab_image())
        if self.visible:
            _paste(canvas, 0, self.top, self._keys_image())


class KeyRepeat:
    """Repetition of a held key (Backspace, arrows) while its finger stays down.

    start() when the finger lands on such a key, stop() when that finger
    lifts; due(now) returns the keysym each time a repeat is due, else None.
    """

    def __init__(self, delay=REPEAT_DELAY_S, every=REPEAT_EVERY_S):
        self.delay, self.every = delay, every
        self.slot = self.keysym = None
        self.next_at = 0.0

    def start(self, slot, keysym, now):
        if keysym in REPEATING:
            self.slot, self.keysym, self.next_at = slot, keysym, now + self.delay
        else:
            self.stop()

    def stop(self, slot=None):
        if slot is None or slot == self.slot:
            self.slot = self.keysym = None

    def due(self, now):
        if self.keysym is None or now < self.next_at:
            return None
        self.next_at = max(self.next_at, now) + self.every   # no burst after a stall
        return self.keysym
