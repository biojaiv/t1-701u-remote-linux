/*
 * tabletd - shows what the laptop sends and reports touch and keys back.
 *
 * One client at a time; a new connection replaces the old one. Every
 * malformed message closes the connection, tabletd itself keeps running.
 */
#define _GNU_SOURCE        /* struct ucred */
#include "tabletd.h"

#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <limits.h>
#include <linux/input-event-codes.h>
#include <arpa/inet.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/wait.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define MAX_INPUTS 4
#define CLIENT_TIMEOUT_MS 5000
#define STATUS_PERIOD_MS 30000
#define STATUS_PAGE_PERIOD_MS 5000
#define PENDING_TIMEOUT_MS 5000   /* to prove the key after connecting */
#define CONNECT_RETRY_MS 5000     /* --secure-connect: next attempt after a failure */
#define CONNECT_TIMEOUT_MS 5000   /* --secure-connect: TCP connection must stand by then */
#define POWER_HOLD_MS 3000        /* power key held this long: switch off */
#define EXIT_POWEROFF 42           /* tells the launcher to shut the tablet down */
#define FRAMES_PER_WAKEUP 8

static struct fb fb;
static struct input_dev inputs[MAX_INPUTS];
static int ninputs;
static int listen_fd = -1;
static int client = -1;
static int client_secure;          /* the client came in over the encrypted listener */
static struct sec client_sec;

/* direct Wi-Fi listener: encrypted connections only (see secure.c) */
static int secure_fd = -1;
/* --secure-connect: the tablet calls the laptop (over the Internet, through
   the laptop's router); the laptop then speaks first, exactly as if it had
   connected here (hello, proof of the key) */
static int connect_fd = -1, have_connect;
static struct sockaddr_storage connect_addr;   /* IPv4 or IPv6 */
static socklen_t connect_addr_len;
static long long connect_since_ms, next_connect_ms;
static uint8_t sec_key[SEC_KEY_LEN];
/* an encrypted connection that has not yet proved the shared key; it does
   not disturb the active client until it has */
static int pend_fd = -1, pend_stage;
static uint8_t pend_buf[4 + SEC_FIRST_MAX];
static size_t pend_have;
static long long pend_since_ms;
static struct sec pend_sec;
/* record layer of the active encrypted client */
static uint8_t rec_hdr[4];
static size_t rec_hdr_have;
static uint32_t rec_len, rec_have;
static uint8_t rec_body[SEC_MAX_PLAIN + SEC_MAC_LEN];
static uint8_t plain[SEC_MAX_PLAIN];
static int pan_on_present;
static int backlight_fd = -1;   /* opened before privileges are dropped */
static int confirm_fd = -1;     /* boot counter of the launcher, reset on first picture */
static int audio_fd = -1;       /* pipe to tb-audio (--audio), -1: no sound */
static pid_t audio_pid = -1;
static int home_requested;      /* MSG_HOME: end, tb-home shows its start screen */

/* display state (steps P5.13 and P5.14) */
static int display_on = 1, brightness = 200;
static long long idle_dim_ms = 300000;   /* without laptop and touch; 0 = never */
static long long power_down_ms = -1, last_activity_ms;
static const char *battery_dir;

/* receive state of the current client */
static uint8_t hdr[HDR_LEN];
static size_t hdr_have;
static uint32_t pay_len, pay_have;
static uint8_t payload[MAX_PAYLOAD];
static uint8_t *unpacked;   /* one screen of RGB565, for compressed rects */
static long long last_rx_ms, last_status_ms;

/* set by TIME; shown on the status page */
static long long time_base_s;
static int32_t tz_offset_s;
static int time_set;

/* status page: shown while no laptop is connected (--status-page) */
static int status_page, rotation = ROT_NONE;
static long long page_minute = -2;
static long page_cap = -2;
static int page_charging = -1;
static char page_addr[INET_ADDRSTRLEN];
static const char *page_iface = "wlan0";
static const char *tz_path;           /* where the laptop's time zone is kept */
static int power_ignore;              /* under tb-home the power key is not ours */

