/*
 * tb-camera - the tablet's cameras for its Linux system.
 *
 * The sensors are run by Android's camera HAL (camera.sc8830.so): the sensor
 * drivers live there, not in the kernel, so no Linux camera program can use
 * /dev/video0 directly. This daemon loads that HAL module itself, like
 * Android's mediaserver does, but without Android's camera service (which
 * would wait for system_server, stopped in the own system). Built with the
 * Android NDK (bionic), because the HAL is a bionic library.
 *
 * It listens on an abstract socket for one client at a time: the session
 * user (5000), root or the shell user (2000). After binding it drops to the
 * media user with the groups mediaserver has (the devices need them).
 *
 * Client -> daemon, text lines:
 *   start CAMERA WIDTH HEIGHT ROTATION   preview as RGB24 of WIDTH x HEIGHT
 *                                        (after rotating by ROTATION: 0, 90,
 *                                        180, 270 degrees clockwise)
 *   photo [ROTATION]                     take a picture (JPEG, largest size),
 *                                        turned upright by ROTATION degrees
 *   stop
 * Daemon -> client, messages "u8 type, u32 length (little endian), payload":
 *   1 frame   u16 width, u16 height, RGB24 rows
 *   2 photo   the JPEG file
 *   3 error   text
 *   4 info    text: "cameras N", then per camera "back|front ORIENTATION"
 * Only the newest preview frame is sent; frames the client cannot take in
 * time are dropped.
 *
 *   tb-camera [--name NAME] [--allow UID]... [--no-drop] [--daemon LOG]
 *   (--daemon: in the background; the log's first line is "tb-camera pid N")
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>

#include <hardware/camera.h>
#include <hardware/hardware.h>

#define MSG_FRAME 1
#define MSG_PHOTO 2
#define MSG_ERROR 3
#define MSG_INFO 4
#define MAX_SIDE 1024
#define AID_MEDIA 1013
#define AID_AUDIO 1005
#define AID_CAMERA 1006
#define AID_GRAPHICS 1003

typedef int (*get_module_fn)(const char *id, const struct hw_module_t **module);

static const struct hw_module_t *module;
static camera_device_t *cam;
static int client = -1;
static int wake[2];                 /* HAL threads -> main loop */

/* the newest converted frame (guarded by lock) */
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static uint8_t *frame;              /* header (9 bytes) + RGB */
static size_t frame_len;
static int frame_ready, frame_busy;
static int out_w, out_h, rotation, src_w, src_h;
static uint8_t *photo;              /* JPEG from the HAL */
static size_t photo_len;
static int photo_ready, photo_pending;

/* -- memory for the HAL's callbacks --------------------------------------- */

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
    int users;                      /* callbacks reading it right now */
    struct mem *next;               /* in "live" */
};

/* Memory the HAL may still hand to callbacks. The HAL frees preview memory
   from its own thread (taking a picture stops the preview first) while a
   callback on another thread may still be converting a frame from it; that
   crashed tb-camera. Android's camera service holds a reference for the
   callback (sp<IMemoryHeap>); here a callback takes the memory with
   mem_acquire, and release waits until no callback reads it any more. */
static pthread_mutex_t mem_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t mem_idle = PTHREAD_COND_INITIALIZER;
static struct mem *live;

static struct mem *mem_acquire(const camera_memory_t *data)
{
    struct mem *x;

    pthread_mutex_lock(&mem_lock);
    for (x = live; x && &x->m != data; x = x->next)
        ;
    if (x)
        x->users++;
    pthread_mutex_unlock(&mem_lock);
    return x;                       /* NULL: not (or no longer) ours */
}

static void mem_done(struct mem *x)
{
    pthread_mutex_lock(&mem_lock);
    if (--x->users == 0)
        pthread_cond_broadcast(&mem_idle);
    pthread_mutex_unlock(&mem_lock);
}

