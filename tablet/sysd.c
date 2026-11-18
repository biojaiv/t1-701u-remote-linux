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
 *   transfer give     the transfer drive (--transfer, a FAT image) to the
 *                     computer: unmounted from the tablet, then offered as a
 *                     writable removable disk
 *   transfer take [force]   back to the tablet (mounted at --transfer-mount,
 *                     owned by the session user); refused while the computer
 *                     has not ejected it, unless "force"
 *   transfer status   "transfer none|tablet|computer|computer-ejected"
 *   transfer create MB   make the drive (64..1024 MB, FAT32, label TABLET) with
 *                     Android's newfs_msdos, if there is none yet and enough room
 *   reply: "ok ...", "brightness N", "on ...", "off", "transfer ..." or "error ..."
 *
 * The transfer drive is either the tablet's or the computer's, never both
 * (a FAT file system must not be written from two sides). It comes back to
 * the tablet by itself when the computer lets go of it: an eject (the drive's
 * backing file gets empty), or the computer dropping the USB connection
 * after it had taken the drive (KDE's "Safely remove" powers the device off
 * without an eject; pulling the cable, too). The gadget reports the latter as
 * a uevent USB_STATE=DISCONNECTED; the switch tb-sysd itself makes is not
 * counted, only a disconnect after the computer was CONFIGURED. Android's "mount -o loop" always takes loop0, which the
 * Linux image already uses, so the loop device is set up here directly.
 *
 * The folder lies in the session user's home: that user could replace it by
 * a symbolic link (to an Android system file, say). So it is opened without
 * following links, checked to be a directory, and everything (owner, mount)
 * goes through that open directory, never through its name again.
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
 *           [--transfer IMAGE --transfer-mount DIR] [--mounter PROGRAM] [--daemon LOG]
 *           [--transfer-uid UID] [--newfs PROGRAM] [--uevents FIFO]
 *   (--uevents, for tests: lines with USB_STATE=... instead of the kernel's)
 *   (--mounter, for tests: "PROGRAM mount IMAGE DIR" / "PROGRAM umount DIR"
 *   instead of the built-in loop device and mount)
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
#include <linux/loop.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <linux/netlink.h>
#include <sys/socket.h>
#include <sys/statvfs.h>
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

/* the transfer drive */
enum { T_NONE, T_TABLET, T_COMPUTER };
static const char *transfer_path, *transfer_dir, *mounter;
static const char *newfs_path = "/system/bin/newfs_msdos";
static unsigned transfer_uid = 5000;
static int transfer_state = T_NONE, transfer_fd = -1;
static char transfer_loop[32];
static volatile sig_atomic_t stop_requested;
static const char *uevent_path;   /* tests; default: the kernel's netlink uevents */
static int host_took_it;          /* the computer was CONFIGURED after "give" */

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
    if (transfer_state == T_COMPUTER) {
        snprintf(reply, len, "error the transfer drive is with the computer: take it back first");
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

/* -- the transfer drive -------------------------------------------------------- */

/* the drive's backing file as the gadget sees it: empty after an eject */
static int lun_is_empty(void)
{
    char path[512], text[8] = "";
    int fd;
    ssize_t n;

    snprintf(path, sizeof(path), "%s/file", lun_dir);
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return 0;
    n = read(fd, text, sizeof(text) - 1);
    close(fd);
    return n <= 0 || text[0] == '\n' || text[0] == '\0';
}

/* a free loop device bound to the open image, released by the kernel when
   it is unmounted (autoclear); its path in transfer_loop. Returns the open
   loop device: it must stay open until the mount holds it, because autoclear
   also releases a loop device on its last close (closing it first made the
   mount fail with "Input/output error" on the tablet). -1 if none is free. */
static int attach_loop(int image)
{
    for (int i = 0; i < 8; i++) {
        struct loop_info64 info;
        int loop;

        snprintf(transfer_loop, sizeof(transfer_loop), "/dev/block/loop%d", i);
        loop = open(transfer_loop, O_RDWR | O_CLOEXEC);
        if (loop < 0)
            continue;
        if (ioctl(loop, LOOP_GET_STATUS64, &info) == 0 || errno != ENXIO ||
            ioctl(loop, LOOP_SET_FD, image) < 0) {
            close(loop);                     /* in use */
            continue;
        }
        memset(&info, 0, sizeof(info));
        info.lo_flags = LO_FLAGS_AUTOCLEAR;
        snprintf((char *)info.lo_file_name, sizeof(info.lo_file_name), "%s", transfer_path);
        ioctl(loop, LOOP_SET_STATUS64, &info);
        return loop;
    }
    transfer_loop[0] = '\0';
    return -1;
}

/* the folder, opened without following a link (see the comment at the top):
   an open directory or -1 */
static int open_folder(char *why, size_t len)
{
    const char *slash = strrchr(transfer_dir, '/');
    char parent[512];
    struct stat st;
    int dir, folder;

    if (!slash || slash == transfer_dir || (size_t)(slash - transfer_dir) >= sizeof(parent)) {
        snprintf(why, len, "bad folder %s", transfer_dir);
        return -1;
    }
    memcpy(parent, transfer_dir, (size_t)(slash - transfer_dir));
    parent[slash - transfer_dir] = '\0';
    dir = open(parent, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) {
        snprintf(why, len, "no home folder for Transfer (%s)", strerror(errno));
        return -1;
    }
    if (mkdirat(dir, slash + 1, 0755) < 0 && errno != EEXIST) {
        snprintf(why, len, "cannot make the Transfer folder (%s)", strerror(errno));
        close(dir);
        return -1;
    }
    folder = openat(dir, slash + 1, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    close(dir);
    if (folder < 0 || fstat(folder, &st) < 0 || !S_ISDIR(st.st_mode)) {
        snprintf(why, len, "Transfer is not a plain folder (a link?): refused");
        if (folder >= 0)
            close(folder);
        return -1;
    }
    if (fchown(folder, transfer_uid, transfer_uid) < 0) { /* best effort (tests) */ }
    return folder;
}

static int mount_on_tablet(char *why, size_t len)
{
    char options[160], target[64];
    int image, folder, loop, err;

    folder = open_folder(why, len);
    if (folder < 0)
        return -1;
    if (mounter) {
        char *argv[] = { (char *)mounter, "mount", (char *)transfer_path, (char *)transfer_dir,
                         NULL };
        close(folder);
        if (run(mounter, argv, NULL, 0) != 0) {
            snprintf(why, len, "mounting failed");
            return -1;
        }
        return 0;
    }
    image = open(transfer_path, O_RDWR | O_NOFOLLOW | O_CLOEXEC);
    if (image < 0) {
        snprintf(why, len, "no transfer drive (%s)", strerror(errno));
        close(folder);
        return -1;
    }
    loop = attach_loop(image);
    close(image);
    if (loop < 0) {
        close(folder);
        snprintf(why, len, "no free loop device");
        return -1;
    }
    /* mounted on the open directory itself, not on its name */
    snprintf(target, sizeof(target), "/proc/self/fd/%d", folder);
    /* the session user owns everything; utf8 for umlauts in file names */
    snprintf(options, sizeof(options), "uid=%u,gid=%u,fmask=0113,dmask=0002,utf8",
             transfer_uid, transfer_uid);
    err = mount(transfer_loop, target, "vfat", MS_NOSUID | MS_NODEV | MS_NOEXEC | MS_NOATIME,
                options) < 0 ? errno : 0;
    if (err == EINVAL) {
        snprintf(options, sizeof(options), "uid=%u,gid=%u,fmask=0113,dmask=0002",
                 transfer_uid, transfer_uid);
        err = mount(transfer_loop, target, "vfat", MS_NOSUID | MS_NODEV | MS_NOEXEC | MS_NOATIME,
                    options) < 0 ? errno : 0;
        if (!err)
            fprintf(stderr, "tb-sysd: mounted without utf8 (not supported)\n");
    }
    close(folder);
    if (err)
        ioctl(loop, LOOP_CLR_FD, 0);
    close(loop);                         /* now the mount holds it (if mounted) */
    if (err) {
        snprintf(why, len, "mounting failed (%s)", strerror(err));
        return -1;
    }
    return 0;
}

static int umount_from_tablet(char *why, size_t len)
{
    if (mounter) {
        char *argv[] = { (char *)mounter, "umount", (char *)transfer_dir, NULL };

        if (run(mounter, argv, NULL, 0) != 0) {
            snprintf(why, len, "the Transfer folder is in use: close the programs using it");
            return -1;
        }
        return 0;
    }
    if (umount2(transfer_dir, UMOUNT_NOFOLLOW) < 0 && errno != EINVAL) {
        int err = errno;

        if (err == EBUSY)
            snprintf(why, len, "the Transfer folder is in use: close the programs using it");
        else
            snprintf(why, len, "unmounting failed (%s)", strerror(err));
        return -1;
    }
    if (transfer_loop[0]) {                  /* in case autoclear did not take */
        int loop = open(transfer_loop, O_RDWR | O_CLOEXEC);

        if (loop >= 0) {
            ioctl(loop, LOOP_CLR_FD, 0);
            close(loop);
        }
    }
    return 0;
}

static void back_to_normal_usb(void)
{
    char normal[64];

    getprop("persist.sys.usb.config", normal, sizeof(normal));
    if (!normal[0])
        snprintf(normal, sizeof(normal), "adb");
    setprop("sys.usb.config", normal);
    wait_usb_state(normal, 10);
}

static void transfer_take(int force, char *reply, size_t len)
{
    char why[160];

    if (!transfer_path) {
        snprintf(reply, len, "error no transfer drive configured");
        return;
    }
    if (transfer_state == T_COMPUTER) {
        if (!force && !lun_is_empty()) {
            snprintf(reply, len, "error the computer still uses the drive: eject it there first");
            return;
        }
        write_lun("file", "\n");
        close(transfer_fd);
        transfer_fd = -1;
        back_to_normal_usb();
        transfer_state = T_NONE;
    }
    if (transfer_state == T_NONE) {
        if (mount_on_tablet(why, sizeof(why)) < 0) {
            snprintf(reply, len, "error %s", why);
            return;
        }
        transfer_state = T_TABLET;
    }
    snprintf(reply, len, "ok transfer tablet");
}

static void transfer_give(char *reply, size_t len)
{
    char why[160], fdpath[64];
    int fd;

    if (!transfer_path) {
        snprintf(reply, len, "error no transfer drive configured");
        return;
    }
    if (transfer_state == T_COMPUTER) {
        snprintf(reply, len, "ok transfer computer");
        return;
    }
    if (active_fd >= 0) {
        snprintf(reply, len, "error the USB installer shares an image: stop it first");
        return;
    }
    if (transfer_state == T_TABLET) {
        if (umount_from_tablet(why, sizeof(why)) < 0) {
            snprintf(reply, len, "error %s", why);
            return;
        }
        transfer_state = T_NONE;
    }
    fd = open(transfer_path, O_RDWR | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) {
        snprintf(reply, len, "error no transfer drive (%s)", strerror(errno));
        return;
    }
    if (setprop("sys.usb.config", "mass_storage,adb") != 0 ||
        wait_usb_state("mass_storage,adb", 10) < 0) {
        close(fd);
        transfer_take(0, why, sizeof(why));
        snprintf(reply, len, "error USB did not switch to mass_storage,adb");
        return;
    }
    snprintf(fdpath, sizeof(fdpath), "/proc/self/fd/%d", fd);
    if (write_lun("ro", "0") < 0 || write_lun("cdrom", "0") < 0 || write_lun("file", fdpath) < 0) {
        int err = errno;

        close(fd);
        back_to_normal_usb();
        transfer_take(0, why, sizeof(why));
        snprintf(reply, len, "error drive did not take the image (%s)", strerror(err));
        return;
    }
    transfer_fd = fd;
    transfer_state = T_COMPUTER;
    host_took_it = 0;               /* our own switch disconnects: not counted */
    snprintf(reply, len, "ok transfer computer");
}

static void transfer_create(const char *size_text, char *reply, size_t len)
{
    char tmp[512], size_arg[16], sectors[24], why[160];
    struct statvfs fs;
    char *end;
    long mb = strtol(size_text, &end, 10);

    if (!transfer_path) {
        snprintf(reply, len, "error no transfer drive configured");
        return;
    }
    if (*size_text == '\0' || *end != '\0' || mb < 64 || mb > 1024) {
        snprintf(reply, len, "error size must be 64 to 1024 (MB)");
        return;
    }
    if (access(transfer_path, F_OK) == 0) {
        snprintf(reply, len, "error there is a transfer drive already");
        return;
    }
    snprintf(tmp, sizeof(tmp), "%s", transfer_path);
    {
        char *slash = strrchr(tmp, '/');

        if (slash)
            *slash = '\0';
    }
    /* keep 256 MB free for everything else on /data */
    if (statvfs(tmp, &fs) < 0 ||
        (unsigned long long)fs.f_bavail * fs.f_frsize < (unsigned long long)(mb + 256) << 20) {
        snprintf(reply, len, "error not enough free space for %ld MB (256 MB stay free)", mb);
        return;
    }
    snprintf(tmp, sizeof(tmp), "%s.new", transfer_path);
    unlink(tmp);
    snprintf(size_arg, sizeof(size_arg), "%ldm", mb);
    snprintf(sectors, sizeof(sectors), "%ld", mb * 2048);
    {
        /* a file has no geometry to ask for: newfs_msdos needs all of it
           (without, "Error getting bytes / sector", seen on the tablet) */
        char *argv[] = { (char *)newfs_path, "-F", "32", "-L", "TABLET", "-S", "512",
                         "-s", sectors, "-h", "64", "-u", "32", "-o", "0", "-C", size_arg,
                         tmp, NULL };

        if (run(newfs_path, argv, NULL, 0) != 0 || chmod(tmp, 0600) < 0 ||
            rename(tmp, transfer_path) < 0) {
            unlink(tmp);
            snprintf(reply, len, "error making the drive failed");
            return;
        }
    }
    transfer_state = T_NONE;
    if (mount_on_tablet(why, sizeof(why)) < 0) {
        snprintf(reply, len, "error made, but %s", why);
        return;
    }
    transfer_state = T_TABLET;
    snprintf(reply, len, "ok transfer tablet");
}

static void transfer_status(char *reply, size_t len)
{
    static const char *names[] = { "none", "tablet", "computer" };

    if (transfer_state == T_COMPUTER && lun_is_empty())
        snprintf(reply, len, "transfer computer-ejected");
    else
        snprintf(reply, len, "transfer %s", names[transfer_state]);
}

/* the gadget's USB_STATE uevents: -1 if there is no source */
static int open_uevents(void)
{
    struct sockaddr_nl nl = { .nl_family = AF_NETLINK, .nl_groups = 1 };
    int fd;

    if (uevent_path)                 /* O_RDWR: a FIFO without a writer stays open */
        return open(uevent_path, O_RDWR | O_NONBLOCK | O_CLOEXEC);
    fd = socket(AF_NETLINK, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, NETLINK_KOBJECT_UEVENT);
    if (fd >= 0 && bind(fd, (struct sockaddr *)&nl, sizeof(nl)) < 0) {
        close(fd);
        fd = -1;
    }
    return fd;
}

/* reads what is there: bit 1 if a DISCONNECTED came, bit 2 if the last
   USB_STATE was CONFIGURED (one batch can hold both: our own switch, or a
   quick reconnect) */
#define UEV_DISCONNECTED 1
#define UEV_CONFIGURED 2
static int read_uevents(int fd)
{
    static char buf[8192];
    int seen = 0, last_configured = 0;
    ssize_t n;

    while ((n = read(fd, buf, sizeof(buf) - 1)) > 0) {
        buf[n] = '\0';
        for (ssize_t i = 0; i + 10 <= n; i++) {
            if (strncmp(buf + i, "USB_STATE=", 10) != 0)
                continue;
            if (strncmp(buf + i + 10, "DISCONNECTED", 12) == 0) {
                seen |= UEV_DISCONNECTED;
                last_configured = 0;
            } else if (strncmp(buf + i + 10, "CONFIGURED", 10) == 0) {
                last_configured = 1;
            } else {
                last_configured = 0;           /* CONNECTED */
            }
        }
    }
    return seen | (last_configured ? UEV_CONFIGURED : 0);
}

static void on_term(int sig)
{
    (void)sig;
    stop_requested = 1;
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
    } else if (n >= 2 && strcmp(words[0], "transfer") == 0 && strcmp(words[1], "give") == 0 &&
               n == 2) {
        transfer_give(reply, len);
    } else if (n >= 2 && strcmp(words[0], "transfer") == 0 && strcmp(words[1], "take") == 0 &&
               (n == 2 || (n == 3 && strcmp(words[2], "force") == 0))) {
        transfer_take(n == 3, reply, len);
    } else if (n == 3 && strcmp(words[0], "transfer") == 0 && strcmp(words[1], "create") == 0) {
        transfer_create(words[2], reply, len);
    } else if (n == 2 && strcmp(words[0], "transfer") == 0 && strcmp(words[1], "status") == 0) {
        transfer_status(reply, len);
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
        } else if (strcmp(a, "--transfer") == 0) {
            transfer_path = v;
        } else if (strcmp(a, "--transfer-mount") == 0) {
            transfer_dir = v;
        } else if (strcmp(a, "--transfer-uid") == 0) {
            transfer_uid = (unsigned)strtoul(v, NULL, 10);
        } else if (strcmp(a, "--mounter") == 0) {
            mounter = v;
        } else if (strcmp(a, "--newfs") == 0) {
            newfs_path = v;
        } else if (strcmp(a, "--uevents") == 0) {
            uevent_path = v;
        } else {
            usage();
            return 2;
        }
    }
    if (!transfer_path != !transfer_dir) {
        fprintf(stderr, "tb-sysd: --transfer and --transfer-mount go together\n");
        return 2;
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
    {
        struct sigaction act;

        memset(&act, 0, sizeof(act));
        act.sa_handler = on_term;           /* no SA_RESTART: poll returns */
        sigaction(SIGTERM, &act, NULL);
        sigaction(SIGINT, &act, NULL);
    }
    if (transfer_path && access(transfer_path, F_OK) == 0) {
        char reply[LINE];

        transfer_take(0, reply, sizeof(reply));
        fprintf(stderr, "tb-sysd: transfer drive at start: %s\n", reply);
    }
    int uevents = transfer_path ? open_uevents() : -1;

    if (transfer_path && uevents < 0)
        fprintf(stderr, "tb-sysd: no USB uevents: only an eject brings the drive back\n");
    for (;;) {
        struct ucred cred;
        socklen_t len = sizeof(cred);
        char line[LINE], reply[LINE];
        size_t got = 0;
        struct pollfd p[2] = { { .fd = fd, .events = POLLIN },
                               { .fd = uevents, .events = POLLIN } };
        int conn, ok = 0;

        if (stop_requested) {
            /* the transfer drive must not stay mounted inside the system that
               is about to be unmounted, nor stay with the computer */
            if (transfer_state == T_TABLET)
                umount_from_tablet(reply, sizeof(reply));
            else if (transfer_state == T_COMPUTER)
                transfer_take(1, reply, sizeof(reply)), umount_from_tablet(reply, sizeof(reply));
            fprintf(stderr, "tb-sysd: ending\n");
            return 0;
        }
        /* the computer ejected the drive: it comes back to the tablet. Checked
           every round, not only when no request came for a while: a program
           asking for the status every few seconds kept the check from ever
           running (found by the test) */
        if (transfer_state == T_COMPUTER && lun_is_empty()) {
            transfer_take(0, reply, sizeof(reply));
            fprintf(stderr, "tb-sysd: the computer ejected the transfer drive: %s\n", reply);
        }
        if (poll(p, 2, 2000) <= 0)
            continue;
        if (uevents >= 0 && (p[1].revents & POLLIN)) {
            int usb = read_uevents(uevents);

            if (usb && transfer_state == T_COMPUTER) {
                fprintf(stderr, "tb-sysd: USB%s%s while the computer has the drive\n",
                        usb & UEV_DISCONNECTED ? " disconnected" : "",
                        usb & UEV_CONFIGURED ? " configured" : "");
                if ((usb & UEV_DISCONNECTED) && host_took_it) {
                    transfer_take(1, reply, sizeof(reply));
                    fprintf(stderr, "tb-sysd: the computer let go of the drive: %s\n", reply);
                } else if (usb & UEV_CONFIGURED) {
                    host_took_it = 1;
                }
            }
        }
        if (!(p[0].revents & POLLIN))
            continue;
        conn = accept4(fd, NULL, NULL, SOCK_CLOEXEC);
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
