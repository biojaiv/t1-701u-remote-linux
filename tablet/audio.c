/*
 * tb-audio - plays the laptop's sound on the tablet's speaker, without Android.
 *
 * tabletd starts it before it gives up root and feeds it through a pipe on
 * stdin: S16LE, 48 kHz, stereo. tb-audio switches the speaker path on (the
 * mixer controls Android's audio HAL would set, from /system/etc/tiny_hw.xml),
 * opens the playback device, gives up root as well (--drop) and then reports
 * "ready" on fd 3 if that is open. Blocking writes are fine here: tabletd only
 * writes into the pipe and never waits for us.
 *
 * Clock drift: the laptop's 48 kHz and the codec's 48 kHz are never exactly
 * equal. If more than MAX_BACKLOG_MS piles up in the pipe, the oldest part is
 * thrown away, so the sound stays close to the picture.
 *
 *   tb-audio [--card N|NAME] [--device N] [--drop UID:GID] [--no-route]
 *   tb-audio --info [--card N|NAME] [--device N]     what the device supports
 *
 * The card numbers change between boots (sprdphone was card 0, after a
 * reboot card 1), so the default is the card's name, looked up in
 * /proc/asound/NAME -> cardN.
 *
 * Uses tinyalsa 1.1.1 (third_party, BSD-3-Clause).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <grp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <tinyalsa/asoundlib.h>

#define RATE 48000
#define CHANNELS 2
#define FRAME_BYTES (CHANNELS * 2)
#define PERIOD_FRAMES 960            /* 20 ms */
#define PERIODS 4                     /* 80 ms in the device */
#define MAX_BACKLOG_MS 150
#define KEEP_BACKLOG_MS 60            /* what is left after throwing away */
#define PREBUFFER_MS 60               /* reserve before playing (again) */
#define PREBUFFER_BYTES (RATE / 1000 * PREBUFFER_MS * FRAME_BYTES)

/* the speaker path of /system/etc/tiny_hw.xml ("speaker", path "on") */
static const struct { const char *name; int value; } route[] = {
    { "SPKL Mixer DACLSPKL Switch", 1 },
    { "SPKL Mixer DACRSPKL Switch", 1 },
    { "Speaker Function", 1 },
    { "Speaker Mute", 0 },
};

static int usage(void)
{
    fprintf(stderr, "usage: tb-audio [--card N|NAME] [--device N] [--drop UID:GID] [--no-route]\n"
                    "       tb-audio --info [--card N|NAME] [--device N]\n"
                    "       (default card: sprdphone)\n");
    return 2;
}

/* card number from a number or a name (/proc/asound/NAME is a link to cardN) */
static int card_number(const char *spec)
{
    char path[128], target[64];
    ssize_t n;
    char *end;
    long number = strtol(spec, &end, 10);

    if (*spec && *end == '\0')
        return number >= 0 && number < 32 ? (int)number : -1;
    if (strchr(spec, '/') || snprintf(path, sizeof(path), "/proc/asound/%s", spec) >= (int)sizeof(path))
        return -1;
    n = readlink(path, target, sizeof(target) - 1);
    if (n <= 0)
        return -1;
    target[n] = '\0';
    if (strncmp(target, "card", 4) != 0)
        return -1;
    number = strtol(target + 4, &end, 10);
    return *end == '\0' && number >= 0 && number < 32 ? (int)number : -1;
}

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

static int info(unsigned card, unsigned device)
{
    struct pcm_params *params = pcm_params_get(card, device, PCM_OUT);

    if (!params) {
        fprintf(stderr, "tb-audio: cannot read the parameters of card %u device %u\n",
                card, device);
        return 1;
    }
    printf("card %u device %u playback\n", card, device);
    printf("rate      %u..%u Hz\n", pcm_params_get_min(params, PCM_PARAM_RATE),
           pcm_params_get_max(params, PCM_PARAM_RATE));
    printf("channels  %u..%u\n", pcm_params_get_min(params, PCM_PARAM_CHANNELS),
           pcm_params_get_max(params, PCM_PARAM_CHANNELS));
    printf("bits      %u..%u\n", pcm_params_get_min(params, PCM_PARAM_SAMPLE_BITS),
           pcm_params_get_max(params, PCM_PARAM_SAMPLE_BITS));
    printf("period    %u..%u frames, %u..%u periods\n",
           pcm_params_get_min(params, PCM_PARAM_PERIOD_SIZE),
           pcm_params_get_max(params, PCM_PARAM_PERIOD_SIZE),
           pcm_params_get_min(params, PCM_PARAM_PERIODS),
           pcm_params_get_max(params, PCM_PARAM_PERIODS));
    pcm_params_free(params);
    return 0;
}

