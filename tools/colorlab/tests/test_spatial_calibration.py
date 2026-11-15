import json
import re

import numpy as np
import pytest
import tifffile

from colorlab.preset import parse_preset
from colorlab.spatial_calibration import (
    CHARTS,
    SIZE,
    SPATIAL_GROUP,
    SPATIAL_VARIANTS,
    charts,
    collect_spatial,
    main,
    read_rgb_tiff,
    write_spatial_kit,
)

BY_NAME = {v.name: v for v in SPATIAL_VARIANTS}


# --- charts --------------------------------------------------------------------------------------

def test_every_chart_is_a_full_frame_image_in_range():
    imgs, regions = charts()
    assert set(imgs) == set(CHARTS) == set(regions)
    for name, img in imgs.items():
        assert img.shape == (*SIZE, 3), name
        assert img.min() >= 0.0 and img.max() <= 1.0, name


def test_regions_lie_inside_the_frame_and_flat_regions_are_uniform():
    imgs, regions = charts()
    for chart, regs in regions.items():
        assert regs, chart
        for name, reg in regs.items():
            y0, y1, x0, x1 = reg["box"]
            assert 0 <= y0 < y1 <= SIZE[0] and 0 <= x0 < x1 <= SIZE[1], (chart, name)
            if reg["kind"] == "flat":
                patch = imgs[chart][y0:y1, x0:x1]
                np.testing.assert_allclose(patch, np.broadcast_to(reg["value"], patch.shape), atol=1e-12,
                                           err_msg=f"{chart}/{name}")


def test_scene_has_flat_levels_edges_ramp_and_colors():
    _, regions = charts()
    kinds = {r["kind"] for r in regions["scene"].values()}
    assert {"flat", "bars", "ramp", "color"} <= kinds
    levels = sorted(r["value"][0] for r in regions["scene"].values() if r["kind"] == "flat")
    assert len(levels) >= 8 and levels[0] < 0.05 and levels[-1] > 0.9


def test_levels_chart_has_flat_bands_and_noise_for_noise_reduction():
    imgs, regions = charts()
    kinds = [r["kind"] for r in regions["levels"].values()]
    assert kinds.count("flat") >= 6 and "noise" in kinds and "color_noise" in kinds
    noise = next(r for r in regions["levels"].values() if r["kind"] == "noise")
    y0, y1, x0, x1 = noise["box"]
    patch = imgs["levels"][y0:y1, x0:x1]
    assert 0.03 < patch.std() < 0.08
    np.testing.assert_array_equal(patch, charts()[0]["levels"][y0:y1, x0:x1])  # deterministic