static long long now_ms(void)
{
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void draw_status_page(int force);

static void drop_client(const char *why)
{
    next_connect_ms = 0;     /* after a session, call the laptop again soon */
    if (client >= 0) {
        fprintf(stderr, "tabletd: client closed: %s\n", why);
        close(client);
        client = -1;
        if (client_secure)
            sec_wipe(&client_sec);
        client_secure = 0;
        draw_status_page(1);
    }
}

static void send_msg(uint8_t type, const void *p, uint32_t len)
{
    uint8_t frame[HDR_LEN + 64], record[4 + HDR_LEN + 64 + SEC_MAC_LEN];
    int rc;

    if (client < 0 || len > sizeof(frame) - HDR_LEN)
        return;   /* the tablet only ever sends small messages */
    frame[0] = type;
    wr32(frame + 1, len);
    if (len)
        memcpy(frame + HDR_LEN, p, len);
    if (client_secure)
        rc = send_all(client, record, sec_seal(&client_sec, record, frame, HDR_LEN + len));
    else
        rc = send_all(client, frame, HDR_LEN + len);
    if (rc < 0)
        drop_client("send failed");
}

static void send_status(void)
{
    uint8_t m[4];
    long cap, temp;
    int charging;

    last_status_ms = now_ms();
    if (ui_battery(battery_dir, &cap, &charging, &temp) < 0)
        return;
    m[0] = (uint8_t)cap;
    m[1] = (uint8_t)charging;
    wr16(m + 2, (uint16_t)(int16_t)temp);
    send_msg(MSG_STATUS, m, sizeof(m));
}

/* waiting screen while no laptop is connected: time, date, battery, Wi-Fi
   address; redrawn only when something changed */
static void draw_status_page(int force)
{
    struct canvas c = { unpacked, fb.w, fb.h, rotation };
    char clock_text[8] = "--:--", date_text[48] = "Time comes from the laptop";
    char addr[INET_ADDRSTRLEN];
    long cap = -1, temp;
    long long minute = -1;
    int charging = 0, cw, ch, x;
    int32_t saved_offset;

    if (!status_page || client >= 0)
        return;
    if (time_set)
        minute = ui_time_texts(time_base_s + now_ms() / 1000 + tz_offset_s, clock_text,
                               sizeof(clock_text), date_text, sizeof(date_text));
    else if (ui_tz_load(tz_path, &saved_offset) == 0)
        /* the tablet's own clock, with the time zone the laptop told us last time */
        minute = ui_time_texts((long long)time(NULL) + saved_offset, clock_text,
                               sizeof(clock_text), date_text, sizeof(date_text));
    if (ui_battery(battery_dir, &cap, &charging, &temp) < 0)
        cap = -1;
    ui_address(page_iface, addr, sizeof(addr));
    if (!force && minute == page_minute && cap == page_cap && charging == page_charging &&
        strcmp(addr, page_addr) == 0)
        return;
    page_minute = minute;
    page_cap = cap;
    page_charging = charging;
    snprintf(page_addr, sizeof(page_addr), "%s", addr);

    cw = (int)canvas_width(&c);
    ch = (int)canvas_height(&c);
    canvas_gradient(&c, UI_BG_TOP, UI_BG_BOTTOM);
    ui_header(&c, addr, cap, charging);
    canvas_text(&c, &face_clock, (cw - text_width(&face_clock, clock_text)) / 2, ch / 2 + 40,
                UI_TEXT_MAIN, clock_text);
    canvas_text(&c, &face_text, (cw - text_width(&face_text, date_text)) / 2, ch / 2 + 100,
                UI_TEXT_SUB, date_text);
    x = (cw - text_width(&face_text, "•") - text_width(&face_text, " Ready – waiting for the laptop")) / 2;
    x = canvas_text(&c, &face_text, x, ch - 48, UI_AMBER, "•");
    canvas_text(&c, &face_text, x, ch - 48, UI_TEXT_DIM, " Ready – waiting for the laptop");
    fb_rect565(&fb, 0, 0, fb.w, fb.h, unpacked);
}

static void write_backlight(unsigned value)
{
    char text[8];
    int n = snprintf(text, sizeof(text), "%u\n", value);

    if (backlight_fd < 0)
        return;
    /* sysfs ignores the truncate; a plain file (tests) needs it */
    if (ftruncate(backlight_fd, 0) < 0)
        errno = 0;
    if (pwrite(backlight_fd, text, (size_t)n, 0) != n)
        fprintf(stderr, "tabletd: cannot set backlight\n");
}

static void set_display(int on)
{
    display_on = on;
    write_backlight(on ? (unsigned)brightness : 0);
}

/* touch, key or a connecting laptop: the display comes back on */
static void activity(void)
{
    last_activity_ms = now_ms();
    if (!display_on)
        set_display(1);
}

static void on_touch(void *ctx, int slot, int state, int x, int y)
{
    uint8_t m[6];

    (void)ctx;
    activity();
    m[0] = (uint8_t)slot;
    m[1] = (uint8_t)state;
    wr16(m + 2, (uint16_t)x);
    wr16(m + 4, (uint16_t)y);
    send_msg(MSG_TOUCH, m, sizeof(m));
}

static void on_key(void *ctx, int code, int state)
{
    uint8_t m[3];

    (void)ctx;
    if (code == KEY_POWER && power_ignore)
        return;   /* tb-home handles it */
    if (code == KEY_POWER) {
        /* handled here, never sent to the laptop: a short press switches the
           display, holding it switches the tablet off (see the main loop) */
        if (state == 1) {
            power_down_ms = now_ms();
        } else if (power_down_ms >= 0) {
            power_down_ms = -1;
            last_activity_ms = now_ms();
            set_display(!display_on);
        }
        return;
    }
    activity();
    wr16(m, (uint16_t)code);
    m[2] = (uint8_t)state;
    send_msg(MSG_KEY, m, sizeof(m));
}

/* --audio: starts the player (tb-audio) while tabletd is still root, so it
   can open the sound device; it gives up root itself (the same --drop). It
   gets the sound through a pipe and reports "ready" on fd 3; only then is
   INFO_AUDIO announced. Without it, everything else works as before. */
static void start_audio(const char *program, const char *drop_spec)
{
    int data[2], ready[2];
    struct pollfd p;
    char word[8] = "";

    if (pipe(data) < 0)
        return;
    if (pipe(ready) < 0) {
        close(data[0]);
        close(data[1]);
        return;
    }
    audio_pid = fork();
    if (audio_pid == 0) {
        char *argv[] = { (char *)program, drop_spec ? "--drop" : NULL, (char *)drop_spec, NULL };

        dup2(data[0], 0);
        dup2(ready[1], 3);
        for (int fd = 4; fd < 1024; fd++)       /* nothing of tabletd's: devices, sockets, key */
            close(fd);
        execv(program, argv);
        _exit(127);
    }
    close(data[0]);
    close(ready[1]);
    if (audio_pid < 0) {
        close(data[1]);
        close(ready[0]);
        return;
    }
    p = (struct pollfd){ .fd = ready[0], .events = POLLIN };
    if (poll(&p, 1, 3000) == 1 && read(ready[0], word, sizeof(word) - 1) > 0 &&
        strncmp(word, "ready", 5) == 0) {
        fcntl(data[1], F_SETFL, fcntl(data[1], F_GETFL) | O_NONBLOCK);
        fcntl(data[1], F_SETFD, FD_CLOEXEC);
        audio_fd = data[1];
        fprintf(stderr, "tabletd: sound through %s\n", program);
    } else {
        fprintf(stderr, "tabletd: %s did not get ready: no sound\n", program);
        close(data[1]);
        kill(audio_pid, SIGKILL);
        waitpid(audio_pid, NULL, 0);
        audio_pid = -1;
    }
    close(ready[0]);
}

static void stop_audio(const char *why)
{
    if (audio_fd < 0)
        return;
    fprintf(stderr, "tabletd: sound off: %s\n", why);
    close(audio_fd);
    audio_fd = -1;
}

/* MSG_AUDIO: whole frames of S16LE at 24 or 48 kHz, mono or stereo; tb-audio
   always gets 48 kHz stereo (each 24 kHz frame twice, mono on both sides).
   The pipe is non-blocking and written in pieces of at most PIPE_BUF, which
   the kernel writes whole or not at all, so frames never get split. When it
   is full (tb-audio behind), the rest of this piece is dropped. */
static int play_audio(void)
{
    static uint8_t out[48000 / 10 * 4];
    uint32_t rate = rd32(payload);
    unsigned channels = payload[4], frame = channels * 2;
    size_t samples = pay_len - AUDIO_HDR, frames, n = 0;
    unsigned repeat;
    const uint8_t *in = payload + AUDIO_HDR;

    if ((rate != 48000 && rate != 24000) || (channels != 1 && channels != 2) ||
        payload[5] != 0 || samples % frame != 0)
        return -1;
    frames = samples / frame;
    repeat = rate == 24000 ? 2 : 1;
    if (frames * repeat * 4 > sizeof(out))
        return -1;
    if (audio_fd < 0)
        return 0;                                /* no player: accepted, not played */
    for (size_t i = 0; i < frames; i++) {
        const uint8_t *f = in + i * frame;
        const uint8_t *right = channels == 2 ? f + 2 : f;

        for (unsigned r = 0; r < repeat; r++) {
            out[n++] = f[0];
            out[n++] = f[1];
            out[n++] = right[0];
            out[n++] = right[1];
        }
    }
    for (size_t off = 0; off < n;) {
        size_t piece = n - off < PIPE_BUF ? n - off : PIPE_BUF;
        ssize_t w = write(audio_fd, out + off, piece);

        if (w < 0 && errno == EINTR)
            continue;
        if (w < 0 && errno == EAGAIN)
            break;                               /* player behind: drop the rest */
        if (w < 0) {
            stop_audio(errno == EPIPE ? "tb-audio ended" : strerror(errno));
            break;
        }
        off += (size_t)w;
    }
    return 0;
}

/* returns 0 if the frame was valid, -1 if the client must be dropped */
static int handle_frame(uint8_t type)
{
    switch (type) {
    case MSG_RECT: {
        uint32_t x = rd16(payload), y = rd16(payload + 2);
        uint32_t w = rd16(payload + 4), h = rd16(payload + 6);
        uint8_t fmt = payload[8];

        if (w == 0 || h == 0 || x + w > fb.w || y + h > fb.h)
            return -1;
        if (fmt == FMT_RGB565) {
            if (pay_len != RECT_HDR + w * h * 2)
                return -1;
            return fb_rect565(&fb, x, y, w, h, payload + RECT_HDR);
        }
        if (fmt != FMT_RGB565_LZ4 ||
            lz4_decode(payload + RECT_HDR, pay_len - RECT_HDR, unpacked,
                       (size_t)w * h * 2) < 0)
            return -1;
        return fb_rect565(&fb, x, y, w, h, unpacked);
    }
    case MSG_COPY: {
        uint32_t sx = rd16(payload), sy = rd16(payload + 2);
        uint32_t w = rd16(payload + 4), h = rd16(payload + 6);
        uint32_t dx = rd16(payload + 8), dy = rd16(payload + 10);

        if (pay_len != COPY_LEN || w == 0 || h == 0 || sx + w > fb.w || sy + h > fb.h ||
            dx + w > fb.w || dy + h > fb.h)
            return -1;
        return fb_copy(&fb, sx, sy, w, h, dx, dy);
    }
    case MSG_PRESENT:
        if (pan_on_present)
            fb_pan(&fb);
        if (confirm_fd >= 0) {
            /* a laptop is connected and showed a picture: this boot worked */
            if (ftruncate(confirm_fd, 0) < 0 || pwrite(confirm_fd, "0\n", 2, 0) != 2)
                fprintf(stderr, "tabletd: cannot reset the boot counter\n");
            close(confirm_fd);
            confirm_fd = -1;
        }
        send_msg(MSG_ACK, payload, 4);
        return 0;
    case MSG_BACKLIGHT:
        if (payload[0])
            brightness = payload[0];   /* remembered for switching back on */
        display_on = payload[0] != 0;
        write_backlight(payload[0]);
        return 0;
    case MSG_TIME: {
        uint64_t secs = (uint64_t)rd32(payload) | (uint64_t)rd32(payload + 4) << 32;

        time_base_s = (long long)secs - now_ms() / 1000;
        tz_offset_s = (int32_t)rd32(payload + 8);
        time_set = 1;
        if (tz_path)
            ui_tz_save(tz_path, tz_offset_s);   /* for tb-home and the next start */
        return 0;
    }
    case MSG_PING:
        send_msg(MSG_PONG, payload, 4);
        return 0;
    case MSG_AUTH:
        return 0;   /* pairing is added in phase 6; accepted and ignored */
    case MSG_AUDIO:
        return play_audio();
    case MSG_HOME:
        /* the "Back" button of the laptop's menus: like holding both volume
           keys, the remote control ends and tb-home shows its start screen */
        home_requested = 1;
        return 0;
    }
    return -1;
}

/* plain connection (adb): read straight into the frame buffers */
static void read_client_plain(void)
{
    for (int frames = 0; client >= 0 && frames < FRAMES_PER_WAKEUP;) {
        ssize_t n;

        if (hdr_have < HDR_LEN) {
            n = recv(client, hdr + hdr_have, HDR_LEN - hdr_have, MSG_DONTWAIT);
        } else {
            n = recv(client, payload + pay_have, pay_len - pay_have, MSG_DONTWAIT);
        }
        if (n < 0 && (errno == EAGAIN || errno == EINTR))
            return;
        if (n <= 0) {
            drop_client(n == 0 ? "peer closed" : "read error");
            return;
        }
        last_rx_ms = now_ms();
        if (hdr_have < HDR_LEN) {
            hdr_have += (size_t)n;
            if (hdr_have < HDR_LEN)
                continue;
            pay_len = rd32(hdr + 1);
            pay_have = 0;
            if (proto_check_len(hdr[0], pay_len) < 0) {
                drop_client("bad type or length");
                return;
            }
        } else {
            pay_have += (uint32_t)n;
        }
        if (pay_have == pay_len) {
            hdr_have = 0;
            frames++;
            if (handle_frame(hdr[0]) < 0)
                drop_client("bad message");
        }
    }
}

/* decrypted bytes of the frame stream; 0, or -1 if the client must go */
static int feed_plain(const uint8_t *d, size_t n)
{
    while (n > 0 && client >= 0) {
        if (hdr_have < HDR_LEN) {
            size_t k = HDR_LEN - hdr_have < n ? HDR_LEN - hdr_have : n;

            memcpy(hdr + hdr_have, d, k);
            hdr_have += k;
            d += k;
            n -= k;
            if (hdr_have < HDR_LEN)
                break;
            pay_len = rd32(hdr + 1);
            pay_have = 0;
            if (proto_check_len(hdr[0], pay_len) < 0)
                return -1;
        } else {
            size_t k = pay_len - pay_have < n ? pay_len - pay_have : n;

            memcpy(payload + pay_have, d, k);
            pay_have += (uint32_t)k;
            d += k;
            n -= k;
        }
        if (pay_have == pay_len) {
            hdr_have = 0;
            if (handle_frame(hdr[0]) < 0)
                return -1;
        }
    }
    return 0;
}

/* encrypted connection: records -> plaintext -> frames */
static void read_client_secure(void)
{
    for (int records = 0; client >= 0 && records < FRAMES_PER_WAKEUP * 4;) {
        ssize_t n;
        long len;

        if (rec_hdr_have < 4)
            n = recv(client, rec_hdr + rec_hdr_have, 4 - rec_hdr_have, MSG_DONTWAIT);
        else
            n = recv(client, rec_body + rec_have, rec_len - rec_have, MSG_DONTWAIT);
        if (n < 0 && (errno == EAGAIN || errno == EINTR))
            return;
        if (n <= 0) {
            drop_client(n == 0 ? "peer closed" : "read error");
            return;
        }
        last_rx_ms = now_ms();
        if (rec_hdr_have < 4) {
            rec_hdr_have += (size_t)n;
            if (rec_hdr_have < 4)
                continue;
            rec_len = rd32(rec_hdr);
            rec_have = 0;
            if (rec_len <= SEC_MAC_LEN || rec_len > SEC_MAX_PLAIN + SEC_MAC_LEN) {
                drop_client("bad record length");
                return;
            }
            continue;
        }
        rec_have += (uint32_t)n;
        if (rec_have < rec_len)
            continue;
        rec_hdr_have = 0;
        records++;
        len = sec_open(&client_sec, plain, rec_hdr, rec_body, rec_len);
        if (len < 0) {
            drop_client("record failed authentication");
            return;
        }
        if (feed_plain(plain, (size_t)len) < 0)
            drop_client("bad message");
    }
}

static void read_client(void)
{
    if (client_secure)
        read_client_secure();
    else
        read_client_plain();
}

/* a connection becomes the client: greet it with INFO and STATUS.
   keys: the session keys of an encrypted connection, NULL for a plain one */
static void start_session(int fd, const struct sec *keys)
{
    int secure = keys != NULL;
    struct timeval tv = { .tv_sec = 2 };
    uint8_t info[7 + NONCE_LEN];
    int rnd;

    drop_client("replaced by new connection");
    client = fd;
    client_secure = secure;
    if (keys)
        client_sec = *keys;   /* before anything is sent */
    hdr_have = 0;
    rec_hdr_have = 0;
    last_rx_ms = now_ms();
    setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    activity();

    wr16(info, PROTO_VERSION);
    wr16(info + 2, (uint16_t)fb.w);
    wr16(info + 4, (uint16_t)fb.h);
    info[6] = (secure ? INFO_ENCRYPTED : 0) | INFO_COPY | INFO_HOME |
              (audio_fd >= 0 ? INFO_AUDIO : 0);
    memset(info + 7, 0, NONCE_LEN);
    rnd = open("/dev/urandom", O_RDONLY);
    if (rnd >= 0) {
        if (read(rnd, info + 7, NONCE_LEN) != NONCE_LEN)
            memset(info + 7, 0, NONCE_LEN);
        close(rnd);
    }
    send_msg(MSG_INFO, info, sizeof(info));
    send_status();
}

/* uids that may use the local socket. An abstract socket is open to every
   process on the device (Android apps, the Linux session); a caller there
   would replace the laptop's session, get every touch and draw on the
   screen. Legitimate callers: adbd (shell, 2000) for the cable link, root,
   and tabletd's own uid (tests; 2900 after --drop). --local-uids replaces it. */
static unsigned local_uids[8];
static int n_local_uids = -1;          /* -1: the default list */

static int local_peer_allowed(int fd)
{
    struct sockaddr_storage own;
    socklen_t own_len = sizeof(own);
    struct ucred cred;
    socklen_t len = sizeof(cred);

    if (getsockname(fd, (struct sockaddr *)&own, &own_len) == 0 && own.ss_family != AF_UNIX)
        return 1;                      /* tcp: bound to 127.0.0.1, for tests only */
    if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cred, &len) < 0)
        return 0;
    if (n_local_uids < 0) {
        if (cred.uid == 0 || cred.uid == 2000 || cred.uid == getuid())
            return 1;
    } else {
        for (int i = 0; i < n_local_uids; i++)
            if (cred.uid == local_uids[i])
                return 1;
    }
    fprintf(stderr, "tabletd: local connection from uid %u refused\n", (unsigned)cred.uid);
    return 0;
}

