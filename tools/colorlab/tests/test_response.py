"""Measured slider responses: the shapes the pipeline takes from Lightroom bakes.

Tone sliders are tables of neutral curves at the amounts that were baked; between
them the clip points move linearly and the shape in between is blended.
Exposure is a two-parameter shoulder. Vibrance is a chroma-gain table around a
pivot between each color's min and max channel.
"""
import json

import numpy as np
import pytest

from colorlab.errors import ColorlabError
from colorlab.response import (
    SAMPLES,
    TONE_KEYS,
    X,
    CurveFamily,
    ExposureShoulder,
    Response,
    VibranceResponse,
    load,
)

RAMP = np.linspace(0.0, 1.0, 257)


def is_monotonic(y):
    return bool(np.all(np.diff(y) >= -1e-9))


def clip_at_half():
    return CurveFamily({100.0: np.minimum(2 * X, 1.0)})


# --- tone curve families --------------------------------------------------------------------------

def test_samples_are_an_even_ramp_including_both_ends():
    assert SAMPLES == len(X) and X[0] == 0.0 and X[-1] == 1.0
    np.testing.assert_allclose(np.diff(X), 1 / (SAMPLES - 1))


def test_curve_family_is_identity_at_zero_and_exact_at_measured_amounts():
    fam = clip_at_half()
    np.testing.assert_allclose(fam.curve(0), X, atol=1e-12)
    np.testing.assert_allclose(fam.curve(100), np.minimum(2 * X, 1.0), atol=1e-9)
    np.testing.assert_array_equal(fam.apply(RAMP, 0), RAMP)


def test_curve_family_moves_clip_points_between_amounts():
    y = clip_at_half().curve(50)
    first_white = X[np.argmax(y >= 1 - 1e-9)]
    assert 0.7 < first_white < 0.8  # halfway between 1 (identity) and 0.5; a plain blend never clips
    assert is_monotonic(y) and y[0] == pytest.approx(0) and y[-1] == pytest.approx(1)
    crush = CurveFamily({-50.0: np.clip((X - 0.1) / 0.9, 0, 1), -100.0: np.clip((X - 0.3) / 0.7, 0, 1)})
    last_black = X[np.flatnonzero(crush.curve(-75) <= 1e-9).max()]
    assert 0.17 < last_black < 0.23


def test_curve_family_clamps_to_its_measured_range():
    fam = CurveFamily({50.0: X ** 0.8})
    np.testing.assert_allclose(fam.curve(100), fam.curve(50))
    np.testing.assert_allclose(fam.curve(-30), X)  # nothing measured below 0: identity


def test_curve_family_stays_monotonic_between_amounts():
    rng = np.random.default_rng(1)
    curves = {}
    for a in (-100.0, -50.0, 50.0, 100.0):
        y = np.cumsum(rng.random(SAMPLES) ** 3)
        curves[a] = (y - y[0]) / (y[-1] - y[0])
    fam = CurveFamily(curves)
    for a in np.linspace(-100, 100, 23):
        assert is_monotonic(fam.curve(a)), a


def test_curve_family_applies_per_value():
    fam = CurveFamily({100.0: X ** 0.5})
    rgb = np.array([[0.25, 0.5, 1.0]])
    np.testing.assert_allclose(fam.apply(rgb, 100), np.sqrt(rgb), atol=2e-3)


def test_curve_family_rejects_bad_tables():
    with pytest.raises(ColorlabError):
        CurveFamily({100.0: np.linspace(0, 1, 10)})
    with pytest.raises(ColorlabError):
        CurveFamily({0.0: X})


# --- exposure ----------------------------------------------------------------------------------------

def test_exposure_shoulder_keeps_black_and_white_and_never_clips():
    shoulder = ExposureShoulder(shoulder=3.6, strength=0.9)
    for ev in (-3, -1, 1, 3):
        y = shoulder.apply(RAMP, ev)
        assert y[0] == pytest.approx(0) and y[-1] == pytest.approx(1) and is_monotonic(y)
        assert np.all(y[:-1] < 1)
    assert np.all(shoulder.apply(RAMP[1:-1], 1) > RAMP[1:-1])
    np.testing.assert_array_equal(shoulder.apply(RAMP, 0), RAMP)


def test_exposure_steps_undo_each_other():
    shoulder = ExposureShoulder(shoulder=2.0, strength=1.1)
    np.testing.assert_allclose(shoulder.apply(shoulder.apply(RAMP, 1.5), -1.5), RAMP, atol=1e-9)


# --- vibrance ------------------------------------------------------------------------------------

def vib():
    chroma = (0.1, 0.5)
    return VibranceResponse(
        chroma=chroma, pivots=(0.6, 0.75),
        gains={-100.0: np.array([0.2, 0.4]), 100.0: np.array([1.8, 1.1])},
        bands={-100.0: np.ones(8), 100.0: np.array([0.5, 1, 1, 1, 1, 1, 1, 1.0])},
    )


def test_vibrance_gain_is_one_at_zero_and_follows_chroma():
    v = vib()
    c = np.array([0.1, 0.3, 0.5, 0.9])
    green = np.zeros((4, 8))
    green[:, 3] = 1  # green band only, factor 1
    np.testing.assert_allclose(v.gain(c, green, 0), 1)
    np.testing.assert_allclose(v.gain(c, green, 100), [1.8, 1.45, 1.1, 1.1])
    np.testing.assert_allclose(v.gain(c, green, 50), [1.4, 1.225, 1.05, 1.05])
    np.testing.assert_allclose(v.gain(c, green, -100), [0.2, 0.3, 0.4, 0.4])


def test_vibrance_band_factors_scale_the_change():
    red = np.zeros((1, 8))
    red[0, 0] = 1
    assert vib().gain(np.array([0.1]), red, 100)[0] == pytest.approx(1.4)  # half of +0.8


def test_vibrance_pivot_depends_on_direction():
    assert vib().pivot(-20) == 0.6 and vib().pivot(20) == 0.75


# --- the shipped response ---------------------------------------------------------------------

def test_shipped_response_has_every_tone_family():
    r = load()
    assert set(r.tone) == set(TONE_KEYS)
    for key, fam in r.tone.items():
        for a in (-100, -50, 50, 100):
            y = fam.curve(a)
            assert is_monotonic(y), key
            assert y[0] == pytest.approx(0, abs=1e-3) and y[-1] == pytest.approx(1, abs=1e-3), key


def test_response_round_trips_through_json(tmp_path):
    r = load()
    path = tmp_path / "r.json"
    path.write_text(json.dumps(r.to_dict()), encoding="utf-8")
    back = load(path)
    assert back.exposure == r.exposure
    for key in TONE_KEYS:
        for a, y in r.tone[key].curves.items():
            np.testing.assert_allclose(back.tone[key].curves[a], y, atol=1e-6)
    assert back.vibrance.pivots == r.vibrance.pivots


def test_response_without_a_tone_family_is_rejected():
    d = load().to_dict()
    del d["tone"]["Whites2012"]
    with pytest.raises(ColorlabError, match="Whites2012"):
        Response.from_dict(d)
