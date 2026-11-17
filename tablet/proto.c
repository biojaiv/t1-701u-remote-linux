/* Frame helpers for protocol version 1. All numbers are little-endian. */
#include "tabletd.h"

#include <errno.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>

uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }

uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

void wr16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }

void wr32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

int proto_check_len(uint8_t type, uint32_t len)
{
    if (len > MAX_PAYLOAD)
        return -1;
    switch (type) {
    case MSG_RECT:      return len > RECT_HDR ? 0 : -1;
    case MSG_PRESENT:   return len == 4 ? 0 : -1;
    case MSG_BACKLIGHT: return len == 1 ? 0 : -1;
    case MSG_TIME:      return len == 12 ? 0 : -1;
    case MSG_PING:      return len == 4 ? 0 : -1;
    case MSG_AUTH:      return len == 32 ? 0 : -1;
    case MSG_COPY:      return len == COPY_LEN ? 0 : -1;
    case MSG_AUDIO:     return len > AUDIO_HDR && len <= AUDIO_MAX_LEN ? 0 : -1;
    case MSG_HOME:      return len == 0 ? 0 : -1;
    default:            return -1;
    }
}

int send_all(int fd, const void *data, size_t n)
{
    const uint8_t *p = data;

    while (n > 0) {
        ssize_t r = send(fd, p, n, MSG_NOSIGNAL);
        if (r < 0) {
            if (errno == EINTR)
                continue;
            return -1;   /* includes the send timeout (EAGAIN) */
        }
        p += r;
        n -= (size_t)r;
    }
    return 0;
}
