"""Bake Lightroom presets into exact 3D LUTs with a Hald round trip.

1. ``kit``: for every user preset, write a *bake copy* (spatial and geometric
   settings neutralized, renamed into the "FilmSim Bake" group) plus a 16-bit
   identity Hald TIFF named after it, with the bake settings embedded as XMP.
2. In Lightroom, apply each bake preset to its same-named Hald, then export
   16-bit sRGB TIFFs keeping the original file names.
3. ``collect``: turn the exported images back into ``.cube`` LUTs
   (sRGB-encoded in, sRGB-encoded out).

Spatial effects (grain, vignette, sharpening, noise reduction, local tone:
highlights/shadows/clarity/texture/dehaze) are zeroed because they would mix
neighbouring Hald cells. The FilmSim engine applies them on top of the LUT.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from html import escape
from pathlib import Path

import numpy as np
import tifffile

from .errors import ColorlabError, LutFormatError, PresetFormatError
from .lut import Lut3D, hald_identity, lut_from_hald, write_cube
from .preset import load_preset_dir

BAKE_PREFIX = "FilmSim Bake - "
BAKE_GROUP = "FilmSim Bake"
DEFAULT_LEVEL = 8  # 64^3 cube, 512 x 512 image

#: setting -> neutral value written into bake copies (only replaced when present)
NEUTRALIZED_SETTINGS: dict[str, str] = {
    # spatial / local effects
    "GrainAmount": "0",
    "PostCropVignetteAmount": "0",
    "VignetteAmount": "0",
    "Sharpness": "0",
    "LuminanceSmoothing": "0",
    "ColorNoiseReduction": "0",
    "Highlights2012": "0",
    "Shadows2012": "0",
    "Clarity2012": "0",
    "Clarity": "0",
    "Texture": "0",
    "Dehaze": "0",
    "DefringePurpleAmount": "0",
    "DefringeGreenAmount": "0",
    # geometry (would scramble the Hald layout)
    "HasCrop": "False",
    "CropTop": "0",
    "CropLeft": "0",
    "CropBottom": "1",
    "CropRight": "1",
    "CropAngle": "0",
    "PerspectiveVertical": "0",
    "PerspectiveHorizontal": "0",
    "PerspectiveRotate": "0",
    "PerspectiveScale": "100",
    "PerspectiveAspect": "0",
    "PerspectiveX": "0",
    "PerspectiveY": "0",
    "PerspectiveUpright": "0",
    "LensManualDistortionAmount": "0",
    "LensProfileEnable": "0",
    "AutoLateralCA": "0",
}

_UNSAFE = re.compile(r'[/\\:*?"<>|]')
_START_TAG = re.compile(r"<rdf:Description\b[^>]*>")


def bake_name(preset_name: str) -> str:
    return BAKE_PREFIX + _UNSAFE.sub("_", preset_name).strip()


def _set_attr(tag: str, qname: str, value: str, insert: bool) -> str:
    pattern = re.compile(rf'(\s{re.escape(qname)}=")[^"]*(")')
    if pattern.search(tag):
        return pattern.sub(lambda m: m.group(1) + value + m.group(2), tag, count=1)
    if not insert:
        return tag
    end = len(tag) - (2 if tag.endswith("/>") else 1)
    return f'{tag[:end]} {qname}="{value}"{tag[end:]}'


def _replace_alt(text: str, element: str, value: str) -> tuple[str, int]:
    pattern = re.compile(rf"(<crs:{element}>\s*<rdf:Alt>\s*<rdf:li\b[^>]*>)(.*?)(</rdf:li>)", re.S)
    return pattern.subn(lambda m: m.group(1) + escape(value, quote=False) + m.group(3), text, count=1)


def make_bake_xmp(xmp_text: str, name: str) -> str:
    """Return a renamed, importable copy of a preset with spatial/geometric settings neutralized."""
    m = _START_TAG.search(xmp_text)
    if m is None:
        raise PresetFormatError("preset has no rdf:Description element")
    tag = m.group(0)
    for key, value in NEUTRALIZED_SETTINGS.items():
        tag = _set_attr(tag, f"crs:{key}", value, insert=False)
    tag = _set_attr(tag, "tiff:Orientation", "1", insert=False)
    tag = _set_attr(tag, "crs:UUID", uuid.uuid4().hex.upper(), insert=True)
    tag = _set_attr(tag, "crs:PresetType", "Normal", insert=True)
    tag = _set_attr(tag, "crs:HasSettings", "True", insert=True)

    rest, renamed = _replace_alt(xmp_text[m.end() :], "Name", name)
    if not renamed:
        raise PresetFormatError("preset has no crs:Name element")
    rest, _ = _replace_alt(rest, "Group", BAKE_GROUP)
    return xmp_text[: m.start()] + tag + rest


# --- Hald TIFFs ------------------------------------------------------------------------------

def write_hald_tiff(path: str | Path, level: int = DEFAULT_LEVEL, xmp: str | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.round(hald_identity(level).astype(np.float64) * 65535).astype(np.uint16)
    extratags = []
    if xmp is not None:
        payload = xmp.encode("utf-8")
        extratags.append((700, 1, len(payload), payload, True))
    tifffile.imwrite(path, data, photometric="rgb", extratags=extratags)


def read_hald_tiff(path: str | Path) -> Lut3D:
    data = tifffile.imread(path)
    if data.ndim == 3 and data.shape[2] == 4:
        data = data[..., :3]  # drop alpha
    if data.dtype == np.uint16:
        img = data / 65535.0
    elif data.dtype == np.uint8:
        img = data / 255.0
    else:
        raise LutFormatError(f"{path}: Hald TIFF must be 8- or 16-bit integer RGB, got {data.dtype}")
    try:
        return lut_from_hald(img)
    except LutFormatError as exc:
        raise LutFormatError(f"{path}: {exc}") from exc


# --- kit -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class KitEntry:
    bake_name: str
    preset_name: str
    source: str  # path relative to the preset folder (posix)


@dataclass
class CollectReport:
    collected: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


README = """FilmSim bake kit
================
presets/        {n} bake presets: copies of your presets with spatial and geometry settings zeroed
hald/           {n} identity images (16-bit TIFF, {w}x{w}), one per preset, named after it
manifest.json   which bake belongs to which original preset

