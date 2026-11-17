"""Touch and key parsing: events are fed through FIFOs, as on the tablet."""
import os
import struct
import unittest

from harness import H, KEY, TOUCH, W, DaemonCase

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT, SYN_MT_REPORT = 0, 2
ABS_MT_SLOT, ABS_MT_TOUCH_MAJOR = 0x2F, 0x30
ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x35, 0x36, 0x39
BTN_TOUCH, KEY_VOLUMEDOWN, KEY_VOLUMEUP, KEY_POWER = 0x14A, 114, 115, 116
UP, DOWN, MOVE = 0, 1, 2


def ev(ev_type, code, value):
    """One event in the 16-byte layout of the tablet's 32-bit kernel."""
    return struct.pack("<IIHHi", 0, 0, ev_type, code, value)


def contact_a(x, y, tracking_id=None):
    out = b""
    if tracking_id is not None:
        out += ev(EV_ABS, ABS_MT_TRACKING_ID, tracking_id)
    out += ev(EV_ABS, ABS_MT_TOUCH_MAJOR, 5)
    out += ev(EV_ABS, ABS_MT_POSITION_X, x) + ev(EV_ABS, ABS_MT_POSITION_Y, y)
    return out + ev(EV_SYN, SYN_MT_REPORT, 0)


REPORT = ev(EV_SYN, SYN_REPORT, 0)
RELEASE_A = ev(EV_SYN, SYN_MT_REPORT, 0) + REPORT


class InputCase(DaemonCase):
    def prepare(self):
        self.touch_path = self.dir / "touch"
        self.keys_path = self.dir / "keys"
        os.mkfifo(self.touch_path)
        os.mkfifo(self.keys_path)

    def args(self):
        return ["--input", str(self.touch_path), "--input", str(self.keys_path)]

    def feed(self, path, data):
        fd = os.open(path, os.O_WRONLY)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    def touches(self, conn, count):
        return [struct.unpack("<BBHH", conn.recv_type(TOUCH)) for _ in range(count)]


class ProtocolA(InputCase):
    def test_tap_with_tracking_id(self):
        conn = self.connect()
        self.feed(self.touch_path,
                  ev(EV_KEY, BTN_TOUCH, 1) + contact_a(100, 200, 0) + REPORT +
                  contact_a(110, 205, 0) + REPORT +
                  ev(EV_KEY, BTN_TOUCH, 0) + RELEASE_A)
        self.assertEqual(self.touches(conn, 3),
                         [(0, DOWN, 100, 200), (0, MOVE, 110, 205), (0, UP, 110, 205)])

    def test_two_fingers_with_tracking_ids(self):
        conn = self.connect()
        self.feed(self.touch_path,
                  contact_a(10, 20, 7) + REPORT +
                  contact_a(10, 20, 7) + contact_a(300, 400, 9) + REPORT +
                  contact_a(300, 410, 9) + REPORT +   # finger 7 lifted
                  RELEASE_A)
        self.assertEqual(self.touches(conn, 5),
                         [(0, DOWN, 10, 20), (1, DOWN, 300, 400),
                          (0, UP, 10, 20), (1, MOVE, 300, 410), (1, UP, 300, 410)])

    def test_without_tracking_ids(self):
        conn = self.connect()
        self.feed(self.touch_path,
                  contact_a(50, 60) + REPORT + contact_a(51, 60) + REPORT + RELEASE_A)
        self.assertEqual(self.touches(conn, 3),
                         [(0, DOWN, 50, 60), (0, MOVE, 51, 60), (0, UP, 51, 60)])

    def test_unchanged_frame_is_not_reported(self):
        conn = self.connect()
        self.feed(self.touch_path,
                  contact_a(5, 6, 1) + REPORT + contact_a(5, 6, 1) + REPORT + RELEASE_A)
        self.assertEqual(self.touches(conn, 2), [(0, DOWN, 5, 6), (0, UP, 5, 6)])

    def test_coordinates_are_clamped(self):
        conn = self.connect()
        self.feed(self.touch_path, contact_a(600, 1024, 0) + REPORT + RELEASE_A)
        self.assertEqual(self.touches(conn, 2),
                         [(0, DOWN, W - 1, H - 1), (0, UP, W - 1, H - 1)])

    def test_release_frame_without_mt_report(self):
        conn = self.connect()
        self.feed(self.touch_path, contact_a(1, 2, 0) + REPORT + REPORT)
        self.assertEqual(self.touches(conn, 2), [(0, DOWN, 1, 2), (0, UP, 1, 2)])


