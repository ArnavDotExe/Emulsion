import json
import re

import numpy as np
import pytest
import tifffile

from colorlab.preset import parse_preset
from colorlab.spatial_calibration import (
    CHARTS,
    SCALE_SIZES,
    SCALE_VARIANTS,
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


# --- image-size test: the same charts and settings at other sizes ---------------------------------

def test_charts_scale_with_the_frame():
    small = (SIZE[0] // 2, SIZE[1] // 2)
    imgs, regions = charts(small)
    _, base = charts()
    assert all(img.shape == (*small, 3) for img in imgs.values())
    for chart in ("scene", "levels"):
        for name, reg in base[chart].items():
            assert regions[chart][name]["box"] == [v // 2 for v in reg["box"]], (chart, name)
    assert regions["checker"]["checker"]["cell"] == base["checker"]["checker"]["cell"] // 2
    bars = [r["period"] for r in regions["scene"].values() if r["kind"] == "bars"]
    assert bars == [r["period"] // 2 for r in base["scene"].values() if r["kind"] == "bars"]


def test_scale_variants_repeat_base_settings_at_each_size():
    assert SIZE not in SCALE_SIZES and len(SCALE_SIZES) == 2
    assert {SCALE_SIZES[0][1], SCALE_SIZES[1][1]} == {SIZE[1] // 2, SIZE[1] * 2}
    by_name = {v.name: v for v in SCALE_VARIANTS}
    for h, w in SCALE_SIZES:
        for base in ("Identity Scene", "Identity Levels", "Identity Checker", "Shadows +50", "Clarity +50",
                     "Clarity -50", "Texture +50", "Dehaze +50", "Sharpen 50", "Grain 50", "Vignette -50"):
            v = by_name[f"Spatial {w}x{h} {base}"]
            ref = BY_NAME[f"Spatial {base}"]
            assert v.size == (h, w) and v.chart == ref.chart and dict(v.settings) == dict(ref.settings)
            assert v.baseline == ref.baseline


def test_scale_kit_writes_each_image_at_its_own_size_and_collect_checks_it(tmp_path):
    h, w = SCALE_SIZES[0]
    picked = [v for v in SCALE_VARIANTS if v.size == (h, w)][:4]
    kit = tmp_path / "kit"
    write_spatial_kit(kit, picked)
    manifest = json.loads((kit / "manifest.json").read_text(encoding="utf-8"))
    assert all(e["size"] == [h, w] for e in manifest["entries"])
    assert f"scene {w}x{h}" in manifest["charts"]
    assert read_rgb_tiff(kit / "images" / f"{picked[0].name}.tif").shape == (h, w, 3)
    exports = tmp_path / "exports"
    for v in picked:
        _export(kit, exports, v.name, change=0.02)
    assert collect_spatial(exports, kit, tmp_path / "out").collected == [v.name for v in picked]


def test_cli_kit_scale_test(tmp_path, capsys, monkeypatch):
    import colorlab.spatial_calibration as sc
    small = tuple(v for v in SCALE_VARIANTS if v.size == SCALE_SIZES[0])
    monkeypatch.setattr(sc, "SCALE_VARIANTS", small)
    assert main(["kit", str(tmp_path / "k"), "--scale-test"]) == 0
    assert f"{len(small)} spatial calibration image" in capsys.readouterr().out


# --- follow-up kit -----------------------------------------------------------------------------

def test_flats_chart_has_large_gray_steps_and_bright_saturated_colors():
    imgs, regions = charts()
    flats = regions["flats"]
    grays = [r for r in flats.values() if r["kind"] == "flat"]
    colors = [r for r in flats.values() if r["kind"] == "color"]
    assert len(grays) >= 12 and len(colors) >= 6
    assert min(r["value"][0] for r in grays) < 0.05 and max(r["value"][0] for r in grays) > 0.95
    bright = [r for r in colors if max(r["value"]) >= 0.9 and min(r["value"]) <= 0.25]
    assert len(bright) >= 6
    for r in grays + colors:
        y0, y1, x0, x1 = r["box"]
        assert y1 - y0 >= 200 and x1 - x0 >= 200  # flat insides far from every edge


def test_followup_variants_cover_the_open_spatial_bakes():
    from colorlab.spatial_calibration import FOLLOWUP_VARIANTS
    names = [v.name for v in FOLLOWUP_VARIANTS]
    by = {v.name: v for v in FOLLOWUP_VARIANTS}
    assert len(names) == len({n.lower() for n in names}) and not set(names) & set(BY_NAME)
    assert all(re.fullmatch(r"Spatial [A-Za-z0-9 +\-.]+", n) for n in names)
    assert by["Spatial Identity Flats"].chart == "flats"
    assert by["Spatial Identity Scene PV15"].settings["ProcessVersion"] == "15.4"
    for key, label, amounts in (("Dehaze", "Dehaze", (-100, -50, 50, 100)), ("Clarity2012", "Clarity", (50, 100)),
                                ("Highlights2012", "Highlights", (50, 100))):
        for a in amounts:
            v = by[f"Spatial Flats {label} {a:+g}"]
            assert v.chart == "flats" and float(v.settings[key]) == a
    for name, pair in (("Highlights +50 Clarity +50", {"Highlights2012": "+50", "Clarity2012": "+50"}),
                       ("Highlights +50 Dehaze +50", {"Highlights2012": "+50", "Dehaze": "+50"}),
                       ("Highlights -35 Shadows +30", {"Highlights2012": "-35", "Shadows2012": "+30"}),
                       ("Highlights -50 Shadows +50", {"Highlights2012": "-50", "Shadows2012": "+50"})):
        v = by[f"Spatial {name}"]
        assert v.chart == "scene" and {k: v.settings[k] for k in pair} == pair
    for m in ("25", "100"):
        s = by[f"Spatial Sharpen 50 Masking {m}"].settings
        assert s["Sharpness"] == "50" and s["SharpenEdgeMasking"] == m and "SharpenRadius" in s
    for label, key in (("Highlights", "Highlights2012"), ("Shadows", "Shadows2012")):
        for a in (-100, -50, -25, 25, 50, 100):
            v = by[f"Spatial PV15 {label} {a:+g}"]
            assert v.chart == "scene" and v.settings["ProcessVersion"] == "15.4" and float(v.settings[key]) == a


def test_photo_variants_move_highlights_and_shadows_at_pv_15_4_on_the_photo():
    from colorlab.spatial_calibration import photo_variants
    vs = photo_variants("photo2", (300, 450))
    assert vs[0].name == "Spatial Photo photo2 Identity" and vs[0].baseline
    assert all(v.chart == "photo photo2" and v.size == (300, 450) and v.settings["ProcessVersion"] == "15.4" for v in vs)
    moved = {(k, float(v.settings[k])) for v in vs for k in ("Highlights2012", "Shadows2012") if float(v.settings.get(k, 0))}
    assert {("Highlights2012", a) for a in (-100, -50, -25, 50, 100)} <= moved
    assert {("Shadows2012", a) for a in (-50, 25, 50, 100)} <= moved
    assert any(float(v.settings.get("Highlights2012", 0)) < 0 < float(v.settings.get("Shadows2012", 0)) for v in vs)
    with pytest.raises(ValueError):
        photo_variants("bad/name", (10, 10))


def test_kit_writes_photos_with_their_settings_and_cli_builds_the_followup_kit(tmp_path):
    from colorlab.bake import write_rgb_tiff16
    from colorlab.errors import ColorlabError
    from colorlab.spatial_calibration import FOLLOWUP_VARIANTS, photo_variants
    photo = np.linspace(0, 1, 40 * 60 * 3).reshape(40, 60, 3)
    out = tmp_path / "kit"
    write_spatial_kit(out, photo_variants("p1", photo.shape[:2]), photos={"p1": photo})
    v = photo_variants("p1", photo.shape[:2])[2]
    np.testing.assert_allclose(read_rgb_tiff(out / "images" / f"{v.name}.tif"), photo, atol=1 / 65535)
    entry = json.loads((out / "manifest.json").read_text(encoding="utf-8"))["entries"][2]
    assert entry["chart"] == "photo p1" and entry["size"] == [40, 60]
    with pytest.raises(ColorlabError):
        write_spatial_kit(tmp_path / "k2", photo_variants("p9", (4, 4)))
    with pytest.raises(ColorlabError):
        write_spatial_kit(tmp_path / "k3", photo_variants("p1", (4, 4)), photos={"p1": photo})
    photos = tmp_path / "photos"
    write_rgb_tiff16(photos / "p1.tif", photo)
    assert main(["kit", str(tmp_path / "follow"), "--followup", "--photos", str(photos)]) == 0
    written = {p.stem for p in (tmp_path / "follow" / "images").glob("*.tif")}
    assert written == {v.name for v in FOLLOWUP_VARIANTS} | {v.name for v in photo_variants("p1", (40, 60))}
