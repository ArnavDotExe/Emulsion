"""Draws the three demo scenes the app's films tab previews each film on.

Procedural artwork of our own (no photographs): a landscape, a backlit portrait and a still life
with a grey ramp and colour patches. Everything is drawn from soft-edged shapes and light, then
given a little lens softness and sensor noise so a film's contrast, colour and grain read as they
do on a photo.

    python tools/demo_images/make_demo_images.py android/app/src/main/assets/demo

Output: landscape.png, portrait.png, still_life.png, 360 x 480 each (3:4 upright, like the frame).
Needs only numpy. Seeded, so the files are reproducible.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import numpy as np

W, H = 360, 480
YY, XX = np.mgrid[0:H, 0:W].astype(np.float32)


def smooth(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian blur (edge-clamped), for 2-D or HxWxC arrays."""
    if sigma <= 0:
        return img
    radius = int(sigma * 3 + 1)
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    k /= k.sum()
    out = img
    for axis in (0, 1):
        pad = [(0, 0)] * out.ndim
        pad[axis] = (radius, radius)
        p = np.pad(out, pad, mode="edge")
        acc = np.zeros_like(out)
        for i, w in enumerate(k):
            sl = [slice(None)] * out.ndim
            sl[axis] = slice(i, i + out.shape[axis])
            acc += w * p[tuple(sl)]
        out = acc
    return out


def noise(rng: np.random.Generator, sigma: float, scale: float = 1.0) -> np.ndarray:
    n = blur(rng.standard_normal((H, W)).astype(np.float32), sigma)
    return n / (n.std() + 1e-6) * scale


def col(hex_: str) -> np.ndarray:
    return np.array([int(hex_[i:i + 2], 16) / 255.0 for i in (1, 3, 5)], np.float32)


def paint(img: np.ndarray, mask: np.ndarray, color, light: np.ndarray | None = None) -> None:
    """Blend [color] (3-vector or HxWx3) into [img] under [mask] (0..1), optionally lit by [light]."""
    c = np.asarray(color, np.float32)
    if c.ndim == 1:
        c = np.broadcast_to(c, img.shape)
    if light is not None:
        c = np.clip(c * np.asarray(light, np.float32)[..., None], 0, 1)
    m = mask[..., None]
    img[:] = img * (1 - m) + c * m


def ellipse(cx, cy, rx, ry, soft=1.5, angle=0.0) -> np.ndarray:
    a = np.deg2rad(angle)
    dx, dy = XX - cx, YY - cy
    u = dx * np.cos(a) + dy * np.sin(a)
    v = -dx * np.sin(a) + dy * np.cos(a)
    d = np.sqrt((u / rx) ** 2 + (v / ry) ** 2)
    return 1.0 - smooth(1.0 - soft / min(rx, ry), 1.0 + soft / min(rx, ry), d)


def rect(x0, y0, x1, y1, soft=1.2) -> np.ndarray:
    mx = smooth(x0 - soft, x0 + soft, XX) * (1 - smooth(x1 - soft, x1 + soft, XX))
    my = smooth(y0 - soft, y0 + soft, YY) * (1 - smooth(y1 - soft, y1 + soft, YY))
    return mx * my


def finish(img: np.ndarray, rng: np.random.Generator, softness: float = 0.7, grain: float = 0.012) -> np.ndarray:
    img = blur(img, softness)
    # A hint of vignette and sensor noise, as a photo has.
    r = np.sqrt(((XX - W / 2) / (W / 2)) ** 2 + ((YY - H / 2) / (H / 2)) ** 2)
    img = img * (1 - 0.10 * smooth(0.6, 1.4, r))[..., None]
    img = img + rng.standard_normal(img.shape).astype(np.float32) * grain
    return np.clip(img, 0, 1)


