"""The portal answers asynchronously. Switching the view quickly closed the
session while an answer was still on its way; the late answer then used the
closed session (TypeError in a D-Bus handler, 2026-11-17). Late answers are
now dropped, and a session created after close() is closed at once."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import portal  # noqa: E402


class FakeBus:
    def __init__(self):
        self.handlers = {}
        self.closed = []

    def add_signal_receiver(self, handler, signal, iface, bus_name, path):
        self.handlers[path] = handler
        bus = self

        class Match:
            def remove(self_inner):
                bus.handlers.pop(path, None)
        return Match()

    def get_object(self, _name, path):
        return path


class FakeMethod:
    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)


def make_portal():
    p = object.__new__(portal.Portal)
    p.bus = FakeBus()
    p.sender = "1_23"
    p.session = None
    p._closed_match = None
    p._closed = False
    p.used_token = None
    p.errors, p.done = [], []
    p.on_error = p.errors.append
    return p


class LateAnswers(unittest.TestCase):
    def setUp(self):
        self.closed = []
        self.saved = portal.dbus.Interface
        test = self

        class FakeInterface:
            def __init__(self, obj, iface=None):
                self.path = obj

            def Close(self):
                test.closed.append(str(self.path))
        portal.dbus.Interface = FakeInterface

    def tearDown(self):
        portal.dbus.Interface = self.saved

    def answer(self, p, results, code=0):
        (path, handler), = p.bus.handlers.items()
        handler(code, results)

    def test_answer_after_close_is_dropped(self):
        p = make_portal()
        p.session = "/session/1"
        p._request(FakeMethod(), (p.session,), {}, p.done.append)
        p.close()                                    # the user switched the view
        self.answer(p, {"streams": []})              # must not reach done()
        self.assertEqual(p.done, [])
        self.assertEqual(p.errors, [])
        self.assertEqual(self.closed, ["/session/1"])

    def test_session_created_after_close_is_closed_at_once(self):
        p = make_portal()
        p._request(FakeMethod(), (), {}, p.done.append)   # CreateSession
        p.close()
        self.answer(p, {"session_handle": "/session/late"})
        self.assertEqual(p.done, [])
        self.assertIsNone(p.session)
        self.assertEqual(self.closed, ["/session/late"])

    def test_answers_while_open_still_arrive(self):
        p = make_portal()
        p._request(FakeMethod(), (), {}, p.done.append)
        self.answer(p, {"x": 1})
        self.assertEqual(p.done, [{"x": 1}])


if __name__ == "__main__":
    unittest.main()
