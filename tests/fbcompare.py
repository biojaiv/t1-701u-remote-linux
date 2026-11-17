#!/usr/bin/env python3
"""Compares a raw dump of the tablet's visible framebuffer page with a PNG.

The dump holds height rows of line_length bytes, 4 bytes per pixel. The
script reports which byte order of red, green and blue matches the picture
after it went through RGB565, and how many pixels differ.

Usage: fbcompare.py DUMP.raw PICTURE.png [--line 2400]
"""
import argparse
import itertools
import sys

import numpy as np
from PIL import Image


def through_565(rgb):
    """What tabletd makes of an 8-bit RGB picture (RGB565 and back)."""
    r5, g6, b5 = rgb[..., 0] >> 3, rgb[..., 1] >> 2, rgb[..., 2] >> 3
    return np.stack([r5 << 3 | r5 >> 2, g6 << 2 | g6 >> 4, b5 << 3 | b5 >> 2], axis=-1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dump")
    parser.add_argument("picture")
    parser.add_argument("--line", type=int, default=None)
    args = parser.parse_args()

    want = through_565(np.asarray(Image.open(args.picture).convert("RGB"), dtype=np.uint8))
    height, width, _ = want.shape
    line = args.line or width * 4
    raw = np.fromfile(args.dump, dtype=np.uint8)
    if raw.size < height * line:
        print(f"dump too small: {raw.size} bytes, need {height * line}")
        return 2
    got = raw[:height * line].reshape(height, line)[:, :width * 4].reshape(height, width, 4)
    best = None
    for order in itertools.permutations(range(4), 3):
        diff = int(np.any(got[..., list(order)] != want, axis=-1).sum())
        if best is None or diff < best[0]:
            best = (diff, order)
    diff, order = best
    rest = ({0, 1, 2, 3} - set(order)).pop()
    print(f"best byte order: R at byte {order[0]}, G at byte {order[1]}, B at byte {order[2]}; "
          f"byte {rest} has values {sorted(set(np.unique(got[..., rest]).tolist()))[:4]}")
    print(f"differing pixels: {diff} of {width * height}")
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
