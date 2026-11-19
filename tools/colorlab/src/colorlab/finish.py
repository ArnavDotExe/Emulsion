"""Finishing passes fitted to Lightroom's exports: vignette, sharpening, noise reduction and
Lightroom-style grain, plus :func:`apply_settings`, which runs every spatial pass a preset names.

    python -m colorlab.finish apply preset.xmp in.tif out.tif [--seed 0]

All passes work on sRGB-encoded H x W x 3 images (0..1), like :mod:`colorlab.local_contrast` and
:mod:`colorlab.film`, so they can run as shader passes after a look's LUT. Their residuals (mean
CIEDE2000 against Lightroom's exports of the spatial calibration charts) are recorded in
``docs/tdd/colorlab-phase1-core.tdd.md``; the fitted numbers are in ``data/finish.json``.

* :func:`vignette`, Lightroom's post-crop vignette. A weight ``m`` (0 in the middle, 1 in the
  corners) is read from a measured profile over the distance from the center, on an ellipse that
  follows the frame. Midpoint moves the profile, feather stretches it, and roundness turns the
  ellipse into a circle in pixels (+100) or into a rectangle with a narrow border (-100). Each
  channel then goes through a measured table ``F(value, m)`` per amount (25, 50, 100 each way),
  which carries Lightroom's own tone response: Highlight Priority (style 1) spares bright
  values. Color Priority (2) is identical on grays, which is all the charts show, so it shares
  style 1's tables. Paint Overlay (3) scales encoded values toward black, or blends them toward
  white, by ``amount * m``. The highlights slider (negative amounts, styles 1 and 2) adds the
  change measured at -50 with highlights 50 and 100, scaled by the amount.
* :func:`sharpen`, an unsharp mask on encoded luma whose overshoot is clamped, far more on the
  dark side than the bright side, like Lightroom's at Detail 25. It is fixed in pixels. Radius
  widens the kernel; Detail above 25 narrows it toward the ringing Lightroom shows at 100.
  Masking keeps it to strong edges: a soft threshold on the gradient of luma blurred by 1 px,
  blended in as (masking / 100) ^ 4, so it barely acts below 50, as in Lightroom.
* :func:`noise_reduction`. Luminance runs a 3 px guided filter on Y (BT.601), blended by
  ``(amount / 100) ** 0.5``, so faint noise goes before strong noise, as in Lightroom. Color is a
  Gaussian blur (sigma 8 px) of Cb and Cr. Lightroom gave the same result at 25, 50 and 100 on
  the TIFF charts, so the full blur is reached at 25.
* :func:`lightroom_grain`, monochrome Gaussian noise added to encoded values and clipped. White
  noise is filtered by ``G(s) - b G(2s)`` to Lightroom's autocorrelation, with a standard
  deviation linear in the amount; size and roughness set ``s``, ``b`` and the strength from
  measured tables. The frame width acts like a change of size (``size * (width / 1536) ** 1.8``):
  Lightroom's grain is finer and stronger on small images. Its pattern isn't reproduced, only
  its statistics. :mod:`colorlab.film` holds the physical film grain.
"""
from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Callable, Mapping

import numpy as np

from .errors import ColorlabError, ParameterError
from .film import gaussian_blur
from .local_contrast import _YCC, _YCC_INV, clarity, dehaze, guided_filter, highlights_shadows, image_strength, texture
from .pipeline import render
from .preset import Preset

GRAIN_WIDTH = 1536  # frame width (px) at which the grain tables were measured
NR_RADIUS = 3
NR_EPS = 0.01
COLOR_NR_SIGMA = 8.0
COLOR_NR_FULL = 25.0  # Lightroom's color NR reaches its full effect by this amount on TIFFs
_AMOUNTS = (25, 50, 100)


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads((resources.files("colorlab") / "data" / "finish.json").read_text(encoding="utf-8"))


def _check(name: str, value: float, lo: float, hi: float) -> float:
    value = float(value)
    if not lo <= value <= hi:
        raise ParameterError(f"{name} must be in {lo:g}..{hi:g}, got {value:g}")
    return value


