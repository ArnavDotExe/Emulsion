import json
import re
from pathlib import Path

import pytest

from colorlab import pipeline
from colorlab.bake import BAKE_PREFIX, write_bake_kit
from colorlab.calibration import CAL_GROUP, K_BOUNDS, VARIANTS, fit, main, variant_xmp, write_calibration_presets
from colorlab.lut import write_cube
from colorlab.pipeline import BANDS, render_lut
from colorlab.preset import parse_preset

BY_NAME = {v.name: v for v in VARIANTS}


def variants_fitting(k):
    return [v for v in VARIANTS if k in v.fits]


# --- the variant set -----------------------------------------------------------------------------

def test_variant_names_are_unique_and_filename_safe():
    names = [v.name for v in VARIANTS]
    assert len(names) == len(set(n.lower() for n in names))
    assert all(re.fullmatch(r"Cal [A-Za-z0-9 +\-.]+", n) for n in names)


def test_every_strength_is_exercised_and_has_bounds():
    ks = {k for k in dir(pipeline) if k.startswith("K_")}
    assert set(K_BOUNDS) == ks
    for k in ks:
        assert variants_fitting(k), k
        lo, hi = K_BOUNDS[k]
        assert 0 <= lo < getattr(pipeline, k) <= hi, k


def test_monotonic_strength_bounds_keep_tone_curves_from_folding():
    assert K_BOUNDS["K_WHITES"][1] < 1 and K_BOUNDS["K_BLACKS"][1] < 1
    assert K_BOUNDS["K_PARAMETRIC"][1] < 0.375 / 3.1416  # narrowest default bump width / pi


def test_variants_cover_each_slider_family():
    keys = {k for v in VARIANTS for k in v.settings}
    for key in ("Exposure2012", "Contrast2012", "Whites2012", "Blacks2012", "IncrementalTemperature",
                "IncrementalTint", "ParametricShadows", "ParametricHighlights", "Saturation", "Vibrance",
                "SplitToningShadowSaturation", "SplitToningHighlightSaturation", "ColorGradeMidtoneSat",
                "ColorGradeGlobalSat", "SplitToningBalance", "ColorGradeBlending", "ConvertToGrayscale"):
        assert key in keys, key
    for band in BANDS:
        for kind in ("Hue", "Saturation", "Luminance"):
            assert f"{kind}Adjustment{band.capitalize()}" in keys
    assert {c for v in VARIANTS for c in v.curves} >= {"ToneCurvePV2012", "ToneCurvePV2012Red", "ToneCurvePV2012Blue"}


def test_grayscale_variants_pin_the_whole_mixer():
    for v in VARIANTS:
        if v.settings.get("ConvertToGrayscale") == "True":
            assert all(f"GrayMixer{b.capitalize()}" in v.settings for b in BANDS), v.name


# --- preset files ------------------------------------------------------------------------------------

def test_variant_xmp_is_a_complete_importable_preset():
    v = BY_NAME["Cal Contrast +50"]
    p = parse_preset(variant_xmp(v))
    assert (p.name, p.group, p.preset_type) == ("Cal Contrast +50", CAL_GROUP, "Normal")
    assert p.number("Contrast2012") == 50
    assert p.text("ProcessVersion") and p.flag("HasSettings")
    assert re.fullmatch(r"[0-9A-F]{32}", p.uuid)


def test_curve_variants_carry_their_curve_and_mark_it_custom():
    v = next(v for v in VARIANTS if "ToneCurvePV2012" in v.curves)
    p = parse_preset(variant_xmp(v))
    assert p.curve("ToneCurvePV2012") == tuple(v.curves["ToneCurvePV2012"])
    assert p.text("ToneCurveName2012") == "Custom"


def test_write_calibration_presets_feeds_the_bake_kit(tmp_path):
    presets = tmp_path / "cal"
    paths = write_calibration_presets(presets)
    assert len(paths) == len(VARIANTS)
    entries = write_bake_kit(presets, tmp_path / "kit", level=2)
    assert sorted(e.preset_name for e in entries) == sorted(v.name for v in VARIANTS)
    assert all(e.bake_name == BAKE_PREFIX + e.preset_name for e in entries)


def test_write_calibration_presets_refuses_non_empty_folder(tmp_path):
    (tmp_path / "x.txt").write_text("x")
    with pytest.raises(FileExistsError):
        write_calibration_presets(tmp_path)


# --- fitting -------------------------------------------------------------------------------------------

