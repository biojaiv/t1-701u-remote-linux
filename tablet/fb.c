/* Framebuffer output: the real fbdev on the tablet or a plain file for tests. */
#include "tabletd.h"

#include <fcntl.h>
#include <linux/fb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static struct fb_var_screeninfo var;   /* kept for FBIOPAN_DISPLAY */

static int open_file(struct fb *fb, const char *path, const char *geom)
{
    unsigned w, h;
    char tail;

    if (sscanf(geom, "%ux%u%c", &w, &h, &tail) != 2 ||
        w == 0 || h == 0 || w > 4096 || h > 4096) {
        fprintf(stderr, "fb: bad geometry '%s'\n", geom);
        return -1;
    }
    fb->fd = open(path, O_RDWR | O_CREAT, 0644);
    if (fb->fd < 0) {
        perror("fb: open");
        return -1;
    }
    fb->is_file = 1;
    fb->w = w;
    fb->h = h;
    fb->line = w * 4;
    fb->yoff = 0;
    /* file layout: bytes R, G, B, 0xFF per pixel */
    fb->rshift = 0;
    fb->gshift = 8;
    fb->bshift = 16;
    fb->map_len = (size_t)fb->line * h;
    if (ftruncate(fb->fd, (off_t)fb->map_len) < 0) {
        perror("fb: ftruncate");
        return -1;
    }
    return 0;
}

static int open_fbdev(struct fb *fb, const char *path)
{
    struct fb_fix_screeninfo fix;

    fb->fd = open(path, O_RDWR);
    if (fb->fd < 0) {
        perror("fb: open");
        return -1;
    }
    if (ioctl(fb->fd, FBIOGET_VSCREENINFO, &var) < 0 ||
        ioctl(fb->fd, FBIOGET_FSCREENINFO, &fix) < 0) {
        perror("fb: ioctl");
        return -1;
    }
    if (var.bits_per_pixel != 32) {
        fprintf(stderr, "fb: %u bits per pixel not supported\n",
                var.bits_per_pixel);
        return -1;
    }
    fb->w = var.xres;
    fb->h = var.yres;
    fb->line = fix.line_length;
    fb->yoff = var.yoffset;
    fb->rshift = var.red.offset;
    fb->gshift = var.green.offset;
    fb->bshift = var.blue.offset;
    fb->map_len = fix.smem_len;
    fprintf(stderr, "fb: %ux%u virtual %ux%u yoffset %u line %u len %u "
            "r@%u g@%u b@%u a@%u/%u\n", var.xres, var.yres, var.xres_virtual,
            var.yres_virtual, var.yoffset, fix.line_length, fix.smem_len,
            var.red.offset, var.green.offset, var.blue.offset,
            var.transp.offset, var.transp.length);
    if (fb->line < fb->w * 4 ||
        (size_t)(fb->yoff + fb->h) * fb->line > fb->map_len) {
        fprintf(stderr, "fb: inconsistent geometry\n");
        return -1;
    }
    return 0;
}

int fb_open(struct fb *fb, const char *path, const char *geom)
{
    memset(fb, 0, sizeof(*fb));
    if ((geom ? open_file(fb, path, geom) : open_fbdev(fb, path)) < 0)
        return -1;
    /* every bit that is not a colour bit is set (alpha or padding = 0xFF) */
    fb->fill = ~(0xffu << fb->rshift | 0xffu << fb->gshift | 0xffu << fb->bshift);
    for (uint32_t i = 0; i < 32; i++) {
        fb->rtab[i] = (i << 3 | i >> 2) << fb->rshift | fb->fill;
        fb->btab[i] = (i << 3 | i >> 2) << fb->bshift;
    }
    for (uint32_t i = 0; i < 64; i++)
        fb->gtab[i] = (i << 2 | i >> 4) << fb->gshift;
    fb->row = malloc((size_t)fb->w * 4);
    if (!fb->row)
        return -1;
    fb->map = mmap(NULL, fb->map_len, PROT_READ | PROT_WRITE, MAP_SHARED,
                   fb->fd, 0);
    if (fb->map == MAP_FAILED) {
        perror("fb: mmap failed, using pwrite");
        fb->map = NULL;
    }
    return 0;
}

int fb_rect565(struct fb *fb, uint32_t x, uint32_t y, uint32_t w, uint32_t h,
               const uint8_t *px)
{
    const uint32_t *rtab = fb->rtab, *gtab = fb->gtab, *btab = fb->btab;

    for (uint32_t r = 0; r < h; r++) {
        size_t off = (size_t)(fb->yoff + y + r) * fb->line + (size_t)x * 4;
        /* with mmap the pixels go straight into the framebuffer */
        uint8_t *d = fb->map ? fb->map + off : fb->row;

        for (uint32_t c = 0; c < w; c++, px += 2, d += 4) {
            uint32_t v = (uint32_t)px[0] | (uint32_t)px[1] << 8;
            uint32_t out = rtab[v >> 11] | gtab[(v >> 5) & 0x3f] | btab[v & 0x1f];

            /* stored little-endian: byte 0 holds bits 0-7 */
            d[0] = (uint8_t)out;
            d[1] = (uint8_t)(out >> 8);
            d[2] = (uint8_t)(out >> 16);
            d[3] = (uint8_t)(out >> 24);
        }
        if (!fb->map && pwrite(fb->fd, fb->row, (size_t)w * 4, (off_t)off) !=
                            (ssize_t)((size_t)w * 4))
            return -1;
    }
    return 0;
}

int fb_copy(struct fb *fb, uint32_t sx, uint32_t sy, uint32_t w, uint32_t h,
            uint32_t dx, uint32_t dy)
{
    size_t len = (size_t)w * 4;

    /* rows in the order that never overwrites a row still to be read; within
       a row memmove handles the overlap */
    for (uint32_t i = 0; i < h; i++) {
        uint32_t r = dy > sy ? h - 1 - i : i;
        size_t from = (size_t)(fb->yoff + sy + r) * fb->line + (size_t)sx * 4;
        size_t to = (size_t)(fb->yoff + dy + r) * fb->line + (size_t)dx * 4;

        if (fb->map) {
            memmove(fb->map + to, fb->map + from, len);
        } else if (pread(fb->fd, fb->row, len, (off_t)from) != (ssize_t)len ||
                   pwrite(fb->fd, fb->row, len, (off_t)to) != (ssize_t)len) {
            return -1;
        }
    }
    return 0;
}

void fb_pan(struct fb *fb)
{
    if (!fb->is_file)
        ioctl(fb->fd, FBIOPAN_DISPLAY, &var);
}