def _image(encoded: np.ndarray) -> np.ndarray:
    img = np.clip(np.asarray(encoded, dtype=np.float64), 0.0, 1.0)
    if img.ndim != 3 or img.shape[2] != 3:
        raise ParameterError(f"expected an H x W x 3 image, got shape {img.shape}")
    return img


def _at(values: list[float], x: float) -> float:
    """``values`` given at slider 0, 50 and 100, linear in between."""
    return float(np.interp(x, (0.0, 50.0, 100.0), values))


# --- vignette ------------------------------------------------------------------------------------

def vignette_mask(shape: tuple[int, ...], midpoint: float = 50.0, feather: float = 50.0,
                  roundness: float = 0.0) -> np.ndarray:
    """Lightroom's vignette weight (0 in the middle, 1 in the corners) for an H x W frame."""
    v = _data()["vignette"]
    h, w = shape[0], shape[1]
    yy, xx = np.mgrid[0:h, 0:w] + 0.5
    u, t = np.abs(xx - w / 2) / (w / 2), np.abs(yy - h / 2) / (h / 2)
    c0 = v["center"]
    center = _at(v["midpoint"]["center"], midpoint) + _at(v["feather"]["center"], feather) - c0
    scale = _at(v["midpoint"]["scale"], midpoint) * _at(v["feather"]["scale"], feather)
    r = roundness / 100.0
    if r >= 0:  # toward a circle in pixels
        d = np.hypot(u, t * (1 + (h / w - 1) * r))
        center += (v["round"]["center"] - c0) * r
        scale *= 1 + (v["round"]["scale"] - 1) * r
    else:  # toward a rectangle: a superellipse whose exponent grows without bound
        q = 0.5 * (1 + r)
        d = np.maximum(u, t) if q < 1e-3 else (u ** (1 / q) + t ** (1 / q)) ** q
        center += (v["rect"]["center"] - c0) * -r
        scale *= 1 + (v["rect"]["scale"] - 1) * -r
    return np.interp(c0 + (d - center) / max(scale, 1e-3), v["mask_d"], v["mask_m"])


def _vignette_table(amount: float, highlights: float) -> np.ndarray:
    v = _data()["vignette"]
    tables = {k: np.asarray(t, dtype=np.float64) for k, t in v["tables"].items()}
    knots = np.asarray(v["value_knots"], dtype=np.float64)
    identity = np.repeat(knots[:, None], v["mask_knots"], axis=1)
    sign, mag = (1 if amount > 0 else -1), abs(amount)
    grid = (0,) + _AMOUNTS
    hi = next(i for i, a in enumerate(grid) if a >= mag)
    t = (mag - grid[hi - 1]) / (grid[hi] - grid[hi - 1])
    lower = tables[str(sign * grid[hi - 1])] if hi > 1 else identity
    table = (1 - t) * lower + t * tables[str(sign * grid[hi])]
    if sign < 0 and highlights > 0:
        steps = (tables["-50"], tables["-50h50"], tables["-50h100"])
        i = 0 if highlights <= 50 else 1
        f = (highlights - 50 * i) / 50
        change = (1 - f) * steps[i] + f * steps[i + 1] - steps[0]
        table = table + change * min(mag / 50.0, 2.0)
    table[:, 0] = knots  # no change where the weight is 0
    return np.clip(table, 0.0, 1.0)


