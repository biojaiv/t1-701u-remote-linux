/*
 * tb-home - the tablet's start screen.
 *
 * Shows time, date, battery and Wi-Fi, and one tile per program. Tapping a
 * tile starts the program as a separate process, which then owns the screen.
 * Holding both volume keys for a moment ends it and brings the start screen
 * back. tb-home keeps the power key: short = display off/on, held for three
 * seconds = switch off (exit status 42, as tabletd did before).
 *
 * Programs: the remote control (tabletd), whose command line comes after "--",
 * and optionally the Linux desktop (--linux PROGRAM, normally tb-linux).
 * Another program that exits with status 50 asks for the remote control,
 * which then starts at once (the "Remote control" tile inside Linux):
 *   tb-home --fb /dev/graphics/fb0 --touch DEV --keys DEV [options]
 *           [--linux tb-linux] -- tabletd ARGS...
 */
#include "tabletd.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input-event-codes.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define EXIT_POWEROFF 42
#define EXIT_TO_REMOTE 50         /* a program asks for the remote control */
#define EXIT_TO_ANDROID 43        /* "Android" tile: the launcher restarts into Android */
#define POWER_HOLD_MS 3000
#define HOME_HOLD_MS 700          /* both volume keys: back to the start screen */
#define STOP_GRACE_MS 5000        /* SIGTERM, then SIGKILL; the Linux session needs up to 3 s */
#define REFRESH_MS 5000
#define IDLE_DIM_MS 300000

static struct fb fb;
static struct input_dev touch, keys;
static uint8_t *canvas_buf;
static int rotation = ROT_NONE;
static const char *battery_dir, *iface = "wlan0", *tz_path, *app_log;
struct app { const char *label; char **argv; };
static struct app apps[2];            /* 0: remote control, 1: Linux (optional) */
static int napps;
static int app_running = -1;          /* index into apps while a program runs */
static char *linux_argv[2];
static int android_tile;              /* --android-tile: last tile, leaves for Android */
static pid_t app_pid = -1;
static long long app_stop_at = -1;    /* SIGKILL deadline after SIGTERM */
static int backlight_fd = -1, display_on = 1, brightness = 200;
static long long power_down_ms = -1, last_touch_ms;
static int vol_up, vol_down;
static long long both_since = -1;
static int pressed_tile = -1;         /* drawn highlighted until the program starts */
static volatile sig_atomic_t quit_requested;   /* SIGTERM: end the program, then ourselves */
static int confirm_fd = -1;           /* launcher's boot counter: reset on the first touch */

/* what the start screen showed last time; redraw only on change */
static long long shown_minute = -2;
static long shown_cap = -2;
static int shown_charging = -1;
static char shown_addr[INET_ADDRSTRLEN];