def test_checker_puts_every_level_at_every_distance_from_the_centre():
    imgs, regions = charts()
    img = imgs["checker"][..., 0]
    levels = regions["checker"]["checker"]["levels"]
    assert len(levels) == 4
    h, w = SIZE
    corner, centre = img[:128, :128], img[h // 2 - 64:h // 2 + 64, w // 2 - 64:w // 2 + 64]
    for patch in (corner, centre):
        assert set(np.round(np.unique(patch), 6)) == set(np.round(levels, 6))


# --- variants ------------------------------------------------------------------------------------

def test_variant_names_are_unique_and_filename_safe():
    names = [v.name for v in SPATIAL_VARIANTS]
    assert len(names) == len({n.lower() for n in names})
    assert all(re.fullmatch(r"Spatial [A-Za-z0-9 +\-.]+", n) for n in names)


def test_every_chart_has_an_identity_baseline():
    for chart in CHARTS:
        v = BY_NAME[f"Spatial Identity {chart.capitalize()}"]
        assert v.chart == chart
        assert all(float(val) == 0 for k, val in v.settings.items() if k in ("Sharpness", "GrainAmount",
                   "PostCropVignetteAmount", "LuminanceSmoothing", "ColorNoiseReduction"))


@pytest.mark.parametrize("key,amounts", [
    ("Highlights2012", (-100, -50, -25, 25, 50, 100)),
    ("Shadows2012", (-100, -50, -25, 25, 50, 100)),
    ("Clarity2012", (-100, -50, -25, 25, 50, 100)),
    ("Texture", (-100, -50, -25, 25, 50, 100)),
    ("Dehaze", (-100, -50, -25, 25, 50, 100)),
    ("Sharpness", (25, 50, 100)),
    ("GrainAmount", (10, 25, 50, 100)),
    ("PostCropVignetteAmount", (-100, -50, -25, 25, 50, 100)),
    ("LuminanceSmoothing", (25, 50, 100)),
    ("ColorNoiseReduction", (25, 50, 100)),
])
def test_each_spatial_slider_is_swept(key, amounts):
    swept = {float(v.settings[key]) for v in SPATIAL_VARIANTS
             if float(v.settings.get(key, 0)) != 0 and not v.name.startswith("Spatial Identity")}
    assert set(map(float, amounts)) <= swept


def test_local_tone_variants_render_the_scene_and_grain_the_levels_and_vignette_the_checker():
    for v in SPATIAL_VARIANTS:
        if any(float(v.settings.get(k, 0)) for k in ("Highlights2012", "Shadows2012", "Clarity2012",
                                                     "Texture", "Dehaze", "Sharpness")):
            assert v.chart == "scene", v.name
        if any(float(v.settings.get(k, 0)) for k in ("GrainAmount", "LuminanceSmoothing", "ColorNoiseReduction")):
            assert v.chart == "levels", v.name
        if float(v.settings.get("PostCropVignetteAmount", 0)):
            assert v.chart == "checker", v.name


def test_grain_vignette_and_sharpening_variants_set_their_whole_family():
    # Lightroom may ignore a lone member of a slider family (as it does for temperature/tint).
    grain = ("GrainAmount", "GrainSize", "GrainFrequency")
    vignette = ("PostCropVignetteAmount", "PostCropVignetteMidpoint", "PostCropVignetteFeather",
                "PostCropVignetteRoundness", "PostCropVignetteStyle", "PostCropVignetteHighlightContrast")
    sharpen = ("Sharpness", "SharpenRadius", "SharpenDetail", "SharpenEdgeMasking")
    for v in SPATIAL_VARIANTS:
        for family in (grain, vignette, sharpen):
            if float(v.settings.get(family[0], 0)):
                assert set(family) <= set(v.settings), v.name


def test_vignette_shape_and_grain_texture_are_swept_at_a_fixed_amount():
    for name in ("Spatial Vignette -50 Feather 0", "Spatial Vignette -50 Feather 100",
                 "Spatial Vignette -50 Midpoint 0", "Spatial Vignette -50 Midpoint 100",
                 "Spatial Vignette -50 Roundness -100", "Spatial Vignette -50 Roundness +100",
                 "Spatial Vignette -50 Style 2", "Spatial Vignette -50 Style 3",
                 "Spatial Vignette -50 Highlights 100",
                 "Spatial Grain 50 Size 10", "Spatial Grain 50 Size 100",
                 "Spatial Grain 50 Roughness 0", "Spatial Grain 50 Roughness 100"):
        assert name in BY_NAME, name


# --- kit -----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def kit(tmp_path_factory):
    out = tmp_path_factory.mktemp("spatial") / "kit"
    write_spatial_kit(out)
    return out


def test_kit_writes_a_preset_an_image_and_a_manifest_entry_per_variant(kit):
    manifest = json.loads((kit / "manifest.json").read_text(encoding="utf-8"))
    assert [e["name"] for e in manifest["entries"]] == [v.name for v in SPATIAL_VARIANTS]
    assert manifest["size"] == list(SIZE)
    assert set(manifest["charts"]) == set(CHARTS)
    assert (kit / "README.txt").is_file()
    assert len(list((kit / "images").glob("*.tif"))) == len(SPATIAL_VARIANTS)
    for v in SPATIAL_VARIANTS[:3]:
        p = parse_preset((kit / "presets" / f"{v.name}.xmp").read_text(encoding="utf-8"))
        assert p.name == v.name and p.group == SPATIAL_GROUP
        assert {k: p.settings[k] for k in v.settings} == dict(v.settings)


def test_kit_images_are_16_bit_charts_carrying_their_settings_as_a_sidecar(kit):
    imgs, _ = charts()
    v = BY_NAME["Spatial Clarity +50"]
    with tifffile.TiffFile(kit / "images" / f"{v.name}.tif") as tif:
        page = tif.pages[0]
        data = page.asarray()
        xmp = page.tags[700].value
    xmp = xmp.decode("utf-8") if isinstance(xmp, bytes) else xmp
    assert data.dtype == np.uint16 and data.shape == (*SIZE, 3)
    np.testing.assert_allclose(data / 65535.0, imgs[v.chart], atol=1 / 65535)
    assert 'crs:AlreadyApplied="False"' in xmp and 'crs:Clarity2012="+50"' in xmp


def test_kit_refuses_a_non_empty_folder(tmp_path):
    (tmp_path / "x").write_text("x")
    with pytest.raises(FileExistsError):
        write_spatial_kit(tmp_path)


# --- collect -------------------------------------------------------------------------------------

def _export(kit, exports, name, change=0.0, size=None):
    data = read_rgb_tiff(kit / "images" / f"{name}.tif")
    if size is not None:
        data = data[: size[0], : size[1]]
    data = np.clip(data + change * np.sin(np.arange(data.shape[1]) / 3.0)[None, :, None], 0, 1)
    exports.mkdir(exist_ok=True)
    tifffile.imwrite(exports / f"{name}.tif", np.round(data * 65535).astype(np.uint16), photometric="rgb")


def test_collect_copies_edited_exports_and_reports_missing_and_unchanged(kit, tmp_path):
    exports = tmp_path / "exports"
    _export(kit, exports, "Spatial Identity Scene")  # identity: unchanged is expected
    _export(kit, exports, "Spatial Clarity +50", change=0.02)
    _export(kit, exports, "Spatial Clarity -50")  # preset not applied
    report = collect_spatial(exports, kit, tmp_path / "out")
    assert report.collected == ["Spatial Identity Scene", "Spatial Clarity +50"]
    assert report.unchanged == ["Spatial Clarity -50"]
    assert len(report.missing) == len(SPATIAL_VARIANTS) - 3
    got = read_rgb_tiff(tmp_path / "out" / "Spatial Clarity +50.tif")
    assert got.shape == (*SIZE, 3)
    assert json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))["charts"]


