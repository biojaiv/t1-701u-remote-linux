#!/usr/bin/env python3
"""Step P5.3: table of all init services of your own tablet.

Reads the service definitions from the init *.rc files you copied from your
tablet (adb pull /init.rc /init.*.rc device/init/; the folder is ignored by
git: these are the vendor's files and are not part of this project) and one
or two property dumps (output of `adb shell getprop`, e.g. one taken with
Android running and one with it stopped), and writes a Markdown table to
stdout. Another folder: INIT_RC_DIR=path.

Usage: init-services.py LABEL=getprop.txt [LABEL=getprop.txt]
"""
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RC_DIR = pathlib.Path(os.environ.get("INIT_RC_DIR", ROOT / "device" / "init"))


def services():
    found = {}
    for rc in sorted(RC_DIR.glob("*.rc")):
        current = None
        for raw in rc.read_text(errors="replace").splitlines():
            line = raw.strip()
            if line.startswith("service "):
                parts = line.split()
                current = {"name": parts[1], "program": " ".join(parts[2:]), "file": rc.name,
                           "class": "default", "user": "root", "flags": []}
                found[parts[1]] = current
            elif line.startswith("on ") or not raw.startswith((" ", "\t")) and line and not line.startswith("#"):
                current = None
            elif current and line and not line.startswith("#"):
                words = line.split()
                if words[0] == "class" and len(words) > 1:
                    current["class"] = words[1]
                elif words[0] == "user" and len(words) > 1:
                    current["user"] = words[1]
                elif words[0] in ("critical", "oneshot", "disabled"):
                    current["flags"].append(words[0])
    return found


def states(path):
    result = {}
    for line in pathlib.Path(path).read_text(errors="replace").splitlines():
        match = re.match(r"\[init\.svc\.([^\]]+)\]: \[([^\]]*)\]", line.strip())
        if match:
            result[match.group(1)] = match.group(2)
    return result


def main():
    dumps = [arg.split("=", 1) for arg in sys.argv[1:]]
    if not dumps or any(len(d) != 2 for d in dumps):
        print(__doc__, file=sys.stderr)
        return 2
    columns = [(label, states(path)) for label, path in dumps]
    table = services()
    print("| Service | Program | Class | User | critical | Flags | File | "
          + " | ".join(label for label, _ in columns) + " |")
    print("|---|---|---|---|---|---|---|" + "---|" * len(columns))
    for name in sorted(table):
        svc = table[name]
        flags = [f for f in svc["flags"] if f != "critical"]
        cells = [f"`{name}`", f"`{svc['program'][:60]}`", svc["class"], svc["user"],
                 "yes" if "critical" in svc["flags"] else "no", ", ".join(flags) or "–", svc["file"]]
        cells += [state.get(name, "–") for _, state in columns]
        print("| " + " | ".join(cells) + " |")
    known = set(table)
    for label, state in columns:
        extra = sorted(set(state) - known)
        if extra:
            print(f"\nOnly in the properties ({label}), not in the .rc files read: "
                  + ", ".join(f"`{name}` ({state[name]})" for name in extra))
    return 0


if __name__ == "__main__":
    sys.exit(main())
