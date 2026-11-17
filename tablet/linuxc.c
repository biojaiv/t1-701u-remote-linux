/*
 * tb-linux - the start screen's program behind the "Linux" tile.
 *
 * Asks tb-linuxd (see linuxd.c) to start the Linux session and waits until it
 * has ended. SIGTERM (tb-home: both volume keys held) asks the daemon to end
 * the session; tb-linux returns only once every process of the session is
 * gone, so the start screen is not drawn over afterwards.
 *
 * Exit status: that of the session; 125 if the daemon went away,
 *              126 if tb-linuxd is not running.
 * Environment: TB_LINUXD_NAME overrides the socket name (tests).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

static volatile sig_atomic_t end_requested;

static void on_term(int sig)
{
    (void)sig;
    end_requested = 1;
}

int main(void)
{
    struct sockaddr_un sa = { .sun_family = AF_UNIX };
    const char *name = getenv("TB_LINUXD_NAME");
    struct sigaction act;
    sigset_t ends, waiting;
    uint8_t reply[2];
    size_t got = 0;
    int fd, asked = 0;

    if (!name || !*name)
        name = "tabletd-linux";
    if (strlen(name) > sizeof(sa.sun_path) - 2)
        return 126;
    memcpy(sa.sun_path + 1, name, strlen(name));
    memset(&act, 0, sizeof(act));
    act.sa_handler = on_term;   /* no SA_RESTART: poll returns at once */
    sigaction(SIGTERM, &act, NULL);
    sigaction(SIGINT, &act, NULL);
    signal(SIGPIPE, SIG_IGN);
    /* SIGTERM/SIGINT stay blocked and are let in only inside ppoll, which
       does that atomically: a signal arriving between the check of
       end_requested and the wait can no longer be missed */
    sigemptyset(&ends);
    sigaddset(&ends, SIGTERM);
    sigaddset(&ends, SIGINT);
    sigprocmask(SIG_BLOCK, &ends, &waiting);
    sigdelset(&waiting, SIGTERM);
    sigdelset(&waiting, SIGINT);

    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0 || connect(fd, (struct sockaddr *)&sa,
                          (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + strlen(name))) < 0) {
        fprintf(stderr, "tb-linux: tb-linuxd is not running\n");
        return 126;
    }
    if (send(fd, "S", 1, MSG_NOSIGNAL) != 1) {
        fprintf(stderr, "tb-linux: tb-linuxd refused the request\n");
        return 126;
    }
    fprintf(stderr, "tb-linux: session requested\n");
    while (got < 2) {
        struct pollfd p = { .fd = fd, .events = POLLIN };
        ssize_t n;

        if (end_requested && !asked) {
            send(fd, "T", 1, MSG_NOSIGNAL);
            asked = 1;
            fprintf(stderr, "tb-linux: asked to end the session\n");
        }
        if (ppoll(&p, 1, NULL, &waiting) < 0) {
            if (errno == EINTR)
                continue;
            return 125;
        }
        n = read(fd, reply + got, 2 - got);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0) {
            fprintf(stderr, "tb-linux: tb-linuxd went away\n");
            return 125;
        }
        got += (size_t)n;
    }
    if (reply[0] != 'E')
        return 125;
    fprintf(stderr, "tb-linux: session ended (status %d)\n", reply[1]);
    return reply[1];
}
