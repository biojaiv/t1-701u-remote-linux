/*
 * Encrypted, mutually authenticated channel for the direct Wi-Fi link.
 *
 * Both sides share a 32-byte key, copied once over the USB cable (pairing).
 * Primitives come from Monocypher; nothing here invents cryptography.
 *
 * Handshake (no round trip is wasted, nothing is sent before it is needed):
 *   client -> server  "HWC1" || e_c           (ephemeral X25519 public key)
 *   server -> client  "HWS1" || e_s
 *   both: dh = X25519(own ephemeral secret, other ephemeral public)
 *         k  = BLAKE2b-512(key = shared key,
 *                          "huawai-companion v1" || e_c || e_s || dh)
 *         client->server key = k[0..31], server->client key = k[32..63]
 *   The client's first record proves to the server that the client holds
 *   the shared key; the server's first record (INFO) proves it the other way.
 *   Fresh ephemeral keys on both sides give forward secrecy and make
 *   replayed records useless.
 *
 * Records: u32 length L (little-endian) || ciphertext || 16-byte MAC,
 *   L = plaintext length + 16, 1 <= plaintext <= SEC_MAX_PLAIN.
 *   XChaCha20-Poly1305 (Monocypher crypto_aead_lock, libsodium compatible),
 *   additional data = the 4 length bytes, nonce = per-direction counter.
 *   Records carry the plain frame stream; frame and record borders need not
 *   coincide.
 */
#include "tabletd.h"

#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "monocypher.h"

static const uint8_t magic_client[4] = { 'H', 'W', 'C', '1' };
static const uint8_t magic_server[4] = { 'H', 'W', 'S', '1' };
static const char label[] = "huawai-companion v1";   /* 19 bytes, no NUL */

static int random_bytes(uint8_t *out, size_t n)
{
    int fd = open("/dev/urandom", O_RDONLY);
    size_t got = 0;

    if (fd < 0)
        return -1;
    while (got < n) {
        ssize_t r = read(fd, out + got, n - got);

        if (r <= 0 && !(r < 0 && errno == EINTR)) {
            close(fd);
            return -1;
        }
        if (r > 0)
            got += (size_t)r;
    }
    close(fd);
    return 0;
}

int sec_load_key(const char *path, uint8_t key[SEC_KEY_LEN])
{
    struct stat st;
    int fd = open(path, O_RDONLY | O_NOFOLLOW);
    ssize_t n;

    if (fd < 0)
        return -1;
    /* a regular file of exactly 32 bytes that nobody else may read */
    if (fstat(fd, &st) < 0 || !S_ISREG(st.st_mode) || st.st_size != SEC_KEY_LEN ||
        (st.st_mode & 077) != 0) {
        close(fd);
        return -1;
    }
    n = read(fd, key, SEC_KEY_LEN);
    close(fd);
    return n == SEC_KEY_LEN ? 0 : -1;
}

int sec_server_handshake(struct sec *s, const uint8_t psk[SEC_KEY_LEN],
                         const uint8_t hello[SEC_HELLO_LEN],
                         uint8_t reply[SEC_HELLO_LEN])
{
    static const uint8_t zero[32];
    uint8_t secret[32], e_s[32], dh[32], k[64];
    uint8_t msg[sizeof(label) - 1 + 3 * 32];
    int ok = -1;

    memset(s, 0, sizeof(*s));
    if (memcmp(hello, magic_client, 4) != 0 || random_bytes(secret, sizeof(secret)) < 0)
        return -1;
    crypto_x25519_public_key(e_s, secret);
    crypto_x25519(dh, secret, hello + 4);
    /* a low-order point from the peer gives an all-zero secret: refuse it */
    if (crypto_verify32(dh, zero) == 0)
        goto out;

    memcpy(msg, label, sizeof(label) - 1);
    memcpy(msg + sizeof(label) - 1, hello + 4, 32);
    memcpy(msg + sizeof(label) - 1 + 32, e_s, 32);
    memcpy(msg + sizeof(label) - 1 + 64, dh, 32);
    crypto_blake2b_keyed(k, sizeof(k), psk, SEC_KEY_LEN, msg, sizeof(msg));
    memcpy(s->k_rx, k, 32);         /* client -> server */
    memcpy(s->k_tx, k + 32, 32);    /* server -> client */

    memcpy(reply, magic_server, 4);
    memcpy(reply + 4, e_s, 32);
    ok = 0;
out:
    crypto_wipe(secret, sizeof(secret));
    crypto_wipe(dh, sizeof(dh));
    crypto_wipe(k, sizeof(k));
    crypto_wipe(msg, sizeof(msg));
    return ok;
}

static void make_nonce(uint8_t nonce[24], uint64_t counter)
{
    memset(nonce, 0, 24);
    for (int i = 0; i < 8; i++)
        nonce[i] = (uint8_t)(counter >> (8 * i));
}

size_t sec_seal(struct sec *s, uint8_t *out, const uint8_t *plain, size_t len)
{
    uint8_t nonce[24];
    uint32_t total = (uint32_t)len + SEC_MAC_LEN;

    wr32(out, total);
    make_nonce(nonce, s->n_tx++);
    crypto_aead_lock(out + 4, out + 4 + len, s->k_tx, nonce, out, 4, plain, len);
    return 4 + total;
}

long sec_open(struct sec *s, uint8_t *plain, const uint8_t hdr[4],
              const uint8_t *body, size_t body_len)
{
    uint8_t nonce[24];
    size_t len;

    if (body_len != rd32(hdr) || body_len <= SEC_MAC_LEN ||
        body_len > SEC_MAX_PLAIN + SEC_MAC_LEN)
        return -1;
    len = body_len - SEC_MAC_LEN;
    make_nonce(nonce, s->n_rx);
    if (crypto_aead_unlock(plain, body + len, s->k_rx, nonce, hdr, 4, body, len) != 0)
        return -1;
    s->n_rx++;   /* only after success: a forged record cannot shift the counter */
    return (long)len;
}

void sec_wipe(struct sec *s)
{
    crypto_wipe(s, sizeof(*s));
}
