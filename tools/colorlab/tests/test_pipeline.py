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


# --- white balance and exposure (encoded, measured shapes) ---------------------------------------

def test_white_balance_defaults_are_identity():
    np.testing.assert_array_equal(white_balance(GRAYS, 0, 0), GRAYS)


def test_white_balance_keeps_black_and_white_and_each_channel_monotonic():
    # Lightroom on a rendered image: white stays white; each channel is brightened on its own curve.
    for t, m in ((100, 0), (-100, 0), (0, 100), (0, -100), (40, -30)):
        out = white_balance(GRAYS, temperature=t, tint=m)
        np.testing.assert_allclose(out[0], 0, atol=1e-12)
        np.testing.assert_allclose(out[-1], 1, atol=1e-12)
        # cone-space stops mix back into RGB, so allow float-level wiggle where a cone has clipped
        assert all(np.all(np.diff(out[:, c]) >= -1e-5) for c in range(3))


def test_temperature_warms_and_tint_adds_magenta_by_brightening_the_other_channels():
    gray = np.array([0.46, 0.46, 0.46])
    warm = white_balance(gray, temperature=50)
    assert warm[0] > warm[1] > gray[1] and warm[2] == pytest.approx(gray[2], abs=0.01)  # blue kept
    cool = white_balance(gray, temperature=-50)
    assert cool[2] > cool[1] > cool[0] >= gray[0]
    magenta = white_balance(gray, tint=50)
    assert magenta[2] > magenta[0] > gray[0] and magenta[1] == pytest.approx(gray[1], abs=0.01)  # green kept


@pytest.mark.parametrize("settings,expected", [
    # Lightroom bakes of gray 0.46 (sRGB in and out)
    ({"IncrementalTemperature": "+50"}, (0.748, 0.624, 0.427)),
    ({"IncrementalTemperature": "-50"}, (0.334, 0.527, 0.793)),
    ({"IncrementalTint": "+100"}, (0.580, 0.431, 0.811)),
    ({"IncrementalTint": "-100"}, (0.323, 0.737, 0.419)),
])
def test_render_white_balance_matches_lightroom_on_gray(settings, expected):
    preset = parse_preset(make_xmp({"WhiteBalance": "Custom", "IncrementalTemperature": "0",
                                    "IncrementalTint": "0", **settings}))
    # the Bradford stops are fitted to colors and the whole gray ramp, which leaves Temperature -50
    # and Tint -100 about 0.023 off at this one gray; working-space stops fitted on grays alone were 0.014
    np.testing.assert_allclose(render(preset, np.array([[0.46] * 3]))[0], expected, atol=0.03)


def test_exposure_keeps_white_and_rolls_off_highlights_like_lightroom():
    # Lightroom never clips with exposure: white stays white and highlights roll off.
    for ev in (-1.0, 1.0, 3.0):
        y = exposure(GRAYS, ev)[:, 0]
        assert y[0] == pytest.approx(0) and y[-1] == pytest.approx(1) and is_monotonic(y)
        assert np.all(y[:-1] < 1)
    gray = np.array([0.18] * 3)  # encoded values; Lightroom bakes: +1 -> 0.299, -1 -> 0.102
    assert exposure(gray, 1.0)[0] == pytest.approx(0.299, abs=0.02)
    assert exposure(gray, -1.0)[0] == pytest.approx(0.102, abs=0.02)
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


def test_whites_and_blacks_move_the_clip_points_like_lightroom():
    whites = tone(GRAYS, whites=100)[:, 0]
    assert np.all(whites[RAMP >= 0.8] > 1 - 1e-3)  # Lightroom: white from x ~ 0.75
    crushed = tone(GRAYS, blacks=-100)[:, 0]
    assert np.all(crushed[RAMP <= 0.25] < 1e-3)  # Lightroom: black up to x ~ 0.3
    soft = tone(GRAYS, whites=-100)[:, 0]
    assert soft[-1] == pytest.approx(1) and np.interp(0.85, RAMP, soft) < 0.8  # Lightroom: 0.754