def landscape(rng: np.random.Generator) -> np.ndarray:
    img = np.zeros((H, W, 3), np.float32)
    horizon = 0.50 * H
    # Sky: blue overhead, warm and pale at the horizon, a low sun with a glow and a few clouds.
    t = np.clip(YY / horizon, 0, 1)[..., None]
    img[:] = col("#3f78c7") * (1 - t) ** 1.2 + col("#f4d6a6") * (1 - (1 - t) ** 1.2)
    sun = np.exp(-(((XX - 250) ** 2 + (YY - 0.40 * H) ** 2) / (2 * 55.0 ** 2)))
    img += sun[..., None] * col("#ffb860") * 0.55
    clouds = smooth(0.15, 0.75, blur(noise(rng, 14, 1.0), 4) + 0.2 * (1 - t[..., 0])) * (YY < 0.46 * H)
    paint(img, clouds * 0.55, col("#fff1e0"))
    # Distant hills in three bluish layers.
    for i, (base, amp, c) in enumerate([(0.47, 26, "#7f9fc4"), (0.50, 22, "#5f8a8a"), (0.53, 18, "#47734f")]):
        ridge = base * H + amp * np.sin(XX / (70 - i * 14) + i * 1.7) + 0.5 * amp * np.sin(XX / (23 + i * 5) + i)
        paint(img, smooth(-1.5, 1.5, YY - ridge), col(c))
    # A lake with the sky reflected and soft ripples.
    lake_top, lake_bot = 0.545 * H, 0.66 * H
    lake = rect(0, lake_top, W, lake_bot, 1.0)
    refl = img[np.clip((2 * lake_top - YY).astype(int), 0, H - 1), np.arange(W)[None, :], :]
    ripple = 0.04 * np.sin(YY * 1.3 + noise(rng, 5, 3))[..., None]
    paint(img, lake, np.clip(refl * 0.8 + ripple + col("#10304c") * 0.2, 0, 1))
    # Meadow: greens and yellows with texture.
    meadow = smooth(lake_bot - 1, lake_bot + 1.5, YY)
    tex = blur(noise(rng, 1.6, 1.0), 0.6)
    depth = np.clip((YY - lake_bot) / (H - lake_bot), 0, 1)[..., None]
    grass = col("#6d9b2f") * (1 - depth) + col("#3f6a1c") * depth
    grass = grass * (1 + 0.14 * tex[..., None]) + smooth(0.4, 1.2, tex + noise(rng, 9, 0.7))[..., None] * col("#d9b441") * 0.35
    paint(img, meadow, np.clip(grass, 0, 1))
    # A red barn with a white-trimmed door and a grey roof.
    paint(img, rect(60, 0.60 * H, 150, 0.745 * H), col("#b5322a"))
    roof = smooth(-1.5, 1.5, YY - (0.60 * H - 0.42 * np.abs(XX - 105) * 0.5 - 6)) * (1 - smooth(-1.5, 1.5, YY - 0.60 * H)) * rect(56, 0, 154, H)
    paint(img, roof, col("#5b5754"))
    paint(img, rect(92, 0.665 * H, 118, 0.745 * H), col("#f3eadb"))
    paint(img, rect(96, 0.672 * H, 114, 0.745 * H), col("#8e231e"))
    # A tree: trunk and layered canopy, backlit.
    paint(img, rect(268, 0.60 * H, 280, 0.80 * H), col("#4b3322"))
    for cx, cy, r, c in [(274, 0.50 * H, 52, "#2f5a22"), (250, 0.545 * H, 38, "#3b6b27"), (300, 0.54 * H, 40, "#36652a"), (274, 0.46 * H, 36, "#4a7a2d")]:
        lit = 0.78 + 0.35 * smooth(-60, 60, (XX - cx) * 0.7 - (YY - cy) * 0.5)
        paint(img, ellipse(cx, cy, r, r * 0.85, 3.0) * (0.92 + 0.08 * noise(rng, 2, 1)), col(c), lit)
    # Wildflowers in the foreground: red, white, violet and yellow dots.
    palette = [col("#d8352c"), col("#f4f1e8"), col("#8a59c9"), col("#f2c230")]
    for _ in range(95):
        x, y = rng.uniform(0, W), rng.uniform(0.70 * H, H)
        r = 2.0 + (y / H) * 2.2
        paint(img, ellipse(x, y, r, r, 1.0), palette[int(rng.integers(0, 4))])
    return finish(img, rng, 0.8)