static long long now_ms(void)
{
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* -- display --------------------------------------------------------------- */

static void write_backlight(unsigned value)
{
    char text[8];
    int n = snprintf(text, sizeof(text), "%u\n", value);

    if (backlight_fd < 0)
        return;
    if (ftruncate(backlight_fd, 0) < 0)
        errno = 0;   /* sysfs ignores it; a plain file (tests) needs it */
    if (pwrite(backlight_fd, text, (size_t)n, 0) != n)
        fprintf(stderr, "tb-home: cannot set backlight\n");
}

static void set_display(int on)
{
    display_on = on;
    write_backlight(on ? (unsigned)brightness : 0);
}

/* -- layout ----------------------------------------------------------------- */

struct tile { const char *label; int x, y, w, h; };

static int tiles(struct canvas *c, struct tile *out)
{
    int cw = (int)canvas_width(c), ch = (int)canvas_height(c);

    int n = napps + android_tile, w = 240, h = 180, gap = 40;
    int x;

    if (n * w + (n - 1) * gap > cw - 2 * 24) {   /* three tiles in portrait: narrower */
        gap = 24;
        w = (cw - 2 * 24 - (n - 1) * gap) / n;
    }
    x = (cw - (n * w + (n - 1) * gap)) / 2;
    for (int i = 0; i < napps; i++)
        out[i] = (struct tile){ apps[i].label, x + i * (w + gap), ch / 2 + 20, w, h };
    if (android_tile)
        out[napps] = (struct tile){ "Android", x + napps * (w + gap), ch / 2 + 20, w, h };
    return n;
}

/* the tile's name below its icon; too wide for the tile (three tiles in
   portrait are 168 px, "Remote control" is 215 px) it is broken at the last
   space that lets the first line fit, into two lines */
static void draw_label(struct canvas *c, const struct tile *t)
{
    const int room = t->w - 20;
    char first[64];
    const char *space = NULL;

    if (text_width(&face_text, t->label) <= room || strlen(t->label) >= sizeof(first)) {
        canvas_text(c, &face_text, t->x + (t->w - text_width(&face_text, t->label)) / 2,
                    t->y + t->h - 26, UI_TEXT_MAIN, t->label);
        return;
    }
    for (const char *p = t->label; (p = strchr(p, ' ')) != NULL; p++) {
        memcpy(first, t->label, (size_t)(p - t->label));
        first[p - t->label] = '\0';
        if (text_width(&face_text, first) > room)
            break;
        space = p;
    }
    if (!space)
        space = strchr(t->label, ' ');
    if (!space) {                                 /* one long word: as it is */
        canvas_text(c, &face_text, t->x + (t->w - text_width(&face_text, t->label)) / 2,
                    t->y + t->h - 26, UI_TEXT_MAIN, t->label);
        return;
    }
    memcpy(first, t->label, (size_t)(space - t->label));
    first[space - t->label] = '\0';
    canvas_text(c, &face_text, t->x + (t->w - text_width(&face_text, first)) / 2,
                t->y + t->h - 50, UI_TEXT_MAIN, first);
    canvas_text(c, &face_text, t->x + (t->w - text_width(&face_text, space + 1)) / 2,
                t->y + t->h - 18, UI_TEXT_MAIN, space + 1);
}

/* a laptop in outline: screen, keyboard base */
static void draw_laptop(struct canvas *c, int cx, int top, struct rgb colour, struct rgb fill)
{
    canvas_round_rect(c, cx - 48, top, 96, 62, 6, colour);
    canvas_rect(c, cx - 43, top + 5, 86, 52, fill);
    canvas_round_rect(c, cx - 60, top + 66, 120, 8, 3, colour);
    canvas_rect(c, cx - 12, top + 66, 24, 3, fill);   /* notch of the touchpad */
}

/* a phone in outline: Android */
static void draw_phone(struct canvas *c, int cx, int top, struct rgb colour, struct rgb fill)
{
    canvas_round_rect(c, cx - 26, top - 2, 52, 80, 9, colour);
    canvas_rect(c, cx - 21, top + 8, 42, 56, fill);
    canvas_rect(c, cx - 6, top + 68, 12, 4, fill);    /* button */
}

/* a terminal window: frame with title bar, prompt inside */
static void draw_terminal(struct canvas *c, int cx, int top, struct rgb colour, struct rgb fill)
{
    canvas_round_rect(c, cx - 50, top, 100, 74, 8, colour);
    canvas_rect(c, cx - 45, top + 14, 90, 55, fill);
    canvas_text(c, &face_text, cx - 36, top + 52, colour, ">_");
}

static void draw_home(int force)
{
    struct canvas c = { canvas_buf, fb.w, fb.h, rotation };
    struct tile t[4];
    char clock_text[8] = "--:--", date_text[48] = "Time comes from the laptop";
    char addr[INET_ADDRSTRLEN];
    long cap = -1, temp;
    long long minute = -1;
    int charging = 0, cw, ch, n;
    int32_t offset;

    if (app_pid > 0)
        return;   /* the program owns the screen */
    if (ui_tz_load(tz_path, &offset) == 0)
        minute = ui_time_texts((long long)time(NULL) + offset, clock_text,
                               sizeof(clock_text), date_text, sizeof(date_text));
    if (ui_battery(battery_dir, &cap, &charging, &temp) < 0)
        cap = -1;
    ui_address(iface, addr, sizeof(addr));
    if (!force && minute == shown_minute && cap == shown_cap && charging == shown_charging &&
        strcmp(addr, shown_addr) == 0)
        return;
    shown_minute = minute;
    shown_cap = cap;
    shown_charging = charging;
    snprintf(shown_addr, sizeof(shown_addr), "%s", addr);

    cw = (int)canvas_width(&c);
    ch = (int)canvas_height(&c);
    canvas_gradient(&c, UI_BG_TOP, UI_BG_BOTTOM);
    ui_header(&c, addr, cap, charging);
    canvas_text(&c, &face_clock, (cw - text_width(&face_clock, clock_text)) / 2, ch / 2 - 60,
                UI_TEXT_MAIN, clock_text);
    canvas_text(&c, &face_text, (cw - text_width(&face_text, date_text)) / 2, ch / 2 - 10,
                UI_TEXT_SUB, date_text);

    n = tiles(&c, t);
    for (int i = 0; i < n; i++) {
        int pressed = i == pressed_tile;
        struct rgb base = pressed ? UI_CARD_PRESSED : UI_CARD;

        canvas_round_rect(&c, t[i].x + 2, t[i].y + 5, t[i].w, t[i].h, 22, (struct rgb){ 2, 4, 9 });
        canvas_round_rect(&c, t[i].x, t[i].y, t[i].w, t[i].h, 22,
                          pressed ? UI_ACCENT : UI_CARD_EDGE);
        canvas_round_rect(&c, t[i].x, t[i].y + 1, t[i].w, t[i].h - 1, 22, base);
        (i == 0 ? draw_laptop : i < napps ? draw_terminal : draw_phone)(
            &c, t[i].x + t[i].w / 2, t[i].y + 28, pressed ? UI_TEXT_MAIN : UI_ACCENT, base);
        draw_label(&c, &t[i]);
    }
    {
        const char *hint = "Back to start: hold both volume keys";

        canvas_text(&c, &face_text, (cw - text_width(&face_text, hint)) / 2, ch - 30,
                    UI_TEXT_DIM, hint);
    }
    fb_rect565(&fb, 0, 0, fb.w, fb.h, canvas_buf);
}

/* -- programs ------------------------------------------------------------- */

static void start_app(int index)
{
    char **argv;
    pid_t pid;

    if (app_pid > 0 || index < 0 || index >= napps)
        return;
    argv = apps[index].argv;
    pid = fork();
    if (pid < 0) {
        perror("tb-home: fork");
        return;
    }
    if (pid == 0) {
        if (app_log) {
            int log = open(app_log, O_WRONLY | O_CREAT | O_APPEND, 0644);

            if (log >= 0) {
                dup2(log, 1);
                dup2(log, 2);
                close(log);
            }
        }
        signal(SIGPIPE, SIG_DFL);
        execv(argv[0], argv);
        perror("tb-home: exec");
        _exit(127);
    }
    app_pid = pid;
    app_running = index;
    app_stop_at = -1;
    fprintf(stderr, "tb-home: started %s (pid %d)\n", argv[0], (int)pid);
}

static void stop_app(void)
{
    if (app_pid > 0 && app_stop_at < 0) {
        kill(app_pid, SIGTERM);
        app_stop_at = now_ms() + STOP_GRACE_MS;
    }
}

/* returns 1 if the program ended (the start screen is back) */
static int reap_app(void)
{
    int status;

    if (app_pid <= 0)
        return 0;
    if (waitpid(app_pid, &status, WNOHANG) != app_pid) {
        if (app_stop_at >= 0 && now_ms() >= app_stop_at) {
            kill(app_pid, SIGKILL);
            app_stop_at = now_ms() + STOP_GRACE_MS;
        }
        return 0;
    }
    int code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    int was = app_running;

    fprintf(stderr, "tb-home: %s ended (status %d)\n", apps[app_running].argv[0], code);
    app_pid = -1;
    app_running = -1;
    app_stop_at = -1;
    pressed_tile = -1;
    if (!display_on)
        set_display(1);
    if (code == EXIT_TO_REMOTE && was != 0) {
        fprintf(stderr, "tb-home: switching to the remote control\n");
        pressed_tile = 0;
        draw_home(1);
        start_app(0);
        if (app_pid > 0)
            return 1;
        pressed_tile = -1;
    }
    draw_home(1);
    return 1;
}

/* -- input ----------------------------------------------------------------- */

static void to_canvas(int x_f, int y_f, int *x_c, int *y_c)
{
    if (rotation == ROT_CCW) {
        *x_c = (int)fb.h - 1 - y_f;
        *y_c = x_f;
    } else if (rotation == ROT_CW) {
        *x_c = y_f;
        *y_c = (int)fb.w - 1 - x_f;
    } else {
        *x_c = x_f;
        *y_c = y_f;
    }
}

static void on_touch(void *ctx, int slot, int state, int x, int y)
{
    struct canvas c = { canvas_buf, fb.w, fb.h, rotation };
    struct tile t[4];
    int n, x_c, y_c;

    (void)ctx;
    last_touch_ms = now_ms();
    if (confirm_fd >= 0) {
        /* someone uses the tablet: this boot worked, no fallback to Android */
        if (ftruncate(confirm_fd, 0) < 0 || pwrite(confirm_fd, "0\n", 2, 0) != 2)
            fprintf(stderr, "tb-home: cannot reset the boot counter\n");
        close(confirm_fd);
        confirm_fd = -1;
    }
    if (app_pid > 0 || slot != 0 || state != TOUCH_DOWN)
        return;   /* while a program runs, the touches are its own */
    if (!display_on) {
        set_display(1);   /* the first touch only wakes the screen */
        return;
    }
    to_canvas(x, y, &x_c, &y_c);
    n = tiles(&c, t);
    for (int i = 0; i < n; i++) {
        if (x_c >= t[i].x && x_c < t[i].x + t[i].w && y_c >= t[i].y && y_c < t[i].y + t[i].h) {
            pressed_tile = i;
            draw_home(1);
            if (i >= napps) {             /* the Android tile */
                fprintf(stderr, "tb-home: Android tile, asking the launcher to restart\n");
                exit(EXIT_TO_ANDROID);
            }
            start_app(i);
            if (app_pid <= 0) {   /* could not start: back to normal */
                pressed_tile = -1;
                draw_home(1);
            }
            return;
        }
    }
}

static void on_key(void *ctx, int code, int state)
{
    (void)ctx;
    if (code == KEY_POWER) {
        if (state == 1) {
            power_down_ms = now_ms();
        } else if (power_down_ms >= 0) {
            power_down_ms = -1;
            set_display(!display_on);
        }
        return;
    }
    if (code == KEY_VOLUMEUP)
        vol_up = state != 0;
    else if (code == KEY_VOLUMEDOWN)
        vol_down = state != 0;
    else
        return;
    both_since = vol_up && vol_down ? (both_since >= 0 ? both_since : now_ms()) : -1;
}

/* -- main ------------------------------------------------------------------- */

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
            "usage: tb-home --fb PATH [--geom WxH] --touch DEV --keys DEV [--battery DIR]\n"
            "               [--backlight FILE] [--rotation R | --rotation-file FILE]\n"
            "               [--iface NAME] [--tz-file FILE] [--app-log FILE] [--start-remote]\n"
            "               [--daemon LOGFILE] [--confirm FILE] [--linux PROGRAM]\n"
            "               [--android-tile]\n"
            "               -- REMOTE-PROGRAM [ARGS...]\n");
}

