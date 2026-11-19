"""Single-slider calibration kit: fit the pipeline's slider strengths to Lightroom.

    python -m colorlab.calibration presets out/cal-presets
    python -m colorlab.bake kit out/cal-presets out/cal-kit          # then Lightroom, see the kit README
    python -m colorlab.bake collect <exports> out/cal-kit out/cal-luts
    python -m colorlab.calibration response out/cal-luts --out src/colorlab/data/lightroom_response.json
    python -m colorlab.calibration fit out/cal-luts --json out/cal-fit.json

Each calibration preset moves exactly one slider. ``response`` measures the
shapes of white balance, exposure, the tone sliders and the parametric curve straight
from their bakes (``colorlab.response``). ``fit`` then fits each remaining
``K_*`` strength in ``colorlab.pipeline`` only from the bakes that exercise it,
and reports the remaining error per preset, which shows where a stage's *shape*
(not just its strength) still differs from Lightroom.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from html import escape
from pathlib import Path

import numpy as np

from . import colorspace as cs
from . import pipeline, response
from .errors import ColorlabError
from .lut import Lut3D, apply_lut, read_cube
from .metrics import delta_e_2000, srgb_to_lab
from .pipeline import BANDS
from .preset import parse_preset

CAL_GROUP = "FilmSim Calibration"

#: search range for each strength
K_BOUNDS: dict[str, tuple[float, float]] = {
    "K_SCENE": (0.05, 1.0),
}

#: channels the measured white balance moves (``response.WB_SPACES``): Bradford cones fit the
#: Temperature/Tint bakes better than working-space RGB (mean CIEDE2000 3.01 vs 3.36 over 12 bakes)
WB_SPACE = "bradford"
#: weight of the gray-ramp error next to the color-grid error in the white-balance refine. Over the
#: 12 bakes, 0 gives colors 3.02 / grays 2.32 dE; 1 gives 3.23 / 1.37 (working space was 3.88 / 1.45)
_WB_GRAY_WEIGHT = 1.0
_WB_KEYS = (("Temperature", "IncrementalTemperature", "IncrementalTint"),
            ("Tint", "IncrementalTint", "IncrementalTemperature"))

Points = tuple[tuple[int, int], ...]
_CURVE_KEYS = ("ToneCurvePV2012", "ToneCurvePV2012Red", "ToneCurvePV2012Green", "ToneCurvePV2012Blue")


@dataclass(frozen=True)
class Variant:
    """One calibration preset: a single slider (or curve) moved, and the strengths it informs."""

    name: str
    settings: Mapping[str, str]
    curves: Mapping[str, Points] = field(default_factory=dict)
    fits: tuple[str, ...] = ()


def _sweep(label: str, key: str, values: Sequence[float], fits: tuple[str, ...] = (),
           extra: Mapping[str, str] | None = None) -> list[Variant]:
    return [Variant(f"Cal {label} {v:+g}", {**(extra or {}), key: f"{v:+g}"}, fits=fits) for v in values]


def _build() -> tuple[Variant, ...]:
    out: list[Variant] = []
    # White balance, exposure, tone and parametric shapes are measured (`response`).
    out += _sweep("Exposure", "Exposure2012", (-2, -1, -0.5, 0.5, 1, 2))
    for label, key in (("Contrast", "Contrast2012"), ("Whites", "Whites2012"), ("Blacks", "Blacks2012")):
        out += _sweep(label, key, (-100, -50, -25, 25, 50, 100))
    # Lightroom ignores an incremental temperature or tint that comes without its partner.
    for label, key, partner in _WB_KEYS:
        out += _sweep(label, key, (-100, -50, -25, 25, 50, 100), extra={"WhiteBalance": "Custom", partner: "0"})
    for region in ("Shadows", "Darks", "Lights", "Highlights"):
        out += _sweep(f"Parametric {region}", f"Parametric{region}", (-100, -50, 50, 100))
    custom = {"ToneCurveName2012": "Custom"}
    for name, key, pts in (
        ("Master S", "ToneCurvePV2012", ((0, 0), (64, 44), (192, 212), (255, 255))),
        ("Master Lift", "ToneCurvePV2012", ((0, 48), (255, 255))),
        ("Red", "ToneCurvePV2012Red", ((0, 0), (128, 168), (255, 255))),
        ("Blue Fade", "ToneCurvePV2012Blue", ((0, 40), (255, 215))),
    ):
        # Lightroom ignores a lone channel curve; list all four, the unused ones as identity.
        curves = {k: ((0, 0), (255, 255)) for k in _CURVE_KEYS}
        out.append(Variant(f"Cal Curve {name}", custom, {**curves, key: pts}))
    out += _process_version_15_4()
    out += _process_version_15_4_color()
    for band in BANDS:
        b = band.capitalize()
        out += _sweep(f"HSL Hue {b}", f"HueAdjustment{b}", (-100, 100), ("K_SCENE",))
        out += _sweep(f"HSL Sat {b}", f"SaturationAdjustment{b}", (-100, 100), ("K_SCENE",))
        out += _sweep(f"HSL Lum {b}", f"LuminanceAdjustment{b}", (-100, 100), ("K_SCENE",))
    out += _sweep("Saturation", "Saturation", (-100, -50, 50, 100))
    out += _sweep("Vibrance", "Vibrance", (-100, -50, -25, 25, 50, 100), ("K_SCENE",))
    zones = (("Shadows", "SplitToningShadowHue", "SplitToningShadowSaturation", "ColorGradeShadowLum"),
             ("Midtones", "ColorGradeMidtoneHue", "ColorGradeMidtoneSat", "ColorGradeMidtoneLum"),
             ("Highlights", "SplitToningHighlightHue", "SplitToningHighlightSaturation", "ColorGradeHighlightLum"),
             ("Global", "ColorGradeGlobalHue", "ColorGradeGlobalSat", "ColorGradeGlobalLum"))
    for label, hue_key, sat_key, lum_key in zones:
        for hue in (0, 120, 240):
            out.append(Variant(f"Cal Grade {label} H{hue} S50", {hue_key: str(hue), sat_key: "50"}))
        out += _sweep(f"Grade {label} Lum", lum_key, (-50, 50))
    blue_shadows = {"SplitToningShadowHue": "240", "SplitToningShadowSaturation": "50"}
    out += _sweep("Grade Balance", "SplitToningBalance", (-50, 50), (), blue_shadows)
    out += [Variant(f"Cal Grade Blending {v}", {**blue_shadows, "ColorGradeBlending": str(v)})
            for v in (0, 100)]
    mixer = {"ConvertToGrayscale": "True", **{f"GrayMixer{b.capitalize()}": "0" for b in BANDS}}
    out.append(Variant("Cal BW Neutral Mix", mixer))
    for band in BANDS:
        b = band.capitalize()
        out.append(Variant(f"Cal BW Mix {b} +100", {**mixer, f"GrayMixer{b}": "+100"}))
    return tuple(out)


def _process_version_15_4() -> list[Variant]:
    """Checks of the PV 15.4 tone stages (the other variants are PV 11): Contrast, parametric and
    point curve in 15.4's combined pulled curve, Whites/Blacks, a channel curve, and the point
    curve's luminance mode (Refine Saturation 0 and 50, alone and after Contrast). They set two or
    more keys, so the measured slider shapes stay those of PV 11."""
    pv = {"ProcessVersion": "15.4"}
    custom = {**pv, "ToneCurveName2012": "Custom"}
    identity = {k: ((0, 0), (255, 255)) for k in _CURVE_KEYS}
    master_s = {**identity, "ToneCurvePV2012": ((0, 0), (64, 44), (192, 212), (255, 255))}
    out = [Variant(f"Cal PV15 {label} {value}", {**pv, key: value})
           for label, key, value in (("Contrast", "Contrast2012", "+100"), ("Parametric Darks", "ParametricDarks", "-100"),
                                     ("Whites", "Whites2012", "+100"), ("Whites", "Whites2012", "-100"),
                                     ("Blacks", "Blacks2012", "+100"), ("Blacks", "Blacks2012", "-100"))]
    out.append(Variant("Cal PV15 Curve Master S", custom, master_s))
    out.append(Variant("Cal PV15 Curve Red", custom, {**identity, "ToneCurvePV2012Red": ((0, 0), (128, 168), (255, 255))}))
    for refine in ("0", "50"):
        out.append(Variant(f"Cal PV15 Curve Master S Refine {refine}", {**custom, "CurveRefineSaturation": refine},
                           master_s))
    out.append(Variant("Cal PV15 Contrast +50 Curve Master S Refine 0",
                       {**custom, "Contrast2012": "+50", "CurveRefineSaturation": "0"}, master_s))
    return out


def _process_version_15_4_color() -> list[Variant]:
    """Checks of the PV 15.4 colour sliders: Saturation, Vibrance and every HSL band alone, and
    Saturation or Vibrance together with the HSL moves that act on bright saturated colours. They
    fit nothing; they compare the PV 15.4 model with Lightroom (export them in ProPhoto as well,
    to see colours beyond sRGB)."""
    pv = {"ProcessVersion": "15.4"}
    out = _sweep("PV15 Color Saturation", "Saturation", (-100, -50, 15, 50, 100), extra=pv)
    out += _sweep("PV15 Color Vibrance", "Vibrance", (-100, -50, 40, 100), extra=pv)
    for band in BANDS:
        b = band.capitalize()
        for label, key in (("Hue", "HueAdjustment"), ("Sat", "SaturationAdjustment"), ("Lum", "LuminanceAdjustment")):
            out += _sweep(f"PV15 Color HSL {label} {b}", f"{key}{b}", (-100, 100), extra=pv)
    hsl_moves = (("Hue Yellow -100", "HueAdjustmentYellow", "-100"), ("Sat Yellow -100", "SaturationAdjustmentYellow", "-100"),
                 ("Lum Yellow +100", "LuminanceAdjustmentYellow", "+100"), ("Lum Orange +100", "LuminanceAdjustmentOrange", "+100"),
                 ("Sat Magenta -100", "SaturationAdjustmentMagenta", "-100"))
    for slider in ("Saturation", "Vibrance"):
        for label, key, value in hsl_moves:
            out.append(Variant(f"Cal PV15 Color {slider} +50 {label}", {**pv, slider: "+50", key: value}))
    out.append(Variant("Cal PV15 Color Saturation +50 Vibrance +50", {**pv, "Saturation": "+50", "Vibrance": "+50"}))
    out.append(Variant("Cal PV15 Color Saturation +100 Hue Yellow -100 Sat Yellow -50",
                       {**pv, "Saturation": "+100", "HueAdjustmentYellow": "-100", "SaturationAdjustmentYellow": "-50"}))
    return out


VARIANTS: tuple[Variant, ...] = _build()


# --- preset files -------------------------------------------------------------------------------

_BASE_ATTRS = {
    "PresetType": "Normal", "Cluster": "", "SupportsAmount": "False", "SupportsColor": "True",
    "SupportsMonochrome": "True", "SupportsHighDynamicRange": "True", "SupportsNormalDynamicRange": "True",
    "SupportsSceneReferred": "True", "SupportsOutputReferred": "True", "CameraModelRestriction": "",
    "Copyright": "", "ContactInfo": "", "Version": "15.0", "ProcessVersion": "11.0", "HasSettings": "True",
}


def variant_xmp(v: Variant, group: str = CAL_GROUP) -> str:
    """A complete Lightroom preset (.xmp) for one calibration variant, in preset group ``group``."""
    uid = uuid.uuid5(uuid.NAMESPACE_URL, f"filmsim-calibration:{v.name}").hex.upper()
    attrs = {**_BASE_ATTRS, "UUID": uid, **v.settings}
    attr_xml = "\n".join(f'    crs:{k}="{escape(str(val))}"' for k, val in attrs.items())
    curves = "".join(
        f"   <crs:{key}>\n    <rdf:Seq>\n"
        + "".join(f"     <rdf:li>{x}, {y}</rdf:li>\n" for x, y in pts)
        + f"    </rdf:Seq>\n   </crs:{key}>\n"
        for key, pts in v.curves.items()
    )
    return f"""<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="FilmSim colorlab">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
{attr_xml}>
   <crs:Name><rdf:Alt><rdf:li xml:lang="x-default">{escape(v.name, quote=False)}</rdf:li></rdf:Alt></crs:Name>
   <crs:Group><rdf:Alt><rdf:li xml:lang="x-default">{escape(group, quote=False)}</rdf:li></rdf:Alt></crs:Group>
{curves}  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def write_calibration_presets(out_dir: str | Path, variants: Sequence[Variant] = VARIANTS) -> list[Path]:
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty; choose a new folder for the calibration presets")
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for v in variants:
        path = out / f"{v.name}.xmp"
        path.write_text(variant_xmp(v), encoding="utf-8")
        paths.append(path)
    return paths


