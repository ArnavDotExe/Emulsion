"""Local tone and contrast: Highlights, Shadows, Clarity and Texture, fitted to Lightroom's exports.

    python -m colorlab.local_contrast apply in.tif out.tif [--highlights -40] [--shadows 30]
                                                           [--clarity 40] [--texture 25]

All four are fitted to Lightroom's own exports of the spatial calibration charts
(``colorlab.spatial_calibration``). They work on sRGB-encoded H x W x 3 images (0..1), so they
run as shader passes around a look's LUT, like :mod:`colorlab.film`.

Clarity (:func:`clarity`) changes only luminance, carried to the channels as a ratio of linear
light, which keeps hue. It works on ``y = Y ** (1 / 2.2)`` around a Gaussian base ``M`` whose
sigma is 1/96 of the frame width, so it follows the frame: at 768, 1536 and 3072 px wide
Lightroom's Clarity has the same shape relative to the width.

    y' = M + T(M) + (1 + D(y)) * (y - M) + F(y) * (y - fine(y))

``D`` (detail around the base), ``F`` (the finest detail, sigma 1/1536 of the width) and the
base curve ``T`` are tables over ``y``, fitted with a smoothness penalty and scaled by a power
of the amount. Both directions are midtone-weighted, like Lightroom:

* Negative Clarity pulls midtones toward the base and leaves black, white and flat areas alone.
  Next to a bright area a dark one is lifted by a soft glow about 4% of the frame wide; the
  bright side barely moves.
* Positive Clarity raises fine detail, darkens the base's midtones (a flat 0.5 gray comes out
  near 0.45 at +100) and slightly lowers detail in the highlights.

A local Laplacian filter (:func:`local_laplacian`) was fitted first, for Clarity and for
Highlights/Shadows, and fits neither: it is halo-free by design, while Lightroom's Clarity has a
wide, one-sided halo (on a dark/bright edge it moved the bright side instead of the dark one),
and as a tone table it scored worse than the base-and-pixel table below. It is kept as a tested
filter.

Highlights and Shadows (:func:`highlights_shadows`) are one local tone stage: a change of ``y``
read from a table over the pixel (half blurred by 1 px, so the finest detail keeps more of its
contrast) and a Gaussian base of 1/32 of the frame width. Lightroom's change follows the frame
and depends on the neighbourhood: a flat patch next to a brighter or darker one is moved by a
gradient a few hundred px long, yet an edge gets no halo. Each slider has its own tables at 25,
50 and 100 per direction, interpolated in between. The change is carried to the channels in
sRGB-encoded values: luminance moves to the new value and each channel's difference from it is
scaled by the square root of the luminance ratio.

Texture (:func:`texture`) is a guided filter of fixed 16 px radius on Y, Cb and Cr (guided by
Y): it scales each channel's difference from its edge-preserving smooth. Its size doesn't
follow the frame, so previews must render it at the output resolution to match.

Measured residuals (mean CIEDE2000 against Lightroom's own exports) are recorded in
``docs/tdd/colorlab-phase1-core.tdd.md``.

The local Laplacian filter is from Paris, Hasinoff and Kautz, "Local Laplacian Filters:
Edge-aware Image Processing with a Laplacian Pyramid" (SIGGRAPH 2011), in the fast form with
evenly sampled reference levels that darktable uses (``src/common/locallaplacian.c``,
GPL-3.0-or-later, Copyright (C) 2016-2026 darktable developers): each output coefficient is
interpolated between the Laplacian pyramids of the image remapped at the two nearest reference
levels. Changes: plain NumPy with clamp-to-edge blurs and a normalized upsample, any remapping
function, an optional curve on the coarse residual, and no padding or preview modes. The guided
filter is from He, Sun and Tang, "Guided Image Filtering" (ECCV 2010).
"""
from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Callable

import numpy as np

from .colorspace import Transfer, decode, encode
from .errors import ParameterError
from .film import gaussian_blur

Remap = Callable[[np.ndarray, float], np.ndarray]

SRGB_LUMA = np.array([0.2126, 0.7152, 0.0722])
_YCC = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5], [0.5, -0.418688, -0.081312]])
_YCC_INV = np.linalg.inv(_YCC)
_BINOMIAL = np.array([1.0, 4.0, 6.0, 4.0, 1.0]) / 16.0

LUMA_GAMMA = 2.2  # clarity works on Y ** (1 / 2.2)
CLARITY_BASE_FRACTION = 1.0 / 96.0  # sigma of the base, as a fraction of the frame width
CLARITY_FINE_FRACTION = 1.0 / 1536.0  # sigma of the finest detail (at least 0.5 px)
_KNOTS = np.linspace(0.0, 1.0, 11)