def vignette(encoded: np.ndarray, amount: float, midpoint: float = 50.0, feather: float = 50.0,
             roundness: float = 0.0, style: int = 1, highlights: float = 0.0) -> np.ndarray:
    """Lightroom's post-crop vignette (amount -100..100) on an sRGB-encoded H x W x 3 image."""
    amount = _check("vignette amount", amount, -100, 100)
    for name, value, lo in (("vignette midpoint", midpoint, 0), ("vignette feather", feather, 0),
                            ("vignette roundness", roundness, -100), ("vignette highlights", highlights, 0)):
        _check(name, value, lo, 100)
    if style not in (1, 2, 3):
        raise ParameterError(f"vignette style must be 1, 2 or 3, got {style}")
    img = _image(encoded)
    if amount == 0:
        return img
    m = vignette_mask(img.shape, midpoint, feather, roundness)
    if style == 3:
        a = amount / 100.0 * m[..., None]
        return np.clip(img * (1 + a) if amount < 0 else img + (1 - img) * a, 0.0, 1.0)
    table = _vignette_table(amount, highlights)
    knots = np.asarray(_data()["vignette"]["value_knots"], dtype=np.float64)
    jt = m * (table.shape[1] - 1)
    j = np.minimum(jt.astype(int), table.shape[1] - 2)
    fj = jt - j
    column = table[:, j] * (1 - fj) + table[:, j + 1] * fj  # value knots x H x W, at each pixel's weight
    out = np.empty_like(img)
    for c in range(3):
        x = img[..., c]
        i = np.clip(np.searchsorted(knots, x, side="right") - 1, 0, len(knots) - 2)
        f = (x - knots[i]) / (knots[i + 1] - knots[i])
        lo = np.take_along_axis(column, i[None], 0)[0]
        hi = np.take_along_axis(column, (i + 1)[None], 0)[0]
        out[..., c] = lo + (hi - lo) * f
    return np.clip(out, 0.0, 1.0)


# --- sharpening ----------------------------------------------------------------------------------

#: Sharpening Masking (fitted to Masking 25/50/100 on the scene chart): an edge mask from the
#: gradient of luma blurred by 1 px, ``MASK_TOP * clip((g - threshold) / width)``, blended in by
#: ``(masking / 100) ** MASK_POWER``
MASK_SIGMA, MASK_THRESHOLD, MASK_WIDTH, MASK_TOP, MASK_POWER = 1.0, 0.05, 0.1, 0.8, 4.0


def sharpen(encoded: np.ndarray, amount: float, radius: float = 1.0, detail: float = 25.0,
            masking: float = 0.0) -> np.ndarray:
    """Lightroom-style sharpening (amount 0..150, radius 0.5..3, detail and masking 0..100) on an
    sRGB-encoded H x W x 3 image. ``masking`` keeps the sharpening to strong edges."""
    amount = _check("sharpening amount", amount, 0, 150)
    radius = _check("sharpening radius", radius, 0.5, 3.0)
    detail = _check("sharpening detail", detail, 0, 100)
    masking = _check("sharpening masking", masking, 0, 100)
    img = _image(encoded)
    if amount == 0:
        return img
    s = _data()["sharpen"]
    table = np.array([[0.0, 0.0, 0.0]] + [s["amounts"][str(a)] for a in _AMOUNTS])
    k, down, up = (float(np.interp(min(amount, 100.0), (0,) + _AMOUNTS, table[:, i])) for i in range(3))
    if amount > 100:  # beyond the measured range: wider clamps, the same kernel
        down, up = down * amount / 100, up * amount / 100
    sigma = s["sigma"] * radius ** s["radius_power"]
    if detail > 25:
        f = (detail - 25) / 75
        sigma *= 1 + (s["detail_100_ratio"] - 1) * f
        k *= 1 + (s["detail_100_gain"] - 1) * f
    k *= radius ** s["gain_radius_power"]
    up *= radius ** s["up_radius_power"]
    y = img @ _YCC[0]
    boost = np.clip(k * (y - gaussian_blur(y, sigma)), -down, up)
    if masking:
        gy, gx = np.gradient(gaussian_blur(y, MASK_SIGMA))
        edges = MASK_TOP * np.clip((np.hypot(gx, gy) - MASK_THRESHOLD) / MASK_WIDTH, 0.0, 1.0)
        boost = boost * (1.0 + (masking / 100.0) ** MASK_POWER * (edges - 1.0))
    return np.clip(img + boost[..., None], 0.0, 1.0)


# --- noise reduction -----------------------------------------------------------------------------

def noise_reduction(encoded: np.ndarray, luminance: float = 0.0, color: float = 0.0) -> np.ndarray:
    """Lightroom-style Luminance and Color noise reduction (each 0..100) on an sRGB-encoded image."""
    luminance = _check("luminance noise reduction", luminance, 0, 100)
    color = _check("color noise reduction", color, 0, 100)
    img = _image(encoded)
    if not luminance and not color:
        return img
    ycc = img @ _YCC.T
    if luminance:
        y = ycc[..., 0]
        ycc[..., 0] = y + (luminance / 100.0) ** 0.5 * (guided_filter(y, y, NR_RADIUS, NR_EPS) - y)
    if color:
        k = min(1.0, color / COLOR_NR_FULL)
        for c in (1, 2):
            ycc[..., c] += k * (gaussian_blur(ycc[..., c], COLOR_NR_SIGMA) - ycc[..., c])
    return np.clip(ycc @ _YCC_INV.T, 0.0, 1.0)


