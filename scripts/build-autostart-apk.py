#!/usr/bin/env python3
"""Builds the tablet's autostart app (an Android APK) without the Android SDK.

/system cannot be changed on this tablet (P5.4), so the own system cannot be
started from a boot hook. Instead this app receives BOOT_COMPLETED and runs,
through KingRoot's su, the launcher away from its own session:

    su -c "/data/tabletd/bin/tb-detach /system/bin/sh /data/tabletd/bin/tabletd-launch.sh"

The launcher decides (mode file, volume key, boot counter) whether Android
stays or the own system takes over.

Debian 13 has no dex compiler (d8/dx), and the app is a single class with
two short methods, so this script writes the dex file and the binary
AndroidManifest.xml itself (formats: source.android.com, "Dalvik executable
format" and the resource types in frameworks/base/libs/androidfw). Android
checks both when installing (dexopt, package parser); the signature is made
with the JDK's keytool and jarsigner (v1 scheme, which Android 4.4 checks).

Usage: scripts/build-autostart-apk.py [--out build/autostart/tablet-autostart.apk]
"""
import argparse
import hashlib
import io
import os
import pathlib
import struct
import subprocess
import zipfile
import zlib

PACKAGE = "local.tablet.autostart"
RECEIVER = PACKAGE + ".BootReceiver"
STARTER = PACKAGE + ".Start"   # the "Tablet System" icon in Android's app list
COMMAND = "/data/tabletd/bin/tb-detach /system/bin/sh /data/tabletd/bin/tabletd-launch.sh"
ROOT = pathlib.Path(__file__).resolve().parent.parent


# -- dex --------------------------------------------------------------------------------

