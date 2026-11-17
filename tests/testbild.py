#!/usr/bin/env python3
"""Step P1.7/P1.8: sends a test picture to tabletd.

The picture is drawn in framebuffer coordinates (600x1024, portrait): three
labelled colour bands, an arrow pointing to the top edge of the tablet and a
marker in each corner. With --touch the script then prints TOUCH and KEY
messages until it is interrupted.

Usage: testbild.py [--port 7100] [--touch SECONDS] [--save FILE.png]
"""
import argparse
import pathlib
import struct
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import link  # noqa: E402

MARK = 40   # size of the corner markers in pixels


def render(width, height):
    img = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(img)
    # DejaVu has the umlauts; Pillow's built-in font drew the "Ü" wrongly
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    font = ImageFont.truetype(bold, 64)
    small = ImageFont.truetype(bold, 26)
    band = (height - 2 * 180) // 3
    for i, (name, colour) in enumerate((("RED", (255, 0, 0)),
                                        ("GREEN", (0, 255, 0)),
                                        ("BLUE", (0, 0, 255)))):
        top = 180 + i * band
        draw.rectangle([0, top, width - 1, top + band - 1], fill=colour)
        draw.text((width // 2, top + band // 2), name, fill="white",
                  font=font, anchor="mm", stroke_width=3, stroke_fill="black")
    # arrow to the top edge
    cx = width // 2
    draw.polygon([(cx, 20), (cx - 60, 100), (cx + 60, 100)], fill="white")
    draw.rectangle([cx - 20, 100, cx + 20, 170], fill="white")
    draw.text((cx, height - 90), "UP is where the arrow points", fill="white",
              font=small, anchor="mm")
    # corner markers: 1 top left, 2 top right, 3 bottom left, 4 bottom right
    for label, (x, y) in zip("1234", ((0, 0), (width - MARK, 0),
                                       (0, height - MARK),
                                       (width - MARK, height - MARK))):
        draw.rectangle([x, y, x + MARK - 1, y + MARK - 1], fill="yellow")
        draw.text((x + MARK // 2, y + MARK // 2), label, fill="black",
                  font=small, anchor="mm")
    return img


def to_rgb565(img):
    rgb = np.asarray(img, dtype=np.uint16)
    packed = (rgb[..., 0] >> 3) << 11 | (rgb[..., 1] >> 2) << 5 | rgb[..., 2] >> 3
    return packed.astype("<u2").tobytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=link.DEFAULT_PORT)
    parser.add_argument("--touch", type=float, default=0, metavar="SECONDS")
    parser.add_argument("--save", metavar="FILE.png")
    parser.add_argument("--no-adb", action="store_true", help="tabletd runs on this machine")
    args = parser.parse_args()

    if not args.no_adb:
        link.adb_forward(args.port)   # a replugged cable drops the forwarding
    conn = link.Link(port=args.port)
    print(f"INFO: {conn.width}x{conn.height}, flags {conn.flags}")
    img = render(conn.width, conn.height)
    if args.save:
        img.save(args.save)
    start = time.monotonic()
    conn.rect(0, 0, conn.width, conn.height, to_rgb565(img))
    conn.present(1)
    print(f"picture sent and acknowledged in {time.monotonic() - start:.2f} s")

    deadline = time.monotonic() + args.touch
    counter = 0
    conn.sock.settimeout(1.0)
    while time.monotonic() < deadline:
        counter += 1
        conn.send(link.PING, struct.pack("<I", counter))
        try:
            while True:
                msg_type, payload = conn.recv()
                if msg_type == link.TOUCH:
                    slot, state, x, y = struct.unpack("<BBHH", payload)
                    print(f"TOUCH slot={slot} state={'up down move'.split()[state]} x={x} y={y}")
                elif msg_type == link.KEY:
                    code, state = struct.unpack("<HB", payload)
                    print(f"KEY code={code} state={state}")
        except link.LinkError as exc:
            if "timed out" not in str(exc):
                raise
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
