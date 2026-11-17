/*
 * libtablet-fbfix.so - lets X.Org's fbdev driver use the tablet's screen.
 *
 * Spreadtrum's framebuffer driver (sprdfb) rejects FBIOPUT_VSCREENINFO with
 * EINVAL unless xres, yres, xres_virtual, yres_virtual and xoffset equal its
 * current values (sprdfb_check_var in drivers/video/sprdfb/sprdfb_main.c of
 * the public SC7731 3.10 kernels). It keeps three pages (600x3072), while X
 * asks for the visible height as virtual height (xfree2fbdev_fblayout in
 * xorg-server hw/xfree86/fbdevhw/fbdevhw.c). Loaded into Xorg with
 * LD_PRELOAD, this library sets those fields of such a request to the
 * driver's values when the visible size is unchanged, and passes everything
 * else through untouched. X draws into the first page, which its yoffset 0
 * shows; the larger virtual height it gets back passes X's own check
 * (fbdev_modes_equal compares with >=). The panel runs in video mode
 * (checked on the tablet), so no refresh call is needed after drawing.
 *
 * <sys/ioctl.h> is not included on purpose: with 64-bit time_t (Debian 13
 * armhf) it renames ioctl to __ioctl_time64, and both names are replaced here.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <linux/fb.h>
#include <stdarg.h>
#include <stddef.h>

typedef int (*ioctl_fn)(int, unsigned long, ...);

int ioctl(int fd, unsigned long request, ...);
int __ioctl_time64(int fd, unsigned long request, ...);

static int forward(ioctl_fn real, int fd, unsigned long request, void *arg)
{
    if (request == FBIOPUT_VSCREENINFO && arg) {
        struct fb_var_screeninfo current, *var = arg;

        if (real(fd, FBIOGET_VSCREENINFO, &current) == 0 &&
            var->xres == current.xres && var->yres == current.yres) {
            var->xres_virtual = current.xres_virtual;
            var->yres_virtual = current.yres_virtual;
            var->xoffset = current.xoffset;
        }
    }
    return real(fd, request, arg);
}

static ioctl_fn lookup(const char *name)
{
    ioctl_fn real = (ioctl_fn)dlsym(RTLD_NEXT, name);

    return real ? real : (ioctl_fn)dlsym(RTLD_NEXT, "ioctl");
}

int ioctl(int fd, unsigned long request, ...)
{
    static ioctl_fn real;
    va_list ap;
    void *arg;

    va_start(ap, request);
    arg = va_arg(ap, void *);
    va_end(ap);
    if (!real)
        real = lookup("ioctl");
    return forward(real, fd, request, arg);
}

int __ioctl_time64(int fd, unsigned long request, ...)
{
    static ioctl_fn real;
    va_list ap;
    void *arg;

    va_start(ap, request);
    arg = va_arg(ap, void *);
    va_end(ap);
    if (!real)
        real = lookup("__ioctl_time64");
    return forward(real, fd, request, arg);
}
