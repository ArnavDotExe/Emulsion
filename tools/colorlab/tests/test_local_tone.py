"""Highlights and Shadows as a local tone table over the pixel and a frame-relative base, shaped
like Lightroom's: edge-preserving, fine detail kept, the other end of the range left alone."""
import numpy as np
import pytest
import tifffile

from colorlab.errors import ParameterError
from colorlab.local_contrast import apply_to_image, clarity, highlights_shadows, local_tone_sigma, main


def flat(level: float, width: int = 384) -> np.ndarray:
    return np.full((width * 2 // 3, width, 3), level)


def edge(width: int, lo: float, hi: float) -> np.ndarray:
    img = flat(lo, width)
    img[:, width // 2:] = hi
    return img


def bars(width: int, period: int, lo: float = 0.2, hi: float = 0.8) -> np.ndarray:
    cols = (np.arange(width) // (period // 2)) % 2
    return np.repeat(np.where(cols == 0, lo, hi)[None, :, None], width * 2 // 3, 0).repeat(3, 2).astype(float)


def g(img: np.ndarray) -> np.ndarray:
    return img[..., 1]


def test_zero_amounts_change_nothing_and_grays_stay_gray():
    img = edge(240, 0.2, 0.9)
    np.testing.assert_allclose(highlights_shadows(img), img, atol=1e-12)
    for h, s in ((80, 0), (-80, 0), (0, 80), (0, -80), (-60, 60)):
        out = highlights_shadows(img, highlights=h, shadows=s)
        np.testing.assert_allclose(out[..., 0], out[..., 1], atol=1e-6)
        np.testing.assert_allclose(out[..., 2], out[..., 1], atol=1e-6)


def test_amounts_outside_the_slider_range_are_rejected():
    with pytest.raises(ParameterError):
        highlights_shadows(flat(0.5, 60), highlights=-101)
    with pytest.raises(ParameterError):
        highlights_shadows(flat(0.5, 60), shadows=100.5)


def test_shadows_moves_the_darks_and_leaves_the_brights():
    dark, bright = flat(0.1), flat(0.95)
    lifts = [g(highlights_shadows(dark, shadows=a)).mean() - 0.1 for a in (25, 50, 100)]
    assert 0 < lifts[0] < lifts[1] < lifts[2] and lifts[2] > 0.08
    assert g(highlights_shadows(dark, shadows=-100)).mean() < 0.1 - 0.02
    assert abs(g(highlights_shadows(bright, shadows=100)).mean() - 0.95) < 0.015


def test_highlights_moves_the_brights_and_leaves_the_darks():
    bright, dark = flat(0.8), flat(0.05)
    drops = [0.8 - g(highlights_shadows(bright, highlights=a)).mean() for a in (-25, -50, -100)]
    assert 0 < drops[0] < drops[1] < drops[2] and drops[2] > 0.15
    assert g(highlights_shadows(bright, highlights=100)).mean() > 0.8 + 0.05
    assert abs(g(highlights_shadows(dark, highlights=-100)).mean() - 0.05) < 0.01


def test_the_change_is_edge_preserving():
    # Lightroom's Shadows +100 lifts the dark side of an edge evenly up to it: no halo
    img = edge(1536, 0.2, 0.9)
    out = g(highlights_shadows(img, shadows=100))[512]
    near, far = out[768 - 3] - 0.2, out[768 - 40] - 0.2
    assert near > 0.05 and abs(near - far) < 0.25 * far
    assert abs(out[768 + 3] - out[768 + 40]) < 0.02


def test_fine_detail_keeps_more_contrast_than_coarse_detail():
    fine, coarse = bars(1536, 4)[:96], bars(1536, 128)[:96]
    keep_fine = np.std(g(highlights_shadows(fine, highlights=-100))) / np.std(g(fine))
    keep_coarse = np.std(g(highlights_shadows(coarse, highlights=-100))) / np.std(g(coarse))
    assert keep_fine > keep_coarse + 0.1


def test_the_base_follows_the_frame():
    assert local_tone_sigma(1536) == 48.0
    assert local_tone_sigma(768) == 24.0


def test_apply_to_image_and_cli_take_highlights_and_shadows(tmp_path):
    img = edge(192, 0.2, 0.9)
    both = apply_to_image(img, clarity_amount=20, highlights_amount=-40, shadows_amount=30)
    np.testing.assert_allclose(both, clarity(highlights_shadows(img, -40, 30), 20), atol=1e-12)
    src, dst = tmp_path / "in.tif", tmp_path / "out.tif"
    tifffile.imwrite(src, np.round(img * 65535).astype(np.uint16), photometric="rgb")
    assert main(["apply", str(src), str(dst), "--highlights", "-40", "--shadows", "30", "--clarity", "20"]) == 0
    np.testing.assert_allclose(tifffile.imread(dst) / 65535.0, both, atol=2 / 65535)
