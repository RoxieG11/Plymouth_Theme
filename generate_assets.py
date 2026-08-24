#!/usr/bin/env python3
"""
Roxie Plymouth Theme — Asset Generator
========================================

Plymouth Script cannot draw arbitrary vector paths, gradients or blur at
boot time, and it cannot be trusted to have a nice font available inside
the initramfs. So instead of rendering the "ROXIE" wordmark live during
boot, we pre-render every pixel here, offline, at build time, using a
real font (Poppins Bold) and real Gaussian blur/bloom — then Plymouth
just flips through the resulting PNG frames like a flipbook. That keeps
the boot-time script to nothing but cheap sprite/opacity math, which is
what makes it safe to run on integrated GPUs.

Run this once (or whenever you want to tweak the look) with:

    python3 generate_assets.py

It writes everything straight into the Roxie/ theme folder, ready to be
copied to /usr/share/plymouth/themes/.

Requires: Pillow  (pip install --break-system-packages pillow)
Font used: Poppins-Bold.ttf (SIL Open Font License, freely redistributable)
"""

import math
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------------------
# Roxie palette — pure black + purple only, per spec. Do not add blue/green.
# ---------------------------------------------------------------------------
BLACK        = (0, 0, 0)
PRIMARY      = (139, 92, 246)    # #8b5cf6
SECONDARY    = (168, 85, 247)    # #a855f7
GLOW         = (192, 132, 252)   # #c084fc
WHITE        = (255, 255, 255)

OUT_DIR   = os.path.join(os.path.dirname(__file__), "Roxie")
LOGO_DIR  = os.path.join(OUT_DIR, "logo")
FONT_PATH = "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"

os.makedirs(LOGO_DIR, exist_ok=True)

WORD = "ROXIE"

# Everything below is rendered at a "4K-native" design scale so the theme
# never has to upscale a raster at runtime (Roxie.script only ever scales
# DOWN for 1080p/1440p via Image.Scale — downscaling stays crisp, upscaling
# a blurred/anti-aliased PNG does not). SCALE=1.0 was the original tuning
# pass; 2.2 pushes the same proportions up to full 4K-safe resolution.
SCALE = 2.2

FONT_SIZE = round(170 * SCALE)
TRACKING  = round(FONT_SIZE * 0.24)     # premium wordmark letter-spacing

REVEAL_LINE_FRAMES   = 6     # phase A: thin line draws itself outward
REVEAL_LETTER_FRAMES = 22    # phase B: line expands / resolves into letters
TOTAL_FRAMES = REVEAL_LINE_FRAMES + REVEAL_LETTER_FRAMES

MAX_BLUR = 11.0 * SCALE        # px of blur a letter has the instant it appears
LINE_FADE_START = 0.55         # (0..1 within phase B) when the draw-line starts dissolving


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_color(c1, c2, t):
    return tuple(int(round(lerp(a, b, t))) for a, b in zip(c1, c2))


def smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------------------
# Step 1 — lay out each glyph's tight bounding box + baseline position
# ---------------------------------------------------------------------------
font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
ascent, descent = font.getmetrics()

glyph_widths = []
for ch in WORD:
    l, t, r, b = font.getbbox(ch)
    glyph_widths.append(r - l)

text_width = sum(glyph_widths) + TRACKING * (len(WORD) - 1)
glyph_h = ascent + descent

PAD_X = round(260 * SCALE)   # room for the draw-line overshoot + bloom bleed
PAD_Y = round(230 * SCALE)

CANVAS_W = text_width + PAD_X * 2
CANVAS_H = glyph_h + PAD_Y * 2

baseline_y = PAD_Y
# Each glyph mask needs enough blank margin around it to blur into without
# clipping (GaussianBlur bleeds roughly 2-3x its radius) — this margin must
# scale with MAX_BLUR, not be a fixed constant, or letters clip at high SCALE.
GLYPH_PAD = round(MAX_BLUR * 2.5)
letters = []   # list of dicts: ch, x (canvas-space left), width, glyph image (L mask, tight)
cursor_x = PAD_X
ink_tops, ink_bottoms = [], []
for ch, w in zip(WORD, glyph_widths):
    glyph_img = Image.new("L", (w + GLYPH_PAD * 2, glyph_h + GLYPH_PAD * 2), 0)
    gdraw = ImageDraw.Draw(glyph_img)
    gdraw.text((GLYPH_PAD - font.getbbox(ch)[0], GLYPH_PAD), ch, font=font, fill=255)
    letters.append({"ch": ch, "x": cursor_x, "w": w, "mask": glyph_img})
    cursor_x += w + TRACKING
    _, top, _, bottom = font.getbbox(ch)
    ink_tops.append(top)
    ink_bottoms.append(bottom)

# Vertical center of the visible letterforms (cap-height midline) — this is
# where the draw-line sits, so it looks like it's striking through the
# optical middle of the wordmark rather than some arbitrary canvas row.
line_center_y = baseline_y + (min(ink_tops) + max(ink_bottoms)) / 2

