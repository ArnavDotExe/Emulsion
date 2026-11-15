import numpy as np
import pytest

from colorlab.metrics import delta_e_2000, delta_e_srgb, srgb_to_lab

colour = pytest.importorskip("colour")

# Sharma, Wu & Dalal (2005) CIEDE2000 test data: (Lab1, Lab2, dE00)
SHARMA = [
    ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
    ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
    ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
    ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
]


def test_white_and_black_in_lab():
    np.testing.assert_allclose(srgb_to_lab(np.array([1.0, 1.0, 1.0])), [100, 0, 0], atol=1e-6)
    np.testing.assert_allclose(srgb_to_lab(np.array([0.0, 0.0, 0.0])), [0, 0, 0], atol=1e-6)


def test_srgb_to_lab_matches_colour_science():
    rgb = np.random.default_rng(1).random((500, 3))
    expected = colour.XYZ_to_Lab(colour.sRGB_to_XYZ(rgb))
    np.testing.assert_allclose(srgb_to_lab(rgb), expected, atol=1e-3)


@pytest.mark.parametrize("lab1,lab2,expected", SHARMA)
def test_delta_e_2000_reference_pairs(lab1, lab2, expected):
    assert delta_e_2000(np.array(lab1), np.array(lab2)) == pytest.approx(expected, abs=1e-4)
    assert delta_e_2000(np.array(lab2), np.array(lab1)) == pytest.approx(expected, abs=1e-4)


def test_delta_e_2000_matches_colour_science_and_is_zero_for_equal_colors():
    rng = np.random.default_rng(2)
    a = rng.uniform([0, -100, -100], [100, 100, 100], (1000, 3))
    b = rng.uniform([0, -100, -100], [100, 100, 100], (1000, 3))
    np.testing.assert_allclose(delta_e_2000(a, b), colour.delta_E(a, b, method="CIE 2000"), atol=1e-6)
    np.testing.assert_allclose(delta_e_2000(a, a), 0.0, atol=1e-9)


def test_delta_e_srgb_compares_encoded_colors_and_clips():
    assert delta_e_srgb(np.array([0.5, 0.5, 0.5]), np.array([0.5, 0.5, 0.5])) == pytest.approx(0.0)
    assert delta_e_srgb(np.array([1.2, 1.0, 1.0]), np.array([1.0, 1.0, 1.0])) == pytest.approx(0.0)
    assert delta_e_srgb(np.array([0.5, 0.2, 0.2]), np.array([0.2, 0.2, 0.5])) > 30
