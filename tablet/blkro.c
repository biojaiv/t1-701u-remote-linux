/*
 * tb-blkro - reads or sets the read-only switch of a block device (BLKROGET,
 * BLKROSET). Huawei's init sets the system partition read-only at every start
 * (sys_wp_init_action in init.rc); writing /system for phase 5 needs it off
 * for that one session. The switch is not stored: the next start sets it again.
 *
 *   tb-blkro DEVICE            print 1 (read-only) or 0
 *   tb-blkro DEVICE 0|1        set it, then print the new state
 */
#include <fcntl.h>
#include <linux/fs.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    int fd, ro;

    if (argc < 2 || argc > 3 || (argc == 3 && strcmp(argv[2], "0") && strcmp(argv[2], "1"))) {
        fprintf(stderr, "usage: tb-blkro DEVICE [0|1]\n");
        return 2;
    }
    fd = open(argv[1], O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        perror("tb-blkro: open");
        return 1;
    }
    if (argc == 3) {
        ro = argv[2][0] == '1';
        if (ioctl(fd, BLKROSET, &ro) < 0) {
            perror("tb-blkro: BLKROSET");
            return 1;
        }
    }
    if (ioctl(fd, BLKROGET, &ro) < 0) {
        perror("tb-blkro: BLKROGET");
        return 1;
    }
    printf("%d\n", ro);
    close(fd);
    return 0;
}
