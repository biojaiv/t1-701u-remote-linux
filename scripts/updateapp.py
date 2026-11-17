#!/usr/bin/env python3
"""List or extract the sections of a Huawei UPDATE.APP file (step P0.9).

Read-only analysis tool for the laptop. It never talks to the tablet.

Layout as observed in the B107 package: zero padding, then entries that each
start with the magic 55 AA 5A A5:

    u32 magic, u32 header_len, u32 unknown, 8s hardware_id, u32 sequence,
    u32 data_len, 16s date, 16s time, 16s type, 16s blank,
    u16 header_crc, u16 block_size, u16 blank,
    (header_len - 98) bytes of block checksums, data_len bytes of data,
    padding to a multiple of 4 bytes.

Usage:
    updateapp.py list UPDATE.APP
    updateapp.py extract UPDATE.APP TYPE OUTFILE   (first entry of that type)
"""
import hashlib
import struct
import sys

MAGIC = b"\x55\xaa\x5a\xa5"
FIXED = struct.Struct("<4sII8sII16s16s16s16sHHH")  # 98 bytes
CHUNK = 1 << 20


def text(raw):
    return raw.split(b"\0", 1)[0].decode("ascii", "replace")


def entries(f):
    """Yield (type, date, time, data_offset, data_len) for every entry."""
    f.seek(0, 2)
    size = f.tell()
    pos = 0
    while pos + FIXED.size <= size:
        f.seek(pos)
        head = f.read(FIXED.size)
        if head[:4] != MAGIC:
            # Padding between entries: move on in 4-byte steps.
            if head[:4] == b"\0\0\0\0" or pos % 4:
                pos += 4 - pos % 4 if pos % 4 else 4
                continue
            raise ValueError(f"unexpected bytes at offset {pos}: {head[:4].hex()}")
        (_, header_len, _, _, _, data_len,
         date, time, kind, _, _, _, _) = FIXED.unpack(head)
        if header_len < FIXED.size or pos + header_len + data_len > size:
            raise ValueError(f"implausible entry at offset {pos}")
        yield text(kind), text(date), text(time), pos + header_len, data_len
        pos += header_len + data_len
        pos += -pos % 4


def md5_of(f, offset, length):
    digest = hashlib.md5()
    f.seek(offset)
    while length:
        block = f.read(min(CHUNK, length))
        if not block:
            raise ValueError("file ends inside an entry")
        digest.update(block)
        length -= len(block)
    return digest.hexdigest()


def main(argv):
    if len(argv) == 3 and argv[1] == "list":
        with open(argv[2], "rb") as f:
            for kind, date, time, offset, length in list(entries(f)):
                print(f"{kind:<16} {length:>11} {date} {time} "
                      f"offset={offset} md5={md5_of(f, offset, length)}")
        return 0
    if len(argv) == 5 and argv[1] == "extract":
        with open(argv[2], "rb") as f:
            for kind, _, _, offset, length in entries(f):
                if kind == argv[3]:
                    f.seek(offset)
                    with open(argv[4], "xb") as out:
                        while length:
                            block = f.read(min(CHUNK, length))
                            out.write(block)
                            length -= len(block)
                    return 0
        print(f"no entry of type {argv[3]}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
