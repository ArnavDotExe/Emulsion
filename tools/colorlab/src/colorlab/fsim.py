"""The ``.fsim.json`` look format: a look's global colour as a 3D LUT plus its spatial passes.

A look file is one JSON object::

    {
      "format": "fsim",
      "version": 1,
      "name": "Warm Fade",
      "description": "...", "author": "...", "license": "...",
      "color": {"lut": {"size": 33, "data": "<base64>"}},
      "local": {"clarity": 20, "shadows": 30},
      "vignette": {"amount": -25},
      "grain": {"amount": 15, "size": 30}
    }

- ``name`` is required (1-80 characters); ``description``, ``author`` and ``license`` are
  optional text.
- ``color.lut`` is a ``size``-point 3D LUT (2..65), sRGB-encoded in and out. ``data`` is base64
  of ``size**3 * 3`` little-endian uint16 values (0..65535 for 0..1) in ``.cube`` order: red
  changes fastest, then green, then blue, three values per point.
- ``local``, ``vignette`` and ``grain`` are optional and hold the spatial passes in Lightroom's
  slider units (see :data:`SETTINGS`). Settings left out take their defaults.

A renderer runs, on the sRGB-encoded image: the local passes (noise reduction,
Highlights/Shadows, Dehaze, Texture, Clarity, sharpening), the vignette, the LUT, then the
grain. That is the order that matched Lightroom's exports of 44 presets best
(:func:`colorlab.finish.render_preset`). :func:`apply` is the reference renderer.

Unknown sections or settings, out-of-range values and other versions are rejected, so a newer
file never renders wrongly in silence.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import numpy as np

from . import finish
from .errors import ColorlabError, FsimFormatError
from .lut import Lut3D, apply_lut
from .pipeline import render_lut
from .preset import Preset

FORMAT = "fsim"
FORMAT_VERSION = 1
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_NAME = 80
MAX_TEXT_FIELD = 2000
LUT_SIZES = (2, 65)
DEFAULT_LUT_SIZE = 33

#: section -> setting -> (Lightroom key, default, minimum, maximum)
SETTINGS: Mapping[str, Mapping[str, tuple[str, float, float, float]]] = MappingProxyType({
    "local": MappingProxyType({
        "luminance_noise_reduction": ("LuminanceSmoothing", 0.0, 0.0, 100.0),
        "color_noise_reduction": ("ColorNoiseReduction", 0.0, 0.0, 100.0),
        "highlights": ("Highlights2012", 0.0, -100.0, 100.0),
        "shadows": ("Shadows2012", 0.0, -100.0, 100.0),
        "dehaze": ("Dehaze", 0.0, -100.0, 100.0),
        "texture": ("Texture", 0.0, -100.0, 100.0),
        "clarity": ("Clarity2012", 0.0, -100.0, 100.0),
        "sharpen_amount": ("Sharpness", 0.0, 0.0, 150.0),
        "sharpen_radius": ("SharpenRadius", 1.0, 0.5, 3.0),
        "sharpen_detail": ("SharpenDetail", 25.0, 0.0, 100.0),
        "sharpen_masking": ("SharpenEdgeMasking", 0.0, 0.0, 100.0),
    }),
    "vignette": MappingProxyType({
        "amount": ("PostCropVignetteAmount", 0.0, -100.0, 100.0),
        "midpoint": ("PostCropVignetteMidpoint", 50.0, 0.0, 100.0),
        "feather": ("PostCropVignetteFeather", 50.0, 0.0, 100.0),
        "roundness": ("PostCropVignetteRoundness", 0.0, -100.0, 100.0),
        "style": ("PostCropVignetteStyle", 1.0, 1.0, 3.0),
        "highlights": ("PostCropVignetteHighlightContrast", 0.0, 0.0, 100.0),
    }),
    "grain": MappingProxyType({
        "amount": ("GrainAmount", 0.0, 0.0, 100.0),
        "size": ("GrainSize", 25.0, 0.0, 100.0),
        "roughness": ("GrainFrequency", 50.0, 0.0, 100.0),
    }),
})
_WHOLE_NUMBER_SETTINGS = {("vignette", "style")}
_TEXT_FIELDS = ("description", "author", "license")
_TOP_LEVEL = {"format", "version", "name", *_TEXT_FIELDS, "color", *SETTINGS}


@dataclass(frozen=True, eq=False)
class Look:
    """A look: a name, its global colour as ``lut`` and its spatial settings per section.

    ``local``, ``vignette`` and ``grain`` keep only the settings that differ from the default.
    """

    name: str
    lut: Lut3D
    local: Mapping[str, float] = field(default_factory=dict)
    vignette: Mapping[str, float] = field(default_factory=dict)
    grain: Mapping[str, float] = field(default_factory=dict)
    description: str = ""
    author: str = ""
    license: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > MAX_NAME:
            raise FsimFormatError(f"name must be 1-{MAX_NAME} characters, got {self.name!r}")
        for key in _TEXT_FIELDS:
            value = getattr(self, key)
            if not isinstance(value, str) or len(value) > MAX_TEXT_FIELD:
                raise FsimFormatError(f"{key} must be text of at most {MAX_TEXT_FIELD} characters")
        if not isinstance(self.lut, Lut3D):
            raise FsimFormatError("a look needs a Lut3D")
        if not LUT_SIZES[0] <= self.lut.size <= LUT_SIZES[1]:
            raise FsimFormatError(f"LUT size must be {LUT_SIZES[0]}..{LUT_SIZES[1]}, got {self.lut.size}")
        for section in SETTINGS:
            object.__setattr__(self, section, MappingProxyType(_checked_section(section, getattr(self, section))))

    def settings(self) -> dict[str, str]:
        """The spatial settings as Camera Raw keys, defaults included, for :mod:`colorlab.finish`."""
        out = {}
        for section, spec in SETTINGS.items():
            values = getattr(self, section)
            for name, (key, default, _, _) in spec.items():
                out[key] = f"{values.get(name, default):g}"
        return out


def _checked_section(section: str, values: object) -> dict[str, float]:
    if not isinstance(values, Mapping):
        raise FsimFormatError(f"{section}: expected an object")
    spec = SETTINGS[section]
    out = {}
    for name, value in values.items():
        if name not in spec:
            raise FsimFormatError(f"{section}: unknown setting {name!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
            raise FsimFormatError(f"{section}.{name}: expected a number, got {value!r}")
        _, default, lo, hi = spec[name]
        if not lo <= value <= hi:
            raise FsimFormatError(f"{section}.{name} must be in {lo:g}..{hi:g}, got {value:g}")
        if (section, name) in _WHOLE_NUMBER_SETTINGS and value != int(value):
            raise FsimFormatError(f"{section}.{name} must be a whole number, got {value:g}")
        if value != default:
            out[name] = float(value)
    return out


def from_preset(preset: Preset, name: str | None = None, lut_size: int = DEFAULT_LUT_SIZE,
                description: str = "", author: str = "", license: str = "") -> Look:
    """Bake ``preset`` into a look: its global colour into a ``lut_size`` LUT, and its spatial
    settings with a look profile's own Highlights/Shadows folded in (:func:`finish.spatial_settings`)."""
    settings = finish.spatial_settings(preset)
    sections = {section: {n: finish._number(settings, key, default) for n, (key, default, _, _) in spec.items()}
                for section, spec in SETTINGS.items()}
    return Look(name=preset.name if name is None else name, lut=render_lut(preset, lut_size),
                description=description, author=author, license=license, **sections)


