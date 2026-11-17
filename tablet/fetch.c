/*
 * tb-fetch - receives one file over Wi-Fi from the laptop.
 *
 * Copying the 1.9 GB Linux image with adb push broke off twice (loose cable).
 * Android 4.4 has no download program, so this one waits for exactly one
 * connection from the given laptop address, writes everything until the
 * sender closes to FILE.part, checks the size, renames it to FILE and exits.
 * Connections from any other address are closed at once. The "complete" line
 * carries the BLAKE2b-256 of exactly the bytes received, so the caller can
 * check the content for files of any size (Android's md5 stops at 2 GiB) and
 * notice when another local program answered first (--from 127.0.0.1). The port should be one the tablet's firewall
 * only opens for the laptop (tabletd_in: 5555).
 *
 *   tb-fetch --listen PORT --from IPV4 [--timeout SECONDS] FILE SIZE
 */
#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#include "monocypher.h"

static int usage(void)
{
    fprintf(stderr, "usage: tb-fetch --listen PORT --from IPV4 [--timeout SECONDS] FILE SIZE\n");
    return 2;
}

int main(int argc, char **argv)
{
    struct sockaddr_in addr = { .sin_family = AF_INET };
    struct in_addr from = { 0 };
    const char *path = NULL;
    long long size = -1, got = 0, next_report = 64LL << 20;
    int port = 0, timeout_s = 600, have_from = 0, lfd, conn = -1, out, one = 1;
    static char part[4096], buf[1 << 16];
    time_t deadline;
    crypto_blake2b_ctx hash;
    uint8_t digest[32];
    char hex[2 * sizeof(digest) + 1];

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--listen") == 0 && i + 1 < argc) {
            port = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--from") == 0 && i + 1 < argc) {
            have_from = inet_pton(AF_INET, argv[++i], &from) == 1;
        } else if (strcmp(argv[i], "--timeout") == 0 && i + 1 < argc) {
            timeout_s = atoi(argv[++i]);
        } else if (!path) {
            path = argv[i];
        } else if (size < 0) {
            size = atoll(argv[i]);
        } else {
            return usage();
        }
    }
    signal(SIGHUP, SIG_IGN);   /* keeps going if the adb session that started it ends */
    if (port <= 0 || port > 65535 || !have_from || !path || size < 0 ||
        snprintf(part, sizeof(part), "%s.part", path) >= (int)sizeof(part))
        return usage();

    lfd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    addr.sin_port = htons((unsigned short)port);
    setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    if (lfd < 0 || bind(lfd, (struct sockaddr *)&addr, sizeof(addr)) < 0 || listen(lfd, 1) < 0) {
        perror("tb-fetch: listen");
        return 1;
    }
    fprintf(stderr, "tb-fetch: waiting on port %d for %s\n", port, inet_ntoa(from));
    deadline = time(NULL) + timeout_s;
    while (conn < 0) {
        struct pollfd p = { .fd = lfd, .events = POLLIN };
        struct sockaddr_in peer;
        socklen_t len = sizeof(peer);
        long left = (long)(deadline - time(NULL));
        int c;

        if (left <= 0) {
            fprintf(stderr, "tb-fetch: nobody came within %d s\n", timeout_s);
            return 1;
        }
        if (poll(&p, 1, (int)(left * 1000)) <= 0)
            continue;
        c = accept4(lfd, (struct sockaddr *)&peer, &len, SOCK_CLOEXEC);
        if (c < 0)
            continue;
        if (peer.sin_addr.s_addr != from.s_addr) {
            fprintf(stderr, "tb-fetch: refused %s\n", inet_ntoa(peer.sin_addr));
            close(c);
            continue;
        }
        conn = c;
    }
    close(lfd);   /* exactly one transfer */

    {
        struct timeval tv = { .tv_sec = 60 };   /* stalled sender: give up */

        setsockopt(conn, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    }
    out = open(part, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (out < 0) {
        perror("tb-fetch: open");
        return 1;
    }
    crypto_blake2b_init(&hash, sizeof(digest));
    for (;;) {
        ssize_t n = read(conn, buf, sizeof(buf));

        if (n < 0 && errno == EINTR)
            continue;
        if (n < 0) {
            perror("tb-fetch: receive");
            break;
        }
        if (n == 0)
            break;
        if (got + n > size) {
            fprintf(stderr, "tb-fetch: more data than the expected %lld bytes\n", size);
            break;
        }
        for (ssize_t off = 0; off < n;) {
            ssize_t w = write(out, buf + off, (size_t)(n - off));

            if (w < 0 && errno == EINTR)
                continue;
            if (w <= 0) {
                perror("tb-fetch: write");
                unlink(part);
                return 1;
            }
            off += w;
        }
        crypto_blake2b_update(&hash, (const uint8_t *)buf, (size_t)n);
        got += n;
        if (got >= next_report) {
            fprintf(stderr, "tb-fetch: %lld MB\n", got >> 20);
            next_report += 64LL << 20;
        }
    }
    close(conn);
    if (fsync(out) < 0 || close(out) < 0 || got != size) {
        fprintf(stderr, "tb-fetch: received %lld of %lld bytes, file removed\n", got, size);
        unlink(part);
        return 1;
    }
    if (rename(part, path) < 0) {
        perror("tb-fetch: rename");
        return 1;
    }
    crypto_blake2b_final(&hash, digest);
    for (size_t i = 0; i < sizeof(digest); i++)
        snprintf(hex + 2 * i, 3, "%02x", digest[i]);
    fprintf(stderr, "tb-fetch: %s complete (%lld bytes, blake2b-256 %s)\n", path, got, hex);
    return 0;
}
