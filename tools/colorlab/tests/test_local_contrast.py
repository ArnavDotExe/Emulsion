"""Clarity (a local Laplacian filter) and Texture (a guided filter), shaped like Lightroom's."""
import numpy as np
import pytest
import tifffile

from colorlab.errors import ParameterError
from colorlab.local_contrast import (
    TEXTURE_RADIUS,
    apply_to_image,
    clarity,
    clarity_levels,
    guided_filter,
    local_laplacian,
    main,
    texture,
)


def edge(width: int, lo: float = 0.35, hi: float = 0.95) -> np.ndarray:
    """A gray frame (3:2), dark on the left half and bright on the right."""
    img = np.full((width * 2 // 3, width, 3), lo)
    img[:, width // 2:] = hi
    return img


def bars(width: int, period: int, lo: float = 0.2, hi: float = 0.8) -> np.ndarray:
    cols = (np.arange(width) // (period // 2)) % 2
    return np.repeat(np.where(cols == 0, lo, hi)[None, :, None], width * 2 // 3, 0).repeat(3, 2).astype(float)


def gray(img: np.ndarray) -> np.ndarray:
    return img[..., 1]


def test_local_laplacian_with_the_identity_remap_reproduces_the_image():
    rng = np.random.default_rng(1)
    y = rng.uniform(0, 1, (67, 90))
    out = local_laplacian(y, lambda x, g: x, levels=4)
    np.testing.assert_allclose(out, y, atol=1e-9)


def test_local_laplacian_leaves_a_flat_image_flat_and_applies_the_residual_curve():
    y = np.full((40, 60), 0.4)
    pull = local_laplacian(y, lambda x, g: x + 0.5 * (g - x), levels=3)
    np.testing.assert_allclose(pull, 0.4, atol=1e-9)
    curved = local_laplacian(y, lambda x, g: x, levels=3, residual=lambda m: m * m)
    np.testing.assert_allclose(curved, 0.16, atol=1e-9)


def test_local_laplacian_detail_remap_is_edge_aware():
    # small details inside sigma are boosted, a big step outside it keeps its height
    y = np.full((64, 64), 0.3)
    y[:, 32:] = 0.9
    y[20:24, 8:12] += 0.02

    def boost(x, g, sigma=0.1, k=1.0):
        c = x - g
        return x + k * np.clip(c, -sigma, sigma) * np.clip(1 - np.abs(c) / (2 * sigma), 0, 1) * 2
    out = local_laplacian(y, boost, levels=4)
    assert out[21, 9] - out[21, 4] > 1.5 * 0.02
    assert abs((out[32, 50] - out[32, 14]) - 0.6) < 0.05


def test_guided_filter_smooths_texture_but_keeps_strong_edges():
    rng = np.random.default_rng(2)
    y = np.full((80, 80), 0.3)
    y[:, 40:] = 0.9
    noisy = y + rng.normal(0, 0.01, y.shape)
    out = guided_filter(noisy, noisy, radius=6, eps=0.01)
    assert np.std(out[:, :30] - y[:, :30]) < 0.5 * 0.01
    assert out[40, 45] - out[40, 34] > 0.5
    flat = guided_filter(noisy, np.full(y.shape, 0.5), radius=6, eps=0.01)
    np.testing.assert_allclose(flat, 0.5, atol=1e-9)


def test_zero_amounts_change_nothing_and_grays_stay_gray():
    img = edge(240)
    np.testing.assert_allclose(clarity(img, 0), img, atol=1e-12)
    np.testing.assert_allclose(texture(img, 0), img, atol=1e-12)
    for out in (clarity(img, 60), clarity(img, -60), texture(img, 60), texture(img, -60)):
        np.testing.assert_allclose(out[..., 0], out[..., 1], atol=1e-6)
        np.testing.assert_allclose(out[..., 2], out[..., 1], atol=1e-6)


def test_amounts_outside_the_slider_range_are_rejected():
    img = edge(120)
    with pytest.raises(ParameterError):
        clarity(img, 101)
    with pytest.raises(ParameterError):
        texture(img, -100.5)


def test_clarity_is_relative_to_the_frame_and_texture_is_fixed_in_pixels():
    assert [clarity_levels(w) for w in (768, 1536, 3072)] == [3, 4, 5]
    assert clarity_levels(40) == 1

    def reach(out, img, width):
        row = np.abs(gray(out) - gray(img))[width // 3]
        return width // 2 - int(np.argmax(row[: width // 2] > 0.01))

    small, large = edge(384), edge(768)
    ratio = reach(clarity(large, -100), large, 768) / reach(clarity(small, -100), small, 384)
    assert 1.6 < ratio < 2.4
    t_ratio = reach(texture(large, 100), large, 768) / reach(texture(small, 100), small, 384)
    assert 0.8 < t_ratio < 1.25
    assert TEXTURE_RADIUS == 16


def test_negative_clarity_lifts_the_dark_side_of_an_edge_and_leaves_flats_alone():
    img = edge(768)
    out = gray(clarity(img, -100))
    y, x = 256, 384
    lift = out[y, x - 2] - 0.35
    drop = 0.95 - out[y, x + 1]
    assert lift > 0.05 and lift > 2 * drop
    assert abs(out[y, 40] - 0.35) < 2e-3 and abs(out[y, 728] - 0.95) < 2e-3


def test_positive_clarity_darkens_the_midtones_and_raises_fine_contrast():
    flat = np.full((256, 384, 3), 0.5)
    out = gray(clarity(flat, 100))
    assert 0.38 < out.mean() < 0.47
    ends = np.zeros((64, 96, 3))
    ends[:, 48:] = 1.0
    np.testing.assert_allclose(gray(clarity(ends, 100))[:, [10, 85]], [[0.0, 1.0]] * 64, atol=0.02)
    fine = bars(384, 4)
    assert np.std(gray(clarity(fine, 100))) > 1.15 * np.std(gray(fine))
    assert np.std(gray(clarity(fine, -100))) < 0.9 * np.std(gray(fine))


def test_texture_raises_and_lowers_fine_contrast_monotonically_and_keeps_flats():
    fine = bars(384, 4)
    s = [np.std(gray(texture(fine, a))) for a in (-100, -50, 0, 50, 100)]
    assert all(a < b for a, b in zip(s, s[1:]))
    flat = np.full((64, 96, 3), 0.6)
    np.testing.assert_allclose(texture(flat, 100), flat, atol=1e-9)


def test_apply_to_image_and_cli_round_trip(tmp_path):
    img = edge(192)
    both = apply_to_image(img, clarity_amount=40, texture_amount=30)
    np.testing.assert_allclose(both, clarity(texture(img, 30), 40), atol=1e-12)
    src, dst = tmp_path / "in.tif", tmp_path / "out.tif"
    tifffile.imwrite(src, np.round(img * 65535).astype(np.uint16), photometric="rgb")
    assert main(["apply", str(src), str(dst), "--clarity", "40", "--texture", "30"]) == 0
    got = tifffile.imread(dst) / 65535.0
    np.testing.assert_allclose(got, both, atol=2 / 65535)
    assert main(["apply", str(tmp_path / "missing.tif"), str(dst)]) == 1
