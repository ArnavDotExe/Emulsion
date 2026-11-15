"""Reference pipeline (global color stages, Lightroom slider order).

Lightroom's exact math is not public, so these tests pin down the properties
every stage must have: identity at defaults, fixed endpoints, monotonic tone
curves, neutrals left neutral, and each slider moving color the right way.
Matching Lightroom numerically is checked separately against private bakes.
"""
import dataclasses

import numpy as np
import pytest

from colorlab import colorspace as cs
from colorlab.adobe_table import Gamma, Gamut, Primaries, RGBTable
from colorlab.errors import ParameterError
from colorlab.lut import Lut3D
from colorlab.pipeline import (
    BANDS,
    Zone,
    apply_curves,
    color_grade,
    exposure,
    grayscale,
    hsl_adjust,
    parametric_curve,
    render,
    render_lut,
    saturation_vibrance,
    spline,
    tone,
    white_balance,
)
from colorlab.preset import LookRef, parse_preset

from conftest import curve_xml, make_xmp

RAMP = np.linspace(0.0, 1.0, 257)
GRAYS = np.repeat(RAMP[:, None], 3, axis=1)
RED, BLUE = np.array([0.8, 0.15, 0.15]), np.array([0.1, 0.2, 0.8])  # RED sits on the red band's center
MUTED = np.array([0.55, 0.45, 0.42])


def chroma(rgb):
    return np.ptp(np.asarray(rgb), axis=-1)


def luma(rgb):
    return np.asarray(rgb) @ np.array([0.2126, 0.7152, 0.0722])


def is_monotonic(y):
    return bool(np.all(np.diff(y) >= -1e-9))


# --- spline ----------------------------------------------------------------------------------

def test_spline_passes_through_control_points():
    pts = ((0, 0), (64, 40), (128, 140), (200, 215), (255, 250))
    f = spline(pts)
    xs = np.array([p[0] for p in pts]) / 255
    np.testing.assert_allclose(f(xs), np.array([p[1] for p in pts]) / 255, atol=1e-12)


@pytest.mark.parametrize("pts", [((0, 0), (255, 255)), ((0, 20), (255, 240)), ((0, 0), (85, 51), (255, 153))])
def test_spline_of_collinear_points_is_a_straight_line(pts):
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    expected = (y0 + (RAMP * 255 - x0) * (y1 - y0) / (x1 - x0)) / 255
    np.testing.assert_allclose(spline(pts)(RAMP), expected, atol=1e-12)


def test_spline_holds_end_values_outside_the_control_range():
    f = spline(((30, 40), (200, 220)))
    assert f(np.array(0.0)) == pytest.approx(40 / 255)
    assert f(np.array(1.0)) == pytest.approx(220 / 255)


def test_spline_is_smooth_and_monotonic_for_a_typical_s_curve():
    y = spline(((0, 0), (64, 50), (192, 210), (255, 255)))(RAMP)
    assert is_monotonic(y)
    assert np.abs(np.diff(y, 2)).max() < 1e-3  # no kinks


@pytest.mark.parametrize("pts", [((0, 0),), ((0, 0), (0, 10)), ((50, 0), (40, 10)), ((0, 0), (300, 255))])
def test_spline_rejects_bad_control_points(pts):
    with pytest.raises(ParameterError):
        spline(pts)


def test_apply_curves_master_hits_every_channel_and_rgb_curves_one_each():
    lift = ((0, 40), (255, 255))
    x = np.array([[0.0, 0.0, 0.0]])
    np.testing.assert_allclose(apply_curves(x, master=lift), [[40 / 255] * 3], atol=1e-12)
    np.testing.assert_allclose(apply_curves(x, red=lift), [[40 / 255, 0, 0]], atol=1e-12)
    np.testing.assert_allclose(apply_curves(x, blue=lift), [[0, 0, 40 / 255]], atol=1e-12)


# --- white balance and exposure (linear) ------------------------------------------------------

def test_white_balance_defaults_are_identity():
    np.testing.assert_array_equal(white_balance(GRAYS, 0, 0), GRAYS)


