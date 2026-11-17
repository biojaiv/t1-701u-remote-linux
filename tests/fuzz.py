#!/usr/bin/env python3
"""Step P6.6: throws random and damaged input at tabletd (x86, sanitizers).

Three kinds of attack, mixed at random:
  messages   valid, mutated and nonsense protocol messages over the socket
  events     random and half-valid input events through the input FIFOs
  blocks     damaged LZ4 blocks straight into the decoder (tests/lz4cli)
  secure     the encrypted listener: garbage, broken handshakes, and after a
             correct handshake damaged records and random frames

tabletd must never die and the sanitizers must stay silent. A connection
that tabletd closes is the expected answer to bad input; the fuzzer simply
connects again. On a finding the input of the last round is saved next to
the log and the run stops with exit status 1.

Usage: fuzz.py [--minutes 60] [--seed N]
"""
import argparse
import errno
import os
import pathlib
import random
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

import lz4.block

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import secure  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
TABLETD = ROOT / "build" / "x86" / "tabletd"
LZ4CLI = ROOT / "build" / "x86" / "lz4cli"
W, H = 600, 1024
TYPES = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08]
LENGTHS = {0x02: 4, 0x03: 1, 0x04: 12, 0x05: 4, 0x06: 32, 0x07: 12}


def frame(msg_type, payload):
    return struct.pack("<BI", msg_type, len(payload)) + payload


def valid_message(rng):
    if rng.random() < 0.005:          # HOME, rarely: tabletd ends and is started again
        return frame(0x09, b"")
    msg_type = rng.choice(TYPES)
    if msg_type == 0x07:              # COPY: mostly valid areas, sometimes at the edges
        w, h = rng.randint(1, W), rng.randint(1, H)
        return frame(0x07, struct.pack("<6H", rng.randint(0, W - w), rng.randint(0, H - h), w, h,
                                       rng.randint(0, W - w), rng.randint(0, H - h)))
    if msg_type == 0x08:              # AUDIO: mostly valid formats, sometimes not
        rate = rng.choice((48000, 24000, 48000, 44100, 0, 0xFFFFFFFF))
        channels = rng.choice((2, 1, 2, 0, 3, 255))
        frames = rng.choice((1, 480, 960, 2400, 4800, 4801, rng.randint(1, 5000)))
        samples = rng.randbytes(frames * max(channels, 1) * 2 + rng.choice((0, 0, 0, 1)))
        return frame(0x08, struct.pack("<IBB", rate, channels, rng.choice((0, 0, 0, 1))) + samples)
    if msg_type != 0x01:
        return frame(msg_type, rng.randbytes(LENGTHS[msg_type]))
    w, h = rng.randint(1, 80), rng.randint(1, 80)
    if rng.random() < 0.05:
        w, h = rng.choice(((W, H), (W, 1), (1, H), (W, 64)))
    x, y = rng.randint(0, W - w), rng.randint(0, H - h)
    pixels = rng.randbytes(w * h * 2) if rng.random() < 0.5 else bytes([rng.randrange(256)]) * (w * h * 2)
    if rng.random() < 0.5:
        return frame(0x01, struct.pack("<HHHHB", x, y, w, h, 1)
                     + lz4.block.compress(pixels, store_size=False))
    return frame(0x01, struct.pack("<HHHHB", x, y, w, h, 0) + pixels)


def mutate(rng, data):
    data = bytearray(data)
    for _ in range(rng.randint(1, 4)):
        choice = rng.random()
        if choice < 0.5 and data:
            data[rng.randrange(len(data))] ^= 1 << rng.randrange(8)
        elif choice < 0.7 and data:
            del data[rng.randrange(len(data)):]
        elif choice < 0.85:
            pos = rng.randrange(len(data) + 1)
            data[pos:pos] = rng.randbytes(rng.randint(1, 16))
        elif len(data) >= 5:
            struct.pack_into("<I", data, 1, rng.choice((0, 1, 0xFFFFFFFF, 1300000, 1300001,
                                                        rng.getrandbits(32))))
    return bytes(data)


def message_round(rng):
    parts = []
    for _ in range(rng.randint(1, 30)):
        choice = rng.random()
        if choice < 0.6:
            parts.append(valid_message(rng))
        elif choice < 0.9:
            parts.append(mutate(rng, valid_message(rng)))
        else:
            parts.append(rng.randbytes(rng.randint(1, 64)))
    return b"".join(parts)