text_left = PAD_X
text_right = PAD_X + text_width
text_center_x = (text_left + text_right) / 2

# Full-word horizontal gradient, built across the WHOLE canvas (not just the
# tight text span) and clamped at the ends, so that any glyph's padded mask
# — including its blur bleed margin — can be cropped from it safely no
# matter how large GLYPH_PAD is.
def canvas_wide_gradient(canvas_w, height, c1, c2, span_left, span_right):
    grad = Image.new("RGB", (canvas_w, height))
    px = grad.load()
    span = max(1, span_right - span_left)
    for x in range(canvas_w):
        t = (x - span_left) / span
        t = max(0.0, min(1.0, t))
        col = lerp_color(c1, c2, t)
        for y in range(height):
            px[x, y] = col
    return grad


word_gradient = canvas_wide_gradient(CANVAS_W, CANVAS_H, PRIMARY, SECONDARY, text_left, text_right)


def letter_color_slice(letter):
    """Crop the shared word-gradient to this letter's padded mask footprint."""
    x0 = letter["x"] - GLYPH_PAD
    y0 = baseline_y - GLYPH_PAD
    x1 = x0 + letter["mask"].width
    y1 = y0 + letter["mask"].height
    return word_gradient.crop((x0, y0, x1, y1))


# ---------------------------------------------------------------------------
# Step 2 — the two "line" assets: a horizontal draw-line and a vertical pen
# ---------------------------------------------------------------------------
def _tint_toward_glow(img, strength=0.9):
    """Blend a white-core soft shape toward the glow colour at its edges,
    keeping a hot white center — used for both line assets below."""
    tint = Image.new("RGBA", img.size, GLOW + (0,))
    alpha = img.split()[3]
    tint.putalpha(alpha.point(lambda a: int(a * strength)))
    return Image.alpha_composite(tint, img)