# Tables at amount 100 over y = 0, 0.1, ..., 1, and the power of |amount / 100| that scales each.
CLARITY_TABLES = {
    -1: {"detail": ((0.05, -0.02, -0.19, -0.48, -0.74, -0.78, -0.57, -0.48, -0.43, -0.26, -0.06), 0.86),
         "fine": ((-0.08, -0.01, 0.06, 0.16, 0.21, 0.18, 0.08, -0.06, -0.16, -0.10, 0.0), 1.34),
         "base": ((0.0,) * 11, 1.0)},
    1: {"detail": ((-0.19, -0.09, 0.01, 0.09, 0.13, 0.12, 0.03, -0.11, -0.23, -0.22, -0.16), 0.98),
        "fine": ((-0.01, 0.07, 0.16, 0.25, 0.34, 0.43, 0.52, 0.59, 0.63, 0.64, 0.65), 0.875),
        "base": ((0.0, -0.03, -0.07, -0.05, -0.06, -0.08, -0.05, -0.06, -0.09, -0.07, 0.0), 0.93)},
}

TEXTURE_RADIUS = 16
TEXTURE_EPS = 0.01
TEXTURE_GAIN = {1: (0.617, 0.56), -1: (0.621, 0.73)}  # gain = scale * |amount / 100| ** power

LOCAL_TONE_BASE_FRACTION = 1.0 / 32.0  # sigma of the Highlights/Shadows base, per frame width
LOCAL_TONE_CARRY = 0.5  # channel differences scale by (encoded luma ratio) ** this
_TONE_AMOUNTS = (25, 50, 100)


def _check(name: str, value: float, lo: float, hi: float) -> float:
    if not lo <= value <= hi:
        raise ParameterError(f"{name}={value:g} is outside [{lo:g}, {hi:g}]")
    return float(value)


def _blur(x: np.ndarray) -> np.ndarray:
    """Separable 1-4-6-4-1 blur, edges clamped."""
    p = np.pad(x, ((2, 2), (0, 0)), mode="edge")
    x = sum(_BINOMIAL[i] * p[i:i + x.shape[0]] for i in range(5))
    p = np.pad(x, ((0, 0), (2, 2)), mode="edge")
    return sum(_BINOMIAL[i] * p[:, i:i + x.shape[1]] for i in range(5))


def _down(x: np.ndarray) -> np.ndarray:
    return _blur(x)[::2, ::2]


def _up(x: np.ndarray, shape: tuple[int, ...]) -> np.ndarray:
    """Upsample by zero insertion and the same blur, normalized so a constant stays constant at the borders."""
    out, weight = np.zeros(shape), np.zeros(shape)
    out[::2, ::2] = x[: (shape[0] + 1) // 2, : (shape[1] + 1) // 2]
    weight[::2, ::2] = 1.0
    return _blur(out) / _blur(weight)


def _gaussian_pyramid(x: np.ndarray, levels: int) -> list[np.ndarray]:
    out = [x]
    for _ in range(levels):
        out.append(_down(out[-1]))
    return out


def local_laplacian(image: np.ndarray, remap: Remap, levels: int, samples: int = 8,
                    residual: Callable[[np.ndarray], np.ndarray] | None = None) -> np.ndarray:
    """Local Laplacian filter of a 2-D ``image`` with values in 0..1.

    ``remap(x, g)`` maps the whole image around the reference level ``g``; each Laplacian
    coefficient of the output comes from the image remapped around that pixel's own Gaussian
    level, interpolated between ``samples`` evenly spaced reference levels. ``residual``, if
    given, is a curve applied to the coarsest Gaussian level before the detail is added back.
    """
    y = np.asarray(image, dtype=np.float64)
    gauss = _gaussian_pyramid(y, levels)
    refs = np.linspace(0.0, 1.0, samples)
    laplacians = []
    for g in refs:
        r = _gaussian_pyramid(remap(y, float(g)), levels)
        laplacians.append([r[k] - _up(r[k + 1], r[k].shape) for k in range(levels)])
    out = gauss[levels] if residual is None else residual(gauss[levels])
    step = refs[1] - refs[0]
    for k in range(levels - 1, -1, -1):
        t = np.clip(gauss[k] / step, 0.0, samples - 1 - 1e-9)
        i = t.astype(int)
        f = t - i
        stack = np.stack([lap[k] for lap in laplacians])
        lo = np.take_along_axis(stack, i[None], 0)[0]
        hi = np.take_along_axis(stack, np.minimum(i + 1, samples - 1)[None], 0)[0]
        out = _up(out, gauss[k].shape) + lo * (1.0 - f) + hi * f
    return out


def _box(x: np.ndarray, radius: int) -> np.ndarray:
    """Mean over a (2r + 1)^2 window, edges clamped."""
    n = 2 * radius + 1
    c = np.pad(x, (radius + 1, radius), mode="edge").cumsum(0).cumsum(1)
    return (c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]) / (n * n)