/* switches the speaker on; a missing control is reported, not fatal */
static void set_route(unsigned card)
{
    struct mixer *mixer = mixer_open(card);

    if (!mixer) {
        fprintf(stderr, "tb-audio: cannot open the mixer of card %u\n", card);
        return;
    }
    for (size_t i = 0; i < sizeof(route) / sizeof(route[0]); i++) {
        struct mixer_ctl *ctl = mixer_get_ctl_by_name(mixer, route[i].name);
        unsigned n = ctl ? mixer_ctl_get_num_values(ctl) : 0;
        int ok = ctl != NULL;

        for (unsigned v = 0; v < n; v++)
            ok &= mixer_ctl_set_value(ctl, v, route[i].value) == 0;
        if (!ok)
            fprintf(stderr, "tb-audio: cannot set \"%s\"\n", route[i].name);
    }
    mixer_close(mixer);
}

/* throws away what piled up beyond MAX_BACKLOG_MS, down to KEEP_BACKLOG_MS.
   Only for a pipe (tabletd): for a file, FIONREAD reports the whole rest of
   the file, which is not a backlog (a test from a file once played 60 ms) */
static void limit_backlog(void)
{
    static char sink[1 << 16];
    const int max_bytes = RATE / 1000 * MAX_BACKLOG_MS * FRAME_BYTES;
    const int keep_bytes = RATE / 1000 * KEEP_BACKLOG_MS * FRAME_BYTES;
    int queued = 0;

    struct stat st;

    if (fstat(0, &st) < 0 || !S_ISFIFO(st.st_mode))
        return;
    if (ioctl(0, FIONREAD, &queued) < 0 || queued <= max_bytes)
        return;
    queued -= keep_bytes;
    queued -= queued % FRAME_BYTES;          /* stay on frame boundaries */
    while (queued > 0) {
        ssize_t n = read(0, sink, (size_t)(queued < (int)sizeof(sink) ? queued : (int)sizeof(sink)));

        if (n <= 0)
            break;
        queued -= (int)n;
    }
}