def test_collect_rejects_a_resized_export(kit, tmp_path):
    exports = tmp_path / "exports"
    _export(kit, exports, "Spatial Clarity +50", change=0.02, size=(512, 768))
    with pytest.raises(Exception, match="1536x1024"):
        collect_spatial(exports, kit, tmp_path / "out")


def test_read_rgb_tiff_drops_alpha_and_accepts_8_bit(tmp_path):
    rgba = np.zeros((4, 4, 4), np.uint8)
    rgba[..., 0] = 255
    tifffile.imwrite(tmp_path / "a.tif", rgba, photometric="rgb")
    got = read_rgb_tiff(tmp_path / "a.tif")
    assert got.shape == (4, 4, 3) and got[..., 0].min() == 1.0
    tifffile.imwrite(tmp_path / "f.tif", np.zeros((4, 4, 3), np.float32), photometric="rgb")
    with pytest.raises(Exception, match="8- or 16-bit"):
        read_rgb_tiff(tmp_path / "f.tif")


# --- command line --------------------------------------------------------------------------------

def test_cli_kit_and_collect(tmp_path, capsys, kit):
    assert main(["kit", str(tmp_path / "k")]) == 0
    assert f"{len(SPATIAL_VARIANTS)} spatial calibration image" in capsys.readouterr().out
    exports = tmp_path / "exports"
    _export(kit, exports, "Spatial Grain 50", change=0.02)
    assert main(["collect", str(exports), str(kit), str(tmp_path / "out")]) == 0
    text = capsys.readouterr().out
    assert "collected 1" in text and "missing" in text


def test_cli_collect_errors_exit_1(tmp_path, capsys):
    assert main(["collect", str(tmp_path), str(tmp_path / "no-kit"), str(tmp_path / "out")]) == 1
    assert "error:" in capsys.readouterr().err
