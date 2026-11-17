/*
 * tb-chroot - runs a command as root inside the Linux system (chroot).
 *
 * Android 4.4 has no chroot program. This one is for setting up and looking
 * after the Devuan system from the laptop (adb shell, su); it gives nothing
 * that root does not already have. The environment is replaced by a fixed,
 * clean one so that nothing from Android's shell leaks in.
 *
 *   tb-chroot DIR PROGRAM [ARGS...]      PROGRAM is a path inside DIR
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    const char *term = getenv("TERM");
    char term_var[64];
    char *env[] = { "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                    "HOME=/root", "LANG=C.UTF-8", "SHELL=/bin/bash", term_var, NULL };

    if (argc < 3 || argv[2][0] != '/') {
        fprintf(stderr, "usage: tb-chroot DIR PROGRAM [ARGS...]   (PROGRAM: absolute path)\n");
        return 2;
    }
    snprintf(term_var, sizeof(term_var), "TERM=%s", term && *term ? term : "linux");
    if (chroot(argv[1]) < 0 || chdir("/") < 0) {
        perror("tb-chroot: chroot");
        return 126;
    }
    execve(argv[2], argv + 2, env);
    perror("tb-chroot: exec");
    return 127;
}
