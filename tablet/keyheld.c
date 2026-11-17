/*
 * tb-keyheld - tells the launcher whether a key is held down right now.
 *
 * Usage: tb-keyheld DEVICE CODE [CODE...]
 * Exit status: 0 = at least one of the keys is held, 1 = none is held,
 *              2 = the device could not be asked (the launcher then plays
 *              safe and starts Android).
 */
#include <fcntl.h>
#include <linux/input.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define BITS_PER_LONG (8 * sizeof(long))

int main(int argc, char **argv)
{
    unsigned long bits[KEY_MAX / BITS_PER_LONG + 1];
    int fd;

    if (argc < 3) {
        fprintf(stderr, "usage: tb-keyheld DEVICE CODE [CODE...]\n");
        return 2;
    }
    memset(bits, 0, sizeof(bits));
    fd = open(argv[1], O_RDONLY);
    if (fd < 0 || ioctl(fd, EVIOCGKEY(sizeof(bits)), bits) < 0) {
        perror("tb-keyheld");
        return 2;
    }
    close(fd);
    for (int i = 2; i < argc; i++) {
        char *end;
        unsigned long code = strtoul(argv[i], &end, 10);

        if (*end || code > KEY_MAX)
            return 2;
        if (bits[code / BITS_PER_LONG] >> (code % BITS_PER_LONG) & 1)
            return 0;
    }
    return 1;
}
