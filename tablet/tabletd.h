/* tabletd - shared declarations. Protocol: docs/PLAN.md, section 5. */
#ifndef TABLETD_H
#define TABLETD_H

#include <stddef.h>
#include <stdint.h>

#define TABLETD_VERSION "0.1"
#define PROTO_VERSION 1
#define HDR_LEN 5            /* u8 type, u32 len */
#define MAX_PAYLOAD 1300000u
#define RECT_HDR 9           /* u16 x, y, w, h; u8 fmt */
#define COPY_LEN 12          /* u16 sx, sy, w, h, dx, dy */
#define INFO_ENCRYPTED 0x02  /* INFO flags: this connection is encrypted */
#define INFO_COPY 0x04       /* INFO flags: MSG_COPY is understood */
#define INFO_AUDIO 0x08      /* INFO flags: MSG_AUDIO is played (tb-audio is ready) */
#define INFO_HOME 0x10       /* INFO flags: MSG_HOME is understood */
#define INFO_ADDRESSES 0x20  /* INFO flags: MSG_ADDRESSES is kept (--learn-file) */
#define ADDRESSES_MAX 4      /* MSG_ADDRESSES: at most this many addresses */
#define AUDIO_HDR 6          /* u32 rate, u8 channels, u8 reserved */
#define AUDIO_MAX_LEN (AUDIO_HDR + 48000 / 10 * 2 * 2)   /* at most 100 ms */
#define NONCE_LEN 16
#define MAX_SLOTS 5

enum {
    MSG_RECT = 0x01, MSG_PRESENT = 0x02, MSG_BACKLIGHT = 0x03,
    MSG_TIME = 0x04, MSG_PING = 0x05, MSG_AUTH = 0x06,
    MSG_COPY = 0x07,      /* u16 sx, sy, w, h, dx, dy: move an area (scrolling) */
    MSG_AUDIO = 0x08,     /* u32 rate, u8 channels, u8 0, S16LE samples (laptop's sound) */
    MSG_HOME = 0x09,      /* no payload: end the remote control, back to the start screen */
    MSG_ADDRESSES = 0x0A, /* u16 port, u8 count (1..4), count x 16-byte IPv6 address: where
                             the tablet calls the laptop from another network */
    MSG_INFO = 0x41, MSG_ACK = 0x42, MSG_TOUCH = 0x43,
    MSG_KEY = 0x44, MSG_STATUS = 0x45, MSG_PONG = 0x46,
};

enum { FMT_RGB565 = 0, FMT_RGB565_LZ4 = 1 };
enum { TOUCH_UP = 0, TOUCH_DOWN = 1, TOUCH_MOVE = 2 };

/* secure.c: encrypted channel for the direct Wi-Fi link */
#define SEC_KEY_LEN 32
#define SEC_HELLO_LEN 36          /* 4-byte magic + X25519 public key */
#define SEC_MAC_LEN 16
#define SEC_MAX_PLAIN 65536       /* plaintext bytes per record */
#define SEC_FIRST_MAX 128         /* the client's first record must be small */
struct sec {
    uint8_t k_rx[32], k_tx[32];
    uint64_t n_rx, n_tx;
};
int sec_load_key(const char *path, uint8_t key[SEC_KEY_LEN]);
/* checks the client hello, writes the server hello and derives the keys */
int sec_server_handshake(struct sec *s, const uint8_t psk[SEC_KEY_LEN],
                         const uint8_t hello[SEC_HELLO_LEN],
                         uint8_t reply[SEC_HELLO_LEN]);
/* writes one record (4 + len + 16 bytes) to out; returns its size */
size_t sec_seal(struct sec *s, uint8_t *out, const uint8_t *plain, size_t len);
/* decrypts one record body; returns the plaintext length or -1 */
long sec_open(struct sec *s, uint8_t *plain, const uint8_t hdr[4],
              const uint8_t *body, size_t body_len);
void sec_wipe(struct sec *s);

/* proto.c */
uint16_t rd16(const uint8_t *p);
uint32_t rd32(const uint8_t *p);
void wr16(uint8_t *p, uint16_t v);
void wr32(uint8_t *p, uint32_t v);
/* 0 if a laptop-to-tablet frame of this type may have this length, else -1 */
int proto_check_len(uint8_t type, uint32_t len);
/* writes all bytes; 0 on success, -1 on error or timeout */
int send_all(int fd, const void *data, size_t n);

/* fb.c */
struct fb {
    int fd;
    uint8_t *map;            /* NULL: fall back to pwrite */
    size_t map_len;
    uint32_t w, h;           /* visible size in pixels */
    uint32_t line;           /* bytes per row */
    uint32_t yoff;           /* first visible row inside the buffer */
    uint32_t rshift, gshift, bshift, fill;  /* 32-bit pixel layout */
    uint32_t rtab[32], gtab[64], btab[32];  /* RGB565 parts -> 32-bit pixel */
    int is_file;
    uint8_t *row;            /* one converted row */
};
/* geom "WxH" selects a plain file as framebuffer (tests); NULL = real fbdev */
int fb_open(struct fb *fb, const char *path, const char *geom);
/* draws w*h RGB565 pixels (little-endian u16) at x, y; caller checked bounds */
int fb_rect565(struct fb *fb, uint32_t x, uint32_t y, uint32_t w, uint32_t h,
               const uint8_t *px);