static void accept_client(void)
{
    int fd = accept(listen_fd, NULL, NULL);

    if (fd < 0)
        return;
    if (!local_peer_allowed(fd)) {
        close(fd);                     /* the current session stays */
        return;
    }
    start_session(fd, NULL);
}

static void drop_pending(const char *why)
{
    if (pend_fd >= 0) {
        fprintf(stderr, "tabletd: encrypted connection refused: %s\n", why);
        close(pend_fd);
        pend_fd = -1;
        sec_wipe(&pend_sec);
    }
}

static void accept_secure(void)
{
    struct timeval tv = { .tv_sec = 2 };
    int fd = accept(secure_fd, NULL, NULL);

    if (fd < 0)
        return;
    drop_pending("replaced by a newer attempt");
    pend_fd = fd;
    pend_stage = 0;
    pend_have = 0;
    pend_since_ms = now_ms();
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
}

/* --secure-connect: start a TCP connection to the laptop without waiting */
static void start_connect(void)
{
    int fd = socket(connect_addr.ss_family, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);

    next_connect_ms = now_ms() + CONNECT_RETRY_MS;
    if (fd < 0)
        return;
    if (connect(fd, (struct sockaddr *)&connect_addr, connect_addr_len) < 0 &&
        errno != EINPROGRESS) {
        close(fd);
        return;
    }
    connect_fd = fd;
    connect_since_ms = now_ms();
}

