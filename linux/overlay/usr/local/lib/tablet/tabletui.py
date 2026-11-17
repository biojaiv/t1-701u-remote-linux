"""Shared look and helpers of the tablet's own Linux programs.

Colours are those of the tablet's start screen (tablet/ui.c): dark blue
gradient, cards with a light edge, blue accent. Used by tablet-launcher,
tablet-usb-installer and tablet-settings.
"""
import fcntl
import socket
import struct

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

CSS = b"""
window.tablet {
    background-image: linear-gradient(to bottom, rgb(16, 24, 44), rgb(5, 7, 13));
    color: rgb(240, 244, 250);
}
window.tablet label { color: rgb(240, 244, 250); }
.clock { font-size: 72px; font-weight: 200; }
.title { font-size: 30px; font-weight: 300; }
.date, .sub { color: rgb(201, 209, 220); font-size: 18px; }
window.tablet .status, window.tablet .dim { color: rgb(154, 164, 178); font-size: 15px; }
window.tablet .hint { color: rgb(154, 164, 178); font-size: 13px; }
window.tablet .good { color: rgb(74, 222, 128); }
window.tablet .warn { color: rgb(251, 191, 36); }
window.tablet .bad { color: rgb(248, 113, 113); }
.card {
    background-color: rgb(24, 33, 56);
    border: 1px solid rgb(44, 58, 92);
    border-radius: 22px;
    padding: 18px;
}
button.tile, button.big {
    background-image: none;
    background-color: rgb(24, 33, 56);
    border: 1px solid rgb(44, 58, 92);
    border-radius: 22px;
    box-shadow: 0 5px 0 rgb(2, 4, 9);
    padding: 12px;
}
button.big { padding: 14px 28px; border-radius: 18px; }
button.tile:active, button.tile.starting, button.big:active,
button.big.accent, button.tile.selected {
    background-color: rgb(37, 99, 235);
    border-color: rgb(96, 165, 250);
}
button.tile label, button.big label { color: rgb(240, 244, 250); font-size: 17px; }
scale trough { min-height: 10px; border-radius: 5px; background-color: rgb(44, 58, 92); }
scale highlight { background-color: rgb(96, 165, 250); border-radius: 5px; }
scale slider { min-width: 34px; min-height: 34px; border-radius: 17px;
               background-color: rgb(240, 244, 250); }
"""

SYSD = "\0tabletd-sys"


def apply_style():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def styled(widget, *classes):
    for name in classes:
        widget.get_style_context().add_class(name)
    return widget


def label(text="", *classes, xalign=0.5, wrap=False):
    widget = Gtk.Label(label=text, xalign=xalign)
    widget.set_line_wrap(wrap)
    return styled(widget, *classes)


def full_screen_window(title, desktop=False):
    window = styled(Gtk.Window(title=title), "tablet")
    if desktop:
        window.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
        window.set_decorated(False)
    screen = Gdk.Screen.get_default()
    window.set_default_size(screen.get_width(), screen.get_height())
    return window


def wlan_address(interface=b"wlan0"):
    """Address of the Wi-Fi interface, or None (SIOCGIFADDR)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            raw = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", interface[:15]))
        return socket.inet_ntoa(raw[20:24])
    except OSError:
        return None


def battery(path="/sys/class/power_supply/battery"):
    """dict with percent, charging, temperature (°C), voltage (V); None if unreadable."""
    def read(name):
        with open(f"{path}/{name}") as f:
            return f.read().strip()
    try:
        info = {"percent": int(read("capacity")),
                "charging": read("status") in ("Charging", "Full")}
    except (OSError, ValueError):
        return None
    try:
        info["temperature"] = int(read("temp")) / 10
        info["voltage"] = int(read("voltage_now")) / 1e6
    except (OSError, ValueError):
        pass
    return info


def sysd(*words, timeout=30):
    """One request to tb-sysd (root actions); returns the reply line or 'error …'."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(SYSD)
            s.sendall((" ".join(words) + "\n").encode())
            reply = b""
            while not reply.endswith(b"\n"):
                chunk = s.recv(512)
                if not chunk:
                    break
                reply += chunk
        return reply.decode(errors="replace").strip() or "error no answer"
    except OSError as error:
        return f"error tb-sysd not reachable ({error.strerror or error})"


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
