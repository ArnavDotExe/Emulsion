"""Vignette, sharpening, noise reduction, Lightroom-style grain, Dehaze and the preset wiring of all
spatial passes, shaped like Lightroom's exports of the spatial calibration charts."""
import numpy as np
import pytest

from colorlab.errors import ParameterError
from colorlab.finish import apply_settings, lightroom_grain, noise_reduction, sharpen, vignette
from colorlab.local_contrast import dehaze, highlights_shadows


def flat(level: float, width: int = 384) -> np.ndarray:
    return np.full((width * 2 // 3, width, 3), level)


def edge(width: int, lo: float = 0.2, hi: float = 0.8) -> np.ndarray:
    img = flat(lo, width)
    img[:, width // 2:] = hi
    return img


def g(img: np.ndarray) -> np.ndarray:
    return img[..., 1]


def lag1(z: np.ndarray) -> float:
    z = z - z.mean()
    return float((z[:, 1:] * z[:, :-1]).mean() / z.var())


# --- vignette ------------------------------------------------------------------------------------

def test_vignette_darkens_or_lightens_the_corners_and_keeps_the_center():
    img = flat(0.45)
    np.testing.assert_allclose(vignette(img, 0), img, atol=1e-12)
    h, w = img.shape[:2]
    dark, light = g(vignette(img, -50)), g(vignette(img, 50))
    assert abs(dark[h // 2, w // 2] - 0.45) < 1e-3 and abs(light[h // 2, w // 2] - 0.45) < 1e-3
    assert dark[0, 0] < 0.25 and light[0, 0] > 0.6
    corners = [g(vignette(img, a))[2, 2] for a in (-100, -50, -25, 0, 25, 50, 100)]
    assert all(a < b for a, b in zip(corners, corners[1:]))
    out = vignette(np.dstack([flat(0.3)[..., 0], flat(0.3)[..., 0], flat(0.3)[..., 0]]), -60)
    np.testing.assert_allclose(out[..., 0], out[..., 2], atol=1e-9)


def test_vignette_shape_follows_midpoint_feather_and_roundness():
    img = flat(0.45, 600)
    h, w = img.shape[:2]
    area = lambda out: float((g(out) < 0.44).mean())
    assert area(vignette(img, -50, midpoint=0)) > area(vignette(img, -50)) > area(vignette(img, -50, midpoint=100))
    row = lambda out: g(out)[h // 2]
    steep = lambda out: float(np.abs(np.diff(row(out))).max())
    assert steep(vignette(img, -50, feather=0)) > 3 * steep(vignette(img, -50, feather=100))
    circle = g(vignette(img, -50, roundness=100))
    r = h // 2 - 10  # the same distance in pixels, across and down
    assert abs(circle[h // 2, w // 2 + r] - circle[h // 2 + r, w // 2]) < 0.02
    rect = g(vignette(img, -50, roundness=-100))
    assert rect[h // 2, 5] < 0.44 and rect[h // 2, w // 4] > 0.449


def test_vignette_highlight_priority_spares_highlights_more_than_paint_overlay():
    img = flat(0.95)
    hp, paint = g(vignette(img, -50, style=1))[2, 2], g(vignette(img, -50, style=3))[2, 2]
    assert hp > paint + 0.05
    protected = g(vignette(img, -50, style=1, highlights=100))[2, 2]
    assert protected > hp
    with pytest.raises(ParameterError):
        vignette(img, -101)
    with pytest.raises(ParameterError):
        vignette(img, -50, style=4)


# --- sharpening ----------------------------------------------------------------------------------

def test_sharpening_overshoots_edges_mostly_on_the_bright_side_and_keeps_flats():
    img = edge(256)
    np.testing.assert_allclose(sharpen(img, 0), img, atol=1e-12)
    out = g(sharpen(img, 50))[80]
    bright, dark = out[128] - 0.8, 0.2 - out[127]
    assert bright > 0.05 and bright > 2 * dark >= 0
    assert abs(out[20] - 0.2) < 1e-6 and abs(out[230] - 0.8) < 1e-6
    wide = g(sharpen(img, 50, radius=3.0))[80]
    assert (np.abs(wide - g(img)[80]) > 0.005).sum() > (np.abs(out - g(img)[80]) > 0.005).sum()
    with pytest.raises(ParameterError):
        sharpen(img, 151)


def test_sharpening_grows_with_amount_and_is_fixed_in_pixels():
    img = edge(256)
    peaks = [g(sharpen(img, a))[80, 128] for a in (25, 50, 100)]
    assert peaks[0] < peaks[1] <= peaks[2]
    big = edge(1024)
    assert abs(g(sharpen(big, 50))[300, 512] - peaks[1]) < 1e-3


# --- noise reduction -----------------------------------------------------------------------------

def test_luminance_noise_reduction_smooths_noise_monotonically_and_keeps_edges():
    rng = np.random.default_rng(3)
    noisy = np.clip(0.5 + rng.normal(0, 0.05, (200, 300))[..., None].repeat(3, 2), 0, 1)
    np.testing.assert_allclose(noise_reduction(noisy), noisy, atol=1e-12)
    sds = [g(noise_reduction(noisy, luminance=a)).std() for a in (0, 25, 50, 100)]
    assert all(a > b for a, b in zip(sds, sds[1:])) and sds[-1] < 0.5 * sds[0]
    step = noise_reduction(edge(256), luminance=100)
    assert g(step)[80, 140] - g(step)[80, 115] > 0.55


def test_color_noise_reduction_removes_chroma_noise_but_not_luma_noise():
    rng = np.random.default_rng(4)
    noisy = np.clip(0.5 + rng.normal(0, 0.04, (200, 300, 3)), 0, 1)
    out = noise_reduction(noisy, color=25)
    chroma = lambda im: float((im[..., 0] - im[..., 1]).std())
    luma = lambda im: float((im @ [0.299, 0.587, 0.114]).std())
    assert chroma(out) < 0.5 * chroma(noisy)
    assert luma(out) > 0.9 * luma(noisy)
    with pytest.raises(ParameterError):
        noise_reduction(noisy, color=-1)


# --- grain ---------------------------------------------------------------------------------------

def test_lightroom_grain_is_monochrome_deterministic_and_scales_with_amount():
    img = flat(0.45, 1536)
    np.testing.assert_allclose(lightroom_grain(img, 0), img, atol=1e-12)
    out = lightroom_grain(img, 50, seed=7)
    d = out - img
    np.testing.assert_allclose(d[..., 0], d[..., 1], atol=1e-9)
    np.testing.assert_allclose(lightroom_grain(img, 50, seed=7), out, atol=0)
    sd = float(g(d).std())
    assert 0.064 < sd < 0.084  # Lightroom: 0.0736 at Grain 50, size 25, roughness 50, 1536 px wide
    assert abs(float(g(lightroom_grain(img, 100, seed=7) - img).std()) / sd - 2.0) < 0.15


def test_lightroom_grain_size_roughness_and_frame():
    img = flat(0.45, 1536)
    fine, coarse = (lag1(g(lightroom_grain(img, 50, size=s) - img)) for s in (10, 100))
    assert fine < -0.05 and coarse > 0.15  # Lightroom: -0.13 and +0.22
    smooth = lag1(g(lightroom_grain(img, 50, roughness=100) - img))
    rough = lag1(g(lightroom_grain(img, 50, roughness=0) - img))
    assert smooth > 0.15 and rough < -0.1
    small, large = flat(0.45, 768), flat(0.45, 3072)
    assert g(lightroom_grain(small, 50) - small).std() > g(lightroom_grain(large, 50) - large).std() * 1.4


# --- dehaze --------------------------------------------------------------------------------------

def test_dehaze_darkens_and_saturates_or_lifts_and_desaturates():
    img = flat(0.5)
    np.testing.assert_allclose(dehaze(img, 0), img, atol=1e-12)
    assert g(dehaze(img, 100)).mean() < 0.35 and g(dehaze(img, -100)).mean() > 0.75
    color = np.zeros((64, 96, 3)) + [0.6, 0.45, 0.35]
    spread = lambda im: float((im[..., 0] - im[..., 2]).mean())
    assert spread(dehaze(color, 100)) > spread(color) + 0.05
    assert spread(dehaze(color, -100)) < spread(color) - 0.1
    out = dehaze(edge(240), 60)
    np.testing.assert_allclose(out[..., 0], out[..., 2], atol=1e-6)


# --- wiring --------------------------------------------------------------------------------------

def test_apply_settings_runs_the_spatial_passes_named_in_a_preset():
    img = edge(192)
    np.testing.assert_allclose(apply_settings(img, {}), img, atol=1e-12)
    settings = {"PostCropVignetteAmount": "-30", "Sharpness": "40", "Dehaze": "+20", "Shadows2012": "+30",
                "GrainAmount": "20", "GrainSize": "25", "GrainFrequency": "50", "LuminanceSmoothing": "20"}
    out = apply_settings(img, settings, seed=1)
    assert out.shape == img.shape and not np.allclose(out, img)
    np.testing.assert_allclose(apply_settings(img, settings, seed=1), out, atol=0)
    no_grain = apply_settings(img, {**settings, "GrainAmount": "0"}, seed=1)
    assert np.abs(out - no_grain).std() > 0.01


def test_finish_cli_applies_a_presets_spatial_passes(tmp_path):
    import tifffile

    from colorlab.finish import main
    from conftest import make_xmp

    img = edge(96)
    settings = {"PostCropVignetteAmount": "-40", "GrainAmount": "15", "Dehaze": "10"}
    preset, src, dst = tmp_path / "p.xmp", tmp_path / "in.tif", tmp_path / "out" / "o.tif"
    preset.write_text(make_xmp(settings), encoding="utf-8")
    tifffile.imwrite(src, np.round(img * 65535).astype(np.uint16), photometric="rgb")
    assert main(["apply", str(preset), str(src), str(dst), "--seed", "3"]) == 0
    np.testing.assert_allclose(tifffile.imread(dst) / 65535.0, apply_settings(img, settings, seed=3), atol=2 / 65535)
    tifffile.imwrite(src, np.zeros((8, 8), np.uint16))
    assert main(["apply", str(preset), str(src), str(dst)]) == 1
    with pytest.raises(ParameterError):
        apply_settings(img, {"Sharpness": "lots"})
    with pytest.raises(ParameterError):
        vignette(img[..., :2], -10)


# --- whole presets -------------------------------------------------------------------------------

def test_render_preset_runs_the_look_then_the_spatial_passes_in_either_order():
    from colorlab.finish import render_preset
    from colorlab.pipeline import render
    from colorlab.preset import parse_preset
    from conftest import make_xmp

    img = edge(96)
    plain = parse_preset(make_xmp())
    np.testing.assert_allclose(render_preset(plain, img), render(plain, img), atol=1e-6)
    settings = {"Exposure2012": "+0.30", "Shadows2012": "+40", "Sharpness": "40", "PostCropVignetteAmount": "-30",
                "GrainAmount": "20"}
    preset = parse_preset(make_xmp(settings))
    after = render_preset(preset, img, seed=2)
    np.testing.assert_allclose(after, apply_settings(render(preset, img), preset.settings, seed=2), atol=1e-6)
    before = render_preset(preset, img, seed=2, local_first=True)
    local = sharpen(highlights_shadows(img, 0, 40), 40)
    expected = lightroom_grain(vignette(render(preset, local), -30), 20, seed=2)
    np.testing.assert_allclose(before, expected, atol=1e-6)
    assert np.abs(before - after).mean() > 1e-4
    own = render_preset(preset, img, seed=2, look=lambda x: x)  # a stand-in for the global look
    np.testing.assert_allclose(own, apply_settings(img, preset.settings, seed=2), atol=1e-6)


def test_dehaze_keeps_a_saturated_colors_main_channel_like_lightroom():
    # Lightroom's Dehaze +100 takes the chart's blue (0.15, 0.25, 0.85) to (0.00, 0.005, 0.83)
    img = np.zeros((128, 192, 3)) + [0.15, 0.25, 0.85]
    out = dehaze(img, 100)[64, 96]
    assert out[2] > 0.7 and out[0] < 0.05 and out[1] < 0.2
    gray = dehaze(flat(0.5, 192), 100)
    np.testing.assert_allclose(gray[..., 0], gray[..., 2], atol=1e-6)


def test_dehaze_neighbourhood_follows_the_frame():
    from colorlab.local_contrast import dehaze_radii

    assert dehaze_radii(1536) == (12, 96)
    assert dehaze_radii(768) == (6, 48)
    assert dehaze_radii(100) == (1, 6)