def apply(look: Look, image: np.ndarray, seed: int = 0) -> np.ndarray:
    """The reference renderer: ``look`` on an sRGB-encoded H x W x 3 image (float, 0..1)."""
    settings = look.settings()
    img = finish.vignette_pass(finish.local_passes(image, settings), settings)
    img = apply_lut(look.lut, np.clip(img, 0.0, 1.0)).astype(np.float64)
    return finish.grain_pass(img, settings, seed)


# --- text -----------------------------------------------------------------------------------------

def _encode_lut(lut: Lut3D) -> str:
    cube = np.transpose(lut.table, (2, 1, 0, 3))  # [b, g, r]: red changes fastest
    q = np.round(np.clip(cube, 0.0, 1.0) * 65535.0).astype("<u2")
    return base64.b64encode(q.tobytes()).decode("ascii")


def _decode_lut(node: object) -> Lut3D:
    if not isinstance(node, Mapping) or set(node) != {"size", "data"}:
        raise FsimFormatError("color.lut: expected an object with 'size' and 'data'")
    size, data = node["size"], node["data"]
    if isinstance(size, bool) or not isinstance(size, int) or not LUT_SIZES[0] <= size <= LUT_SIZES[1]:
        raise FsimFormatError(f"color.lut.size must be a whole number {LUT_SIZES[0]}..{LUT_SIZES[1]}, got {size!r}")
    if not isinstance(data, str):
        raise FsimFormatError("color.lut.data: expected base64 text")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise FsimFormatError("color.lut.data: not valid base64") from None
    expected = size ** 3 * 3 * 2
    if len(raw) != expected:
        raise FsimFormatError(f"color.lut.data: expected {expected} bytes for size {size}, got {len(raw)}")
    cube = np.frombuffer(raw, dtype="<u2").reshape(size, size, size, 3).astype(np.float64) / 65535.0
    return Lut3D(np.transpose(cube, (2, 1, 0, 3)))


