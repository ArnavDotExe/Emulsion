"""Whole-preset check kit: full preset copies (spatial settings kept) on the scene chart, and the
comparison of Lightroom's exports with our global look plus spatial passes."""
import json

import numpy as np
import pytest

from colorlab.bake import read_rgb_tiff, write_rgb_tiff16
from colorlab.finish import render_preset
from colorlab.lut import Lut3D, write_cube
from colorlab.preset import load_preset_dir, parse_preset, read_dng_xmp
from colorlab.preset_check import FULL_GROUP, FULL_PREFIX, compare_exports, main, write_preset_kit

from conftest import make_xmp

SIZE = (64, 96)
SPATIAL = {"Exposure2012": "+0.20", "Shadows2012": "+30", "Sharpness": "30", "PostCropVignetteAmount": "-20",
           "GrainAmount": "15", "CropTop": "0.1", "HasCrop": "True"}


def make_presets(root):
    (root / "pack").mkdir(parents=True)
    (root / "pack" / "Grainy.xmp").write_text(make_xmp(SPATIAL).replace("Test Preset", "Grainy"), encoding="utf-8")
    plain = make_xmp({"Saturation": "-20", "UUID": "00000000000000000000000000000002"}).replace("Test Preset", "Plain")
    (root / "pack" / "Plain.xmp").write_text(plain, encoding="utf-8")
    (root / "pack" / "Profile.xmp").write_text(make_xmp({"PresetType": "Look", "UUID": "P"}), encoding="utf-8")
    return root


def test_kit_holds_full_copies_and_charts_with_embedded_sidecars(tmp_path):
    presets, kit = make_presets(tmp_path / "presets"), tmp_path / "kit"
    entries = write_preset_kit(presets, kit, size=SIZE)
    assert [e.kit_name for e in entries] == [f"{FULL_PREFIX}Grainy", f"{FULL_PREFIX}Plain"]  # looks skipped
    copy = parse_preset((kit / "presets" / f"{FULL_PREFIX}Grainy.xmp").read_text(encoding="utf-8"))
    assert copy.number("GrainAmount") == 15 and copy.number("Sharpness") == 30 and copy.number("CropTop") == 0
    tif = kit / "images" / f"{FULL_PREFIX}Grainy.tif"
    embedded = parse_preset(read_dng_xmp(tif))
    assert embedded.number("Shadows2012") == 30 and embedded.flag("HasSettings")
    assert read_rgb_tiff(tif).shape == SIZE + (3,)
    manifest = json.loads((kit / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["chart"] == "scene" and manifest["size"] == list(SIZE)
    assert manifest["entries"][0] == {"kit_name": f"{FULL_PREFIX}Grainy", "preset_name": "Grainy",
                                      "source": "pack/Grainy.xmp"}
    readme = (kit / "README.txt").read_text(encoding="utf-8")
    assert FULL_GROUP in readme and "16 bits" in readme and "compare" in readme
    with pytest.raises(FileExistsError):
        write_preset_kit(presets, kit, size=SIZE)


def _export(kit, exports, name, img):
    exports.mkdir(exist_ok=True)
    write_rgb_tiff16(exports / f"{name}.tif", img)


def test_compare_scores_each_order_and_finds_missing_and_unchanged_exports(tmp_path):
    presets, kit, exports = make_presets(tmp_path / "presets"), tmp_path / "kit", tmp_path / "exports"
    write_preset_kit(presets, kit, size=SIZE)
    by_name = {p.name: p for p in load_preset_dir(presets)}
    chart = read_rgb_tiff(kit / "images" / f"{FULL_PREFIX}Grainy.tif")
    _export(kit, exports, f"{FULL_PREFIX}Grainy", render_preset(by_name["Grainy"], chart, seed=5))
    report = compare_exports(exports, kit, presets, seed=5)
    assert report.missing == [f"{FULL_PREFIX}Plain"] and report.unchanged == []
    (row,) = report.rows
    assert row["name"] == "Grainy" and row["after"] < 0.02 < row["before"]
    assert row["global"] > row["after"] and row["lr_change"] > 0.5
    assert row["after_smooth"] < 0.02 and "lut_after" not in row
    _export(kit, exports, f"{FULL_PREFIX}Plain", chart)  # Lightroom exported it without the preset
    assert compare_exports(exports, kit, presets).unchanged == [f"{FULL_PREFIX}Plain"]


def test_compare_runs_the_spatial_passes_on_lightrooms_own_look_when_given_its_luts(tmp_path):
    presets, kit, exports, luts = (make_presets(tmp_path / "presets"), tmp_path / "kit", tmp_path / "exports",
                                   tmp_path / "luts")
    write_preset_kit(presets, kit, size=SIZE)
    preset = {p.name: p for p in load_preset_dir(presets)}["Grainy"]
    chart = read_rgb_tiff(kit / "images" / f"{FULL_PREFIX}Grainy.tif")
    luts.mkdir()
    write_cube(Lut3D.identity(9), luts / "Grainy.cube")  # Lightroom's global look, here the identity
    _export(kit, exports, f"{FULL_PREFIX}Grainy", render_preset(preset, chart, seed=0, look=lambda x: x))
    (row,) = compare_exports(exports, kit, presets, lut_dir=luts).rows
    assert row["lut_after"] < 0.02 < row["lut_before"] + 0.02 and row["after"] > row["lut_after"]


def test_cli_writes_the_kit_and_a_json_report(tmp_path, capsys):
    presets, kit, exports = make_presets(tmp_path / "presets"), tmp_path / "kit", tmp_path / "exports"
    assert main(["kit", str(presets), str(kit), "--size", "64x96"]) == 0
    chart = read_rgb_tiff(kit / "images" / f"{FULL_PREFIX}Plain.tif")
    preset = {p.name: p for p in load_preset_dir(presets)}["Plain"]
    _export(kit, exports, f"{FULL_PREFIX}Plain", render_preset(preset, chart))
    out = tmp_path / "report.json"
    assert main(["compare", str(exports), str(kit), str(presets), "--json", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [r["name"] for r in data["rows"]] == ["Plain"] and data["missing"] == [f"{FULL_PREFIX}Grainy"]
    printed = capsys.readouterr().out
    assert "Plain" in printed and "median" in printed
    assert main(["compare", str(exports), str(tmp_path / "nope"), str(presets)]) == 1
    assert main(["kit", str(presets), str(kit), "--size", "64by96"]) == 1
