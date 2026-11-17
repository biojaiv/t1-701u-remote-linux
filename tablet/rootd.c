/*
 * tb-rootd - root access for adb shell while Android is stopped (phase 5).
 *
 * KingRoot's su needs the running Android user interface. In mini mode this
 * daemon replaces it: started as root by the launcher, it listens on an
 * abstract socket, accepts only the allowed user ids (default: root and
 * adb's shell user) and runs the requested command line with sh -c.
 * It gives exactly what su already gives under Android, nothing more.
 *
 * Request:  u32 length (little-endian), command line (at most MAX_CMD bytes)
 * Reply:    frames of u8 kind, u32 length, data;
 *           kind 1 = output of the command, kind 2 = exit status (i32), last.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

#define MAX_CMD 4096
#define MAX_ALLOW 4

static const char *shell = "/system/bin/sh";

static int read_all(int fd, void *buf, size_t n)
{
    for (size_t got = 0; got < n;) {
        ssize_t r = read(fd, (char *)buf + got, n - got);

        if (r <= 0 && !(r < 0 && errno == EINTR))
            return -1;
        if (r > 0)
            got += (size_t)r;
    }
    return 0;
}

static int send_frame(int fd, uint8_t kind, const void *data, uint32_t len)
{
    uint8_t head[5] = { kind, (uint8_t)len, (uint8_t)(len >> 8),
                        (uint8_t)(len >> 16), (uint8_t)(len >> 24) };

    if (send(fd, head, 5, MSG_NOSIGNAL) != 5)
        return -1;
    for (uint32_t off = 0; off < len;) {
        ssize_t r = send(fd, (const char *)data + off, len - off, MSG_NOSIGNAL);

        if (r <= 0)
            return -1;
        off += (uint32_t)r;
    }
    return 0;
}

/* runs in its own process: one request, then exit */
static void serve(int conn)
{
    static char cmd[MAX_CMD + 1], buf[4096];
    uint8_t lenbuf[4];
    uint32_t len;
    int out[2], status = 255;
    pid_t child;
    ssize_t n;

    if (read_all(conn, lenbuf, 4) < 0)
        _exit(0);
    len = (uint32_t)lenbuf[0] | (uint32_t)lenbuf[1] << 8 |
          (uint32_t)lenbuf[2] << 16 | (uint32_t)lenbuf[3] << 24;
    if (len == 0 || len > MAX_CMD || read_all(conn, cmd, len) < 0)
        _exit(0);
    cmd[len] = '\0';
    if (pipe(out) < 0 || (child = fork()) < 0)
        _exit(0);
    if (child == 0) {
        int null = open("/dev/null", O_RDONLY);

        setpgid(0, 0);
        dup2(null, 0);
        dup2(out[1], 1);
        dup2(out[1], 2);
        close(out[0]);
        close(out[1]);
        close(conn);
        execl(shell, "sh", "-c", cmd, (char *)NULL);
        _exit(127);
    }
    /* also here: the child may not have run yet (one CPU online) when the
       caller hangs up, and kill(-child) needs the group to exist */
    setpgid(child, child);
    close(out[1]);
    for (;;) {
        struct pollfd p[2] = { { .fd = out[0], .events = POLLIN },
                               { .fd = conn, .events = POLLIN | POLLRDHUP } };

        if (poll(p, 2, -1) < 0) {
            if (errno == EINTR)
                continue;
            break;
        }
        /* the caller sends nothing after the request: readable means gone */
        if (p[1].revents) {
            kill(-child, SIGKILL);
            break;
        }
        n = read(out[0], buf, sizeof(buf));
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0)
            break;   /* the command closed its output */
        if (send_frame(conn, 1, buf, (uint32_t)n) < 0) {
            kill(-child, SIGKILL);
            break;
        }
    }
    if (waitpid(child, &status, 0) == child)
        status = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    {
        uint8_t code[4] = { (uint8_t)status, (uint8_t)(status >> 8), 0, 0 };

        send_frame(conn, 2, code, 4);
    }
    _exit(0);
}

int main(int argc, char **argv)
{
    struct sockaddr_un sa = { .sun_family = AF_UNIX };
    const char *name = "tabletd-root";
    unsigned allow[MAX_ALLOW] = { 0, 2000 };
    int nallow = 2, custom = 0, fd;

    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--name") == 0) {
            name = argv[i + 1];
        } else if (strcmp(argv[i], "--shell") == 0) {
            shell = argv[i + 1];
        } else if (strcmp(argv[i], "--allow") == 0 && custom < MAX_ALLOW) {
            allow[custom++] = (unsigned)strtoul(argv[i + 1], NULL, 10);
            nallow = custom;
        } else {
            fprintf(stderr, "usage: tb-rootd [--name NAME] [--shell PATH] [--allow UID]...\n");
            return 2;
        }
    }
    if (strlen(name) > sizeof(sa.sun_path) - 2)
        return 2;
    memcpy(sa.sun_path + 1, name, strlen(name));   /* abstract: first byte stays 0 */
    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0 || bind(fd, (struct sockaddr *)&sa,
                       (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + strlen(name))) < 0 ||
        listen(fd, 4) < 0) {
        perror("tb-rootd: listen");
        return 1;
    }
    signal(SIGCHLD, SIG_IGN);   /* finished handlers are reaped by the kernel */
    signal(SIGPIPE, SIG_IGN);
    for (;;) {
        struct ucred cred;
        socklen_t len = sizeof(cred);
        int conn = accept(fd, NULL, NULL), ok = 0;

        if (conn < 0)
            continue;
        if (getsockopt(conn, SOL_SOCKET, SO_PEERCRED, &cred, &len) == 0)
            for (int i = 0; i < nallow; i++)
                ok |= cred.uid == allow[i];
        if (ok && fork() == 0) {
            close(fd);
            signal(SIGCHLD, SIG_DFL);   /* serve() waits for its command */
            serve(conn);
        }
        close(conn);   /* refused callers just see the connection close */
    }
}
