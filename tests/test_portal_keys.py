"""portal.Portal.key: the release follows only after KDE has taken the press,
a failed release is tried again, release_all lets go of everything."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from host import portal  # noqa: E402


class FakeRemote:
    """Records NotifyKeyboardKeysym calls; answers are given by the test."""
    def __init__(self):
        self.calls = []          # (keysym, state, reply_handler, error_handler)

    def NotifyKeyboardKeysym(self, session, options, keysym, state, reply_handler, error_handler):
        self.calls.append((int(keysym), int(state), reply_handler, error_handler))


def make_portal():
    p = object.__new__(portal.Portal)
    p.session = "/session"
    p.remote = FakeRemote()
    p.keys_down = set()
    return p


class Keys(unittest.TestCase):
    def test_release_waits_for_the_answer_to_the_press(self):
        p = make_portal()
        p.key(0x61)
        self.assertEqual([(c[0], c[1]) for c in p.remote.calls], [(0x61, 1)])
        p.remote.calls[0][2]()                      # KDE answers the press
        self.assertEqual([(c[0], c[1]) for c in p.remote.calls], [(0x61, 1), (0x61, 0)])
        self.assertIn(0x61, p.keys_down)
        p.remote.calls[1][2]()                      # ... and the release
        self.assertEqual(p.keys_down, set())

    def test_failed_press_still_releases(self):
        p = make_portal()
        p.key(0x61)
        p.remote.calls[0][3]("timeout")
        self.assertEqual((p.remote.calls[1][0], p.remote.calls[1][1]), (0x61, 0))

    def test_failed_release_is_tried_again(self):
        p = make_portal()
        p.key(0xFF08)
        p.remote.calls[0][2]()
        p.remote.calls[1][3]("busy")                # release failed
        self.assertEqual((p.remote.calls[2][0], p.remote.calls[2][1]), (0xFF08, 0))
        p.remote.calls[2][2]()
        self.assertEqual(p.keys_down, set())

    def test_release_all(self):
        p = make_portal()
        p.key(0x61)
        p.key(0x62)                                  # neither answered yet
        p.release_all()
        released = [c[0] for c in p.remote.calls if c[1] == 0]
        self.assertEqual(sorted(released), [0x61, 0x62])

    def test_no_session_no_call(self):
        p = make_portal()
        p.session = None
        p.key(0x61)
        self.assertEqual(p.remote.calls, [])


if __name__ == "__main__":
    unittest.main()