def uleb128(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def sleb128(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        done = (value == 0 and not byte & 0x40) or (value == -1 and byte & 0x40)
        out.append(byte if done else byte | 0x80)
        if done:
            return bytes(out)


def descriptor(class_name):
    return "L" + class_name.replace(".", "/") + ";"


RUNTIME, PROCESS = "Ljava/lang/Runtime;", "Ljava/lang/Process;"
STRING_ARRAY = "[Ljava/lang/String;"
ACTIVITY = "Landroid/app/Activity;"
BUNDLE = "Landroid/os/Bundle;"
RECEIVER_BASE = "Landroid/content/BroadcastReceiver;"
CONTEXT, INTENT = "Landroid/content/Context;", "Landroid/content/Intent;"


def shorty(ret, params):
    letter = lambda t: "L" if t[0] in "L[" else t
    return letter(ret) + "".join(letter(p) for p in params)


def exec_su(r, command, base):
    """Dalvik code: Runtime.getRuntime().exec(new String[]{"su", "-c", command});
    uses v0..v3; base = code address where it starts (for nothing but clarity)."""
    return [0x0071, r.method(RUNTIME, "getRuntime", RUNTIME, ()), 0x0000,  # invoke-static {}
            0x000C,                                    # move-result-object v0
            0x3112,                                    # const/4 v1, 3
            0x1123, r.type(STRING_ARRAY),              # new-array v1, v1, String[]
            0x0212, 0x031A, r.string("su"), 0x034D, 0x0201,       # v1[0] = "su"
            0x1212, 0x031A, r.string("-c"), 0x034D, 0x0201,       # v1[1] = "-c"
            0x2212, 0x031A, r.string(command), 0x034D, 0x0201,    # v1[2] = command
            0x206E, r.method(RUNTIME, "exec", PROCESS, (STRING_ARRAY,)), 0x0010]  # exec(v1)


class Refs:
    """Collects (first pass) and resolves (second pass) the dex constant pools."""
    def __init__(self, final=None):
        self.final = final
        self.strings, self.types, self.methods = set(), set(), set()

    def string(self, text):
        self.strings.add(text)
        return self.final.sidx[text] if self.final else 0

    def type(self, descriptor):
        self.types.add(descriptor)
        return self.final.tidx[descriptor] if self.final else 0

    def method(self, cls, name, ret, params):
        self.methods.add((cls, name, ret, tuple(params)))
        self.type(cls)
        return self.final.midx[(cls, name, ret, tuple(params))] if self.final else 0


def receiver_class(r):
    me = descriptor(RECEIVER)
    init = [0x1070, r.method(RECEIVER_BASE, "<init>", "V", ()), 0x0000, 0x000E]
    body = exec_su(r, COMMAND, 0) + [0x000E,          # return-void        (25)
                                     0x000D, 0x000E]  # move-exception v0; return-void (26)
    return {"class": me, "super": RECEIVER_BASE,
            "direct": [("<init>", "V", (), 0x10001, 1, 1, 1, init, None)],
            "virtual": [("onReceive", "V", (CONTEXT, INTENT), 0x1, 7, 3, 2, body, (0, 25, 26))]}


def activity_class(r):
    me = descriptor(STARTER)
    init = [0x1070, r.method(ACTIVITY, "<init>", "V", ()), 0x0000, 0x000E]
    finish = r.method(ACTIVITY, "finish", "V", ())
    body = ([0x206F, r.method(ACTIVITY, "onCreate", "V", (BUNDLE,)), 0x0054]   # super.onCreate(b)
            + exec_su(r, COMMAND + " now", 3)                                   # (3 .. 27)
            + [0x106E, finish, 0x0004, 0x000E,          # finish(); return-void       (28)
               0x000D, 0x106E, finish, 0x0004, 0x000E])  # handler: same          (32)
    return {"class": me, "super": ACTIVITY,
            "direct": [("<init>", "V", (), 0x10001, 1, 1, 1, init, None)],
            "virtual": [("onCreate", "V", (BUNDLE,), 0x4, 6, 2, 2, body, (3, 25, 32))]}


CLASSES = [receiver_class, activity_class]


def build_dex():
    # pass 1: what is referenced
    refs = Refs()
    specs = [make(refs) for make in CLASSES]
    for spec in specs:
        refs.type(spec["class"])
        refs.type(spec["super"])
        for name, ret, params, *_ in spec["direct"] + spec["virtual"]:
            refs.method(spec["class"], name, ret, params)
    protos_wanted = {(shorty(ret, params), ret, params)
                     for _, _, ret, params in refs.methods}
    strings = set(refs.strings) | {m[1] for m in refs.methods} | {p[0] for p in protos_wanted}
    types = set(refs.types) | {p[1] for p in protos_wanted} | {t for p in protos_wanted for t in p[2]}
    strings |= types
    strings = sorted(strings)                 # ASCII: code point order = byte order

    class Final:
        pass
    final = Final()
    final.sidx = {text: i for i, text in enumerate(strings)}
    types = sorted(types, key=lambda t: final.sidx[t])
    final.tidx = {t: i for i, t in enumerate(types)}
    protos = sorted({(final.sidx[sh], final.tidx[ret], tuple(final.tidx[p] for p in params))
                     for sh, ret, params in protos_wanted}, key=lambda q: (q[1], q[2]))
    def proto_index(ret, params):
        key = (final.sidx[shorty(ret, params)], final.tidx[ret],
               tuple(final.tidx[p] for p in params))
        return protos.index(key)
    method_keys = sorted(refs.methods, key=lambda m: (final.tidx[m[0]], final.sidx[m[1]],
                                                       proto_index(m[2], m[3])))
    final.midx = {m: i for i, m in enumerate(method_keys)}

    # pass 2: real indices
    refs2 = Refs(final)
    specs = sorted((make(refs2) for make in CLASSES), key=lambda c: final.tidx[c["class"]])

    header_size = 0x70
    off = header_size
    string_ids_off = off; off += 4 * len(strings)
    type_ids_off = off; off += 4 * len(types)
    proto_ids_off = off; off += 12 * len(protos)
    method_ids_off = off; off += 8 * len(method_keys)
    class_defs_off = off; off += 32 * len(specs)
    data_off = off
    data = bytearray()

    def align(n):
        while (data_off + len(data)) % n:
            data.append(0)

    def code_item(registers, ins, outs, insns, tries):
        body = struct.pack("<HHHHII", registers, ins, outs, 1 if tries else 0, 0, len(insns))
        body += struct.pack(f"<{len(insns)}H", *insns)
        if tries:
            if len(insns) % 2:
                body += b"\0\0"
            start, count, handler_addr = tries
            body += struct.pack("<IHH", start, count, 1)
            body += uleb128(1) + sleb128(0) + uleb128(handler_addr)   # catch-all
        return body

    code_offs = {}
    first_code = None
    code_count = 0
    for spec in specs:
        for name, ret, params, access, regs, ins, outs, insns, tries in spec["direct"] + spec["virtual"]:
            align(4)
            pos = data_off + len(data)
            first_code = pos if first_code is None else first_code
            code_offs[(spec["class"], name)] = pos
            data += code_item(regs, ins, outs, insns, tries)
            code_count += 1

    type_list_offs = {}
    for proto in protos:
        params = proto[2]
        if params and params not in type_list_offs:
            align(4)
            type_list_offs[params] = data_off + len(data)
            data += struct.pack("<I", len(params)) + struct.pack(f"<{len(params)}H", *params)

    string_data_offs, string_data_start = [], data_off + len(data)
    for text in strings:
        string_data_offs.append(data_off + len(data))
        data += uleb128(len(text)) + text.encode("utf-8") + b"\0"

    class_data_offs = []
    first_class_data = data_off + len(data)
    for spec in specs:
        class_data_offs.append(data_off + len(data))
        data += uleb128(0) + uleb128(0) + uleb128(len(spec["direct"])) + uleb128(len(spec["virtual"]))
        for group in (spec["direct"], spec["virtual"]):
            entries = sorted(group, key=lambda m: final.midx[(spec["class"], m[0], m[1], tuple(m[2]))])
            previous = 0
            for name, ret, params, access, *_ in entries:
                index = final.midx[(spec["class"], name, ret, tuple(params))]
                data += uleb128(index - previous) + uleb128(access) + \
                    uleb128(code_offs[(spec["class"], name)])
                previous = index

    align(4)
    map_off = data_off + len(data)
    entries = [(0x0000, 1, 0), (0x0001, len(strings), string_ids_off),
               (0x0002, len(types), type_ids_off), (0x0003, len(protos), proto_ids_off),
               (0x0005, len(method_keys), method_ids_off), (0x0006, len(specs), class_defs_off),
               (0x2001, code_count, first_code)]
    if type_list_offs:
        entries.append((0x1001, len(type_list_offs), min(type_list_offs.values())))
    entries += [(0x2002, len(strings), string_data_start),
                (0x2000, len(specs), first_class_data), (0x1000, 1, map_off)]
    entries.sort(key=lambda e: e[2])
    data += struct.pack("<I", len(entries))
    for kind, size, offset in entries:
        data += struct.pack("<HHII", kind, 0, size, offset)

    body = bytearray()
    body += b"".join(struct.pack("<I", o) for o in string_data_offs)
    body += b"".join(struct.pack("<I", final.sidx[t]) for t in types)
    for sh, ret, params in protos:
        body += struct.pack("<III", sh, ret, type_list_offs.get(params, 0))
    for cls, name, ret, params in method_keys:
        body += struct.pack("<HHI", final.tidx[cls], proto_index(ret, params), final.sidx[name])
    for spec, cdo in zip(specs, class_data_offs):
        body += struct.pack("<IIIIIIII", final.tidx[spec["class"]], 0x1, final.tidx[spec["super"]],
                            0, 0xFFFFFFFF, 0, cdo, 0)
    assert header_size + len(body) == data_off

    file_size = data_off + len(data)
    header = bytearray(b"dex\n035\0" + b"\0" * (header_size - 8))
    struct.pack_into("<" + "I" * 20, header, 32,
                     file_size, header_size, 0x12345678, 0, 0, map_off,
                     len(strings), string_ids_off, len(types), type_ids_off,
                     len(protos), proto_ids_off, 0, 0, len(method_keys), method_ids_off,
                     len(specs), class_defs_off, len(data), data_off)
    dex = header + body + data
    dex[12:32] = hashlib.sha1(dex[32:]).digest()
    dex[8:12] = struct.pack("<I", zlib.adler32(bytes(dex[12:])) & 0xFFFFFFFF)
    return bytes(dex)


# -- binary AndroidManifest.xml ---------------------------------------------------------------

ANDROID_NS = "http://schemas.android.com/apk/res/android"
ATTR_IDS = {"theme": 0x01010000, "label": 0x01010001, "icon": 0x01010002,
            "name": 0x01010003, "exported": 0x01010010, "excludeFromRecents": 0x01010017,
            "minSdkVersion": 0x0101020C, "versionCode": 0x0101021B,
            "versionName": 0x0101021C, "targetSdkVersion": 0x01010270}
TYPE_REFERENCE, TYPE_STRING, TYPE_INT_DEC, TYPE_BOOLEAN = 0x01, 0x03, 0x10, 0x12
# framework resources: Theme.NoDisplay, the standard app icon
THEME_NO_DISPLAY, DEFAULT_ICON = 0x01030055, 0x01080093


class Ref(int):
    """An attribute value that refers to a framework resource."""

MANIFEST = ("manifest", [("", "package", PACKAGE), ("android", "versionCode", 2),
                         ("android", "versionName", "2")], [
    ("uses-sdk", [("android", "minSdkVersion", 19), ("android", "targetSdkVersion", 19)], []),
    ("uses-permission", [("android", "name", "android.permission.RECEIVE_BOOT_COMPLETED")], []),
    ("application", [("android", "label", "Tablet System"),
                     ("android", "icon", Ref(DEFAULT_ICON))], [
        ("activity", [("android", "name", STARTER), ("android", "label", "Tablet System"),
                      ("android", "theme", Ref(THEME_NO_DISPLAY)),
                      ("android", "excludeFromRecents", True)], [
            ("intent-filter", [], [
                ("action", [("android", "name", "android.intent.action.MAIN")], []),
                ("category", [("android", "name", "android.intent.category.LAUNCHER")], []),
            ]),
        ]),
        ("receiver", [("android", "name", RECEIVER), ("android", "exported", True)], [
            ("intent-filter", [], [
                ("action", [("android", "name", "android.intent.action.BOOT_COMPLETED")], []),
            ]),
        ]),
    ]),
])


def build_manifest():
    # attribute names with resource ids first, in the order of the resource map
    attr_names = sorted({name for _, attrs, _ in walk(MANIFEST) for ns, name, _ in attrs
                         if ns == "android"}, key=lambda n: ATTR_IDS[n])
    pool = list(attr_names)

    def s(text):
        if text not in pool:
            pool.append(text)
        return pool.index(text)

    s("android")
    s(ANDROID_NS)
    for tag, attrs, _ in walk(MANIFEST):
        s(tag)
        for ns, name, value in attrs:
            s(name)
            if isinstance(value, str):
                s(value)

    def chunk(kind, header_extra, body):
        header_size = 8 + len(header_extra)
        return struct.pack("<HHI", kind, header_size, header_size + len(body)) + header_extra + body

    nodes = bytearray()
    ns_prefix, ns_uri = s("android"), s(ANDROID_NS)
    nodes += chunk(0x0100, struct.pack("<II", 1, 0xFFFFFFFF), struct.pack("<II", ns_prefix, ns_uri))

    def element(node):
        tag, attrs, children = node
        attrs = sorted(attrs, key=lambda a: ATTR_IDS.get(a[1], 0) if a[0] else -1)
        body = struct.pack("<IIHHHHHH", 0xFFFFFFFF, s(tag), 20, 20, len(attrs), 0, 0, 0)
        for ns, name, value in attrs:
            ns_idx = ns_uri if ns == "android" else 0xFFFFFFFF
            if isinstance(value, Ref):
                raw, kind, data = 0xFFFFFFFF, TYPE_REFERENCE, int(value)
            elif isinstance(value, bool):
                raw, kind, data = 0xFFFFFFFF, TYPE_BOOLEAN, 0xFFFFFFFF if value else 0
            elif isinstance(value, int):
                raw, kind, data = 0xFFFFFFFF, TYPE_INT_DEC, value
            else:
                raw, kind, data = s(value), TYPE_STRING, s(value)
            body += struct.pack("<IIIHBBI", ns_idx, s(name), raw, 8, 0, kind, data)
        out = chunk(0x0102, struct.pack("<II", 1, 0xFFFFFFFF), body)
        for child in children:
            out += element(child)
        return out + chunk(0x0103, struct.pack("<II", 1, 0xFFFFFFFF),
                           struct.pack("<II", 0xFFFFFFFF, s(tag)))

    nodes += element(MANIFEST)
    nodes += chunk(0x0101, struct.pack("<II", 1, 0xFFFFFFFF), struct.pack("<II", ns_prefix, ns_uri))

    # string pool, UTF-16
    offsets, strdata = [], bytearray()
    for text in pool:
        offsets.append(len(strdata))
        encoded = text.encode("utf-16-le")
        strdata += struct.pack("<H", len(text)) + encoded + b"\0\0"
    while len(strdata) % 4:
        strdata += b"\0"
    strings_start = 28 + 4 * len(pool)
    pool_chunk = chunk(0x0001, struct.pack("<IIIII", len(pool), 0, 0, strings_start, 0),
                       b"".join(struct.pack("<I", o) for o in offsets) + strdata)
    resmap = chunk(0x0180, b"", b"".join(struct.pack("<I", ATTR_IDS[n]) for n in attr_names))
    return chunk(0x0003, b"", pool_chunk + resmap + nodes)


def walk(node):
    yield node
    for child in node[2]:
        yield from walk(child)


# -- APK ----------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "build/autostart/tablet-autostart.apk"))
    args = parser.parse_args()
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    unsigned = out.with_suffix(".unsigned.apk")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", build_manifest())
        z.writestr("classes.dex", build_dex())
    unsigned.write_bytes(buffer.getvalue())

    # own signing key, kept in build/autostart (not secret beyond this project: the
    # app only runs a fixed command and Android only needs the same key for updates)
    store = out.parent / "signing.keystore"
    password = out.parent / "signing.password"
    if not store.exists():
        password.write_text(hashlib.sha256(os.urandom(32)).hexdigest())
        password.chmod(0o600)
        subprocess.run(["keytool", "-genkeypair", "-keystore", str(store), "-alias", "tablet",
                        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
                        "-storepass:file", str(password), "-keypass:file", str(password),
                        "-dname", "CN=Tablet Autostart"], check=True, capture_output=True)
        store.chmod(0o600)
    signed = out.with_suffix(".signing.apk")
    subprocess.run(["jarsigner", "-keystore", str(store), "-storepass:file", str(password),
                    "-keypass:file", str(password), "-sigalg", "SHA256withRSA",
                    "-digestalg", "SHA-256", "-signedjar", str(signed), str(unsigned), "tablet"],
                   check=True, capture_output=True)
    signed.replace(out)
    unsigned.unlink()
    print(f"build-autostart-apk: {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
