"""huawai: one command for setting up and checking laptop and tablet.

  huawai status            what is running where
  huawai doctor            are all programs and libraries on this laptop
  huawai pair [--new]      shared key for the Wi-Fi link (USB cable needed)
  huawai up [--remote]     tablet: stop Android, start the start screen (cable
                           needed); --remote opens the remote control at once
  huawai down              tablet: end the start screen, start Android again
  huawai config [K=V ...]  show or change hostd.toml

"up" sets the tablet's firewall and pairs if needed while Android still runs
(root on the tablet only works then), mounts the Linux system and starts its
session daemon if it is installed (scripts/linux.sh), stops Android, starts
the start screen with the encrypted Wi-Fi port and remembers the tablet's
Wi-Fi address for hostd.
"""
import argparse
import importlib
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from host import config, link, secure  # noqa: E402

TABLET_KEY = "/data/local/tmp/tabletd-secret/key"
SERVICE = "huawai-hostd"
LINUX_IMAGE = "/data/tabletd/linux/devuan.img"


def run(*command, timeout=300, check=False):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        sys.exit(f"huawai: {' '.join(command)} failed:\n{result.stdout}{result.stderr}")
    return result


def adb_shell(serial, command, timeout=30):
    out = run("adb", "-s", serial, "shell", command, timeout=timeout).stdout
    return out.replace("\r", "")


def need_usb():
    serial = link.usb_serial()
    if not serial:
        sys.exit("huawai: the tablet must be connected by USB for this")
    return serial


def tablet_state(serial):
    out = adb_shell(serial, "getprop init.svc.zygote; cat /data/local/tmp/tb-home.log "
                            "/data/local/tmp/tabletd.log 2>/dev/null; cat /proc/net/tcp; ls "
                            + TABLET_KEY + " 2>&1")
    lines = out.splitlines()
    state = {"android": lines[0].strip() if lines else "?", "tb-home": None, "tabletd": None,
             "port7100": any(":1BBC " in line and " 0A " in line for line in lines),
             "key": any(line.strip() == TABLET_KEY for line in lines)}
    for line in lines:
        for name in ("tb-home", "tabletd"):
            if line.startswith(f"{name} pid "):
                state[name] = line.split()[2]
    for name in ("tb-home", "tabletd"):
        if state[name]:
            alive = adb_shell(serial, f"[ -d /proc/{state[name]} ] && echo yes").strip()
            if alive != "yes":
                state[name] = None
    state["linux"] = "@tabletd-linux" in adb_shell(serial, "cat /proc/net/unix")
    return state


def linux_installed(serial):
    """Only while Android runs: the image lies in a directory only root may read."""
    out = adb_shell(serial, f"su -c '[ -f {LINUX_IMAGE} ] && echo yes'", timeout=15)
    return out.strip() == "yes"


def start_linux(rotation):
    """Mount the Linux system and start its session daemon (needs Android for su)."""
    for step in (["mount"], ["daemon", rotation]):
        result = run("scripts/linux.sh", *step, timeout=120)
        print((result.stdout + result.stderr).strip())
        if result.returncode != 0:
            print("the Linux tile stays off this time")
            return False
    return True


# -- commands -------------------------------------------------------------------


def cmd_status(_args):
    settings = config.load()
    serial = link.usb_serial()
    print(f"settings     {config.path()}: wifi_host={settings['wifi_host'] or '-'}, "
          f"rotation={settings['rotation']}, mode={settings['mode']}")
    print(f"pairing key  {'present' if secure.key_path().exists() else 'missing'} on the laptop")
    service = run("systemctl", "--user", "is-active", SERVICE).stdout.strip()
    print(f"hostd        service {SERVICE}: {service}")
    if not serial:
        print("tablet       not connected by USB")
    else:
        state = tablet_state(serial)
        def running(name):
            return f"pid {state[name]}" if state[name] else "not running"
        print(f"tablet       USB {serial}; Android {state['android']}; "
              f"start screen {running('tb-home')}; remote control {running('tabletd')}; "
              f"encrypted port 7100 {'open' if state['port7100'] else 'closed'}; "
              f"key {'present' if state['key'] else 'missing'}; "
              f"Linux session daemon {'runs' if state['linux'] else 'not running'}")
        print(f"tablet Wi-Fi {link.tablet_wlan_address() or 'no address'}")
    return 0


def cmd_doctor(_args):
    problems = 0

    def report(ok, text, hint=""):
        nonlocal problems
        print(f"{'ok     ' if ok else 'MISSING'} {text}" + ("" if ok or not hint else f"  ({hint})"))
        problems += not ok

    for module, package in (("gi", "python3-gi"), ("dbus", "python3-dbus"),
                            ("nacl", "python3-nacl"), ("lz4", "python3-lz4"),
                            ("numpy", "python3-numpy"), ("PIL", "python3-pil"),
                            ("psutil", "python3-psutil")):
        try:
            importlib.import_module(module)
            report(True, f"Python module {module}")
        except ImportError:
            report(False, f"Python module {module}", f"sudo apt install {package}")
    for tool, package in (("adb", "adb"), ("gst-inspect-1.0", "gstreamer1.0-tools"),
                          ("systemctl", "systemd")):
        report(shutil.which(tool) is not None, f"program {tool}", f"sudo apt install {package}")
    if shutil.which("gst-inspect-1.0"):
        for element, package in (("pipewiresrc", "gstreamer1.0-pipewire"),
                                 ("videoconvert", "gstreamer1.0-plugins-base"),
                                 ("videorate", "gstreamer1.0-plugins-base"),
                                 ("appsink", "gstreamer1.0-plugins-base"),
                                 ("pulsesrc", "gstreamer1.0-pulseaudio"),
                                 ("audioresample", "gstreamer1.0-plugins-base")):
            found = run("gst-inspect-1.0", element).returncode == 0
            report(found, f"GStreamer element {element}", f"sudo apt install {package}")
    portal = run("busctl", "--user", "get-property", "org.freedesktop.portal.Desktop",
                 "/org/freedesktop/portal/desktop", "org.freedesktop.portal.RemoteDesktop",
                 "version")
    report(portal.returncode == 0, "desktop portal with remote desktop",
           "a Wayland desktop with xdg-desktop-portal (KDE or GNOME) is needed")
    print("all present" if not problems else f"{problems} missing")
    return 0 if not problems else 1


