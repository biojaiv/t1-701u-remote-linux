#!/usr/bin/env python3
"""Lets only this laptop reach the tablet's network ports.

Ports 7100 (tabletd, encrypted) and 5555 (adb over Wi-Fi, if switched on)
accept connections only from the laptop's own addresses in the tablet's
network; everything else is dropped. Needs root on the tablet, which with
KingRoot means: Android must be running. The rules last until the next
reboot.

Usage: firewall.py TABLET_IP [--show]
"""
import argparse
import ipaddress
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from host import link  # noqa: E402

PORTS = (7100, 5555)
CHAIN = "tabletd_in"


def laptop_addresses(tablet_ip):
    """IPv4 addresses of this laptop in the same network as the tablet."""
    out = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"],
                         capture_output=True, text=True, check=True).stdout
    found = []
    for line in out.splitlines():
        interface = ipaddress.ip_interface(line.split()[3])
        if tablet_ip in interface.network:
            found.append(str(interface.ip))
    return found


def usb_serial():
    """The one tablet on USB that adb can use (the shared rule in host.link)."""
    serial = link.usb_serial()
    if serial is None:
        sys.exit("firewall: need exactly one tablet on USB in state 'device' (adb devices -l)")
    return serial


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tablet_ip", type=ipaddress.IPv4Address)
    parser.add_argument("--show", action="store_true", help="only print the rules")
    args = parser.parse_args()

    allowed = laptop_addresses(args.tablet_ip)
    if not allowed:
        sys.exit(f"firewall: this laptop has no address in the network of {args.tablet_ip}")
    commands = [f"iptables -N {CHAIN} 2>/dev/null", f"iptables -F {CHAIN}"]
    commands += [f"iptables -A {CHAIN} -s {ip} -j ACCEPT" for ip in allowed]
    commands.append(f"iptables -A {CHAIN} -j DROP")
    for port in PORTS:
        # remove an earlier hook first, so the rule exists exactly once
        commands.append(f"while iptables -D INPUT -p tcp --dport {port} -j {CHAIN} "
                        f"2>/dev/null; do :; done")
        commands.append(f"iptables -I INPUT 1 -p tcp --dport {port} -j {CHAIN}")
    commands.append(f"iptables -L {CHAIN} -n; iptables -L INPUT -n")
    script = "; ".join(commands)
    if args.show:
        print(script.replace("; ", "\n"))
        return 0
    result = subprocess.run(["adb", "-s", usb_serial(), "shell", f"su -c '{script}'"],
                            capture_output=True, text=True, timeout=40)
    out = result.stdout.replace("\r", "")
    if "timed out" in out or "tabletd_in" not in out:
        sys.exit(f"firewall: rules not set (is Android running? su needs it):\n{out}")
    hooks = sum(1 for line in out.splitlines()
                if line.startswith(CHAIN) and "dpt:" in line)
    print(f"firewall: ports {PORTS} only for {', '.join(allowed)}; hooks in INPUT: {hooks}")
    return 0 if hooks == len(PORTS) else 1


if __name__ == "__main__":
    sys.exit(main())
