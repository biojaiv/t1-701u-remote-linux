"""Turns a new frame into the rectangles tabletd has to redraw (step P2.3).

A frame is a numpy array of shape (height, width) with RGB565 values, in
framebuffer coordinates. The encoder compares it with the last frame the
tablet acknowledged, in tiles of 60x64 pixels, joins neighbouring changed
tiles into rectangles and compresses each rectangle with LZ4 if that makes
it smaller.
"""
import struct

import lz4.block
import numpy as np

from . import link

TILE_W, TILE_H = 60, 64
FULL_FRAME_SHARE = 0.6   # from this share of changed tiles on: send everything


def changed_tiles(old, new):
    """Boolean array (tile rows, tile columns): True where pixels differ."""
    height, width = new.shape
    rows, cols = -(-height // TILE_H), -(-width // TILE_W)
    diff = old != new
    padded = np.zeros((rows * TILE_H, cols * TILE_W), dtype=bool)
    padded[:height, :width] = diff
    return padded.reshape(rows, TILE_H, cols, TILE_W).any(axis=(1, 3))


def merge_tiles(mask):
    """Joins changed tiles into rectangles of tiles: list of (row, col, rows, cols).

    Runs of changed tiles in a row are joined; equal runs in consecutive rows
    are stacked. Every changed tile is covered exactly once.
    """
    rects, open_runs = [], {}   # open_runs: (col, cols) -> index into rects
    for row in range(mask.shape[0]):
        runs, col = [], 0
        while col < mask.shape[1]:
            if mask[row, col]:
                start = col
                while col < mask.shape[1] and mask[row, col]:
                    col += 1
                runs.append((start, col - start))
            else:
                col += 1
        still_open = {}
        for run in runs:
            if run in open_runs:
                index = open_runs[run]
                top, left, height, width = rects[index]
                rects[index] = (top, left, height + 1, width)
            else:
                index = len(rects)
                rects.append((row, run[0], 1, run[1]))
            still_open[run] = index
        open_runs = still_open
    return rects


def pack_rect(frame, x, y, width, height, strong=False):
    """One RECT payload for the given area of the frame.

    strong: spend a few milliseconds more on compression (for the slower
    Wi-Fi link). The tablet decodes both kinds equally fast.
    """
    raw = np.ascontiguousarray(frame[y:y + height, x:x + width]).astype("<u2").tobytes()
    packed = lz4.block.compress(raw, mode="fast", store_size=False)
    if strong and len(packed) < 0.9 * len(raw):
        # only where it pays: pictures that barely compress are not retried
        better = lz4.block.compress(raw, mode="high_compression", compression=4,
                                    store_size=False)
        packed = min(packed, better, key=len)
    if len(packed) < len(raw):
        fmt, data = link.FMT_RGB565_LZ4, packed
    else:
        fmt, data = link.FMT_RGB565, raw
    return struct.pack("<HHHHB", x, y, width, height, fmt) + data


# -- moved areas (scrolling) -----------------------------------------------------------

COPY_MIN_SHARE = 0.15     # look for a moved area only when this share of tiles changed
COPY_MIN_RUN = 32         # pixels: smaller moved areas are not worth a COPY
_WEIGHTS = {}


def _line_hashes(block, axis):
    """One number per column (axis 1) or row (axis 0) of block; equal lines
    give equal numbers, different ones almost surely different numbers."""
    n = block.shape[0] if axis == 1 else block.shape[1]
    w = _WEIGHTS.get(n)
    if w is None:
        w = _WEIGHTS[n] = np.random.default_rng(n).random(n) + 0.5
    return w @ block.astype(np.float64) if axis == 1 else block.astype(np.float64) @ w


def _longest_run(flags):
    """(start, length) of the longest run of True in a 1-D bool array."""
    if not flags.any():
        return 0, 0
    padded = np.concatenate(([False], flags, [False])).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    starts, ends = edges[0::2], edges[1::2]
    best = int(np.argmax(ends - starts))
    return int(starts[best]), int(ends[best] - starts[best])


def _move_in_band(o, n):
    """Columns of o that reappear shifted sideways in n (both: a band of rows).
    Returns (d, first column in n, length) or None."""
    ho, hn = _line_hashes(o, 1), _line_hashes(n, 1)
    values, first, counts = np.unique(ho, return_index=True, return_counts=True)
    pos = np.clip(np.searchsorted(values, hn), 0, len(values) - 1)
    # lines that occur exactly once in old: unambiguous votes for a shift
    hit = (values[pos] == hn) & (counts[pos] == 1)
    shifts = np.flatnonzero(hit) - first[pos[hit]]
    shifts = shifts[shifts != 0]
    if shifts.size == 0:
        return None
    width = n.shape[1]
    votes = np.bincount(shifts + width)
    d = int(np.argmax(votes)) - width
    if votes[d + width] < 8:
        return None
    lo, hi = max(0, d), min(width, width + d)            # columns of n with a source
    if hi - lo < COPY_MIN_RUN:
        return None
    start, length = _longest_run((o[:, lo - d:hi - d] == n[:, lo:hi]).all(axis=0))
    if length < COPY_MIN_RUN:
        return None
    return d, lo + start, length


BAND = 64   # rows per band when looking for moved columns


def _find_columns_move(old, new):
    """Moved columns in two equally sized pictures: (sx, sy, w, h, dx, dy) or None.

    Searched in bands of rows, so that parts that do not move with the rest
    (a scroll bar, a side panel) do not spoil the search; the best band is
    then extended up and down as far as the moved columns really match."""
    height = new.shape[0]
    best = None
    for top in range(0, height, BAND):
        found = _move_in_band(old[top:top + BAND], new[top:top + BAND])
        if found and (best is None or found[2] > best[1][2]):
            best = (top, found)
    if best is None:
        return None
    top, (d, x, w) = best
    bottom = min(height, top + BAND)

    def row_matches(y):
        return bool((new[y, x:x + w] == old[y, x - d:x - d + w]).all())

    while top > 0 and row_matches(top - 1):
        top -= 1
    while bottom < height and row_matches(bottom):
        bottom += 1
    # the band itself may contain rows that do not match (only the columns as
    # a whole were compared): keep the longest matching stretch
    rows = (new[top:bottom, x:x + w] == old[top:bottom, x - d:x - d + w]).all(axis=1)
    best_start, best_len = _longest_run(rows)
    if best_len < COPY_MIN_RUN // 2:
        return None
    y = top + best_start
    return (x - d, y, w, best_len, x, y)


def find_move(old, new, box, axis):
    """Looks for an area of old that reappears shifted in new along axis
    (1: columns move left/right, 0: rows move up/down), inside box =
    (x0, y0, x1, y1). Returns (sx, sy, w, h, dx, dy), checked pixel by pixel,
    or None."""
    x0, y0, x1, y1 = box
    o, n = old[y0:y1, x0:x1], new[y0:y1, x0:x1]
    if axis == 1:
        found = _find_columns_move(o, n)
        if found is None:
            return None
        sx, sy, w, h, dx, dy = found
    else:
        found = _find_columns_move(o.T, n.T)
        if found is None:
            return None
        sy, sx, h, w, dy, dx = found          # transposed back
    return (x0 + sx, y0 + sy, w, h, x0 + dx, y0 + dy)


# -- approximate moves (scaled pictures) ----------------------------------------------
#
# KDE delivers 1745x1091 here and hostd scales it to the tablet with a factor
# that is not a whole number; a scroll by n laptop pixels is then a shift by a
# fraction of a pixel, and no exact copy exists. The encoder then copies by the
# nearest whole shift and lets tiles that differ only a little stay as they are
# while the movement goes on; once a frame comes without movement, those tiles
# are sent exactly ("refined"). During scrolling the picture is slightly soft.

APPROX_MAX_SHIFT = 400        # pixels
APPROX_TILE_ERROR = 5.0       # mean difference per colour channel (5/6-bit units);
                              # chosen by measurement: 1.5 copied 6 of 21 frames, 5 copied 19


def luminance(frame):
    """Brightness per pixel, 2R + G + 2B in the 5/6-bit units (0..187), float32."""
    v = frame.astype(np.int32)
    return (((v >> 11) & 31) * 2 + ((v >> 5) & 63) + (v & 31) * 2).astype(np.float32)


def find_approx_move(lum_old, lum_new, box, axis):
    """Best whole shift of the box's content along axis by its brightness
    profile (one value per column or row): (sx, sy, w, h, dx, dy) or None.
    Not checked pixel by pixel."""
    x0, y0, x1, y1 = box
    po = lum_old[y0:y1, x0:x1].mean(axis=0 if axis == 1 else 1)
    pn = lum_new[y0:y1, x0:x1].mean(axis=0 if axis == 1 else 1)
    length = len(pn)
    if length < 2 * COPY_MIN_RUN or po.std() < 1e-6:
        return None
    base = float(np.abs(pn - po).mean())
    reach = min(APPROX_MAX_SHIFT, length - COPY_MIN_RUN)
    # all shifts at once: windows of the padded old profile against the new one
    padded = np.concatenate((np.full(reach, np.nan), po, np.full(reach, np.nan)))
    windows = np.lib.stride_tricks.sliding_window_view(padded, length)[::-1]
    errors = np.nanmean(np.abs(windows - pn), axis=1)    # index i: shift d = i - reach
    errors[reach] = np.inf                                # d = 0 is no move
    best_i = int(np.nanargmin(errors))
    best_d, best_err = best_i - reach, float(errors[best_i])
    if not np.isfinite(best_err) or best_err > 0.25 * base:   # no clearly better match
        return None
    d = best_d
    lo, hi = max(0, d), min(length, length + d)
    if axis == 1:
        return (x0 + lo - d, y0, hi - lo, y1 - y0, x0 + lo, y0)
    return (x0, y0 + lo - d, x1 - x0, hi - lo, x0, y0 + lo)


def tile_errors(lum_a, lum_b):
    """Mean difference per tile and colour channel (5/6-bit units), estimated
    from the brightness (2R + G + 2B: five channel units)."""
    height, width = lum_a.shape
    diff = np.abs(lum_a - lum_b) / 5.0
    rows, cols = -(-height // TILE_H), -(-width // TILE_W)
    padded = np.zeros((rows * TILE_H, cols * TILE_W), dtype=np.float32)
    padded[:height, :width] = diff
    return padded.reshape(rows, TILE_H, cols, TILE_W).mean(axis=(1, 3))


def apply_move(frame, move):
    sx, sy, w, h, dx, dy = move
    frame[dy:dy + h, dx:dx + w] = frame[sy:sy + h, sx:sx + w].copy()


class Encoder:
    def __init__(self, width, height, strong=False, ordered=False, copy=False):
        """ordered: every frame sent reaches the tablet in order unless the
        connection breaks (TCP), and after a break a new Encoder starts.
        Then each frame can be compared with the previous one sent, without
        waiting for its acknowledgement; that is what allows more than one
        frame in flight."""
        self.width, self.height = width, height
        self.strong, self.ordered = strong, ordered
        self.copy = copy and ordered   # COPY needs the order of the frames (TCP)
        self.moves = 0                 # COPY messages sent (statistics)
        self.approx = None             # tiles the tablet shows only approximately
        self._last_way = None          # (axis, exact) of the last move found: tried first
        self.sent = None   # last frame sent (ordered mode)
        self.acked = None      # last frame the tablet confirmed
        self.pending = None    # frame sent, confirmation outstanding
        self.uncertain = None  # tiles an unconfirmed frame may have changed

    def reset(self):
        """After a reconnect the tablet's screen content is unknown."""
        self.acked = self.pending = self.uncertain = None

    def encode(self, frame):
        """Returns the RECT payloads that turn the acked frame into this one.

        An empty list means: nothing changed, nothing needs to be sent.
        """
        if frame.shape != (self.height, self.width):
            raise ValueError(f"frame has shape {frame.shape}")
        if self.ordered:
            return self._encode_ordered(frame)
        if self.acked is None:
            areas = [(0, 0, self.width, self.height)]
        else:
            if self.pending is not None:
                # The previous frame was never confirmed. The tablet may show
                # its tiles or the old ones, so they all have to be resent.
                lost = changed_tiles(self.acked, self.pending)
                self.uncertain = lost if self.uncertain is None else self.uncertain | lost
            mask = changed_tiles(self.acked, frame)
            if self.uncertain is not None:
                mask |= self.uncertain
            if not mask.any():
                self.pending = None
                return []
            if mask.mean() >= FULL_FRAME_SHARE:
                areas = [(0, 0, self.width, self.height)]
            else:
                areas = []
                for row, col, rows, cols in merge_tiles(mask):
                    x, y = col * TILE_W, row * TILE_H
                    areas.append((x, y, min(cols * TILE_W, self.width - x),
                                  min(rows * TILE_H, self.height - y)))
        self.pending = frame.copy()
        return [pack_rect(frame, *area, strong=self.strong) for area in areas]

    def _encode_ordered(self, frame):
        parts = []
        if self.sent is None:
            areas = [(0, 0, self.width, self.height)]
        else:
            mask = changed_tiles(self.sent, frame)
            if not mask.any():
                return []
            moved = False
            if self.copy and mask.mean() >= COPY_MIN_SHARE:
                parts, mask, moved = self._moves(frame, mask)
            if self.approx is not None and not moved:
                # the movement has stopped: now the exact pixels of those tiles
                mask = mask | (self.approx & changed_tiles(self.sent, frame))
                self.approx = None
            if mask.mean() >= FULL_FRAME_SHARE:
                areas = [(0, 0, self.width, self.height)]
            else:
                areas = []
                for row, col, rows, cols in merge_tiles(mask):
                    x, y = col * TILE_W, row * TILE_H
                    areas.append((x, y, min(cols * TILE_W, self.width - x),
                                  min(rows * TILE_H, self.height - y)))
        if self.approx is not None:
            # the tablet keeps showing those tiles approximately: remember what
            # it really shows there, so that the refinement sends them
            shown = self.sent
            self.sent = frame.copy()
            approx_pixels = np.repeat(np.repeat(self.approx, TILE_H, axis=0), TILE_W, axis=1)
            approx_pixels = approx_pixels[:self.height, :self.width] & ~self._sent_mask(areas)
            self.sent[approx_pixels] = shown[approx_pixels]
        else:
            self.sent = frame.copy()
        return parts + [pack_rect(frame, *area, strong=self.strong) for area in areas]

    def _sent_mask(self, areas):
        sent = np.zeros((self.height, self.width), dtype=bool)
        for x, y, w, h in areas:
            sent[y:y + h, x:x + w] = True
        return sent

    def _moves(self, frame, mask):
        """COPY parts for a scrolled area, and the tiles still to send after it.

        Only used if it saves at least a quarter of the changed tiles."""
        differs = self.sent != frame
        cols = np.flatnonzero(differs.any(axis=0))
        rows = np.flatnonzero(differs.any(axis=1))
        box = (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)
        best = None
        lum_old = lum_new = None
        ways = [(axis, exact) for axis in (1, 0) for exact in (True, False)]
        if self._last_way in ways:          # scrolling goes on the same way: try it first
            ways.remove(self._last_way)
            ways.insert(0, self._last_way)
        for axis, exact in ways:
            if exact:
                move = find_move(self.sent, frame, box, axis)
            else:
                if lum_old is None:            # once per frame, only if needed
                    lum_old, lum_new = luminance(self.sent), luminance(frame)
                move = find_approx_move(lum_old, lum_new, box, axis)
            if move is None:
                continue
            predicted = self.sent.copy()
            apply_move(predicted, move)
            rest = changed_tiles(predicted, frame)
            close = None
            if not exact:
                # tiles that differ only a little may wait for the refinement
                lum_pred = lum_old.copy()
                apply_move(lum_pred, move)
                close = rest & (tile_errors(lum_pred, lum_new) <= APPROX_TILE_ERROR)
                rest = rest & ~close
            if best is None or rest.sum() < best[2].sum():
                best = (move, predicted, rest, close, (axis, exact))
            if rest.sum() <= 0.25 * mask.sum():
                break                  # good enough: no need to try the other ways
        if best is None or best[2].sum() > 0.75 * mask.sum():
            return [], mask, False
        move, predicted, rest, close, self._last_way = best
        self.sent = predicted            # what the tablet shows after the COPY
        if close is not None and close.any():
            self.approx = close if self.approx is None else (self.approx | close)
        self.moves += 1
        return [(link.COPY, struct.pack("<6H", *move))], rest, True

    def confirm(self):
        """Call when the tablet acknowledged the frame from the last encode()."""
        if self.pending is not None:
            self.acked, self.pending, self.uncertain = self.pending, None, None
