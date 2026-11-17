"""Settings of hostd in ~/.config/huawai-companion/hostd.toml.

Command-line options override the file. hostd itself writes only one value
back: the tablet's Wi-Fi address, which it reads over the cable whenever the
tablet is connected by USB (so a new address from the router is picked up).
"""
import ipaddress
import os
import pathlib
import tomllib

DEFAULTS = {
    "wifi_host": "",                  # empty: cable only
    "rotation": "counterclockwise",
    "mode": "mirror",
    "cable_fallback": True,
    "internet_port": 0,               # 0: the tablet cannot call from outside
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
                           audio="true" if values["audio"] else "false")
    tmp = file.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, file)


def update(file=None, **changes):
    values = load(file)
    values.update(changes)
    save(values, file)
    return values
