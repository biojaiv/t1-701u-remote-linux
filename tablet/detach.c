/*
 * tb-detach - starts a program in its own session, away from the caller.
 *
 * The autostart app runs the launcher through KingRoot's su. When the
 * launcher stops Android, the app dies, its su session ends and KingRoot
 * takes the session's processes along; with one CPU online the child often
 * has not even run yet at that point. So tb-detach returns only after the
 * child has called setsid() (it reports that through a pipe), with stdin,
 * stdout and stderr on /dev/null and every other descriptor closed.
 *
 *   tb-detach PROGRAM [ARGS...]      PROGRAM: absolute path
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    int report[2];
    char ok = 0;
    pid_t pid;

    if (argc < 2 || argv[1][0] != '/') {
        fprintf(stderr, "usage: tb-detach PROGRAM [ARGS...]   (PROGRAM: absolute path)\n");
        return 2;
    }
    if (pipe2(report, O_CLOEXEC) < 0 || (pid = fork()) < 0) {
        perror("tb-detach");
        return 1;
    }
    if (pid == 0) {
        int null = open("/dev/null", O_RDWR);

        if (setsid() < 0)
            _exit(127);
        signal(SIGHUP, SIG_IGN);
        if (null >= 0) {
            dup2(null, 0);
            dup2(null, 1);
            dup2(null, 2);
        }
        for (int fd = 3; fd < 1024; fd++)
            if (fd != report[1])
                close(fd);
        if (write(report[1], "1", 1) != 1)     /* the session is ours now */
            _exit(127);
        execv(argv[1], argv + 1);              /* report[1] closes on exec */
        _exit(127);
    }
    close(report[1]);
    if (read(report[0], &ok, 1) != 1 || ok != '1') {
        fprintf(stderr, "tb-detach: the child did not start\n");
        return 1;
    }
    printf("%d\n", (int)pid);
    return 0;
}