# --- grain ---------------------------------------------------------------------------------------

def grain_parameters(size: float, roughness: float, width: int) -> tuple[float, float, float]:
    """``(s, b, sd)`` of Lightroom's grain at Amount 50 for a frame ``width`` px wide."""
    g = _data()["grain"]
    eff = max(size, 1.0) * (width / GRAIN_WIDTH) ** g["frame_power"]
    sizes = sorted(int(k) for k in g["size"])
    tab = np.array([g["size"][str(k)] for k in sizes])
    s, b, sd = (float(np.interp(eff, sizes, tab[:, i])) for i in range(3))
    if eff > sizes[-1]:  # coarser than measured: keep widening and weakening
        s *= (eff / sizes[-1]) ** 0.5
        sd *= (sizes[-1] / eff) ** 0.5
    r = np.array([g["roughness"][k] for k in ("0", "50", "100")])
    ds, db = (float(np.interp(roughness, (0, 50, 100), r[:, i]) - r[1, i]) for i in range(2))
    ratio = float(np.interp(roughness, (0, 50, 100), r[:, 2])) / r[1, 2]
    return max(0.3, s + ds), float(np.clip(b + db, 0.0, 0.95)), sd * ratio


def lightroom_grain(encoded: np.ndarray, amount: float, size: float = 25.0, roughness: float = 50.0,
                    seed: int = 0) -> np.ndarray:
    """Lightroom-style grain (amount, size, roughness 0..100) on an sRGB-encoded image; the same
    seed gives the same grain."""
    amount = _check("grain amount", amount, 0, 100)
    size = _check("grain size", size, 0, 100)
    roughness = _check("grain roughness", roughness, 0, 100)
    img = _image(encoded)
    if amount == 0:
        return img
    s, b, sd = grain_parameters(size, roughness, img.shape[1])
    noise = np.random.default_rng(seed).normal(size=img.shape[:2])
    field = gaussian_blur(noise, s) - b * gaussian_blur(noise, 2 * s)
    field = (field - field.mean()) / max(float(field.std()), 1e-12)
    return np.clip(img + (sd * amount / 50.0 * field)[..., None], 0.0, 1.0)


# --- wiring --------------------------------------------------------------------------------------

def _number(settings: Mapping[str, str], key: str, default: float = 0.0) -> float:
    raw = settings.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ParameterError(f"{key}: not a number: {raw!r}") from None


def local_passes(encoded: np.ndarray, settings: Mapping[str, str]) -> np.ndarray:
    """The neighbourhood passes a preset names, in Lightroom's order: noise reduction,
    Highlights/Shadows (at the image's own strength, :func:`~colorlab.local_contrast.image_strength`),
    Dehaze, Texture, Clarity and sharpening. Missing keys mean the default."""
    def n(key: str, default: float = 0.0) -> float:
        return _number(settings, key, default)

    img = _image(encoded)
    img = noise_reduction(img, n("LuminanceSmoothing"), n("ColorNoiseReduction"))
    img = highlights_shadows(img, n("Highlights2012"), n("Shadows2012"), strength=image_strength(img))
    img = dehaze(img, n("Dehaze"))
    img = clarity(texture(img, n("Texture")), n("Clarity2012"))
    return sharpen(img, n("Sharpness"), n("SharpenRadius", 1.0), n("SharpenDetail", 25.0), n("SharpenEdgeMasking"))


def vignette_pass(encoded: np.ndarray, settings: Mapping[str, str]) -> np.ndarray:
    """The post-crop vignette a preset's ``settings`` name."""
    def n(key: str, default: float = 0.0) -> float:
        return _number(settings, key, default)

    return vignette(encoded, n("PostCropVignetteAmount"), n("PostCropVignetteMidpoint", 50.0),
                    n("PostCropVignetteFeather", 50.0), n("PostCropVignetteRoundness"),
                    int(n("PostCropVignetteStyle", 1.0)), n("PostCropVignetteHighlightContrast"))


