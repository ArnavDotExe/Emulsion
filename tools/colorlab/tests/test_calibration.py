import dataclasses
import json
import re
from pathlib import Path

import numpy as np
import pytest

from colorlab import colorspace as cs
from colorlab import pipeline, response
from colorlab.bake import BAKE_PREFIX, write_bake_kit
from colorlab.calibration import (
    CAL_GROUP,
    K_BOUNDS,
    VARIANTS,
    fit,
    main,
    measure_response,
    variant_xmp,
    write_calibration_presets,
)
from colorlab.lut import Lut3D, write_cube
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


def test_tone_and_white_balance_shapes_are_measured_not_fitted_strengths():
    for k in ("K_CONTRAST", "K_WHITES", "K_BLACKS", "K_PARAMETRIC", "K_TEMPERATURE", "K_TINT"):
        assert k not in K_BOUNDS and not hasattr(pipeline, k)


@pytest.mark.parametrize("key,partner", [("IncrementalTemperature", "IncrementalTint"),
                                         ("IncrementalTint", "IncrementalTemperature")])
def test_white_balance_is_baked_at_several_amounts_with_its_partner_at_zero(key, partner):
    found = {float(v.settings[key]) for v in VARIANTS
             if key in v.settings and v.settings.get(partner) == "0" and v.settings[key] != "0"}
    assert found == {-100, -50, -25, 25, 50, 100}


@pytest.mark.parametrize("key,amounts", [
    ("Exposure2012", {-2, -1, -0.5, 0.5, 1, 2}),
    ("Contrast2012", {-100, -50, -25, 25, 50, 100}),
    ("Whites2012", {-100, -50, -25, 25, 50, 100}),
    ("Blacks2012", {-100, -50, -25, 25, 50, 100}),
    ("ParametricDarks", {-100, -50, 50, 100}),
    ("Vibrance", {-100, -50, -25, 25, 50, 100}),
])
def test_measured_sliders_are_baked_at_several_amounts(key, amounts):
    assert {float(v.settings[key]) for v in VARIANTS if set(v.settings) == {key}} == amounts


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
    variants = variants_fitting("K_LUMINANCE") + variants_fitting("K_MIXER")
    luts = fake_lightroom(tmp_path, variants, K_LUMINANCE=0.6, K_MIXER=1.2)
    report = fit(luts, variants=variants, grid_size=9)
    assert report.constants["K_LUMINANCE"] == pytest.approx(0.6, rel=0.02)
    assert report.constants["K_MIXER"] == pytest.approx(1.2, rel=0.02)
    for v in variants:
        assert report.after[v.name] < 0.05 <= report.before[v.name]


def test_fit_restores_the_pipeline_strengths(tmp_path):
    variants = variants_fitting("K_LUMINANCE")
    before = {k: getattr(pipeline, k) for k in K_BOUNDS}
    fit(fake_lightroom(tmp_path, variants, K_LUMINANCE=0.5), variants=variants, grid_size=9)
    assert {k: getattr(pipeline, k) for k in K_BOUNDS} == before


def test_fit_reports_missing_bakes_and_scores_unfitted_variants(tmp_path):
    exposure = [v for v in VARIANTS if "Exposure2012" in v.settings]
    report = fit(fake_lightroom(tmp_path, exposure), variants=exposure + variants_fitting("K_HUE"), grid_size=9)
    assert set(report.missing) == {v.name for v in variants_fitting("K_HUE")}
    assert report.constants == {}
    assert all(report.after[v.name] < 0.05 for v in exposure)


def test_color_grading_is_reconstructed_not_fitted():
    assert not hasattr(pipeline, "K_GRADE_SAT") and not hasattr(pipeline, "K_GRADE_LUM")
    grade = [v for v in VARIANTS if v.name.startswith("Cal Grade")]
    assert len(grade) == 24 and all(not v.fits for v in grade)  # the bakes stay as accuracy checks


# --- command line ------------------------------------------------------------------------------------