def event_round(rng):
    events = []
    for _ in range(rng.randint(1, 200)):
        if rng.random() < 0.7:
            ev_type = rng.choice((0, 1, 3))
            code = rng.choice((0, 2, 0x2F, 0x35, 0x36, 0x39, 0x14A, 114, 115, rng.getrandbits(16)))
            value = rng.choice((-1, 0, 1, 2, 5, 599, 600, 1023, 1024, 99999, -99999,
                                rng.getrandbits(31)))
            events.append(struct.pack("<IIHHi", 0, 0, ev_type, code, value))
        else:
            events.append(rng.randbytes(rng.randint(1, 31)))
    return b"".join(events)


def block_round(rng):
    cases = []
    for _ in range(200):
        data = rng.randbytes(rng.randint(0, 300)) + bytes(rng.randint(0, 600))
        block = mutate(rng, lz4.block.compress(data, store_size=False)) if rng.random() < 0.8 \
            else rng.randbytes(rng.randint(1, 100))
        cases.append(struct.pack("<II", len(block), rng.choice((len(data), rng.randint(0, 2000))))
                     + block)
    return b"".join(cases)


class Target:
    def __init__(self, tmp, tabletd):
        self.tmp = pathlib.Path(tmp)
        self.key = os.urandom(32)
        key_file = self.tmp / "key"
        key_file.write_bytes(self.key)
        key_file.chmod(0o600)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.secure_port = probe.getsockname()[1]
        # a player that takes the sound and throws it away (tb-audio's stand-in)
        self.player = self.tmp / "player"
        self.player.write_text("#!/bin/sh\necho ready >&3\nexec 3>&-\nexec cat > /dev/null\n")
        self.player.chmod(0o755)
        self.touch, self.keys = self.tmp / "touch", self.tmp / "keys"
        os.mkfifo(self.touch)
        os.mkfifo(self.keys)
        self.name = f"tabletd-fuzz-{os.getpid()}"
        self.err = open(self.tmp / "stderr.txt", "wb")
        # the power key would end tabletd on purpose (exit 42); the fuzzer
        # does not send code 116
        self.homes = 0
        self.argv = (
            [str(tabletd), "--fb", str(self.tmp / "fb.raw"), "--geom", f"{W}x{H}",
             "--listen", f"abstract:{self.name}", "--input", str(self.touch),
             "--input", str(self.keys), "--status-page", "--idle-dim", "0",
             "--secure-listen", f"127.0.0.1:{self.secure_port}", "--key", str(key_file),
             "--audio", str(self.player)])
        self.proc = subprocess.Popen(self.argv, stderr=self.err)
        self.sock = None

    def wait_ready(self):
        """Both ports of a (re)started tabletd accept connections."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.proc.poll() is None:
            try:
                with socket.create_connection(("127.0.0.1", self.secure_port), timeout=1):
                    pass
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.connect("\0" + self.name)
                return
            except OSError:
                time.sleep(0.02)

    def connect(self):
        deadline = time.monotonic() + 5
        while True:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect("\0" + self.name)
                sock.settimeout(0.5)
                self.sock = sock
                return
            except OSError:
                if self.proc.poll() is not None and self.restart_after_home():
                    continue
                if time.monotonic() > deadline or self.proc.poll() is not None:
                    raise
                time.sleep(0.01)

    def send(self, data):
        if self.sock is None:
            self.connect()
        try:
            self.sock.sendall(data)
            self.sock.setblocking(False)
            try:
                while self.sock.recv(65536):
                    pass
            except (BlockingIOError, InterruptedError):
                pass
            self.sock.settimeout(0.5)
        except OSError:
            pass
        else:
            return
        self.sock.close()       # tabletd rejected the input: start over
        self.sock = None

    def secure_round(self, rng):
        """One connection to the encrypted port; returns the bytes sent."""
        sent = []
        try:
            sock = socket.create_connection(("127.0.0.1", self.secure_port), timeout=1)
        except OSError:
            return b""
        sock.settimeout(1)
        try:
            choice = rng.random()
            if choice < 0.2:
                sent.append(rng.randbytes(rng.randint(1, 300)))
                sock.sendall(sent[-1])
            elif choice < 0.4:
                hello = b"HWC1" + rng.choice((bytes(32), b"\xff" * 32, rng.randbytes(32)))
                hello = mutate(rng, hello) if rng.random() < 0.5 else hello
                sent.append(hello + rng.randbytes(rng.randint(0, 200)))
                sock.sendall(sent[-1])
            else:
                key = self.key if rng.random() < 0.9 else os.urandom(32)
                channel = secure.client_handshake(sock, key)
                for _ in range(rng.randint(1, 12)):
                    plain = message_round(rng) if rng.random() < 0.7 else rng.randbytes(rng.randint(1, 3000))
                    record = channel.seal(plain)
                    if rng.random() < 0.3:
                        record = mutate(rng, record)
                    sent.append(record)
                    sock.sendall(record)
            sock.setblocking(False)
            try:
                while sock.recv(65536):
                    pass
            except (BlockingIOError, InterruptedError):
                pass
        except (OSError, secure.SecureError):
            pass
        finally:
            sock.close()
        return b"".join(sent)

    def feed(self, path, data):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:            # no reader yet: a tabletd just restarted
            if exc.errno == errno.ENXIO:
                return
            raise
        try:
            os.write(fd, data[:4096])
        except BlockingIOError:
            pass
        finally:
            os.close(fd)

    def restart_after_home(self):
        """A HOME message (random bytes can form one) ends tabletd on purpose:
        status 0 and its log line. Then it is started again and True is
        returned; every other end is left for finding()."""
        if self.proc.poll() is None or self.proc.returncode != 0:
            return False
        if b"back to the start screen" not in (self.tmp / "stderr.txt").read_bytes()[-300:]:
            return False
        self.homes += 1
        if self.sock:
            self.sock.close()
        self.sock = None
        self.proc = subprocess.Popen(self.argv, stderr=self.err)
        self.wait_ready()
        return True

    def finding(self):
        if self.proc.poll() is not None and not self.restart_after_home():
            return f"tabletd ended with status {self.proc.returncode}"
        log = (self.tmp / "stderr.txt").read_bytes()
        if b"Sanitizer" in log or b"runtime error" in log:
            return "sanitizer report"
        return None

    def stderr_tail(self):
        return (self.tmp / "stderr.txt").read_bytes()[-4000:].decode(errors="replace")

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.err.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=60)
    parser.add_argument("--seed", type=int, default=int(time.time()))
    args = parser.parse_args()
    rng = random.Random(args.seed)
    print(f"seed {args.seed}, {args.minutes} minutes, start {time.strftime('%H:%M:%S')}", flush=True)

    counts = {"messages": 0, "events": 0, "blocks": 0, "secure": 0}
    sent = 0
    with tempfile.TemporaryDirectory() as tmp:
        # private copies: a rebuild during the run must not pull the programs away
        tabletd = shutil.copy2(TABLETD, pathlib.Path(tmp) / "tabletd")
        lz4cli = shutil.copy2(LZ4CLI, pathlib.Path(tmp) / "lz4cli")
        target = Target(tmp, tabletd)
        target.connect()   # tabletd opens its inputs before it listens: now they exist
        start = time.monotonic()
        next_report = 300
        problem = None
        try:
            while time.monotonic() - start < args.minutes * 60 and not problem:
                choice = rng.random()
                if choice < 0.45:
                    kind, data = "messages", message_round(rng)
                    target.send(data)
                elif choice < 0.65:
                    kind, data = "secure", target.secure_round(rng)
                elif choice < 0.9:
                    kind, data = "events", event_round(rng)
                    data = data.replace(struct.pack("<H", 116), struct.pack("<H", 117))
                    target.feed(rng.choice((target.touch, target.keys)), data)
                else:
                    kind, data = "blocks", block_round(rng)
                    done = subprocess.run([str(lz4cli)], input=data, capture_output=True, timeout=60)
                    if done.returncode != 0 or done.stderr:
                        problem = "lz4cli: " + done.stderr.decode(errors="replace")[-2000:]
                counts[kind] += 1
                sent += len(data)
                problem = problem or target.finding()
                elapsed = time.monotonic() - start
                if elapsed >= next_report:
                    print(f"{time.strftime('%H:%M:%S')} rounds {counts}, {sent / 1e6:.0f} MB", flush=True)
                    next_report += 300
            if not problem:
                time.sleep(0.5)
                problem = target.finding()
            if problem:
                crash = ROOT / "docs" / f"fuzz-finding-{args.seed}.bin"
                crash.write_bytes(data)
                print(f"FINDING in a '{kind}' round: {problem}\ninput saved to {crash}\n"
                      f"{target.stderr_tail()}")
                return 1
        finally:
            target.close()
    print(f"end {time.strftime('%H:%M:%S')}: no finding. rounds {counts}, {sent / 1e6:.0f} MB of input, "
          f"{target.homes} intended ends by HOME (restarted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
