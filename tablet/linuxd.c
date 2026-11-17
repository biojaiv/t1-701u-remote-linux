/*
 * tb-linuxd - starts and ends the Linux desktop session (Devuan in a chroot).
 *
 * KingRoot's su only works while Android runs, but the session is started
 * later from the start screen (tb-home, shell user). So this daemon is started
 * as root beforehand. It accepts callers with an allowed user id (default:
 * root and adb's shell user 2000) and does exactly one thing for them: start
 * the session that was fixed on its own command line. Nothing from a request
 * is executed.
 *
 * The session runs inside the chroot as an unprivileged user. It ends when
 * the caller goes away or says so (tb-home ends its program), or by itself.
 * Then the session's process group and every process of the session user get
 * SIGTERM, after a grace period SIGKILL; only then is the caller answered, so
 * that nothing draws over the start screen afterwards.
 *
 * Request: one byte, 'S' = start. Afterwards the caller may send 'T' or close
 *          its writing side to end the session.
 * Reply:   two bytes, 'E' and the exit status, once the session has ended and
 *          all its processes are gone. There is one session at a time; a
 *          second caller waits until the first session has ended.
 *
 *   tb-linuxd [--name NAME] [--allow UID]... [--root DIR] [--user UID:GID]
 *             [--groups GID,GID,...] [--env NAME=VALUE]... [--log FILE]
 *             [--grace MS] [--daemon LOGFILE] -- PROGRAM [ARGS...]
 *
 * --daemon detaches from the starting shell (so that "su -c" returns) and
 * writes the daemon's own messages to LOGFILE. PROGRAM is a path inside --root. Run as root, --user is required, and the
 * session user may be neither root nor one of the allowed callers (ending a
 * session kills every process of that user).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
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
#include <time.h>
#include <unistd.h>

#define MAX_ALLOW 4
#define MAX_GROUPS 16
#define MAX_ENV 16

static const char *root_dir, *log_path;
static char **program;
static int have_user;
static uid_t user_uid;
static gid_t user_gid;
static gid_t groups[MAX_GROUPS];
static int ngroups;
static char *env[MAX_ENV + 1];
static int nenv;
static int grace_ms = 1500;
static const char *daemon_log;

static long long now_ms(void)
{
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* -- the session ------------------------------------------------------------ */

static pid_t start_session(void)
{
    pid_t pid = fork();

    if (pid != 0)
        return pid;
    /* child: own session, then chroot, then drop all privileges, then exec */
    setsid();
    signal(SIGPIPE, SIG_DFL);
    signal(SIGCHLD, SIG_DFL);
    {
        int null = open("/dev/null", O_RDONLY);
        int log = log_path ? open(log_path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600)
                           : -1;

        if (null >= 0)
            dup2(null, 0);
        if (log >= 0) {
            dup2(log, 1);
            dup2(log, 2);
        }
    }
    if (root_dir && (chroot(root_dir) < 0 || chdir("/") < 0)) {
        perror("tb-linuxd: chroot");
        _exit(127);
    }
    if (have_user) {
        if (setgroups((size_t)ngroups, groups) < 0 || setgid(user_gid) < 0 ||
            setuid(user_uid) < 0) {
            perror("tb-linuxd: drop privileges");
            _exit(127);
        }
        if (getuid() != user_uid || geteuid() != user_uid || setuid(0) == 0) {
            fprintf(stderr, "tb-linuxd: privileges not dropped\n");
            _exit(127);
        }
    }
    execve(program[0], program, env);
    perror("tb-linuxd: exec");
    _exit(127);
}

/*
 * Signal every process of the session user (kill(-1) as that user reaches
 * exactly those). Returns 1 if there was at least one. Without --user only
 * the process group exists.
 */
static int signal_user(int sig)
{
    pid_t pid;
    int status;

    if (!have_user)
        return 0;
    pid = fork();
    if (pid == 0) {
        if (setgid(user_gid) < 0 || setuid(user_uid) < 0)
            _exit(2);
        _exit(kill(-1, sig) == 0 ? 0 : 1);
    }
    if (pid < 0 || waitpid(pid, &status, 0) != pid)
        return 0;
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

static int group_alive(pid_t sid)
{
    return kill(-sid, 0) == 0;
}

/*
 * Ends the session completely: SIGTERM to its process group and to every
 * process of the session user, SIGKILL after the grace period. Returns the
 * leader's exit status (255 if it could not be collected).
 */
static int end_session(pid_t sid, int status, int leader_done)
{
    long long deadline = now_ms() + grace_ms;
    int killed = 0;

    kill(-sid, SIGTERM);
    signal_user(SIGTERM);
    for (;;) {
        if (!leader_done && waitpid(sid, &status, WNOHANG) == sid)
            leader_done = 1;
        /* the leader is reaped first: a zombie would still count as a member */
        if (leader_done && !group_alive(sid) && !signal_user(0))
            break;
        if (now_ms() >= deadline) {
            if (killed) {
                fprintf(stderr, "tb-linuxd: processes left after SIGKILL\n");
                break;
            }
            kill(-sid, SIGKILL);
            signal_user(SIGKILL);
            killed = 1;
            deadline = now_ms() + grace_ms;
        }
        usleep(50000);
    }
    if (!leader_done)
        return 255;
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}

/* one caller: start, wait for the end, answer */
static void serve(int conn)
{
    uint8_t req, reply[2] = { 'E', 255 };
    pid_t sid;
    int status = 0, leader_done = 0;

    if (recv(conn, &req, 1, 0) != 1 || req != 'S')
        return;
    sid = start_session();
    if (sid < 0) {
        send(conn, reply, 2, MSG_NOSIGNAL);
        return;
    }
    fprintf(stderr, "tb-linuxd: session started (pid %d)\n", (int)sid);
    for (;;) {
        struct pollfd p = { .fd = conn, .events = POLLIN | POLLRDHUP };
        int r = poll(&p, 1, 200);

        if (r < 0 && errno != EINTR)
            break;
        if (waitpid(sid, &status, WNOHANG) == sid) {
            leader_done = 1;
            fprintf(stderr, "tb-linuxd: session ended by itself\n");
            break;
        }
        if (r > 0 && p.revents) {
            fprintf(stderr, "tb-linuxd: caller asks to end the session\n");
            break;   /* 'T', end of stream or an error: all mean "end it" */
        }
    }
    reply[1] = (uint8_t)end_session(sid, status, leader_done);
    fprintf(stderr, "tb-linuxd: session gone (status %d)\n", reply[1]);
    send(conn, reply, 2, MSG_NOSIGNAL);
}

/* -- main ------------------------------------------------------------------- */

/*
 * Detach from the starting shell; the daemon's messages go to the log file.
 * Every other inherited descriptor is closed: KingRoot's su hands down its
 * terminal and property descriptors, which kept the adb call open and would
 * reach the session too. Only the listening socket stays.
 */
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
    for (int fd = 3; fd < 1024; fd++)
        if (fd != keep)
            close(fd);
    return 0;
}

