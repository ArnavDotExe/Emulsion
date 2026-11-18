"""Film halation and grain, sized on the film: the spatial half of a film look.

    python -m colorlab.film apply in.tif out.tif [--format-mm 36] [--seed 0]

A 3D LUT carries a film stock's color, but not its spatial effects: light spreading through
the emulsion (scatter), light bouncing off the film base back into the red-sensitive layer
(halation), and the silver grains themselves. They depend on a pixel's neighbourhood, so
the camera renders them as shader passes around the look's LUT:

* :func:`halation` on scene-linear light, before the look: scatter and halation are what
  the light does inside the film before it is developed.
* :func:`grain` on the finished image, treated as a positive transparency: its density
  ``-log10(linear)`` is made of grains, so highlights (clear film) and deep blacks
  (fully developed) are smooth and the midtones are grainiest.

Every size is in micrometres on the film, converted with :func:`pixel_size_um`. A preview
and a full-size capture of the same frame therefore get the same halo, and the grain
averages the way real grain does: a 2 x 2 average of a 4x-pixel render matches the 1x
render. The film format (36 mm for 135, 56 mm for 6x6) sets how big grain and halo are
relative to the frame.

Ported from spektrafilm by Andrea Volpato (https://github.com/andreavolpato/spektrafilm,
GPL-3.0; ``model/diffusion.py``, ``model/grain.py`` and ``runtime/params_schema.py``) and from
darktable's C port of it (``src/common/spektra_core.c``, GPL-3.0-or-later, Copyright (C) 2026
darktable developers), which anchors the highlight boost to scene exposure. Changes: plain
NumPy with clamp-to-edge blurs (as a GPU texture samples), the binomial-of-Poisson grain
sampled as the single Poisson it equals, a seeded generator per channel, and grain applied
to the final positive rather than to a simulated negative. No spektrafilm film or paper
profile data (CC BY-SA 4.0) is used: the defaults are its generic code defaults.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .colorspace import Transfer, decode, encode

#: scene-linear mid-gray, the anchor of the highlight boost
MIDGRAY = 0.184
#: the highlight boost's curve spans this many stops above its protected range
BOOST_SPAN_EV = 4.0
#: an isotropic 2-D exponential PSF ``exp(-r/L) / (2 pi L^2)`` as three Gaussians:
#: (weight, sigma / L); spektrafilm's fit
_EXPONENTIAL_AS_GAUSSIANS = ((0.1633, 0.5360), (0.6496, 1.5236), (0.1870, 2.7684))
#: blurs reach this many sigmas
_TRUNCATE = 3.0


@dataclass(frozen=True)
class Halation:
    """Scatter and halation of a color negative, per channel (R, G, B), in micrometres.

    Scatter is energy preserving: a ``scatter_tail_weight`` share of the light goes to an
    exponential tail of decay ``scatter_tail_um``, the rest to a Gaussian core. Halation adds
    ``strength * amount`` of the light back as ``bounces`` Gaussians of widths
    ``first_sigma_um * sqrt(k)``, each ``bounce_decay`` times the previous one; with
    ``renormalize`` the total is divided back so flat areas keep their level. Blue has no
    halation: the red layer is at the back, next to the base, and a yellow filter stops blue.
    """

    scatter_amount: float = 1.0
    scatter_core_um: tuple[float, float, float] = (2.2, 2.0, 1.6)
    scatter_tail_um: tuple[float, float, float] = (9.3, 9.7, 9.1)
    scatter_tail_weight: tuple[float, float, float] = (0.78, 0.65, 0.67)
    amount: float = 1.0
    strength: tuple[float, float, float] = (0.05, 0.015, 0.0)
    first_sigma_um: tuple[float, float, float] = (65.0, 65.0, 65.0)
    bounces: int = 3
    bounce_decay: float = 0.5
    renormalize: bool = True
    boost_ev: float = 0.0
    boost_range: float = 0.3
    protect_ev: float = 4.0


@dataclass(frozen=True)
class Grain:
    """Silver-grain model per channel (R, G, B).

    Each pixel holds ``pixel area / (particle_area_um2 * particle_scale)`` grains, each adding
    ``density_max / count`` when developed. ``uniformity`` near 1 makes the grain count less
    random where the film is dense. ``blur_px`` is the scan's softness, in output pixels.
    """

    particle_area_um2: float = 0.2
    particle_scale: tuple[float, float, float] = (1.6, 1.6, 3.2)
    uniformity: tuple[float, float, float] = (0.97, 0.99, 0.97)
    density_min: tuple[float, float, float] = (0.03, 0.03, 0.03)
    density_max: tuple[float, float, float] = (2.2, 2.2, 2.2)
    blur_px: float = 0.65


def pixel_size_um(shape: tuple[int, ...], format_mm: float = 36.0) -> float:
    """Size of one pixel on the film, in micrometres, for a frame whose long side is ``format_mm``."""
    return format_mm * 1000.0 / max(shape[0], shape[1])


# --- blurs ---------------------------------------------------------------------------------------

def _kernel(sigma: float) -> np.ndarray:
    radius = max(int(np.ceil(_TRUNCATE * sigma)), 1)
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    return k / k.sum()


def _blur_axis(a: np.ndarray, k: np.ndarray, axis: int) -> np.ndarray:
    a = np.moveaxis(a, axis, 0)
    r, n = len(k) // 2, a.shape[0]
    padded = np.concatenate([np.repeat(a[:1], r, axis=0), a, np.repeat(a[-1:], r, axis=0)])
    out = np.zeros_like(a)
    for i, w in enumerate(k):
        out += w * padded[i:i + n]
    return np.moveaxis(out, 0, axis)


def gaussian_blur(image: np.ndarray, sigma) -> np.ndarray:
    """Gaussian blur of an H x W (x C) image, in pixels; ``sigma`` may differ per channel.

    The kernel is truncated at 3 sigma and normalized, and edges are clamped, so flat
    areas stay flat up to the border and the total light is kept.
    """
    image = np.asarray(image, dtype=np.float64)
    if image.ndim == 2:
        return gaussian_blur(image[..., None], sigma)[..., 0]
    sigmas = np.broadcast_to(np.asarray(sigma, dtype=np.float64), (image.shape[2],))
    out = image.copy()
    for c, s in enumerate(sigmas):
        if s > 0:
            k = _kernel(float(s))
            out[..., c] = _blur_axis(_blur_axis(image[..., c], k, 0), k, 1)
    return out


def exponential_blur(image: np.ndarray, decay) -> np.ndarray:
    """Blur by an isotropic exponential PSF of decay length ``decay`` pixels (per channel)."""
    decay = np.asarray(decay, dtype=np.float64)
    total = sum(w for w, _ in _EXPONENTIAL_AS_GAUSSIANS)  # 0.9999: renormalized to keep the light
    return sum(w / total * gaussian_blur(image, ratio * decay) for w, ratio in _EXPONENTIAL_AS_GAUSSIANS)


# --- halation ------------------------------------------------------------------------------------

def boost_highlights(raw: np.ndarray, boost_ev: float, boost_range: float = 0.3,
                     protect_ev: float = 4.0, midgray: float = MIDGRAY) -> np.ndarray:
    """Lift highlights above ``midgray * 2**protect_ev`` so clipped light feeds the halo.

    Identity below that point; up to ``BOOST_SPAN_EV`` stops above it the curve bends up
    (``boost_range`` 0..1 sets how late) until the top of the span is ``2**boost_ev``
    times brighter; beyond, it continues along its tangent. It is anchored to scene
    exposure, not to the frame's brightest pixel, so a pixel's boost doesn't depend on the
    rest of the frame (a preview, a crop and the capture agree).
    """
    if boost_ev < 0 or not 0.0 <= boost_range <= 1.0 or protect_ev < 0:
        raise ValueError("boost_ev and protect_ev must be >= 0 and boost_range in 0..1")
    raw = np.asarray(raw, dtype=np.float64)
    if boost_ev == 0:
        return raw.copy()
    x0 = midgray * 2.0 ** protect_ev
    top = midgray * 2.0 ** (protect_ev + BOOST_SPAN_EV)
    a = 28.0 ** (1.0 - boost_range)
    span = 1.0 - x0 / top
    gain = 2.0 ** boost_ev - 1.0
    k = gain / (np.exp(a * span) - a * span - 1.0)
    dx = (np.clip(raw, x0, top) - x0) / top
    curve = raw + k * top * (np.exp(a * dx) - a * dx - 1.0)
    slope = k * a * (np.exp(a * span) - 1.0)
    tangent = raw + gain * top + slope * (raw - top)
    return np.where(raw <= x0, raw, np.where(raw <= top, curve, tangent))


def halation(raw: np.ndarray, params: Halation = Halation(), pixel_um: float | None = None) -> np.ndarray:
    """Highlight boost, scatter and halation on a scene-linear H x W x 3 image.

    ``pixel_um`` is the size of a pixel on the film (default: a 36 mm frame).
    """
    raw = np.asarray(raw, dtype=np.float64)
    if pixel_um is None:
        pixel_um = pixel_size_um(raw.shape)
    raw = boost_highlights(raw, params.boost_ev, params.boost_range, params.protect_ev)
    if params.scatter_amount > 0:
        tail_w = np.asarray(params.scatter_tail_weight)
        core = gaussian_blur(raw, np.asarray(params.scatter_core_um) / pixel_um)
        tail = exponential_blur(raw, np.asarray(params.scatter_tail_um) / pixel_um)
        scattered = (1.0 - tail_w) * core + tail_w * tail
        raw = (1.0 - params.scatter_amount) * raw + params.scatter_amount * scattered
    strength = np.asarray(params.strength) * params.amount
    if params.bounces >= 1 and np.any(strength > 0):
        weights = params.bounce_decay ** np.arange(params.bounces)
        weights = weights / weights.sum()
        sigma = np.asarray(params.first_sigma_um) / pixel_um
        halo = sum(w * gaussian_blur(raw, sigma * np.sqrt(k + 1)) for k, w in enumerate(weights))
        raw = raw + strength * halo
        if params.renormalize:
            raw = raw / (1.0 + strength)
    return raw


# --- grain ---------------------------------------------------------------------------------------

def grain_density(density: np.ndarray, params: Grain = Grain(), pixel_um: float | None = None,
                  seed: int = 0) -> np.ndarray:
    """Make an H x W x 3 density image out of grains (deterministic for a given ``seed``).

    A pixel at density ``D`` develops each of its ``n`` grains with probability
    ``p = (D + Dmin) / Dtotal``. The developed count is Poisson with mean ``n p / s``, with
    ``s = 1 - uniformity * p``, and each grain adds ``s * Dtotal / n``: the mean density is
    ``D`` and the variance ``(D + Dmin) * (Dtotal / n) * s``. (spektrafilm draws a Poisson
    seed count and then a binomial of it, which is this same Poisson.)
    """
    density = np.asarray(density, dtype=np.float64)
    if pixel_um is None:
        pixel_um = pixel_size_um(density.shape)
    dmin = np.asarray(params.density_min)
    dmax = np.asarray(params.density_max)
    total = dmax + dmin
    count = pixel_um ** 2 / (params.particle_area_um2 * np.asarray(params.particle_scale))
    d = np.clip(density, 0.0, dmax)
    out = np.empty_like(d)
    for c in range(3):
        rng = np.random.default_rng([seed, c])
        p = np.clip((d[..., c] + dmin[c]) / total[c], 1e-6, 1.0 - 1e-6)
        s = 1.0 - p * params.uniformity[c]
        out[..., c] = rng.poisson(count[c] * p / s) * (s * total[c] / count[c]) - dmin[c]
    return gaussian_blur(out, params.blur_px)


def grain(linear: np.ndarray, params: Grain = Grain(), pixel_um: float | None = None,
          seed: int = 0) -> np.ndarray:
    """Film grain on a linear H x W x 3 image (0..1), as density ``-log10(linear)``."""
    linear = np.asarray(linear, dtype=np.float64)
    floor = 10.0 ** -np.asarray(params.density_max)
    density = -np.log10(np.clip(linear, floor, 1.0))
    return np.clip(10.0 ** -grain_density(density, params, pixel_um, seed), 0.0, 1.0)


# --- command line --------------------------------------------------------------------------------

def apply_to_image(encoded: np.ndarray, format_mm: float = 36.0, seed: int = 0,
                   halation_params: Halation | None = Halation(),
                   grain_params: Grain | None = Grain()) -> np.ndarray:
    """Halation then grain on an sRGB-encoded H x W x 3 image (0..1)."""
    pixel_um = pixel_size_um(encoded.shape, format_mm)
    linear = decode(np.clip(np.asarray(encoded, dtype=np.float64), 0.0, 1.0), Transfer.SRGB)
    if halation_params is not None:
        linear = np.clip(halation(linear, halation_params, pixel_um), 0.0, 1.0)
    if grain_params is not None:
        linear = grain(linear, grain_params, pixel_um, seed)
    return encode(linear, Transfer.SRGB)


def main(argv: list[str] | None = None) -> int:
    import tifffile

    parser = argparse.ArgumentParser(prog="python -m colorlab.film", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    app = sub.add_parser("apply", help="add halation and grain to an sRGB TIFF")
    app.add_argument("input", type=Path)
    app.add_argument("output", type=Path)
    app.add_argument("--format-mm", type=float, default=36.0, help="long side of the film frame (36 for 135)")
    app.add_argument("--seed", type=int, default=0)
    app.add_argument("--halation", type=float, default=1.0, help="halation amount (0 turns it off)")
    app.add_argument("--particle-area", type=float, default=Grain.particle_area_um2,
                     help="grain particle area in square micrometres (0 turns grain off)")
    args = parser.parse_args(argv)
    try:
        data = tifffile.imread(args.input)
        if data.ndim != 3 or data.shape[2] < 3:
            raise ValueError(f"{args.input} is not an RGB image")
        rgb = data[..., :3]
        scale = float(np.iinfo(rgb.dtype).max) if rgb.dtype.kind in "ui" else 1.0
        out = apply_to_image(
            rgb / scale, args.format_mm, args.seed,
            Halation(amount=args.halation) if args.halation > 0 else None,
            Grain(particle_area_um2=args.particle_area) if args.particle_area > 0 else None)
        if rgb.dtype.kind in "ui":
            out = np.round(out * scale).astype(rgb.dtype)
        else:
            out = out.astype(rgb.dtype)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(args.output, out, photometric="rgb")
        print(f"wrote {args.output}")
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