def fake_lightroom(tmp_path, variants, **true_strengths):
    """Write .cube 'bakes' rendered by our own pipeline with different strengths."""
    saved = {k: getattr(pipeline, k) for k in true_strengths}
    try:
        for k, v in true_strengths.items():
            setattr(pipeline, k, v)
        for v in variants:
            write_cube(render_lut(parse_preset(variant_xmp(v)), size=9), tmp_path / f"{v.name}.cube")
    finally:
        for k, v in saved.items():
            setattr(pipeline, k, v)
    return tmp_path


def test_fit_recovers_known_strengths(tmp_path):
    variants = variants_fitting("K_CONTRAST") + variants_fitting("K_BLACKS")
    luts = fake_lightroom(tmp_path, variants, K_CONTRAST=0.9, K_BLACKS=0.3)
    report = fit(luts, variants=variants, grid_size=9)
    assert report.constants["K_CONTRAST"] == pytest.approx(0.9, rel=0.02)
    assert report.constants["K_BLACKS"] == pytest.approx(0.3, rel=0.02)
    for v in variants:
        assert report.after[v.name] < 0.05 <= report.before[v.name]


def test_fit_restores_the_pipeline_strengths(tmp_path):
    variants = variants_fitting("K_WHITES")
    before = {k: getattr(pipeline, k) for k in K_BOUNDS}
    fit(fake_lightroom(tmp_path, variants, K_WHITES=0.2), variants=variants, grid_size=9)
    assert {k: getattr(pipeline, k) for k in K_BOUNDS} == before


def test_fit_reports_missing_bakes_and_scores_unfitted_variants(tmp_path):
    exposure = [v for v in VARIANTS if "Exposure2012" in v.settings]
    report = fit(fake_lightroom(tmp_path, exposure), variants=exposure + variants_fitting("K_TINT"), grid_size=9)
    assert set(report.missing) == {v.name for v in variants_fitting("K_TINT")}
    assert report.constants == {}
    assert all(report.after[v.name] < 0.05 for v in exposure)


# --- command line ------------------------------------------------------------------------------------

def test_cli_presets_and_fit(tmp_path, capsys):
    assert main(["presets", str(tmp_path / "cal")]) == 0
    assert f"wrote {len(VARIANTS)} calibration preset" in capsys.readouterr().out
    (tmp_path / "luts").mkdir()
    luts = fake_lightroom(tmp_path / "luts", variants_fitting("K_CONTRAST"), K_CONTRAST=0.8)
    out_json = tmp_path / "fit.json"
    assert main(["fit", str(luts), "--grid", "9", "--json", str(out_json)]) == 0
    text = capsys.readouterr().out
    assert "K_CONTRAST = 0.8" in text and f"missing {len(VARIANTS) - len(variants_fitting('K_CONTRAST'))}" in text
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["constants"]["K_CONTRAST"] == pytest.approx(0.8, rel=0.02)


def test_cli_fit_without_any_bakes_fails(tmp_path, capsys):
    assert main(["fit", str(tmp_path)]) == 1
    assert "no calibration bakes" in capsys.readouterr().err


# --- Lightroom ignored these in the first real bake ----------------------------------------------

CURVE_KEYS = {"ToneCurvePV2012", "ToneCurvePV2012Red", "ToneCurvePV2012Green", "ToneCurvePV2012Blue"}


def test_white_balance_variants_set_temperature_and_tint_together():
    wb = [v for v in VARIANTS if any(k.startswith("Incremental") for k in v.settings)]
    assert wb
    for v in wb:
        assert {"IncrementalTemperature", "IncrementalTint"} <= set(v.settings), v.name


def test_curve_variants_list_all_four_curves():
    curved = [v for v in VARIANTS if v.curves]
    assert curved
    for v in curved:
        assert set(v.curves) == CURVE_KEYS, v.name


def test_cli_presets_only_missing_writes_just_the_unbaked_variants(tmp_path, capsys):
    luts = tmp_path / "luts"
    luts.mkdir()
    done = [v for v in VARIANTS if not v.name.startswith(("Cal Tint", "Cal Curve"))]
    for v in done:
        (luts / f"{v.name}.cube").write_text("", encoding="utf-8")
    out = tmp_path / "redo"
    assert main(["presets", str(out), "--only-missing", str(luts)]) == 0
    written = sorted(p.stem for p in out.glob("*.xmp"))
    assert written == sorted(v.name for v in VARIANTS if v not in done)
    assert f"wrote {len(written)} calibration preset" in capsys.readouterr().out
