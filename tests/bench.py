#!/usr/bin/env python3
"""Step P2.5: measures confirmed frames per second and MB/s to tabletd.

Three picture sequences, each sent as fast as the tablet confirms them:
  still   nothing changes; only PRESENT/ACK round trips (upper limit)
  scroll  text on a landscape desktop, scrolled three lines per frame
  noise   random pixels, cannot be compressed (stands in for video)

All frames are prepared before the clock starts, so the result contains the
tile comparison, LZ4, the USB transfer and the drawing on the tablet, but not
the rendering of the test pictures.

Usage: bench.py [--seconds 20] [--port 7100] [--no-adb] [--only NAME]
"""
import argparse
import pathlib
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import encode, link, secure  # noqa: E402

LINE = 18   # pixel height of one text line


def to565(img):
    rgb = np.asarray(img, dtype=np.uint16)
    return ((rgb[..., 0] >> 3) << 11 | (rgb[..., 1] >> 2) << 5 | rgb[..., 2] >> 3).astype(np.uint16)


def scroll_frames(width, height, count=40):
    """Landscape text page scrolled upwards, rotated into framebuffer layout."""
    land_w, land_h = height, width          # the tablet lies on its side
    font = ImageFont.load_default(size=14)
    rng = np.random.default_rng(7)
    words = ("tabletd hostd framebuffer protocol rect present touch laptop mirror "
             "kernel socket buffer pixel frame window scroll text line code").split()
    lines = [" ".join(rng.choice(words, size=14)) for _ in range(land_h // LINE + 3 * count + 2)]
    frames = []
    for step in range(count):
        img = Image.new("RGB", (land_w, land_h), (250, 250, 250))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, land_w - 1, 28], fill=(60, 90, 160))      # title bar
        for row in range((land_h - 30) // LINE + 1):
            draw.text((8, 32 + row * LINE), lines[step * 3 + row], fill=(20, 20, 20), font=font)
        frames.append(np.ascontiguousarray(np.rot90(to565(img))))
    return frames


def noise_frames(width, height, count=8):
    rng = np.random.default_rng(8)
    return [rng.integers(0, 65536, (height, width), dtype=np.uint16) for _ in range(count)]


def wait_event(session, kind, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in session.pump(0.01):
            if event[0] == kind:
                return event
            if event[0] == "disconnected":
                raise link.LinkError(f"disconnected: {event[1]}")
    raise link.LinkError(f"no '{kind}' within {timeout} s")


def run(session, name, frames, seconds):
    enc = encode.Encoder(session.width, session.height)
    # bring the tablet into a known state before the clock starts
    session.send_frame(enc.encode(frames[0]))
    wait_event(session, "ack", 10)
    enc.confirm()

    count = encode_time = 0
    bytes_before = session.bytes_sent
    start = time.monotonic()
    index = 0
    while time.monotonic() - start < seconds:
        index += 1
        t0 = time.monotonic()
        payloads = enc.encode(frames[index % len(frames)])
        encode_time += time.monotonic() - t0
        session.send_frame(payloads)
        wait_event(session, "ack", 10)
        enc.confirm()
        count += 1
    elapsed = time.monotonic() - start
    megabytes = (session.bytes_sent - bytes_before) / 1e6
    print(f"{name:<7} {count / elapsed:7.1f} frames/s  {megabytes / elapsed:6.2f} MB/s  "
          f"{megabytes * 1000 / max(count, 1):8.1f} kB/frame  "
          f"encode {encode_time * 1000 / max(count, 1):5.1f} ms/frame  "
          f"({count} frames in {elapsed:.1f} s)")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--port", type=int, default=link.DEFAULT_PORT)
    parser.add_argument("--no-adb", action="store_true", help="tabletd runs on this machine")
    parser.add_argument("--only", choices=("still", "scroll", "noise"))
    parser.add_argument("--wifi", metavar="HOST", help="direct encrypted connection")
    args = parser.parse_args()

    if args.wifi:
        session = link.Session(port=args.port, host=args.wifi, psk=secure.load_key())
    else:
        session = link.Session(port=args.port, use_adb=not args.no_adb)
    wait_event(session, "connected", 10)
    width, height = session.width, session.height
    print(f"connected: {width}x{height}")
    sequences = {
        "still": lambda: [np.full((height, width), 0x18C3, dtype=np.uint16)],
        "scroll": lambda: scroll_frames(width, height),
        "noise": lambda: noise_frames(width, height),
    }
    for name, make in sequences.items():
        if args.only in (None, name):
            run(session, name, make(), args.seconds)
    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