# --- fitting -----------------------------------------------------------------------------------------

@dataclass
class FitReport:
    constants: dict[str, float] = field(default_factory=dict)  # fitted strengths
    before: dict[str, float] = field(default_factory=dict)  # mean dE2000 per variant, current strengths
    after: dict[str, float] = field(default_factory=dict)  # mean dE2000 per variant, fitted strengths
    missing: list[str] = field(default_factory=list)  # variants with no bake


@contextmanager
def _restored_strengths() -> Iterator[None]:
    saved = {k: getattr(pipeline, k) for k in K_BOUNDS}
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(pipeline, k, v)


def _minimize(cost: Callable[[float], float], lo: float, hi: float) -> float:
    """Coarse grid, then golden-section search around the best grid point."""
    xs = np.linspace(lo, hi, 21)
    i = int(np.argmin([cost(x) for x in xs]))
    a, b = xs[max(i - 1, 0)], xs[min(i + 1, len(xs) - 1)]
    phi = (np.sqrt(5) - 1) / 2
    c, d = b - phi * (b - a), a + phi * (b - a)
    fc, fd = cost(c), cost(d)
    for _ in range(40):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - phi * (b - a)
            fc = cost(c)
        else:
            a, c, fc = c, d, fd
            d = a + phi * (b - a)
            fd = cost(d)
    return float((a + b) / 2)