def test_blacks_between_baked_amounts_crush_a_range_in_between():
    def last_black(v):
        y = tone(GRAYS, blacks=v)[:, 0]
        return RAMP[np.flatnonzero(y < 1e-3).max()]
    assert last_black(-50) < last_black(-75) < last_black(-100)


# --- parametric curve -----------------------------------------------------------------------------

def test_parametric_curve_defaults_are_identity():
    np.testing.assert_array_equal(parametric_curve(GRAYS), GRAYS)


@pytest.mark.parametrize("region,center", [("shadows", 0.12), ("darks", 0.37), ("lights", 0.62), ("highlights", 0.87)])
def test_each_parametric_region_mostly_moves_its_own_tones(region, center):
    y = parametric_curve(GRAYS, **{region: 100})[:, 0] - RAMP
    peak = RAMP[np.argmax(y)]
    assert abs(peak - center) < 0.15
    assert y.min() > -2e-3  # measured curves carry bake noise of a few thousandths


def test_parametric_regions_are_as_strong_as_lightrooms():
    # Lightroom bakes: Darks +100 lifts 0.35 to 0.56; Lights -100 drops 0.55 to 0.35.
    assert np.interp(0.35, RAMP, parametric_curve(GRAYS, darks=100)[:, 0]) > 0.35 + 0.15
    assert np.interp(0.55, RAMP, parametric_curve(GRAYS, lights=-100)[:, 0]) < 0.55 - 0.15


def test_parametric_splits_move_the_regions():
    low = parametric_curve(GRAYS, darks=100, splits=(10, 30, 75))[:, 0] - RAMP
    high = parametric_curve(GRAYS, darks=100, splits=(40, 60, 85))[:, 0] - RAMP
    assert RAMP[np.argmax(low)] < RAMP[np.argmax(high)]
    assert is_monotonic(low + RAMP) and is_monotonic(high + RAMP)


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


def scene_color(hue_deg, sat=0.6, value=0.5):
    """A working-encoded color whose hexcone hue on the HSL stage's scene scale is ``hue_deg``."""
    import colorsys
    from colorlab.pipeline import K_SCENE
    return unscene(np.array(colorsys.hsv_to_rgb(hue_deg / 360, sat, value)) / K_SCENE)


def scene_hsv(enc):
    import colorsys
    from colorlab.pipeline import K_SCENE
    h, s, v = colorsys.rgb_to_hsv(*(scene(enc) * K_SCENE))
    return h * 360, s, v


def test_hsl_band_centers_are_lightrooms():
    from colorlab.pipeline import HSL_CENTERS
    assert tuple(HSL_CENTERS) == (0, 30, 55, 95, 160, 230, 275, 330)


@pytest.mark.parametrize("band,amount,start,end", [
    ("red", 100, 0, 30),          # toward orange by max(0.9 * 30, 30) degrees
    ("blue", -100, 230, 167),     # toward aqua by 0.9 * 70
    ("green", 100, 95, 153.5),    # toward aqua by 0.9 * 65
    ("magenta", 100, 330, 360),   # toward red, wrapping
])
def test_hsl_hue_moves_a_band_center_by_lightrooms_offset(band, amount, start, end):
    h, s, v = scene_hsv(hsl_adjust(scene_color(start), hue={band: amount}))
    assert h % 360 == pytest.approx(end % 360, abs=0.02)
    assert (s, v) == pytest.approx(scene_hsv(scene_color(start))[1:], abs=1e-9)


def test_hsl_band_values_fall_off_linearly_between_centers():
    at_green = scene_color(95)
    np.testing.assert_allclose(hsl_adjust(at_green, saturation={"yellow": -100}), at_green, atol=1e-12)
    between = scene_color(75)  # halfway from yellow (55) to green (95): half of Yellow -100
    s0, s1 = scene_hsv(between)[1], scene_hsv(hsl_adjust(between, saturation={"yellow": -100}))[1]
    lo, hi = 0.5 * (1 - s0), 0.5  # the scene min and max of ``between``
    mid = (lo + hi) / 2
    expected_gap = (hi - lo) * 0.5  # both ends move half way to the middle
    assert s1 == pytest.approx(expected_gap / (mid + expected_gap / 2), abs=1e-6)


