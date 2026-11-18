"""Film halation and grain (ported from spektrafilm), sized in micrometres on the film."""
import numpy as np
import pytest
import tifffile

from colorlab.film import (
    BOOST_SPAN_EV,
    MIDGRAY,
    Grain,
    Halation,
    apply_to_image,
    boost_highlights,
    exponential_blur,
    gaussian_blur,
    grain,
    grain_density,
    halation,
    main,
    pixel_size_um,
)

NO_BLUR = Grain(blur_px=0.0)


def delta(size: int) -> np.ndarray:
    img = np.zeros((size, size))
    img[size // 2, size // 2] = 1.0
    return img


def radius2(shape) -> np.ndarray:
    h, w = shape
    y, x = np.mgrid[:h, :w]
    return (x - (w - 1) / 2) ** 2 + (y - (h - 1) / 2) ** 2


def mean_r2(img: np.ndarray) -> float:
    return float((img * radius2(img.shape)).sum() / img.sum())


def expected_grain_variance(d, params: Grain, pixel_um: float) -> np.ndarray:
    dmin = np.array(params.density_min)
    total = np.array(params.density_max) + dmin
    count = pixel_um ** 2 / (params.particle_area_um2 * np.array(params.particle_scale))
    p = (d + dmin) / total
    return (d + dmin) * (total / count) * (1 - np.array(params.uniformity) * p)


# --- sizes and blurs -----------------------------------------------------------------------------

def test_pixel_size_is_the_frames_long_side_over_its_pixels():
    assert pixel_size_um((4000, 6000, 3)) == pytest.approx(6.0)
    assert pixel_size_um((6000, 4000, 3)) == pytest.approx(6.0)
    assert pixel_size_um((1000, 1000), format_mm=56.0) == pytest.approx(56.0)


def test_gaussian_blur_keeps_light_and_flat_areas_and_has_the_requested_width():
    out = gaussian_blur(delta(61), 3.0)
    assert out.sum() == pytest.approx(1.0)
    assert mean_r2(out) == pytest.approx(2 * 3.0 ** 2, rel=0.03)  # truncated at 3 sigma
    flat = np.full((20, 30, 3), 0.4)
    np.testing.assert_allclose(gaussian_blur(flat, (0.5, 4.0, 9.0)), flat, atol=1e-12)  # edges clamp
    img = np.random.default_rng(0).random((16, 16, 3))
    np.testing.assert_array_equal(gaussian_blur(img, 0.0), img)
    per_channel = gaussian_blur(np.stack([delta(61)] * 3, axis=-1), (1.0, 2.0, 4.0))
    assert [mean_r2(per_channel[..., c]) for c in range(3)] == pytest.approx([2.0, 8.0, 32.0], rel=0.05)


def test_exponential_blur_approximates_an_exponential_psf():
    decay = 4.0
    out = exponential_blur(delta(161), decay)
    assert out.sum() == pytest.approx(1.0, abs=1e-12)
    assert mean_r2(out) == pytest.approx(6 * decay ** 2, rel=0.03)  # E[r^2] of exp(-r/L) in 2-D
    r = np.sqrt(radius2(out.shape))
    for radius in (4, 8, 16):  # encircled energy of exp(-r/L) / (2 pi L^2)
        assert out[r <= radius].sum() == pytest.approx(1 - (1 + radius / decay) * np.exp(-radius / decay), abs=0.02)


# --- halation ------------------------------------------------------------------------------------

def test_halation_keeps_flat_areas_and_with_defaults_is_red_then_green_never_blue():
    flat = np.full((40, 50, 3), 0.3)
    np.testing.assert_allclose(halation(flat, Halation(), 10.0), flat, atol=1e-12)
    point = np.zeros((301, 301, 3))
    point[150, 150] = 1.0
    out = halation(point, Halation(), 10.0)
    ring = np.abs(np.sqrt(radius2(point.shape[:2])) - 15) < 0.5  # 150 um from the light
    red, green, blue = (out[..., c][ring].mean() for c in range(3))
    assert red > 2.5 * green > 0 and blue == 0.0
    np.testing.assert_allclose(out.sum(axis=(0, 1)), 1.0, atol=1e-9)  # renormalized: light kept


def test_halation_adds_strength_times_the_light_as_bounces_of_widening_gaussians():
    point = np.zeros((301, 301, 3))
    point[150, 150] = 1.0
    params = Halation(scatter_amount=0.0, renormalize=False, strength=(0.05, 0.02, 0.0), amount=2.0)
    out = halation(point, params, 10.0)
    added = out[..., 0] - point[..., 0]
    assert added.sum() == pytest.approx(0.1)
    assert out[..., 1].sum() - 1 == pytest.approx(0.04)
    # bounces k = 1, 2, 3 weigh 4/7, 2/7, 1/7 and have widths 65 um * sqrt(k)
    sigma = 65.0 / 10.0
    assert mean_r2(added) == pytest.approx(2 * sigma ** 2 * 11 / 7, rel=0.03)
    single = halation(point, Halation(scatter_amount=0.0, renormalize=False, bounces=1), 10.0)
    assert mean_r2(single[..., 0] - point[..., 0]) == pytest.approx(2 * sigma ** 2, rel=0.03)


def test_halation_is_sized_on_the_film_so_every_resolution_gets_the_same_halo():
    def render(pixel_um: float) -> tuple[np.ndarray, np.ndarray]:
        size = int(round(3000 / pixel_um)) | 1  # a 3 mm square of film
        img = np.full((size, size, 3), 0.05)
        r_um = np.sqrt(radius2((size, size))) * pixel_um
        img[r_um <= 100] = 1.0  # a 0.2 mm bright disc
        return halation(img, Halation(), pixel_um), r_um

    (fine, r_fine), (coarse, r_coarse) = render(5.0), render(10.0)
    for distance in (150, 250, 400):
        a = fine[..., 0][np.abs(r_fine - distance) < 5].mean()
        b = coarse[..., 0][np.abs(r_coarse - distance) < 5].mean()
        assert a == pytest.approx(b, rel=0.03), distance
    assert fine[..., 0][np.abs(r_fine - 150) < 5].mean() > 0.055  # the halo lifts the dark surround


def test_no_halation_and_no_scatter_leave_the_image_unchanged():
    img = np.random.default_rng(1).random((24, 24, 3))
    off = Halation(scatter_amount=0.0, amount=0.0)
    np.testing.assert_array_equal(halation(img, off, 10.0), img)
    np.testing.assert_array_equal(halation(img, Halation(scatter_amount=0.0, bounces=0), 10.0), img)
    assert halation(np.zeros((36, 54, 3))).shape == (36, 54, 3)  # default pixel size: a 36 mm frame


def test_highlight_boost_is_anchored_to_scene_exposure_not_to_the_frame():
    x0 = MIDGRAY * 2 ** 4.0
    top = MIDGRAY * 2 ** (4.0 + BOOST_SPAN_EV)
    x = np.linspace(0, 3 * top, 3001)
    y = boost_highlights(x, 2.0)
    np.testing.assert_array_equal(y[x <= x0], x[x <= x0])
    assert boost_highlights(np.array([top]), 2.0)[0] == pytest.approx(4 * top)  # 2 stops at the top
    assert np.all(np.diff(y) > 0)
    eps = 1e-6
    left = (boost_highlights(np.array([top]), 2.0) - boost_highlights(np.array([top - eps]), 2.0)) / eps
    right = (boost_highlights(np.array([top + eps]), 2.0) - boost_highlights(np.array([top]), 2.0)) / eps
    assert left[0] == pytest.approx(right[0], rel=1e-4)  # continues along its tangent
    alone = boost_highlights(np.array([10.0]), 2.0)
    with_sun = boost_highlights(np.array([10.0, 1000.0]), 2.0)
    assert with_sun[0] == alone[0]
    np.testing.assert_array_equal(boost_highlights(x, 0.0), x)
    for bad in ({"boost_ev": -1.0}, {"boost_ev": 1.0, "boost_range": 1.5}, {"boost_ev": 1.0, "protect_ev": -1}):
        with pytest.raises(ValueError):
            boost_highlights(x, **bad)


def test_halation_feeds_boosted_highlights_into_the_halo():
    img = np.full((101, 101, 3), 0.9)
    img[45:56, 45:56] = 40.0  # a light source far above mid-gray
    plain = halation(img, Halation(), 10.0)
    boosted = halation(img, Halation(boost_ev=3.0), 10.0)
    assert boosted[50, 20, 0] > plain[50, 20, 0] + 0.002
    np.testing.assert_allclose(boosted[0, 0], plain[0, 0], rtol=1e-3)  # far away, unchanged


# --- grain ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("d", [0.0, 0.2, 0.7, 1.5, 2.2])
def test_grain_keeps_the_mean_density_and_has_the_particle_models_variance(d):
    out = grain_density(np.full((256, 256, 3), d), NO_BLUR, 10.0, seed=1)
    var = expected_grain_variance(d, NO_BLUR, 10.0)
    assert np.all(np.abs(out.mean(axis=(0, 1)) - d) < 4 * np.sqrt(var / 256 ** 2))
    np.testing.assert_allclose(out.var(axis=(0, 1)), var, rtol=0.03)


def test_grain_is_sized_on_the_film_so_averaged_fine_grain_matches_coarse_pixels():
    density = np.full((512, 512, 3), 0.8)
    fine = grain_density(density, NO_BLUR, 5.0, seed=2)
    coarse = grain_density(density[:256, :256], NO_BLUR, 10.0, seed=3)
    binned = fine.reshape(256, 2, 256, 2, 3).mean(axis=(1, 3))
    np.testing.assert_allclose(binned.var(axis=(0, 1)), coarse.var(axis=(0, 1)), rtol=0.03)
    assert fine.var() > 3.5 * coarse.var()  # each fine pixel holds a quarter of the grains


def test_bigger_particles_give_stronger_grain_and_the_scan_blur_softens_it():
    density = np.full((256, 256, 3), 0.8)
    small = grain_density(density, NO_BLUR, 10.0, seed=4)
    big = grain_density(density, Grain(particle_area_um2=0.8, blur_px=0.0), 10.0, seed=4)
    np.testing.assert_allclose(big.var(axis=(0, 1)) / small.var(axis=(0, 1)), 4.0, rtol=0.05)
    soft = grain_density(density, Grain(blur_px=2.0), 10.0, seed=4)
    kernel = gaussian_blur(delta(41), 2.0)
    inner = (slice(16, -16), slice(16, -16))  # clamped edges repeat pixels, so leave them out
    np.testing.assert_allclose(soft[inner].var(axis=(0, 1)) / small[inner].var(axis=(0, 1)), (kernel ** 2).sum(),
                               rtol=0.05)


def test_grain_is_deterministic_per_seed_and_independent_per_channel():
    density = np.full((128, 128, 3), 1.0)
    a = grain_density(density, Grain(), 10.0, seed=7)
    np.testing.assert_array_equal(a, grain_density(density, Grain(), 10.0, seed=7))
    assert not np.array_equal(a, grain_density(density, Grain(), 10.0, seed=8))
    noise = (a - a.mean(axis=(0, 1))).reshape(-1, 3)
    corr = np.corrcoef(noise.T)
    assert np.all(np.abs(corr[np.triu_indices(3, 1)]) < 0.05)
    assert grain_density(np.zeros((36, 54, 3))).shape == (36, 54, 3)  # default pixel size: a 36 mm frame


def test_grain_on_a_positive_is_strongest_in_the_midtones():
    levels = np.array([1.0, 0.2, 0.02, 0.0])  # white (clear film), midtones, deep shadow, black
    img = np.broadcast_to(levels[:, None, None, None], (4, 128, 128, 3)).reshape(4 * 128, 128, 3)
    out = grain(img, Grain(), 10.0, seed=5)
    assert out.shape == img.shape and out.min() >= 0.0 and out.max() <= 1.0
    density = -np.log10(np.clip(out, 10 ** -2.2, 1.0)).reshape(4, 128, 128, 3)
    spread = density.std(axis=(1, 2)).mean(axis=1)
    assert spread[1] > 2 * spread[0] and spread[1] > 2 * spread[3]
    np.testing.assert_allclose(density[1].mean(), -np.log10(0.2), atol=0.01)


# --- whole image and command line ----------------------------------------------------------------

def test_apply_to_image_adds_halation_then_grain_and_either_can_be_off():
    img = np.full((60, 90, 3), 0.5)
    img[25:35, 40:50] = 1.0
    both = apply_to_image(img, format_mm=0.9, seed=1)  # 10 um pixels
    assert both.shape == img.shape and 0.0 <= both.min() and both.max() <= 1.0
    neither = apply_to_image(img, format_mm=0.9, halation_params=None, grain_params=None)
    np.testing.assert_allclose(neither, img, atol=1e-9)
    halo_only = apply_to_image(img, format_mm=0.9, grain_params=None)
    assert halo_only[30, 30, 0] > img[30, 30, 0] + 0.001 and halo_only[30, 30, 2] == pytest.approx(0.5, abs=1e-6)
    grain_only = apply_to_image(img, format_mm=0.9, seed=1, halation_params=None)
    assert grain_only[:20].std() > 0.01


def test_cli_applies_halation_and_grain_to_a_tiff(tmp_path, capsys):
    src = tmp_path / "in.tif"
    img = np.full((40, 60, 3), 30000, dtype=np.uint16)
    img[15:25, 25:35] = 65535
    tifffile.imwrite(src, img, photometric="rgb")
    out = tmp_path / "sub" / "out.tif"
    assert main(["apply", str(src), str(out), "--format-mm", "0.6", "--seed", "3"]) == 0
    result = tifffile.imread(out)
    assert result.dtype == np.uint16 and result.shape == img.shape and not np.array_equal(result, img)
    assert "wrote" in capsys.readouterr().out
    off = tmp_path / "off.tif"
    assert main(["apply", str(src), str(off), "--halation", "0", "--particle-area", "0"]) == 0
    np.testing.assert_allclose(tifffile.imread(off).astype(int), img.astype(int), atol=1)
    flt = tmp_path / "f.tif"
    tifffile.imwrite(flt, np.full((8, 8, 3), 0.5, dtype=np.float32), photometric="rgb")
    assert main(["apply", str(flt), str(tmp_path / "f2.tif")]) == 0
    assert tifffile.imread(tmp_path / "f2.tif").dtype == np.float32


def test_cli_reports_bad_input(tmp_path, capsys):
    gray = tmp_path / "gray.tif"
    tifffile.imwrite(gray, np.zeros((8, 8), dtype=np.uint8))
    assert main(["apply", str(gray), str(tmp_path / "o.tif")]) == 1
    assert main(["apply", str(tmp_path / "missing.tif"), str(tmp_path / "o.tif")]) == 1
    assert "error" in capsys.readouterr().err
