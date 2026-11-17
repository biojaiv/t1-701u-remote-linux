"""One look for everything hostd draws: the same dark blue as the tablet's
status page, rounded cards, one accent colour, DejaVu fonts."""
import functools

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BG_TOP, BG_BOTTOM = (16, 24, 44), (5, 7, 13)
CARD, CARD_EDGE, CARD_DARK = (24, 33, 56), (44, 58, 92), (14, 20, 36)
TEXT_MAIN, TEXT_SUB, TEXT_DIM = (240, 244, 250), (201, 209, 220), (154, 164, 178)
LINE = (30, 41, 59)
ACCENT, ACCENT_DARK = (96, 165, 250), (37, 99, 235)
AMBER, GREEN, RED = (251, 191, 36), (74, 222, 128), (248, 113, 113)

_FONTS = {"regular": "DejaVuSans.ttf", "bold": "DejaVuSans-Bold.ttf",
          "light": "DejaVuSans-ExtraLight.ttf"}


@functools.lru_cache(maxsize=32)
def font(size, weight="regular"):
    return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/" + _FONTS[weight], size)


@functools.lru_cache(maxsize=4)
def _gradient(width, height):
    t = np.linspace(0.0, 1.0, height)[:, None]
    rows = (np.array(BG_TOP) * (1 - t) + np.array(BG_BOTTOM) * t).astype(np.uint8)
    return np.repeat(rows[:, None, :], width, axis=1)


def background(width, height):
    """A fresh image with the background gradient."""
    return Image.fromarray(_gradient(width, height))


def level_colour(percent):
    return GREEN if percent < 60 else AMBER if percent < 85 else RED


def card(draw, box, radius=18, pressed=False, fill=None):
    """Rounded card with a soft shadow and a light top edge."""
    x0, y0, x1, y1 = box
    draw.rounded_rectangle([x0 + 2, y0 + 4, x1 + 2, y1 + 4], radius, fill=(2, 4, 9))
    base = ACCENT_DARK if pressed else (fill or CARD)
    edge = ACCENT if pressed else CARD_EDGE
    draw.rounded_rectangle([x0, y0, x1, y1], radius, fill=edge)
    draw.rounded_rectangle([x0, y0 + 1, x1, y1], radius, fill=base)


BACK_BOX = (20, 10, 150, 58)     # the "Back" button in the header, canvas pixels


def header(draw, width, title, right_text, back=False, back_pressed=False):
    """Title line; with back, a "Back" button on the left and the title after it."""
    title_x = 28
    if back:
        card(draw, BACK_BOX, radius=14, pressed=back_pressed)
        x0, y0, x1, y1 = BACK_BOX
        draw.text(((x0 + x1) / 2, (y0 + y1) / 2), "‹ Back", font=font(24, "bold"),
                  fill=TEXT_MAIN, anchor="mm")
        title_x = x1 + 22
    draw.text((title_x, 46), title, font=font(30, "bold"), fill=TEXT_MAIN, anchor="ls")
    draw.text((width - 28, 46), right_text, font=font(26), fill=TEXT_DIM, anchor="rs")
    draw.line([(28, 64), (width - 28, 64)], fill=LINE, width=1)


def bar(draw, box, percent, colour):
    x0, y0, x1, y1 = box
    radius = (y1 - y0) // 2
    draw.rounded_rectangle(box, radius, fill=CARD_DARK)
    width = int((x1 - x0) * max(0.0, min(percent, 100.0)) / 100)
    if width > 2 * radius:
        draw.rounded_rectangle([x0, y0, x0 + width, y1], radius, fill=colour)


def new(width, height):
    """(image, draw) with the background already painted."""
    image = background(width, height)
    return image, ImageDraw.Draw(image)


BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], dtype=np.uint16)


def dither565(rgb):
    """8-bit RGB array -> RGB565 with ordered dithering (smooth dark gradients)."""
    h, w = rgb.shape[:2]
    d = np.tile(BAYER, (h // 4 + 1, w // 4 + 1))[:h, :w]
    rgb = rgb.astype(np.uint16)
    r = np.minimum(rgb[..., 0] + d // 2, 255)
    g = np.minimum(rgb[..., 1] + d // 4, 255)
    b = np.minimum(rgb[..., 2] + d // 2, 255)
    return ((r >> 3) << 11 | (g >> 2) << 5 | b >> 3).astype(np.uint16)
