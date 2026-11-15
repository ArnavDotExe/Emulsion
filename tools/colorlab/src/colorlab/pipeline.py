"""Reference pipeline: a preset's global color sliders, applied in Lightroom's order.

Lightroom's exact formulas are not public. Every stage here is a plain, documented
model with the properties a photographer expects: identity at default, endpoints
held, tone curves monotonic, neutrals kept neutral. The ``K_*`` strengths are
tuned against Lightroom bakes of real presets (see ``docs/tdd``).

Working space is ProPhoto RGB primaries. White balance and exposure run on linear
light. Every later stage runs on sRGB-curve encoded values, the space Lightroom's
tone curve panel shows. Spatial effects (grain, vignette, highlights/shadows,
clarity, texture, dehaze, sharpening, noise reduction) belong to a later stage and
are not applied here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from . import colorspace as cs
from .adobe_table import apply_rgb_table
from .errors import ParameterError
from .lut import Lut3D
from .preset import IDENTITY_CURVE, Preset

WORKING = cs.PROPHOTO_RGB
WORKING_TRANSFER = cs.Transfer.SRGB

BANDS = ("red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta")
_BAND_CENTERS = np.array([0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 270.0, 300.0, 360.0])
_LUMA = WORKING.rgb_to_xyz[1] / WORKING.rgb_to_xyz[1].sum()

# Stage strengths (slider value 100 == full strength), fitted with `colorlab.calibration`
# to single-slider Lightroom bakes (2026-11-15, 113 bakes). Temperature/tint are not fitted yet.
K_TEMPERATURE = 0.004  # log-gain per incremental temperature unit (first guess)
K_TINT = 0.003  # first guess
K_CONTRAST = 0.654  # log2 of the power applied at contrast 100
K_WHITES = 0.892
K_BLACKS = 0.908
K_PARAMETRIC = 0.115  # peak lift of one parametric region at 100 (at its monotonic bound)
K_HUE = 0.831  # fraction of the gap to the neighbouring band at hue 100
K_LUMINANCE = 0.708
K_GRADE_SAT = 0.231
K_GRADE_LUM = 0.0868
K_MIXER = 1.55

CONTRAST_PIVOT = float(cs.encode(np.array(0.18), WORKING_TRANSFER))

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


def apply_curves(rgb: np.ndarray, master: Curve = IDENTITY_CURVE, red: Curve = IDENTITY_CURVE,
                 green: Curve = IDENTITY_CURVE, blue: Curve = IDENTITY_CURVE) -> np.ndarray:
    """Master curve on every channel, then each channel's own curve (encoded values)."""
    out = _as_rgb(rgb).copy()
    if not _is_identity(master):
        out = spline(master)(out)
    for i, curve in enumerate((red, green, blue)):
        if not _is_identity(curve):
            out[..., i] = spline(curve)(out[..., i])
    return out


# --- linear stages -------------------------------------------------------------------------------

def white_balance(lin: np.ndarray, temperature: float = 0.0, tint: float = 0.0) -> np.ndarray:
    """Incremental temperature/tint (-100..100) as channel gains that keep luminance."""
    _check("temperature", temperature, -100, 100)
    _check("tint", tint, -100, 100)
    lin = _as_rgb(lin)
    if temperature == 0 and tint == 0:
        return lin
    t, m = K_TEMPERATURE * temperature, K_TINT * tint
    gains = np.exp(np.array([t + m, -m, -t + m]))
    gains /= gains @ WORKING.rgb_to_xyz[1] / WORKING.rgb_to_xyz[1].sum()
    return lin * gains


def exposure(lin: np.ndarray, ev: float) -> np.ndarray:
    """Exposure in stops (-5..5) on linear light."""
    _check("exposure", ev, -5, 5)
    lin = _as_rgb(lin)
    return lin if ev == 0 else lin * 2.0 ** ev


# --- tone ----------------------------------------------------------------------------------------------

