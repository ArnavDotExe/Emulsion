"""The colorlab command: everyday preset and look commands, and every module's own tool.

    colorlab render PRESET IN.tif OUT.tif [--global-only] [--seed N] [--profiles DIR]
    colorlab lut    PRESET OUT.cube [--size 33]
    colorlab look   PRESET OUT.fsim.json [--name NAME] [--size 33]
    colorlab apply  LOOK(.fsim.json | .cube) IN.tif OUT.tif [--seed N]
    colorlab info   PRESET
    colorlab bake | calibration | spatial | check | finish | film | local-contrast | fsim ...

Presets are Lightroom ``.xmp`` or mobile ``.dng`` files. A preset whose look is defined
elsewhere (a stubbed look) finds it among the Look presets in its own folder and in the
``--profiles`` folders (for Lightroom's built-in looks, where Camera Raw installs its profiles).
Images are 8- or 16-bit RGB TIFFs, sRGB-encoded; outputs are 16-bit TIFFs.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

import numpy as np

from . import __version__
from .errors import ColorlabError

#: the module tools reachable as ``colorlab <name> ...`` (arguments go to that module's ``main``)
TOOLS = {
    "bake": "colorlab.bake",
    "calibration": "colorlab.calibration",
    "spatial": "colorlab.spatial_calibration",
    "check": "colorlab.preset_check",
    "finish": "colorlab.finish",
    "film": "colorlab.film",
    "local-contrast": "colorlab.local_contrast",
    "fsim": "colorlab.fsim",
}

#: preset keys that describe the preset rather than move a slider
_BOOKKEEPING = {"ProcessVersion", "Version", "UUID", "PresetType", "HasSettings", "Cluster", "Copyright",
                "ContactInfo", "CameraModelRestriction", "AlreadyApplied", "RequiresRGBTables", "Group", "Name"}


def _preset(path: Path, profile_dirs: list[Path]):
    from .preset import load_preset, load_profiles, resolve_looks

    preset = load_preset(path)
    if preset.look is not None and preset.look.source is None:
        [preset] = resolve_looks([preset], load_profiles([path.parent, *profile_dirs]))
    return preset


def _read_image(path: Path) -> np.ndarray:
    from .bake import read_rgb_tiff

    try:
        return read_rgb_tiff(path)
    except (ValueError, TypeError) as exc:  # tifffile's errors for files that aren't TIFFs
        raise ColorlabError(f"{path}: not an 8- or 16-bit RGB TIFF ({exc})") from None


def _write_image(path: Path, img: np.ndarray) -> None:
    from .bake import write_rgb_tiff16

    write_rgb_tiff16(path, np.clip(img, 0.0, 1.0))


def _render(args: argparse.Namespace) -> None:
    from .finish import render_preset
    from .pipeline import render

    preset = _preset(args.preset, args.profiles)
    img = _read_image(args.input)
    out = render(preset, img) if args.global_only else render_preset(preset, img, seed=args.seed)
    _write_image(args.output, out)


def _lut(args: argparse.Namespace) -> None:
    from .lut import write_cube
    from .pipeline import render_lut

    write_cube(render_lut(_preset(args.preset, args.profiles), args.size), args.output)


def _look(args: argparse.Namespace) -> None:
    from . import fsim

    fsim.write(fsim.from_preset(_preset(args.preset, args.profiles), name=args.name, lut_size=args.size), args.output)


def _apply(args: argparse.Namespace) -> None:
    from . import fsim
    from .lut import apply_lut, read_cube

    img = _read_image(args.input)
    name = args.look.name.lower()
    if name.endswith(".cube"):
        out = apply_lut(read_cube(args.look), np.clip(img, 0.0, 1.0)).astype(np.float64)
    elif name.endswith(".json"):
        out = fsim.apply(fsim.read(args.look), img, seed=args.seed)
    else:
        raise ColorlabError(f"{args.look}: a look is a .fsim.json or a .cube file")
    _write_image(args.output, out)


def _info(args: argparse.Namespace) -> None:
    preset = _preset(args.preset, args.profiles)
    print(f"name: {preset.name}")
    if preset.group:
        print(f"group: {preset.group}")
    print(f"process version {preset.settings.get('ProcessVersion', 'unknown')}")
    look = preset.look
    if look is None:
        print("look: none")
    else:
        missing = look.stubbed and look.source is None and look.table is None
        print(f"look: {look.name} at {look.amount:.0%}" + (" (not found: give --profiles)" if missing else ""))
    moved = {k: v for k, v in preset.settings.items() if k not in _BOOKKEEPING and not k.startswith("Supports")}
    print(f"settings ({len(moved)}):")
    for key, value in moved.items():
        print(f"  {key} {value}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="colorlab", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"colorlab {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    def preset_command(name: str, help: str, func) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help)
        p.add_argument("preset", type=Path, help="Lightroom .xmp or mobile .dng preset")
        p.set_defaults(func=func)
        return p

    def profiles(p: argparse.ArgumentParser) -> None:
        p.add_argument("--profiles", type=Path, action="append", default=[], metavar="DIR",
                       help="also look for stubbed looks here (repeatable)")

    r = preset_command("render", "render a preset on an sRGB TIFF", _render)
    r.add_argument("input", type=Path)
    r.add_argument("output", type=Path)
    r.add_argument("--global-only", action="store_true", help="only the global colour, no spatial passes")
    r.add_argument("--seed", type=int, default=0, help="grain seed")
    profiles(r)
    lut = preset_command("lut", "bake a preset's global colour into a .cube LUT", _lut)
    lut.add_argument("output", type=Path)
    lut.add_argument("--size", type=int, default=33, help="LUT points per axis")
    profiles(lut)
    look = preset_command("look", "turn a preset into a .fsim.json look", _look)
    look.add_argument("output", type=Path)
    look.add_argument("--name", help="the look's name (default: the preset's)")
    look.add_argument("--size", type=int, default=33, help="LUT points per axis")
    profiles(look)
    info = preset_command("info", "show a preset's look and the sliders it moves", _info)
    profiles(info)
    app = sub.add_parser("apply", help="apply a .fsim.json look or a .cube LUT to an sRGB TIFF")
    app.add_argument("look", type=Path)
    app.add_argument("input", type=Path)
    app.add_argument("output", type=Path)
    app.add_argument("--seed", type=int, default=0, help="grain seed")
    app.set_defaults(func=_apply)
    for name, module in TOOLS.items():
        sub.add_parser(name, help=f"{module}'s own tool (see: colorlab {name} --help)", add_help=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in TOOLS:
        return importlib.import_module(TOOLS[argv[0]]).main(argv[1:])
    args = _parser().parse_args(argv)
    try:
        args.func(args)
    except (ColorlabError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