def test_temperature_warms_and_tint_adds_magenta_while_keeping_brightness():
    gray = np.array([0.18, 0.18, 0.18])
    warm = white_balance(gray, temperature=50, tint=0)
    assert warm[0] > gray[0] and warm[2] < gray[2]
    cool = white_balance(gray, temperature=-50, tint=0)
    assert cool[0] < gray[0] and cool[2] > gray[2]
    magenta = white_balance(gray, temperature=0, tint=50)
    assert magenta[1] < gray[1] and magenta[0] > gray[0]
    y = cs.PROPHOTO_RGB.rgb_to_xyz[1]  # working-space luminance
    for out in (warm, cool, magenta):
        assert out @ y == pytest.approx(gray @ y, rel=1e-9)


def test_exposure_scales_linear_light_by_powers_of_two():
    np.testing.assert_allclose(exposure(GRAYS, 1.0), GRAYS * 2)
    np.testing.assert_allclose(exposure(GRAYS, -2.0), GRAYS / 4)
    np.testing.assert_array_equal(exposure(GRAYS, 0.0), GRAYS)


@pytest.mark.parametrize("call", [
    lambda: exposure(GRAYS, 5.5),
    lambda: white_balance(GRAYS, 101, 0),
    lambda: white_balance(GRAYS, 0, -101),
    lambda: tone(GRAYS, contrast=120),
    lambda: parametric_curve(GRAYS, darks=-101),
    lambda: saturation_vibrance(GRAYS, saturation=200),
    lambda: hsl_adjust(GRAYS, hue={"red": 150}),
    lambda: color_grade(GRAYS, shadows=Zone(hue=400, sat=10)),
    lambda: color_grade(GRAYS, blending=101),
    lambda: grayscale(GRAYS, {"blue": -300}),
])
def test_out_of_range_sliders_are_rejected(call):
    with pytest.raises(ParameterError):
        call()


# --- tone (encoded) ---------------------------------------------------------------------------

def test_tone_defaults_are_identity():
    np.testing.assert_array_equal(tone(GRAYS), GRAYS)


@pytest.mark.parametrize("slider", ["contrast", "whites", "blacks"])
@pytest.mark.parametrize("value", [-100, -40, 40, 100])
def test_tone_sliders_keep_endpoints_and_monotonicity(slider, value):
    y = tone(GRAYS, **{slider: value})[:, 0]
    assert y[0] == pytest.approx(0.0, abs=1e-12) and y[-1] == pytest.approx(1.0, abs=1e-12)
    assert is_monotonic(y)


def test_contrast_darkens_shadows_and_brightens_highlights():
    dark, light = np.array([0.2] * 3), np.array([0.8] * 3)
    assert tone(dark, contrast=50)[0] < 0.2 < tone(dark, contrast=-50)[0]
    assert tone(light, contrast=50)[0] > 0.8 > tone(light, contrast=-50)[0]


def test_blacks_work_low_and_whites_work_high():
    dark, light = np.array([0.1] * 3), np.array([0.9] * 3)
    b = tone(dark, blacks=60)[0] - 0.1, tone(light, blacks=60)[0] - 0.9
    w = tone(dark, whites=60)[0] - 0.1, tone(light, whites=60)[0] - 0.9
    assert b[0] > b[1] > -1e-12
    assert w[1] > w[0] > -1e-12
    assert tone(dark, blacks=-60)[0] < 0.1 and tone(light, whites=-60)[0] < 0.9


# --- parametric curve -----------------------------------------------------------------------------

def test_parametric_curve_defaults_are_identity():
    np.testing.assert_array_equal(parametric_curve(GRAYS), GRAYS)


@pytest.mark.parametrize("region,center", [("shadows", 0.12), ("darks", 0.37), ("lights", 0.62), ("highlights", 0.87)])
def test_each_parametric_region_mostly_moves_its_own_tones(region, center):
    y = parametric_curve(GRAYS, **{region: 100})[:, 0] - RAMP
    peak = RAMP[np.argmax(y)]
    assert abs(peak - center) < 0.15
    assert y.min() > -1e-12