def test_cli_presets_and_fit(tmp_path, capsys):
    assert main(["presets", str(tmp_path / "cal")]) == 0
    assert f"wrote {len(VARIANTS)} calibration preset" in capsys.readouterr().out
    (tmp_path / "luts").mkdir()
    luts = fake_lightroom(tmp_path / "luts", variants_fitting("K_LUMINANCE"), K_LUMINANCE=0.55)
    out_json = tmp_path / "fit.json"
    assert main(["fit", str(luts), "--grid", "9", "--json", str(out_json)]) == 0
    text = capsys.readouterr().out
    assert "K_LUMINANCE = 0.55" in text and f"missing {len(VARIANTS) - len(variants_fitting('K_LUMINANCE'))}" in text
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["constants"]["K_LUMINANCE"] == pytest.approx(0.55, rel=0.02)


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


# --- measured responses ----------------------------------------------------------------------------

def solo(key):
    return [v for v in VARIANTS if set(v.settings) == {key}]


def write_neutral_bakes(lut_dir, variants, curve_for):
    """'Bakes' that apply one per-channel curve (a function of the variant) to every color."""
    lut_dir.mkdir(exist_ok=True)
    ident = Lut3D.identity(17).table.astype(np.float64)
    for v in variants:
        write_cube(Lut3D(curve_for(v)(ident)), lut_dir / f"{v.name}.cube")
    return lut_dir


def test_measure_response_reads_tone_curves_from_neutral_ramps(tmp_path):
    whites = solo("Whites2012")
    luts = write_neutral_bakes(tmp_path / "luts", whites,
                               lambda v: (lambda x: x + float(v.settings["Whites2012"]) / 400 * x * (1 - x)))
    report = measure_response(luts)
    fam = report.response.tone["Whites2012"]
    assert set(fam.curves) == {float(v.settings["Whites2012"]) for v in whites}
    np.testing.assert_allclose(fam.curves[100.0], response.X + 0.25 * response.X * (1 - response.X), atol=2e-3)
    assert "Whites2012" in report.measured
    assert "Blacks2012" in report.kept  # no bakes: the shipped curves stay
    np.testing.assert_allclose(report.response.tone["Blacks2012"].curves[100.0],
                               response.load().tone["Blacks2012"].curves[100.0])


def test_measure_response_recovers_the_exposure_shoulder(tmp_path):
    truth = response.ExposureShoulder(shoulder=2.5, strength=0.8)
    luts = write_neutral_bakes(tmp_path / "luts", solo("Exposure2012"),
                               lambda v: (lambda x: truth.apply(x, float(v.settings["Exposure2012"]))))
    got = measure_response(luts).response.exposure
    assert got.shoulder == pytest.approx(2.5, abs=0.15) and got.strength == pytest.approx(0.8, abs=0.02)


def test_measure_response_recovers_white_balance_stops_in_cone_space(tmp_path):
    from colorlab import calibration as cal
    truth = {"IncrementalTemperature": lambda a: np.array([0.016, 0.013, 0.0]) * a,
             "IncrementalTint": lambda a: np.array([0.005, 0.0, 0.01]) * a}
    wb = [v for v in VARIANTS if any(k.startswith("Incremental") and v.settings[k] != "0" for k in v.settings)]

    def curve_for(v):  # per-channel stops in Bradford cone space; bakes are sRGB
        key = next(k for k in truth if v.settings[k] != "0")
        evs = np.abs(truth[key](float(v.settings[key])))
        return lambda x: cal._from_working(pipeline.white_balance_stops(cal._to_working(x), evs, "bradford"))

    got = measure_response(write_neutral_bakes(tmp_path / "luts", wb, curve_for)).response.white_balance
    assert got.space == "bradford"
    np.testing.assert_allclose(got.temperature[50.0], [0.8, 0.65, 0.0], atol=0.05)
    np.testing.assert_allclose(got.tint[-100.0], [0.5, 0.0, 1.0], atol=0.05)



def test_measure_response_recovers_white_balance_hue_preservation_per_amount(tmp_path):
    from colorlab import calibration as cal
    truth = {"IncrementalTemperature": lambda a: np.array([0.016, 0.013, 0.0]) * a,
             "IncrementalTint": lambda a: np.array([0.005, 0.0, 0.01]) * a}
    wb = [v for v in VARIANTS if any(k.startswith("Incremental") and v.settings[k] != "0" for k in v.settings)]

    def curve_for(v):  # hue preservation 0.75 at |amount| 100, 0 at 25
        key = next(k for k in truth if v.settings[k] != "0")
        amount = float(v.settings[key])
        hue = {25.0: 0.0, 50.0: 0.25, 100.0: 0.75}[abs(amount)]
        evs = np.abs(truth[key](amount))
        return lambda x: cal._from_working(pipeline.white_balance_stops(
            cal._to_working(x), evs, "bradford", hue_preservation=hue))

    got = measure_response(write_neutral_bakes(tmp_path / "luts", wb, curve_for)).response.white_balance
    assert got.temperature_hue[100.0] == pytest.approx(0.75, abs=0.13)
    assert got.tint_hue[-100.0] == pytest.approx(0.75, abs=0.13)
    assert got.temperature_hue[25.0] == pytest.approx(0.0, abs=0.13)
    np.testing.assert_allclose(got.temperature[100.0], [1.6, 1.3, 0.0], atol=0.08)
    np.testing.assert_allclose(got.tint[-100.0], [0.5, 0.0, 1.0], atol=0.08)

