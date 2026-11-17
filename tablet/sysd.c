/*
 * tb-sysd - fixed system actions for the Linux session: the tablet as a USB
 * installation medium (phase 7) and the display brightness.
 *
 * Root daemon with a few fixed actions, started while Android still runs
 * (like tb-linuxd). Callers with an allowed user id (default: root, adb's
 * shell user 2000 and the Linux session user 5000, whose "USB-Installer" and
 * "Settings" programs use it) send one request line and get one reply:
 *
 *   on NAME disk|cd   offer the image NAME from the image folder as a
 *                     read-only removable disk (works for images that also
 *                     boot from a USB stick, i.e. most Linux ISOs) or as a
 *                     CD-ROM (kernel limit about 2.2 GB)
 *   off               take the image away, USB back to adb only
 *   status            "on NAME MODE" or "off"
 *   brightness [N]    read, or set to N (clamped to 10..255, so the screen
 *                     never goes fully dark by accident): "brightness N"
 *   reply: "ok ...", "brightness N", "on ...", "off" or "error ..."
 *
 * Only plain files in the image folder are accepted: the name may not contain
 * a slash, must end in .iso or .img, is opened without following symbolic
 * links, must be a regular file owned by one of the allowed owners (default
 * 2000 and 5000: written by tb-fetch or by the session), and the kernel gets
 * that open file through /proc/self/fd, so nothing can be swapped in between.
 *
 * Order (Huawei's init.sc8830.usb.rc): switching sys.usb.config to
 * mass_storage,adb makes init empty the drive and reset ro and cdrom, so the
 * daemon switches first, waits for sys.usb.state, then sets ro, cdrom, file.
 * adb stays available in both states.
 *
 *   tb-sysd [--name NAME] [--allow UID]... [--owner UID]... [--dir IMAGES]
 *           [--lun SYSFS-DIR] [--backlight FILE] [--setprop PATH] [--getprop PATH]
 *           [--daemon LOG]
 *   tb-sysd ask REQUEST...     client: send one request, print the reply
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define MAX_IDS 4
#define LINE 512

static const char *image_dir = "/data/tabletd/iso";
static const char *lun_dir = "/sys/class/android_usb/android0/f_mass_storage/lun";
static const char *setprop_path = "/system/bin/setprop";
static const char *getprop_path = "/system/bin/getprop";
static const char *backlight_path = "/sys/class/backlight/sprd_backlight/brightness";
static unsigned allow[MAX_IDS] = { 0, 2000, 5000 }, owners[MAX_IDS] = { 2000, 5000 };
static int nallow = 3, nowners = 2;

static int active_fd = -1;
static char active_name[128], active_mode[8];

/* -- helpers ------------------------------------------------------------------ */

static int run(const char *path, char *const argv[], char *out, size_t outlen)
{
    int pipefd[2], status;
    pid_t pid;
    size_t got = 0;

    if (pipe2(pipefd, O_CLOEXEC) < 0)
        return -1;
    pid = fork();
    if (pid < 0)
        return -1;
    if (pid == 0) {
        dup2(pipefd[1], 1);
        execv(path, argv);
        _exit(127);
    }
    close(pipefd[1]);
    for (;;) {
        char discard[64];
        int keep = out && got + 1 < outlen;
        ssize_t n = keep ? read(pipefd[0], out + got, outlen - 1 - got)
                         : read(pipefd[0], discard, sizeof(discard));

        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0)
            break;
        if (keep)
            got += (size_t)n;
    }
    close(pipefd[0]);
    if (out) {
        out[got] = '\0';
        while (got > 0 && (out[got - 1] == '\n' || out[got - 1] == '\r'))
            out[--got] = '\0';
    }
    if (waitpid(pid, &status, 0) != pid)
        return -1;
    return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

static int setprop(const char *key, const char *value)
{
    char *argv[] = { "setprop", (char *)key, (char *)value, NULL };

    return run(setprop_path, argv, NULL, 0);
}

static void getprop(const char *key, char *out, size_t len)
{
    char *argv[] = { "getprop", (char *)key, NULL };

    if (run(getprop_path, argv, out, len) != 0)
        out[0] = '\0';
}

