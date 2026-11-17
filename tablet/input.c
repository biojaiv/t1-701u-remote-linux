/*
 * Touch and key input from Linux event devices.
 *
 * Handles both multi-touch protocols: B (ABS_MT_SLOT, a tracking id of -1
 * lifts the finger) and A (every contact ends with SYN_MT_REPORT, a frame
 * without contacts lifts all fingers). The protocol is detected from the
 * events themselves. Reports are only made on SYN_REPORT.
 */
#include "tabletd.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/input-event-codes.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

struct raw_input_event {   /* layout used by 32-bit kernel 3.10 */
    uint32_t sec, usec;
    uint16_t type, code;
    int32_t value;
};
_Static_assert(sizeof(struct raw_input_event) == 16, "input event size");

#define HAS_X 1
#define HAS_Y 2
#define HAS_ID 4

static int clamp(int v, int max) { return v < 0 ? 0 : v > max ? max : v; }

int input_open(struct input_dev *d, const char *path)
{
    struct stat st;

    memset(d, 0, sizeof(*d));
    d->fd = open(path, O_RDONLY | O_NONBLOCK);
    if (d->fd >= 0 && fstat(d->fd, &st) == 0 && S_ISFIFO(st.st_mode)) {
        /* a FIFO opened read-write never reports end of file (tests) */
        close(d->fd);
        d->fd = open(path, O_RDWR | O_NONBLOCK);
    }
    return d->fd < 0 ? -1 : 0;
}

/* protocol B: move the fields collected in d->cur into the current slot */
static void commit_slot(struct input_dev *d)
{
    int s = d->cur_slot;

    if (d->cur_has_pos && s >= 0 && s < MAX_SLOTS) {
        struct contact *n = &d->next[s];

        if (d->cur_has_pos & HAS_X)
            n->x = d->cur.x;
        if (d->cur_has_pos & HAS_Y)
            n->y = d->cur.y;
        if (d->cur_has_pos & HAS_ID) {
            n->used = d->cur.id >= 0;
            n->id = d->cur.id;
        } else {
            n->used = 1;   /* position without tracking id: finger is down */
        }
        d->touched[s] = 1;
    }
    d->cur_has_pos = 0;
}

static void report_b(struct input_dev *d, touch_fn on_touch, void *ctx)
{
    for (int s = 0; s < MAX_SLOTS; s++) {
        struct contact *o = &d->slot[s], *n = &d->next[s];

        if (!d->touched[s])
            continue;
        d->touched[s] = 0;
        if (o->used && (!n->used || n->id != o->id))
            on_touch(ctx, s, TOUCH_UP, o->x, o->y);
        if (n->used && (!o->used || n->id != o->id))
            on_touch(ctx, s, TOUCH_DOWN, n->x, n->y);
        else if (n->used && (n->x != o->x || n->y != o->y))
            on_touch(ctx, s, TOUCH_MOVE, n->x, n->y);
        *o = *n;
    }
}

static void report_a(struct input_dev *d, touch_fn on_touch, void *ctx)
{
    int target[MAX_SLOTS];   /* slot of each contact in this frame */
    int kept[MAX_SLOTS] = {0};

    for (int i = 0; i < d->nframe; i++) {
        struct contact *c = &d->frame[i];

        target[i] = -1;
        if (c->id < 0) {
            target[i] = i;   /* no tracking ids: order in the frame */
        } else {
            for (int s = 0; s < MAX_SLOTS; s++)
                if (d->slot[s].used && d->slot[s].id == c->id)
                    target[i] = s;
        }
        if (target[i] >= 0)
            kept[target[i]] = 1;
    }
    for (int i = 0; i < d->nframe; i++) {   /* new fingers get a free slot */
        if (target[i] >= 0)
            continue;
        for (int s = 0; s < MAX_SLOTS; s++) {
            if (!d->slot[s].used && !kept[s]) {
                target[i] = s;
                kept[s] = 1;
                break;
            }
        }
    }
    for (int s = 0; s < MAX_SLOTS; s++) {
        if (d->slot[s].used && !kept[s]) {
            on_touch(ctx, s, TOUCH_UP, d->slot[s].x, d->slot[s].y);
            d->slot[s].used = 0;
        }
    }
    for (int i = 0; i < d->nframe; i++) {
        struct contact *c = &d->frame[i], *o;

        if (target[i] < 0)
            continue;   /* more fingers than slots */
        o = &d->slot[target[i]];
        if (!o->used || o->id != c->id) {
            if (o->used)
                on_touch(ctx, target[i], TOUCH_UP, o->x, o->y);
            on_touch(ctx, target[i], TOUCH_DOWN, c->x, c->y);
        } else if (o->x != c->x || o->y != c->y) {
            on_touch(ctx, target[i], TOUCH_MOVE, c->x, c->y);
        }
        *o = *c;
        o->used = 1;
    }
    d->nframe = 0;
}

static void handle(struct input_dev *d, const struct raw_input_event *e,
                   int max_x, int max_y, touch_fn on_touch, key_fn on_key,
                   void *ctx)
{
    if (e->type == EV_KEY) {
        if (e->code != BTN_TOUCH && (e->value == 0 || e->value == 1))
            on_key(ctx, e->code, e->value);
    } else if (e->type == EV_ABS) {
        switch (e->code) {
        case ABS_MT_SLOT:
            commit_slot(d);
            d->cur_slot = e->value;
            break;
        case ABS_MT_POSITION_X:
            d->cur.x = clamp(e->value, max_x);
            d->cur_has_pos |= HAS_X;
            break;
        case ABS_MT_POSITION_Y:
            d->cur.y = clamp(e->value, max_y);
            d->cur_has_pos |= HAS_Y;
            break;
        case ABS_MT_TRACKING_ID:
            d->cur.id = e->value;
            d->cur_has_pos |= HAS_ID;
            break;
        }
    } else if (e->type == EV_SYN && e->code == SYN_MT_REPORT) {
        d->mode_a = 1;
        if ((d->cur_has_pos & (HAS_X | HAS_Y)) && d->nframe < MAX_SLOTS) {
            if (!(d->cur_has_pos & HAS_ID))
                d->cur.id = -1;
            d->frame[d->nframe++] = d->cur;
        }
        d->cur_has_pos = 0;
    } else if (e->type == EV_SYN && e->code == SYN_REPORT) {
        if (d->mode_a) {
            d->cur_has_pos = 0;
            report_a(d, on_touch, ctx);
        } else {
            commit_slot(d);
            report_b(d, on_touch, ctx);
        }
    }
}

int input_read(struct input_dev *d, int max_x, int max_y,
               touch_fn on_touch, key_fn on_key, void *ctx)
{
    for (;;) {
        ssize_t n = read(d->fd, d->buf + d->have, sizeof(d->buf) - d->have);
        size_t used = 0;

        if (n < 0)
            return errno == EAGAIN || errno == EINTR ? 0 : -1;
        if (n == 0)
            return -1;
        d->have += (size_t)n;
        while (d->have - used >= sizeof(struct raw_input_event)) {
            struct raw_input_event e;

            memcpy(&e, d->buf + used, sizeof(e));
            used += sizeof(e);
            handle(d, &e, max_x, max_y, on_touch, on_key, ctx);
        }
        memmove(d->buf, d->buf + used, d->have - used);
        d->have -= used;
    }
}
