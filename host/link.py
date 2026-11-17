"""Connection to tabletd: framing and message types of protocol version 1.

Link is a plain blocking connection (tests, test picture). Session adds what
the mirror needs (step P2.4): at most one unconfirmed frame on the way,
a PING every second while idle, and automatic reconnection.
"""
import collections
import ipaddress
import os
import select
import socket
import struct
import subprocess
import threading
import time

from . import secure

PROTO_VERSION = 1
MAX_PAYLOAD = 1300000
DEFAULT_PORT = 7100

RECT, PRESENT, BACKLIGHT, TIME, PING, AUTH = 0x01, 0x02, 0x03, 0x04, 0x05, 0x06
COPY = 0x07              # u16 sx, sy, w, h, dx, dy: the tablet moves an area itself
INFO_COPY = 0x04         # INFO flags: tabletd understands COPY
AUDIO = 0x08             # u32 rate, u8 channels, u8 0, S16LE samples (at most 100 ms)
INFO_AUDIO = 0x08        # INFO flags: tabletd plays AUDIO (its player is ready)
HOME = 0x09              # no payload: tabletd ends, the tablet shows its start screen
INFO_HOME = 0x10         # INFO flags: tabletd understands HOME
INFO, ACK, TOUCH, KEY, STATUS, PONG = 0x41, 0x42, 0x43, 0x44, 0x45, 0x46
FMT_RGB565, FMT_RGB565_LZ4 = 0, 1

_HEADER = struct.Struct("<BI")
HANDSHAKE_PING = 0xFFFFFFFF   # counter of the PING that proves the key


class LinkError(Exception):
    """The connection to tabletd failed or tabletd sent something invalid."""


def usb_serial():
    """Serial of the one tablet connected by USB, or None."""
    try:
        out = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    usb = [line.split()[0] for line in out.splitlines()[1:]
           if "usb:" in line and len(line.split()) > 1 and line.split()[1] == "device"]
    return usb[0] if len(usb) == 1 else None


def parse_wlan_address(netcfg_output):
    """IPv4 address of wlan0 from the output of netcfg on the tablet, or None."""
    for line in netcfg_output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "wlan0" and parts[1] == "UP":
            address = parts[2].split("/")[0]
            if address not in ("0.0.0.0", ""):
                return address
    return None


