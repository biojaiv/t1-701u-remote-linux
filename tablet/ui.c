/*
 * Pieces shared by the home screen (tb-home) and the remote-control program
 * (tabletd): colours, battery and Wi-Fi state, clock and date texts, the
 * status bar at the top.
 */
#include "tabletd.h"

#include <arpa/inet.h>
#include <fcntl.h>
#include <net/if.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

const struct rgb UI_BG_TOP = { 16, 24, 44 }, UI_BG_BOTTOM = { 5, 7, 13 };
const struct rgb UI_TEXT_MAIN = { 240, 244, 250 }, UI_TEXT_SUB = { 201, 209, 220 };
const struct rgb UI_TEXT_DIM = { 154, 164, 178 }, UI_LINE = { 30, 41, 59 };
const struct rgb UI_AMBER = { 251, 191, 36 }, UI_GREEN = { 74, 222, 128 };
const struct rgb UI_RED = { 248, 113, 113 }, UI_ACCENT = { 96, 165, 250 };
const struct rgb UI_CARD = { 24, 33, 56 }, UI_CARD_EDGE = { 44, 58, 92 };
const struct rgb UI_CARD_PRESSED = { 37, 99, 235 };

static int read_long(const char *dir, const char *name, long *out)
{
    char path[256], buf[32];
    FILE *f;
    int ok;

    snprintf(path, sizeof(path), "%s/%s", dir, name);
    f = fopen(path, "r");
    if (!f)
        return -1;
    ok = fgets(buf, sizeof(buf), f) != NULL;
    fclose(f);
    if (!ok)
        return -1;
    *out = strtol(buf, NULL, 10);
    return 0;
}

int ui_battery(const char *dir, long *cap, int *charging, long *temp)
{
    char path[256], state[32] = "";
    FILE *f;

    if (!dir || read_long(dir, "capacity", cap) < 0)
        return -1;
    *cap = *cap < 0 ? 0 : *cap > 100 ? 100 : *cap;
    *temp = 0;
    read_long(dir, "temp", temp);
    snprintf(path, sizeof(path), "%s/status", dir);
    f = fopen(path, "r");
    if (f) {
        if (!fgets(state, sizeof(state), f))
            state[0] = '\0';
        fclose(f);
    }
    *charging = strncmp(state, "Charging", 8) == 0 || strncmp(state, "Full", 4) == 0;
    return 0;
}

void ui_address(const char *iface, char *out, size_t size)
{
    struct ifreq ifr;
    int fd = socket(AF_INET, SOCK_DGRAM, 0);

    out[0] = '\0';
    if (fd < 0)
        return;
    memset(&ifr, 0, sizeof(ifr));
    snprintf(ifr.ifr_name, sizeof(ifr.ifr_name), "%s", iface);
    if (ioctl(fd, SIOCGIFADDR, &ifr) == 0)
        inet_ntop(AF_INET, &((struct sockaddr_in *)&ifr.ifr_addr)->sin_addr, out,
                  (socklen_t)size);
    close(fd);
}

/* days since 1970-01-01 -> year, month, day (proleptic Gregorian calendar) */
static void civil_date(long long days, int *year, int *month, int *day)
{
    long long z = days + 719468, era = (z >= 0 ? z : z - 146096) / 146097;
    long long doe = z - era * 146097;
    long long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    long long doy = doe - (365 * yoe + yoe / 4 - yoe / 100), mp = (5 * doy + 2) / 153;

    *day = (int)(doy - (153 * mp + 2) / 5 + 1);
    *month = (int)(mp < 10 ? mp + 3 : mp - 9);
    *year = (int)(yoe + era * 400 + (*month <= 2));
}

long long ui_time_texts(long long t, char *clock, size_t clock_size, char *date,
                        size_t date_size)
{
    static const char *weekdays[7] = { "Sunday", "Monday", "Tuesday", "Wednesday",
                                       "Thursday", "Friday", "Saturday" };
    static const char *months[12] = { "January", "February", "March", "April", "May", "June",
                                      "July", "August", "September", "October",
                                      "November", "December" };
    long long days = (t >= 0 ? t : t - 86399) / 86400;
    int of_day = (int)((t - days * 86400) / 60);   /* 0..1439 */
    int year, month, day;

    snprintf(clock, clock_size, "%02d:%02d", of_day / 60 % 24, of_day % 60);
    civil_date(days, &year, &month, &day);
    snprintf(date, date_size, "%s, %d %s", weekdays[((days % 7) + 11) % 7], day,
             months[month - 1]);
    return days * 1440 + of_day;
}

void ui_header(struct canvas *c, const char *addr, long cap, int charging)
{
    char line[64];
    int cw = (int)canvas_width(c);

    if (addr[0])
        snprintf(line, sizeof(line), "Wi-Fi  %s", addr);
    else
        snprintf(line, sizeof(line), "No Wi-Fi");
    canvas_text(c, &face_text, UI_MARGIN, 50, UI_TEXT_DIM, line);
    if (cap >= 0) {
        struct rgb level = cap > 50 ? UI_GREEN : cap >= 20 ? UI_AMBER : UI_RED;
        int tx, ix;

        snprintf(line, sizeof(line), "%ld %%", cap);
        tx = cw - UI_MARGIN - text_width(&face_text, line);
        canvas_text(c, &face_text, tx, 50, UI_TEXT_SUB, line);
        ix = tx - 12 - 44;                                   /* icon: body 40, nub 4 */
        canvas_rect(c, ix, 31, 40, 2, UI_TEXT_DIM);          /* outline */
        canvas_rect(c, ix, 49, 40, 2, UI_TEXT_DIM);
        canvas_rect(c, ix, 31, 2, 20, UI_TEXT_DIM);
        canvas_rect(c, ix + 38, 31, 2, 20, UI_TEXT_DIM);
        canvas_rect(c, ix + 40, 37, 4, 8, UI_TEXT_DIM);      /* nub */
        canvas_rect(c, ix + 4, 35, (int)(32 * cap / 100), 12, level);
        if (charging)
            canvas_text(c, &face_text, ix - 8 - text_width(&face_text, "⚡"), 50, UI_AMBER,
                        "⚡");
    } else {
        canvas_text(c, &face_text, cw - UI_MARGIN - text_width(&face_text, "Battery ?"), 50,
                    UI_TEXT_DIM, "Battery ?");
    }
    canvas_rect(c, UI_MARGIN, 72, cw - 2 * UI_MARGIN, 1, UI_LINE);
}

int ui_tz_load(const char *path, int32_t *offset)
{
    char buf[32] = "";
    FILE *f;
    char *end;
    long v;

    *offset = 0;
    if (!path || !(f = fopen(path, "r")))
        return -1;
    if (!fgets(buf, sizeof(buf), f))
        buf[0] = '\0';
    fclose(f);
    v = strtol(buf, &end, 10);
    if (end == buf || v < -14 * 3600 || v > 14 * 3600)
        return -1;
    *offset = (int32_t)v;
    return 0;
}

void ui_tz_save(const char *path, int32_t offset)
{
    char tmp[256], buf[32];
    int fd, n;

    if (!path || snprintf(tmp, sizeof(tmp), "%s.new", path) >= (int)sizeof(tmp))
        return;
    fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0)
        return;
    n = snprintf(buf, sizeof(buf), "%d\n", (int)offset);
    if (write(fd, buf, (size_t)n) == n)
        rename(tmp, path);
    close(fd);
}
