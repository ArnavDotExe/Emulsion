"""Reference pipeline: a preset's global color sliders, applied in Lightroom's order.

Lightroom's exact formulas are not public. Every stage here is a plain, documented
model with the properties a photographer expects: identity at default, endpoints
held, tone curves monotonic. White balance, exposure, the tone sliders, the
parametric curve and vibrance take their shapes from measured Lightroom responses
(``colorlab.response``); the ``K_*`` strengths of the other stages are fitted to
single-slider Lightroom bakes (``colorlab.calibration``).

Working space is ProPhoto RGB primaries. Every stage runs on sRGB-curve encoded
values, the space Lightroom's tone curve panel shows. Spatial effects (grain,
vignette, highlights/shadows, clarity, texture, dehaze, sharpening, noise reduction) belong to a later stage and
are not applied here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from . import colorspace as cs
from . import response
from .adobe_table import apply_rgb_table
from .errors import ParameterError
from .lut import Lut3D
from .preset import IDENTITY_CURVE, Preset

WORKING = cs.PROPHOTO_RGB
WORKING_TRANSFER = cs.Transfer.SRGB

BANDS = ("red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta")
_BAND_CENTERS = np.array([0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 270.0, 300.0, 360.0])
_LUMA = WORKING.rgb_to_xyz[1] / WORKING.rgb_to_xyz[1].sum()
# Bradford cone responses from linear working RGB, each row scaled so white maps to (1, 1, 1)
_CONES = cs._BRADFORD @ WORKING.rgb_to_xyz
_CONES = _CONES / (_CONES @ np.ones(3))[:, None]
_CONES_INV = np.linalg.inv(_CONES)

#: measured shapes of white balance, exposure, the tone sliders, the parametric curve and vibrance;
#: read from the shipped file on first use, so a broken file never stops ``calibration`` from
#: importing (and regenerating it)
RESPONSE: response.Response | None = None


def _response() -> response.Response:
    global RESPONSE
    if RESPONSE is None:
        RESPONSE = response.load()
    return RESPONSE

# Stage strengths (slider value 100 == full strength), fitted with `colorlab.calibration`
# to single-slider Lightroom bakes (2026-11-15, 143 bakes).
K_HUE = 0.831  # fraction of the gap to the neighbouring band at hue 100
K_LUMINANCE = 0.708
K_MIXER = 1.55

Curve = Sequence[tuple[float, float]]


def _check(name: str, value: float, lo: float, hi: float) -> float:
    if not lo <= value <= hi:
        raise ParameterError(f"{name}={value:g} is outside [{lo:g}, {hi:g}]")
    return value


def _as_rgb(rgb: np.ndarray) -> np.ndarray:
    a = np.asarray(rgb, dtype=np.float64)
    if a.shape[-1:] != (3,):
        raise ParameterError(f"expected RGB values with a last axis of 3, got shape {a.shape}")
    return a


# --- point curves -------------------------------------------------------------------------------

def spline(points: Curve) -> Callable[[np.ndarray], np.ndarray]:
    """Smooth curve through Lightroom curve points (0-255), as a function on [0, 1].

    A C2 cubic spline with the end conditions of the DNG SDK's spline solver; the
    value is held flat beyond the first and last points.
    """
    if len(points) < 2:
        raise ParameterError(f"a curve needs at least 2 points, got {len(points)}")
    pts = np.asarray(points, dtype=np.float64)
    if pts.min() < 0 or pts.max() > 255:
        raise ParameterError(f"curve points must lie in 0-255: {points}")
    if np.any(np.diff(pts[:, 0]) <= 0):
        raise ParameterError(f"curve x values must strictly increase: {points}")
    x, y = pts[:, 0] / 255.0, pts[:, 1] / 255.0
    slopes = _spline_slopes(x, y)

    def evaluate(v: np.ndarray) -> np.ndarray:
        v = np.asarray(v, dtype=np.float64)
        j = np.clip(np.searchsorted(x, v, side="right"), 1, len(x) - 1)
        x0, x1, y0, y1, s0, s1 = x[j - 1], x[j], y[j - 1], y[j], slopes[j - 1], slopes[j]
        h = x1 - x0
        t = (v - x0) / h
        t2, t3 = t * t, t * t * t
        out = (y0 * (2 * t3 - 3 * t2 + 1) + y1 * (3 * t2 - 2 * t3)
               + h * (s0 * (t3 - 2 * t2 + t) + s1 * (t3 - t2)))
        return np.where(v <= x[0], y[0], np.where(v >= x[-1], y[-1], out))

    return evaluate


def _spline_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    n = len(x)
    d = np.diff(y) / np.diff(x)
    s = np.empty(n)
    s[0] = d[0]
    for j in range(1, n - 1):
        a, c = x[j] - x[j - 1], x[j + 1] - x[j]
        s[j] = (d[j - 1] * c + d[j] * a) / (a + c)
    s[-1] = 2 * d[-1] - s[-2]
    s[0] = 2 * s[0] - s[1]
    if n == 2:
        return s
    e, f, g = np.zeros(n), np.zeros(n), np.zeros(n)
    f[0], e[-1] = 0.5, 0.5
    g[0], g[-1] = 0.75 * (s[0] + s[1]), 0.75 * (s[-2] + s[-1])
    for j in range(1, n - 1):
        span = 2.0 * (x[j + 1] - x[j - 1])
        e[j] = (x[j + 1] - x[j]) / span
        f[j] = (x[j] - x[j - 1]) / span
        g[j] = 1.5 * s[j]
    for j in range(1, n):  # tridiagonal solve, unit diagonal
        m = 1.0 - f[j - 1] * e[j]
        if j != n - 1:
            f[j] /= m
        g[j] = (g[j] - g[j - 1] * e[j]) / m
    for j in range(n - 2, -1, -1):
        g[j] -= f[j] * g[j + 1]
    return g


def _is_identity(curve: Curve) -> bool:
    return tuple(map(tuple, curve)) == IDENTITY_CURVE


def rgb_tone(rgb: np.ndarray, curve: Callable[[np.ndarray], np.ndarray]) -> np.ndarray:
    """Apply a monotonic tone ``curve`` to colors without shifting their hue.

    The curve maps each pixel's largest and smallest channel; the middle channel keeps its
    relative place between them (the "RGB tone" method of Adobe's public DNG SDK). Neutrals
    get exactly ``curve``. Per-channel curves would instead push bright saturated colors
    toward the primaries and secondaries (red toward orange, blue toward cyan).
    """
    rgb = _as_rgb(rgb)
    hi, lo = rgb.max(axis=-1), rgb.min(axis=-1)
    f_hi, f_lo = curve(hi), curve(lo)
    span = hi - lo
    place = (rgb - lo[..., None]) / np.where(span > 0, span, 1.0)[..., None]
    return f_lo[..., None] + place * (f_hi - f_lo)[..., None]


def apply_curves(rgb: np.ndarray, master: Curve = IDENTITY_CURVE, red: Curve = IDENTITY_CURVE,
                 green: Curve = IDENTITY_CURVE, blue: Curve = IDENTITY_CURVE) -> np.ndarray:
    """Master curve (hue-preserving, see ``rgb_tone``), then each channel's own curve (encoded values)."""
    out = _as_rgb(rgb).copy()
    if not _is_identity(master):
        out = rgb_tone(out, spline(master))
    for i, curve in enumerate((red, green, blue)):
        if not _is_identity(curve):
            out[..., i] = spline(curve)(out[..., i])
    return out


# --- white balance, exposure and tone (encoded, measured shapes) -----------------------------

def white_balance(enc: np.ndarray, temperature: float = 0.0, tint: float = 0.0) -> np.ndarray:
    """Incremental temperature/tint (-100..100): Lightroom's measured stops per channel on the
    exposure shoulder, so black and white stay put."""
    _check("temperature", temperature, -100, 100)
    _check("tint", tint, -100, 100)
    enc = _as_rgb(enc)
    if temperature == 0 and tint == 0:
        return enc
    wb = _response().white_balance
    return white_balance_stops(enc, wb.evs(temperature, tint), wb.space, hue_preservation=wb.hue_preservation(temperature, tint))


def white_balance_stops(enc: np.ndarray, evs: np.ndarray, space: str = "working",
                        shoulder: response.ExposureShoulder | None = None,
                        hue_preservation: float = 0.0) -> np.ndarray:
    """Per-channel stops ``evs`` on the exposure shoulder (default: the shipped one), in the
    working space's channels or in Bradford cone space (``response.WB_SPACES``).

    In cone space each pixel goes to linear cone responses (white = 1, 1, 1), is encoded with
    the working curve (odd-symmetric, so slightly negative cones survive), gets its stops, and
    comes back. Black and white stay put either way.

    The stops are gains on the shoulder's stretched scale. Each pixel's largest and smallest
    channel always roll off on their own; ``hue_preservation`` (0..1) blends the middle channel
    from its own rolloff toward its place between them on the stretched scale (the RGB-tone
    method), which keeps saturated colors from mixing.
    """
    if space not in response.WB_SPACES:
        raise ParameterError(f"white balance space {space!r} is not one of {', '.join(response.WB_SPACES)}")
    _check("hue_preservation", hue_preservation, 0.0, 1.0)
    enc = _as_rgb(enc)
    evs = np.asarray(evs, dtype=np.float64)
    if not np.any(evs):
        return enc
    shoulder = shoulder if shoulder is not None else _response().exposure
    if space == "working":
        return _rolloff_stops(enc, evs, shoulder, hue_preservation)
    cones = cs.encode(cs.decode(enc, WORKING_TRANSFER) @ _CONES.T, WORKING_TRANSFER)
    moved = np.sign(cones) * _rolloff_stops(np.minimum(np.abs(cones), 1.0), evs, shoulder, hue_preservation)
    return cs.encode(cs.decode(moved, WORKING_TRANSFER) @ _CONES_INV.T, WORKING_TRANSFER)


def _rolloff_stops(x: np.ndarray, evs: np.ndarray, shoulder: response.ExposureShoulder,
                   hue_preservation: float) -> np.ndarray:
    """``evs`` per channel on the shoulder, with the middle channel partly placed (see above)."""
    out = shoulder.apply(x, evs)
    if not hue_preservation:
        return out
    z = shoulder.stretch(x) * 2.0 ** (shoulder.strength * evs)
    i_hi, i_lo = z.argmax(axis=-1)[..., None], z.argmin(axis=-1)[..., None]
    z_hi, z_lo = np.take_along_axis(z, i_hi, -1), np.take_along_axis(z, i_lo, -1)
    o_hi, o_lo = np.take_along_axis(out, i_hi, -1), np.take_along_axis(out, i_lo, -1)
    span = z_hi - z_lo
    with np.errstate(invalid="ignore", over="ignore"):  # a channel at 1 stretches to a huge value
        place = np.nan_to_num((z - z_lo) / np.where(span > 0, span, 1.0))
    placed = o_lo + place * (o_hi - o_lo)
    return out + hue_preservation * (placed - out)


def exposure(enc: np.ndarray, ev: float) -> np.ndarray:
    """Exposure in stops (-5..5): Lightroom's shoulder, which keeps white and never clips,
    applied hue-preservingly (``rgb_tone``)."""
    _check("exposure", ev, -5, 5)
    enc = _as_rgb(enc)
    if ev == 0:
        return enc
    shoulder = _response().exposure
    return rgb_tone(enc, lambda v: shoulder.apply(v, ev))


def tone(enc: np.ndarray, contrast: float = 0.0, whites: float = 0.0, blacks: float = 0.0) -> np.ndarray:
    """Contrast, Whites and Blacks (-100..100): Lightroom's measured curves, in that order, applied
    hue-preservingly (``rgb_tone``)."""
    for name, v in (("contrast", contrast), ("whites", whites), ("blacks", blacks)):
        _check(name, v, -100, 100)
    enc = _as_rgb(enc)
    if contrast == 0 and whites == 0 and blacks == 0:
        return enc
    tones = _response().tone

    def curve(v: np.ndarray) -> np.ndarray:
        v = np.clip(v, 0.0, 1.0)
        for key, amount in (("Contrast2012", contrast), ("Whites2012", whites), ("Blacks2012", blacks)):
            v = tones[key].apply(v, amount)
        return v

    return rgb_tone(enc, curve)


def parametric_curve(enc: np.ndarray, shadows: float = 0.0, darks: float = 0.0, lights: float = 0.0,
                     highlights: float = 0.0, splits: Sequence[float] = (25, 50, 75)) -> np.ndarray:
    """Lightroom's four-region parametric curve (-100..100); ``splits`` are the region dividers (0-100)."""
    amounts = (shadows, darks, lights, highlights)
    for name, v in zip(("shadows", "darks", "lights", "highlights"), amounts):
        _check(name, v, -100, 100)
    s1, s2, s3 = (float(v) / 100 for v in splits)
    if not 0 < s1 < s2 < s3 < 1:
        raise ParameterError(f"parametric splits must increase strictly inside 0-100: {tuple(splits)}")
    enc = _as_rgb(enc)
    if not any(amounts):
        return enc
    # Lightroom's measured region curves are for the default splits; other splits stretch the
    # tonal axis so each region keeps its place, and the regions apply one after another.
    custom, default = (0.0, s1, s2, s3, 1.0), (0.0, 0.25, 0.5, 0.75, 1.0)
    tones = _response().tone

    def curve(v: np.ndarray) -> np.ndarray:
        v = np.interp(np.clip(v, 0.0, 1.0), custom, default)
        for key, amount in zip(("ParametricShadows", "ParametricDarks", "ParametricLights", "ParametricHighlights"),
                               amounts):
            v = tones[key].apply(v, amount)
        return np.interp(v, default, custom)

    return rgb_tone(enc, curve)


# --- hue/chroma decomposition ---------------------------------------------------------------------

def _hue(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Hexcone hue in degrees and chroma (max - min)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx, mn = rgb.max(axis=-1), rgb.min(axis=-1)
    c = mx - mn
    safe = np.where(c > 0, c, 1.0)
    h = np.where(mx == r, ((g - b) / safe) % 6, np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4))
    return (h * 60.0) % 360.0, c


def _shape(h: np.ndarray) -> np.ndarray:
    """Fully saturated hexcone color for hue ``h`` (min 0, max 1), luma removed."""
    k = (np.array([5.0, 3.0, 1.0]) + np.asarray(h)[..., None] / 60.0) % 6
    s = 1.0 - np.clip(np.minimum(k, 4 - k), 0.0, 1.0)
    return s - (s @ _LUMA)[..., None]


def _recompose(rgb: np.ndarray, hue: np.ndarray, chroma: np.ndarray, shift: np.ndarray | float = 0.0) -> np.ndarray:
    """Rebuild ``rgb`` with a new hue and chroma at the same luma (plus ``shift``); neutrals pass through."""
    luma = rgb @ _LUMA + shift
    out = luma[..., None] + chroma[..., None] * _shape(hue)
    return np.where((np.ptp(rgb, axis=-1) > 0)[..., None], out, rgb)


def _band_weights(h: np.ndarray) -> np.ndarray:
    """Weight of each of the eight bands for hue ``h``; tent-shaped, summing to 1."""
    idx = np.clip(np.searchsorted(_BAND_CENTERS, h, side="right") - 1, 0, 7)
    lo, hi = _BAND_CENTERS[idx], _BAND_CENTERS[idx + 1]
    t = (h - lo) / (hi - lo)
    w = np.zeros(h.shape + (8,))
    np.put_along_axis(w, idx[..., None], (1 - t)[..., None], axis=-1)
    nxt = ((idx + 1) % 8)[..., None]
    np.put_along_axis(w, nxt, np.take_along_axis(w, nxt, axis=-1) + t[..., None], axis=-1)
    return w


def _band_values(name: str, values: Mapping[str, float] | None, lo: float, hi: float) -> np.ndarray:
    out = np.zeros(8)
    for band, v in (values or {}).items():
        if band not in BANDS:
            raise ParameterError(f"unknown {name} band {band!r}; expected one of {', '.join(BANDS)}")
        out[BANDS.index(band)] = _check(f"{name}.{band}", v, lo, hi)
    return out


def hsl_adjust(enc: np.ndarray, hue: Mapping[str, float] | None = None,
               saturation: Mapping[str, float] | None = None,
               luminance: Mapping[str, float] | None = None) -> np.ndarray:
    """Per-band hue, saturation and luminance (-100..100) over Lightroom's eight color bands."""
    hv, sv, lv = (_band_values(n, m, -100, 100) for n, m in
                  (("hue", hue), ("saturation", saturation), ("luminance", luminance)))
    enc = _as_rgb(enc)
    if not (hv.any() or sv.any() or lv.any()):
        return enc
    h, c = _hue(enc)
    w = _band_weights(h)
    gaps = np.diff(_BAND_CENTERS)  # gap from band i to band i+1
    toward = np.where(hv > 0, gaps, np.roll(gaps, 1))
    new_h = (h + (w @ (K_HUE * hv / 100 * toward))) % 360
    new_c = c * np.maximum(1 + w @ (sv / 100), 0.0)
    return _recompose(enc, new_h, new_c, shift=K_LUMINANCE * (w @ (lv / 100)) * c)


# --- color grading -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Zone:
    """One color-grading wheel: hue (0-360), saturation (0-100), luminance (-100..100)."""

    hue: float = 0.0
    sat: float = 0.0
    lum: float = 0.0

    def check(self, name: str) -> Zone:
        _check(f"{name}.hue", self.hue, 0, 360)
        _check(f"{name}.sat", self.sat, 0, 100)
        _check(f"{name}.lum", self.lum, -100, 100)
        return self

    @property
    def neutral(self) -> bool:
        return self.sat == 0 and self.lum == 0


def color_grade(enc: np.ndarray, shadows: Zone = Zone(), midtones: Zone = Zone(), highlights: Zone = Zone(),
                global_: Zone = Zone(), blending: float = 50.0, balance: float = 0.0) -> np.ndarray:
    """Color grading / split toning: three per-channel tone curves on linear working values.

    The wheels tint and brighten tonal zones the way Lightroom does (reconstructed, and matched
    to its bakes to 0.01 dE on every wheel, luminance, balance and blending setting). Each
    curve depends only on the settings, so a look bakes it once: into its 3D LUT, or as three
    1-D textures. Values above 1 keep their excess.
    """
    zones = tuple(z.check(n) for z, n in ((shadows, "shadows"), (midtones, "midtones"),
                                         (highlights, "highlights"), (global_, "global")))
    _check("blending", blending, 0, 100)
    _check("balance", balance, -100, 100)
    enc = _as_rgb(enc)
    if all(z.neutral for z in zones):
        return enc
    curves = grade_curves(shadows, midtones, highlights, global_, blending, balance)
    lin = cs.decode(enc, WORKING_TRANSFER)
    inside = np.clip(lin, 0.0, 1.0)
    out = np.stack([curve(inside[..., c]) for c, curve in enumerate(curves)], axis=-1) + (lin - inside)
    return cs.encode(out, WORKING_TRANSFER)


# Luminance weights of linear working (ProPhoto) red and green; blue's is ~0. A wheel's tint keeps
# this luminance at 1, so hue and saturation recolor without brightening.
_TINT_LUMA = (0.2880859375, 0.7119140625, 0.0)
# hexcone sector of the hue -> (channel at the top, channel moving between, its 0..1 position)
_TINT_SECTORS = ((0, 1, lambda h: h / 60), (1, 0, lambda h: (120 - h) / 60), (1, 2, lambda h: (h - 120) / 60),
                 (2, 1, lambda h: (240 - h) / 60), (2, 0, lambda h: (h - 240) / 60), (0, 2, lambda h: (360 - h) / 60))


def _wheel_tint(hue: float, strength: float, lo: float, hi: float) -> np.ndarray:
    """Per-channel factors for a wheel's hue: unit luminance, spread ``strength * (hi - lo)``.

    The top channel sits ``spread`` above the lowest, the hue's neighbour in between; the lowest
    is kept in [lo, hi] and so that the top stays in range too.
    """
    top, mid, pos = _TINT_SECTORS[int(hue % 360 // 60)]
    spread = (hi - lo) * strength
    f = pos(hue % 360)
    base = 1 - spread * (_TINT_LUMA[top] + f * _TINT_LUMA[mid])
    base = max(min(max(min(base, hi), lo), hi - spread), lo - spread)
    out = np.full(3, base)
    out[top], out[mid] = base + spread, base + f * spread
    return out


def _bend(x: np.ndarray, d: float) -> np.ndarray:
    """Rational curve through (0, 0), (d, 0.5) and (1, 1)."""
    k = (1 - d) / d
    return k * x / (k * x + 1 - x)


def _unbend(y: np.ndarray, d: float) -> np.ndarray:
    k = (1 - d) / d
    return (y / k) / (y / k + 1 - y)


def _zone_cubic(q: np.ndarray, low: float, high: float) -> np.ndarray:
    """Cubic through (0, 0) and (1, 1) whose shadow and highlight slopes follow ``low``/``high``
    (``low`` 1, ``high`` 2 is the identity)."""
    return q * (q * q + (low + q * (high - low)) * (1 - q))


def _lift(t: np.ndarray) -> np.ndarray:
    return np.where(t < 0.48, ((0.72338 * t) * t + 0.5) * t + 0.16, t)


def _dim(t: np.ndarray) -> np.ndarray:
    return np.where(t > 0.52, ((0.72338 * t - 2.17014) * t + 2.67014) * t - 0.38338, t)


def grade_curves(shadows: Zone = Zone(), midtones: Zone = Zone(), highlights: Zone = Zone(),
                 global_: Zone = Zone(), blending: float = 50.0,
                 balance: float = 0.0) -> tuple[Callable[[np.ndarray], np.ndarray], ...]:
    """The red, green and blue curves of a color grade, on linear working values in [0, 1].

    Shadows and highlights tint by cubic zone curves split at a balance point (the sRGB-decoded
    0.5 -/+ 0.4 * balance); blending mixes the split toward one smooth curve. Midtones and the
    global wheel bend each channel around its middle. The zone luminances act on the
    sRGB-encoded value: shadows lift (black too), highlights dim (white too).
    """
    bw = float(_bend(np.array(blending / 100), 0.7))
    spread = 0.9 + 0.002 * max(blending - 50, 0.0)
    shadow = _wheel_tint(shadows.hue, shadows.sat * spread / 100, 0.05, 2.5)
    highlight = 3 - _wheel_tint(highlights.hue, -highlights.sat * spread / 100, 0.2, 2.5)
    middle = _wheel_tint(midtones.hue, 1.0, -1.0, 1.0) * midtones.sat / 100
    overall = _wheel_tint(global_.hue, 1.0, -1.0, 1.0) * global_.sat / 100
    fade = 1 - 0.85 * abs(balance) / 100
    shadow_lum, highlight_lum = shadows.lum, highlights.lum
    if shadow_lum > 0:
        shadow_lum *= 0.7 * (fade if balance > 0 else 1.0)
    if highlight_lum < 0:
        highlight_lum *= 0.7 * (fade if balance < 0 else 1.0)
    low, high = 1 + shadow_lum / 100, 2 + highlight_lum / 100
    mid_lum, global_lum = midtones.lum / 100, global_.lum / 100
    split = float(cs.decode(np.array(0.5 - 0.4 * np.clip(balance / 100, -1, 1)), WORKING_TRANSFER))
    gray = float(cs.decode(np.array(0.5), WORKING_TRANSFER))

    def curve(c: int) -> Callable[[np.ndarray], np.ndarray]:
        def apply(x: np.ndarray) -> np.ndarray:
            xs = _bend(x, split)
            bell = 1 - (2 * xs - 1) ** 2
            narrow = bell * bell
            y = x
            if low != 1 or high != 2:
                y = np.where(xs <= 0.5, _zone_cubic(xs, low, 2), _zone_cubic(xs, 1, high))
                y = y + (xs - y) * narrow
                y = y + (_zone_cubic(xs, low, high) - y) * bw
                t = cs.encode(_unbend(y, split), WORKING_TRANSFER)
                if low > 1:
                    t = t + (_lift(t) - t) * (low - 1)
                if high < 2:
                    t = t + (_dim(t) - t) * (2 - high)
                y = cs.decode(t, WORKING_TRANSFER)
            ys = _bend(y, split)
            out = _zone_cubic(ys, shadow[c], highlight[c])
            if shadow[c] != 1 or highlight[c] != 2:
                split_tone = np.where(xs <= 0.5, _zone_cubic(ys, shadow[c], 2), _zone_cubic(ys, 1, highlight[c]))
                split_tone = split_tone + (ys - split_tone) * narrow
                out = split_tone + (out - split_tone) * bw
            weight = narrow + (bell - narrow) * bw
            if middle[c]:
                out = out + (_bend(out, 0.5 - 0.14 * middle[c]) - out) * weight
            if mid_lum:
                out = out + (_bend(out, 0.5 - 0.12 * mid_lum) - out) * weight
            out = _unbend(out, split)
            if overall[c] or global_lum:
                z = _bend(out, gray)
                if overall[c]:
                    z = _bend(z, 0.5 - 0.14 * overall[c])
                if global_lum:
                    z = _bend(z, 0.5 - 0.12 * global_lum)
                out = _unbend(z, gray)
            return out
        return apply

    return tuple(curve(c) for c in range(3))


# --- saturation, vibrance, grayscale ----------------------------------------------------------------

def saturation_vibrance(enc: np.ndarray, saturation: float = 0.0, vibrance: float = 0.0) -> np.ndarray:
    """Vibrance (Lightroom's measured response, favouring muted colors), then global saturation (-100..100)."""
    _check("saturation", saturation, -100, 100)
    _check("vibrance", vibrance, -100, 100)
    enc = _as_rgb(enc)
    if vibrance:
        h, c = _hue(enc)
        vib = _response().vibrance
        gain = np.maximum(vib.gain(c, _band_weights(h), vibrance), 0.0)
        pivot = enc.min(axis=-1) + vib.pivot(vibrance) * c  # a gray between the min and max channel
        enc = pivot[..., None] + gain[..., None] * (enc - pivot[..., None])
    if saturation:
        h, c = _hue(enc)
        enc = _recompose(enc, h, c * max(1 + saturation / 100, 0.0))
    return enc


def grayscale(enc: np.ndarray, mixer: Mapping[str, float] | None = None) -> np.ndarray:
    """Black-and-white conversion; ``mixer`` brightens or darkens each band (-100..100)."""
    mv = _band_values("mixer", mixer, -100, 100)
    enc = _as_rgb(enc)
    h, c = _hue(enc)
    gray = enc @ _LUMA + K_MIXER * (_band_weights(h) @ (mv / 100)) * c
    return np.where((c > 0)[..., None], np.repeat(gray[..., None], 3, axis=-1), enc)


# --- whole preset -------------------------------------------------------------------------------------

_NUMERIC = {  # setting -> (min, max); look values add to the user's, clamped to the range
    "IncrementalTemperature": (-100, 100), "IncrementalTint": (-100, 100), "Exposure2012": (-5, 5),
    "Contrast2012": (-100, 100), "Whites2012": (-100, 100), "Blacks2012": (-100, 100),
    "ParametricShadows": (-100, 100), "ParametricDarks": (-100, 100),
    "ParametricLights": (-100, 100), "ParametricHighlights": (-100, 100),
    "Saturation": (-100, 100), "Vibrance": (-100, 100),
    "ColorGradeMidtoneLum": (-100, 100), "ColorGradeShadowLum": (-100, 100),
    "ColorGradeHighlightLum": (-100, 100), "ColorGradeGlobalLum": (-100, 100),
    **{f"{kind}Adjustment{b.capitalize()}": (-100, 100)
       for kind in ("Hue", "Saturation", "Luminance") for b in BANDS},
    **{f"GrayMixer{b.capitalize()}": (-100, 100) for b in BANDS},
}
_CURVES = ("ToneCurvePV2012", "ToneCurvePV2012Red", "ToneCurvePV2012Green", "ToneCurvePV2012Blue")


@dataclass(frozen=True)
class _Sliders:
    """A preset's settings with its look profile's own settings folded in."""

    preset: Preset
    profile: Preset | None
    amount: float

    def number(self, key: str, default: float = 0.0) -> float:
        value = self.preset.number(key, default)
        if self.profile is not None and key in _NUMERIC and self.profile.has(key):
            lo, hi = _NUMERIC[key]
            value = float(np.clip(value + self.amount * self.profile.number(key), lo, hi))
        return value

    def text(self, key: str) -> str:
        for p in (self.preset, self.profile):
            if p is not None and p.has(key):
                return p.text(key) or ""
        return ""

    def curves(self, key: str) -> list[Curve]:
        found = [p.curve(key) for p in (self.profile, self.preset) if p is not None]
        return [c for c in found if not _is_identity(c)]

    def bands(self, prefix: str) -> dict[str, float]:
        return {b: self.number(f"{prefix}{b.capitalize()}") for b in BANDS}

    def has(self, key: str) -> bool:
        return any(p is not None and p.has(key) for p in (self.preset, self.profile))

    def zone(self, name: str, legacy: str | None = None) -> Zone:
        hue = self.number(f"ColorGrade{name}Hue")
        sat = self.number(f"ColorGrade{name}Sat")
        if legacy and not sat:
            hue, sat = self.number(f"SplitToning{legacy}Hue"), self.number(f"SplitToning{legacy}Saturation")
        return Zone(hue=hue, sat=sat, lum=self.number(f"ColorGrade{name}Lum"))


def _sliders(preset: Preset) -> _Sliders:
    look = preset.look
    profile = look.source if look is not None else None
    return _Sliders(preset, profile, look.amount if look is not None else 1.0)


def _look_table(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    look = s.preset.look
    table = look.table if look is not None else None
    if table is None and s.preset.rgb_table is not None:
        table, amount = s.preset.rgb_table, 1.0
    elif table is not None:
        amount = look.amount
    else:
        return enc
    lin = cs.decode(enc, WORKING_TRANSFER)
    out = apply_rgb_table(table, lin, WORKING, amount=amount).astype(np.float64)
    return cs.encode(out, WORKING_TRANSFER)


def _stage_tone(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    return tone(enc, s.number("Contrast2012"), s.number("Whites2012"), s.number("Blacks2012"))


def _stage_parametric(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    return parametric_curve(
        enc, s.number("ParametricShadows"), s.number("ParametricDarks"), s.number("ParametricLights"),
        s.number("ParametricHighlights"),
        splits=(s.number("ParametricShadowSplit", 25), s.number("ParametricMidtoneSplit", 50),
                s.number("ParametricHighlightSplit", 75)),
    )


def _stage_curves(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    for master in s.curves("ToneCurvePV2012"):
        enc = apply_curves(enc, master=master)
    for i, key in enumerate(_CURVES[1:]):
        for c in s.curves(key):
            enc = apply_curves(enc, **{("red", "green", "blue")[i]: c})
    return enc


def _stage_hsl(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    return hsl_adjust(enc, s.bands("HueAdjustment"), s.bands("SaturationAdjustment"),
                      s.bands("LuminanceAdjustment"))


_LEGACY_SPLIT_TONING = ("SplitToningShadowHue", "SplitToningShadowSaturation", "SplitToningHighlightHue",
                        "SplitToningHighlightSaturation", "SplitToningBalance")


def _stage_grade(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    # Measured: Lightroom reads settings with split-toning keys but no blending as legacy split
    # toning, which is Blending 100; color-grading settings default to 50.
    legacy = any(s.has(k) for k in _LEGACY_SPLIT_TONING)
    blending = s.number("ColorGradeBlending", 50.0 if s.has("ColorGradeBlending") or not legacy else 100.0)
    return color_grade(enc, shadows=s.zone("Shadow", legacy="Shadow"), midtones=s.zone("Midtone"),
                       highlights=s.zone("Highlight", legacy="Highlight"), global_=s.zone("Global"),
                       blending=blending, balance=s.number("SplitToningBalance"))


def _stage_saturation(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    return saturation_vibrance(enc, s.number("Saturation"), s.number("Vibrance"))


def _stage_grayscale(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    if s.text("ConvertToGrayscale").lower() != "true":
        return enc
    return grayscale(enc, s.bands("GrayMixer"))


ENCODED_STAGES: dict[str, Callable[[_Sliders, np.ndarray], np.ndarray]] = {
    "tone": _stage_tone,
    "parametric": _stage_parametric,
    "curves": _stage_curves,
    "look": _look_table,
    "hsl": _stage_hsl,
    "grade": _stage_grade,
    "saturation": _stage_saturation,
    "grayscale": _stage_grayscale,
}
ORDER: tuple[str, ...] = ("tone", "parametric", "curves", "look", "hsl", "grade", "saturation", "grayscale")


def render(preset: Preset, image: np.ndarray, space: cs.RGBSpace = cs.SRGB) -> np.ndarray:
    """Apply ``preset``'s global color to an sRGB-curve encoded ``image`` in ``space``.

    Returns float32 values in [0, 1] with the input's shape.
    """
    img = _as_rgb(image)
    s = _sliders(preset)
    lin = cs.convert(cs.decode(np.clip(img, 0.0, 1.0), WORKING_TRANSFER), space, WORKING)
    enc = cs.encode(np.maximum(lin, 0.0), WORKING_TRANSFER)
    enc = white_balance(enc, s.number("IncrementalTemperature"), s.number("IncrementalTint"))
    enc = exposure(enc, s.number("Exposure2012"))
    for name in ORDER:
        enc = ENCODED_STAGES[name](s, enc)
    out = cs.convert(cs.decode(enc, WORKING_TRANSFER), WORKING, space)
    return np.clip(cs.encode(out, WORKING_TRANSFER), 0.0, 1.0).astype(np.float32)


def render_lut(preset: Preset, size: int = 33) -> Lut3D:
    """Bake ``preset``'s global color into a ``size``-point 3D LUT (sRGB in, sRGB out)."""
    grid = Lut3D.identity(size).table.astype(np.float64)
    return Lut3D(render(preset, grid), title=preset.name)