def fit(lut_dir: str | Path, variants: Sequence[Variant] = VARIANTS, grid_size: int = 17) -> FitReport:
    """Fit each strength to the Lightroom bakes (``<variant name>.cube``) in ``lut_dir``.

    The pipeline's own strengths are left unchanged; the fitted values are returned.
    """
    grid = Lut3D.identity(grid_size).table.reshape(-1, 3).astype(np.float64)
    report = FitReport()
    targets: dict[str, np.ndarray] = {}
    present: list[Variant] = []
    for v in variants:
        path = Path(lut_dir) / f"{v.name}.cube"
        if not path.is_file():
            report.missing.append(v.name)
            continue
        targets[v.name] = srgb_to_lab(apply_lut(read_cube(path), grid))
        present.append(v)
    presets = {v.name: parse_preset(variant_xmp(v)) for v in present}

    def error(v: Variant) -> float:
        ours = pipeline.render(presets[v.name], grid)
        return float(delta_e_2000(srgb_to_lab(ours), targets[v.name]).mean())

    report.before = {v.name: error(v) for v in present}
    with _restored_strengths():
        for k in sorted({k for v in present for k in v.fits}):
            group = [v for v in present if k in v.fits]

            def cost(x: float, k: str = k, group: list[Variant] = group) -> float:
                setattr(pipeline, k, x)
                return float(np.mean([error(v) for v in group]))

            best = _minimize(cost, *K_BOUNDS[k])
            setattr(pipeline, k, best)
            report.constants[k] = best
        report.after = {v.name: error(v) for v in present}
    return report