Grain, vignette, sharpening, noise reduction, highlights/shadows, clarity, texture and
dehaze are zeroed because they would smear the Hald image. FilmSim adds them back itself.

Lightroom Classic
-----------------
1. File > Import Develop Profiles and Presets... and select every file in presets/.
   They appear in the preset group "FilmSim Bake". Any profiles your presets depend on
   (for example profile/look .xmp files) must be installed too.
2. Import the hald/ folder (Add, don't move or copy).
3. Open one image in Develop. If its settings already match its name (they're embedded
   in the file), skip to step 5.
4. Otherwise, apply to each image the "FilmSim Bake" preset with the same name.
   Don't crop, rotate, resize or otherwise edit the images.
5. Select all of them and choose File > Export:
     File Naming:    Filename (keep the original names)
     File Settings:  TIFF, Compression None, Color Space sRGB, Bit Depth 16 bits/component
     Image Sizing:   Resize to Fit OFF
     Output Sharpening OFF, Watermark OFF
6. Run:
     python -m colorlab.bake collect <export folder> <this kit folder> <output folder>

Lightroom (desktop/mobile): same steps. Import the presets and images, apply the
matching preset, and export as 16-bit sRGB TIFF with the original file names.
"""


def write_bake_kit(preset_dir: str | Path, out_dir: str | Path, level: int = DEFAULT_LEVEL) -> list[KitEntry]:
    root = Path(preset_dir).resolve()
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty; choose a new folder for the bake kit")
    entries: list[KitEntry] = []
    used: set[str] = set()
    for p in load_preset_dir(root):
        if p.source is None or p.source.suffix.lower() != ".xmp" or p.preset_type not in ("Normal", ""):
            continue
        base = bake_name(p.name or p.source.stem)
        name, k = base, 2
        while name.lower() in used:
            name, k = f"{base} ({k})", k + 1
        used.add(name.lower())
        baked = make_bake_xmp(p.source.read_text(encoding="utf-8-sig"), name)
        (out / "presets").mkdir(parents=True, exist_ok=True)
        (out / "presets" / f"{name}.xmp").write_text(baked, encoding="utf-8")
        write_hald_tiff(out / "hald" / f"{name}.tif", level, xmp=baked)
        entries.append(KitEntry(name, p.name, p.source.resolve().relative_to(root).as_posix()))
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"version": 1, "level": level, "entries": [asdict(e) for e in entries]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "README.txt").write_text(README.format(n=len(entries), w=level**3), encoding="utf-8")
    return entries


def collect_bakes(export_dir: str | Path, kit_dir: str | Path, out_dir: str | Path) -> CollectReport:
    manifest = json.loads((Path(kit_dir) / "manifest.json").read_text(encoding="utf-8"))
    level = int(manifest["level"])
    exports = {p.stem.lower(): p for p in Path(export_dir).glob("*") if p.suffix.lower() in (".tif", ".tiff")}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = CollectReport()
    for entry in manifest["entries"]:
        name = entry["bake_name"]
        path = exports.get(name.lower())
        if path is None:
            report.missing.append(name)
            continue
        lut = read_hald_tiff(path)
        if lut.size != level * level:
            raise LutFormatError(f"{path}: Hald level {round(lut.size ** 0.5)} does not match kit level {level}")
        cube_name = name[len(BAKE_PREFIX) :] if name.startswith(BAKE_PREFIX) else name
        write_cube(Lut3D(lut.table, title=entry["preset_name"]), out / f"{cube_name}.cube")
        report.collected.append(name)
    return report


# --- command line ------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m colorlab.bake", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    kit = sub.add_parser("kit", help="write bake presets + Hald TIFFs for Lightroom")
    kit.add_argument("preset_dir", type=Path)
    kit.add_argument("out_dir", type=Path)
    kit.add_argument("--level", type=int, default=DEFAULT_LEVEL, help="Hald level (default 8 = 64^3 LUT)")
    col = sub.add_parser("collect", help="convert Lightroom exports into .cube LUTs")
    col.add_argument("export_dir", type=Path)
    col.add_argument("kit_dir", type=Path)
    col.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "kit":
            entries = write_bake_kit(args.preset_dir, args.out_dir, args.level)
            print(f"wrote {len(entries)} bake preset(s) to {args.out_dir}; see README.txt there")
        else:
            report = collect_bakes(args.export_dir, args.kit_dir, args.out_dir)
            print(f"collected {len(report.collected)}, missing {len(report.missing)} -> {args.out_dir}")
            for name in report.missing:
                print(f"  missing: {name}")
    except (ColorlabError, FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