def test_hsl_saturation_minus_100_at_a_band_center_gives_gray():
    h, s, v = scene_hsv(hsl_adjust(scene_color(160), saturation={"aqua": -100}))
    assert s == pytest.approx(0, abs=1e-9)


def test_hsl_luminance_keeps_the_scene_hue_and_moves_the_right_way():
    orange = scene_color(30)
    up, down = (scene_hsv(hsl_adjust(orange, luminance={"orange": a})) for a in (100, -100))
    h0, _, v0 = scene_hsv(orange)
    assert up[0] == pytest.approx(h0, abs=1e-6) and down[0] == pytest.approx(h0, abs=1e-6)
    assert down[2] < v0 < up[2]


def test_scene_scale_keeps_the_hue_of_linear_values():
    import colorsys
    from colorlab.pipeline import WORKING_TRANSFER, _from_scene, _to_scene
    enc = np.random.default_rng(5).random((400, 3))
    hue = lambda rgb: np.array([colorsys.rgb_to_hsv(*c)[0] for c in rgb])
    lin = cs.decode(enc, WORKING_TRANSFER)
    d = (hue(_to_scene(enc)) - hue(lin) + 0.5) % 1.0 - 0.5
    np.testing.assert_allclose(d, 0, atol=1e-9)
    np.testing.assert_allclose(_to_scene(enc), scene(enc), rtol=1e-9)
    np.testing.assert_allclose(_from_scene(_to_scene(enc)), enc, atol=1e-9)


def test_hsl_models_round_trip():
    from colorlab.pipeline import _from_mmh, _to_mmh
    x = np.random.default_rng(3).random((500, 3))
    for smooth in (False, True):
        lo, hi, h, gap = _to_mmh(x, smooth)
        np.testing.assert_allclose(_from_mmh(lo, hi, h, smooth), x, atol=1e-12)


def test_process_version_15_4_uses_spline_tables_and_the_smooth_hue_model():
    def r(settings, pv):
        return render(parse_preset(make_xmp({**settings, "ProcessVersion": pv})), SV_SAMPLES)
    for settings in ({"SaturationAdjustmentYellow": "-100"}, {"Vibrance": "+60"}):
        assert np.abs(r(settings, "15.4") - r(settings, "11.0")).max() > 1e-3, settings
    plain = {"Exposure2012": "+0.5"}
    np.testing.assert_array_equal(r(plain, "15.4"), r(plain, "11.0"))
    every = {f"SaturationAdjustment{b.capitalize()}": "-80" for b in BANDS}
    np.testing.assert_allclose(render(parse_preset(make_xmp({**every, "ProcessVersion": "15.4"})), GRAYS),
                               GRAYS, atol=2e-6)


# Lightroom bakes of the calibration presets (process version 11), sRGB in and out, in gamut
HSL_SAMPLES = np.array([[0.35, 0.4, 0.55], [0.6, 0.5, 0.3], [0.55, 0.45, 0.42], [0.4, 0.55, 0.45]])


@pytest.mark.parametrize("settings,expected", [
    ({"HueAdjustmentBlue": "-100"}, [[0.198, 0.531, 0.537], [0.6, 0.5, 0.3], [0.55, 0.45, 0.42], [0.4, 0.55, 0.45]]),
    ({"SaturationAdjustmentGreen": "-100"}, [[0.35, 0.4, 0.55], [0.6, 0.5, 0.3], [0.55, 0.45, 0.42], [0.464, 0.52, 0.481]]),
    ({"LuminanceAdjustmentOrange": "-100"}, [[0.35, 0.4, 0.55], [0.538, 0.446, 0.26], [0.458, 0.37, 0.344], [0.4, 0.55, 0.45]]),
    ({"HueAdjustmentYellow": "+100"}, [[0.35, 0.4, 0.55], [0.5, 0.546, 0.292], [0.55, 0.45, 0.42], [0.4, 0.55, 0.45]]),
])
def test_render_hsl_matches_lightroom(settings, expected):
    np.testing.assert_allclose(render(parse_preset(make_xmp(settings)), HSL_SAMPLES), expected, atol=0.015)