static int wait_usb_state(const char *want, int seconds)
{
    char state[64];

    for (int i = 0; i < seconds * 5; i++) {
        getprop("sys.usb.state", state, sizeof(state));
        if (strcmp(state, want) == 0)
            return 0;
        usleep(200000);
    }
    return -1;
}

static int write_lun(const char *attr, const char *value)
{
    char path[512];
    int fd, ok;
    size_t len = strlen(value);

    snprintf(path, sizeof(path), "%s/%s", lun_dir, attr);
    fd = open(path, O_WRONLY | O_CLOEXEC);
    if (fd < 0)
        return -1;
    ok = write(fd, value, len) == (ssize_t)len;
    close(fd);
    return ok ? 0 : -1;
}

static int valid_name(const char *name)
{
    size_t len = strlen(name);

    if (len < 5 || len > 100 || name[0] == '.')
        return 0;
    for (size_t i = 0; i < len; i++)
        if (!isalnum((unsigned char)name[i]) && !strchr("._+-", name[i]))
            return 0;
    return strcasecmp(name + len - 4, ".iso") == 0 || strcasecmp(name + len - 4, ".img") == 0;
}

/* -- actions ------------------------------------------------------------------ */

static void turn_off(char *reply, size_t len)
{
    char normal[64];

    write_lun("file", "\n");            /* eject */
    getprop("persist.sys.usb.config", normal, sizeof(normal));
    if (!normal[0])
        snprintf(normal, sizeof(normal), "adb");
    setprop("sys.usb.config", normal);
    if (active_fd >= 0)
        close(active_fd);
    active_fd = -1;
    active_name[0] = active_mode[0] = '\0';
    if (wait_usb_state(normal, 10) < 0)
        snprintf(reply, len, "error USB did not return to %s", normal);
    else
        snprintf(reply, len, "ok off");
}