# --- measured responses -------------------------------------------------------------------------------



@dataclass
class ResponseReport:
    response: response.Response
    measured: list[str] = field(default_factory=list)  # sliders measured from the bakes
    kept: list[str] = field(default_factory=list)  # sliders without bakes: taken from the base response
    warnings: list[str] = field(default_factory=list)  # measurements that fell back to the base


def _neutral(lut: Lut3D) -> np.ndarray:
    """Lightroom's response to a neutral ramp, at ``response.X`` (monotonic, 0-1)."""
    y = apply_lut(lut, np.repeat(response.X[:, None], 3, axis=1)).astype(np.float64).mean(axis=1)
    return np.clip(np.maximum.accumulate(y), 0.0, 1.0)


def _fit_exposure(curves: Mapping[float, np.ndarray]) -> response.ExposureShoulder:
    """Grid search of the exposure shoulder (p) and strength over every baked exposure."""
    x = response.X[:-1]
    strengths = np.linspace(0.5, 1.5, 201)
    best = (np.inf, 0.0, 0.0)
    for p in np.linspace(0.5, 8.0, 151):
        s = p * ((1 - x) ** (-1 / p) - 1)
        err = np.zeros_like(strengths)
        for ev, y in curves.items():
            pred = 1 - (1 + s[None, :] * 2.0 ** (strengths[:, None] * ev) / p) ** -p
            err += np.abs(pred - y[None, :-1]).mean(axis=1)
        i = int(np.argmin(err))
        if err[i] < best[0]:
            best = (float(err[i]), float(p), float(strengths[i]))
    return response.ExposureShoulder(shoulder=round(best[1], 4), strength=round(best[2], 4))


