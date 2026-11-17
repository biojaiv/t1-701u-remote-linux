"""Desktop portal session: screen cast plus pointer input (plan section 6.6).

One combined RemoteDesktop/ScreenCast session. The user confirms a KDE
dialog once; after that the restore token makes later starts silent.

Everything here runs in the GLib main loop. Nothing the tablet sends reaches
D-Bus except pointer positions, button states and scroll steps.
"""
import os
import pathlib
import secrets

import dbus
from dbus.mainloop.glib import DBusGMainLoop

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"


def token_file():
    base = os.environ.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "huawai-companion" / "restore_token"


DEVICE_KEYBOARD, DEVICE_POINTER, DEVICE_TOUCHSCREEN = 1, 2, 4
SOURCE_MONITOR = 1
CURSOR_EMBEDDED = 2
PERSIST_UNTIL_REVOKED = 2


def load_token():
    try:
        return token_file().read_text().strip() or None
    except OSError:
        return None


def save_token(token):
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(token + "\n")
    os.chmod(path, 0o600)


def delete_token():
    try:
        token_file().unlink()
    except OSError:
        pass


class Portal:
    """on_ready(fd, node, size) once the stream exists; size is the portal's
    (width, height) for pointer coordinates. on_closed() when the session
    ends. on_error(text) when it cannot be set up. stop_before_start=True
    ends after the steps that need no dialog (used by the probe script)."""

    def __init__(self, on_ready, on_closed, on_error, stop_before_start=False):
        DBusGMainLoop(set_as_default=True)
        self.on_ready, self.on_closed, self.on_error = on_ready, on_closed, on_error
        self.stop_before_start = stop_before_start
        self.bus = dbus.SessionBus()
        obj = self.bus.get_object(BUS_NAME, OBJECT_PATH)
        self.remote = dbus.Interface(obj, "org.freedesktop.portal.RemoteDesktop")
        self.cast = dbus.Interface(obj, "org.freedesktop.portal.ScreenCast")
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.session = None
        self.node = None
        self.size = None
        self.used_token = None
        self.keys_down = set()           # keysyms pressed and not yet confirmed released
        self._closed_match = None

    # -- request/response plumbing ---------------------------------------

    def _request(self, method, args, options, done):
        """Calls a portal method and passes its Response results to done()."""
        token = "hc" + secrets.token_hex(8)
        path = f"{OBJECT_PATH}/request/{self.sender}/{token}"
        match = None

        def on_response(code, results):
            match.remove()
            if code == 0:
                done(results)
            else:
                self._failed("cancelled by the user" if code == 1 else "portal error", code)

        # subscribe before calling: the path is predictable
        match = self.bus.add_signal_receiver(on_response, "Response", REQUEST_IFACE,
                                             BUS_NAME, path)
        options = dict(options)
        options["handle_token"] = token
        try:
            method(*args, dbus.Dictionary(options, signature="sv"))
        except dbus.DBusException as exc:
            match.remove()
            self._failed(exc.get_dbus_message(), 2)

    def _failed(self, text, code):
        if self.used_token and code != 1:
            # a stored token that is no longer accepted: forget it, ask again once
            delete_token()
            self.used_token = None
            self.close()
            self.start(use_token=False)
            return
        self.close()
        self.on_error(text)

    # -- session set-up ---------------------------------------------------

    def start(self, use_token=True):
        self._use_token = use_token
        session_token = "hc" + secrets.token_hex(8)
        self._request(self.remote.CreateSession, (),
                      {"session_handle_token": session_token}, self._created)

    def _created(self, results):
        self.session = dbus.ObjectPath(str(results["session_handle"]))
        self._closed_match = self.bus.add_signal_receiver(
            self._on_closed, "Closed", SESSION_IFACE, BUS_NAME, self.session)
        options = {"types": dbus.UInt32(DEVICE_KEYBOARD | DEVICE_POINTER),
                   "persist_mode": dbus.UInt32(PERSIST_UNTIL_REVOKED)}
        token = load_token() if self._use_token else None
        if token:
            options["restore_token"] = token
            self.used_token = token
        self._request(self.remote.SelectDevices, (self.session,), options, self._devices_selected)

    def _devices_selected(self, _results):
        # no persist_mode here: in a combined session it belongs to SelectDevices
        options = {"types": dbus.UInt32(SOURCE_MONITOR), "multiple": dbus.Boolean(False),
                   "cursor_mode": dbus.UInt32(CURSOR_EMBEDDED)}
        self._request(self.cast.SelectSources, (self.session,), options, self._sources_selected)

    def _sources_selected(self, _results):
        if self.stop_before_start:
            self.on_ready(None, None, None)
            return
        self._request(self.remote.Start, (self.session, ""), {}, self._started)

    def _started(self, results):
        streams = results.get("streams")
        if not streams:
            self._failed("the portal returned no stream", 2)
            return
        node, props = streams[0]
        self.node = int(node)
        size = props.get("size")
        self.size = (int(size[0]), int(size[1])) if size else None
        token = results.get("restore_token")
        if token:
            save_token(str(token))
        self.used_token = None   # accepted; later failures are not the token's fault
        try:
            fd = self.cast.OpenPipeWireRemote(
                self.session, dbus.Dictionary({}, signature="sv")).take()
        except dbus.DBusException as exc:
            self._failed(exc.get_dbus_message(), 2)
            return
        self.on_ready(fd, self.node, self.size)

    def _on_closed(self, *_args):
        self.session = None
        self._drop_closed_match()
        self.on_closed()

    def _drop_closed_match(self):
        if self._closed_match:
            self._closed_match.remove()
            self._closed_match = None

    def close(self):
        self._drop_closed_match()
        if self.session:
            try:
                dbus.Interface(self.bus.get_object(BUS_NAME, self.session), SESSION_IFACE).Close()
            except dbus.DBusException:
                pass
            self.session = None

    # -- input (the interface host/pointer.py expects) ----------------------

    def _notify(self, method, *args):
        if self.session is None:
            return
        method(self.session, dbus.Dictionary({}, signature="sv"), *args,
               reply_handler=lambda *_: None, error_handler=lambda *_: None)

    def move(self, x, y):
        self._notify(self.remote.NotifyPointerMotionAbsolute,
                     dbus.UInt32(self.node), dbus.Double(x), dbus.Double(y))

    def button(self, code, pressed):
        self._notify(self.remote.NotifyPointerButton,
                     dbus.Int32(code), dbus.UInt32(1 if pressed else 0))

    def key(self, keysym):
        """Types one key (press and release) given as an X keysym.

        KDE looks the keysym up in the current layout and presses Shift or
        AltGr itself; a keysym the layout does not have is silently dropped.

        The release is sent only after KDE has answered the press. Sent back
        to back, without waiting and with errors dropped, a release sometimes
        got lost and KDE repeated the key until the next key (seen by the user,
        2026-11-16). A failed release is tried again once, and every error is
        logged.
        """
        if self.session is None:
            return
        self.keys_down.add(keysym)

        def release(*_):
            self._key_state(keysym, 0, lambda *_: self.keys_down.discard(keysym),
                            lambda error: self._release_again(keysym, error))

        def press_failed(error):
            print(f"portal: key {keysym:#x} press failed: {error}", flush=True)
            release()

        self._key_state(keysym, 1, release, press_failed)

    def _key_state(self, keysym, state, done, failed):
        self.remote.NotifyKeyboardKeysym(self.session, dbus.Dictionary({}, signature="sv"),
                                         dbus.Int32(keysym), dbus.UInt32(state),
                                         reply_handler=done, error_handler=failed)

    def _release_again(self, keysym, error):
        print(f"portal: key {keysym:#x} release failed ({error}), trying again", flush=True)
        self._key_state(keysym, 0, lambda *_: self.keys_down.discard(keysym),
                        lambda again: print(f"portal: key {keysym:#x} still not released: "
                                            f"{again}", flush=True))

    def release_all(self):
        """Releases every key KDE might still hold (end of mirroring, lost link)."""
        if self.session is None:
            self.keys_down.clear()
            return
        for keysym in list(self.keys_down):
            self._key_state(keysym, 0, lambda *_, k=keysym: self.keys_down.discard(k),
                            lambda error, k=keysym: print(f"portal: key {k:#x} not released: "
                                                          f"{error}", flush=True))

    def scroll(self, steps):
        self._notify(self.remote.NotifyPointerAxisDiscrete,
                     dbus.UInt32(0), dbus.Int32(steps))   # axis 0 = vertical