@pytest.mark.parametrize("value", [-100, 100])
def test_parametric_extremes_stay_monotonic_with_fixed_endpoints(value):
    y = parametric_curve(GRAYS, shadows=value, darks=value, lights=value, highlights=value)[:, 0]
    assert is_monotonic(y) and y[0] == pytest.approx(0.0, abs=1e-12) and y[-1] == pytest.approx(1.0, abs=1e-12)


def test_parametric_splits_must_increase():
    with pytest.raises(ParameterError):
        parametric_curve(GRAYS, darks=10, splits=(50, 40, 75))


# --- HSL -------------------------------------------------------------------------------------------

def test_hsl_bands_are_lightrooms_eight():
    assert BANDS == ("red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta")


def test_hsl_never_touches_neutrals():
    every = {b: 100 for b in BANDS}
    np.testing.assert_allclose(hsl_adjust(GRAYS, hue=every, saturation=every, luminance=every), GRAYS, atol=1e-12)


def test_hsl_saturation_targets_one_band():
    out = hsl_adjust(np.stack([RED, BLUE]), saturation={"red": -100})
    assert chroma(out[0]) < 0.05 * chroma(RED)
    np.testing.assert_allclose(out[1], BLUE, atol=1e-12)


def test_hsl_hue_and_luminance_move_the_right_way():
    toward_orange = hsl_adjust(RED, hue={"red": 100})
    assert toward_orange[1] > RED[1]
    brighter_blue = hsl_adjust(BLUE, luminance={"blue": 60})
    assert luma(brighter_blue) > luma(BLUE)


def test_hsl_rejects_unknown_band():
    with pytest.raises(ParameterError, match="teal"):
        hsl_adjust(RED, saturation={"teal": 10})


# --- color grading ---------------------------------------------------------------------------------

def test_color_grade_defaults_are_identity():
    np.testing.assert_array_equal(color_grade(GRAYS), GRAYS)


def test_zero_saturation_zone_with_any_hue_is_identity():
    np.testing.assert_allclose(color_grade(GRAYS, shadows=Zone(hue=200, sat=0)), GRAYS, atol=1e-12)


def test_shadow_tint_colors_darks_more_than_lights():
    dark, light = np.array([0.15] * 3), np.array([0.9] * 3)
    blue = Zone(hue=240, sat=50)
    d, li = color_grade(dark, shadows=blue), color_grade(light, shadows=blue)
    assert d[2] > d[0] and chroma(d) > chroma(li)


def test_highlight_tint_colors_lights_more_than_darks():
    dark, light = np.array([0.15] * 3), np.array([0.9] * 3)
    orange = Zone(hue=30, sat=50)
    li = color_grade(light, highlights=orange)
    assert li[0] > li[2] and chroma(li) > chroma(color_grade(dark, highlights=orange))


def test_global_tint_reaches_every_tone_and_zone_luminance_brightens():
    mid = np.array([0.5] * 3)
    assert chroma(color_grade(mid, global_=Zone(hue=120, sat=40))) > 0.01
    assert color_grade(mid, midtones=Zone(lum=50))[0] > 0.5


def test_balance_moves_the_shadow_highlight_split():
    mid = np.array([0.5] * 3)
    tint = Zone(hue=240, sat=60)
    assert chroma(color_grade(mid, shadows=tint, balance=-100)) > chroma(color_grade(mid, shadows=tint, balance=100))


# --- saturation, vibrance, grayscale ----------------------------------------------------------------

def test_saturation_minus_100_gives_gray_and_plus_increases_chroma():
    gray = saturation_vibrance(RED, saturation=-100)
    assert chroma(gray) < 1e-12
    assert chroma(saturation_vibrance(RED, saturation=40)) > chroma(RED)


