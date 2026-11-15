"""Spatial calibration kit: measure Lightroom's spatial effects on synthetic charts.

    python -m colorlab.spatial_calibration kit out/spatial-kit       # then Lightroom, see the kit README
    python -m colorlab.spatial_calibration collect <exports> out/spatial-kit out/spatial-exports

A Hald LUT can't capture grain, vignetting, sharpening, noise reduction or local tone
(highlights/shadows, clarity, texture, dehaze): they depend on a pixel's neighbourhood
and position. Instead, each variant moves one spatial slider on one of three charts,
and the export is compared, pixel by pixel, with the chart's identity export:

* ``scene``: big flat gray patches (the global part of local tone), bar gratings of
  several periods (halo and frequency response), a smooth ramp and color patches.
  Used by highlights, shadows, clarity, texture, dehaze and sharpening.
* ``levels``: flat gray bands (grain strength against brightness), plus a luma-noise
  and a color-noise patch for noise reduction.
* ``checker``: a fine checker of four grays, so every gray sits at every distance from
  the centre (the vignette's radial profile and its highlight protection).

Every chart image carries its variant's settings as an embedded Lightroom sidecar,
which Lightroom applies on import. The manifest records each chart's regions.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .bake import make_sidecar_xmp, read_rgb_tiff, write_rgb_tiff16
from .calibration import Variant, variant_xmp
from .errors import ColorlabError

__all__ = ["CHARTS", "SCALE_SIZES", "SCALE_VARIANTS", "SIZE", "SPATIAL_GROUP", "SPATIAL_VARIANTS", "SpatialVariant",
           "charts", "collect_spatial", "main", "read_rgb_tiff", "write_spatial_kit"]

SPATIAL_GROUP = "FilmSim Spatial Calibration"
SIZE = (1024, 1536)  # height, width: a 3:2 frame like a photo
SCALE_SIZES = ((512, 768), (2048, 3072))  # the image-size test: half and double the frame
CHARTS = ("scene", "levels", "checker")

#: an export whose 99.9th-percentile difference from its chart is below this had no preset applied
UNCHANGED_TOLERANCE = 2e-3

_NOISE_SEED = 20261115


# --- charts --------------------------------------------------------------------------------------

def _region(kind: str, box: tuple[int, int, int, int], **extra) -> dict:
    return {"kind": kind, "box": [int(v) for v in box], **extra}


def _px(n: int, size: tuple[int, int]) -> int:
    """A feature size in pixels, scaled from the default frame width to ``size``."""
    return max(1, round(n * size[1] / SIZE[1]))


def _scene(size: tuple[int, int]) -> tuple[np.ndarray, dict]:
    h, w = size
    img = np.zeros((h, w, 3))
    regions: dict[str, dict] = {}
    # left half: 2 x 4 big flat patches, dark to light
    levels = (0.03, 0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95)
    pw, ph = w // 4, h // 4
    for i, level in enumerate(levels):
        y0, x0 = (i % 4) * ph, (i // 4) * pw
        img[y0:y0 + ph, x0:x0 + pw] = level
        regions[f"flat {level:.2f}"] = _region("flat", (y0, y0 + ph, x0, x0 + pw), value=[level] * 3)
    # right top: bar gratings, one segment per bar width
    lo, hi = 0.2, 0.8
    x = w // 2
    seg = (w // 2) // 6
    for i, width in enumerate(_px(b, size) for b in (2, 4, 8, 16, 32, 64)):
        cols = np.arange(seg)
        img[0:h // 2, x:x + seg] = np.where((cols // width) % 2 == 0, lo, hi)[None, :, None]
        regions[f"bars {i}"] = _region("bars", (0, h // 2, x, x + seg), period=2 * width, levels=[lo, hi])
        x += seg
    # right, third quarter: a smooth horizontal ramp
    x0, y0, y1 = w // 2, h // 2, 3 * h // 4
    img[y0:y1, x0:] = np.linspace(0.0, 1.0, w - x0)[None, :, None]
    regions["ramp"] = _region("ramp", (y0, y1, x0, w), range=[0.0, 1.0])
    # right, bottom quarter: saturated and muted colors
    colors = ((0.8, 0.1, 0.1), (0.1, 0.7, 0.15), (0.15, 0.25, 0.85), (0.9, 0.8, 0.1), (0.1, 0.75, 0.8),
              (0.75, 0.15, 0.75), (0.85, 0.6, 0.5), (0.45, 0.55, 0.7), (0.4, 0.5, 0.3), (0.6, 0.45, 0.35),
              (0.95, 0.95, 0.9), (0.08, 0.08, 0.12))
    cw = (w - x0) // len(colors)
    for i, c in enumerate(colors):
        xa = x0 + i * cw
        img[y1:h, xa:xa + cw] = c
        regions[f"color {i:02d}"] = _region("color", (y1, h, xa, xa + cw), value=list(c))
    return img, regions


def _levels(size: tuple[int, int]) -> tuple[np.ndarray, dict]:
    h, w = size
    img = np.zeros((h, w, 3))
    regions: dict[str, dict] = {}
    levels = (0.05, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 0.97)
    bw, bh = w // len(levels), 5 * h // 8
    for i, level in enumerate(levels):
        img[:bh, i * bw:(i + 1) * bw] = level
        regions[f"flat {level:.2f}"] = _region("flat", (0, bh, i * bw, (i + 1) * bw), value=[level] * 3)
    rng = np.random.default_rng(_NOISE_SEED)
    half = w // 2
    luma = 0.5 + rng.normal(0.0, 0.05, (h - bh, half))
    img[bh:, :half] = np.clip(luma, 0.0, 1.0)[..., None]
    regions["noise"] = _region("noise", (bh, h, 0, half), mean=0.5, sigma=0.05)
    img[bh:, half:] = np.clip(0.5 + rng.normal(0.0, 0.04, (h - bh, w - half, 3)), 0.0, 1.0)
    regions["color noise"] = _region("color_noise", (bh, h, half, w), mean=0.5, sigma=0.04)
    return img, regions


def _checker(size: tuple[int, int]) -> tuple[np.ndarray, dict]:
    h, w = size
    cell = max(2, _px(32, size))
    levels = (0.2, 0.45, 0.7, 0.95)
    rows, cols = np.arange(h)[:, None] // cell, np.arange(w)[None, :] // cell
    index = (rows % 2) * 2 + (cols % 2)  # every 2 x 2 block holds all four levels
    img = np.repeat(np.asarray(levels)[index][..., None], 3, axis=2)
    return img, {"checker": _region("checker", (0, h, 0, w), cell=cell, levels=list(levels))}


def charts(size: tuple[int, int] = SIZE) -> tuple[dict[str, np.ndarray], dict[str, dict]]:
    """The three charts (sRGB-encoded 0-1 images of ``size``, default ``SIZE``) and each chart's
    named regions. At other sizes every feature scales with the frame width, like a resized photo."""
    size = (int(size[0]), int(size[1]))
    built = {"scene": _scene(size), "levels": _levels(size), "checker": _checker(size)}
    return {k: v[0] for k, v in built.items()}, {k: v[1] for k, v in built.items()}


# --- variants ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SpatialVariant:
    """One spatial calibration image: a chart and the Lightroom settings applied to it."""

    name: str
    chart: str
    settings: Mapping[str, str]
    size: tuple[int, int] = SIZE

    @property
    def baseline(self) -> bool:
        return " Identity " in f"{self.name} "


#: every variant pins the spatial sliders Lightroom might default on for a TIFF
_OFF = {"Sharpness": "0", "LuminanceSmoothing": "0", "ColorNoiseReduction": "0", "GrainAmount": "0",
        "PostCropVignetteAmount": "0"}
_SHARPEN = {"SharpenRadius": "+1.0", "SharpenDetail": "25", "SharpenEdgeMasking": "0"}
_GRAIN = {"GrainSize": "25", "GrainFrequency": "50"}
_VIGNETTE = {"PostCropVignetteMidpoint": "50", "PostCropVignetteFeather": "50", "PostCropVignetteRoundness": "0",
             "PostCropVignetteStyle": "1", "PostCropVignetteHighlightContrast": "0"}
_LUMA_NR = {"LuminanceNoiseReductionDetail": "50", "LuminanceNoiseReductionContrast": "0"}
_COLOR_NR = {"ColorNoiseReductionDetail": "50", "ColorNoiseReductionSmoothness": "50"}


def _v(name: str, chart: str, *settings: Mapping[str, str]) -> SpatialVariant:
    merged: dict[str, str] = dict(_OFF)
    for s in settings:
        merged.update(s)
    return SpatialVariant(f"Spatial {name}", chart, merged)


def _build() -> tuple[SpatialVariant, ...]:
    out = [_v(f"Identity {c.capitalize()}", c) for c in CHARTS]
    signed = (-100, -50, -25, 25, 50, 100)
    for label, key in (("Highlights", "Highlights2012"), ("Shadows", "Shadows2012"), ("Clarity", "Clarity2012"),
                       ("Texture", "Texture"), ("Dehaze", "Dehaze")):
        out += [_v(f"{label} {a:+g}", "scene", {key: f"{a:+g}"}) for a in signed]
    out += [_v(f"Sharpen {a}", "scene", _SHARPEN, {"Sharpness": str(a)}) for a in (25, 50, 100)]
    out += [_v("Sharpen 50 Radius 3.0", "scene", _SHARPEN, {"Sharpness": "50", "SharpenRadius": "+3.0"}),
            _v("Sharpen 50 Detail 100", "scene", _SHARPEN, {"Sharpness": "50", "SharpenDetail": "100"}),
            _v("Sharpen 50 Masking 50", "scene", _SHARPEN, {"Sharpness": "50", "SharpenEdgeMasking": "50"})]
    out += [_v(f"Grain {a}", "levels", _GRAIN, {"GrainAmount": str(a)}) for a in (10, 25, 50, 100)]
    out += [_v(f"Grain 50 Size {s}", "levels", _GRAIN, {"GrainAmount": "50", "GrainSize": str(s)})
            for s in (10, 50, 100)]
    out += [_v(f"Grain 50 Roughness {r}", "levels", _GRAIN, {"GrainAmount": "50", "GrainFrequency": str(r)})
            for r in (0, 100)]
    out += [_v(f"Luminance NR {a}", "levels", _LUMA_NR, {"LuminanceSmoothing": str(a)}) for a in (25, 50, 100)]
    out += [_v(f"Color NR {a}", "levels", _COLOR_NR, {"ColorNoiseReduction": str(a)}) for a in (25, 50, 100)]
    out += [_v(f"Vignette {a:+g}", "checker", _VIGNETTE, {"PostCropVignetteAmount": f"{a:+g}"}) for a in signed]
    shape = [("Feather 0", {"PostCropVignetteFeather": "0"}), ("Feather 100", {"PostCropVignetteFeather": "100"}),
             ("Midpoint 0", {"PostCropVignetteMidpoint": "0"}), ("Midpoint 100", {"PostCropVignetteMidpoint": "100"}),
             ("Roundness -100", {"PostCropVignetteRoundness": "-100"}),
             ("Roundness +100", {"PostCropVignetteRoundness": "+100"}),
             ("Style 2", {"PostCropVignetteStyle": "2"}), ("Style 3", {"PostCropVignetteStyle": "3"}),
             ("Highlights 50", {"PostCropVignetteHighlightContrast": "50"}),
             ("Highlights 100", {"PostCropVignetteHighlightContrast": "100"})]
    out += [_v(f"Vignette -50 {label}", "checker", _VIGNETTE, {"PostCropVignetteAmount": "-50"}, s)
            for label, s in shape]
    return tuple(out)


SPATIAL_VARIANTS: tuple[SpatialVariant, ...] = _build()

_SCALE_EFFECTS = ("Identity Scene", "Identity Levels", "Identity Checker", "Shadows +50", "Clarity +50",
                  "Clarity -50", "Texture +50", "Dehaze +50", "Sharpen 50", "Grain 50", "Vignette -50")


def _build_scale() -> tuple[SpatialVariant, ...]:
    by_name = {v.name: v for v in SPATIAL_VARIANTS}
    out = []
    for h, w in SCALE_SIZES:
        for effect in _SCALE_EFFECTS:
            ref = by_name[f"Spatial {effect}"]
            out.append(SpatialVariant(f"Spatial {w}x{h} {effect}", ref.chart, ref.settings, (h, w)))
    return tuple(out)


#: the image-size test: the same settings on the same charts at half and double the frame
SCALE_VARIANTS: tuple[SpatialVariant, ...] = _build_scale()


# --- kit -----------------------------------------------------------------------------------------

README = """FilmSim spatial calibration kit
===============================
images/         {n} charts (16-bit TIFF, {w}x{h}), one per variant, named after it. Each carries its
                settings as an embedded Lightroom sidecar.
