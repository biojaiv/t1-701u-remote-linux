/*
 * tb-camtest - first contact with the camera through Android's HAL (v1):
 * opens camera N, prints its parameters, and counts preview frames for a
 * few seconds, first without a preview window (callbacks only). A test tool
 * for the way to a camera app in the tablet's Linux.
 *
 *   tb-camtest [CAMERA] [SECONDS]
 */
#include <dlfcn.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <sys/mman.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <hardware/camera.h>
#include <hardware/hardware.h>

typedef int (*get_module_fn)(const char *id, const struct hw_module_t **module);

static volatile int frames, last_size, notifies;

/* What the HAL asks for. Like Android's camera service (MemoryHeapBase): with
   a descriptor (ION memory the HAL writes the frames into) that memory is
   mapped, otherwise plain heap memory. Without the mapping every preview
   frame stays empty (all zero: a green picture). */
struct mem {
    camera_memory_t m;
    void *base;
    size_t len, buf_size;
    unsigned num_bufs;
    int fd;                         /* our copy of the HAL's descriptor, or -1 */
};

static void release_memory(struct camera_memory *m)
{
    struct mem *x = (struct mem *)m;

    if (x->fd >= 0) {
        munmap(x->base, x->len);
        close(x->fd);
    } else {
        free(x->base);
    }
    free(x);
}

static camera_memory_t *get_memory(int fd, size_t buf_size, unsigned int num_bufs, void *user)
{
    struct mem *x = calloc(1, sizeof(*x));

    (void)user;
    if (!x)
        return NULL;
    x->buf_size = buf_size;
    x->num_bufs = num_bufs ? num_bufs : 1;
    x->len = buf_size * x->num_bufs;
    x->fd = -1;
    if (x->len == 0 || x->len / x->num_bufs != buf_size) {
        free(x);
        return NULL;
    }
    if (fd >= 0) {
        x->fd = fcntl(fd, F_DUPFD_CLOEXEC, 0);
        x->base = x->fd < 0 ? MAP_FAILED
                            : mmap(NULL, x->len, PROT_READ | PROT_WRITE, MAP_SHARED, x->fd, 0);
        if (x->base == MAP_FAILED) {
            if (x->fd >= 0)
                close(x->fd);
            free(x);
            return NULL;
        }
    } else {
        x->base = calloc(1, x->len);
        if (!x->base) {
            free(x);
            return NULL;
        }
    }
    x->m.data = x->base;
    x->m.size = x->len;
    x->m.handle = x;
    x->m.release = release_memory;
    return &x->m;
}

/* buffer INDEX of memory from get_memory, or NULL */
static const uint8_t *buffer_at(const camera_memory_t *data, unsigned index, size_t *len)
{
    const struct mem *x = (const struct mem *)data;

    if (!data || !data->data || index >= x->num_bufs)
        return NULL;
    *len = x->buf_size;
    return (const uint8_t *)x->base + (size_t)index * x->buf_size;
}

static void on_notify(int32_t msg, int32_t a, int32_t b, void *user)
{
    (void)user;
    notifies++;
    if (notifies < 10)
        fprintf(stderr, "notify 0x%x %d %d\n", msg, a, b);
}

static void on_data(int32_t msg, const camera_memory_t *data, unsigned int index,
                    camera_frame_metadata_t *meta, void *user)
{
    (void)index;
    (void)meta;
    (void)user;
    if (msg & CAMERA_MSG_PREVIEW_FRAME) {
        frames++;
        last_size = data ? (int)data->size : -1;
        if (frames <= 3) {
            size_t len = 0, nz = 0;
            const unsigned char *b = buffer_at(data, index, &len);

            for (size_t i = 0; b && i < len; i++)
                nz += b[i] != 0;
            fprintf(stderr, "frame %d index %u: %zu of %zu bytes not zero\n", frames, index, nz, len);
        }
    }
}

static void on_data_ts(int64_t ts, int32_t msg, const camera_memory_t *data, unsigned int index,
                       void *user)
{
    (void)ts;
    (void)msg;
    (void)data;
    (void)index;
    (void)user;
}

int main(int argc, char **argv)
{
    const char *id = argc > 1 ? argv[1] : "0";
    int seconds = argc > 2 ? atoi(argv[2]) : 3;
    void *lib = dlopen("libhardware.so", RTLD_NOW);
    get_module_fn get_module = lib ? (get_module_fn)dlsym(lib, "hw_get_module") : NULL;
    const struct hw_module_t *mod = NULL;
    struct hw_device_t *dev = NULL;
    camera_device_t *cam;
    char *params;
    int rc;

    if (!get_module || get_module(CAMERA_HARDWARE_MODULE_ID, &mod) != 0) {
        fprintf(stderr, "tb-camtest: no camera module\n");
        return 1;
    }
    rc = mod->methods->open(mod, id, &dev);
    if (rc != 0 || !dev) {
        fprintf(stderr, "tb-camtest: open camera %s failed (%d)\n", id, rc);
        return 1;
    }
    cam = (camera_device_t *)dev;
    params = cam->ops->get_parameters(cam);
    printf("parameters:\n%s\n", params ? params : "(none)");
    if (params && cam->ops->put_parameters)
        cam->ops->put_parameters(cam, params);
    cam->ops->set_callbacks(cam, on_notify, on_data, on_data_ts, get_memory, NULL);
    cam->ops->enable_msg_type(cam, CAMERA_MSG_PREVIEW_FRAME | CAMERA_MSG_ERROR);
    rc = cam->ops->set_preview_window(cam, NULL);
    printf("set_preview_window(NULL): %d\n", rc);
    rc = cam->ops->start_preview(cam);
    printf("start_preview: %d\n", rc);
    if (rc == 0) {
        sleep((unsigned)seconds);
        printf("preview frames in %d s: %d (last %d bytes), notifications %d\n", seconds,
               frames, last_size, notifies);
        cam->ops->stop_preview(cam);
    }
    cam->ops->release(cam);
    dev->close(dev);
    return 0;
}
