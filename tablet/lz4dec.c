/*
 * Decoder for one LZ4 block (no frame header, no stored size).
 *
 * A block is a list of sequences: token, literals, 2-byte offset, match.
 * The upper 4 token bits are the literal count, the lower 4 the match length
 * minus 4; a value of 15 is extended by following bytes until one is below
 * 255. The last sequence ends after its literals.
 *
 * The input comes from outside and is not trusted: every length is checked
 * against what is left of the input and of the output.
 */
#include "tabletd.h"

#include <string.h>

/* reads the extension bytes of a length; -1 if the input ends */
static int more_length(const uint8_t *in, size_t in_len, size_t *ip, size_t *len)
{
    uint8_t b;

    do {
        if (*ip >= in_len)
            return -1;
        b = in[(*ip)++];
        *len += b;
    } while (b == 255);
    return 0;
}

int lz4_decode(const uint8_t *in, size_t in_len, uint8_t *out, size_t out_len)
{
    size_t ip = 0, op = 0;

    for (;;) {
        size_t lit, match, offset;
        unsigned token;

        if (ip >= in_len)
            return -1;
        token = in[ip++];

        lit = token >> 4;
        if (lit == 15 && more_length(in, in_len, &ip, &lit) < 0)
            return -1;
        if (lit > in_len - ip || lit > out_len - op)
            return -1;
        if (lit)   /* in or out may be NULL when their length is 0 */
            memcpy(out + op, in + ip, lit);
        ip += lit;
        op += lit;
        if (ip == in_len)
            break;   /* last sequence: literals only */

        if (in_len - ip < 2)
            return -1;
        offset = (size_t)in[ip] | (size_t)in[ip + 1] << 8;
        ip += 2;
        if (offset == 0 || offset > op)
            return -1;

        match = token & 15;
        if (match == 15 && more_length(in, in_len, &ip, &match) < 0)
            return -1;
        match += 4;
        if (match > out_len - op)
            return -1;
        if (offset >= match) {
            memcpy(out + op, out + op - offset, match);   /* no overlap */
        } else {
            /* source and destination overlap: copy forwards byte by byte */
            for (size_t i = 0; i < match; i++)
                out[op + i] = out[op - offset + i];
        }
        op += match;
    }
    return op == out_len ? 0 : -1;
}