def _to_working(srgb: np.ndarray) -> np.ndarray:
    lin = cs.convert(cs.decode(np.clip(srgb, 0.0, 1.0), cs.Transfer.SRGB), cs.SRGB, pipeline.WORKING)
    return np.clip(cs.encode(np.maximum(lin, 0.0), pipeline.WORKING_TRANSFER), 0.0, 1.0)


def _from_working(enc: np.ndarray) -> np.ndarray:
    lin = cs.convert(cs.decode(enc, pipeline.WORKING_TRANSFER), pipeline.WORKING, cs.SRGB)
    return np.clip(cs.encode(lin, cs.Transfer.SRGB), 0.0, 1.0)


def _wb_channels(enc: np.ndarray) -> np.ndarray:
    """Working-encoded RGB as encoded Bradford cone responses (the white-balance channels)."""
    lin = cs.decode(enc, pipeline.WORKING_TRANSFER) @ pipeline._CONES.T
    return cs.encode(lin, pipeline.WORKING_TRANSFER)


def _nelder_mead(cost: Callable[[np.ndarray], float], x0: np.ndarray, step: float = 0.2,
                 iters: int = 300, tol: float = 1e-5) -> tuple[np.ndarray, float]:
    """Minimise ``cost`` from ``x0`` with a plain Nelder-Mead simplex."""
    n = len(x0)
    pts = [np.asarray(x0, dtype=np.float64)] + [x0 + step * np.eye(n)[i] for i in range(n)]
    vals = [cost(q) for q in pts]
    for _ in range(iters):
        order = np.argsort(vals)
        pts, vals = [pts[i] for i in order], [vals[i] for i in order]
        if vals[-1] - vals[0] < tol:
            break
        c = np.mean(pts[:-1], axis=0)
        xr = c + (c - pts[-1])
        fr = cost(xr)
        if fr < vals[0]:
            xe = c + 2 * (c - pts[-1])
            fe = cost(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = c + 0.5 * (pts[-1] - c)
            fc = cost(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0] + 0.5 * (q - pts[0]) for q in pts]
                vals = [cost(q) for q in pts]
    i = int(np.argmin(vals))
    return pts[i], float(vals[i])