def _plain(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def dumps(look: Look) -> str:
    """``look`` as ``.fsim.json`` text. The same look always gives the same text."""
    doc: dict[str, object] = {"format": FORMAT, "version": FORMAT_VERSION, "name": look.name}
    for key in _TEXT_FIELDS:
        if getattr(look, key):
            doc[key] = getattr(look, key)
    doc["color"] = {"lut": {"size": look.lut.size, "data": _encode_lut(look.lut)}}
    for section, spec in SETTINGS.items():
        values = getattr(look, section)
        if values:
            doc[section] = {n: _plain(values[n]) for n in spec if n in values}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def loads(text: str) -> Look:
    """Parse and check ``.fsim.json`` text; any problem raises :class:`FsimFormatError`."""
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise FsimFormatError(f"look text is larger than {MAX_TEXT_BYTES} bytes")
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise FsimFormatError(f"not valid JSON: {exc}") from None
    if not isinstance(doc, dict):
        raise FsimFormatError("a look must be a JSON object")
    unknown = set(doc) - _TOP_LEVEL
    if unknown:
        raise FsimFormatError(f"unknown sections: {sorted(unknown)}")
    if doc.get("format") != FORMAT:
        raise FsimFormatError(f"format must be {FORMAT!r}, got {doc.get('format')!r}")
    version = doc.get("version")
    if isinstance(version, bool) or version != FORMAT_VERSION:
        raise FsimFormatError(f"unsupported version {version!r} (this reader supports {FORMAT_VERSION})")
    if "name" not in doc:
        raise FsimFormatError("a look needs a name")
    color = doc.get("color")
    if not isinstance(color, dict) or set(color) != {"lut"}:
        raise FsimFormatError("color: expected an object with a 'lut'")
    return Look(name=doc["name"], lut=_decode_lut(color["lut"]),
                **{key: doc.get(key, "") for key in _TEXT_FIELDS},
                **{section: doc.get(section, {}) for section in SETTINGS})


def write(look: Look, path: str | Path) -> None:
    Path(path).write_text(dumps(look), encoding="utf-8")


def read(path: str | Path) -> Look:
    path = Path(path)
    if path.stat().st_size > MAX_TEXT_BYTES:
        raise FsimFormatError(f"{path}: larger than {MAX_TEXT_BYTES} bytes")
    try:
        return loads(path.read_text(encoding="utf-8"))
    except FsimFormatError as exc:
        raise FsimFormatError(f"{path}: {exc}") from None


# --- command line ---------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import tifffile

    from .preset import load_preset

    parser = argparse.ArgumentParser(prog="python -m colorlab.fsim", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("from-preset", help="bake a Lightroom preset into a .fsim.json look")
    make.add_argument("preset", type=Path)
    make.add_argument("output", type=Path)
    make.add_argument("--name", help="the look's name (default: the preset's)")
    make.add_argument("--size", type=int, default=DEFAULT_LUT_SIZE, help="LUT points per axis")
    app = sub.add_parser("apply", help="apply a .fsim.json look to an sRGB TIFF")
    app.add_argument("look", type=Path)
    app.add_argument("input", type=Path)
    app.add_argument("output", type=Path)
    app.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        if args.command == "from-preset":
            write(from_preset(load_preset(args.preset), name=args.name, lut_size=args.size), args.output)
        else:
            look = read(args.look)
            img = tifffile.imread(args.input)
            scale = float(np.iinfo(img.dtype).max) if np.issubdtype(img.dtype, np.integer) else 1.0
            out = apply(look, img[..., :3].astype(np.float64) / scale, seed=args.seed)
            tifffile.imwrite(args.output, np.round(np.clip(out, 0.0, 1.0) * 65535.0).astype(np.uint16))
    except (ColorlabError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
