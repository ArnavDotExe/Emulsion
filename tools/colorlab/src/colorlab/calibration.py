"""Single-slider calibration kit: fit the pipeline's slider strengths to Lightroom.

    python -m colorlab.calibration presets out/cal-presets
    python -m colorlab.bake kit out/cal-presets out/cal-kit          # then Lightroom, see the kit README
    python -m colorlab.bake collect <exports> out/cal-kit out/cal-luts
    python -m colorlab.calibration fit out/cal-luts --json out/cal-fit.json

Each calibration preset moves exactly one slider, so each ``K_*`` strength in
``colorlab.pipeline`` is fitted only from the bakes that exercise it. The fit also
reports the remaining error per preset, which shows where a stage's *shape* (not
just its strength) differs from Lightroom.
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

from . import pipeline
from .errors import ColorlabError
from .lut import Lut3D, apply_lut, read_cube
from .metrics import delta_e_2000, srgb_to_lab
from .pipeline import BANDS
from .preset import parse_preset

CAL_GROUP = "FilmSim Calibration"

#: search range for each strength; tone-curve bounds keep the curves monotonic
K_BOUNDS: dict[str, tuple[float, float]] = {
    "K_TEMPERATURE": (0.0, 0.05),
    "K_TINT": (0.0, 0.05),
    "K_CONTRAST": (0.0, 2.0),
    "K_WHITES": (0.0, 0.95),
    "K_BLACKS": (0.0, 0.95),
    "K_PARAMETRIC": (0.0, 0.115),
    "K_HUE": (0.0, 1.0),
    "K_LUMINANCE": (0.0, 1.5),
    "K_GRADE_SAT": (0.0, 1.0),
    "K_GRADE_LUM": (0.0, 1.0),
    "K_MIXER": (0.0, 3.0),
}

Points = tuple[tuple[int, int], ...]


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
    out += _sweep("Exposure", "Exposure2012", (-1, 1))
    for label, key, k in (("Contrast", "Contrast2012", "K_CONTRAST"), ("Whites", "Whites2012", "K_WHITES"),
                          ("Blacks", "Blacks2012", "K_BLACKS")):
        out += _sweep(label, key, (-100, -50, 50, 100), (k,))
    custom_wb = {"WhiteBalance": "Custom"}
    out += _sweep("Temperature", "IncrementalTemperature", (-100, -50, 50, 100), ("K_TEMPERATURE",), custom_wb)
    out += _sweep("Tint", "IncrementalTint", (-100, -50, 50, 100), ("K_TINT",), custom_wb)
    for region in ("Shadows", "Darks", "Lights", "Highlights"):
        out += _sweep(f"Parametric {region}", f"Parametric{region}", (-100, 100), ("K_PARAMETRIC",))
    custom = {"ToneCurveName2012": "Custom"}
    for name, key, pts in (
        ("Master S", "ToneCurvePV2012", ((0, 0), (64, 44), (192, 212), (255, 255))),
        ("Master Lift", "ToneCurvePV2012", ((0, 48), (255, 255))),
        ("Red", "ToneCurvePV2012Red", ((0, 0), (128, 168), (255, 255))),
        ("Blue Fade", "ToneCurvePV2012Blue", ((0, 40), (255, 215))),
    ):
        out.append(Variant(f"Cal Curve {name}", custom, {key: pts}))
    for band in BANDS:
        b = band.capitalize()
        out += _sweep(f"HSL Hue {b}", f"HueAdjustment{b}", (-100, 100), ("K_HUE",))
        out += _sweep(f"HSL Sat {b}", f"SaturationAdjustment{b}", (-100, 100))
        out += _sweep(f"HSL Lum {b}", f"LuminanceAdjustment{b}", (-100, 100), ("K_LUMINANCE",))
    out += _sweep("Saturation", "Saturation", (-100, -50, 50, 100))
    out += _sweep("Vibrance", "Vibrance", (-100, -50, 50, 100))
    zones = (("Shadows", "SplitToningShadowHue", "SplitToningShadowSaturation", "ColorGradeShadowLum"),
             ("Midtones", "ColorGradeMidtoneHue", "ColorGradeMidtoneSat", "ColorGradeMidtoneLum"),
             ("Highlights", "SplitToningHighlightHue", "SplitToningHighlightSaturation", "ColorGradeHighlightLum"),
             ("Global", "ColorGradeGlobalHue", "ColorGradeGlobalSat", "ColorGradeGlobalLum"))
    for label, hue_key, sat_key, lum_key in zones:
        for hue in (0, 120, 240):
            out.append(Variant(f"Cal Grade {label} H{hue} S50", {hue_key: str(hue), sat_key: "50"}, fits=("K_GRADE_SAT",)))
        out += _sweep(f"Grade {label} Lum", lum_key, (-50, 50), ("K_GRADE_LUM",))
    blue_shadows = {"SplitToningShadowHue": "240", "SplitToningShadowSaturation": "50"}
    out += _sweep("Grade Balance", "SplitToningBalance", (-50, 50), ("K_GRADE_SAT",), blue_shadows)
    out += [Variant(f"Cal Grade Blending {v}", {**blue_shadows, "ColorGradeBlending": str(v)}, fits=("K_GRADE_SAT",))
            for v in (0, 100)]
    mixer = {"ConvertToGrayscale": "True", **{f"GrayMixer{b.capitalize()}": "0" for b in BANDS}}
    out.append(Variant("Cal BW Neutral Mix", mixer))
    for band in BANDS:
        b = band.capitalize()
        out.append(Variant(f"Cal BW Mix {b} +100", {**mixer, f"GrayMixer{b}": "+100"}, fits=("K_MIXER",)))
    return tuple(out)


VARIANTS: tuple[Variant, ...] = _build()


# --- preset files -------------------------------------------------------------------------------

_BASE_ATTRS = {
    "PresetType": "Normal", "Cluster": "", "SupportsAmount": "False", "SupportsColor": "True",
    "SupportsMonochrome": "True", "SupportsHighDynamicRange": "True", "SupportsNormalDynamicRange": "True",
    "SupportsSceneReferred": "True", "SupportsOutputReferred": "True", "CameraModelRestriction": "",
    "Copyright": "", "ContactInfo": "", "Version": "15.0", "ProcessVersion": "11.0", "HasSettings": "True",
}


def variant_xmp(v: Variant) -> str:
    """A complete Lightroom preset (.xmp) for one calibration variant."""
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
   <crs:Group><rdf:Alt><rdf:li xml:lang="x-default">{CAL_GROUP}</rdf:li></rdf:Alt></crs:Group>
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


# --- command line ------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m colorlab.calibration", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("presets", help="write the single-slider calibration presets")
    pre.add_argument("out_dir", type=Path)
    fp = sub.add_parser("fit", help="fit the pipeline strengths to collected calibration bakes")
    fp.add_argument("lut_dir", type=Path)
    fp.add_argument("--grid", type=int, default=17, help="sample grid size per axis (default 17)")
    fp.add_argument("--json", type=Path, help="also write the report as JSON")
    args = parser.parse_args(argv)
    try:
        if args.command == "presets":
            paths = write_calibration_presets(args.out_dir)
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