presets/        the same settings as presets (group "{group}"), for applying by hand
manifest.json   which chart and settings each image has, and where each chart's regions are

Each image moves one spatial slider: highlights, shadows, clarity, texture, dehaze, sharpening,
grain, noise reduction or the post-crop vignette. The "Spatial Identity" images move nothing; they
are the baseline every other export is compared with. The kit is about {mb} MB and so is the export.

Lightroom Classic
-----------------
1. Optional: File > Import Develop Profiles and Presets... and select every file in presets/.
2. Import the images/ folder (Add, don't move or copy). Lightroom should apply each image's
   embedded settings on import: the grain, vignette and noise-reduction images show it plainly.
3. An image that still looks untouched (other than the Identity ones) didn't pick its settings
   up: apply to it the preset with the same name. (`collect` lists any export that comes back
   unedited.)
4. Don't crop, rotate, resize or otherwise edit the images.
5. Select all of them and choose File > Export:
     File Naming:    Filename (keep the original names)
     File Settings:  TIFF, Compression None, Color Space sRGB, Bit Depth 16 bits/component
     Image Sizing:   Resize to Fit OFF
     Output Sharpening OFF, Watermark OFF
6. Run:
     python -m colorlab.spatial_calibration collect <export folder> <this kit folder> <output folder>
"""


def _chart_key(chart: str, size: tuple[int, int]) -> str:
    """Manifest key of a chart's regions: its name at the default size, else "name WxH"."""
    return chart if tuple(size) == SIZE else f"{chart} {size[1]}x{size[0]}"


def write_spatial_kit(out_dir: str | Path, variants: Sequence[SpatialVariant] = SPATIAL_VARIANTS) -> list[Path]:
    """Write a chart image (with its settings embedded) and a preset per variant, plus the manifest."""
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty; choose a new folder for the spatial calibration kit")
    built: dict[tuple[int, int], tuple[dict, dict]] = {}
    chart_regions: dict[str, dict] = {}
    (out / "presets").mkdir(parents=True, exist_ok=True)
    paths = []
    for v in variants:
        if v.size not in built:
            built[v.size] = charts(v.size)
            for chart, regs in built[v.size][1].items():
                chart_regions[_chart_key(chart, v.size)] = regs
        xmp = variant_xmp(Variant(v.name, v.settings), group=SPATIAL_GROUP)
        (out / "presets" / f"{v.name}.xmp").write_text(xmp, encoding="utf-8")
        path = out / "images" / f"{v.name}.tif"
        write_rgb_tiff16(path, built[v.size][0][v.chart], xmp=make_sidecar_xmp(xmp))
        paths.append(path)
    manifest = {
        "version": 1,
        "size": list(SIZE),
        "charts": chart_regions,
        "entries": [{**asdict(v), "size": list(v.size), "settings": dict(v.settings), "baseline": v.baseline}
                    for v in variants],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    mb = round(sum(v.size[0] * v.size[1] for v in variants) * 6 / 1e6)
    (out / "README.txt").write_text(
        README.format(n=len(variants), w=SIZE[1], h=SIZE[0], group=SPATIAL_GROUP, mb=mb), encoding="utf-8")
    return paths


# --- collect -------------------------------------------------------------------------------------

@dataclass
class SpatialCollectReport:
    collected: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)  # exported without its settings applied


def collect_spatial(export_dir: str | Path, kit_dir: str | Path, out_dir: str | Path) -> SpatialCollectReport:
    """Check Lightroom's exports against the kit and copy the usable ones into ``out_dir``."""
    kit = Path(kit_dir)
    manifest = json.loads((kit / "manifest.json").read_text(encoding="utf-8"))
    exports = {p.stem.lower(): p for p in Path(export_dir).glob("*") if p.suffix.lower() in (".tif", ".tiff")}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = SpatialCollectReport()
    for entry in manifest["entries"]:
        name = entry["name"]
        path = exports.get(name.lower())
        if path is None:
            report.missing.append(name)
            continue
        data = read_rgb_tiff(path)
        h, w = entry.get("size", manifest["size"])
        if data.shape[:2] != (h, w):
            raise ColorlabError(f"{path}: export is {data.shape[1]}x{data.shape[0]}, expected {w}x{h} "
                                "(turn Resize to Fit off and don't crop)")
        source = read_rgb_tiff(kit / "images" / f"{name}.tif")
        if not entry["baseline"] and np.percentile(np.abs(data - source), 99.9) < UNCHANGED_TOLERANCE:
            report.unchanged.append(name)
            continue
        shutil.copyfile(path, out / f"{name}.tif")
        report.collected.append(name)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return report


# --- command line --------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m colorlab.spatial_calibration",
                                     description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    kit = sub.add_parser("kit", help="write the chart images and presets for Lightroom")
    kit.add_argument("out_dir", type=Path)
    kit.add_argument("--scale-test", action="store_true",
                     help="write the image-size test instead: a few effects at half and double the frame")
    col = sub.add_parser("collect", help="check Lightroom's exports and copy them next to the manifest")
    col.add_argument("export_dir", type=Path)
    col.add_argument("kit_dir", type=Path)
    col.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "kit":
            paths = write_spatial_kit(args.out_dir, SCALE_VARIANTS if args.scale_test else SPATIAL_VARIANTS)
            print(f"wrote {len(paths)} spatial calibration image(s) and preset(s) to {args.out_dir}; "
                  "see README.txt there")
        else:
            report = collect_spatial(args.export_dir, args.kit_dir, args.out_dir)
            print(f"collected {len(report.collected)}, missing {len(report.missing)}, "
                  f"unchanged {len(report.unchanged)} -> {args.out_dir}")
            for name in report.missing:
                print(f"  missing: {name}")
            for name in report.unchanged:
                print(f"  unchanged (settings not applied): {name}")
    except (ColorlabError, FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