/* the connection to the laptop stands (or failed): it becomes the pending one */
static void finish_connect(void)
{
    struct timeval tv = { .tv_sec = 2 };
    int err = 0, fd = connect_fd;
    socklen_t len = sizeof(err);

    connect_fd = -1;
    if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len) < 0 || err != 0) {
        close(fd);
        return;
    }
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) & ~O_NONBLOCK);
    drop_pending("replaced by the connection to the laptop");
    pend_fd = fd;
    pend_stage = 0;
    pend_have = 0;
    pend_since_ms = now_ms();
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    fprintf(stderr, "tabletd: connected to the laptop, waiting for its handshake\n");
}

/* handshake, then the first record: only a valid one makes it the client */
static void read_pending(void)
{
    size_t want;
    ssize_t n;
    long len;

    if (pend_stage == 0)
        want = SEC_HELLO_LEN;
    else
        want = pend_have < 4 ? 4 : 4 + rd32(pend_buf);
    n = recv(pend_fd, pend_buf + pend_have, want - pend_have, MSG_DONTWAIT);
    if (n < 0 && (errno == EAGAIN || errno == EINTR))
        return;
    if (n <= 0) {
        drop_pending(n == 0 ? "closed before the handshake ended" : "read error");
        return;
    }
    pend_have += (size_t)n;
    if (pend_have < want)
        return;

    if (pend_stage == 0) {
        uint8_t reply[SEC_HELLO_LEN];

        if (sec_server_handshake(&pend_sec, sec_key, pend_buf, reply) < 0 ||
            send_all(pend_fd, reply, sizeof(reply)) < 0) {
            drop_pending("bad hello");
            return;
        }
        pend_stage = 1;
        pend_have = 0;
        return;
    }
    if (pend_have == 4) {
        uint32_t body = rd32(pend_buf);

        if (body <= SEC_MAC_LEN || body > SEC_FIRST_MAX)
            drop_pending("bad first record");
        return;
    }
    len = sec_open(&pend_sec, plain, pend_buf, pend_buf + 4, pend_have - 4);
    if (len < 0) {
        drop_pending("wrong key");
        return;
    }
    /* proven: the new connection takes over */
    {
        int fd = pend_fd;

        pend_fd = -1;
        start_session(fd, &pend_sec);
        sec_wipe(&pend_sec);
    }
    if (feed_plain(plain, (size_t)len) < 0)
        drop_client("bad message");
}