class ProtocolB(InputCase):
    def test_tap(self):
        conn = self.connect()
        self.feed(self.touch_path,
                  ev(EV_ABS, ABS_MT_SLOT, 0) + ev(EV_ABS, ABS_MT_TRACKING_ID, 41) +
                  ev(EV_ABS, ABS_MT_POSITION_X, 300) +
                  ev(EV_ABS, ABS_MT_POSITION_Y, 500) + REPORT +
                  ev(EV_ABS, ABS_MT_POSITION_X, 310) + REPORT +
                  ev(EV_ABS, ABS_MT_TRACKING_ID, -1) + REPORT)
        self.assertEqual(self.touches(conn, 3),
                         [(0, DOWN, 300, 500), (0, MOVE, 310, 500), (0, UP, 310, 500)])

    def test_slot_zero_is_implied(self):
        conn = self.connect()
        self.feed(self.touch_path,
                  ev(EV_ABS, ABS_MT_TRACKING_ID, 3) +
                  ev(EV_ABS, ABS_MT_POSITION_X, 7) +
                  ev(EV_ABS, ABS_MT_POSITION_Y, 8) + REPORT +
                  ev(EV_ABS, ABS_MT_TRACKING_ID, -1) + REPORT)
        self.assertEqual(self.touches(conn, 2), [(0, DOWN, 7, 8), (0, UP, 7, 8)])

    def test_two_slots(self):
        conn = self.connect()
        self.feed(self.touch_path,
                  ev(EV_ABS, ABS_MT_SLOT, 0) + ev(EV_ABS, ABS_MT_TRACKING_ID, 1) +
                  ev(EV_ABS, ABS_MT_POSITION_X, 10) + ev(EV_ABS, ABS_MT_POSITION_Y, 11) +
                  ev(EV_ABS, ABS_MT_SLOT, 1) + ev(EV_ABS, ABS_MT_TRACKING_ID, 2) +
                  ev(EV_ABS, ABS_MT_POSITION_X, 20) + ev(EV_ABS, ABS_MT_POSITION_Y, 21) +
                  REPORT +
                  ev(EV_ABS, ABS_MT_SLOT, 0) + ev(EV_ABS, ABS_MT_TRACKING_ID, -1) +
                  REPORT +
                  ev(EV_ABS, ABS_MT_SLOT, 1) + ev(EV_ABS, ABS_MT_POSITION_Y, 25) +
                  REPORT +
                  ev(EV_ABS, ABS_MT_TRACKING_ID, -1) + REPORT)
        self.assertEqual(self.touches(conn, 5),
                         [(0, DOWN, 10, 11), (1, DOWN, 20, 21), (0, UP, 10, 11),
                          (1, MOVE, 20, 25), (1, UP, 20, 25)])

    def test_report_without_position_keeps_finger_down(self):
        conn = self.connect()
        self.feed(self.touch_path,
                  ev(EV_ABS, ABS_MT_TRACKING_ID, 3) +
                  ev(EV_ABS, ABS_MT_POSITION_X, 7) +
                  ev(EV_ABS, ABS_MT_POSITION_Y, 8) + REPORT +
                  ev(EV_ABS, ABS_MT_TOUCH_MAJOR, 9) + REPORT +   # pressure only
                  ev(EV_ABS, ABS_MT_TRACKING_ID, -1) + REPORT)
        self.assertEqual(self.touches(conn, 2), [(0, DOWN, 7, 8), (0, UP, 7, 8)])

    def test_out_of_range_slot_is_ignored(self):
        conn = self.connect()
        self.feed(self.touch_path,
                  ev(EV_ABS, ABS_MT_SLOT, 99) + ev(EV_ABS, ABS_MT_TRACKING_ID, 1) +
                  ev(EV_ABS, ABS_MT_POSITION_X, 1) + ev(EV_ABS, ABS_MT_POSITION_Y, 1) +
                  REPORT +
                  ev(EV_ABS, ABS_MT_SLOT, 0) + ev(EV_ABS, ABS_MT_TRACKING_ID, 2) +
                  ev(EV_ABS, ABS_MT_POSITION_X, 4) + ev(EV_ABS, ABS_MT_POSITION_Y, 5) +
                  REPORT)
        self.assertEqual(self.touches(conn, 1), [(0, DOWN, 4, 5)])


class Keys(InputCase):
    def test_volume_keys_are_reported_and_power_is_not(self):
        conn = self.connect()
        self.feed(self.keys_path,
                  ev(EV_KEY, KEY_VOLUMEUP, 1) + REPORT + ev(EV_KEY, KEY_VOLUMEUP, 0) +
                  REPORT + ev(EV_KEY, KEY_POWER, 1) + REPORT +   # handled on the tablet
                  ev(EV_KEY, KEY_POWER, 0) + REPORT +
                  ev(EV_KEY, KEY_VOLUMEDOWN, 1) + REPORT +
                  ev(EV_KEY, KEY_VOLUMEDOWN, 2) + REPORT +       # autorepeat: ignored
                  ev(EV_KEY, KEY_VOLUMEDOWN, 0) + REPORT)
        got = [struct.unpack("<HB", conn.recv_type(KEY)) for _ in range(4)]
        self.assertEqual(got, [(KEY_VOLUMEUP, 1), (KEY_VOLUMEUP, 0),
                               (KEY_VOLUMEDOWN, 1), (KEY_VOLUMEDOWN, 0)])

    def test_event_split_across_writes(self):
        conn = self.connect()
        data = ev(EV_KEY, KEY_VOLUMEDOWN, 1) + REPORT
        self.feed(self.keys_path, data[:5])
        self.feed(self.keys_path, data[5:])
        self.assertEqual(struct.unpack("<HB", conn.recv_type(KEY)), (KEY_VOLUMEDOWN, 1))


if __name__ == "__main__":
    unittest.main()