def test_vibrance_boosts_muted_colors_relatively_more():
    muted_gain = chroma(saturation_vibrance(MUTED, vibrance=50)) / chroma(MUTED)
    vivid_gain = chroma(saturation_vibrance(RED, vibrance=50)) / chroma(RED)
    assert muted_gain > vivid_gain > 1.0


def test_saturation_and_vibrance_leave_neutrals():
    np.testing.assert_allclose(saturation_vibrance(GRAYS, saturation=80, vibrance=80), GRAYS, atol=1e-12)


def test_grayscale_is_neutral_and_mixer_brightens_its_band():
    out = grayscale(np.stack([RED, BLUE, MUTED]))
    assert np.all(chroma(out) < 1e-12)
    np.testing.assert_allclose(grayscale(GRAYS), GRAYS, atol=1e-12)
    assert grayscale(RED, {"red": 60})[0] > grayscale(RED)[0]


# --- render ------------------------------------------------------------------------------------------

def grid(n=9):
    return Lut3D.identity(n).table.reshape(-1, 3).astype(np.float64)


def test_render_of_an_empty_preset_is_identity():
    out = render(parse_preset(make_xmp()), grid())
    assert out.dtype == np.float32 and out.shape == grid().shape
    np.testing.assert_allclose(out, grid(), atol=1e-5)


def test_render_keeps_image_shape():
    img = np.random.default_rng(0).random((4, 5, 3))
    assert render(parse_preset(make_xmp({"Exposure2012": "+0.5"})), img).shape == (4, 5, 3)


def test_render_exposure_doubles_linear_gray():
    gray = cs.encode(np.array([[0.2, 0.2, 0.2]]), cs.Transfer.SRGB)
    out = render(parse_preset(make_xmp({"Exposure2012": "+1.00"})), gray)
    np.testing.assert_allclose(cs.decode(out.astype(np.float64), cs.Transfer.SRGB), [[0.4] * 3], atol=1e-5)


def test_render_applies_point_curve_in_the_encoded_domain():
    preset = parse_preset(make_xmp(children=curve_xml("ToneCurvePV2012", [(0, 40), (255, 255)])))
    np.testing.assert_allclose(render(preset, np.zeros((1, 3))), [[40 / 255] * 3], atol=1e-5)


def test_render_grayscale_preset_is_neutral():
    out = render(parse_preset(make_xmp({"ConvertToGrayscale": "True"})), grid())
    assert np.all(chroma(out) < 1e-5)


def inverting_look(amount, extra_settings=None):
    lut = Lut3D(1.0 - Lut3D.identity(5).table)
    table = RGBTable(lut, Primaries.SRGB, Gamma.SRGB, Gamut.CLIP, 0.0, 2.0)
    profile = parse_preset(make_xmp({"PresetType": "Look", **(extra_settings or {})}))
    look = LookRef(name="Inverse", uuid="L", amount=amount, stubbed=True, table=table, source=profile)
    return dataclasses.replace(parse_preset(make_xmp()), look=look)


def test_render_applies_the_look_table_with_its_amount():
    x = grid(5)
    np.testing.assert_allclose(render(inverting_look(1.0), x), 1.0 - x, atol=1e-4)
    np.testing.assert_allclose(render(inverting_look(0.5), x), np.full_like(x, 0.5), atol=1e-4)


def test_render_applies_the_look_profiles_own_settings():
    preset = inverting_look(1.0, {"Exposure2012": "+1.00"})
    gray = cs.encode(np.array([[0.2, 0.2, 0.2]]), cs.Transfer.SRGB)
    lin = cs.decode(1.0 - render(preset, gray).astype(np.float64), cs.Transfer.SRGB)
    np.testing.assert_allclose(lin, [[0.4] * 3], atol=1e-4)


def test_render_rejects_non_rgb_input():
    with pytest.raises(ParameterError):
        render(parse_preset(make_xmp()), np.zeros((4, 2)))


def test_render_lut_of_empty_preset_is_identity():
    lut = render_lut(parse_preset(make_xmp()), size=9)
    assert lut.size == 9
    np.testing.assert_allclose(lut.table, Lut3D.identity(9).table, atol=1e-5)