def cmd_pair(args):
    need_usb()
    command = [sys.executable, "scripts/pair.py"] + (["--new"] if args.new else [])
    result = run(*command)
    print((result.stdout + result.stderr).strip())
    return result.returncode


def allow_sound(serial):
    """Lets the shell user (tabletd and tb-audio run as it on this path) use the
    speaker's sound card, until the next reboot; root is needed for that, so
    it happens while Android (and with it su) still runs. The card number
    changes between boots: it is looked up by name. Without this, there is
    just no sound."""
    script = ("n=$(readlink /proc/asound/sprdphone); n=${n#card}; "
              "[ -n \"$n\" ] && chgrp 2000 /dev/snd/pcmC${n}D0p /dev/snd/controlC${n} && echo sound-ok")
    result = run("adb", "-s", serial, "shell", f"su -c '{script}'")
    if "sound-ok" not in result.stdout:
        print("sound: the sound card could not be opened up for the remote control; no sound")


def cmd_up(args):
    serial = need_usb()
    settings = config.load()
    state = tablet_state(serial)
    address = link.tablet_wlan_address()
    if state["android"] == "running":
        if address:
            result = run(sys.executable, "scripts/firewall.py", address)
            print((result.stdout + result.stderr).strip())
            if result.returncode != 0:
                return 1
        if not secure.key_path().exists() or not state["key"]:
            if cmd_pair(argparse.Namespace(new=False)) != 0:
                return 1
            state["key"] = True           # the tablet holds the key now
        linux = linux_installed(serial) and start_linux(settings["rotation"])
        allow_sound(serial)
        result = run("scripts/android.sh", "stop")
        print(result.stdout.strip())
        if result.returncode != 0:
            print(result.stderr.strip())
            return 1
    else:
        print("Android is already stopped: firewall and pairing left as they are "
              "(root on the tablet needs Android)")
        if not state["key"]:
            print("the tablet has no pairing key; Wi-Fi will not work until "
                  "'huawai down', 'huawai pair', 'huawai up'")
        linux = state["linux"]
    run_args = ["--rotation", settings["rotation"]] + (["--remote"] if args.remote else [])
    if linux:
        run_args.append("--linux")
    tabletd_args = []
    # tabletd refuses to start with --key and no key file: only with the
    # tablet's key (the cable works without it)
    if state["key"]:
        tabletd_args = ["--secure-listen", "7100", "--key", TABLET_KEY]
    result = run("scripts/run.sh", *run_args, "--", *tabletd_args)
    print(result.stdout.strip())
    if result.returncode != 0:
        print(result.stderr.strip())
        return 1
    if address and address != settings["wifi_host"]:
        config.update(wifi_host=address)
        print(f"tablet's Wi-Fi address {address} saved in {config.path()}")
    if run("systemctl", "--user", "is-active", SERVICE).stdout.strip() == "active":
        run("systemctl", "--user", "restart", SERVICE)
        print(f"{SERVICE} restarted")
    return 0


def cmd_down(_args):
    serial = need_usb()
    state = tablet_state(serial)
    for name in ("tb-home", "tabletd"):   # tb-home ends its program itself
        if state[name]:
            adb_shell(serial, f"[ -d /proc/{state[name]} ] && kill {state[name]}")
            print(f"{name} (pid {state[name]}) stopped")
    if state["android"] != "running":
        result = run("scripts/android.sh", "start", timeout=400)
        print(result.stdout.strip())
        return result.returncode
    print("Android is running")
    return 0


def cmd_config(args):
    if not args.changes:
        print(config.path().read_text() if config.path().exists()
              else f"{config.path()} does not exist yet; defaults: {config.DEFAULTS}")
        return 0
    changes = {}
    for item in args.changes:
        key, _, value = item.partition("=")
        if key not in config.DEFAULTS:
            sys.exit(f"huawai: unknown setting {key!r}")
        if isinstance(config.DEFAULTS[key], bool):
            changes[key] = value.lower() in ("1", "true", "yes", "ja")
        else:
            changes[key] = value
    try:
        config.update(**changes)
    except config.ConfigError as exc:
        sys.exit(f"huawai: {exc}")
    print(f"saved in {config.path()}; restart hostd to apply: "
          f"systemctl --user restart {SERVICE}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="huawai", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    pair = sub.add_parser("pair")
    pair.add_argument("--new", action="store_true", help="replace the key on both sides")
    pair.set_defaults(func=cmd_pair)
    up = sub.add_parser("up")
    up.add_argument("--remote", action="store_true", help="open the remote control at once")
    up.set_defaults(func=cmd_up)
    sub.add_parser("down").set_defaults(func=cmd_down)
    conf = sub.add_parser("config")
    conf.add_argument("changes", nargs="*", metavar="KEY=VALUE")
    conf.set_defaults(func=cmd_config)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
