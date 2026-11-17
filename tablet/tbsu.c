/*
 * tb-su - runs a command as root through tb-rootd (see rootd.c).
 *
 * Usage: tb-su COMMAND [ARGS...]     arguments are joined with spaces
 *        tb-su -c "COMMAND LINE"     as with su
 * Environment: TB_ROOTD_NAME overrides the socket name (tests).
 * Exit status: that of the command; 126 if tb-rootd refused or is not there.
 */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#define MAX_CMD 4096

static int read_all(int fd, void *buf, size_t n)
{
    for (size_t got = 0; got < n;) {
        ssize_t r = read(fd, (char *)buf + got, n - got);

        if (r <= 0)
            return -1;
        got += (size_t)r;
    }
    return 0;
}

int main(int argc, char **argv)
{
    struct sockaddr_un sa = { .sun_family = AF_UNIX };
    const char *name = getenv("TB_ROOTD_NAME");
    static char cmd[MAX_CMD + 1], buf[4096];
    uint8_t head[5];
    size_t len = 0;
    int first = 1, fd;

    if (!name)
        name = "tabletd-root";
    if (argc > 2 && strcmp(argv[1], "-c") == 0)
        first = 2;
    if (argc <= first) {
        fprintf(stderr, "usage: tb-su COMMAND [ARGS...] | tb-su -c \"COMMAND LINE\"\n");
        return 2;
    }
    for (int i = first; i < argc; i++) {
        size_t part = strlen(argv[i]);

        if (len + part + 1 > MAX_CMD) {
            fprintf(stderr, "tb-su: command too long\n");
            return 2;
        }
        if (i > first)
            cmd[len++] = ' ';
        memcpy(cmd + len, argv[i], part);
        len += part;
    }
    if (strlen(name) > sizeof(sa.sun_path) - 2)
        return 2;
    memcpy(sa.sun_path + 1, name, strlen(name));
    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0 || connect(fd, (struct sockaddr *)&sa,
                          (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + strlen(name))) < 0) {
        fprintf(stderr, "tb-su: tb-rootd is not running\n");
        return 126;
    }
    head[0] = (uint8_t)len;
    head[1] = (uint8_t)(len >> 8);
    head[2] = head[3] = 0;
    if (write(fd, head, 4) != 4 || write(fd, cmd, len) != (ssize_t)len)
        return 126;
    for (;;) {
        uint32_t n;

        if (read_all(fd, head, 5) < 0) {
            fprintf(stderr, "tb-su: refused or connection lost\n");
            return 126;
        }
        n = (uint32_t)head[1] | (uint32_t)head[2] << 8 |
            (uint32_t)head[3] << 16 | (uint32_t)head[4] << 24;
        if (head[0] == 2 && n == 4) {
            if (read_all(fd, buf, 4) < 0)
                return 126;
            return (uint8_t)buf[0];
        }
        if (head[0] != 1 || n > sizeof(buf) || read_all(fd, buf, n) < 0)
            return 126;
        if (n && fwrite(buf, 1, n, stdout) != n)
            return 126;
        fflush(stdout);
    }
}