int main(int argc, char **argv)
{
    struct pcm_config config = {
        .channels = CHANNELS, .rate = RATE, .format = PCM_FORMAT_S16_LE,
        .period_size = PERIOD_FRAMES, .period_count = PERIODS,
    };
    static char buf[PERIOD_FRAMES * FRAME_BYTES];
    const char *card_spec = "sprdphone";
    unsigned card, device = 0;
    int found;
    const char *drop = NULL;
    int want_info = 0, want_route = 1;
    struct pcm *pcm;
    struct timespec t0, t1;
    unsigned long long frames = 0, silent_frames = 0, writes = 0, restarts = 0, dry = 0;
    int buffering = 1, is_fifo, report_fd, failures = 0;
    struct stat st;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--card") == 0 && i + 1 < argc)
            card_spec = argv[++i];
        else if (strcmp(argv[i], "--device") == 0 && i + 1 < argc)
            device = (unsigned)atoi(argv[++i]);
        else if (strcmp(argv[i], "--drop") == 0 && i + 1 < argc)
            drop = argv[++i];
        else if (strcmp(argv[i], "--info") == 0)
            want_info = 1;
        else if (strcmp(argv[i], "--no-route") == 0)
            want_route = 0;
        else
            return usage();
    }
    found = card_number(card_spec);
    if (found < 0) {
        fprintf(stderr, "tb-audio: no sound card \"%s\"\n", card_spec);
        return 1;
    }
    card = (unsigned)found;
    if (want_info)
        return info(card, device);

    signal(SIGPIPE, SIG_IGN);
    /* is fd 3 tabletd's "ready" pipe? Asked before anything is opened: once
       the mixer or the device is open, one of them may itself be fd 3 (it
       was, when tb-audio ran without tabletd, and got closed by mistake) */
    report_fd = fcntl(3, F_GETFD) >= 0 ? 3 : -1;
    if (want_route)
        set_route(card);
    pcm = pcm_open(card, device, PCM_OUT, &config);
    if (!pcm || !pcm_is_ready(pcm)) {
        fprintf(stderr, "tb-audio: cannot open card %u device %u: %s\n", card, device,
                pcm ? pcm_get_error(pcm) : "no memory");
        return 1;
    }
    if (drop && drop_privileges(drop) < 0) {
        fprintf(stderr, "tb-audio: cannot drop privileges to %s\n", drop);
        return 1;
    }
    if (report_fd >= 0) {                     /* tabletd waits for this */
        if (write(report_fd, "ready\n", 6) != 6)
            fprintf(stderr, "tb-audio: cannot report ready\n");
        close(report_fd);
    }
    fprintf(stderr, "tb-audio: playing on card %u device %u, %u Hz, %u channels, "
            "buffer %u frames\n", card, device, RATE, CHANNELS, pcm_get_buffer_size(pcm));
    is_fifo = fstat(0, &st) == 0 && S_ISFIFO(st.st_mode);
    clock_gettime(CLOCK_MONOTONIC, &t0);

    /* The device is fed one whole period at a time and never runs dry: when
       the sound has not arrived in time (Wi-Fi delivers it unevenly, or the
       laptop is silent and sends nothing), the period is filled up with
       silence. Stopping and restarting the stream instead (the first version)
       restarted it 503 times in 44 s and the sound got lost. After running
       dry, playing waits until PREBUFFER_MS have piled up again, so small
       delays no longer reach the speaker. The blocking write paces the loop. */
    for (;;) {
        struct pollfd p = { .fd = 0, .events = POLLIN };
        int queued = 0, eof = 0;
        size_t got = 0;

        limit_backlog();
        if (ioctl(0, FIONREAD, &queued) < 0)
            queued = 0;
        if (queued == 0) {
            /* a pipe: closed by tabletd? a file: at its end */
            if (is_fifo)
                eof = poll(&p, 1, 0) == 1 && (p.revents & POLLHUP) && !(p.revents & POLLIN);
            else
                eof = 1;
        }
        if (eof)
            break;
        if (buffering && queued >= PREBUFFER_BYTES)
            buffering = 0;
        if (!buffering) {
            size_t want = sizeof(buf);

            if ((size_t)queued < want) {
                want = (size_t)queued - (size_t)queued % FRAME_BYTES;
                buffering = 1;                 /* ran dry: collect a reserve first */
                dry++;
            }
            while (got < want) {
                ssize_t n = read(0, buf + got, want - got);

                if (n < 0 && errno == EINTR)
                    continue;
                if (n <= 0)
                    break;
                got += (size_t)n;
            }
            got -= got % FRAME_BYTES;
        }
        memset(buf + got, 0, sizeof(buf) - got);          /* silence for the rest */
        silent_frames += (sizeof(buf) - got) / FRAME_BYTES;
        frames += got / FRAME_BYTES;
        {
            int r = pcm_writei(pcm, buf, PERIOD_FRAMES);

            /* tinyalsa returns 0 when it (re)started the stream: the first
               time, or after an underrun (should no longer happen) */
            if (r < 0) {
                fprintf(stderr, "tb-audio: %s\n", pcm_get_error(pcm));
                if (++failures >= 25) {        /* half a second of nothing but errors */
                    fprintf(stderr, "tb-audio: giving up\n");
                    pcm_close(pcm);
                    return 1;
                }
            } else {
                failures = 0;
            }
            restarts += r == 0;
            writes++;
        }
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    /* the balance: playing N frames should take about N/48000 seconds */
    fprintf(stderr, "tb-audio: end: %llu ms of sound and %llu ms of filled-in silence in "
            "%lld ms, %llu periods, ran dry %llu times, %llu (re)starts\n",
            frames * 1000 / RATE, silent_frames * 1000 / RATE,
            (long long)(t1.tv_sec - t0.tv_sec) * 1000 + (t1.tv_nsec - t0.tv_nsec) / 1000000,
            writes, dry, restarts);
    pcm_close(pcm);
    return 0;
}
