"""The addresses the tablet calls when it is in another network.

hostd sends them at every connection (MSG_ADDRESSES), so the tablet always
has this laptop's current IPv6 addresses: the router may give the laptop new
ones (seen during development), and before, they had to be copied by hand
("scripts/autostart.sh internet auto") before leaving.

Which addresses: the global IPv6 addresses of one network card, the one the
router opens to the internet (setting internet_interface), or else the one
with the preferred default route (lowest metric). Not the temporary privacy
addresses (they change daily and are not opened), not deprecated or
unfinished ones, and only global unicast 2000::/3 (a unique-local fd..
address is "scope global" too, but unreachable from outside). At most 4, in
the order "ip" lists them, as "internet auto" does.
"""
import ipaddress
import re
import struct
import subprocess

MAX = 4
SKIP_FLAGS = {"temporary", "deprecated", "tentative", "dadfailed"}
GLOBAL_UNICAST = ipaddress.ip_network("2000::/3")


def preferred_interface(route_output):
    """The device of the default route with the lowest metric, from
    "ip -6 route show default", or None."""
    best = None
    for line in route_output.splitlines():
        words = line.split()
        if not words or words[0] != "default" or "dev" not in words:
            continue
        dev = words[words.index("dev") + 1]
        metric = 1024
        if "metric" in words:
            try:
                metric = int(words[words.index("metric") + 1])
            except (ValueError, IndexError):
                continue
        if best is None or metric < best[0]:
            best = (metric, dev)
    return best[1] if best else None


def global_addresses(addr_output, interface):
    """The usable addresses of interface, from "ip -6 -o addr show scope global"."""
    found = []
    for line in addr_output.splitlines():
        words = line.split()
        if len(words) < 4 or words[1] != interface or words[2] != "inet6":
            continue
        if SKIP_FLAGS & set(words):
            continue
        try:
            address = ipaddress.IPv6Interface(words[3]).ip
        except ValueError:
            continue
        if address in GLOBAL_UNICAST and str(address) not in found:
            found.append(str(address))
    return found[:MAX]


def _ip(*args):
    return subprocess.run(["ip", *args], capture_output=True, text=True, timeout=2).stdout


def call_addresses(interface="", ip=_ip):
    """This laptop's addresses for the tablet's calls ([] if there are none)."""
    try:
        if not interface:
            interface = preferred_interface(ip("-6", "route", "show", "default"))
            if not interface:
                return []
        return global_addresses(ip("-6", "-o", "addr", "show", "scope", "global"), interface)
    except (OSError, subprocess.SubprocessError):
        return []


INTERFACE_NAME = re.compile(r"^[A-Za-z0-9_.:@-]{1,15}$")


def payload(port, addresses):
    """MSG_ADDRESSES: u16 port, u8 count, count x 16-byte address."""
    if not 1 <= port <= 65535 or not 1 <= len(addresses) <= MAX:
        raise ValueError("port 1..65535 and 1 to 4 addresses")
    return struct.pack("<HB", port, len(addresses)) + b"".join(
        ipaddress.IPv6Address(a).packed for a in addresses)