# Lightroom bakes at saturated colors (sRGB in and out, Lightroom's output inside sRGB): a per-channel
# scene scale bends these colors' hue; Lightroom keeps the hue of the linear values
@pytest.mark.parametrize("settings,rgb,expected,tol", [
    ({"LuminanceAdjustmentBlue": "-100"}, [0.35, 0.2, 0.8], [0.193, 0.022, 0.537], 0.025),
    ({"Saturation": "+50"}, [0.2, 0.3, 0.9], [0.092, 0.269, 0.976], 0.015),
    ({"Saturation": "+50"}, [0.35, 0.2, 0.8], [0.354, 0.123, 0.89], 0.015),
    ({"HueAdjustmentBlue": "-100"}, [0.3, 0.75, 0.8], [0.151, 0.828, 0.735], 0.005),
])
def test_render_matches_lightroom_on_saturated_colors(settings, rgb, expected, tol):
    out = render(parse_preset(make_xmp(settings)), np.array([rgb]))[0]
    np.testing.assert_allclose(out, expected, atol=tol)


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


# Lightroom bakes of the calibration presets (sRGB in and out): grays 0.1, 0.46, 0.8 and one red
GRADE_SAMPLES = np.array([[0.1] * 3, [0.46] * 3, [0.8] * 3, [0.8, 0.3, 0.2]])


@pytest.mark.parametrize("settings,expected", [
    ({"SplitToningShadowHue": "240", "SplitToningShadowSaturation": "50"},
     [[0.073, 0.1, 0.168], [0.412, 0.46, 0.602], [0.791, 0.8, 0.831], [0.79, 0.3, 0.339]]),
    ({"SplitToningHighlightHue": "0", "SplitToningHighlightSaturation": "50"},
     [[0.104, 0.099, 0.099], [0.6, 0.408, 0.432], [1.0, 0.721, 0.758], [0.958, 0.17, 0.193]]),
    ({"ColorGradeMidtoneHue": "120", "ColorGradeMidtoneSat": "50"},
     [[0.098, 0.102, 0.099], [0.319, 0.529, 0.395], [0.765, 0.819, 0.781], [0.722, 0.381, 0.17]]),
    ({"ColorGradeGlobalLum": "+50"},
     [[0.116, 0.116, 0.116], [0.503, 0.503, 0.503], [0.832, 0.832, 0.832], [0.851, 0.342, 0.228]]),
    ({"ColorGradeShadowLum": "+50"},
     [[0.155, 0.155, 0.155], [0.471, 0.471, 0.471], [0.802, 0.802, 0.802], [0.801, 0.326, 0.251]]),
    ({"SplitToningShadowHue": "240", "SplitToningShadowSaturation": "50", "SplitToningBalance": "-50"},
     [[0.073, 0.1, 0.168], [0.401, 0.46, 0.625], [0.781, 0.8, 0.867], [0.79, 0.3, 0.34]]),
    ({"SplitToningShadowHue": "240", "SplitToningShadowSaturation": "50", "ColorGradeBlending": "0"},
     [[0.077, 0.1, 0.162], [0.459, 0.46, 0.463], [0.8, 0.8, 0.8], [0.794, 0.3, 0.296]]),
])
def test_render_color_grading_matches_lightroom(settings, expected):
    np.testing.assert_allclose(render(parse_preset(make_xmp(settings)), GRADE_SAMPLES), expected, atol=0.002)


@pytest.mark.parametrize("kw", [
    {"shadows": Zone(hue=240, sat=50)},
    {"highlights": Zone(hue=0, sat=50)},
    {"midtones": Zone(hue=120, sat=80)},
    {"global_": Zone(hue=60, sat=30)},
    {"shadows": Zone(hue=200, sat=100), "highlights": Zone(hue=30, sat=100), "balance": -80, "blending": 0},
])
def test_color_grade_tints_keep_black_and_white(kw):
    out = color_grade(GRAYS, **kw)
    np.testing.assert_allclose(out[[0, -1]], GRAYS[[0, -1]], atol=1e-9)