def grain_pass(encoded: np.ndarray, settings: Mapping[str, str], seed: int = 0) -> np.ndarray:
    """The grain a preset's ``settings`` name."""
    def n(key: str, default: float = 0.0) -> float:
        return _number(settings, key, default)

    return lightroom_grain(encoded, n("GrainAmount"), n("GrainSize", 25.0), n("GrainFrequency", 50.0), seed)


def finishing_passes(encoded: np.ndarray, settings: Mapping[str, str], seed: int = 0) -> np.ndarray:
    """The post-crop vignette and then the grain a preset names."""
    return grain_pass(vignette_pass(encoded, settings), settings, seed)


def apply_settings(encoded: np.ndarray, settings: Mapping[str, str], seed: int = 0) -> np.ndarray:
    """Every spatial pass a preset's Camera Raw ``settings`` name, in Lightroom's order: noise
    reduction, Highlights/Shadows, Dehaze, Texture, Clarity, sharpening, vignette, grain.
    A missing key means the slider's default."""
    return finishing_passes(local_passes(encoded, settings), settings, seed)


#: The spatial settings Lightroom takes from a look profile, added at the look's amount. A look's
#: Sharpness is ignored (looks with Sharpness 40 export the same as their baked LUT).
LOOK_SPATIAL_SETTINGS = ("Highlights2012", "Shadows2012")


def spatial_settings(preset: Preset) -> dict[str, str]:
    """``preset``'s settings with its look profile's Highlights/Shadows added at the look's amount
    and clamped to -100..100, as Lightroom applies them."""
    settings = dict(preset.settings)
    look = preset.look
    profile = look.source if look is not None else None
    if profile is not None:
        for key in LOOK_SPATIAL_SETTINGS:
            if profile.has(key):
                value = preset.number(key) + look.amount * profile.number(key)
                settings[key] = f"{float(np.clip(value, -100.0, 100.0)):g}"
    return settings


def render_preset(preset: Preset, image: np.ndarray, seed: int = 0, local_first: bool = True,
                  look: Callable[[np.ndarray], np.ndarray] | None = None) -> np.ndarray:
    """A whole preset on an sRGB-encoded image: its global look and its spatial passes.

    By default the neighbourhood passes (with the look profile's Highlights/Shadows folded in,
    see :func:`spatial_settings`) and then the vignette run before the look, and the grain after
    it, which matched Lightroom's exports of 44 presets best. ``local_first=False`` runs
    everything after the look. ``look`` replaces :func:`colorlab.pipeline.render`, for example
    with Lightroom's own baked LUT of the preset.
    """
    def default(x: np.ndarray) -> np.ndarray:
        return render(preset, x).astype(np.float64)

    look = look or default
    settings = spatial_settings(preset)
    img = _image(image)
    if local_first:
        return grain_pass(_image(look(vignette_pass(local_passes(img, settings), settings))), settings, seed)
    return finishing_passes(local_passes(_image(look(img)), settings), settings, seed)


def main(argv: list[str] | None = None) -> int:
    import tifffile

    from .preset import load_preset

    parser = argparse.ArgumentParser(prog="python -m colorlab.finish", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    app = sub.add_parser("apply", help="apply a preset's spatial passes to an sRGB TIFF")
    app.add_argument("preset", type=Path)
    app.add_argument("input", type=Path)
    app.add_argument("output", type=Path)
    app.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        preset = load_preset(args.preset)
        data = tifffile.imread(args.input)
        if data.ndim != 3 or data.shape[2] < 3:
            raise ValueError(f"{args.input} is not an RGB image")
        rgb = data[..., :3]
        scale = float(np.iinfo(rgb.dtype).max) if rgb.dtype.kind in "ui" else 1.0
        out = apply_settings(rgb / scale, preset.settings, args.seed)
        out = np.round(out * scale).astype(rgb.dtype) if rgb.dtype.kind in "ui" else out.astype(rgb.dtype)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(args.output, out, photometric="rgb")
        print(f"wrote {args.output}")
    except (ValueError, OSError, ColorlabError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