static int open_tcp(int port, uint32_t addr)
{
    struct sockaddr_in sa = { .sin_family = AF_INET };
    int fd, one = 1;

    if (port <= 0 || port > 65535)
        return -1;
    sa.sin_port = htons((uint16_t)port);
    sa.sin_addr.s_addr = htonl(addr);
    fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return -1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    if (bind(fd, (struct sockaddr *)&sa, sizeof(sa)) < 0 || listen(fd, 1) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

/* spec: abstract:NAME | tcp:PORT (bound to 127.0.0.1) */
static int open_listener(const char *spec)
{
    int fd;

    if (strncmp(spec, "abstract:", 9) == 0) {
        struct sockaddr_un sa = { .sun_family = AF_UNIX };
        size_t n = strlen(spec + 9);

        if (n == 0 || n > sizeof(sa.sun_path) - 2)
            return -1;
        memcpy(sa.sun_path + 1, spec + 9, n);   /* sun_path[0] stays 0 */
        fd = socket(AF_UNIX, SOCK_STREAM, 0);
        if (fd < 0 || bind(fd, (struct sockaddr *)&sa,
                           (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + n)) < 0)
            return -1;
    } else if (strncmp(spec, "tcp:", 4) == 0) {
        return open_tcp(atoi(spec + 4), INADDR_LOOPBACK);
    } else {
        return -1;
    }
    return listen(fd, 1) < 0 ? -1 : fd;
}

/* spec: UID:GID as numbers; called after all devices and sockets are open */
static int drop_privileges(const char *spec)
{
    unsigned uid, gid;
    char tail;

    if (sscanf(spec, "%u:%u%c", &uid, &gid, &tail) != 2 || uid == 0 || gid == 0)
        return -1;
    if (setgroups(0, NULL) < 0 || setgid(gid) < 0 || setuid(uid) < 0)
        return -1;
    return setuid(0) == 0 ? -1 : 0;   /* must not be able to get root back */
}

/* detach from the starting shell; stderr goes to the log file */
static int daemonize(const char *log_path)
{
    int log = open(log_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    int null = open("/dev/null", O_RDWR);
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
    close(null);
    close(log);
    return 0;
}

static int parse_rotation(const char *word)
{
    if (strncmp(word, "counterclockwise", 16) == 0)
        return ROT_CCW;
    if (strncmp(word, "clockwise", 9) == 0)
        return ROT_CW;
    return strncmp(word, "none", 4) == 0 ? ROT_NONE : -1;
}

static void usage(void)
{
    fprintf(stderr,
            "usage: tabletd --fb PATH [--geom WxH] [--listen abstract:NAME|tcp:PORT]\n"
            "               [--local-uids UID,...]   (default: 0, 2000 and its own uid)\n"
            "               [--audio PROGRAM]   (sound player, gets --drop and the sound on stdin)\n"
            "               [--input PATH]... [--backlight FILE] [--battery DIR]\n"
            "               [--pan] [--drop UID:GID] [--daemon LOGFILE] [--confirm FILE]\n"
            "               [--status-page] [--rotation none|counterclockwise|clockwise]\n"
            "               [--rotation-file FILE] [--idle-dim SECONDS]\n"
            "               [--secure-listen [IPV4:]PORT --key FILE] [--iface NAME]\n"
            "               [--secure-connect IPV4:PORT | [IPV6]:PORT]   (with --key: call the laptop)\n"
            "               [--tz-file FILE] [--power-key handle|ignore]\n"
            "       tabletd --version\n");
}

int main(int argc, char **argv)
{
    const char *fb_path = NULL, *geom = NULL, *drop = NULL, *log_path = NULL;
    const char *audio_program = NULL;
    const char *listen_spec = "abstract:tabletd", *key_path = NULL, *secure_spec = NULL;
    const char *connect_spec = NULL;

    for (int i = 1; i < argc; i++) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;

        if (strcmp(a, "--version") == 0) {
            printf("tabletd %s proto %d\n", TABLETD_VERSION, PROTO_VERSION);
            return 0;
        } else if (strcmp(a, "--pan") == 0) {
            pan_on_present = 1;
        } else if (strcmp(a, "--status-page") == 0) {
            status_page = 1;
        } else if (!v) {
            usage();
            return 2;
        } else if (strcmp(a, "--fb") == 0) {
            fb_path = v, i++;
        } else if (strcmp(a, "--geom") == 0) {
            geom = v, i++;
        } else if (strcmp(a, "--listen") == 0) {
            listen_spec = v, i++;
        } else if (strcmp(a, "--local-uids") == 0 && v) {
            /* comma-separated list, replaces the default 0,2000,own uid */
            char *list = strdup(v), *save = NULL;

            n_local_uids = 0;
            for (char *tok = strtok_r(list, ",", &save); tok && n_local_uids < 8;
                 tok = strtok_r(NULL, ",", &save))
                local_uids[n_local_uids++] = (unsigned)strtoul(tok, NULL, 10);
            free(list);
            i++;
        } else if (strcmp(a, "--backlight") == 0) {
            backlight_fd = open(v, O_WRONLY);
            if (backlight_fd < 0) {
                fprintf(stderr, "tabletd: cannot open backlight %s\n", v);
                return 1;
            }
            i++;
        } else if (strcmp(a, "--battery") == 0) {
            battery_dir = v, i++;
        } else if (strcmp(a, "--iface") == 0) {
            page_iface = v, i++;
        } else if (strcmp(a, "--tz-file") == 0) {
            tz_path = v, i++;
        } else if (strcmp(a, "--power-key") == 0) {
            if (strcmp(v, "ignore") != 0 && strcmp(v, "handle") != 0) {
                usage();
                return 2;
            }
            power_ignore = strcmp(v, "ignore") == 0;
            i++;
        } else if (strcmp(a, "--idle-dim") == 0) {
            idle_dim_ms = atoll(v) * 1000;
            i++;
        } else if (strcmp(a, "--rotation") == 0) {
            rotation = parse_rotation(v);
            if (rotation < 0) {
                usage();
                return 2;
            }
            i++;
        } else if (strcmp(a, "--rotation-file") == 0) {
            /* a missing or unreadable file keeps the current setting */
            char word[32] = "";
            FILE *f = fopen(v, "r");

            if (f) {
                if (fgets(word, sizeof(word), f) && parse_rotation(word) >= 0)
                    rotation = parse_rotation(word);
                fclose(f);
            }
            i++;
        } else if (strcmp(a, "--confirm") == 0) {
            confirm_fd = open(v, O_WRONLY);
            if (confirm_fd < 0) {
                fprintf(stderr, "tabletd: cannot open %s\n", v);
                return 1;
            }
            i++;
        } else if (strcmp(a, "--secure-listen") == 0) {
            secure_spec = v, i++;
        } else if (strcmp(a, "--secure-connect") == 0) {
            connect_spec = v, i++;
        } else if (strcmp(a, "--key") == 0) {
            key_path = v, i++;
        } else if (strcmp(a, "--drop") == 0) {
            drop = v, i++;
        } else if (strcmp(a, "--audio") == 0 && v) {
            audio_program = v, i++;
        } else if (strcmp(a, "--daemon") == 0) {
            log_path = v, i++;
        } else if (strcmp(a, "--input") == 0 && ninputs < MAX_INPUTS) {
            if (input_open(&inputs[ninputs], v) < 0) {
                fprintf(stderr, "tabletd: cannot open input %s\n", v);
                return 1;
            }
            ninputs++, i++;
        } else {
            usage();
            return 2;
        }
    }
    if (!fb_path) {
        usage();
        return 2;
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGHUP, SIG_IGN);
    if (log_path && daemonize(log_path) < 0) {
        fprintf(stderr, "tabletd: cannot write log %s\n", log_path);
        return 1;
    }
    fprintf(stderr, "tabletd pid %d\n", (int)getpid());
    if (fb_open(&fb, fb_path, geom) < 0)
        return 1;
    unpacked = malloc((size_t)fb.w * fb.h * 2);
    if (!unpacked)
        return 1;
    listen_fd = open_listener(listen_spec);
    if (listen_fd < 0) {
        fprintf(stderr, "tabletd: cannot listen on %s\n", listen_spec);
        return 1;
    }
    if (secure_spec) {
        /* [IPV4:]PORT, all interfaces if no address is given */
        const char *colon = strrchr(secure_spec, ':');
        struct in_addr addr = { .s_addr = htonl(INADDR_ANY) };
        char host[32] = "";

        if (colon) {
            size_t len = (size_t)(colon - secure_spec);

            if (len >= sizeof(host)) {
                usage();
                return 2;
            }
            memcpy(host, secure_spec, len);
            if (inet_pton(AF_INET, host, &addr) != 1) {
                usage();
                return 2;
            }
        }
        /* the key file must belong to us alone; without it, no network port */
        if (!key_path || sec_load_key(key_path, sec_key) < 0) {
            fprintf(stderr, "tabletd: --secure-listen needs --key with a private "
                    "32-byte file\n");
            return 1;
        }
        secure_fd = open_tcp(atoi(colon ? colon + 1 : secure_spec), ntohl(addr.s_addr));
        if (secure_fd < 0) {
            fprintf(stderr, "tabletd: cannot listen on %s\n", secure_spec);
            return 1;
        }
        fprintf(stderr, "tabletd: encrypted connections on %s\n", secure_spec);
    }
    if (connect_spec) {
        /* IPV4:PORT or [IPV6]:PORT */
        const char *colon = strrchr(connect_spec, ':');
        const char *start = connect_spec, *end = colon;
        char host[64], *rest = NULL;
        long port = colon ? strtol(colon + 1, &rest, 10) : 0;
        int v6 = connect_spec[0] == '[';

        if (!rest || rest == colon + 1 || *rest != '\0')
            port = 0;                   /* digits only after the colon */

        if (v6) {
            start = connect_spec + 1;
            end = colon && colon > connect_spec && colon[-1] == ']' ? colon - 1 : NULL;
        }
        if (!colon || !end || end <= start || (size_t)(end - start) >= sizeof(host) ||
            port <= 0 || port > 65535) {
            usage();
            return 2;
        }
        memcpy(host, start, (size_t)(end - start));
        host[end - start] = '\0';
        memset(&connect_addr, 0, sizeof(connect_addr));
        if (v6) {
            struct sockaddr_in6 *a = (struct sockaddr_in6 *)&connect_addr;

            a->sin6_family = AF_INET6;
            a->sin6_port = htons((uint16_t)port);
            connect_addr_len = sizeof(*a);
            if (inet_pton(AF_INET6, host, &a->sin6_addr) != 1) {
                usage();
                return 2;
            }
        } else {
            struct sockaddr_in *a = (struct sockaddr_in *)&connect_addr;

            a->sin_family = AF_INET;
            a->sin_port = htons((uint16_t)port);
            connect_addr_len = sizeof(*a);
            if (inet_pton(AF_INET, host, &a->sin_addr) != 1) {
                usage();
                return 2;
            }
        }
        if (!key_path || (!secure_spec && sec_load_key(key_path, sec_key) < 0)) {
            fprintf(stderr, "tabletd: --secure-connect needs --key with a private 32-byte file\n");
            return 1;
        }
        have_connect = 1;
        fprintf(stderr, "tabletd: calling the laptop at %s\n", connect_spec);
    }
    if (audio_program)
        start_audio(audio_program, drop);
    if (drop && drop_privileges(drop) < 0) {
        fprintf(stderr, "tabletd: cannot drop privileges to %s\n", drop);
        return 1;
    }
    fprintf(stderr, "tabletd %s: %ux%u, listening on %s\n", TABLETD_VERSION,
            fb.w, fb.h, listen_spec);
    draw_status_page(1);
    last_activity_ms = now_ms();

    for (;;) {
        struct pollfd p[5 + MAX_INPUTS];
        int n = 0, timeout = -1;

        /* a negative fd is ignored by poll */
        p[n++] = (struct pollfd){ .fd = listen_fd, .events = POLLIN };
        p[n++] = (struct pollfd){ .fd = client, .events = POLLIN };
        p[n++] = (struct pollfd){ .fd = secure_fd, .events = POLLIN };
        p[n++] = (struct pollfd){ .fd = pend_fd, .events = POLLIN };
        if (have_connect && client < 0 && pend_fd < 0 && connect_fd < 0 &&
            now_ms() >= next_connect_ms)
            start_connect();
        p[n++] = (struct pollfd){ .fd = connect_fd, .events = POLLOUT };
        for (int i = 0; i < ninputs; i++)
            p[n++] = (struct pollfd){ .fd = inputs[i].fd, .events = POLLIN };
        if (client >= 0) {
            long long idle = now_ms() - last_rx_ms;

            timeout = idle >= CLIENT_TIMEOUT_MS ? 0 : (int)(CLIENT_TIMEOUT_MS - idle);
        } else if (status_page) {
            timeout = STATUS_PAGE_PERIOD_MS;
        }
        if (power_down_ms >= 0) {
            long long left = POWER_HOLD_MS - (now_ms() - power_down_ms);

            if (timeout < 0 || left < timeout)
                timeout = left < 0 ? 0 : (int)left;
        }
        if (client < 0 && display_on && idle_dim_ms > 0) {
            long long left = idle_dim_ms - (now_ms() - last_activity_ms);

            if (timeout < 0 || left < timeout)
                timeout = left < 0 ? 0 : (int)left;
        }
        if (pend_fd >= 0) {
            long long left = PENDING_TIMEOUT_MS - (now_ms() - pend_since_ms);

            if (timeout < 0 || left < timeout)
                timeout = left < 0 ? 0 : (int)left;
        }
        if (have_connect && client < 0) {
            long long left = connect_fd >= 0 ? CONNECT_TIMEOUT_MS - (now_ms() - connect_since_ms)
                                             : next_connect_ms - now_ms();

            if (timeout < 0 || left < timeout)
                timeout = left < 0 ? 0 : (int)left;
        }
        if (poll(p, (nfds_t)n, timeout) < 0 && errno != EINTR)
            return 1;

        if (p[0].revents & POLLIN)
            accept_client();
        else if (client >= 0 && (p[1].revents & (POLLIN | POLLHUP | POLLERR)))
            read_client();
        if (p[2].revents & POLLIN)
            accept_secure();
        else if (pend_fd >= 0 && (p[3].revents & (POLLIN | POLLHUP | POLLERR)))
            read_pending();
        if (pend_fd >= 0 && now_ms() - pend_since_ms >= PENDING_TIMEOUT_MS)
            drop_pending("no proof of the key within 5 seconds");
        if (connect_fd >= 0 && (p[4].revents & (POLLOUT | POLLHUP | POLLERR))) {
            finish_connect();
        } else if (connect_fd >= 0 && now_ms() - connect_since_ms >= CONNECT_TIMEOUT_MS) {
            close(connect_fd);
            connect_fd = -1;
        }
        for (int i = 0; i < ninputs; i++) {
            if (!(p[5 + i].revents & (POLLIN | POLLHUP | POLLERR)))
                continue;
            if (input_read(&inputs[i], (int)fb.w - 1, (int)fb.h - 1,
                           on_touch, on_key, NULL) < 0) {
                fprintf(stderr, "tabletd: input device %d lost\n", i);
                close(inputs[i].fd);
                inputs[i].fd = -1;
            }
        }
        if (home_requested) {
            fprintf(stderr, "tabletd: back to the start screen (asked by the laptop)\n");
            drop_client("back to the start screen");
            return 0;
        }
        if (client >= 0 && now_ms() - last_rx_ms >= CLIENT_TIMEOUT_MS)
            drop_client("silent for 5 seconds");
        if (client >= 0 && now_ms() - last_status_ms >= STATUS_PERIOD_MS)
            send_status();
        if (power_down_ms >= 0 && now_ms() - power_down_ms >= POWER_HOLD_MS) {
            fprintf(stderr, "tabletd: power key held, asking the launcher to switch off\n");
            return EXIT_POWEROFF;
        }
        if (client < 0 && display_on && idle_dim_ms > 0 &&
            now_ms() - last_activity_ms >= idle_dim_ms)
            set_display(0);
        draw_status_page(0);
    }
}
