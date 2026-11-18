"""Whole-preset check against Lightroom: each preset, spatial settings included, on the scene chart.

    python -m colorlab.preset_check kit <preset dir> <kit dir> [--size 1024x1536]
    python -m colorlab.preset_check compare <export dir> <kit dir> <preset dir> [--luts DIR] [--json PATH]

``kit`` writes a full copy of every preset (only geometry is neutralized, so the chart stays in
place) and the scene chart from :mod:`colorlab.spatial_calibration` with that copy embedded as a
Lightroom sidecar. Lightroom applies it on import; export the images as 16-bit sRGB TIFFs.

``compare`` scores, per preset, mean CIEDE2000 against Lightroom's export:

* ``lr_change``: Lightroom's own change from the chart.
* ``global``: our global look alone (:func:`colorlab.pipeline.render`).
* ``after`` / ``before``: :func:`colorlab.finish.render_preset` with the neighbourhood passes after
  or before the look. ``*_smooth`` are the same after a Gaussian blur (sigma 2 px) of both
  images, so grain, whose pattern isn't reproduced, adds little.
* ``lut_*`` (with ``--luts``, the ``.cube`` files baked from Lightroom by ``colorlab.bake``): the
  same with Lightroom's own global look, so they measure the spatial passes alone.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .bake import _UNSAFE, make_bake_xmp, make_sidecar_xmp, read_rgb_tiff, write_rgb_tiff16
from .errors import ColorlabError
from .film import gaussian_blur
from .finish import render_preset
from .lut import apply_lut, read_cube
from .metrics import delta_e_2000, srgb_to_lab
from .pipeline import render
from .preset import Preset, load_preset_dir
from .spatial_calibration import SIZE, charts

FULL_PREFIX = "FilmSim Full - "
FULL_GROUP = "FilmSim Full Check"
UNCHANGED_TOLERANCE = 2e-3
SMOOTH_SIGMA = 2.0


@dataclass(frozen=True)
class KitEntry:
    kit_name: str
    preset_name: str
    source: str


@dataclass
class CompareReport:
    rows: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


README = """FilmSim whole-preset check kit
==============================
presets/        {n} full copies of your presets (group "{group}"), spatial settings kept
images/         {n} copies of the scene chart ({w}x{h}), each carrying its preset as a sidecar
manifest.json   which image belongs to which original preset

Lightroom Classic
-----------------
1. File > Import Develop Profiles and Presets... and select every file in presets/.
   Profiles your presets depend on must be installed too.
2. Import the images/ folder (Add). Lightroom should apply each image's settings on import;
   if an image still looks like the plain chart, apply the "{group}" preset with its name.
3. Don't crop, rotate, resize or otherwise edit the images.
4. Select all of them and choose File > Export:
     File Naming:    Filename (keep the original names)
     File Settings:  TIFF, Compression None, Color Space sRGB, Bit Depth 16 bits/component
     Image Sizing:   Resize to Fit OFF
     Output Sharpening OFF, Watermark OFF
5. Run:
     python -m colorlab.preset_check compare <export folder> <this kit folder> <preset folder> --luts <baked luts>