static void on_sigterm(int sig)
{
    (void)sig;
    quit_requested = 1;
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

static void shorten(int *timeout, long long left)
{
    if (left < 0)
        left = 0;
    if (*timeout < 0 || left < *timeout)
        *timeout = (int)left;
}

int main(int argc, char **argv)
{
    const char *fb_path = NULL, *geom = NULL, *touch_path = NULL, *keys_path = NULL;
    const char *log_path = NULL;
    char **app_argv = NULL;
    int start_remote = 0;

    for (int i = 1; i < argc; i++) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;

        if (strcmp(a, "--") == 0) {
            if (i + 1 < argc)
                app_argv = argv + i + 1;
            break;
        } else if (strcmp(a, "--start-remote") == 0) {
            start_remote = 1;
        } else if (strcmp(a, "--android-tile") == 0) {
            android_tile = 1;
        } else if (!v) {
            usage();
            return 2;
        } else if (strcmp(a, "--fb") == 0) {
            fb_path = v, i++;
        } else if (strcmp(a, "--geom") == 0) {
            geom = v, i++;
        } else if (strcmp(a, "--touch") == 0) {
            touch_path = v, i++;
        } else if (strcmp(a, "--keys") == 0) {
            keys_path = v, i++;
        } else if (strcmp(a, "--battery") == 0) {
            battery_dir = v, i++;
        } else if (strcmp(a, "--backlight") == 0) {
            backlight_fd = open(v, O_WRONLY);
            if (backlight_fd < 0)
                fprintf(stderr, "tb-home: cannot open backlight %s (display stays on)\n", v);
            i++;
        } else if (strcmp(a, "--rotation") == 0) {
            if ((rotation = parse_rotation(v)) < 0) {
                usage();
                return 2;
            }
            i++;
        } else if (strcmp(a, "--rotation-file") == 0) {
            char word[32] = "";
            FILE *f = fopen(v, "r");

            if (f) {
                if (fgets(word, sizeof(word), f) && parse_rotation(word) >= 0)
                    rotation = parse_rotation(word);
                fclose(f);
            }
            i++;
        } else if (strcmp(a, "--iface") == 0) {
            iface = v, i++;
        } else if (strcmp(a, "--tz-file") == 0) {
            tz_path = v, i++;
        } else if (strcmp(a, "--app-log") == 0) {
            app_log = v, i++;
        } else if (strcmp(a, "--daemon") == 0) {
            log_path = v, i++;
        } else if (strcmp(a, "--confirm") == 0) {
            confirm_fd = open(v, O_WRONLY);
            i++;
        } else if (strcmp(a, "--linux") == 0) {
            linux_argv[0] = (char *)v, i++;
        } else {
            usage();
            return 2;
        }
    }
    if (!fb_path || !touch_path || !keys_path || !app_argv) {
        usage();
        return 2;
    }
    apps[napps++] = (struct app){ "Remote control", app_argv };
    if (linux_argv[0])
        apps[napps++] = (struct app){ "Linux", linux_argv };
    signal(SIGPIPE, SIG_IGN);
    signal(SIGHUP, SIG_IGN);
    if (log_path && daemonize(log_path) < 0) {
        fprintf(stderr, "tb-home: cannot write log %s\n", log_path);
        return 1;
    }
    {
        struct sigaction sa;

        memset(&sa, 0, sizeof(sa));
        sa.sa_handler = on_sigterm;   /* no SA_RESTART: poll returns at once */
        sigaction(SIGTERM, &sa, NULL);
    }
    if (fb_open(&fb, fb_path, geom) < 0)
        return 1;
    canvas_buf = malloc((size_t)fb.w * fb.h * 2);
    if (!canvas_buf || input_open(&touch, touch_path) < 0 || input_open(&keys, keys_path) < 0) {
        fprintf(stderr, "tb-home: cannot open the screen buffer or the input devices\n");
        return 1;
    }
    fprintf(stderr, "tb-home pid %d\n", (int)getpid());
    last_touch_ms = now_ms();
    draw_home(1);
    if (start_remote)
        start_app(0);

    for (;;) {
        struct pollfd p[2] = { { .fd = touch.fd, .events = POLLIN },
                               { .fd = keys.fd, .events = POLLIN } };
        int timeout = REFRESH_MS;

        if (app_pid > 0)
            shorten(&timeout, 250);   /* watch the program */
        if (power_down_ms >= 0)
            shorten(&timeout, POWER_HOLD_MS - (now_ms() - power_down_ms));
        if (both_since >= 0)
            shorten(&timeout, HOME_HOLD_MS - (now_ms() - both_since));
        if (app_pid <= 0 && display_on)
            shorten(&timeout, IDLE_DIM_MS - (now_ms() - last_touch_ms));
        if (poll(p, 2, timeout) < 0 && errno != EINTR)
            break;
        if (p[0].revents & (POLLIN | POLLHUP | POLLERR))
            if (input_read(&touch, (int)fb.w - 1, (int)fb.h - 1, on_touch, on_key, NULL) < 0)
                touch.fd = -1;
        if (p[1].revents & (POLLIN | POLLHUP | POLLERR))
            if (input_read(&keys, (int)fb.w - 1, (int)fb.h - 1, on_touch, on_key, NULL) < 0)
                keys.fd = -1;

        if (quit_requested) {
            if (app_pid > 0) {
                kill(app_pid, SIGTERM);
                waitpid(app_pid, NULL, 0);
            }
            fprintf(stderr, "tb-home: stopped\n");
            return 0;
        }
        reap_app();
        if (both_since >= 0 && now_ms() - both_since >= HOME_HOLD_MS) {
            both_since = -1;
            if (app_pid > 0) {
                fprintf(stderr, "tb-home: both volume keys held, back to the start screen\n");
                stop_app();
            }
        }
        if (power_down_ms >= 0 && now_ms() - power_down_ms >= POWER_HOLD_MS) {
            fprintf(stderr, "tb-home: power key held, asking the launcher to switch off\n");
            if (app_pid > 0) {
                kill(app_pid, SIGTERM);
                waitpid(app_pid, NULL, 0);
            }
            return EXIT_POWEROFF;
        }
        if (app_pid <= 0 && display_on && now_ms() - last_touch_ms >= IDLE_DIM_MS)
            set_display(0);
        draw_home(0);
    }
    return 1;
}
