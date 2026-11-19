"""Reference pipeline: a preset's global color sliders, applied in Lightroom's order.

Lightroom's exact formulas are not public. Every stage here is a plain, documented
model with the properties a photographer expects: identity at default, endpoints
held, tone curves monotonic. White balance, exposure, the tone sliders and the
parametric curve take their shapes from measured Lightroom responses
(``colorlab.response``). Saturation, vibrance and color grading are reconstructions of
Lightroom's behaviour, checked against its bakes; the ``K_*`` strengths of the other
stages are fitted to single-slider Lightroom bakes (``colorlab.calibration``).

Working space is ProPhoto RGB primaries. Stages take and return sRGB-curve encoded
values, the space Lightroom's tone curve panel shows; saturation and vibrance work
inside on the exposure shoulder's stretched (scene-linear) scale, color grading on linear values. Spatial effects (grain,
vignette, highlights/shadows, clarity, texture, dehaze, sharpening, noise reduction) belong to a later stage and
are not applied here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from . import colorspace as cs
from . import response
from .adobe_table import apply_hue_sat_map, apply_rgb_table
from .errors import ParameterError
from .lut import Lut3D
from .preset import IDENTITY_CURVE, Preset

WORKING = cs.PROPHOTO_RGB
WORKING_TRANSFER = cs.Transfer.SRGB

BANDS = ("red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta")
_LUMA = WORKING.rgb_to_xyz[1] / WORKING.rgb_to_xyz[1].sum()
# Bradford cone responses from linear working RGB, each row scaled so white maps to (1, 1, 1)
_CONES = cs._BRADFORD @ WORKING.rgb_to_xyz
_CONES = _CONES / (_CONES @ np.ones(3))[:, None]
_CONES_INV = np.linalg.inv(_CONES)

#: measured shapes of white balance, exposure, the tone sliders and the parametric curve;
#: read from the shipped file on first use, so a broken file never stops ``calibration`` from
#: importing (and regenerating it)
RESPONSE: response.Response | None = None


def _response() -> response.Response:
    global RESPONSE
    if RESPONSE is None:
        RESPONSE = response.load()
    return RESPONSE

# Constants fitted with `colorlab.calibration` to single-slider Lightroom bakes.
#: the HSL stage's unit on the stretched (scene-linear) scale: vibrance and the band luminance and
#: saturation taper as a channel nears 1 in this unit (fitted to the HSL and Vibrance bakes)
K_SCENE = 0.141  # 2026-11-18, all 147 bakes

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
        out = _hermite(x, y, slopes, v)
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


def rgb_tone(rgb: np.ndarray, curve: Callable[[np.ndarray], np.ndarray], pull: bool = False) -> np.ndarray:
    """Apply a monotonic tone ``curve`` to colors without shifting their hue.

    The curve maps each pixel's largest and smallest channel; the middle channel keeps its
    relative place between them (the "RGB tone" method of Adobe's public DNG SDK). Neutrals
    get exactly ``curve``. Per-channel curves would instead push bright saturated colors
    toward the primaries and secondaries (red toward orange, blue toward cyan).

    With ``pull`` (Lightroom's curves from process version 15.4), the curve reads the max and min
    pulled toward the middle channel, by ``t**2 / 2`` and ``(1 - t)**2 / 2`` of the way (``t`` the
    middle's place), and the outputs are the max and min whose pulled values the curve gave, kept
    in [0, 1]. A color whose middle sits near its max is then toned more like that pair.
    """
    rgb = _as_rgb(rgb)
    hi, lo = rgb.max(axis=-1), rgb.min(axis=-1)
    span = hi - lo
    place = (rgb - lo[..., None]) / np.where(span > 0, span, 1.0)[..., None]
    if not pull:
        f_hi, f_lo = curve(hi), curve(lo)
    else:
        t = np.median(place, axis=-1)
        mid = lo + t * span
        f_hi = curve(hi + (mid - hi) * t * t / 2)
        f_lo = curve(lo + (mid - lo) * (1 - t) ** 2 / 2)
        # invert the pull: the max and min whose pulled values are f_hi and f_lo
        t2, inv = t * t, 1.0 / (t * t - t + 2.0)
        f_hi, f_lo = (np.clip(inv * ((2 + 2 * t2 - t - t * t2) * f_hi + (t - 1) * t2 * f_lo), 0.0, 1.0),
                      np.clip(inv * (-t * (t - 1) ** 2 * f_hi + (t * t2 - t2 + 2) * f_lo), 0.0, 1.0))
    return f_lo[..., None] + place * (f_hi - f_lo)[..., None]


def _version(text: str) -> tuple[int, int]:
    """A process version string such as "15.4" as (major, minor); (0, 0) when missing or unreadable."""
    parts = (text or "").split(".")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return 0, 0


def curve_tone(enc: np.ndarray, curve: Callable[[np.ndarray], np.ndarray], process_version: str = "") -> np.ndarray:
    """Lightroom's tone curve (Contrast, the parametric and the point curve) on encoded colors.

    Before process version 15.4 the encoded ``curve`` maps each color's *linear* max and min
    (:func:`rgb_tone`), so the hue of its linear values is kept, as exposure keeps it. From 15.4
    Lightroom moves these curves to a second stage that combines them into one curve on
    sRGB-encoded values (our working encoding) and applies it with the pull
    (``rgb_tone(pull=True)``). Pulled tones compose exactly, so applying the three curves in turn
    gives the same result. That stage's luminance mode is :func:`luma_curve`, used only for the point
    curve when Refine Saturation is below 100 (:func:`apply_curves`).
    """
    enc = _as_rgb(enc)
    if _version(process_version) >= (15, 4):
        return rgb_tone(enc, lambda v: curve(np.clip(v, 0.0, 1.0)), pull=True)
    lin = rgb_tone(cs.decode(enc, WORKING_TRANSFER),
                   lambda x: cs.decode(curve(np.clip(cs.encode(np.maximum(x, 0.0), WORKING_TRANSFER), 0.0, 1.0)),
                                       WORKING_TRANSFER))
    return cs.encode(np.maximum(lin, 0.0), WORKING_TRANSFER)


_CURVE_LUMA = np.array([0.30, 0.59, 0.11])  # the luminance mode's weights, on encoded values


def luma_curve(enc: np.ndarray, curve: Callable[[np.ndarray], np.ndarray], blend: float = 1.0) -> np.ndarray:
    """A tone ``curve`` in Lightroom's luminance mode (process version 15.4's Refine Saturation),
    on encoded values.

    Each channel goes through the curve, and the curved values' luma ``Y`` (0.30 R + 0.59 G +
    0.11 B) sets one shift for all channels: ``Y`` minus the input's luma. So the curve changes
    brightness while the channels keep their differences, and the curve's contrast doesn't add
    saturation. A result outside [0, 1] is scaled toward ``Y`` until it fits, first below 0, then
    above 1. ``blend`` (0..1) mixes this with the pulled hue-preserving tone
    (``rgb_tone(pull=True)``), which it is at 0. Grays always get exactly ``curve``.
    """
    _check("blend", blend, 0, 1)
    enc = _as_rgb(enc)
    def bounded(v: np.ndarray) -> np.ndarray:
        return curve(np.clip(v, 0.0, 1.0))
    hue = rgb_tone(enc, bounded, pull=True)
    if blend == 0:
        return hue
    y = (bounded(enc) @ _CURVE_LUMA)[..., None]
    out = enc + (y - (enc @ _CURVE_LUMA)[..., None])
    lo = out.min(axis=-1, keepdims=True)
    out = y + (out - y) * np.where(lo < 0, y / np.where(lo < 0, y - lo, 1.0), 1.0)
    hi = out.max(axis=-1, keepdims=True)
    out = y + (out - y) * np.where(hi > 1, (1 - y) / np.where(hi > 1, hi - y, 1.0), 1.0)
    return hue + (out - hue) * blend


def apply_curves(rgb: np.ndarray, master: Curve = IDENTITY_CURVE, red: Curve = IDENTITY_CURVE,
                 green: Curve = IDENTITY_CURVE, blue: Curve = IDENTITY_CURVE, process_version: str = "",
                 refine_saturation: float = 100.0) -> np.ndarray:
    """Master curve (hue-preserving, see :func:`curve_tone`), then each channel's own curve (encoded values).

    From process version 15.4, Refine Saturation (0..100) below 100 moves the master curve to the
    luminance mode (:func:`luma_curve`) with blend ``(100 - refine_saturation) / 100``.
    """
    _check("refine saturation", refine_saturation, 0, 100)
    out = _as_rgb(rgb).copy()
    if not _is_identity(master):
        if _version(process_version) >= (15, 4) and refine_saturation < 100:
            out = luma_curve(out, spline(master), (100 - refine_saturation) / 100)
        else:
            out = curve_tone(out, spline(master), process_version)
    for i, curve in enumerate((red, green, blue)):
        if not _is_identity(curve):
            out[..., i] = spline(curve)(out[..., i])
    return out


# --- white balance, exposure and tone (encoded, measured shapes) -----------------------------

def white_balance(enc: np.ndarray, temperature: float = 0.0, tint: float = 0.0) -> np.ndarray:
    """Incremental temperature/tint (-100..100): Lightroom's measured stops per Bradford cone,
    as gains on the scene scale (see :func:`white_balance_stops`), so black and white stay put."""
    _check("temperature", temperature, -100, 100)
    _check("tint", tint, -100, 100)
    enc = _as_rgb(enc)
    if temperature == 0 and tint == 0:
        return enc
    wb = _response().white_balance
    return white_balance_stops(enc, wb.evs(temperature, tint), wb.space)


def white_balance_stops(enc: np.ndarray, evs: np.ndarray, space: str = "working",
                        shoulder: response.ExposureShoulder | None = None) -> np.ndarray:
    """Per-channel stops ``evs`` as gains on the scene scale (:func:`_to_scene`, on ``shoulder``,
    default the shipped one), in the working space's channels or in Bradford cone space
    (``response.WB_SPACES``; cones scaled so white is 1, 1, 1: a von Kries adaptation).

    A stop is ``2 ** shoulder.strength`` on the scene scale, as for exposure, so equal stops in
    every channel are exposure. Black stays black and white stays white.
    """
    if space not in response.WB_SPACES:
        raise ParameterError(f"white balance space {space!r} is not one of {', '.join(response.WB_SPACES)}")
    enc = _as_rgb(enc)
    evs = np.asarray(evs, dtype=np.float64)
    if not np.any(evs):
        return enc
    shoulder = shoulder if shoulder is not None else _response().exposure
    gains = 2.0 ** (shoulder.strength * evs)
    scene = _to_scene(enc, shoulder)
    if space == "bradford":
        scene = (scene @ _CONES.T * gains) @ _CONES_INV.T
    else:
        scene = scene * gains
    return _from_scene(scene, shoulder)


def exposure(enc: np.ndarray, ev: float) -> np.ndarray:
    """Exposure in stops (-5..5): a gain on the scene scale, so it rolls off along Lightroom's
    shoulder, keeps white, never clips and keeps the hue of the linear values."""
    _check("exposure", ev, -5, 5)
    enc = _as_rgb(enc)
    if ev == 0:
        return enc
    shoulder = _response().exposure
    return _from_scene(_to_scene(enc, shoulder) * 2.0 ** (shoulder.strength * ev), shoulder)


def tone(enc: np.ndarray, contrast: float = 0.0, whites: float = 0.0, blacks: float = 0.0,
         process_version: str = "") -> np.ndarray:
    """Contrast, Whites and Blacks (-100..100): Lightroom's measured curves, in that order, applied
    hue-preservingly (:func:`curve_tone`). Grays get exactly the measured curves. :func:`render`
    applies Whites and Blacks in :func:`whites_blacks` instead, the way Lightroom does for colors."""
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

    return curve_tone(enc, curve, process_version)


_BRIGHTNESS_KEY = np.array([0.25, 0.5, 0.25])  # Whites - and Blacks + read (R + 2G + B) / 4


def whites_blacks(enc: np.ndarray, whites: float = 0.0, blacks: float = 0.0) -> np.ndarray:
    """Whites and Blacks (-100..100) as Lightroom applies them to colors, with exposure, before
    saturation and HSL. Grays get the measured curves (:func:`tone`); colors, by direction:

    - Whites +: the curve on each color's linear max and min (the exposure clip and rolloff).
    - Whites - and Blacks +: one gain for all channels on the scene scale (:func:`_to_scene`),
      the curve's ratio at the brightness key (R + 2G + B) / 4, so scene chroma ratios are kept.
    - Blacks -: the curve on each channel on its own, on the scene scale, so dark channels of a
      saturated color reach black before its bright ones.

    Whites apply first, then Blacks.
    """
    _check("whites", whites, -100, 100)
    _check("blacks", blacks, -100, 100)
    enc = _as_rgb(enc)
    tones = _response().tone
    if whites > 0:
        enc = curve_tone(enc, lambda v: tones["Whites2012"].apply(v, whites))
    elif whites < 0:
        enc = _scene_ratio(enc, _scene_curve(tones["Whites2012"], whites))
    if blacks < 0:
        enc = _from_scene(_scene_curve(tones["Blacks2012"], blacks)(_to_scene(enc)))
    elif blacks > 0:
        enc = _scene_ratio(enc, _scene_curve(tones["Blacks2012"], blacks))
    return enc


def _scene_curve(curve: response.CurveFamily, amount: float) -> Callable[[np.ndarray], np.ndarray]:
    """A measured curve on encoded grays as a function on the scene scale."""
    sh = _response().exposure
    return lambda s: sh.stretch(np.clip(curve.apply(np.clip(sh.rolloff(np.maximum(s, 0.0)), 0.0, 1.0), amount),
                                        0.0, 1.0))


def _scene_ratio(enc: np.ndarray, curve: Callable[[np.ndarray], np.ndarray]) -> np.ndarray:
    scene = _to_scene(enc)
    key = scene @ _BRIGHTNESS_KEY
    return _from_scene(scene * (curve(key) / np.maximum(key, 1e-12))[..., None])


def parametric_curve(enc: np.ndarray, shadows: float = 0.0, darks: float = 0.0, lights: float = 0.0,
                     highlights: float = 0.0, splits: Sequence[float] = (25, 50, 75),
                     process_version: str = "") -> np.ndarray:
    """Lightroom's four-region parametric curve (-100..100); ``splits`` are the region dividers (0-100).
    Applied hue-preservingly (:func:`curve_tone`)."""
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

    return curve_tone(enc, curve, process_version)


# --- hue bands -----------------------------------------------------------------------------------

def _band_values(name: str, values: Mapping[str, float] | None, lo: float, hi: float) -> np.ndarray:
    out = np.zeros(8)
    for band, v in (values or {}).items():
        if band not in BANDS:
            raise ParameterError(f"unknown {name} band {band!r}; expected one of {', '.join(BANDS)}")
        out[BANDS.index(band)] = _check(f"{name}.{band}", v, lo, hi)
    return out


def hsl_adjust(enc: np.ndarray, hue: Mapping[str, float] | None = None,
               saturation: Mapping[str, float] | None = None,
               luminance: Mapping[str, float] | None = None,
               vibrance: float = 0.0, smooth: bool = False) -> np.ndarray:
    """Vibrance, then per-band hue, saturation and luminance (all -100..100), as Lightroom's HSL.

    Runs on scene-linear values (see :func:`_to_color_scene`, in units of ``K_SCENE``), split into
    each color's smallest and largest channel and its hexcone hue. A
    band's settings fade linearly from its center to the neighbouring centers; ``smooth``
    (process version 15.4 and later) fades them along a monotone spline instead and splits colors
    with the smooth hue model. Hue moves a color toward the neighbouring band, by at most 90% of
    the gap to it but at least 30 degrees. Saturation cuts pull min and max toward their middle;
    boosts lower the min. Luminance scales both, less for bright channels and near-neutrals.
    """
    hv, sv, lv = (_band_values(n, m, -100, 100) / 100 for n, m in
                  (("hue", hue), ("saturation", saturation), ("luminance", luminance)))
    _check("vibrance", vibrance, -100, 100)
    enc = _as_rgb(enc)
    if not (vibrance or hv.any() or sv.any() or lv.any()):
        return enc
    lo, hi, h, gap = _to_mmh(_to_color_scene(enc) * K_SCENE, smooth)
    if vibrance:
        lo, hi = _vibrance(lo, hi, h, vibrance / 100)
        gap = hi - lo
    if hv.any() or sv.any() or lv.any():
        hue_table = _band_table(_hue_shifts(hv), smooth) + np.arange(_TABLE_SIZE + 2) / 60.0
        if hue_table[0] < 0:
            hue_table = hue_table + 6.0
        lo, hi, h = _tune_bands(lo, hi, h, gap, hue_table, _band_table(sv, smooth), _band_table(lv, smooth))
    return _from_color_scene(_from_mmh(lo, hi, h, smooth) / K_SCENE)


def _to_scene(enc: np.ndarray, shoulder: response.ExposureShoulder | None = None) -> np.ndarray:
    """Working-encoded values -> the scene-linear values Lightroom's color sliders work on.

    The exposure shoulder's stretch, applied as Lightroom applies its tone curve: to each color's
    largest and smallest channel, with the middle one kept in place (:func:`rgb_tone`). So a
    color's hexcone hue stays that of its linear values; stretching each channel on its own
    would bend the hue of saturated colors. ``shoulder`` defaults to the shipped one.
    """
    stretch = (shoulder if shoulder is not None else _response().exposure).stretch
    return rgb_tone(cs.decode(enc, WORKING_TRANSFER),
                    lambda x: stretch(cs.encode(np.maximum(x, 0.0), WORKING_TRANSFER)))


def _from_scene(scene: np.ndarray, shoulder: response.ExposureShoulder | None = None) -> np.ndarray:
    """The inverse of :func:`_to_scene`."""
    rolloff = (shoulder if shoulder is not None else _response().exposure).rolloff
    lin = rgb_tone(np.maximum(scene, 0.0), lambda y: cs.decode(rolloff(y), WORKING_TRANSFER))
    return cs.encode(np.maximum(lin, 0.0), WORKING_TRANSFER)


#: encoded value above which the colour sliders' scene scale goes on linearly. The shoulder's stretch
#: runs off to infinity at white, which made saturation wipe out near-white tints' low channel; fitted
#: on the PV 15.4 colour bakes' ProPhoto exports (0.94 of 0.90-0.98; colour stages only, since
#: white balance and exposure fit the plain shoulder)
COLOR_KNEE = 0.94


def _color_stretch(shoulder: response.ExposureShoulder) -> tuple[Callable, Callable]:
    """The shoulder's stretch up to :data:`COLOR_KNEE`, then a straight line with its slope there."""
    knee = np.array(COLOR_KNEE)
    top = shoulder.stretch(knee)
    slope = (shoulder.stretch(knee + 1e-7) - shoulder.stretch(knee - 1e-7)) / 2e-7

    def forward(e: np.ndarray) -> np.ndarray:
        return np.where(e <= COLOR_KNEE, shoulder.stretch(np.minimum(e, COLOR_KNEE)), top + slope * (e - COLOR_KNEE))

    def inverse(y: np.ndarray) -> np.ndarray:
        return np.where(y <= top, shoulder.rolloff(np.minimum(y, top)), COLOR_KNEE + (y - top) / slope)

    return forward, inverse


def _to_color_scene(enc: np.ndarray, shoulder: response.ExposureShoulder | None = None) -> np.ndarray:
    """Working-encoded values -> the scene scale of saturation, vibrance, HSL and B&W: that of
    :func:`_to_scene`, with a finite top (:data:`COLOR_KNEE`). ``shoulder`` replaces the exposure's."""
    forward, _ = _color_stretch(shoulder or _response().exposure)
    return rgb_tone(cs.decode(enc, WORKING_TRANSFER),
                    lambda x: forward(cs.encode(np.maximum(x, 0.0), WORKING_TRANSFER)))


def _from_color_scene(scene: np.ndarray, shoulder: response.ExposureShoulder | None = None) -> np.ndarray:
    """The inverse of :func:`_to_color_scene`."""
    _, inverse = _color_stretch(shoulder or _response().exposure)
    lin = rgb_tone(np.maximum(scene, 0.0), lambda y: cs.decode(np.maximum(inverse(y), 0.0), WORKING_TRANSFER))
    return cs.encode(np.maximum(lin, 0.0), WORKING_TRANSFER)


#: hue (degrees) of each band's center, where its sliders have their full effect
HSL_CENTERS = np.array([0.0, 30.0, 55.0, 95.0, 160.0, 230.0, 275.0, 330.0])
_TABLE_SIZE = 360  # band tables hold one entry per degree, plus two that wrap around
_GAP_AHEAD = (np.roll(HSL_CENTERS, -1) - HSL_CENTERS) % 360  # degrees from each center to the next
# channel order (r, g, b) of each hexcone sector, as indices into (min, middle, max)
_SECTOR_CHANNELS = np.array([[2, 1, 0], [1, 2, 0], [0, 2, 1], [0, 1, 2], [1, 0, 2], [2, 0, 1]])


def _hue_shifts(values: np.ndarray) -> np.ndarray:
    """Hue slider per band (-1..1) -> hue shift at the band's center, in sextants (1 = 60 degrees)."""
    gap = np.where(values >= 0, _GAP_AHEAD, np.roll(_GAP_AHEAD, 1))
    return values * np.maximum(0.9 * gap, 30.0) / 60.0


def _band_table(values: np.ndarray, smooth: bool) -> np.ndarray:
    """The eight band values spread over hue, one entry per degree (wrapping)."""
    degrees = np.arange(_TABLE_SIZE + 2, dtype=np.float64)
    if not smooth:
        return np.interp(degrees, HSL_CENTERS, values, period=360.0)
    # five periods so the spline's ends don't matter; the middle period's slopes are used everywhere
    x = np.concatenate([HSL_CENTERS + 360.0 * k for k in range(-2, 3)])
    y = np.tile(values, 5)
    slopes = np.tile(_monotone(x, y, _spline_slopes(x, y))[16:24], 5)
    return _hermite(x, y, slopes, degrees % 360.0)


def _monotone(x: np.ndarray, y: np.ndarray, slopes: np.ndarray) -> np.ndarray:
    """Limit spline slopes so each segment stays monotone (Fritsch-Carlson)."""
    m = slopes.copy()
    for i in range(len(x) - 1):
        d = (y[i + 1] - y[i]) / (x[i + 1] - x[i])
        if d >= 0:
            m[i], m[i + 1] = max(m[i], 0.0), max(m[i + 1], 0.0)
        if d <= 0:
            m[i], m[i + 1] = min(m[i], 0.0), min(m[i + 1], 0.0)
        if d != 0:
            r = (m[i] / d) ** 2 + (m[i + 1] / d) ** 2
            if r > 9:
                m[i], m[i + 1] = m[i] * 3 / np.sqrt(r), m[i + 1] * 3 / np.sqrt(r)
    return m


def _hermite(x: np.ndarray, y: np.ndarray, slopes: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Cubic Hermite interpolation through (x, y) with the given slopes, at ``v`` inside [x0, xn]."""
    j = np.clip(np.searchsorted(x, v, side="right"), 1, len(x) - 1)
    x0, x1, y0, y1, s0, s1 = x[j - 1], x[j], y[j - 1], y[j], slopes[j - 1], slopes[j]
    h = x1 - x0
    t = (v - x0) / h
    t2, t3 = t * t, t * t * t
    return (y0 * (2 * t3 - 3 * t2 + 1) + y1 * (3 * t2 - 2 * t3)
            + h * (s0 * (t3 - 2 * t2 + t) + s1 * (t3 - t2)))


def _lookup(table: np.ndarray, h: np.ndarray) -> np.ndarray:
    """A band table at hue ``h`` (sextants, 0..6), interpolating between degrees."""
    idx = h * (_TABLE_SIZE / 6.0)
    i = np.clip(np.floor(idx).astype(np.int64), 0, _TABLE_SIZE)
    return table[i] + (table[i + 1] - table[i]) * (idx - i)


def _to_mmh(x: np.ndarray, smooth: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Min, max, hexcone hue (0..6) and their gap of non-negative values.

    The smooth model (``smooth``) pulls the max and the min toward the middle channel by
    ``t * t / 2`` and ``(1 - t) ** 2 / 2`` of the way, with ``t`` the middle's place between them.
    """
    x = np.maximum(x, 0.0)
    hi, lo = x.max(axis=-1), x.min(axis=-1)
    gap = hi - lo
    h = _hexcone_hue(x, hi, gap)
    if smooth:
        with np.errstate(invalid="ignore", divide="ignore"):
            t = np.where(gap > 0, (x.sum(axis=-1) - hi - 2 * lo) / np.where(gap > 0, gap, 1.0), 0.0)
        mid = lo + t * gap
        hi, lo = hi + (mid - hi) * t * t * 0.5, lo + (mid - lo) * (1 - t) ** 2 * 0.5
        gap = hi - lo
    return lo, hi, h, gap


def _from_mmh(lo: np.ndarray, hi: np.ndarray, h: np.ndarray, smooth: bool) -> np.ndarray:
    """RGB from min, max and hexcone hue: the inverse of :func:`_to_mmh`."""
    h = np.mod(h, 6.0)
    sector = np.minimum(np.floor(h).astype(np.int64), 5)
    f = h - sector
    t = np.where(sector % 2 == 0, f, 1.0 - f)  # the middle channel's place between min and max
    if smooth:
        k = 1.0 / (t * t - t + 2.0)
        lo, hi = (np.maximum((-t * (t - 1) ** 2 * hi + (t ** 3 - t * t + 2) * lo) * k, 0.0),
                  np.maximum(((t + t ** 3 - 2 * t * t - 2) * -hi + (t - 1) * t * t * lo) * k, 0.0))
    ends = np.stack([lo, lo + t * (hi - lo), hi], axis=-1)
    return np.take_along_axis(ends, _SECTOR_CHANNELS[sector], axis=-1)


def _tune_bands(lo: np.ndarray, hi: np.ndarray, h: np.ndarray, gap: np.ndarray, hue_table: np.ndarray,
                sat_table: np.ndarray, lum_table: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Band luminance, then saturation, then hue, each looked up at the color's original hue."""
    lo, hi = np.maximum(lo, 0.0), np.maximum(hi, 0.0)
    w = np.clip(gap, 0.0, 1.0)
    for _ in range(3):
        w = w * (2.0 - w)  # near-neutrals take less of the band's luminance
    a = w * _lookup(lum_table, h)
    for _ in range(2):
        lo = lo * (1.0 + (1.0 - np.clip(lo, 0.0, 1.0)) * a)
        hi = hi * (1.0 + (1.0 - np.clip(hi, 0.0, 1.0)) * a)
    sat = _lookup(sat_table, h)
    with np.errstate(invalid="ignore", divide="ignore"):
        s = np.where(hi > 0, (hi - lo) / np.where(hi > 0, hi, 1.0), 0.0)
        shadow = np.minimum(hi * 16.0, 1.0)
        boosted = hi * (1.0 - s / (1.0 - 0.8333333 * (1.0 - s) * sat * (1.0 - lo) * shadow * (2.0 - shadow)))
    mid = (lo + hi) / 2
    new_lo = np.where(sat > 0, boosted, np.where(sat < 0, lo + (lo - mid) * sat, lo))
    new_hi = np.where(sat < 0, hi + (hi - mid) * sat, hi)
    colored = gap > 0
    return np.where(colored, new_lo, lo), np.where(colored, new_hi, hi), np.where(colored, _lookup(hue_table, h), h)


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

def saturation_vibrance(enc: np.ndarray, saturation: float = 0.0, vibrance: float = 0.0,
                        smooth: bool = False, process_version: tuple[int, int] = (0, 0)) -> np.ndarray:
    """Global saturation, then vibrance (-100..100), on scene-linear values as Lightroom does.

    Both run on scene-linear values (:func:`_to_scene`, the values Lightroom recovers from a
    rendered image). Saturation scales the two opponent axes and keeps luminance.
    Vibrance, the first step of :func:`hsl_adjust`, rewrites each color's largest and smallest
    channel and keeps the middle one in place: it favours muted colors, spares skin hues when
    boosting, and keeps some color at -100.

    From ``process_version`` 15.4, positive saturation runs on a scale with a stronger shoulder
    (:data:`POSITIVE_SATURATION_SHOULDER`) around its own luminance (:data:`POSITIVE_SATURATION_PIVOT`).
    """
    _check("saturation", saturation, -100, 100)
    enc = _as_rgb(enc)
    if saturation:
        s = (saturation + 100) / 100
        shoulder, opponent = None, _OPPONENT
        if saturation > 0 and process_version >= (15, 4):
            shoulder = response.ExposureShoulder(POSITIVE_SATURATION_SHOULDER, _response().exposure.strength)
            opponent = _opponent(POSITIVE_SATURATION_PIVOT)
        matrix = np.linalg.inv(opponent) @ np.diag([1.0, s, s]) @ opponent
        enc = _from_color_scene(np.maximum(_to_color_scene(enc, shoulder) @ matrix.T, 0.0), shoulder)
    return hsl_adjust(enc, vibrance=vibrance, smooth=smooth)


# Luminance and two opponent axes of linear working RGB: (R - G) / 2 and (B - luminance) / 4
_OPPONENT = np.array([[0.2880859375, 0.7119140625, 0.0],
                      [0.5, -0.5, 0.0],
                      [-0.072021484375, -0.177978515625, 0.25]])
_OPPONENT_INV = np.linalg.inv(_OPPONENT)

#: PV 15.4's positive saturation: the shoulder of its scene scale and the luminance it keeps, fitted
#: on the PV 15.4 Saturation +15/+50/+100 bakes' ProPhoto exports (Lightroom's own PV 11 and PV 15.4
#: Saturation bakes differ by 0.3-0.5 dE; negative amounts fit the PV 11 scale and weights)
POSITIVE_SATURATION_SHOULDER = 3.2
POSITIVE_SATURATION_PIVOT = (0.31, 0.71, -0.02)


def _opponent(luminance: Sequence[float]) -> np.ndarray:
    """Luminance with these weights and the two opponent axes (R - G) / 2, (B - luminance) / 4."""
    w = np.asarray(luminance, dtype=np.float64)
    return np.array([w, [0.5, -0.5, 0.0], [-w[0] / 4, -w[1] / 4, 0.25 - w[2] / 4]])


def _vibrance(lo: np.ndarray, hi: np.ndarray, h: np.ndarray, amount: float) -> tuple[np.ndarray, np.ndarray]:
    """Vibrance ``amount`` (-1..1) on a color's scene min, max and hexcone hue: the new min and max.

    With hexcone saturation ``s = (max - min) / max``, the max and the min move by amounts that
    peak for mid-saturated colors and fade in deep shadows (below about 1/16) and toward white.
    Boosts are damped for skin hues (red to yellow).
    """
    gap = hi - lo
    colored = gap > 0
    with np.errstate(invalid="ignore", divide="ignore"):
        s = np.where(colored, gap / np.where(hi > 0, hi, 1.0), 0.0)
    shadow = np.minimum(hi * 16.0, 1.0)
    shadow = shadow * (2.0 - shadow)
    mid_sat = s * (1.0 - s)
    headroom = 1.0 - np.minimum(hi, 1.0)
    if amount > 0:
        hue = h + 1.0  # red-to-yellow lands on 1..2
        hue = np.where(hue >= 6.0, hue - 6.0, hue)
        skin = np.minimum(np.clip(hue, 0.0, 1.0), np.clip((1.5 - hue) * 4.0 + 1.0, 0.0, 1.0))
        muted_skin = (1.0 - s * s) * skin
        push = (muted_skin * amount + 1.0 - muted_skin) * amount
        new_hi = hi * (headroom * mid_sat * (2.0 - mid_sat) * shadow * push * 0.25 + 1.0)
        spread = (1.0 - s) * shadow * (1.0 - lo) * (0.8333333 - 0.4047619 * muted_skin) * push
        new_lo = new_hi * (1.0 - s / (1.0 - spread))
    else:
        new_hi = hi * (headroom * shadow * amount * mid_sat * (2.0 - mid_sat) + 1.0)
        kept = s * (amount + 1.0 - shadow * s * amount * (s * 0.5 + 0.5))
        new_lo = new_hi * (1.0 - (amount * 0.25 + 1.0) * kept)
    return np.where(colored, new_lo, lo), np.where(colored, new_hi, hi)


def _hexcone_hue(x: np.ndarray, hi: np.ndarray, gap: np.ndarray) -> np.ndarray:
    """HSV hue in [0, 6) (0 red, 2 green, 4 blue); 0 for neutrals."""
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    with np.errstate(invalid="ignore", divide="ignore"):
        safe = np.where(gap > 0, gap, 1.0)
        h = np.where(hi == r, ((g - b) / safe) % 6.0, np.where(hi == g, (b - r) / safe + 2.0, (r - g) / safe + 4.0))
    return np.where(gap > 0, h, 0.0)


#: a B&W band slider at -100 sets its gray weight to this; at +100 it adds this to the default
_MIXER_DOWN, _MIXER_UP = -2.0, 2.25


def grayscale(enc: np.ndarray, mixer: Mapping[str, float] | None = None) -> np.ndarray:
    """Black-and-white conversion, as Lightroom's B&W mixer; ``mixer`` moves each band (-100..100).

    Runs on the HSL stage's scene scale (units of ``K_SCENE``). Each color's gray lies on the line
    from its smallest to its largest channel, ``lo + w * (hi - lo)``, with the weight ``w`` read at
    the color's hexcone hue from a table of the eight band weights (linear between band centers).
    A band's weight at 0 is the scene luminance of its center's pure color, so the neutral mix
    is luminance at every center. A negative weight puts the gray below the min, along a
    perceptual curve (:func:`_dark_encode`) so it darkens smoothly instead of going negative.
    Grays stay as they are.

    Lightroom builds the table differently from process version 15.4; that table isn't
    reconstructed yet, so every version uses this one.
    """
    sliders = _band_values("mixer", mixer, -100, 100) / 100
    enc = _as_rgb(enc)
    table = _band_table(_mixer_weight(sliders, _mixer_defaults()), smooth=False)
    lo, hi, h, gap = _to_mmh(_to_color_scene(enc) * K_SCENE, smooth=False)
    w = _lookup(table, h)
    dark = _dark_decode(_dark_encode(lo) + (_dark_encode(hi) - _dark_encode(lo)) * w)
    gray = np.clip(np.where(w >= 0, lo + gap * w, dark), 0.0, 1.0)
    out = _from_color_scene(np.repeat(gray[..., None], 3, axis=-1) / K_SCENE)
    return np.where((gap > 0)[..., None], out, enc)


def _mixer_defaults() -> np.ndarray:
    """Each band's gray weight at 0: the luminance of its center's pure color (min 0, max 1)."""
    pure = _from_mmh(np.zeros(8), np.ones(8), HSL_CENTERS / 60.0, smooth=False)
    return pure @ np.asarray(_TINT_LUMA)


def _mixer_weight(slider: np.ndarray, default: np.ndarray) -> np.ndarray:
    """Band sliders (-1..1) -> gray weights: flattened around 0, then the parabola through
    (-1, ``_MIXER_DOWN``), (0, ``default``) and (1, ``default + _MIXER_UP``)."""
    k = (1.0 - default * default / 2.0) / 2.0
    s = slider * (1.0 - k + k * np.abs(slider))
    down, up = _MIXER_DOWN, default + _MIXER_UP
    return default + ((up - down) / 2.0 + ((down + up) / 2.0 - default) * s) * s


def _dark_encode(x: np.ndarray) -> np.ndarray:
    """A perceptual curve for the mixer's darkening: linear (x12) below 0, ``3x / (6.75x + 0.25)``
    up to 1/9 and ``0.75x + 0.25`` above (continuous, 0 -> 0 and 1 -> 1)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(x < 0, 12.0 * x, np.where(x < 1 / 9, 3.0 * x / (6.75 * x + 0.25), 0.75 * x + 0.25))


def _dark_decode(y: np.ndarray) -> np.ndarray:
    """The inverse of :func:`_dark_encode`."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(y < 0, y / 12.0, np.where(y < 1 / 3, y / (12.0 - 27.0 * y), (y - 0.25) / 0.75))


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
    """The look's RGB table at the look amount x its ``RGBTableAmount``, then its hue/sat map
    at the look amount (a look profile's own tables at amount 1 when there is no look)."""
    look, preset = s.preset.look, s.preset
    if look is not None and (look.table is not None or look.hue_sat_map is not None):
        table, hue_sat_map, amount, table_amount = look.table, look.hue_sat_map, look.amount, look.table_amount
    elif preset.rgb_table is not None or preset.look_table is not None:
        table, hue_sat_map, amount, table_amount = preset.rgb_table, preset.look_table, 1.0, preset.table_amount
    else:
        return enc
    lin = cs.decode(enc, WORKING_TRANSFER)
    if table is not None:
        lin = apply_rgb_table(table, lin, WORKING, amount=amount * table_amount).astype(np.float64)
    if hue_sat_map is not None:
        lin = apply_hue_sat_map(hue_sat_map, lin, WORKING, amount=amount)
    return cs.encode(lin, WORKING_TRANSFER)


def _stage_whites_blacks(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    return whites_blacks(enc, s.number("Whites2012"), s.number("Blacks2012"))


def _stage_tone(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    return tone(enc, s.number("Contrast2012"), process_version=s.preset.process_version)


def _stage_parametric(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    return parametric_curve(
        enc, s.number("ParametricShadows"), s.number("ParametricDarks"), s.number("ParametricLights"),
        s.number("ParametricHighlights"),
        splits=(s.number("ParametricShadowSplit", 25), s.number("ParametricMidtoneSplit", 50),
                s.number("ParametricHighlightSplit", 75)),
        process_version=s.preset.process_version,
    )


def _stage_curves(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    refine = s.number("CurveRefineSaturation", 100.0)
    for master in s.curves("ToneCurvePV2012"):
        enc = apply_curves(enc, master=master, process_version=s.preset.process_version, refine_saturation=refine)
    for i, key in enumerate(_CURVES[1:]):
        for c in s.curves(key):
            enc = apply_curves(enc, **{("red", "green", "blue")[i]: c})
    return enc


def _stage_hsl(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    return hsl_adjust(enc, s.bands("HueAdjustment"), s.bands("SaturationAdjustment"),
                      s.bands("LuminanceAdjustment"), vibrance=s.number("Vibrance"),
                      smooth=_process_version(s.preset) >= (15, 4))


def _process_version(preset: Preset) -> tuple[int, int]:
    """``crs:ProcessVersion`` as (major, minor); (0, 0) when missing or unreadable."""
    return _version(preset.process_version)


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
    return saturation_vibrance(enc, s.number("Saturation"), process_version=_process_version(s.preset))


def _stage_grayscale(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    if s.text("ConvertToGrayscale").lower() != "true":
        return enc
    return grayscale(enc, s.bands("GrayMixer"))


ENCODED_STAGES: dict[str, Callable[[_Sliders, np.ndarray], np.ndarray]] = {
    "whites_blacks": _stage_whites_blacks,
    "tone": _stage_tone,
    "parametric": _stage_parametric,
    "curves": _stage_curves,
    "look": _look_table,
    "hsl": _stage_hsl,
    "grade": _stage_grade,
    "saturation": _stage_saturation,
    "grayscale": _stage_grayscale,
}
# Lightroom's order: Whites and Blacks with exposure, saturation, then vibrance and HSL (one stage;
# B&W takes HSL's place), all on scene-linear values; then the tone curve (Contrast, parametric,
# point curve), the profile's table and color grading.
ORDER: tuple[str, ...] = ("whites_blacks", "saturation", "hsl", "grayscale", "tone", "parametric", "curves",
                          "look", "grade")


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