def make_horizontal_bar(width, core_h=round(7 * SCALE), blur=round(9 * SCALE)):
    """The line that draws itself across the center (phase A), and which
    then sits behind the word dissolving away as letters solidify (phase B)."""
    width = max(2, width)
    pad = blur * 3
    img = Image.new("RGBA", (width + pad * 2, core_h + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([pad, pad, pad + width, pad + core_h], fill=WHITE + (255,))
    img = img.filter(ImageFilter.GaussianBlur(blur))
    return _tint_toward_glow(img, 0.85)


def make_vertical_pen(height, core_w=round(5 * SCALE), blur=round(10 * SCALE)):
    """The bright accent that sweeps left -> right while letters resolve
    out of the line (phase B) — like a pen tracing the word."""
    pad = blur * 3
    img = Image.new("RGBA", (core_w + pad * 2, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([pad, 0, pad + core_w, height], fill=WHITE + (255,))
    img = img.filter(ImageFilter.GaussianBlur(blur))
    return _tint_toward_glow(img, 0.75)


pen_asset = make_vertical_pen(glyph_h + 70)
full_bar_asset = make_horizontal_bar(text_width)


# ---------------------------------------------------------------------------
# Step 3 — render each of the flipbook frames
# ---------------------------------------------------------------------------
def render_frame(idx):
    frame = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))

    if idx < REVEAL_LINE_FRAMES:
        # Phase A — a thin horizontal line draws itself outward from the
        # center until it spans the full width the word will occupy.
        p = smoothstep((idx + 1) / REVEAL_LINE_FRAMES)
        bar_w = max(2, int(text_width * p))
        line_alpha = smoothstep(min(1.0, (idx + 1) / 2.0))  # quick fade-in, no pop
        bar = make_horizontal_bar(bar_w)
        lx = int(text_center_x - bar.width / 2)
        ly = int(line_center_y - bar.height / 2)
        layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        layer.alpha_composite(bar, (lx, ly))
        a = layer.split()[3].point(lambda a, m=line_alpha: int(a * m))
        layer.putalpha(a)
        frame = Image.alpha_composite(frame, layer)
        return frame

    # Phase B — the line sweeps left->right; letters resolve out of it.
    bidx = idx - REVEAL_LINE_FRAMES
    s = (bidx + 1) / REVEAL_LETTER_FRAMES               # 0..1 across phase B
    sweep_x = text_left + s * text_width

    glow_layer   = Image.new("L", frame.size, 0)     # accumulated alpha, for bloom
    letters_rgba = Image.new("RGBA", frame.size, (0, 0, 0, 0))

    for letter in letters:
        center = letter["x"] + letter["w"] / 2
        # local reveal progress: starts a bit before the sweep reaches the
        # glyph and finishes a bit after, so neighbours overlap smoothly.
        local = (sweep_x - (center - letter["w"] * 0.9)) / (letter["w"] * 1.8)
        local = smoothstep(local)
        if local <= 0.001:
            continue
        blur_r = MAX_BLUR * (1 - local)
        mask = letter["mask"].point(lambda a, m=local: int(a * m))
        if blur_r > 0.15:
            mask = mask.filter(ImageFilter.GaussianBlur(blur_r))
        colour = letter_color_slice(letter).convert("RGBA")
        colour.putalpha(mask)
        px = letter["x"] - GLYPH_PAD
        py = baseline_y - GLYPH_PAD
        letters_rgba.alpha_composite(colour, (px, py))
        glow_layer.paste(Image.eval(mask, lambda a: a), (px, py), mask)

    # Bloom: blur the accumulated letter alpha heavily, tint with GLOW.
    bloom_mask = glow_layer.filter(ImageFilter.GaussianBlur(26 * SCALE))
    bloom = Image.new("RGBA", frame.size, GLOW + (0,))
    bloom.putalpha(bloom_mask.point(lambda a: int(a * 0.55)))
    frame = Image.alpha_composite(frame, bloom)

    # The full horizontal line, still present at the start of phase B,
    # dissolving away as the letters solidify and take over.
    bar_fade = 1.0
    if s > LINE_FADE_START:
        bar_fade = 1.0 - smoothstep((s - LINE_FADE_START) / (1 - LINE_FADE_START))
    if bar_fade > 0.01:
        layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        blx = int(text_center_x - full_bar_asset.width / 2)
        bly = int(line_center_y - full_bar_asset.height / 2)
        layer.alpha_composite(full_bar_asset, (blx, bly))
        a = layer.split()[3].point(lambda a, m=bar_fade: int(a * m))
        layer.putalpha(a)
        frame = Image.alpha_composite(frame, layer)

    # The bright pen tracing left -> right, present only while the sweep
    # is actively moving across the word (fades out as it exits at the end).
    pen_fade = smoothstep(min(1.0, (bidx + 1) / 2.0))
    if s > 0.85:
        pen_fade *= 1.0 - smoothstep((s - 0.85) / 0.15)
    if pen_fade > 0.01:
        layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        plx = int(sweep_x - pen_asset.width / 2)
        ply = int(line_center_y - pen_asset.height / 2)
        layer.alpha_composite(pen_asset, (plx, ply))
        a = layer.split()[3].point(lambda a, m=pen_fade: int(a * m))
        layer.putalpha(a)
        frame = Image.alpha_composite(frame, layer)

    frame = Image.alpha_composite(frame, letters_rgba)
    return frame


for i in range(TOTAL_FRAMES):
    render_frame(i).save(os.path.join(LOGO_DIR, f"frame_{i:02d}.png"))

final_frame = render_frame(TOTAL_FRAMES - 1)
final_frame.save(os.path.join(OUT_DIR, "logo_final.png"))

# ---------------------------------------------------------------------------
# Step 4 — idle bloom layer (bigger, softer halo, breathes via opacity at runtime)
# ---------------------------------------------------------------------------
final_alpha = final_frame.split()[3]
idle_bloom_mask = final_alpha.filter(ImageFilter.GaussianBlur(42 * SCALE))
idle_glow = Image.new("RGBA", final_frame.size, GLOW + (0,))
idle_glow.putalpha(idle_bloom_mask.point(lambda a: int(a * 0.75)))
idle_glow.save(os.path.join(OUT_DIR, "logo_glow.png"))

# ---------------------------------------------------------------------------
# Step 5 — ambient particle sprite (soft glowing dot, reused many times)
# ---------------------------------------------------------------------------
def make_soft_dot(size, core_radius, blur, color, core_boost=None):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size // 2
    d.ellipse([c - core_radius, c - core_radius, c + core_radius, c + core_radius],
              fill=color + (255,))
    img = img.filter(ImageFilter.GaussianBlur(blur))
    if core_boost:
        hot = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        hd = ImageDraw.Draw(hot)
        hd.ellipse([c - core_boost, c - core_boost, c + core_boost, c + core_boost],
                    fill=WHITE + (235,))
        hot = hot.filter(ImageFilter.GaussianBlur(max(1, core_boost // 2)))
        img = Image.alpha_composite(img, hot)
    return img


particle = make_soft_dot(size=round(48 * SCALE), core_radius=round(5 * SCALE),
                          blur=round(7 * SCALE), color=GLOW)
particle.save(os.path.join(OUT_DIR, "particle.png"))

spinner_dot = make_soft_dot(size=round(64 * SCALE), core_radius=round(7 * SCALE),
                             blur=round(8 * SCALE), color=SECONDARY, core_boost=round(3 * SCALE))
spinner_dot.save(os.path.join(OUT_DIR, "spinner_dot.png"))

bullet = make_soft_dot(size=round(28 * SCALE), core_radius=round(5 * SCALE),
                        blur=round(3 * SCALE), color=GLOW, core_boost=round(2 * SCALE))
bullet.save(os.path.join(OUT_DIR, "bullet.png"))

print("Canvas:", CANVAS_W, "x", CANVAS_H)
print("Text width:", text_width)
print(f"Generated {TOTAL_FRAMES} reveal frames + logo_final, logo_glow, particle, spinner_dot, bullet")
