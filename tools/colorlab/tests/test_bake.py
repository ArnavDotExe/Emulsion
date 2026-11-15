import json
import re

import numpy as np
import pytest
import tifffile

from colorlab.bake import (
    NEUTRALIZED_SETTINGS,
    bake_name,
    collect_bakes,
    main,
    make_bake_xmp,
    read_hald_tiff,
    write_bake_kit,
    write_hald_tiff,
)
from colorlab.errors import LutFormatError, PresetFormatError
from colorlab.lut import Lut3D, hald_identity, read_cube
from colorlab.preset import parse_preset, read_dng_xmp

from conftest import curve_xml, make_xmp

SPATIAL = {"GrainAmount": "35", "Highlights2012": "-25", "Shadows2012": "+24", "Clarity2012": "10",
           "Texture": "5", "Dehaze": "3", "PostCropVignetteAmount": "-15", "Sharpness": "40"}


# --- bake preset copies -----------------------------------------------------------------

def test_bake_name_is_prefixed_and_filename_safe():
    assert bake_name("Film N 1") == "FilmSim Bake - Film N 1"
    assert bake_name('a/b\\c:d*e?f"g<h>i|j ') == "FilmSim Bake - a_b_c_d_e_f_g_h_i_j"


def test_bake_copy_neutralizes_spatial_settings_and_keeps_color():
    src = make_xmp({**SPATIAL, "Exposure2012": "+0.50", "Saturation": "-10"})
    baked = parse_preset(make_bake_xmp(src, "FilmSim Bake - X"))
    for key in SPATIAL:
        assert baked.number(key) == 0.0, key
    assert baked.number("Exposure2012") == 0.5
    assert baked.number("Saturation") == -10


def test_neutralized_list_covers_every_spatial_slider():
    assert set(SPATIAL) <= set(NEUTRALIZED_SETTINGS)


def test_bake_copy_resets_geometry_that_would_scramble_a_hald():
    geometry = {"HasCrop": "True", "CropTop": "0.1", "CropRight": "0.9", "CropAngle": "2.5",
                "PerspectiveRotate": "1.5", "PerspectiveScale": "110"}
    src = make_xmp(geometry).replace('rdf:about=""', 'rdf:about="" xmlns:tiff="http://ns.adobe.com/tiff/1.0/" tiff:Orientation="6"')
    out = make_bake_xmp(src, "FilmSim Bake - X")
    baked = parse_preset(out)
    assert baked.flag("HasCrop") is False
    assert (baked.number("CropTop"), baked.number("CropRight"), baked.number("CropAngle")) == (0, 1, 0)
    assert (baked.number("PerspectiveRotate"), baked.number("PerspectiveScale")) == (0, 100)
    assert 'tiff:Orientation="1"' in out


def test_sidecar_style_xmp_without_preset_type_becomes_an_importable_preset():
    src = make_xmp().replace('crs:PresetType="Normal"', "")
    baked = parse_preset(make_bake_xmp(src, "FilmSim Bake - X"))
    assert baked.preset_type == "Normal"
    assert baked.flag("HasSettings")


def test_bake_copy_is_renamed_regrouped_and_gets_a_fresh_uuid():
    src = make_xmp({"UUID": "ABCDEF"})
    baked = parse_preset(make_bake_xmp(src, "FilmSim Bake - X"))
    assert baked.name == "FilmSim Bake - X"
    assert baked.group == "FilmSim Bake"
    assert re.fullmatch(r"[0-9A-F]{32}", baked.uuid) and baked.uuid != "ABCDEF"


def test_bake_copy_preserves_curves_and_look():
    children = curve_xml("ToneCurvePV2012", [(0, 20), (255, 240)]) + (
        '<crs:Look><rdf:Description crs:Name="L" crs:UUID="LOOKID" crs:Amount="0.8" crs:Stubbed="true"/></crs:Look>'
    )
    baked = parse_preset(make_bake_xmp(make_xmp(children=children), "FilmSim Bake - X"))
    assert baked.curve("ToneCurvePV2012") == ((0, 20), (255, 240))
    assert (baked.look.uuid, baked.look.amount) == ("LOOKID", 0.8)


def test_bake_copy_escapes_xml_in_names():
    baked = parse_preset(make_bake_xmp(make_xmp(), "FilmSim Bake - Salt & <Pepper>"))
    assert baked.name == "FilmSim Bake - Salt & <Pepper>"


def test_bake_copy_requires_a_description_and_a_name():
    with pytest.raises(PresetFormatError):
        make_bake_xmp("<x/>", "n")
    no_name = make_xmp().replace("<crs:Name>", "<crs:Nope>").replace("</crs:Name>", "</crs:Nope>")
    with pytest.raises(PresetFormatError, match="Name"):
        make_bake_xmp(no_name, "n")


# --- Hald TIFFs ----------------------------------------------------------------------------

def test_hald_tiff_round_trip_is_identity(tmp_path):
    path = tmp_path / "h.tif"
    write_hald_tiff(path, level=4)
    data = tifffile.imread(path)
    assert data.dtype == np.uint16 and data.shape == (64, 64, 3)
    np.testing.assert_allclose(read_hald_tiff(path).table, Lut3D.identity(16).table, atol=1 / 65535)


def test_hald_tiff_embeds_xmp_settings(tmp_path):
    path = tmp_path / "h.tif"
    write_hald_tiff(path, level=2, xmp=make_xmp({"Exposure2012": "+1.00"}))
    assert parse_preset(read_dng_xmp(path)).number("Exposure2012") == 1.0