def guided_filter(guide: np.ndarray, src: np.ndarray, radius: int, eps: float) -> np.ndarray:
    """He et al.'s guided filter: ``src`` smoothed where ``guide`` is flat, kept where it has edges."""
    mean_i, mean_p = _box(guide, radius), _box(src, radius)
    var_i = _box(guide * guide, radius) - mean_i * mean_i
    cov = _box(guide * src, radius) - mean_i * mean_p
    a = cov / (var_i + eps)
    b = mean_p - a * mean_i
    return _box(a, radius) * guide + _box(b, radius)


def _table(values, x: np.ndarray) -> np.ndarray:
    return np.interp(x, _KNOTS, values)


def _law(coeffs: tuple[float, float], a: float) -> float:
    """``scale * a ** power`` for a unit amount ``a`` in 0..1."""
    scale, power = coeffs
    return scale * a ** power


def clarity_sigmas(width: int) -> tuple[float, float]:
    """Sigmas (px) of Clarity's base and of its finest detail for a frame ``width`` px wide."""
    return width * CLARITY_BASE_FRACTION, max(0.5, width * CLARITY_FINE_FRACTION)


def _clarity_luma(y: np.ndarray, amount: float) -> np.ndarray:
    a = abs(amount) / 100.0
    tables = {k: (np.asarray(v), a ** p) for k, (v, p) in CLARITY_TABLES[1 if amount > 0 else -1].items()}
    base_sigma, fine_sigma = clarity_sigmas(y.shape[1])
    base = gaussian_blur(y, base_sigma)
    fine = y - gaussian_blur(y, fine_sigma)
    detail, s_detail = tables["detail"]
    fine_t, s_fine = tables["fine"]
    curve, s_base = tables["base"]
    return (y + s_base * _table(curve, base) + s_detail * _table(detail, y) * (y - base)
            + s_fine * _table(fine_t, y) * fine)


def clarity(encoded: np.ndarray, amount: float, luma: np.ndarray = SRGB_LUMA) -> np.ndarray:
    """Lightroom-style Clarity (-100..100) on an sRGB-encoded H x W x 3 image."""
    amount = _check("clarity", amount, -100.0, 100.0)
    img = np.clip(np.asarray(encoded, dtype=np.float64), 0.0, 1.0)
    if amount == 0:
        return img
    linear = decode(img, Transfer.SRGB)
    lum = np.maximum(linear @ np.asarray(luma, dtype=np.float64), 1e-6)
    y = _clarity_luma(lum ** (1.0 / LUMA_GAMMA), amount)
    ratio = np.maximum(y, 0.0) ** LUMA_GAMMA / lum
    return encode(np.clip(linear * ratio[..., None], 0.0, 1.0), Transfer.SRGB)


def texture(encoded: np.ndarray, amount: float) -> np.ndarray:
    """Lightroom-style Texture (-100..100) on an sRGB-encoded H x W x 3 image."""
    amount = _check("texture", amount, -100.0, 100.0)
    img = np.clip(np.asarray(encoded, dtype=np.float64), 0.0, 1.0)
    if amount == 0:
        return img
    sign = 1 if amount > 0 else -1
    gain = sign * _law(TEXTURE_GAIN[sign], abs(amount) / 100.0)
    ycc = img @ _YCC.T
    guide = ycc[..., 0]
    out = np.stack([ycc[..., c] + gain * (ycc[..., c] - guided_filter(guide, ycc[..., c], TEXTURE_RADIUS, TEXTURE_EPS))
                    for c in range(3)], axis=-1)
    return np.clip(out @ _YCC_INV.T, 0.0, 1.0)


@lru_cache(maxsize=1)
def _local_tone_tables() -> dict[str, dict[int, np.ndarray]]:
    """The fitted ``y`` change per slider and signed amount, each a (pixel knots x base knots) grid."""
    data = json.loads((resources.files("colorlab") / "data" / "local_tone.json").read_text(encoding="utf-8"))
    shape = (data["pixel_knots"], data["base_knots"])
    return {s: {int(a): np.asarray(v, dtype=np.float64).reshape(shape) for a, v in data[s].items()}
            for s in ("highlights", "shadows", "dehaze")}


