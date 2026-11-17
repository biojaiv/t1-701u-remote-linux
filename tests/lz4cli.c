/*
 * Test driver for tablet/lz4dec.c, built with the sanitizers.
 *
 * Reads cases from stdin until it ends: u32 in_len, u32 out_len (little-
 * endian), then in_len bytes. Writes one status byte per case (0 = decoded,
 * 1 = error) and, on success, the out_len decoded bytes.
 * Buffers are allocated with their exact size so that the address sanitizer
 * sees every access beyond them.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

int lz4_decode(const uint8_t *in, size_t in_len, uint8_t *out, size_t out_len);

static int read_u32(uint32_t *v)
{
    uint8_t b[4];

    if (fread(b, 1, 4, stdin) != 4)
        return -1;
    *v = (uint32_t)b[0] | (uint32_t)b[1] << 8 | (uint32_t)b[2] << 16 | (uint32_t)b[3] << 24;
    return 0;
}

int main(void)
{
    uint32_t in_len, out_len;

    while (read_u32(&in_len) == 0 && read_u32(&out_len) == 0) {
        uint8_t *in = in_len ? malloc(in_len) : NULL;
        uint8_t *out = out_len ? malloc(out_len) : NULL;
        int rc;

        if ((in_len && !in) || (out_len && !out))
            return 2;
        if (in_len && fread(in, 1, in_len, stdin) != in_len)
            return 2;
        rc = lz4_decode(in, in_len, out, out_len);
        putchar(rc == 0 ? 0 : 1);
        if (rc == 0 && out_len)
            fwrite(out, 1, out_len, stdout);
        free(in);
        free(out);
    }
    return 0;
}