@pytest.mark.parametrize("kw", [
    {"shadows": Zone(hue=240, sat=50, lum=100)},
    {"highlights": Zone(hue=0, sat=50, lum=-100)},
    {"midtones": Zone(hue=120, sat=80, lum=30), "global_": Zone(hue=60, sat=30, lum=-50)},
    {"shadows": Zone(hue=200, sat=100, lum=60), "highlights": Zone(hue=30, sat=100), "balance": -80, "blending": 0},
])
def test_color_grade_is_monotonic_per_channel_and_luminance_moves_the_ends_like_lightroom(kw):
    out = color_grade(GRAYS, **kw)
    assert np.all(np.diff(out, axis=0) > 0)
    if kw.get("shadows", Zone()).lum > 0:
        assert np.all(out[0] > 0)  # Lightroom's shadow luminance lifts black
    if kw.get("highlights", Zone()).lum < 0:
        assert np.all(out[-1] < 1)  # and highlight luminance dims white


def test_color_grade_passes_values_above_white_through_monotonically():
    over = np.array([[1.0] * 3, [1.05] * 3, [1.1] * 3])
    out = color_grade(over, shadows=Zone(hue=240, sat=50), midtones=Zone(hue=30, sat=40))
    assert np.all(np.isfinite(out)) and np.all(np.diff(out, axis=0) > 0)


def test_legacy_split_toning_without_blending_renders_as_blending_100():
    # Measured: Lightroom reads an XMP that has SplitToning* keys but no ColorGradeBlending as
    # legacy split toning (Blending 100); presets written with ColorGrade* keys default to 50.
    def r(settings):
        return render(parse_preset(make_xmp(settings)), GRADE_SAMPLES)
    legacy = {"SplitToningShadowHue": "240", "SplitToningShadowSaturation": "50"}
    np.testing.assert_allclose(r(legacy), r({**legacy, "ColorGradeBlending": "100"}), atol=1e-6)
    assert np.abs(r(legacy) - r({**legacy, "ColorGradeBlending": "50"})).max() > 0.01
    modern = {"ColorGradeShadowHue": "240", "ColorGradeShadowSat": "50"}
    np.testing.assert_allclose(r(modern), r({**modern, "ColorGradeBlending": "50"}), atol=1e-6)


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


def test_vibrance_plus_100_boosts_muted_colors_but_barely_moves_vivid_ones():
    # Lightroom bakes: chroma gain ~1.9 at chroma 0.1, ~1.05 at chroma 0.6 and above.
    muted_green = np.array([0.45, 0.55, 0.47])
    assert chroma(saturation_vibrance(muted_green, vibrance=100)) / chroma(muted_green) > 1.5
    assert chroma(saturation_vibrance(RED, vibrance=100)) / chroma(RED) < 1.2


def test_vibrance_protects_skin_tones():
    # Lightroom bakes: muted reds and oranges get a smaller boost than other hues.
    skin, muted_green = MUTED, np.array([0.45, 0.55, 0.47])
    skin_gain = chroma(saturation_vibrance(skin, vibrance=100)) / chroma(skin)
    assert 1.0 < skin_gain < chroma(saturation_vibrance(muted_green, vibrance=100)) / chroma(muted_green)


def test_vibrance_minus_100_keeps_some_color():
    # Lightroom bakes: about a third of a vivid color's chroma survives.
    kept = chroma(saturation_vibrance(RED, vibrance=-100)) / chroma(RED)
    assert 0.2 < kept < 0.5


def scene(enc):
    """Working values on the scene-linear scale: the exposure shoulder's stretch applied the way
    Lightroom's tone curve is, to each color's min and max with the middle channel kept in place,
    so a color's hexcone hue is that of its linear values."""
    from colorlab.pipeline import WORKING_TRANSFER, _response, rgb_tone
    sh = _response().exposure
    lin = cs.decode(np.asarray(enc, dtype=np.float64), WORKING_TRANSFER)
    return rgb_tone(lin, lambda x: sh.stretch(cs.encode(np.maximum(x, 0.0), WORKING_TRANSFER)))


