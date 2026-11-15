import colour
import numpy as np
import pytest

from colorlab import colorspace as cs

RNG = np.random.default_rng(7)

SPACES = [
    (cs.SRGB, "sRGB"),
    (cs.ADOBE_RGB, "Adobe RGB (1998)"),
    (cs.DISPLAY_P3, "Display P3"),
    (cs.PROPHOTO_RGB, "ProPhoto RGB"),
    (cs.REC2020, "ITU-R BT.2020"),
]


@pytest.mark.parametrize(("space", "ref"), SPACES)
def test_rgb_to_xyz_matrix_matches_colour_science(space, ref):
    expected = colour.RGB_COLOURSPACES[ref].matrix_RGB_to_XYZ
    np.testing.assert_allclose(space.rgb_to_xyz, expected, atol=1e-3)


@pytest.mark.parametrize(("space", "ref"), SPACES)
def test_xyz_to_rgb_is_inverse(space, ref):
    np.testing.assert_allclose(space.xyz_to_rgb @ space.rgb_to_xyz, np.eye(3), atol=1e-10)


def test_same_space_conversion_is_identity():
    np.testing.assert_allclose(cs.conversion_matrix(cs.SRGB, cs.SRGB), np.eye(3), atol=1e-12)


def test_conversion_round_trip():
    rgb = RNG.random((200, 3))
    there = cs.convert(rgb, cs.SRGB, cs.PROPHOTO_RGB)
    back = cs.convert(there, cs.PROPHOTO_RGB, cs.SRGB)
    np.testing.assert_allclose(back, rgb, atol=1e-9)


@pytest.mark.parametrize(("dst", "ref"), [(cs.PROPHOTO_RGB, "ProPhoto RGB"), (cs.ADOBE_RGB, "Adobe RGB (1998)")])
def test_conversion_matches_colour_science_bradford(dst, ref):
    rgb = RNG.random((50, 3))
    expected = colour.RGB_to_RGB(
        rgb,
        colour.RGB_COLOURSPACES["sRGB"],
        colour.RGB_COLOURSPACES[ref],
        chromatic_adaptation_transform="Bradford",
    )
    np.testing.assert_allclose(cs.convert(rgb, cs.SRGB, dst), expected, atol=2e-3)


def test_white_maps_to_white_across_white_points():
    np.testing.assert_allclose(cs.convert(np.ones(3), cs.SRGB, cs.PROPHOTO_RGB), np.ones(3), atol=1e-9)


def test_convert_preserves_image_shape():
    img = RNG.random((4, 5, 3))
    assert cs.convert(img, cs.SRGB, cs.DISPLAY_P3).shape == (4, 5, 3)


@pytest.mark.parametrize("tf", list(cs.Transfer))
def test_transfer_round_trip(tf):
    x = np.linspace(0.0, 1.0, 1001)
    np.testing.assert_allclose(cs.decode(cs.encode(x, tf), tf), x, atol=1e-9)


@pytest.mark.parametrize("tf", list(cs.Transfer))
def test_transfer_is_odd_symmetric_for_extended_range(tf):
    x = np.array([0.01, 0.2, 0.7])
    np.testing.assert_allclose(cs.encode(-x, tf), -cs.encode(x, tf))


def test_srgb_encoding_matches_colour_science():
    x = np.linspace(0.0, 1.0, 257)
    np.testing.assert_allclose(cs.encode(x, cs.Transfer.SRGB), colour.cctf_encoding(x, function="sRGB"), atol=1e-9)


def test_gamma22_encoding():
    assert cs.encode(np.array(0.5), cs.Transfer.GAMMA_22) == pytest.approx(0.5 ** (1 / 2.2))


def test_prophoto_linear_toe():
    assert cs.encode(np.array(1 / 1024), cs.Transfer.PROPHOTO) == pytest.approx(16 / 1024)


def test_linear_transfer_is_identity():
    x = np.array([0.0, 0.25, 1.0])
    np.testing.assert_array_equal(cs.encode(x, cs.Transfer.LINEAR), x)
