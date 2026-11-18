"""Settings of hostd in ~/.config/huawai-companion/hostd.toml.

Command-line options override the file. hostd itself writes only one value
back: the tablet's Wi-Fi address, which it reads over the cable whenever the
tablet is connected by USB (so a new address from the router is picked up).
"""
import ipaddress
import os
import re
import pathlib
import tomllib

DEFAULTS = {
    "wifi_host": "",                  # empty: cable only
    "rotation": "counterclockwise",
    "mode": "mirror",
    "cable_fallback": True,
    "internet_port": 0,               # 0: the tablet cannot call from outside
    "internet_call_port": 0,          # the port the tablet calls; 0: internet_port
    "internet_interface": "",         # network card the router opens; "": automatic
    "audio": True,                    # the laptop's sound also on the tablet
}
ROTATIONS = ("counterclockwise", "clockwise")
MODES = ("mirror", "monitor", "macros")

TEMPLATE = """# Settings of hostd (laptop side of the Huawei tablet companion).
# Command-line options override these values.

# Address of the tablet in the Wi-Fi network. Empty: only the USB cable.
# hostd updates it itself when the tablet is connected by cable.
wifi_host = "{wifi_host}"

# How the picture is turned for the tablet: "counterclockwise" (tablet lying
# with the USB port on the left) or "clockwise" (USB port on the right).
rotation = "{rotation}"

# View after the start: "mirror", "monitor" or "macros".
mode = "{mode}"

# Use the USB cable when the Wi-Fi connection does not work.
cable_fallback = {cable_fallback}

# Port on which the tablet may call this laptop from another network (through
# a port forwarding of the router to this laptop). 0: off. The connection is
# encrypted and must prove the pairing key, like the Wi-Fi link.
internet_port = {internet_port}

# The port the tablet calls, if the router opens another one than
# internet_port: 443 with the laptop's redirect (scripts/port443.nft), for
# networks that let nothing else out. 0: the same as internet_port.
internet_call_port = {internet_call_port}

# The network card whose IPv6 addresses hostd sends the tablet at every
# connection (the tablet calls them from another network): the one the
# router opens to the internet, for example "eth0" or "enp3s0". Empty: the one with
# the preferred default route.
internet_interface = "{internet_interface}"

# Play the laptop's sound on the tablet too (the laptop keeps playing it).
# About 190 KB/s over Wi-Fi and cable, 48 KB/s (24 kHz mono) over the internet.
audio = {audio}
"""


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "huawai-companion"


def path():
    return config_dir() / "hostd.toml"


class ConfigError(Exception):
    pass


def validate(values):
    host = values["wifi_host"]
    if host:
        try:
            ipaddress.IPv4Address(host)
        except ValueError as exc:
            raise ConfigError(f"wifi_host: not an IPv4 address: {host!r}") from exc
    if values["rotation"] not in ROTATIONS:
        raise ConfigError(f"rotation must be one of {ROTATIONS}")
    if values["mode"] not in MODES:
        raise ConfigError(f"mode must be one of {MODES}")
    if not isinstance(values["cable_fallback"], bool):
        raise ConfigError("cable_fallback must be true or false")
    if not isinstance(values["audio"], bool):
        raise ConfigError("audio must be true or false")
    port = values["internet_port"]
    if isinstance(port, str) and port.isdigit():
        port = values["internet_port"] = int(port)
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ConfigError("internet_port must be a number from 0 to 65535")
    if port and port < 1024:
        raise ConfigError("internet_port: use a port of 1024 or above")
    call = values["internet_call_port"]
    if isinstance(call, str) and call.isdigit():
        call = values["internet_call_port"] = int(call)
    if (not isinstance(call, int) or isinstance(call, bool) or
            not (call in (0, 443) or 1024 <= call <= 65535)):
        raise ConfigError("internet_call_port must be 0, 443 or from 1024 to 65535")
    interface = values["internet_interface"]
    if not isinstance(interface, str) or (interface and
                                          not re.match(r"^[A-Za-z0-9_.:@-]{1,15}$", interface)):
        raise ConfigError("internet_interface: not a network card name")
    return values


def load(file=None):
    """The settings: defaults, overridden by the file if it exists."""
    file = pathlib.Path(file or path())
    values = dict(DEFAULTS)
    try:
        with open(file, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return values
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{file}: {exc}") from exc
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise ConfigError(f"{file}: unknown settings {sorted(unknown)}")
    values.update(data)
    return validate(values)


def save(values, file=None):
    """Writes all settings (atomically; comments come from the template)."""
    file = pathlib.Path(file or path())
    values = validate(dict(DEFAULTS, **values))
    file.parent.mkdir(parents=True, exist_ok=True)
    text = TEMPLATE.format(wifi_host=values["wifi_host"], rotation=values["rotation"],
                           mode=values["mode"],
                           cable_fallback="true" if values["cable_fallback"] else "false",
                           internet_port=values["internet_port"],
                           internet_call_port=values["internet_call_port"],
                           internet_interface=values["internet_interface"],
                           audio="true" if values["audio"] else "false")
    tmp = file.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, file)


def update(file=None, **changes):
    values = load(file)
    values.update(changes)
    save(values, file)
    return values