static void release_memory(struct camera_memory *m)
{
    struct mem *x = (struct mem *)m, **pp;

    pthread_mutex_lock(&mem_lock);
    for (pp = &live; *pp && *pp != x; pp = &(*pp)->next)
        ;
    if (*pp)
        *pp = x->next;
    while (x->users > 0)
        pthread_cond_wait(&mem_idle, &mem_lock);
    pthread_mutex_unlock(&mem_lock);
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
    pthread_mutex_lock(&mem_lock);
    x->next = live;
    live = x;
    pthread_mutex_unlock(&mem_lock);
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

/* -- NV21 -> RGB24, scaled (nearest) and rotated -------------------------- */

static inline uint8_t clamp(int v)
{
    return v < 0 ? 0 : v > 255 ? 255 : (uint8_t)v;
}

/* destination pixel (x, y) of an out_w x out_h image rotated clockwise by
   rotation; the source is src_w x src_h */
static void convert(const uint8_t *nv21, uint8_t *rgb)
{
    const uint8_t *vu = nv21 + (size_t)src_w * src_h;
    int rot_w = rotation % 180 ? src_h : src_w;   /* size after rotating */
    int rot_h = rotation % 180 ? src_w : src_h;

    for (int y = 0; y < out_h; y++) {
        int ry = y * rot_h / out_h;
        uint8_t *o = rgb + (size_t)y * out_w * 3;

        for (int x = 0; x < out_w; x++) {
            int rx = x * rot_w / out_w, sx, sy;

            switch (rotation) {        /* rotated (rx, ry) -> source (sx, sy) */
            case 90:  sx = ry;              sy = src_h - 1 - rx; break;
            case 180: sx = src_w - 1 - rx;  sy = src_h - 1 - ry; break;
            case 270: sx = src_w - 1 - ry;  sy = rx;             break;
            default:  sx = rx;              sy = ry;             break;
            }
            {
                int Y = nv21[(size_t)sy * src_w + sx] - 16;
                const uint8_t *c = vu + (size_t)(sy / 2) * src_w + (sx & ~1);
                int V = c[0] - 128, U = c[1] - 128;
                int C = 298 * (Y < 0 ? 0 : Y);

                o[0] = clamp((C + 409 * V + 128) >> 8);
                o[1] = clamp((C - 100 * U - 208 * V + 128) >> 8);
                o[2] = clamp((C + 516 * U + 128) >> 8);
                o += 3;
            }
        }
    }
}

static void poke(void)
{
    char c = 1;

    if (write(wake[1], &c, 1) < 0) { /* the loop wakes up anyway soon */ }
}

static void put_header(uint8_t *p, uint8_t type, uint32_t len)
{
    p[0] = type;
    p[1] = (uint8_t)len;
    p[2] = (uint8_t)(len >> 8);
    p[3] = (uint8_t)(len >> 16);
    p[4] = (uint8_t)(len >> 24);
}

/* -- HAL callbacks (their own threads) ------------------------------------- */

static void on_notify(int32_t msg, int32_t a, int32_t b, void *user)
{
    (void)user;
    if (msg == CAMERA_MSG_ERROR)
        fprintf(stderr, "tb-camera: HAL error %d %d\n", a, b);
}

static void on_data(int32_t msg, const camera_memory_t *data, unsigned int index,
                    camera_frame_metadata_t *meta, void *user)
{
    size_t len = 0;
    struct mem *x = mem_acquire(data);
    const uint8_t *src = x ? buffer_at(data, index, &len) : NULL;

    (void)meta;
    (void)user;
    if (!src) {
        if (x)
            mem_done(x);
        return;
    }
    if (msg & CAMERA_MSG_PREVIEW_FRAME) {
        pthread_mutex_lock(&lock);
        if (!frame_busy && frame && len >= (size_t)src_w * src_h * 3 / 2) {
            frame_busy = 1;
            pthread_mutex_unlock(&lock);
            convert(src, frame + 9);
            pthread_mutex_lock(&lock);
            frame_busy = 0;
            frame_ready = 1;
            pthread_mutex_unlock(&lock);
            poke();
        } else {
            pthread_mutex_unlock(&lock);
        }
    }
    if (msg & CAMERA_MSG_COMPRESSED_IMAGE) {
        uint8_t *copy = malloc(5 + len);

        if (!copy) {
            mem_done(x);
            return;
        }
        put_header(copy, MSG_PHOTO, (uint32_t)len);
        memcpy(copy + 5, src, len);
        pthread_mutex_lock(&lock);
        free(photo);
        photo = copy;
        photo_len = 5 + len;
        photo_ready = 1;
        pthread_mutex_unlock(&lock);
        poke();
    }
    mem_done(x);
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

/* -- parameters: "key=value;key=value" ----------------------------------- */

static char *set_param(char *params, const char *key, const char *value)
{
    size_t klen = strlen(key), len = strlen(params);
    char *out = malloc(len + klen + strlen(value) + 3);
    const char *p = params;
    int found = 0;

    if (!out)
        return params;
    out[0] = '\0';
    while (*p) {
        const char *end = strchr(p, ';');
        size_t n = end ? (size_t)(end - p) : strlen(p);

        if (out[0])
            strcat(out, ";");
        if (n > klen && strncmp(p, key, klen) == 0 && p[klen] == '=') {
            strcat(out, key);
            strcat(out, "=");
            strcat(out, value);
            found = 1;
        } else {
            strncat(out, p, n);
        }
        p += n + (end ? 1 : 0);
    }
    if (!found) {
        if (out[0])
            strcat(out, ";");
        strcat(out, key);
        strcat(out, "=");
        strcat(out, value);
    }
    free(params);
    return out;
}

/* the largest "WxH" in a comma list */
static void largest(const char *list, char *out, size_t len)
{
    long best = 0;

    out[0] = '\0';
    while (list && *list) {
        int w = 0, h = 0;

        if (sscanf(list, "%dx%d", &w, &h) == 2 && (long)w * h > best) {
            best = (long)w * h;
            snprintf(out, len, "%dx%d", w, h);
        }
        list = strchr(list, ',');
        if (list)
            list++;
    }
}

static const char *param(const char *params, const char *key, char *out, size_t len)
{
    const char *p = params;
    size_t klen = strlen(key);

    while (p && *p) {
        if (strncmp(p, key, klen) == 0 && p[klen] == '=') {
            size_t n = strcspn(p + klen + 1, ";");

            if (n >= len)
                n = len - 1;
            memcpy(out, p + klen + 1, n);
            out[n] = '\0';
            return out;
        }
        p = strchr(p, ';');
        if (p)
            p++;
    }
    out[0] = '\0';
    return out;
}

/* -- camera control (main thread) ----------------------------------------- */

static void send_msg(uint8_t type, const void *data, size_t len)
{
    uint8_t head[5];

    if (client < 0)
        return;
    put_header(head, type, (uint32_t)len);
    if (send(client, head, 5, MSG_NOSIGNAL) != 5 ||
        (len && send(client, data, len, MSG_NOSIGNAL) != (ssize_t)len)) {
        close(client);
        client = -1;
    }
}

static void stop_camera(void)
{
    if (!cam)
        return;
    cam->ops->stop_preview(cam);
    cam->ops->release(cam);
    cam->common.close(&cam->common);
    cam = NULL;
    pthread_mutex_lock(&lock);
    free(frame);
    frame = NULL;
    frame_ready = photo_ready = photo_pending = 0;
    pthread_mutex_unlock(&lock);
    fprintf(stderr, "tb-camera: camera closed\n");
}

/* preview size: the smallest supported one that is at least the output's
   size after rotating back (less to convert), else the largest */
static void choose_preview(const char *list, int need_w, int need_h, int *w, int *h)
{
    long best = -1, biggest = 0;

    *w = *h = 0;
    while (list && *list) {
        int pw = 0, ph = 0;

        if (sscanf(list, "%dx%d", &pw, &ph) == 2) {
            long area = (long)pw * ph;

            if (pw >= need_w && ph >= need_h && pw * 3 == ph * 4 && (best < 0 || area < best)) {
                best = area;
                *w = pw;
                *h = ph;
            }
            if (best < 0 && area > biggest) {
                biggest = area;
                *w = pw;
                *h = ph;
            }
        }
        list = strchr(list, ',');
        if (list)
            list++;
    }
}

static void start_camera(int id, int w, int h, int rot)
{
    char name[8], sizes[512], size[32], biggest[32];
    struct hw_device_t *dev = NULL;
    char *params;
    int pw, ph;

    stop_camera();
    if (w < 16 || h < 16 || w > MAX_SIDE || h > MAX_SIDE || (rot % 90) || rot < 0 || rot > 270) {
        send_msg(MSG_ERROR, "bad start request", 17);
        return;
    }
    snprintf(name, sizeof(name), "%d", id);
    if (module->methods->open(module, name, &dev) != 0 || !dev) {
        send_msg(MSG_ERROR, "cannot open the camera", 22);
        return;
    }
    cam = (camera_device_t *)dev;
    params = cam->ops->get_parameters(cam);
    if (!params) {
        stop_camera();
        send_msg(MSG_ERROR, "camera has no parameters", 24);
        return;
    }
    params = strdup(params);
    choose_preview(param(params, "preview-size-values", sizes, sizeof(sizes)),
                   rot % 180 ? h : w, rot % 180 ? w : h, &pw, &ph);
    if (!pw) {
        free(params);
        stop_camera();
        send_msg(MSG_ERROR, "no preview size", 15);
        return;
    }
    snprintf(size, sizeof(size), "%dx%d", pw, ph);
    params = set_param(params, "preview-size", size);
    params = set_param(params, "preview-format", "yuv420sp");
    largest(param(params, "picture-size-values", sizes, sizeof(sizes)), biggest,
            sizeof(biggest));
    if (biggest[0])
        params = set_param(params, "picture-size", biggest);
    params = set_param(params, "jpeg-quality", "92");
    cam->ops->set_parameters(cam, params);
    free(params);

    pthread_mutex_lock(&lock);
    src_w = pw;
    src_h = ph;
    out_w = w;
    out_h = h;
    rotation = rot;
    free(frame);
    frame_len = 9 + (size_t)w * h * 3;
    frame = malloc(frame_len);
    if (frame) {
        put_header(frame, MSG_FRAME, (uint32_t)(frame_len - 5));
        frame[5] = (uint8_t)w;
        frame[6] = (uint8_t)(w >> 8);
        frame[7] = (uint8_t)h;
        frame[8] = (uint8_t)(h >> 8);
    }
    frame_ready = 0;
    pthread_mutex_unlock(&lock);

    cam->ops->set_callbacks(cam, on_notify, on_data, on_data_ts, get_memory, NULL);
    cam->ops->enable_msg_type(cam, CAMERA_MSG_PREVIEW_FRAME | CAMERA_MSG_COMPRESSED_IMAGE |
                                   CAMERA_MSG_ERROR);
    cam->ops->set_preview_window(cam, NULL);       /* callbacks only: no window needed */
    if (!frame || cam->ops->start_preview(cam) != 0) {
        stop_camera();
        send_msg(MSG_ERROR, "preview did not start", 21);
        return;
    }
    fprintf(stderr, "tb-camera: camera %d, preview %dx%d -> %dx%d, rotation %d, photos %s\n",
            id, pw, ph, w, h, rot, biggest);
}

static void take_photo(int rot)
{
    if (!cam || photo_pending)
        return;
    if (rot == 0 || rot == 90 || rot == 180 || rot == 270) {
        char *params = cam->ops->get_parameters(cam), value[8];

        if (params) {
            params = strdup(params);
            snprintf(value, sizeof(value), "%d", rot);
            params = set_param(params, "rotation", value);   /* the HAL turns the JPEG */
            cam->ops->set_parameters(cam, params);
            free(params);
        }
    }
    photo_pending = 1;
    if (cam->ops->take_picture(cam) != 0) {
        photo_pending = 0;
        send_msg(MSG_ERROR, "taking the picture failed", 25);
    }
}

static void send_info(void)
{
    const camera_module_t *m = (const camera_module_t *)module;
    char text[256];
    int n = m->get_number_of_cameras(), len;

    len = snprintf(text, sizeof(text), "cameras %d", n);
    for (int i = 0; i < n && len < (int)sizeof(text) - 32; i++) {
        struct camera_info info = { 0 };

        if (m->get_camera_info(i, &info) == 0)
            len += snprintf(text + len, sizeof(text) - (size_t)len, "\n%s %d",
                            info.facing == CAMERA_FACING_BACK ? "back" : "front",
                            info.orientation);
    }
    send_msg(MSG_INFO, text, (size_t)len);
}

/* one command line from the client */
static void command(char *line)
{
    int id, w, h, rot;

    if (sscanf(line, "start %d %d %d %d", &id, &w, &h, &rot) == 4)
        start_camera(id, w, h, rot);
    else if (sscanf(line, "photo %d", &rot) == 1)
        take_photo(rot);
    else if (strcmp(line, "photo") == 0)
        take_photo(-1);
    else if (strcmp(line, "stop") == 0)
        stop_camera();
    else
        send_msg(MSG_ERROR, "unknown request", 15);
}

/* what the HAL threads have ready */
static void deliver(void)
{
    uint8_t *copy = NULL;
    size_t len = 0;
    int restart = 0;

    pthread_mutex_lock(&lock);
    if (photo_ready) {
        copy = photo;
        len = photo_len;
        photo = NULL;
        photo_ready = 0;
        photo_pending = 0;
        restart = 1;                 /* taking a picture stops the preview */
    }
    pthread_mutex_unlock(&lock);
    if (copy) {
        send_msg(MSG_PHOTO, copy + 5, len - 5);
        free(copy);
    }
    if (restart && cam)
        cam->ops->start_preview(cam);
    pthread_mutex_lock(&lock);
    if (frame_ready && frame && client >= 0) {
        frame_busy = 1;              /* the HAL does not write it while it is sent */
        frame_ready = 0;
        pthread_mutex_unlock(&lock);
        if (send(client, frame, frame_len, MSG_NOSIGNAL) != (ssize_t)frame_len) {
            close(client);
            client = -1;
        }
        pthread_mutex_lock(&lock);
        frame_busy = 0;
    }
    pthread_mutex_unlock(&lock);
}

/* in the background, the log on stderr; everything else inherited is closed
   (su's terminal and property descriptors), except the listening socket */
static int daemonize(const char *path, int keep)
{
    int log = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0640);
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

static int drop_to_media(void)
{
    gid_t groups[] = { AID_AUDIO, AID_CAMERA, AID_GRAPHICS, AID_MEDIA };

    if (setgroups(sizeof(groups) / sizeof(groups[0]), groups) < 0 || setgid(AID_MEDIA) < 0 ||
        setuid(AID_MEDIA) < 0)
        return -1;
    return setuid(0) == 0 ? -1 : 0;
}

/* The camera runs in a child process; if the HAL (or this program) crashes,
   the child is started again, so the app can reconnect instead of finding no
   service until the next start of the Linux system. The child ends with the
   parent (linux-root.sh stops the parent, whose pid is in the log). Returns
   in the child. */
static void supervise(void)
{
    time_t window = time(NULL);
    int starts = 0;

    for (;;) {
        pid_t parent = getpid(), pid = fork();   /* parent: before the fork */
        int status;

        if (pid == 0) {
            prctl(PR_SET_PDEATHSIG, SIGTERM);
            if (getppid() != parent)
                _exit(0);           /* the parent is already gone */
            return;
        }
        if (pid < 0) {
            sleep(1);
            continue;
        }
        while (waitpid(pid, &status, 0) < 0 && errno == EINTR)
            ;
        if (WIFEXITED(status)) {
            fprintf(stderr, "tb-camera: camera process ended (%d)\n", WEXITSTATUS(status));
            exit(WEXITSTATUS(status));
        }
        fprintf(stderr, "tb-camera: camera process died (signal %d), starting it again\n",
                WIFSIGNALED(status) ? WTERMSIG(status) : 0);
        if (time(NULL) - window > 60) {
            window = time(NULL);
            starts = 0;
        }
        if (++starts > 5) {
            fprintf(stderr, "tb-camera: died too often within a minute, giving up\n");
            exit(1);
        }
        sleep(1);
    }
}

int main(int argc, char **argv)
{
    const char *name = "tabletd-camera";
    unsigned allow[4] = { 5000, 0, 2000 };
    int nallow = 3, custom = 0, drop = 1, lfd;
    const char *daemon_log = NULL;
    struct sockaddr_un sa = { .sun_family = AF_UNIX };
    socklen_t salen;
    void *lib;
    get_module_fn get_module;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--name") == 0 && i + 1 < argc) {
            name = argv[++i];
        } else if (strcmp(argv[i], "--allow") == 0 && i + 1 < argc && custom < 4) {
            allow[custom++] = (unsigned)strtoul(argv[++i], NULL, 10);
            nallow = custom;
        } else if (strcmp(argv[i], "--no-drop") == 0) {
            drop = 0;
        } else if (strcmp(argv[i], "--daemon") == 0 && i + 1 < argc) {
            daemon_log = argv[++i];
        } else {
            fprintf(stderr, "usage: tb-camera [--name NAME] [--allow UID]... [--no-drop] "
                    "[--daemon LOG]\n");
            return 2;
        }
    }
    signal(SIGPIPE, SIG_IGN);
    if (strlen(name) > sizeof(sa.sun_path) - 2)
        return 2;
    memcpy(sa.sun_path + 1, name, strlen(name));
    salen = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + strlen(name));
    lfd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (lfd < 0 || bind(lfd, (struct sockaddr *)&sa, salen) < 0 || listen(lfd, 2) < 0) {
        perror("tb-camera: listen");
        return 1;
    }
    if (daemon_log && daemonize(daemon_log, lfd) < 0) {
        fprintf(stderr, "tb-camera: cannot write log %s\n", daemon_log);
        return 1;
    }
    /* first line, read by linux-root.sh to recognise the running daemon */
    fprintf(stderr, "tb-camera pid %d\n", (int)getpid());
    if (drop && getuid() == 0 && drop_to_media() < 0) {
        fprintf(stderr, "tb-camera: cannot drop to the media user\n");
        return 1;
    }
    supervise();
    lib = dlopen("libhardware.so", RTLD_NOW);
    get_module = lib ? (get_module_fn)dlsym(lib, "hw_get_module") : NULL;
    if (!get_module || get_module(CAMERA_HARDWARE_MODULE_ID, &module) != 0 || !module) {
        fprintf(stderr, "tb-camera: no camera module\n");
        return 1;
    }
    if (pipe2(wake, O_CLOEXEC | O_NONBLOCK) < 0)
        return 1;
    fprintf(stderr, "tb-camera: %d cameras\n",
            ((const camera_module_t *)module)->get_number_of_cameras());

    for (;;) {
        struct pollfd p[3] = { { .fd = lfd, .events = POLLIN },
                               { .fd = client, .events = POLLIN },
                               { .fd = wake[0], .events = POLLIN } };
        char buf[64];

        if (poll(p, 3, -1) < 0)
            continue;
        if (p[0].revents & POLLIN) {
            int conn = accept(lfd, NULL, NULL);     /* accept4: only from Android 5 */
            struct ucred cred;
            socklen_t len = sizeof(cred);
            int ok = 0;

            if (conn >= 0)
                fcntl(conn, F_SETFD, FD_CLOEXEC);
            if (conn >= 0 && getsockopt(conn, SOL_SOCKET, SO_PEERCRED, &cred, &len) == 0)
                for (int i = 0; i < nallow; i++)
                    ok |= cred.uid == allow[i];
            if (conn >= 0 && (!ok || client >= 0)) {
                close(conn);           /* not allowed, or the camera is in use */
            } else if (conn >= 0) {
                client = conn;
                send_info();
            }
        }
        if (client >= 0 && (p[1].revents & (POLLIN | POLLHUP | POLLERR))) {
            static char line[128];
            static size_t have;
            ssize_t n = recv(client, line + have, sizeof(line) - 1 - have, 0);

            if (n <= 0) {
                close(client);
                client = -1;
                have = 0;
            } else {
                char *nl;

                have += (size_t)n;
                line[have] = '\0';
                while ((nl = strchr(line, '\n')) != NULL) {
                    *nl = '\0';
                    command(line);
                    memmove(line, nl + 1, have - (size_t)(nl + 1 - line) + 1);
                    have -= (size_t)(nl + 1 - line);
                }
                if (have >= sizeof(line) - 1)
                    have = 0;           /* a line that long is not a command */
            }
        }
        if (p[2].revents & POLLIN)
            while (read(wake[0], buf, sizeof(buf)) > 0)
                ;
        if (client < 0 && cam)
            stop_camera();             /* the app went away */
        deliver();
    }
}
