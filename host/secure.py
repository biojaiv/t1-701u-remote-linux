"""Laptop side of the encrypted Wi-Fi channel (counterpart of tablet/secure.c).

Same construction as on the tablet: X25519 with fresh keys per connection,
BLAKE2b keyed with the shared 32-byte key, XChaCha20-Poly1305 records with a
counter per direction. libsodium (PyNaCl) and Monocypher implement the same
primitives; nothing here invents cryptography.
"""
import hashlib
import os
import pathlib
import stat
import struct

from nacl import bindings as sodium
from nacl.exceptions import CryptoError, RuntimeError as SodiumError

KEY_LEN = 32
MAC_LEN = 16
MAX_PLAIN = 65536
MAGIC_CLIENT, MAGIC_SERVER = b"HWC1", b"HWS1"
LABEL = b"huawai-companion v1"
_LEN = struct.Struct("<I")


class SecureError(Exception):
    """Handshake failed or a record did not authenticate: drop the connection."""


def key_path():
    base = os.environ.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "huawai-companion" / "key"


def load_key(path=None):
    """Reads the shared key; refuses a file that others could read."""
    path = pathlib.Path(path or key_path())
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise SecureError(f"{path} must be a regular file readable only by its owner")
    data = path.read_bytes()
    if len(data) != KEY_LEN:
        raise SecureError(f"{path} must hold exactly {KEY_LEN} bytes")
    return data


def derive(psk, e_client, e_server, dh):
    """(client->server key, server->client key)"""
    k = hashlib.blake2b(LABEL + e_client + e_server + dh, key=psk, digest_size=64).digest()
    return k[:32], k[32:]


def _nonce(counter):
    return struct.pack("<Q", counter) + bytes(16)


class Channel:
    """Record layer once the keys are known."""

    def __init__(self, k_tx, k_rx):
        self.k_tx, self.k_rx = k_tx, k_rx
        self.n_tx = self.n_rx = 0
        self._raw = bytearray()

    def seal(self, data):
        """Plaintext bytes -> records, each with at most MAX_PLAIN bytes."""
        out = []
        for start in range(0, len(data), MAX_PLAIN):
            chunk = bytes(data[start:start + MAX_PLAIN])
            header = _LEN.pack(len(chunk) + MAC_LEN)
            body = sodium.crypto_aead_xchacha20poly1305_ietf_encrypt(
                chunk, header, _nonce(self.n_tx), self.k_tx)
            self.n_tx += 1
            out.append(header + body)
        return b"".join(out)

    def feed(self, raw):
        """Received bytes -> the plaintext of all complete records so far."""
        self._raw += raw
        plain = []
        while len(self._raw) >= 4:
            body_len = _LEN.unpack_from(self._raw)[0]
            if body_len <= MAC_LEN or body_len > MAX_PLAIN + MAC_LEN:
                raise SecureError("bad record length")
            if len(self._raw) < 4 + body_len:
                break
            header, body = bytes(self._raw[:4]), bytes(self._raw[4:4 + body_len])
            del self._raw[:4 + body_len]
            try:
                plain.append(sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(
                    body, header, _nonce(self.n_rx), self.k_rx))
            except CryptoError as exc:
                raise SecureError("record failed authentication") from exc
            self.n_rx += 1
        return b"".join(plain)


def client_handshake(sock, psk):
    """Runs the handshake on a connected socket; returns the Channel.

    The caller must send its first record right away: only then does the
    tablet accept the connection.
    """
    secret = os.urandom(32)
    e_client = sodium.crypto_scalarmult_base(secret)
    sock.sendall(MAGIC_CLIENT + e_client)
    reply = b""
    while len(reply) < 36:
        chunk = sock.recv(36 - len(reply))
        if not chunk:
            raise SecureError("tablet closed the connection during the handshake")
        reply += chunk
    if reply[:4] != MAGIC_SERVER:
        raise SecureError("not a tabletd handshake")
    try:
        dh = sodium.crypto_scalarmult(secret, reply[4:])
    except SodiumError as exc:   # libsodium refuses an all-zero result
        raise SecureError("unacceptable public key from the tablet") from exc
    k_c2s, k_s2c = derive(psk, e_client, reply[4:], dh)
    return Channel(k_tx=k_c2s, k_rx=k_s2c)
