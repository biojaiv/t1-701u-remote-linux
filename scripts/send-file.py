#!/usr/bin/env python3
"""Send one file to tb-fetch on the tablet over Wi-Fi.

The laptop connects (outgoing connections to the tablet are known to work,
hostd uses them too); tb-fetch must already be waiting on the tablet:
    tb-fetch --listen 5555 --from <laptop address> FILE SIZE

Usage: scripts/send-file.py HOST PORT FILE
"""
import os
import socket
import sys
import time


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__.strip().splitlines()[-1])
    host, port, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    size = os.path.getsize(path)
    started = time.monotonic()
    with socket.create_connection((host, port), timeout=30) as conn, open(path, "rb") as f:
        conn.settimeout(120)
        sent = 0
        report = 64 << 20
        while sent < size:
            n = conn.sendfile(f, sent, min(16 << 20, size - sent))
            if n == 0:
                sys.exit(f"send-file: connection closed after {sent} of {size} bytes")
            sent += n
            if sent >= report:
                rate = sent / (time.monotonic() - started) / 1e6
                print(f"send-file: {sent >> 20} MB of {size >> 20} MB, {rate:.1f} MB/s", flush=True)
                report += 64 << 20
        conn.shutdown(socket.SHUT_WR)
        conn.recv(1)            # wait until the receiver has closed its side
    print(f"send-file: {size} bytes in {time.monotonic() - started:.0f} s")


if __name__ == "__main__":
    main()