static void usage(void)
{
    fprintf(stderr,
            "usage: tb-linuxd [--name NAME] [--allow UID]... [--root DIR] [--user UID:GID]\n"
            "                 [--groups GID,...] [--env NAME=VALUE]... [--log FILE]\n"
            "                 [--grace MS] [--daemon LOGFILE] -- PROGRAM [ARGS...]\n");
}

static int parse_groups(const char *text)
{
    char *end;

    while (*text) {
        unsigned long g = strtoul(text, &end, 10);

        if (end == text || ngroups >= MAX_GROUPS || g == 0)
            return -1;
        groups[ngroups++] = (gid_t)g;
        text = *end == ',' ? end + 1 : end;
        if (*end && *end != ',')
            return -1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    struct sockaddr_un sa = { .sun_family = AF_UNIX };
    const char *name = "tabletd-linux";
    unsigned allow[MAX_ALLOW] = { 0, 2000 };
    int nallow = 2, custom = 0, fd;

    for (int i = 1; i < argc; i++) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;

        if (strcmp(a, "--") == 0) {
            if (i + 1 < argc)
                program = argv + i + 1;
            break;
        }
        if (!v) {
            usage();
            return 2;
        }
        i++;
        if (strcmp(a, "--name") == 0) {
            name = v;
        } else if (strcmp(a, "--allow") == 0 && custom < MAX_ALLOW) {
            allow[custom++] = (unsigned)strtoul(v, NULL, 10);
            nallow = custom;
        } else if (strcmp(a, "--root") == 0) {
            root_dir = v;
        } else if (strcmp(a, "--user") == 0) {
            unsigned long u, g;

            if (sscanf(v, "%lu:%lu", &u, &g) != 2) {
                usage();
                return 2;
            }
            user_uid = (uid_t)u;
            user_gid = (gid_t)g;
            have_user = 1;
        } else if (strcmp(a, "--groups") == 0) {
            if (parse_groups(v) < 0) {
                usage();
                return 2;
            }
        } else if (strcmp(a, "--env") == 0 && nenv < MAX_ENV && strchr(v, '=')) {
            env[nenv++] = (char *)v;
        } else if (strcmp(a, "--log") == 0) {
            log_path = v;
        } else if (strcmp(a, "--grace") == 0) {
            grace_ms = atoi(v);
        } else if (strcmp(a, "--daemon") == 0) {
            daemon_log = v;
        } else {
            usage();
            return 2;
        }
    }
    if (!program || program[0][0] != '/') {
        usage();
        return 2;
    }
    if (geteuid() == 0 && !have_user) {
        fprintf(stderr, "tb-linuxd: running as root, --user is required\n");
        return 2;
    }
    if (have_user) {
        if (user_uid == 0 || user_gid == 0) {
            fprintf(stderr, "tb-linuxd: the session must not run as root\n");
            return 2;
        }
        for (int i = 0; i < nallow; i++)
            if (allow[i] == user_uid) {
                fprintf(stderr, "tb-linuxd: the session user must not be a caller\n");
                return 2;
            }
    }
    if (strlen(name) > sizeof(sa.sun_path) - 2)
        return 2;
    memcpy(sa.sun_path + 1, name, strlen(name));   /* abstract: first byte stays 0 */
    fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0 || bind(fd, (struct sockaddr *)&sa,
                       (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + strlen(name))) < 0 ||
        listen(fd, 4) < 0) {
        perror("tb-linuxd: listen");
        return 1;
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGHUP, SIG_IGN);   /* outlives the su session that started it */
    if (daemon_log && daemonize(daemon_log, fd) < 0) {
        fprintf(stderr, "tb-linuxd: cannot write log %s\n", daemon_log);
        return 1;
    }
    fprintf(stderr, "tb-linuxd pid %d\n", (int)getpid());
    for (;;) {
        struct ucred cred;
        socklen_t len = sizeof(cred);
        int conn = accept4(fd, NULL, NULL, SOCK_CLOEXEC), ok = 0;

        if (conn < 0)
            continue;
        if (getsockopt(conn, SOL_SOCKET, SO_PEERCRED, &cred, &len) == 0)
            for (int i = 0; i < nallow; i++)
                ok |= cred.uid == allow[i];
        if (ok)
            serve(conn);   /* one session at a time; other callers wait in the backlog */
        close(conn);   /* refused callers just see the connection close */
    }
}
