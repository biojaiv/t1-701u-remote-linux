#!/usr/bin/env python3
"""Writes the button images of the matchbox theme "Tablet" (linux/overlay).

44x44 pixel XPM files: close (x) and menu (three bars) in the text colour of
the tablet's start screen on the card colour, large enough for a finger.
"""
import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "linux/overlay/usr/share/themes/Tablet/matchbox"
SIZE = 44
BG, FG = "#182138", "#f0f4fa"


def xpm(name, pixels):
    rows = ["".join("#" if (x, y) in pixels else "." for x in range(SIZE)) for y in range(SIZE)]
    body = ",\n".join(f'"{row}"' for row in rows)
    return (f"/* XPM */\nstatic char *{name}[] = {{\n\"{SIZE} {SIZE} 2 1\",\n"
            f'". c {BG}",\n"# c {FG}",\n{body}\n}};\n')


def close():
    pixels = set()
    for i in range(14, 31):
        for t in (-1, 0, 1):
            pixels |= {(i, i + t), (i, 44 - i + t - 1)}
    return pixels


def menu():
    return {(x, y) for x in range(12, 32) for y0 in (14, 21, 28) for y in range(y0, y0 + 3)}


OUT.mkdir(parents=True, exist_ok=True)
(OUT / "closebutton.xpm").write_text(xpm("closebutton", close()))
(OUT / "menubutton.xpm").write_text(xpm("menubutton", menu()))
print(f"written to {OUT}")