def test_read_hald_tiff_accepts_8_bit_and_alpha(tmp_path):
    img = np.round(hald_identity(2) * 255).astype(np.uint8)
    rgba = np.concatenate([img, np.full(img.shape[:2] + (1,), 255, np.uint8)], axis=2)
    path = tmp_path / "h8.tif"
    tifffile.imwrite(path, rgba, photometric="rgb", extrasamples=["unassalpha"])
    np.testing.assert_allclose(read_hald_tiff(path).table, Lut3D.identity(4).table, atol=1 / 255)


def test_read_hald_tiff_rejects_float_and_bad_shapes(tmp_path):
    f = tmp_path / "f.tif"
    tifffile.imwrite(f, hald_identity(2))  # float32
    with pytest.raises(LutFormatError, match="8- or 16-bit"):
        read_hald_tiff(f)
    g = tmp_path / "g.tif"
    tifffile.imwrite(g, np.zeros((10, 10, 3), np.uint16), photometric="rgb")
    with pytest.raises(LutFormatError):
        read_hald_tiff(g)


# --- kit + collection ---------------------------------------------------------------------------

def make_preset_dir(root):
    (root / "pack").mkdir(parents=True)
    (root / "pack" / "Normal One.xmp").write_text(make_xmp({"GrainAmount": "30"}), encoding="utf-8")
    (root / "pack" / "Profile.xmp").write_text(make_xmp({"PresetType": "Look", "UUID": "P"}), encoding="utf-8")
    return root


def test_write_bake_kit_creates_presets_halds_manifest_and_instructions(tmp_path):
    presets = make_preset_dir(tmp_path / "presets")
    kit = tmp_path / "kit"
    entries = write_bake_kit(presets, kit, level=2)
    assert [e.bake_name for e in entries] == ["FilmSim Bake - Test Preset"]  # Look profiles are skipped
    e = entries[0]
    assert (kit / "presets" / f"{e.bake_name}.xmp").is_file()
    tif = kit / "hald" / f"{e.bake_name}.tif"
    assert parse_preset(read_dng_xmp(tif)).number("GrainAmount") == 0.0  # embedded bake settings
    manifest = json.loads((kit / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["level"] == 2
    assert manifest["entries"][0]["source"] == "pack/Normal One.xmp"
    assert "16-bit" in (kit / "README.txt").read_text(encoding="utf-8")


def test_write_bake_kit_deduplicates_names(tmp_path):
    presets = tmp_path / "presets"
    (presets / "a").mkdir(parents=True)
    (presets / "b").mkdir()
    for d in ("a", "b"):
        (presets / d / "p.xmp").write_text(make_xmp(), encoding="utf-8")
    names = [e.bake_name for e in write_bake_kit(presets, tmp_path / "kit", level=2)]
    assert names == ["FilmSim Bake - Test Preset", "FilmSim Bake - Test Preset (2)"]


def test_write_bake_kit_refuses_non_empty_output(tmp_path):
    presets = make_preset_dir(tmp_path / "presets")
    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "stale.txt").write_text("x")
    with pytest.raises(FileExistsError):
        write_bake_kit(presets, kit, level=2)


def test_collect_bakes_converts_exports_and_reports_missing(tmp_path):
    presets = make_preset_dir(tmp_path / "presets")
    (presets / "pack" / "Second.xmp").write_text(
        make_xmp().replace("Test Preset", "Second Preset"), encoding="utf-8"
    )
    kit = tmp_path / "kit"
    write_bake_kit(presets, kit, level=2)
    exports = tmp_path / "exports"
    exports.mkdir()
    inverted = np.round((1.0 - hald_identity(2)) * 65535).astype(np.uint16)
    tifffile.imwrite(exports / "FilmSim Bake - Test Preset.tif", inverted, photometric="rgb")
    out = tmp_path / "luts"
    report = collect_bakes(exports, kit, out)
    assert report.collected == ["FilmSim Bake - Test Preset"]
    assert report.missing == ["FilmSim Bake - Second Preset"]
    lut = read_cube(out / "Test Preset.cube")
    np.testing.assert_allclose(lut.table, 1.0 - Lut3D.identity(4).table, atol=2 / 65535)


def test_collect_bakes_rejects_wrong_level(tmp_path):
    presets = make_preset_dir(tmp_path / "presets")
    kit = tmp_path / "kit"
    write_bake_kit(presets, kit, level=2)
    exports = tmp_path / "exports"
    write_hald_tiff(exports / "FilmSim Bake - Test Preset.tif", level=3)
    with pytest.raises(LutFormatError, match="level"):
        collect_bakes(exports, kit, tmp_path / "luts")


def test_cli_kit_and_collect(tmp_path, capsys):
    presets = make_preset_dir(tmp_path / "presets")
    kit, exports, out = tmp_path / "kit", tmp_path / "exports", tmp_path / "luts"
    assert main(["kit", str(presets), str(kit), "--level", "2"]) == 0
    write_hald_tiff(exports / "FilmSim Bake - Test Preset.tif", level=2)
    assert main(["collect", str(exports), str(kit), str(out)]) == 0
    assert (out / "Test Preset.cube").is_file()
    text = capsys.readouterr().out
    assert "1 bake preset" in text and "collected 1" in text