def portrait(rng: np.random.Generator) -> np.ndarray:
    img = np.zeros((H, W, 3), np.float32)
    # Background: soft green-teal foliage with warm bokeh discs, lit from the upper right.
    bg = np.stack([blur(noise(rng, 20, 1), 6) * 0.5 + 0.5 for _ in range(3)], -1)
    img[:] = col("#3c5a45") * (0.7 + 0.5 * bg) + col("#c8a45a") * 0.18 * smooth(0.3, 1.1, (XX / W) - (YY / H) * 0.3)[..., None]
    palette = [col("#ffe7a8"), col("#f4c77a"), col("#bfe3b0"), col("#fff6df")]
    for _ in range(26):
        cx, cy, r = rng.uniform(0, W), rng.uniform(0, 0.75 * H), rng.uniform(10, 26)
        paint(img, ellipse(cx, cy, r, r, 2.0) * 0.34, palette[int(rng.integers(0, 4))])
    img = blur(img, 7)
    # Shoulders: a denim-blue jacket over a cream shirt.
    paint(img, ellipse(W / 2, 1.02 * H, 205, 150, 3.0), col("#2c4a6e"), 0.78 + 0.35 * smooth(-200, 200, XX - W / 2))
    paint(img, ellipse(W / 2, 0.80 * H, 62, 46, 3.0), col("#efe2cf"), 0.9 + 0.1 * smooth(-60, 60, XX - W / 2))
    # Neck, in shadow beneath the chin.
    paint(img, rect(W / 2 - 34, 0.57 * H, W / 2 + 34, 0.80 * H, 6.0), col("#b8805e"), 0.70 + 0.30 * smooth(-40, 40, XX - W / 2 + 8))
    # Hair behind the head: dark brown with a warm rim from the backlight.
    paint(img, ellipse(W / 2, 0.40 * H, 118, 128, 4.0), col("#2a1a12"), 0.85 + 0.45 * smooth(40, 120, XX - W / 2 + 20 * np.sin(YY / 18)))
    # Face: warm skin with form shading, cheeks flushed, light from the right.
    fx, fy = W / 2 + 2, 0.40 * H
    face = ellipse(fx, fy, 82, 106, 4.0)
    shade = 0.72 + 0.40 * smooth(-90, 90, (XX - fx) * 0.9 - (YY - fy) * 0.2)
    skin = col("#e6b08e") * np.ones((H, W, 1), np.float32)
    skin = skin + ellipse(fx - 44, fy + 28, 24, 16, 8.0)[..., None] * col("#e58a7e") * 0.35
    skin = skin + ellipse(fx + 44, fy + 28, 24, 16, 8.0)[..., None] * col("#e58a7e") * 0.30
    paint(img, face, np.clip(skin, 0, 1), shade)
    # Features: brows, eyes, nose shadow, lips.
    for sx in (-1, 1):
        ex = fx + sx * 34
        paint(img, ellipse(ex, fy - 18, 22, 4.5, 1.5, angle=sx * -6), col("#3a2418"))
        paint(img, ellipse(ex, fy - 2, 14, 7, 1.2), col("#f4ece4"))
        paint(img, ellipse(ex, fy - 2, 6.5, 6.5, 1.0), col("#4a6b3c"))
        paint(img, ellipse(ex, fy - 2, 3, 3, 0.8), col("#0f0b09"))
        paint(img, ellipse(ex + 2, fy - 4, 1.6, 1.6, 0.6), col("#ffffff"))
        paint(img, ellipse(ex, fy - 9, 15, 2.2, 1.2), col("#9a6a55"))
    paint(img, ellipse(fx + 5, fy + 30, 9, 17, 5.0), col("#bd8466"), 0.9)
    paint(img, ellipse(fx, fy + 62, 25, 7, 1.5), col("#b44a4f"))
    paint(img, ellipse(fx, fy + 59, 20, 3, 1.2), col("#cf6a6a"))
    paint(img, ellipse(fx + 3, fy + 90, 36, 12, 8.0), col("#9a6a50"), 0.8)
    # Hair in front: a fringe and strands framing the face.
    paint(img, ellipse(fx - 6, fy - 94, 90, 36, 4.0) * (1 - smooth(fy - 76, fy - 50, YY)), col("#2f1d14"), 0.9 + 0.4 * smooth(-60, 80, XX - fx))
    for sx in (-1, 1):
        paint(img, ellipse(fx + sx * 86, fy + 20, 17, 105, 4.0), col("#2a1a12"), 0.85 + 0.5 * smooth(-30, 30, sx * (XX - fx)))
    # A warm rim light on the right edge, as from the sun behind.
    rim = ellipse(fx + 6, fy, 88, 112, 5.0) * (1 - ellipse(fx - 4, fy, 82, 106, 5.0)) * smooth(0, 70, XX - fx)
    img += rim[..., None] * col("#ffc27a") * 0.5
    return finish(img, rng, 0.8, 0.014)