def unscene(x):
    from colorlab.pipeline import WORKING_TRANSFER, _response, rgb_tone
    sh = _response().exposure
    lin = rgb_tone(np.asarray(x, dtype=np.float64), lambda y: cs.decode(sh.rolloff(y), WORKING_TRANSFER))
    return cs.encode(lin, WORKING_TRANSFER)


def test_vibrance_keeps_hue_on_the_scene_linear_scale_and_brightens_drained_blues():
    from colorlab.pipeline import _LUMA
    out = saturation_vibrance(BLUE, vibrance=-100)
    place = lambda x: (x[1] - x.min()) / np.ptp(x)  # the middle channel's place between min and max
    assert place(scene(out)) == pytest.approx(place(scene(BLUE)), abs=1e-9)
    assert out @ _LUMA > BLUE @ _LUMA  # Lightroom brightens blues as vibrance drains them


def test_saturation_keeps_scene_linear_luminance():
    ys = np.array([0.2880859375, 0.7119140625, 0.0])  # the working space's luminance weights
    for sat in (-60, 50):
        out = saturation_vibrance(MUTED, saturation=sat)
        assert scene(out) @ ys == pytest.approx(scene(MUTED) @ ys, rel=1e-9)


# Lightroom bakes of the calibration presets (sRGB in and out) at in-gamut colors
SV_SAMPLES = np.array([[0.55, 0.45, 0.42], [0.6, 0.5, 0.3], [0.35, 0.4, 0.55]])


@pytest.mark.parametrize("settings,expected", [
    ({"Saturation": "-50"}, [[0.513, 0.461, 0.447], [0.561, 0.507, 0.42], [0.377, 0.4, 0.481]]),
    ({"Saturation": "+50"}, [[0.584, 0.439, 0.392], [0.633, 0.495, 0.092], [0.321, 0.4, 0.607]]),
    ({"Vibrance": "-100"}, [[0.411, 0.402, 0.399], [0.416, 0.39, 0.352], [0.38, 0.385, 0.407]]),
    ({"Vibrance": "+50"}, [[0.56, 0.45, 0.417], [0.628, 0.506, 0.224], [0.277, 0.36, 0.569]]),
])
def test_render_saturation_and_vibrance_match_lightroom(settings, expected):
    np.testing.assert_allclose(render(parse_preset(make_xmp(settings)), SV_SAMPLES), expected, atol=0.02)


def test_stages_run_in_lightrooms_order():
    # Lightroom: saturation, then vibrance and HSL (one stage, vibrance first; B&W replaces HSL),
    # all on scene-linear values before the tone curve; then curves, profile table, color grading.
    from colorlab.pipeline import ORDER
    assert ORDER == ("saturation", "hsl", "grayscale", "tone", "parametric", "curves", "look", "grade")


def test_saturation_and_vibrance_leave_neutrals():
    np.testing.assert_allclose(saturation_vibrance(GRAYS, saturation=80, vibrance=80), GRAYS, atol=1e-12)


def test_grayscale_is_neutral_and_mixer_brightens_its_band():
    out = grayscale(np.stack([RED, BLUE, MUTED]))
    assert np.all(chroma(out) < 1e-12)
    np.testing.assert_allclose(grayscale(GRAYS), GRAYS, atol=1e-12)
    assert grayscale(RED, {"red": 60})[0] > grayscale(RED)[0]


BAND_DEGREES = {"red": 0, "orange": 30, "yellow": 55, "green": 95, "aqua": 160, "blue": 230,
                "purple": 275, "magenta": 330}


@pytest.mark.parametrize("band", list(BAND_DEGREES))
def test_neutral_mix_gives_scene_luminance_at_each_band_center(band):
    from colorlab.pipeline import _TINT_LUMA
    color = scene_color(BAND_DEGREES[band], sat=0.7, value=0.6)
    out = grayscale(color)
    np.testing.assert_allclose(out, out[0], atol=1e-12)
    np.testing.assert_allclose(scene(out)[0], scene(color) @ np.array(_TINT_LUMA), rtol=1e-6)