void fb_pan(struct fb *fb);
/* moves the w x h area at (sx, sy) to (dx, dy); areas may overlap */
int fb_copy(struct fb *fb, uint32_t sx, uint32_t sy, uint32_t w, uint32_t h,
            uint32_t dx, uint32_t dy);

/* lz4dec.c */
/* decodes one LZ4 block; 0 only if exactly out_len bytes were produced */
int lz4_decode(const uint8_t *in, size_t in_len, uint8_t *out, size_t out_len);

/* font.c: drawing for the status page */
enum { ROT_NONE = 0, ROT_CCW = 1, ROT_CW = 2 };
struct canvas {
    uint8_t *px;             /* fb_w * fb_h pixels RGB565, framebuffer layout */
    uint32_t fb_w, fb_h;
    int rot;                 /* how the canvas is turned inside the buffer */
};
struct rgb { uint8_t r, g, b; };
struct glyph {
    uint32_t cp;             /* Unicode code point */
    int16_t x, y;            /* ink box relative to pen position and baseline */
    uint16_t w, h, adv;
    uint32_t off;            /* into glyph_pixels */
};
struct face { const struct glyph *g; int n; int ascent; };
extern const struct face face_clock, face_text;
uint32_t canvas_width(const struct canvas *c);
uint32_t canvas_height(const struct canvas *c);
uint16_t rgb565(struct rgb c);
void canvas_gradient(struct canvas *c, struct rgb top, struct rgb bottom);
void canvas_rect(struct canvas *c, int x, int y, int w, int h, struct rgb colour);
/* filled, with anti-aliased corners of radius r */
void canvas_round_rect(struct canvas *c, int x, int y, int w, int h, int r, struct rgb colour);
/* mixes a colour into one pixel with coverage a (0..15) */
void canvas_blend(struct canvas *c, int x, int y, struct rgb colour, unsigned a);
int text_width(const struct face *f, const char *utf8);
/* baseline at y; clipped at the edges; returns the pen position after it */
int canvas_text(struct canvas *c, const struct face *f, int x, int y, struct rgb colour,
                const char *utf8);

/* ui.c: shared by tb-home and tabletd */
#define UI_MARGIN 28
extern const struct rgb UI_BG_TOP, UI_BG_BOTTOM, UI_TEXT_MAIN, UI_TEXT_SUB, UI_TEXT_DIM;
extern const struct rgb UI_LINE, UI_AMBER, UI_GREEN, UI_RED, UI_ACCENT;
extern const struct rgb UI_CARD, UI_CARD_EDGE, UI_CARD_PRESSED;
/* 0 if the battery could be read: percent 0..100, charging 0/1, tenths of a degree */
int ui_battery(const char *dir, long *cap, int *charging, long *temp);
/* IPv4 address of the interface, or "" */
void ui_address(const char *iface, char *out, size_t size);
/* local seconds since 1970 -> "HH:MM" and "Montag, 16. November";
   returns a number that changes every minute */
long long ui_time_texts(long long local, char *clock, size_t clock_size, char *date,
                        size_t date_size);
/* status bar: Wi-Fi left, battery right (cap < 0: unknown), line below */
void ui_header(struct canvas *c, const char *addr, long cap, int charging);
/* UTC offset in seconds that tabletd saved from the laptop's TIME message */
int ui_tz_load(const char *path, int32_t *offset);
void ui_tz_save(const char *path, int32_t offset);

/* input.c */
typedef void (*touch_fn)(void *ctx, int slot, int state, int x, int y);
typedef void (*key_fn)(void *ctx, int code, int state);

struct contact { int used, x, y, id; };

struct input_dev {
    int fd;
    uint8_t buf[16 * 64];
    size_t have;
    int mode_a;                       /* saw SYN_MT_REPORT: protocol A */
    int cur_slot;                     /* protocol B */
    struct contact slot[MAX_SLOTS];   /* state already reported */
    struct contact next[MAX_SLOTS];   /* protocol B: state being collected */
    int touched[MAX_SLOTS];           /* protocol B: slot changed in frame */
    struct contact frame[MAX_SLOTS];  /* protocol A: contacts of this frame */
    int nframe;
    struct contact cur;               /* protocol A: contact being collected */
    int cur_has_pos;
};
int input_open(struct input_dev *d, const char *path);
/* reads what is available; returns -1 if the device is gone */
int input_read(struct input_dev *d, int max_x, int max_y,
               touch_fn on_touch, key_fn on_key, void *ctx);

#endif
