#!/usr/bin/env python3
"""Step P2.6: endurance run with scrolling text.

Sends frames as fast as the tablet confirms them for the given time and
reads the resident memory (VmRSS) of tabletd before and after via adb.

Usage: soak.py [--minutes 30] [--port 7100]
"""
import argparse
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import bench  # noqa: E402
from host import encode, link  # noqa: E402

LOG = "/data/local/tmp/tabletd.log"


def adb(command):
    return subprocess.run(["adb", "shell", command], capture_output=True, text=True,
                          timeout=15).stdout.replace("\r", "")


def tabletd_pid():
    for line in adb(f"cat {LOG}").splitlines():
        if line.startswith("tabletd pid "):
            return int(line.split()[2])
    raise SystemExit("soak: tabletd pid not found in its log")


def rss_kb(pid):
    for line in adb(f"cat /proc/{pid}/status").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    raise SystemExit(f"soak: tabletd (pid {pid}) is not running")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--port", type=int, default=link.DEFAULT_PORT)
    args = parser.parse_args()

    pid = tabletd_pid()
    before = rss_kb(pid)
    battery_before = adb("cat /sys/class/power_supply/battery/capacity").strip()
    print(f"start {time.strftime('%H:%M:%S')}: tabletd pid {pid}, VmRSS {before} kB, "
          f"battery {battery_before} %", flush=True)

    session = link.Session(port=args.port)
    frames = enc = None
    acks = disconnects = index = 0
    start = time.monotonic()
    next_report = 300
    while time.monotonic() - start < args.minutes * 60:
        for event in session.pump(0.01):
            if event[0] == "connected":
                frames = frames or bench.scroll_frames(session.width, session.height)
                enc = encode.Encoder(session.width, session.height)
            elif event[0] == "disconnected":
                disconnects += 1
                print(f"{time.strftime('%H:%M:%S')} disconnected: {event[1]}", flush=True)
            elif event[0] == "ack":
                enc.confirm()
                acks += 1
        if session.can_send and frames:
            index += 1
            try:
                session.send_frame(enc.encode(frames[index % len(frames)]))
            except link.LinkError:
                pass
        elapsed = time.monotonic() - start
        if elapsed >= next_report:
            print(f"{time.strftime('%H:%M:%S')} {acks} frames, {acks / elapsed:.1f} frames/s, "
                  f"VmRSS {rss_kb(pid)} kB", flush=True)
            next_report += 300
    elapsed = time.monotonic() - start
    megabytes = session.bytes_sent / 1e6
    session.close()

    after = rss_kb(pid)
    print(f"end {time.strftime('%H:%M:%S')}: {acks} frames in {elapsed:.0f} s "
          f"({acks / elapsed:.1f} frames/s, {megabytes / elapsed:.2f} MB/s), "
          f"disconnects {disconnects}")
    print(f"VmRSS before {before} kB, after {after} kB, growth {after - before} kB; "
          f"same pid still running: {tabletd_pid() == pid}; "
          f"battery {battery_before} % -> {adb('cat /sys/class/power_supply/battery/capacity').strip()} %")
    return 0 if disconnects == 0 and after - before <= 1024 else 1


if __name__ == "__main__":
    sys.exit(main())