@lru_cache(maxsize=1)
def _dehaze_gains() -> dict[int, np.ndarray]:
    """Dehaze's gain on linear differences from luminance, per signed amount, over the new ``y``."""
    data = json.loads((resources.files("colorlab") / "data" / "local_tone.json").read_text(encoding="utf-8"))
    return {int(a): np.asarray(v, dtype=np.float64) for a, v in data["dehaze_chroma"].items()}


def local_tone_sigma(width: int) -> float:
    """Sigma (px) of the Highlights/Shadows base for a frame ``width`` px wide."""
    return width * LOCAL_TONE_BASE_FRACTION


def _amount_table(tables: dict[int, np.ndarray], amount: float) -> np.ndarray:
    """The table for ``amount``, linear between the fitted 25, 50 and 100 (and 0 at 0)."""
    sign, mag = (1 if amount > 0 else -1), abs(amount)
    grid = (0,) + _TONE_AMOUNTS
    hi = next(i for i, a in enumerate(grid) if a >= mag)
    t = (mag - grid[hi - 1]) / (grid[hi] - grid[hi - 1])
    lower = tables[sign * grid[hi - 1]] if hi > 1 else 0.0
    return (1 - t) * lower + t * tables[sign * grid[hi]]


def _bilinear(table: np.ndarray, p: np.ndarray, b: np.ndarray) -> np.ndarray:
    """``table`` (knots evenly over 0..1 on both axes) at the points (p, b)."""
    def split(v: np.ndarray, n: int):
        t = np.clip(v, 0.0, 1.0) * (n - 1)
        i = np.minimum(t.astype(int), n - 2)
        return i, t - i
    i, fi = split(p, table.shape[0])
    j, fj = split(b, table.shape[1])
    return ((1 - fi) * ((1 - fj) * table[i, j] + fj * table[i, j + 1])
            + fi * ((1 - fj) * table[i + 1, j] + fj * table[i + 1, j + 1]))


def highlights_shadows(encoded: np.ndarray, highlights: float = 0.0, shadows: float = 0.0) -> np.ndarray:
    """Lightroom-style Highlights and Shadows (each -100..100) on an sRGB-encoded H x W x 3 image."""
    amounts = {"highlights": _check("highlights", highlights, -100.0, 100.0),
               "shadows": _check("shadows", shadows, -100.0, 100.0)}
    img = np.clip(np.asarray(encoded, dtype=np.float64), 0.0, 1.0)
    if not any(amounts.values()):
        return img
    lum = np.maximum(decode(img, Transfer.SRGB) @ SRGB_LUMA, 1e-6)
    y = lum ** (1.0 / LUMA_GAMMA)
    pixel = (y + gaussian_blur(y, 1.0)) / 2.0
    base = gaussian_blur(y, local_tone_sigma(y.shape[1]))
    tables = _local_tone_tables()
    delta = sum(_bilinear(_amount_table(tables[name], a), pixel, base) for name, a in amounts.items() if a)
    old = encode(np.clip(lum, 0.0, 1.0), Transfer.SRGB)
    new = encode(np.clip(np.maximum(y + delta, 0.0) ** LUMA_GAMMA, 0.0, 1.0), Transfer.SRGB)
    scale = (new / np.maximum(old, 1e-6)) ** LOCAL_TONE_CARRY
    return np.clip(new[..., None] + (img - old[..., None]) * scale[..., None], 0.0, 1.0)


#: Dehaze's dark channel half (positive amounts): the haze map mixes the darkest channel with
#: luma, takes its minimum over width/128 px and refines it by a width/16 px guided filter.
DEHAZE_MIX = 0.65
DEHAZE_EPS = 0.01
DEHAZE_AIR = 1.05  # the haze's (encoded) level
DEHAZE_MIN_TRANSMISSION = 0.05
_DEHAZE_AMOUNTS = (0.0, 25.0, 50.0, 100.0)
_DEHAZE_STRENGTH = (0.0, 0.2, 0.4, 0.775)  # how much of the haze map is removed
_DEHAZE_SHARE = (0.0, 0.2, 0.5, 0.8)  # the dark channel result's share against the tone table's


def dehaze_radii(width: int) -> tuple[int, int]:
    """The min-filter and guided-filter radii (px) of Dehaze's haze map for a frame ``width`` px wide."""
    return max(1, round(width / 128)), max(1, round(width / 16))


def _min_filter(x: np.ndarray, radius: int) -> np.ndarray:
    pad = np.pad(x, radius, mode="edge")
    rows = np.lib.stride_tricks.sliding_window_view(pad, 2 * radius + 1, axis=0).min(-1)
    return np.lib.stride_tricks.sliding_window_view(rows, 2 * radius + 1, axis=1).min(-1)