def test_vibrance_bakes_fit_the_kernel_scale_and_are_not_measured_as_a_table(tmp_path):
    assert {v.name for v in variants_fitting("K_VIBRANCE")} == {v.name for v in solo("Vibrance")}
    luts = tmp_path / "luts"
    luts.mkdir()
    for v in solo("Vibrance"):
        write_cube(render_lut(parse_preset(variant_xmp(v)), size=9), luts / f"{v.name}.cube")
    report = measure_response(luts)
    assert "Vibrance" not in report.measured + report.kept


def test_cli_response_writes_a_loadable_file(tmp_path, capsys):
    luts = write_neutral_bakes(tmp_path / "luts", solo("Contrast2012"), lambda v: (lambda x: x))
    out = tmp_path / "resp.json"
    assert main(["response", str(luts), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "measured: Contrast2012" in text and "kept" in text
    np.testing.assert_allclose(response.load(out).tone["Contrast2012"].curve(100), response.X, atol=2e-3)


def test_cli_response_without_any_bakes_fails(tmp_path, capsys):
    assert main(["response", str(tmp_path), "--out", str(tmp_path / "r.json")]) == 1
    assert "no calibration bakes" in capsys.readouterr().err


# --- regenerating the response must not depend on the file it regenerates -------------------------

def _unreadable(*_args, **_kwargs):
    raise response.ColorlabError("broken response file")


def test_calibration_imports_even_when_the_shipped_response_is_unreadable():
    import subprocess
    import sys

    code = ("import colorlab.response as r\n"
            "def broken(*a, **k):\n    raise r.ColorlabError('broken')\n"
            "r.load = broken\n"
            "import colorlab.calibration\n")
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


def test_pipeline_reads_the_response_on_first_use_and_reports_a_broken_file(monkeypatch):
    monkeypatch.setattr(pipeline, "RESPONSE", None)
    monkeypatch.setattr(response, "load", _unreadable)
    with pytest.raises(response.ColorlabError, match="broken response file"):
        pipeline.exposure(np.full((1, 3), 0.5), 1.0)


def test_measure_response_without_a_readable_base_uses_identity_and_warns(tmp_path, monkeypatch):
    luts = write_neutral_bakes(tmp_path / "luts", solo("Contrast2012"), lambda v: (lambda x: x))
    monkeypatch.setattr(response, "load", _unreadable)
    report = measure_response(luts)
    assert any("base response" in w for w in report.warnings)
    np.testing.assert_allclose(report.response.tone["Whites2012"].curve(100), response.X)
    np.testing.assert_allclose(report.response.white_balance.evs(100, 100), 0.0)


def test_white_balance_bake_without_usable_samples_keeps_the_base_and_warns(tmp_path):
    base = response.load()
    wb = [BY_NAME["Cal Temperature +50"]]
    luts = write_neutral_bakes(tmp_path / "luts", wb, lambda v: (lambda x: np.ones_like(x)))  # all white
    report = measure_response(luts)
    assert any("IncrementalTemperature" in w and "50" in w for w in report.warnings)
    np.testing.assert_allclose(report.response.white_balance.temperature[50.0], base.white_balance.temperature[50.0])


def test_cli_response_prints_warnings(tmp_path, capsys, monkeypatch):
    luts = write_neutral_bakes(tmp_path / "luts", solo("Contrast2012"), lambda v: (lambda x: x))
    monkeypatch.setattr(response, "load", _unreadable)
    assert main(["response", str(luts), "--out", str(tmp_path / "r.json")]) == 0
    assert "warning: " in capsys.readouterr().out
