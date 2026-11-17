"""hostd's main loop survives an error in one round (2026-11-17: an
exception in pump() ended the loop, hostd ran on and did nothing)."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import main  # noqa: E402


class FakeSession:
    connected = True

    def __init__(self):
        self.dropped = []

    def drop(self, reason):
        self.dropped.append(reason)
        return [("disconnected", reason)]


class PumpLoop(unittest.TestCase):
    def setUp(self):
        self.scheduled = []
        self._saved = main.GLib.timeout_add
        main.GLib.timeout_add = lambda ms, fn: self.scheduled.append((ms, fn))
        self.hostd = object.__new__(main.Hostd)
        self.hostd.session = FakeSession()
        self.disconnects = []
        self.hostd.on_disconnected = self.disconnects.append
        self.hostd.pump_interval_ms = lambda now: 20

    def tearDown(self):
        main.GLib.timeout_add = self._saved

    def test_error_in_a_round_is_survived(self):
        def broken():
            raise ValueError("could not broadcast input array")
        self.hostd.pump = broken
        self.assertIs(self.hostd.run_pump(), False)
        self.assertEqual(len(self.scheduled), 1, "no next round planned")
        self.assertEqual(len(self.hostd.session.dropped), 1)
        self.assertEqual(len(self.disconnects), 1)

    def test_normal_round_keeps_the_connection(self):
        self.hostd.pump = lambda: None
        self.hostd.run_pump()
        self.assertEqual(self.scheduled, [(20, self.hostd.run_pump)])
        self.assertEqual(self.hostd.session.dropped, [])


if __name__ == "__main__":
    unittest.main()


class BackStartsFresh(unittest.TestCase):
    """After "Back" in a menu, the remote control opened again starts with the
    start view (the laptop's picture), not with the menu it was left from."""

    def test_back_resets_the_view_and_asks_for_home(self):
        sent = []

        class Session:
            height, width = 1024, 600

            def send_message(self, msg_type, payload=b""):
                sent.append(msg_type)

        class Menu:
            def tap(self, x, y):
                return main.modes.BACK

        hostd = object.__new__(main.Hostd)
        hostd.session = Session()
        hostd.rotation = "counterclockwise"
        hostd.mode, hostd.start_mode = "macros", "mirror"
        hostd.screens = {"macros": Menu()}
        hostd.on_touch(0, main.pointer.DOWN, 10, 10)
        self.assertEqual(sent, [main.link.HOME])
        self.assertEqual(hostd.mode, "mirror")