"""


def _normal_xmp(p: Preset) -> bool:
    return p.source is not None and p.source.suffix.lower() == ".xmp" and p.preset_type in ("Normal", "")


def write_preset_kit(preset_dir: str | Path, out_dir: str | Path, size: tuple[int, int] = SIZE) -> list[KitEntry]:
    """Write the kit for every normal ``.xmp`` preset under ``preset_dir`` into a new ``out_dir``."""
    root = Path(preset_dir).resolve()
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty; choose a new folder for the kit")
    chart = charts(size)[0]["scene"]
    entries: list[KitEntry] = []
    used: set[str] = set()
    for p in load_preset_dir(root):
        if not _normal_xmp(p):
            continue
        base = FULL_PREFIX + _UNSAFE.sub("_", p.name or p.source.stem).strip()
        name, k = base, 2
        while name.lower() in used:
            name, k = f"{base} ({k})", k + 1
        used.add(name.lower())
        copy = make_bake_xmp(p.source.read_text(encoding="utf-8-sig"), name, group=FULL_GROUP, keep_spatial=True)
        (out / "presets").mkdir(parents=True, exist_ok=True)
        (out / "images").mkdir(parents=True, exist_ok=True)
        (out / "presets" / f"{name}.xmp").write_text(copy, encoding="utf-8")
        write_rgb_tiff16(out / "images" / f"{name}.tif", chart, xmp=make_sidecar_xmp(copy))
        entries.append(KitEntry(name, p.name, p.source.resolve().relative_to(root).as_posix()))
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"version": 1, "chart": "scene", "size": list(size), "entries": [asdict(e) for e in entries]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "README.txt").write_text(README.format(n=len(entries), group=FULL_GROUP, w=size[1], h=size[0]),
                                    encoding="utf-8")
    return entries


def _de(ours: np.ndarray, theirs: np.ndarray) -> float:
    return float(delta_e_2000(srgb_to_lab(np.clip(ours, 0.0, 1.0)), srgb_to_lab(theirs)).mean())


def _smooth(img: np.ndarray) -> np.ndarray:
    return gaussian_blur(img, SMOOTH_SIGMA)


def _scores(prefix: str, preset: Preset, chart: np.ndarray, theirs: np.ndarray, seed: int,
            look: Callable[[np.ndarray], np.ndarray] | None) -> dict[str, float]:
    glob = look(chart) if look else render(preset, chart)
    after = render_preset(preset, chart, seed=seed, look=look)
    before = render_preset(preset, chart, seed=seed, local_first=True, look=look)
    smooth = _smooth(theirs)
    return {f"{prefix}global": _de(glob, theirs), f"{prefix}after": _de(after, theirs),
            f"{prefix}before": _de(before, theirs), f"{prefix}after_smooth": _de(_smooth(after), smooth),
            f"{prefix}before_smooth": _de(_smooth(before), smooth)}


def compare_exports(export_dir: str | Path, kit_dir: str | Path, preset_dir: str | Path,
                    lut_dir: str | Path | None = None, seed: int = 0) -> CompareReport:
    """Score Lightroom's exports of the kit against our renders (see the module notes)."""
    kit = Path(kit_dir)
    manifest = json.loads((kit / "manifest.json").read_text(encoding="utf-8"))
    root = Path(preset_dir).resolve()
    presets = {p.source.resolve().relative_to(root).as_posix(): p for p in load_preset_dir(root) if _normal_xmp(p)}
    exports = {p.stem.lower(): p for p in Path(export_dir).glob("*") if p.suffix.lower() in (".tif", ".tiff")}
    report = CompareReport()
    for entry in manifest["entries"]:
        name = entry["kit_name"]
        path, preset = exports.get(name.lower()), presets.get(entry["source"])
        if path is None or preset is None:
            report.missing.append(name)
            continue
        chart = read_rgb_tiff(kit / "images" / f"{name}.tif").astype(np.float64)
        theirs = read_rgb_tiff(path).astype(np.float64)
        if theirs.shape != chart.shape:
            raise ColorlabError(f"{path}: {theirs.shape[1]}x{theirs.shape[0]} is not the kit's chart size")
        if np.abs(theirs - chart).mean() < UNCHANGED_TOLERANCE:
            report.unchanged.append(name)
            continue
        row: dict = {"name": entry["preset_name"], "lr_change": _de(chart, theirs)}
        row.update(_scores("", preset, chart, theirs, seed, None))
        cube = Path(lut_dir) / f"{name[len(FULL_PREFIX):]}.cube" if lut_dir else None
        if cube is not None and cube.is_file():
            lut = read_cube(cube)
            row.update(_scores("lut_", preset, chart, theirs, seed, lambda x: apply_lut(lut, x).astype(np.float64)))
        report.rows.append(row)
    return report


_COLUMNS = ("lr_change", "global", "after", "before", "after_smooth", "before_smooth", "lut_global", "lut_after",
            "lut_before", "lut_after_smooth", "lut_before_smooth")


def _print_report(report: CompareReport) -> None:
    cols = [c for c in _COLUMNS if any(c in r for r in report.rows)]
    width = max([len(r["name"]) for r in report.rows] + [6])
    print(f"{'preset':<{width}} " + " ".join(f"{c:>17}" for c in cols))
    for r in report.rows:
        print(f"{r['name']:<{width}} " + " ".join(f"{r[c]:>17.3f}" if c in r else " " * 17 for c in cols))
    for label, fn in (("median", statistics.median), ("mean", statistics.fmean)):
        values = [fn([r[c] for r in report.rows if c in r]) for c in cols]
        print(f"{label:<{width}} " + " ".join(f"{v:>17.3f}" for v in values))
    if report.missing:
        print(f"missing {len(report.missing)}: " + ", ".join(report.missing))
    if report.unchanged:
        print(f"unchanged (Lightroom didn't apply the preset) {len(report.unchanged)}: " + ", ".join(report.unchanged))


def _size(text: str) -> tuple[int, int]:
    parts = text.lower().split("x")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"--size must look like 1024x1536 (height x width), got {text!r}")
    return int(parts[0]), int(parts[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m colorlab.preset_check", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    k = sub.add_parser("kit", help="write the whole-preset check kit")
    k.add_argument("presets", type=Path)
    k.add_argument("out", type=Path)
    k.add_argument("--size", default=f"{SIZE[0]}x{SIZE[1]}", help="chart size, height x width")
    c = sub.add_parser("compare", help="score Lightroom's exports of the kit")
    c.add_argument("exports", type=Path)
    c.add_argument("kit", type=Path)
    c.add_argument("presets", type=Path)
    c.add_argument("--luts", type=Path, help="Lightroom's baked .cube files, named after the presets")
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--json", type=Path, help="also write the report here")
    args = parser.parse_args(argv)
    try:
        if args.command == "kit":
            entries = write_preset_kit(args.presets, args.out, _size(args.size))
            print(f"wrote {len(entries)} presets and images to {args.out}; see README.txt")
            return 0
        report = compare_exports(args.exports, args.kit, args.presets, args.luts, args.seed)
        _print_report(report)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    except (ValueError, OSError, ColorlabError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