def still_life(rng: np.random.Generator) -> np.ndarray:
    img = np.zeros((H, W, 3), np.float32)
    # A neutral grey backdrop falling off at the edges, and a wooden table.
    img[:] = col("#8c8f93") * (1.05 - 0.35 * smooth(0, 1.2, np.sqrt(((XX - W / 2) / (W / 2)) ** 2 + ((YY - 0.3 * H) / (0.7 * H)) ** 2)))[..., None]
    grain = np.sin((YY * 0.9 + 6 * np.sin(XX / 38) + 3 * noise(rng, 3, 1)) * 0.9) * 0.5 + 0.5
    wood = col("#9a6a3f") * (0.82 + 0.28 * grain[..., None]) * (1.15 - 0.35 * np.clip((YY - 0.47 * H) / (0.5 * H), 0, 1))[..., None]
    paint(img, smooth(0.47 * H - 1, 0.47 * H + 1, YY), np.clip(wood, 0, 1))

    def sphere(cx, cy, r, c, shine=0.5):
        nx, ny = (XX - cx) / r, (YY - cy) / r
        light = np.clip(0.45 + 0.65 * (-nx * 0.5 - ny * 0.65 + np.sqrt(np.clip(1 - nx * nx - ny * ny, 0, 1)) * 0.55), 0.18, 1.25)
        paint(img, ellipse(cx + 6, cy + r * 0.92, r * 1.05, r * 0.28, 5.0) * 0.5, col("#2b1c10"))
        paint(img, ellipse(cx, cy, r, r, 1.5), c, light)
        paint(img, ellipse(cx - r * 0.35, cy - r * 0.4, r * 0.18, r * 0.12, 1.5) * shine, col("#ffffff"))

    sphere(78, 0.40 * H, 44, col("#c42a22"))                        # red apple
    paint(img, rect(77, 0.40 * H - 60, 80, 0.40 * H - 38), col("#4b3322"))
    paint(img, ellipse(92, 0.40 * H - 58, 15, 7, 1.5, -25), col("#4f8a2b"))
    sphere(180, 0.42 * H, 40, col("#f2cf2b"), 0.4)                   # lemon
    paint(img, ellipse(217, 0.42 * H, 9, 6, 1.5), col("#d8b422"))
    # A white plate and a blue cup of coffee.
    paint(img, ellipse(278, 0.495 * H + 9, 60, 13, 3.0), col("#e8e4dc"), 0.9 + 0 * XX)
    paint(img, ellipse(278, 0.495 * H, 56, 11, 3.0), col("#f6f3ec"))
    cup = rect(246, 0.33 * H, 312, 0.47 * H, 2.0)
    paint(img, cup, col("#2f62a8"), 0.62 + 0.55 * smooth(-33, 33, XX - 262))
    paint(img, ellipse(279, 0.33 * H, 33, 9, 2.0), col("#1e4577"))
    paint(img, ellipse(279, 0.335 * H, 28, 6, 2.0), col("#6d4a2c"))
    # Greens: a leaf spray in the foreground.
    for i in range(5):
        paint(img, ellipse(54 + i * 22, 0.595 * H - i * 7, 30, 8, 1.5, -40 + i * 18), col(["#3f7d33", "#5a9a3c", "#2f6a2e", "#6cab45", "#3b8040"][i]), 0.8 + 0.2 * (i % 2) + 0 * XX)
    # Colour patches and a grey ramp along the bottom, to judge a film's colour and tone.
    patches = ["#735244", "#c29682", "#627a9d", "#576c43", "#8580b1", "#67bdaa",
               "#d6802b", "#505ba6", "#c15a63", "#5e3c6c", "#9dbc40", "#e0a32e"]
    pw = W / len(patches)
    for i, c in enumerate(patches):
        paint(img, rect(i * pw + 1, 0.70 * H, (i + 1) * pw - 1, 0.775 * H, 0.8), col(c))
    ramp = np.clip(XX / W, 0, 1)[..., None] * np.ones(3, np.float32)
    paint(img, rect(0, 0.80 * H, W, 0.875 * H, 0.8), ramp)
    for i, c in enumerate(["#ff0000", "#ff7a00", "#ffee00", "#00b140", "#00a3e0", "#2a2fd8", "#a020a0", "#ffffff"]):
        paint(img, rect(i * (W / 8) + 1, 0.90 * H, (i + 1) * (W / 8) - 1, 0.97 * H, 0.8), col(c))
    return finish(img, rng, 0.6, 0.010)


def write_png(path: Path, rgb: np.ndarray) -> None:
    data = (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)
    raw = b"".join(b"\x00" + data[y].tobytes() for y in range(data.shape[0]))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", data.shape[1], data.shape[0], 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, draw, seed in [("landscape", landscape, 11), ("portrait", portrait, 22), ("still_life", still_life, 33)]:
        write_png(out / f"{name}.png", draw(np.random.default_rng(seed)))
        print(name, (out / f"{name}.png").stat().st_size, "bytes")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "demo"))
