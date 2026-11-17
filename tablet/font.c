/*
 * Drawing for the status page: gradient, rectangles and anti-aliased text
 * into an RGB565 buffer in framebuffer layout (upright). The canvas the
 * caller draws on can be turned, so that the page is readable when the
 * tablet lies on its side.
 *
 * All arithmetic is in integers and fixed here, so that tests/test_status.py
 * can compute the expected page pixel by pixel.
 */
#include "tabletd.h"

#include <string.h>

#include "font_aa.h"

const struct face face_clock = { glyphs_clock, FACE_CLOCK_COUNT, FACE_CLOCK_ASCENT };
const struct face face_text = { glyphs_text, FACE_TEXT_COUNT, FACE_TEXT_ASCENT };

uint32_t canvas_width(const struct canvas *c)
{
    return c->rot == ROT_NONE ? c->fb_w : c->fb_h;
}

uint32_t canvas_height(const struct canvas *c)
{
    return c->rot == ROT_NONE ? c->fb_h : c->fb_w;
}

uint16_t rgb565(struct rgb c)
{
    return (uint16_t)((c.r >> 3) << 11 | (c.g >> 2) << 5 | c.b >> 3);
}

/* framebuffer byte offset of a canvas pixel, or -1 outside */
static long pixel_offset(const struct canvas *c, int x, int y)
{
    uint32_t fx, fy;

    if (x < 0 || y < 0 || (uint32_t)x >= canvas_width(c) || (uint32_t)y >= canvas_height(c))
        return -1;
    if (c->rot == ROT_CCW) {
        fx = (uint32_t)y;
        fy = c->fb_h - 1 - (uint32_t)x;
    } else if (c->rot == ROT_CW) {
        fx = c->fb_w - 1 - (uint32_t)y;
        fy = (uint32_t)x;
    } else {
        fx = (uint32_t)x;
        fy = (uint32_t)y;
    }
    return ((long)fy * c->fb_w + fx) * 2;
}

static void put(struct canvas *c, int x, int y, uint16_t v)
{
    long off = pixel_offset(c, x, y);

    if (off >= 0) {
        c->px[off] = (uint8_t)v;
        c->px[off + 1] = (uint8_t)(v >> 8);
    }
}

/* mixes colour into the pixel with coverage a (0..15) */
void canvas_blend(struct canvas *c, int x, int y, struct rgb fg, unsigned a)
{
    long off = pixel_offset(c, x, y);
    unsigned v, r, g, b;

    if (off < 0 || a == 0)
        return;
    v = (unsigned)c->px[off] | (unsigned)c->px[off + 1] << 8;
    r = v >> 11;
    g = (v >> 5) & 0x3f;
    b = v & 0x1f;
    r = r << 3 | r >> 2;
    g = g << 2 | g >> 4;
    b = b << 3 | b >> 2;
    r = (fg.r * a + r * (15 - a)) / 15;
    g = (fg.g * a + g * (15 - a)) / 15;
    b = (fg.b * a + b * (15 - a)) / 15;
    v = (r >> 3) << 11 | (g >> 2) << 5 | b >> 3;
    c->px[off] = (uint8_t)v;
    c->px[off + 1] = (uint8_t)(v >> 8);
}

void canvas_gradient(struct canvas *c, struct rgb top, struct rgb bottom)
{
    /* ordered 4x4 dither: RGB565 has too few steps for a smooth dark gradient */
    static const uint8_t bayer[4][4] = {
        { 0, 8, 2, 10 }, { 12, 4, 14, 6 }, { 3, 11, 1, 9 }, { 15, 7, 13, 5 },
    };
    int w = (int)canvas_width(c), h = (int)canvas_height(c);

    for (int y = 0; y < h; y++) {
        int r = top.r + (bottom.r - top.r) * y / (h - 1);
        int g = top.g + (bottom.g - top.g) * y / (h - 1);
        int b = top.b + (bottom.b - top.b) * y / (h - 1);

        for (int x = 0; x < w; x++) {
            int d = bayer[y & 3][x & 3];
            struct rgb px = {
                (uint8_t)(r + d / 2 > 255 ? 255 : r + d / 2),   /* 5 bits: steps of 8 */
                (uint8_t)(g + d / 4 > 255 ? 255 : g + d / 4),   /* 6 bits: steps of 4 */
                (uint8_t)(b + d / 2 > 255 ? 255 : b + d / 2),
            };

            put(c, x, y, rgb565(px));
        }
    }
}