def test_mixer_plus_100_adds_two_and_a_quarter_gaps_and_minus_100_darkens_smoothly():
    from colorlab.pipeline import K_SCENE, _TINT_LUMA
    color = scene_color(30, sat=0.5, value=0.08)  # orange, dim enough not to clip
    x = scene(color) * K_SCENE
    lo, hi = x.min(), x.max()
    lum = x @ np.array(_TINT_LUMA)
    up = scene(grayscale(color, {"orange": 100}))[0] * K_SCENE
    np.testing.assert_allclose(up, lum + 2.25 * (hi - lo), rtol=1e-6)
    darker = [scene(grayscale(color, {"orange": a}))[0] for a in (0, -25, -50, -75, -100)]
    assert all(a > b for a, b in zip(darker, darker[1:])) and darker[-1] >= 0.0
    np.testing.assert_allclose(grayscale(GRAYS, {"orange": -100, "blue": 100}), GRAYS, atol=1e-12)


# Lightroom bakes at saturated colors (sRGB in and out)
@pytest.mark.parametrize("band,rgb,expected,tol", [
    (None, [0.2, 0.3, 0.9], 0.477, 0.015),
    ("Yellow", [0.85, 0.5, 0.1], 0.655, 0.01),
    ("Blue", [0.6, 0.2, 0.7], 0.463, 0.01),
    ("Red", [0.9, 0.25, 0.2], 0.827, 0.015),
])
def test_render_bw_mixer_matches_lightroom(band, rgb, expected, tol):
    settings = {"ConvertToGrayscale": "True", **{f"GrayMixer{b.capitalize()}": "0" for b in BAND_DEGREES}}
    if band:
        settings[f"GrayMixer{band}"] = "+100"
    out = render(parse_preset(make_xmp(settings)), np.array([rgb]))[0]
    np.testing.assert_allclose(out, expected, atol=tol)


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


def test_render_exposure_applies_the_exposure_stage_to_grays():
    gray = np.array([[0.4, 0.4, 0.4]])  # neutral sRGB == neutral working-space encoded value
    out = render(parse_preset(make_xmp({"Exposure2012": "+1.00"})), gray)
    np.testing.assert_allclose(out, exposure(gray, 1.0), atol=1e-5)
    assert out[0, 0] > 0.5


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
    gray = np.array([[0.4, 0.4, 0.4]])
    np.testing.assert_allclose(1.0 - render(preset, gray), exposure(gray, 1.0), atol=1e-4)


def test_render_rejects_non_rgb_input():
    with pytest.raises(ParameterError):
        render(parse_preset(make_xmp()), np.zeros((4, 2)))


def test_render_lut_of_empty_preset_is_identity():
    lut = render_lut(parse_preset(make_xmp()), size=9)
    assert lut.size == 9
    np.testing.assert_allclose(lut.table, Lut3D.identity(9).table, atol=1e-5)


# --- tone curves keep hue (curve on max and min channel, middle keeps its place) -----------------

SATURATED = np.array([[0.9, 0.35, 0.1], [0.15, 0.6, 0.85], [0.7, 0.2, 0.55]])


def _place(rgb):
    """Where the middle channel sits between the min and max channel (the hexcone hue)."""
    s = np.sort(rgb, axis=-1)
    return (s[..., 1] - s[..., 0]) / (s[..., 2] - s[..., 0])


# exposure keeps the hue of the linear values instead (test_exposure_keeps_the_hue_of_linear_values)
@pytest.mark.parametrize("stage", [
    lambda e: tone(e, contrast=60),
    lambda e: tone(e, whites=100, blacks=-50),
    lambda e: parametric_curve(e, darks=80, lights=-60),
    lambda e: apply_curves(e, master=((0, 0), (64, 40), (192, 220), (255, 255))),
])
def test_tone_stages_keep_each_colors_hue(stage):
    out = stage(SATURATED)
    np.testing.assert_allclose(_place(out), _place(SATURATED), atol=1e-9)
    assert np.all(np.argsort(out, axis=-1) == np.argsort(SATURATED, axis=-1))