def _white_balance_stops(lut: Lut3D, shoulder: response.ExposureShoulder, space: str = WB_SPACE,
                         grid_size: int = 17) -> np.ndarray:
    """Stops per white-balance channel (``space``) on the exposure shoulder for one bake.

    Starts from the median over a neutral ramp, away from black, white and any clipped output
    (a channel without such samples is NaN: not measurable from this bake). Then refines all three
    stops by mean CIEDE2000 over a ``grid_size``^3 sRGB grid plus the same over a gray ramp
    (``_WB_GRAY_WEIGHT``: colors alone would pull the neutrals off).
    """
    ramp = np.repeat(response.X[:, None], 3, axis=1)
    baked = apply_lut(lut, ramp).astype(np.float64)
    out = _to_working(baked)
    if space == "bradford":
        out = _wb_channels(out)
    stops = np.full(3, np.nan)
    for ch in range(3):
        ok = ((ramp[:, ch] > 0.02) & (ramp[:, ch] < 0.9) & (out[:, ch] > 0.005) & (out[:, ch] < 0.98)
              & np.all(baked < 1 - 1e-4, axis=1))
        if ok.any():
            stops[ch] = float(np.median(shoulder.stops(ramp[ok, ch], out[ok, ch])))
    if np.isnan(stops).any():
        return np.round(stops, 4)
    grid = Lut3D.identity(grid_size).table.reshape(-1, 3).astype(np.float64)
    grays = np.repeat(np.linspace(0.0, 1.0, 65)[:, None], 3, axis=1)
    sets = [(_to_working(x), srgb_to_lab(apply_lut(lut, x).astype(np.float64)), w)
            for x, w in ((grid, 1.0), (grays, _WB_GRAY_WEIGHT))]

    def cost(evs: np.ndarray) -> float:
        return sum(w * float(delta_e_2000(srgb_to_lab(_from_working(
            pipeline.white_balance_stops(src, evs, space, shoulder))), lab_ref).mean())
            for src, lab_ref, w in sets)

    evs, _ = _nelder_mead(cost, stops, step=0.1)
    return np.round(evs, 4)


def measure_response(lut_dir: str | Path, variants: Sequence[Variant] = VARIANTS,
                     base: response.Response | None = None) -> ResponseReport:
    """Measure exposure, tone, parametric and white-balance responses from the bakes in
    ``lut_dir``.

    Sliders without any bake keep their curves from ``base`` (default: the shipped response, or
    the identity response when the shipped file can't be read). A white-balance channel that a
    bake can't measure keeps the base value too; ``warnings`` lists every such fallback.
    """
    warnings: list[str] = []
    if base is None:
        try:
            base = response.load()
        except ColorlabError as exc:
            base = response.identity()
            warnings.append(f"base response unreadable ({exc}); sliders without bakes are identity")
    luts = {v.name: read_cube(p) for v in variants if (p := Path(lut_dir) / f"{v.name}.cube").is_file()}
    if not luts:
        raise ColorlabError(f"no calibration bakes (.cube) found in {lut_dir}")

    def solo(key: str) -> dict[float, Lut3D]:
        return {float(v.settings[key]): luts[v.name] for v in variants
                if set(v.settings) == {key} and not v.curves and v.name in luts}

    report = ResponseReport(response=base, warnings=warnings)
    tone = {}
    for key in response.TONE_KEYS:
        found = solo(key)
        if found:
            tone[key] = response.CurveFamily({a: _neutral(lut) for a, lut in found.items()})
            report.measured.append(key)
        else:
            tone[key] = base.tone[key]
            report.kept.append(key)
    exposure = base.exposure
    if found := solo("Exposure2012"):
        exposure = _fit_exposure({a: _neutral(lut) for a, lut in found.items()})
        report.measured.append("Exposure2012")
    else:
        report.kept.append("Exposure2012")
    wb_tables = {}
    for _label, key, partner in _WB_KEYS:
        found = {float(v.settings[key]): luts[v.name] for v in variants
                 if v.name in luts and v.settings.get(partner) == "0" and float(v.settings.get(key, 0)) != 0}
        base_table = base.white_balance.temperature if key == "IncrementalTemperature" else base.white_balance.tint
        if base.white_balance.space != WB_SPACE:
            fallback = f"base stops are for {base.white_balance.space!r} channels, not {WB_SPACE!r}"
        else:
            fallback = ""
        if found:
            wb_tables[key] = {}
            for a, lut in found.items():
                stops = _white_balance_stops(lut, exposure)
                if np.isnan(stops).any():
                    lost = ", ".join("RGB"[ch] for ch in np.flatnonzero(np.isnan(stops)))
                    report.warnings.append(f"{key} {a:+g}: no usable samples for {lost}; kept the base stops"
                                           + (f" ({fallback})" if fallback else ""))
                    stops = np.where(np.isnan(stops), response._table_at(base_table, a), stops)
                wb_tables[key][a] = stops
            report.measured.append(key)
        else:
            wb_tables[key] = base_table
            report.kept.append(key)
            if fallback:
                report.warnings.append(f"{key}: no bakes and the {fallback}; re-measure from a full set of bakes")
    white_balance = response.WhiteBalanceResponse(
        wb_tables["IncrementalTemperature"], wb_tables["IncrementalTint"], space=WB_SPACE)
    report.response = response.Response(exposure=exposure, tone=tone, white_balance=white_balance)
    return report


