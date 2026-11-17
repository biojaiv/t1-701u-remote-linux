"""The two screens that do not mirror the laptop: system monitor and macros.

The tablet can only pick a macro by its position on the screen. What a macro
does is defined on the laptop, in ~/.config/huawai-companion/macros.toml:

    [[macro]]
    label = "Quieter"
    command = ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"]

Commands are argument lists and run without a shell.
"""
import os
import pathlib
import subprocess
import sys
import time
import tomllib

import numpy as np
from PIL import Image, ImageDraw

from host import sysmon, theme  # noqa: E402



def config_dir():
    """~/.config/huawai-companion, or below $XDG_CONFIG_HOME if that is set."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "huawai-companion"


MAX_MACROS = 8
PRESS_FLASH_S = 0.4

DEFAULT_MACROS = [
    ("Quieter", ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"]),
    ("Mute", ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"]),
    ("Louder", ["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "5%+"]),
    ("Lock", ["loginctl", "lock-session"]),
]


def load_macros(path=None):
    """Returns a list of (label, argv). Falls back to the defaults if the
    file does not exist; a broken file raises ValueError."""
    path = path or config_dir() / "macros.toml"
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return list(DEFAULT_MACROS)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    macros = []
    for entry in data.get("macro", []):
        label, command = entry.get("label"), entry.get("command")
        if (not isinstance(label, str) or not isinstance(command, list) or not command
                or not all(isinstance(part, str) for part in command)):
            raise ValueError(f"{path}: every macro needs a label and a command list")
        macros.append((label, command))
    if not macros:
        raise ValueError(f"{path}: no [[macro]] entries")
    return macros[:MAX_MACROS]


_started = []   # macro processes not yet collected


def run_macro(command):
    """Starts the command and does not wait for it."""
    _started[:] = [proc for proc in _started if proc.poll() is None]   # reap finished ones
    try:
        _started.append(subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        return True
    except OSError:
        return False


def to_frame(image, rotation):
    """Landscape PIL image -> RGB565 array in framebuffer layout (dithered)."""
    packed = theme.dither565(np.asarray(image.convert("RGB")))
    turned = np.rot90(packed, 1 if rotation == "counterclockwise" else -1)
    return np.ascontiguousarray(turned)


BACK = "back"     # tap() result: the "Back" button (to the tablet's start screen)


class Screen:
    """Common part: buttons, hit test, flash after a press. back_enabled: the
    header shows a "Back" button (the tablet understands MSG_HOME)."""

    def __init__(self, macros, canvas_size):
        self.macros = macros
        self.canvas_w, self.canvas_h = canvas_size
        self.pressed, self.pressed_until = None, 0.0
        self.back_enabled = False

    def rects(self):
        raise NotImplementedError

    def tap(self, x_c, y_c, now=None):
        """Runs the macro under the canvas point. Returns its index or None."""
        now = time.monotonic() if now is None else now
        if self.back_enabled:
            x0, y0, x1, y1 = theme.BACK_BOX
            if x0 <= x_c <= x1 and y0 <= y_c <= y1:
                self.pressed, self.pressed_until = BACK, now + PRESS_FLASH_S
                return BACK
        for index, (x0, y0, x1, y1) in enumerate(self.rects()):
            if x0 <= x_c <= x1 and y0 <= y_c <= y1 and index < len(self.macros):
                run_macro(self.macros[index][1])
                self.pressed, self.pressed_until = index, now + PRESS_FLASH_S
                return index
        return None

    def current_pressed(self, now=None):
        now = time.monotonic() if now is None else now
        if self.pressed is not None and now > self.pressed_until:
            self.pressed = None
        return self.pressed


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _clock():
    return time.strftime("%H:%M")


class MonitorScreen(Screen):
    """Laptop at a glance (CPU, cores, memory, disk, network) and four macros."""

    BUTTON_H = 92

    def __init__(self, macros, canvas_size=(1024, 600)):
        super().__init__(macros[:4], canvas_size)
        self.monitor = sysmon.Monitor()

    def rects(self):
        gap, n = 16, max(1, len(self.macros))
        width = (self.canvas_w - 2 * 28 - gap * (n - 1)) // n
        y0 = self.canvas_h - 28 - self.BUTTON_H
        return [(28 + i * (width + gap), y0, 28 + i * (width + gap) + width, y0 + self.BUTTON_H)
                for i in range(len(self.macros))]

    def render(self):
        m = self.monitor
        m.sample()
        w, h = self.canvas_w, self.canvas_h
        image, draw = theme.new(w, h)
        right = []
        if m.bat:
            right.append(f"Battery {m.bat.percent:.0f} %{' ⚡' if m.bat.power_plugged else ''}")
        if m.temp:
            right.append(f"{m.temp:.0f} °C")
        right.append(_clock())
        theme.header(draw, w, "Laptop", "   ".join(right), back=self.back_enabled,
                     back_pressed=self.current_pressed() == BACK)

        top, bottom = 84, h - 28 - self.BUTTON_H - 18
        split = int(w * 0.58)
        # CPU history as a filled area
        theme.card(draw, (28, top, split - 8, top + 190))
        draw.text((48, top + 40), "CPU", font=theme.font(22, "bold"), fill=theme.TEXT_DIM,
                  anchor="ls")
        draw.text((split - 28, top + 44), f"{m.cpu:.0f} %", font=theme.font(40, "light"),
                  fill=theme.TEXT_MAIN, anchor="rs")
        gx0, gy0, gx1, gy1 = 48, top + 62, split - 28, top + 172
        points = [(gx0 + i * (gx1 - gx0) / (len(m.hist) - 1), gy1 - v / 100 * (gy1 - gy0))
                  for i, v in enumerate(m.hist)]
        draw.polygon([(gx0, gy1)] + points + [(gx1, gy1)], fill=(30, 58, 110))
        draw.line(points, fill=theme.ACCENT, width=3)
        # cores
        theme.card(draw, (split + 8, top, w - 28, top + 190))
        draw.text((split + 28, top + 40), "Cores", font=theme.font(22, "bold"),
                  fill=theme.TEXT_DIM, anchor="ls")
        n = len(m.cores)
        cx0, cx1, cy0, cy1 = split + 28, w - 48, top + 62, top + 172
        step = (cx1 - cx0) / n
        for i, c in enumerate(m.cores):
            x = cx0 + i * step
            theme.bar(draw, (int(x), cy0, int(x + step - 6), cy1), 100, theme.CARD_DARK)
            height = int((cy1 - cy0) * min(c, 100) / 100)
            if height > 6:
                draw.rounded_rectangle([int(x), cy1 - height, int(x + step - 6), cy1], 4,
                                       fill=theme.level_colour(c))
        # memory, disk, network
        theme.card(draw, (28, top + 206, w - 28, bottom))
        rows = (("Memory", m.mem.percent, f"{_human(m.mem.used)} of {_human(m.mem.total)}"),
                ("/home", m.disk.percent, f"{_human(m.disk.used)} of {_human(m.disk.total)}"))
        for i, (name, pct, text) in enumerate(rows):
            y = top + 244 + i * 40
            draw.text((48, y), name, font=theme.font(22, "bold"), fill=theme.TEXT_SUB, anchor="ls")
            theme.bar(draw, (170, y - 16, 620, y - 2), pct, theme.level_colour(pct))
            draw.text((640, y), f"{pct:.0f} %   {text}", font=theme.font(20), fill=theme.TEXT_DIM,
                      anchor="ls")
        y = top + 244 + 2 * 40
        draw.text((48, y), "Network", font=theme.font(22, "bold"), fill=theme.TEXT_SUB, anchor="ls")
        draw.text((170, y), f"↓ {_human(m.rate[0])}/s     ↑ {_human(m.rate[1])}/s",
                  font=theme.font(22), fill=theme.TEXT_MAIN, anchor="ls")
        # macros
        pressed = self.current_pressed()
        for i, ((label, _), box) in enumerate(zip(self.macros, self.rects())):
            theme.card(draw, box, radius=16, pressed=i == pressed)
            draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), label,
                      font=theme.font(28, "bold"), fill=theme.TEXT_MAIN, anchor="mm")
        return image


class MacroScreen(Screen):
    """Up to eight large buttons, two rows of four."""

    TOP = 84

    def rects(self):
        gap, cols, margin = 18, 4, 28
        rows = 2 if len(self.macros) > cols else 1
        width = (self.canvas_w - 2 * margin - gap * (cols - 1)) // cols
        height = (self.canvas_h - self.TOP - margin - gap * (rows - 1)) // rows
        return [(margin + (i % cols) * (width + gap), self.TOP + (i // cols) * (height + gap),
                 margin + (i % cols) * (width + gap) + width,
                 self.TOP + (i // cols) * (height + gap) + height)
                for i in range(len(self.macros))]

    def render(self):
        image, draw = theme.new(self.canvas_w, self.canvas_h)
        pressed = self.current_pressed()
        theme.header(draw, self.canvas_w, "Macros", _clock(), back=self.back_enabled,
                     back_pressed=pressed == BACK)
        for index, ((label, _), box) in enumerate(zip(self.macros, self.rects())):
            x0, y0, x1, y1 = box
            theme.card(draw, box, radius=22, pressed=index == pressed)
            draw.text((x0 + 20, y0 + 34), str(index + 1), font=theme.font(20),
                      fill=theme.TEXT_SUB if index == pressed else theme.TEXT_DIM, anchor="ls")
            draw.text(((x0 + x1) / 2, (y0 + y1) / 2 + 8), label, font=theme.font(32, "bold"),
                      fill=theme.TEXT_MAIN, anchor="mm")
        return image