def tone(enc: np.ndarray, contrast: float = 0.0, whites: float = 0.0, blacks: float = 0.0) -> np.ndarray:
    """Contrast, Whites and Blacks (-100..100) as monotonic per-channel curves with fixed ends."""
    for name, v in (("contrast", contrast), ("whites", whites), ("blacks", blacks)):
        _check(name, v, -100, 100)
    enc = _as_rgb(enc)
    if contrast == 0 and whites == 0 and blacks == 0:
        return enc
    e = np.clip(enc, 0.0, 1.0)
    if contrast:
        g, p = 2.0 ** (K_CONTRAST * contrast / 100), CONTRAST_PIVOT
        e = np.where(e <= p, p * (e / p) ** g, 1 - (1 - p) * ((1 - e) / (1 - p)) ** g)
    if whites:
        e = e + K_WHITES * whites / 100 * e ** 3 * (1 - e)
    if blacks:
        e = e + K_BLACKS * blacks / 100 * e * (1 - e) ** 3
    return e


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
    e = np.clip(enc, 0.0, 1.0)
    # Each region's bump spans from the middle of the region below to the middle of the one above.
    mids = (0.0, s1 / 2, (s1 + s2) / 2, (s2 + s3) / 2, (s3 + 1) / 2, 1.0)
    lift = np.zeros_like(e)
    for i, amount in enumerate(amounts):
        if amount:
            lo, hi = mids[i], mids[i + 2]
            u = np.clip((e - lo) / (hi - lo), 0.0, 1.0)
            lift += K_PARAMETRIC * amount / 100 * np.sin(np.pi * u) ** 2
    return e + lift


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
    """Color grading / split toning: tint and brighten tonal zones of the image."""
    zones = tuple(z.check(n) for z, n in ((shadows, "shadows"), (midtones, "midtones"),
                                         (highlights, "highlights"), (global_, "global")))
    _check("blending", blending, 0, 100)
    _check("balance", balance, -100, 100)
    enc = _as_rgb(enc)
    if all(z.neutral for z in zones):
        return enc
    t = np.clip(enc @ _LUMA, 0.0, 1.0) ** (2.0 ** (-balance / 100))
    p = 1 + 2 * (1 - blending / 100)
    ws, wh = (1 - t) ** p, t ** p
    weights = (ws, 1 - ws - wh, wh, np.ones_like(t))
    out = enc.copy()
    for zone, w in zip(zones, weights):
        if zone.neutral:
            continue
        tint = K_GRADE_SAT * zone.sat / 100 * _shape(np.array(zone.hue)) + K_GRADE_LUM * zone.lum / 100
        out = out + w[..., None] * tint
    return out


# --- saturation, vibrance, grayscale ----------------------------------------------------------------

def saturation_vibrance(enc: np.ndarray, saturation: float = 0.0, vibrance: float = 0.0) -> np.ndarray:
    """Global saturation, and vibrance that favours muted colors (-100..100)."""
    _check("saturation", saturation, -100, 100)
    _check("vibrance", vibrance, -100, 100)
    enc = _as_rgb(enc)
    if saturation == 0 and vibrance == 0:
        return enc
    h, c = _hue(enc)
    gain = (1 + saturation / 100) * (1 + vibrance / 100 * (1 - np.clip(c, 0.0, 1.0)))
    return _recompose(enc, h, c * np.maximum(gain, 0.0))


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


def _stage_grade(s: _Sliders, enc: np.ndarray) -> np.ndarray:
    return color_grade(enc, shadows=s.zone("Shadow", legacy="Shadow"), midtones=s.zone("Midtone"),
                       highlights=s.zone("Highlight", legacy="Highlight"), global_=s.zone("Global"),
                       blending=s.number("ColorGradeBlending", 50), balance=s.number("SplitToningBalance"))


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
    lin = white_balance(lin, s.number("IncrementalTemperature"), s.number("IncrementalTint"))
    lin = exposure(lin, s.number("Exposure2012"))
    enc = cs.encode(np.maximum(lin, 0.0), WORKING_TRANSFER)
    for name in ORDER:
        enc = ENCODED_STAGES[name](s, enc)
    out = cs.convert(cs.decode(enc, WORKING_TRANSFER), WORKING, space)
    return np.clip(cs.encode(out, WORKING_TRANSFER), 0.0, 1.0).astype(np.float32)


def render_lut(preset: Preset, size: int = 33) -> Lut3D:
    """Bake ``preset``'s global color into a ``size``-point 3D LUT (sRGB in, sRGB out)."""
    grid = Lut3D.identity(size).table.astype(np.float64)
    return Lut3D(render(preset, grid), title=preset.name)