def tablet_wlan_address():
    """Asks the tablet over the USB cable for its Wi-Fi address (or None)."""
    serial = usb_serial()
    if not serial:
        return None
    try:
        out = subprocess.run(["adb", "-s", serial, "shell", "netcfg"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_wlan_address(out.replace("\r", ""))


def local_networks():
    """The networks this laptop sits in (IPv4 and IPv6, from "ip addr"):
    a caller from one of them is at home, not out on the Internet."""
    try:
        out = subprocess.run(["ip", "-o", "addr", "show", "scope", "global"],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    nets = []
    for line in out.splitlines():
        fields = line.split()
        if len(fields) > 3 and fields[2] in ("inet", "inet6"):
            try:
                nets.append(ipaddress.ip_interface(fields[3]).network)
            except ValueError:
                pass
    return nets


def is_local(peer, networks):
    """peer (an address string, IPv4 also as ::ffff:a.b.c.d) lies in one of
    networks. Single-address networks (/32, /128: VPN tunnels, temporary
    addresses) do not count."""
    try:
        address = ipaddress.ip_address(peer.split("%")[0])
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in net and net.num_addresses > 1 for net in networks
               if net.version == address.version)


def adb_forward(port=DEFAULT_PORT, remote="localabstract:tabletd"):
    """Lets adb forward a local TCP port to tabletd on the tablet.

    Uses the USB connection if adb also knows the tablet over Wi-Fi.
    """
    command = ["adb"]
    serial = os.environ.get("ANDROID_SERIAL") or usb_serial()
    if serial:
        command += ["-s", serial]
    result = subprocess.run(command + ["forward", f"tcp:{port}", remote],
                            capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise LinkError(f"adb forward failed: {result.stderr.strip()}")


class Link:
    """psk given: direct encrypted connection (Wi-Fi); otherwise plain (adb).

    sock given: a connection the tablet opened to us (Internet: tabletd
    --secure-connect); the handshake is the same, we speak first."""

    def __init__(self, port=DEFAULT_PORT, host="127.0.0.1", timeout=5.0, psk=None, sock=None):
        self.channel = None
        self._plain = b""
        if sock is not None:
            self.sock = sock
            self.sock.settimeout(timeout)
        else:
            try:
                self.sock = socket.create_connection((host, port), timeout=timeout)
            except OSError as exc:
                raise LinkError(f"cannot connect to tabletd: {exc}") from exc
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if psk is not None:
            try:
                self.channel = secure.client_handshake(self.sock, psk)
                # the first record proves the key; a PING is harmless
                self.sock.sendall(self.channel.seal(
                    _HEADER.pack(PING, 4) + struct.pack("<I", HANDSHAKE_PING)))
            except (OSError, secure.SecureError) as exc:
                self.sock.close()
                raise LinkError(f"encrypted handshake failed: {exc}") from exc
        try:
            msg_type, payload = self.recv()
            if msg_type != INFO or len(payload) != 23:
                raise LinkError("tabletd did not start with INFO")
            version, self.width, self.height, self.flags = struct.unpack("<HHHB", payload[:7])
            self.nonce = payload[7:]
            if version != PROTO_VERSION:
                raise LinkError(f"tabletd speaks protocol {version}, expected {PROTO_VERSION}")
        except (LinkError, OSError):
            self.sock.close()             # no half-open link left behind
            raise

    def close(self):
        self.sock.close()

    def wrap(self, data):
        """Frame bytes as they go on the wire."""
        return self.channel.seal(data) if self.channel else data

    def unwrap(self, raw):
        """Bytes from the wire -> frame bytes (may be empty for half a record)."""
        if not self.channel:
            return raw
        try:
            return self.channel.feed(raw)
        except secure.SecureError as exc:
            raise LinkError(str(exc)) from exc

    def send(self, msg_type, payload=b""):
        try:
            self.sock.sendall(self.wrap(_HEADER.pack(msg_type, len(payload)) + payload))
        except OSError as exc:
            raise LinkError(f"send failed: {exc}") from exc

    def _exact(self, count):
        while len(self._plain) < count:
            try:
                chunk = self.sock.recv(65536 if self.channel else count - len(self._plain))
            except OSError as exc:
                raise LinkError(f"receive failed: {exc}") from exc
            if not chunk:
                raise LinkError("tabletd closed the connection")
            self._plain += self.unwrap(chunk)
        data, self._plain = self._plain[:count], self._plain[count:]
        return data

    def recv(self):
        """Returns (type, payload) of the next message from the tablet."""
        msg_type, length = _HEADER.unpack(self._exact(_HEADER.size))
        if length > 64:
            # Nothing the tablet sends is larger. tabletd is not trusted with
            # the laptop's memory either.
            raise LinkError(f"oversized message from tablet ({length} bytes)")
        return msg_type, self._exact(length)

    def recv_type(self, wanted):
        """Skips other messages until one of the wanted type arrives."""
        while True:
            msg_type, payload = self.recv()
            if msg_type == wanted:
                return payload

    def rect(self, x, y, width, height, data, fmt=FMT_RGB565):
        self.send(RECT, struct.pack("<HHHHB", x, y, width, height, fmt) + data)

    def present(self, seq):
        """Sends PRESENT and waits for the matching ACK."""
        self.send(PRESENT, struct.pack("<I", seq))
        ack = self.recv_type(ACK)
        if ack != struct.pack("<I", seq):
            raise LinkError("ACK with wrong sequence number")

    def ping(self, counter):
        self.send(PING, struct.pack("<I", counter))
        answer = self.recv_type(PONG)
        if answer == struct.pack("<I", HANDSHAKE_PING):
            answer = self.recv_type(PONG)   # the answer to the handshake comes first
        if answer != struct.pack("<I", counter):
            raise LinkError("PONG with wrong counter")


ACK_TIMEOUT = 5.0      # seconds without ACK before the link counts as dead
PING_INTERVAL = 1.0
RETRY_INTERVAL = 1.0
RETRY_SLOW = 5.0         # after several failures in a row


MAX_HANDSHAKES = 4   # callers in their handshake at the same time


class Session:
    """Keeps a connection to tabletd alive and allows one frame in flight.

    pump() must be called regularly. It returns a list of events:
    ("connected", width, height), ("disconnected", reason), ("ack", seq),
    ("touch", slot, state, x, y), ("key", code, state),
    ("status", percent, charging, temperature_tenths).
    """

    def __init__(self, port=DEFAULT_PORT, remote="localabstract:tabletd", use_adb=True,
                 host=None, psk=None, max_in_flight=1, adb_port=None, listen_port=None):
        """host given: direct encrypted connection to host:port with key psk,
        and, if use_adb is also set, the USB cable (adb, local port adb_port)
        as the second choice when the direct one fails. Without host: adb, or
        a local tabletd (tests).

        listen_port given (with psk): the tablet may also call us there, from
        anywhere (Internet, through a port forwarding of the router); such a
        connection is route "internet" and has to prove the key like the others."""
        if (host is not None or listen_port) and psk is None:
            raise ValueError("a direct connection needs the shared key")
        self.port, self.remote, self.use_adb = port, remote, use_adb
        self.adb_port = adb_port or port
        self.host, self.psk = host, psk
        self.route = None   # "wifi" or "adb" while connected
        self.link = None
        self.width = self.height = 0
        # frames sent but not yet acknowledged: (sequence number, time sent).
        # More than one in flight lets sending overlap with drawing on the
        # tablet; tabletd handles frames strictly in order.
        self.max_in_flight = max_in_flight
        self._flight = collections.deque()
        self.seq = 0
        self.bytes_sent = 0
        self._buf = b""
        self._last_tx = self._next_try = 0.0
        self._ping = 0
        self._failures = 0
        self.listener = None
        # caller address -> time until which it is refused (after a failed
        # handshake); only that address pauses, not every caller
        self._paused = {}
        # handshakes with callers, each in a thread so that a caller that never
        # answers cannot hold up hostd: [(thread, result box, socket, address)].
        # Several at once, so that strangers on the internet port cannot keep
        # the tablet's own call out by occupying a single slot.
        self._incoming = []
        if listen_port:
            # IPv4 and IPv6: behind DS-Lite (no public IPv4) only IPv6 reaches us
            if socket.has_dualstack_ipv6():
                self.listener = socket.create_server(("::", listen_port), family=socket.AF_INET6,
                                                     dualstack_ipv6=True)
            else:
                self.listener = socket.create_server(("0.0.0.0", listen_port))
            self.listener.setblocking(False)

    @property
    def connected(self):
        return self.link is not None

    @property
    def can_send(self):
        return self.link is not None and len(self._flight) < self.max_in_flight

    @property
    def in_flight(self):
        """Sequence number of the oldest unacknowledged frame, or None."""
        return self._flight[0][0] if self._flight else None

    def fileno(self):
        return self.link.sock.fileno() if self.link else -1

    def close(self):
        if self.link:
            self.link.close()
            self.link = None
            self._flight.clear()
        pending, self._incoming = self._incoming, []
        for thread, box, sock, _ in pending:
            try:
                sock.shutdown(socket.SHUT_RDWR)   # wakes a handshake still waiting
            except OSError:
                pass
            sock.close()
        for thread, box, sock, _ in pending:
            thread.join(timeout=5)
            if "link" in box:
                box["link"].close()
        if self.listener:
            self.listener.close()
            self.listener = None

    def _handshake(self, sock, box):
        """Thread: the encrypted handshake with a caller (at most 3 s)."""
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            box["link"] = Link(sock=sock, psk=self.psk, timeout=3.0)
        except (LinkError, OSError) as exc:
            box["error"] = exc
            sock.close()

    def _accept_incoming(self):
        """A connection the tablet opened to us, once its handshake (in a
        thread) has succeeded: (Link, caller address), else None. Never
        waits. Up to MAX_HANDSHAKES at once (a new caller pushes out the
        oldest waiting one); callers while connected and, for 2 s, an address
        whose handshake failed are closed at once."""
        if not self.listener:
            return None
        now = time.monotonic()
        result = None
        still = []
        for entry in self._incoming:
            thread, box, _, peer = entry
            if thread.is_alive():
                still.append(entry)
            elif "link" not in box:
                print(f"link: incoming connection from {peer} refused: {box.get('error')}",
                      flush=True)
                self._paused[peer] = now + 2.0
            elif self.link is not None or result is not None:
                box["link"].close()       # connected another way in the meantime
            else:
                result = (box["link"], peer)
        self._incoming = still
        for peer in [p for p, until in self._paused.items() if until <= now]:
            del self._paused[peer]
        while True:
            try:
                sock, address = self.listener.accept()
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            peer = address[0]
            if self.link is not None or result is not None or peer in self._paused:
                sock.close()              # busy, or this address just failed
                continue
            if len(self._incoming) >= MAX_HANDSHAKES:
                # all places taken: the oldest waiting caller makes room, so
                # silent callers cannot keep the tablet out for long
                _, _, oldest, _ = self._incoming.pop(0)
                try:
                    oldest.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            box = {}
            thread = threading.Thread(target=self._handshake, args=(sock, box),
                                      name="hostd-incoming", daemon=True)
            self._incoming.append((thread, box, sock, peer))
            thread.start()
        return result

    def _connect(self, events):
        incoming = self._accept_incoming()
        if incoming is not None:
            # a call from the home network (the tablet reached our public IPv6
            # from inside) is Wi-Fi; only callers from elsewhere are "internet"
            self.link, peer = incoming
            self.route = "wifi" if is_local(peer, local_networks()) else "internet"
            self._connected(events, time.monotonic())
            return
        now = time.monotonic()
        if now < self._next_try:
            return
        self._next_try = now + (RETRY_SLOW if self._failures >= 5 else RETRY_INTERVAL)
        routes = (["wifi"] if self.host else []) + (["adb"] if self.use_adb else [])
        if not routes:
            routes = ["local"]
        for route in routes:
            try:
                if route == "wifi":
                    self.link = Link(port=self.port, host=self.host, timeout=2.0, psk=self.psk)
                else:
                    if route == "adb":
                        adb_forward(self.adb_port, self.remote)
                    self.link = Link(port=self.adb_port, timeout=2.0)
                self.route = route
                break
            except (LinkError, subprocess.TimeoutExpired, OSError):
                self.link = None
        if self.link is None:
            self._failures += 1
            return
        self._connected(events, now)

    def _connected(self, events, now):
        self._failures = 0
        self.link.sock.setblocking(False)
        self.width, self.height = self.link.width, self.link.height
        self._flight.clear()
        self._buf = self.link._plain   # frames that arrived together with INFO
        self._last_tx = now
        events.append(("connected", self.width, self.height))

    def drop(self, reason):
        """Ends the connection from outside (hostd after an error of its
        own); returns the events, as pump() does."""
        events = []
        self._drop(events, reason)
        return events

    def _drop(self, events, reason):
        if self.link:
            self.link.close()
            self.link = None
            self._flight.clear()
            events.append(("disconnected", reason))

    def _send(self, data):
        data = self.link.wrap(data)
        self.link.sock.setblocking(True)
        self.link.sock.settimeout(ACK_TIMEOUT)
        try:
            self.link.sock.sendall(data)
        finally:
            if self.link:
                self.link.sock.setblocking(False)
        self.bytes_sent += len(data)
        self._last_tx = time.monotonic()

    def send_frame(self, payloads):
        """Sends the frame's parts followed by PRESENT. Returns the sequence number.

        A part is a RECT payload (bytes) or a (message type, payload) pair,
        e.g. (COPY, ...) from the encoder.

        Raises LinkError if the frame could not be sent; the next pump()
        reports the disconnect.
        """
        if not self.can_send:
            raise LinkError("not connected or a frame is still unconfirmed")
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        data = b"".join(_HEADER.pack(*((p[0], len(p[1])) if isinstance(p, tuple) else
                                       (RECT, len(p)))) + (p[1] if isinstance(p, tuple) else p)
                        for p in payloads)
        data += _HEADER.pack(PRESENT, 4) + struct.pack("<I", self.seq)
        try:
            self._send(data)
        except OSError as exc:
            self._broken = str(exc)
            raise LinkError(f"send failed: {exc}") from exc
        self._flight.append((self.seq, time.monotonic()))
        return self.seq

    def send_message(self, msg_type, payload=b""):
        if not self.link:
            return
        try:
            self._send(_HEADER.pack(msg_type, len(payload)) + payload)
        except OSError as exc:
            self._broken = str(exc)

    _broken = None

    def _parse(self, events):
        while len(self._buf) >= _HEADER.size:
            msg_type, length = _HEADER.unpack_from(self._buf)
            if length > 64:
                raise LinkError("oversized message from tablet")
            if len(self._buf) < _HEADER.size + length:
                return
            payload = self._buf[_HEADER.size:_HEADER.size + length]
            self._buf = self._buf[_HEADER.size + length:]
            if msg_type == ACK and length == 4:
                seq = struct.unpack("<I", payload)[0]
                if self._flight and self._flight[0][0] == seq:
                    self._flight.popleft()
                events.append(("ack", seq))
            elif msg_type == TOUCH and length == 6:
                events.append(("touch", *struct.unpack("<BBHH", payload)))
            elif msg_type == KEY and length == 3:
                events.append(("key", *struct.unpack("<HB", payload)))
            elif msg_type == STATUS and length == 4:
                events.append(("status", *struct.unpack("<BBh", payload)))
            elif msg_type not in (PONG, INFO):
                raise LinkError(f"unexpected message type {msg_type:#x}")

    def pump(self, timeout=0.0):
        """Waits up to timeout seconds for data, returns the list of events."""
        events = []
        if self._broken:
            reason, self._broken = self._broken, None
            self._drop(events, reason)
        if not self.link:
            self._connect(events)
            if not self.link:
                if timeout:
                    time.sleep(min(timeout, 0.05))
                return events
        if self.listener:
            self._accept_incoming()       # while connected: close further callers at once
        try:
            readable, _, _ = select.select([self.link.sock], [], [], timeout)
            if readable:
                chunk = self.link.sock.recv(65536)
                if not chunk:
                    raise LinkError("tabletd closed the connection")
                self._buf += self.link.unwrap(chunk)
                self._parse(events)
            now = time.monotonic()
            if self._flight and now - self._flight[0][1] > ACK_TIMEOUT:
                raise LinkError("no ACK within 5 seconds")
            if now - self._last_tx >= PING_INTERVAL:
                self._ping = (self._ping + 1) & 0xFFFFFFFF
                self._send(_HEADER.pack(PING, 4) + struct.pack("<I", self._ping))
        except (LinkError, OSError) as exc:
            self._drop(events, str(exc))
        return events