def test_hue_preserving_tone_still_maps_neutrals_through_the_curve():
    gray = np.full((1, 3), 0.4)
    color = np.array([[0.4, 0.4, 0.4]])
    np.testing.assert_allclose(tone(gray, whites=100), tone(color, whites=100))
    np.testing.assert_allclose(exposure(gray, 1.0)[0, 0], exposure(np.array([[0.4, 0.1, 0.0]]), 1.0)[0, 0])


def test_white_balance_stops_in_cone_space_keep_black_and_white_and_mix_channels_on_colors():
    from colorlab.pipeline import white_balance_stops
    evs = np.array([0.8, 0.6, 0.0])
    ends = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
    np.testing.assert_allclose(white_balance_stops(ends, evs, "bradford"), ends, atol=1e-9)
    color = np.array([[0.2, 0.5, 0.8], [0.7, 0.3, 0.2]])
    np.testing.assert_allclose(white_balance_stops(color, np.zeros(3), "bradford"), color, atol=1e-9)
    assert np.abs(white_balance_stops(color, evs, "bradford") - white_balance_stops(color, evs, "working")).max() > 1e-3
    with pytest.raises(ParameterError, match="space"):
        white_balance_stops(color, evs, "lab")


def test_white_balance_and_exposure_are_gains_on_the_scene_scale():
    from colorlab.pipeline import _response, white_balance_stops
    sh = _response().exposure
    colors = np.random.default_rng(7).uniform(0.02, 0.95, (200, 3))
    for space in ("working", "bradford"):  # equal stops in every channel are exposure
        np.testing.assert_allclose(white_balance_stops(colors, np.full(3, 0.7), space), exposure(colors, 0.7),
                                   atol=1e-7)
    evs = np.array([0.5, 0.0, -0.4])  # working-space stops scale each channel's scene value
    np.testing.assert_allclose(scene(white_balance_stops(colors, evs, "working")),
                               scene(colors) * 2.0 ** (sh.strength * evs), rtol=1e-6)
    ends = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
    np.testing.assert_allclose(white_balance_stops(ends, np.array([0.2, 0.9, 2.5]), "bradford"), ends, atol=1e-9)


def test_exposure_keeps_the_hue_of_linear_values():
    import colorsys
    from colorlab.pipeline import WORKING_TRANSFER
    colors = np.random.default_rng(8).uniform(0.02, 0.9, (200, 3))
    hue = lambda enc: np.array([colorsys.rgb_to_hsv(*c)[0] for c in cs.decode(enc, WORKING_TRANSFER)])
    for ev in (-1.5, 1.0):
        d = (hue(exposure(colors, ev)) - hue(colors) + 0.5) % 1.0 - 0.5
        np.testing.assert_allclose(d, 0, atol=1e-9)


# Lightroom bakes at saturated colors (sRGB in and out, Lightroom's output inside sRGB)
@pytest.mark.parametrize("settings,rgb,expected,tol", [
    ({"WhiteBalance": "Custom", "IncrementalTemperature": "-100", "IncrementalTint": "0"},
     [0.85, 0.5, 0.1], [0.675, 0.713, 0.887], 0.03),
    ({"WhiteBalance": "Custom", "IncrementalTemperature": "+100", "IncrementalTint": "0"},
     [0.15, 0.6, 0.3], [0.657, 0.846, 0.205], 0.02),
    ({"WhiteBalance": "Custom", "IncrementalTemperature": "0", "IncrementalTint": "+100"},
     [0.85, 0.5, 0.1], [0.957, 0.463, 0.451], 0.02),
    ({"Exposure2012": "+2"}, [0.6, 0.2, 0.7], [0.882, 0.633, 0.969], 0.025),
])
def test_render_white_balance_and_exposure_match_lightroom_on_saturated_colors(settings, rgb, expected, tol):
    out = render(parse_preset(make_xmp(settings)), np.array([rgb]))[0]
    np.testing.assert_allclose(out, expected, atol=tol)


def test_per_channel_curves_still_act_per_channel():
    out = apply_curves(SATURATED, red=((0, 0), (128, 180), (255, 255)))
    np.testing.assert_allclose(out[:, 1:], SATURATED[:, 1:])