static void turn_on(const char *name, const char *mode, char *reply, size_t len)
{
    struct stat st;
    char fdpath[64];
    int dir, fd, owner_ok = 0;

    if (!valid_name(name)) {
        snprintf(reply, len, "error invalid image name");
        return;
    }
    if (strcmp(mode, "disk") != 0 && strcmp(mode, "cd") != 0) {
        snprintf(reply, len, "error mode must be disk or cd");
        return;
    }
    dir = open(image_dir, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    fd = dir < 0 ? -1 : openat(dir, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (dir >= 0)
        close(dir);
    if (fd < 0 || fstat(fd, &st) < 0 || !S_ISREG(st.st_mode) || st.st_size <= 0) {
        if (fd >= 0)
            close(fd);
        snprintf(reply, len, "error no such image");
        return;
    }
    for (int i = 0; i < nowners; i++)
        owner_ok |= st.st_uid == owners[i];
    if (!owner_ok) {
        close(fd);
        snprintf(reply, len, "error image has a foreign owner");
        return;
    }
    if (strcmp(mode, "cd") == 0 && st.st_size > 2200LL * 1000 * 1000) {
        close(fd);
        snprintf(reply, len, "error too large for cd mode, use disk");
        return;
    }
    if (active_fd >= 0) {
        write_lun("file", "\n");
        close(active_fd);
        active_fd = -1;
    }
    /* init resets the drive when the configuration changes: switch first */
    if (setprop("sys.usb.config", "mass_storage,adb") != 0 ||
        wait_usb_state("mass_storage,adb", 10) < 0) {
        close(fd);
        snprintf(reply, len, "error USB did not switch to mass_storage,adb");
        return;
    }
    snprintf(fdpath, sizeof(fdpath), "/proc/self/fd/%d", fd);
    if (write_lun("ro", "1") < 0 || write_lun("cdrom", strcmp(mode, "cd") == 0 ? "1" : "0") < 0 ||
        write_lun("file", fdpath) < 0) {
        int err = errno;

        close(fd);
        turn_off(reply, len);
        snprintf(reply, len, "error drive did not take the image (%s)", strerror(err));
        return;
    }
    active_fd = fd;
    snprintf(active_name, sizeof(active_name), "%s", name);
    snprintf(active_mode, sizeof(active_mode), "%s", mode);
    snprintf(reply, len, "ok on %s %s", name, mode);
}

static void brightness(const char *value, char *reply, size_t len)
{
    char text[16];
    int fd;
    ssize_t n;

    if (value) {
        char *end;
        long v = strtol(value, &end, 10);

        if (*value == '\0' || *end != '\0') {
            snprintf(reply, len, "error brightness must be a number");
            return;
        }
        v = v < 10 ? 10 : v > 255 ? 255 : v;
        n = snprintf(text, sizeof(text), "%ld\n", v);
        fd = open(backlight_path, O_WRONLY | O_TRUNC | O_CLOEXEC);
        if (fd < 0 || write(fd, text, (size_t)n) != n) {
            if (fd >= 0)
                close(fd);
            snprintf(reply, len, "error cannot set the brightness");
            return;
        }
        close(fd);
    }
    fd = open(backlight_path, O_RDONLY | O_CLOEXEC);
    n = fd < 0 ? -1 : read(fd, text, sizeof(text) - 1);
    if (fd >= 0)
        close(fd);
    if (n <= 0) {
        snprintf(reply, len, "error cannot read the brightness");
        return;
    }
    text[n] = '\0';
    snprintf(reply, len, "brightness %ld", strtol(text, NULL, 10));
}

static void handle(char *line, char *reply, size_t len)
{
    char *words[4] = { 0 };
    int n = 0;

    for (char *save = NULL, *w = strtok_r(line, " \t\r\n", &save); w && n < 4;
         w = strtok_r(NULL, " \t\r\n", &save))
        words[n++] = w;
    if (n == 1 && strcmp(words[0], "status") == 0) {
        if (active_fd >= 0)
            snprintf(reply, len, "on %s %s", active_name, active_mode);
        else
            snprintf(reply, len, "off");
    } else if (n == 1 && strcmp(words[0], "off") == 0) {
        turn_off(reply, len);
    } else if (n == 3 && strcmp(words[0], "on") == 0) {
        turn_on(words[1], words[2], reply, len);
    } else if ((n == 1 || n == 2) && strcmp(words[0], "brightness") == 0) {
        brightness(n == 2 ? words[1] : NULL, reply, len);
    } else {
        snprintf(reply, len, "error unknown request");
    }
}

/* -- daemon and client -------------------------------------------------------------- */

static int socket_address(struct sockaddr_un *sa, const char *name, socklen_t *len)
{
    memset(sa, 0, sizeof(*sa));
    sa->sun_family = AF_UNIX;
    if (strlen(name) > sizeof(sa->sun_path) - 2)
        return -1;
    memcpy(sa->sun_path + 1, name, strlen(name));   /* abstract */
    *len = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + strlen(name));
    return 0;
}

static int client(const char *name, int argc, char **argv)
{
    struct sockaddr_un sa;
    socklen_t salen;
    char request[LINE] = "", reply[LINE];
    ssize_t n;
    size_t got = 0;
    int fd;

    for (int i = 0; i < argc; i++) {
        size_t used = strlen(request);

        snprintf(request + used, sizeof(request) - used, "%s%s", i ? " " : "", argv[i]);
    }
    strncat(request, "\n", sizeof(request) - strlen(request) - 1);
    if (socket_address(&sa, name, &salen) < 0 ||
        (fd = socket(AF_UNIX, SOCK_STREAM, 0)) < 0 || connect(fd, (struct sockaddr *)&sa, salen) < 0) {
        fprintf(stderr, "tb-sysd: daemon is not running\n");
        return 126;
    }
    /* a refused caller may find the socket already closed: that is a
       refusal (126), not a death by SIGPIPE */
    if (send(fd, request, strlen(request), MSG_NOSIGNAL) != (ssize_t)strlen(request))
        return 126;
    while (got + 1 < sizeof(reply) && (n = read(fd, reply + got, sizeof(reply) - 1 - got)) > 0)
        got += (size_t)n;
    reply[got] = '\0';
    if (!got) {
        fprintf(stderr, "tb-sysd: refused\n");
        return 126;
    }
    fputs(reply, stdout);
    return strncmp(reply, "error", 5) == 0 ? 1 : 0;
}

static int daemonize(const char *path, int keep)
{
    int log = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    int null = open("/dev/null", O_RDWR | O_CLOEXEC);
    pid_t pid;

    if (log < 0 || null < 0)
        return -1;
    pid = fork();
    if (pid < 0)
        return -1;
    if (pid > 0)
        _exit(0);
    setsid();
    dup2(null, 0);
    dup2(null, 1);
    dup2(log, 2);
    for (int fd = 3; fd < 1024; fd++)   /* su's terminal and property descriptors */
        if (fd != keep)
            close(fd);
    return 0;
}

static void usage(void)
{
    fprintf(stderr,
            "usage: tb-sysd [--name NAME] [--allow UID]... [--owner UID]... [--dir IMAGES]\n"
            "               [--lun SYSFS-DIR] [--backlight FILE] [--setprop PATH] [--getprop PATH]\n"
            "               [--daemon LOG]\n"
            "       tb-sysd ask REQUEST...\n");
}

int main(int argc, char **argv)
{
    const char *name = getenv("TB_SYSD_NAME"), *daemon_log = NULL;
    struct sockaddr_un sa;
    socklen_t salen;
    int custom_allow = 0, custom_owner = 0, fd;

    if (!name || !*name)
        name = "tabletd-sys";
    if (argc >= 3 && strcmp(argv[1], "ask") == 0)
        return client(name, argc - 2, argv + 2);
    for (int i = 1; i < argc; i += 2) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;

        if (!v) {
            usage();
            return 2;
        }
        if (strcmp(a, "--name") == 0) {
            name = v;
        } else if (strcmp(a, "--allow") == 0 && custom_allow < MAX_IDS) {
            allow[custom_allow++] = (unsigned)strtoul(v, NULL, 10);
            nallow = custom_allow;
        } else if (strcmp(a, "--owner") == 0 && custom_owner < MAX_IDS) {
            owners[custom_owner++] = (unsigned)strtoul(v, NULL, 10);
            nowners = custom_owner;
        } else if (strcmp(a, "--dir") == 0) {
            image_dir = v;
        } else if (strcmp(a, "--lun") == 0) {
            lun_dir = v;
        } else if (strcmp(a, "--backlight") == 0) {
            backlight_path = v;
        } else if (strcmp(a, "--setprop") == 0) {
            setprop_path = v;
        } else if (strcmp(a, "--getprop") == 0) {
            getprop_path = v;
        } else if (strcmp(a, "--daemon") == 0) {
            daemon_log = v;
        } else {
            usage();
            return 2;
        }
    }
    if (socket_address(&sa, name, &salen) < 0)
        return 2;
    fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0 || bind(fd, (struct sockaddr *)&sa, salen) < 0 || listen(fd, 4) < 0) {
        perror("tb-sysd: listen");
        return 1;
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGHUP, SIG_IGN);
    if (daemon_log && daemonize(daemon_log, fd) < 0) {
        fprintf(stderr, "tb-sysd: cannot write log %s\n", daemon_log);
        return 1;
    }
    fprintf(stderr, "tb-sysd pid %d\n", (int)getpid());
    for (;;) {
        struct ucred cred;
        socklen_t len = sizeof(cred);
        char line[LINE], reply[LINE];
        size_t got = 0;
        int conn = accept4(fd, NULL, NULL, SOCK_CLOEXEC), ok = 0;

        if (conn < 0)
            continue;
        if (getsockopt(conn, SOL_SOCKET, SO_PEERCRED, &cred, &len) == 0)
            for (int i = 0; i < nallow; i++)
                ok |= cred.uid == allow[i];
        if (ok) {
            struct timeval tv = { .tv_sec = 5 };

            setsockopt(conn, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
            while (got + 1 < sizeof(line)) {
                ssize_t n = read(conn, line + got, sizeof(line) - 1 - got);

                if (n <= 0)
                    break;
                got += (size_t)n;
                if (memchr(line, '\n', got))
                    break;
            }
            line[got] = '\0';
            if (got && memchr(line, '\n', got)) {
                char logged[sizeof(line)];

                /* handle() splits the line in place: keep the whole request
                   for the log, so refused arguments show up there too */
                memcpy(logged, line, got + 1);
                handle(line, reply, sizeof(reply));
                fprintf(stderr, "tb-sysd: uid %u: %.*s -> %s\n", (unsigned)cred.uid,
                        (int)strcspn(logged, "\n"), logged, reply);
                strncat(reply, "\n", sizeof(reply) - strlen(reply) - 1);
                send(conn, reply, strlen(reply), MSG_NOSIGNAL);
            }
        }
        close(conn);
    }
}