# --- command line ------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m colorlab.calibration", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("presets", help="write the single-slider calibration presets")
    pre.add_argument("out_dir", type=Path)
    pre.add_argument("--only-missing", type=Path, metavar="LUT_DIR",
                     help="only write variants that have no .cube in LUT_DIR yet (to re-bake failures)")
    fp = sub.add_parser("fit", help="fit the pipeline strengths to collected calibration bakes")
    fp.add_argument("lut_dir", type=Path)
    fp.add_argument("--grid", type=int, default=17, help="sample grid size per axis (default 17)")
    fp.add_argument("--json", type=Path, help="also write the report as JSON")
    rp = sub.add_parser("response", help="measure exposure/tone/parametric/white-balance shapes from the bakes")
    rp.add_argument("lut_dir", type=Path)
    rp.add_argument("--out", type=Path, required=True,
                    help="response file to write (the shipped one is src/colorlab/data/lightroom_response.json)")
    args = parser.parse_args(argv)
    try:
        if args.command == "response":
            measured = measure_response(args.lut_dir)
            response.save(measured.response, args.out)
            print(f"measured: {', '.join(measured.measured) or '-'}")
            print(f"kept (no bakes): {', '.join(measured.kept) or '-'}")
            for warning in measured.warnings:
                print(f"warning: {warning}")
            print(f"wrote {args.out}")
            return 0
        if args.command == "presets":
            variants = VARIANTS
            if args.only_missing is not None:
                variants = [v for v in VARIANTS if not (args.only_missing / f"{v.name}.cube").is_file()]
            paths = write_calibration_presets(args.out_dir, variants)
            print(f"wrote {len(paths)} calibration preset(s) to {args.out_dir}; "
                  f"next: python -m colorlab.bake kit {args.out_dir} <kit folder>")
            return 0
        report = fit(args.lut_dir, grid_size=args.grid)
        if not report.before:
            print(f"error: no calibration bakes (.cube) found in {args.lut_dir}", file=sys.stderr)
            return 1
        print(f"fitted from {len(report.before)} bake(s), missing {len(report.missing)}")
        for k, v in report.constants.items():
            print(f"{k} = {v:.4g}   (was {getattr(pipeline, k):.4g})")
        print("mean dE2000 per preset, current -> fitted:")
        for name in sorted(report.after, key=report.after.get, reverse=True):
            print(f"  {name:34s} {report.before[name]:6.2f} -> {report.after[name]:6.2f}")
        print(f"overall {np.mean(list(report.before.values())):.2f} -> {np.mean(list(report.after.values())):.2f}")
        if args.json:
            args.json.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    except (ColorlabError, FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