def _clear_haze(img: np.ndarray, amount: float) -> np.ndarray:
    """The dark channel prior (He, Sun and Tang, 2009) on encoded values: ``(I - A) / t + A``."""
    luma = img @ SRGB_LUMA
    small, large = dehaze_radii(img.shape[1])
    haze = _min_filter(DEHAZE_MIX * img.min(axis=-1) + (1 - DEHAZE_MIX) * luma, small)
    haze = guided_filter(luma, haze, large, DEHAZE_EPS)
    strength = float(np.interp(amount, _DEHAZE_AMOUNTS, _DEHAZE_STRENGTH))
    t = np.maximum(1.0 - strength * haze / DEHAZE_AIR, DEHAZE_MIN_TRANSMISSION)[..., None]
    return np.clip((img - DEHAZE_AIR) / t + DEHAZE_AIR, 0.0, 1.0)


def dehaze(encoded: np.ndarray, amount: float) -> np.ndarray:
    """Lightroom-style Dehaze (-100..100) on an sRGB-encoded H x W x 3 image.

    Luminance moves by a local tone table, like Highlights/Shadows, and each channel's linear
    difference from luminance is scaled by a measured gain over the new tone, so colors gain
    saturation as the image darkens (and lose it toward -100). Positive amounts then mix in a
    dark channel result, ``(I - A) / t`` with a smooth transmission map: it keeps a saturated
    color's main channel and follows Lightroom's long gradients inside flat areas.
    """
    amount = _check("dehaze", amount, -100.0, 100.0)
    img = np.clip(np.asarray(encoded, dtype=np.float64), 0.0, 1.0)
    if not amount:
        return img
    lin = decode(img, Transfer.SRGB)
    lum = np.maximum(lin @ SRGB_LUMA, 1e-6)
    y = lum ** (1.0 / LUMA_GAMMA)
    pixel = (y + gaussian_blur(y, 1.0)) / 2.0
    base = gaussian_blur(y, local_tone_sigma(y.shape[1]))
    table = _amount_table(_local_tone_tables()["dehaze"], amount)
    new_y = np.clip(y + _bilinear(table, pixel, base), 0.0, 1.0)
    curve = 1.0 + _amount_table({a: g - 1.0 for a, g in _dehaze_gains().items()}, amount)
    gain = np.interp(new_y, np.linspace(0.0, 1.0, len(curve)), curve)
    out = (new_y ** LUMA_GAMMA)[..., None] + (lin - lum[..., None]) * gain[..., None]
    out = np.clip(encode(np.clip(out, 0.0, 1.0), Transfer.SRGB), 0.0, 1.0)
    if amount > 0:
        share = float(np.interp(amount, _DEHAZE_AMOUNTS, _DEHAZE_SHARE))
        out = (1 - share) * out + share * _clear_haze(img, amount)
    return out


def apply_to_image(encoded: np.ndarray, clarity_amount: float = 0.0, texture_amount: float = 0.0,
                   highlights_amount: float = 0.0, shadows_amount: float = 0.0) -> np.ndarray:
    """Highlights/Shadows, Texture, then Clarity on an sRGB-encoded H x W x 3 image."""
    toned = highlights_shadows(encoded, highlights_amount, shadows_amount)
    return clarity(texture(toned, texture_amount), clarity_amount)


def main(argv: list[str] | None = None) -> int:
    import tifffile

    parser = argparse.ArgumentParser(prog="python -m colorlab.local_contrast", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    app = sub.add_parser("apply", help="apply Highlights, Shadows, Clarity and Texture to an sRGB TIFF")
    app.add_argument("input", type=Path)
    app.add_argument("output", type=Path)
    app.add_argument("--clarity", type=float, default=0.0)
    app.add_argument("--texture", type=float, default=0.0)
    app.add_argument("--highlights", type=float, default=0.0)
    app.add_argument("--shadows", type=float, default=0.0)
    args = parser.parse_args(argv)
    try:
        data = tifffile.imread(args.input)
        if data.ndim != 3 or data.shape[2] < 3:
            raise ValueError(f"{args.input} is not an RGB image")
        rgb = data[..., :3]
        scale = float(np.iinfo(rgb.dtype).max) if rgb.dtype.kind in "ui" else 1.0
        out = apply_to_image(rgb / scale, args.clarity, args.texture, args.highlights, args.shadows)
        out = np.round(out * scale).astype(rgb.dtype) if rgb.dtype.kind in "ui" else out.astype(rgb.dtype)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(args.output, out, photometric="rgb")
        print(f"wrote {args.output}")
    except (ValueError, OSError, ParameterError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