void canvas_rect(struct canvas *c, int x, int y, int w, int h, struct rgb colour)
{
    uint16_t v = rgb565(colour);

    for (int j = 0; j < h; j++)
        for (int i = 0; i < w; i++)
            put(c, x + i, y + j, v);
}

/* next code point of a UTF-8 string (invalid bytes are returned as is) */
static uint32_t next_cp(const char **s)
{
    const unsigned char *p = (const unsigned char *)*s;
    uint32_t cp = *p++;

    if (cp >= 0xe0 && cp < 0xf0 && (p[0] & 0xc0) == 0x80 && (p[1] & 0xc0) == 0x80) {
        cp = (cp & 0x0f) << 12 | (uint32_t)(p[0] & 0x3f) << 6 | (p[1] & 0x3f);
        p += 2;
    } else if (cp >= 0xc0 && cp < 0xe0 && (p[0] & 0xc0) == 0x80) {
        cp = (cp & 0x1f) << 6 | (p[0] & 0x3f);
        p += 1;
    }
    *s = (const char *)p;
    return cp;
}

static const struct glyph *find(const struct face *f, uint32_t cp)
{
    for (int i = 0; i < f->n; i++)
        if (f->g[i].cp == cp)
            return &f->g[i];
    return cp == '?' ? NULL : find(f, '?');   /* unknown characters as "?" */
}

int text_width(const struct face *f, const char *s)
{
    int w = 0;

    while (*s) {
        const struct glyph *g = find(f, next_cp(&s));

        if (g)
            w += g->adv;
    }
    return w;
}

/* draws text with its baseline at y; returns the x after the last glyph */
int canvas_text(struct canvas *c, const struct face *f, int x, int y, struct rgb colour,
                const char *s)
{
    while (*s) {
        const struct glyph *g = find(f, next_cp(&s));
        const uint8_t *px;
        int stride;

        if (!g)
            continue;
        px = glyph_pixels + g->off;
        stride = (g->w + 1) / 2;
        for (int j = 0; j < g->h; j++)
            for (int i = 0; i < g->w; i++) {
                uint8_t byte = px[j * stride + i / 2];

                canvas_blend(c, x + g->x + i, y + g->y + j, colour,
                      i % 2 ? byte & 0x0f : byte >> 4);
            }
        x += g->adv;
    }
    return x;
}

/* coverage (0..15) of a corner pixel by a circle of radius r around (cx, cy),
   from 4x4 samples per pixel */
static unsigned corner_coverage(int px, int py, int cx, int cy, int r)
{
    unsigned inside = 0;

    for (int sy = 0; sy < 4; sy++)
        for (int sx = 0; sx < 4; sx++) {
            int dx = (px * 8 + sx * 2 + 1) - cx * 8, dy = (py * 8 + sy * 2 + 1) - cy * 8;

            inside += dx * dx + dy * dy <= r * r * 64;
        }
    return inside * 15 / 16;
}

void canvas_round_rect(struct canvas *c, int x, int y, int w, int h, int r, struct rgb colour)
{
    if (2 * r > w)
        r = w / 2;
    if (2 * r > h)
        r = h / 2;
    canvas_rect(c, x + r, y, w - 2 * r, h, colour);
    canvas_rect(c, x, y + r, r, h - 2 * r, colour);
    canvas_rect(c, x + w - r, y + r, r, h - 2 * r, colour);
    for (int j = 0; j < r; j++)
        for (int i = 0; i < r; i++) {
            /* the four corners share one quarter circle, mirrored */
            unsigned a = corner_coverage(i, j, r, r, r);

            canvas_blend(c, x + i, y + j, colour, a);
            canvas_blend(c, x + w - 1 - i, y + j, colour, a);
            canvas_blend(c, x + i, y + h - 1 - j, colour, a);
            canvas_blend(c, x + w - 1 - i, y + h - 1 - j, colour, a);
        }
}
