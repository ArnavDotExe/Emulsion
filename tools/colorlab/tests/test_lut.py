import numpy as np
import pytest

from colorlab.errors import LutFormatError
from colorlab.lut import (
    Lut3D,
    apply_lut,
    format_cube,
    hald_identity,
    lut_from_hald,
    parse_cube,
    read_cube,
    write_cube,
)

RNG = np.random.default_rng(11)
METHODS = ["tetrahedral", "trilinear"]


def affine_lut(size: int) -> tuple[Lut3D, np.ndarray, np.ndarray]:
    m = np.array([[0.8, 0.1, 0.05], [0.05, 0.9, 0.0], [0.1, 0.0, 0.7]])
    b = np.array([0.02, 0.03, 0.05])
    grid = Lut3D.identity(size).table
    return Lut3D(grid @ m.T + b), m, b


# --- construction -------------------------------------------------------------

def test_identity_shape_and_corners():
    lut = Lut3D.identity(17)
    assert lut.size == 17
    assert lut.table.shape == (17, 17, 17, 3)
    np.testing.assert_array_equal(lut.table[16, 0, 0], [1, 0, 0])
    np.testing.assert_array_equal(lut.table[0, 16, 0], [0, 1, 0])
    np.testing.assert_array_equal(lut.table[0, 0, 16], [0, 0, 1])


def test_identity_rejects_size_below_two():
    with pytest.raises(LutFormatError):
        Lut3D.identity(1)


@pytest.mark.parametrize("shape", [(4, 4, 3, 3), (4, 4, 4), (4, 4, 4, 4)])
def test_constructor_rejects_bad_shape(shape):
    with pytest.raises(LutFormatError):
        Lut3D(np.zeros(shape))


def test_constructor_rejects_non_finite_values():
    table = Lut3D.identity(2).table.copy()
    table[0, 0, 0, 0] = np.nan
    with pytest.raises(LutFormatError):
        Lut3D(table)


# --- interpolation ---------------------------------------------------------------

@pytest.mark.parametrize("method", METHODS)
def test_identity_apply_is_noop(method):
    rgb = RNG.random((500, 3))
    np.testing.assert_allclose(apply_lut(Lut3D.identity(9), rgb, method=method), rgb, atol=1e-6)


@pytest.mark.parametrize("method", METHODS)
def test_affine_lut_is_reproduced_exactly(method):
    lut, m, b = affine_lut(5)
    rgb = RNG.random((500, 3))
    np.testing.assert_allclose(apply_lut(lut, rgb, method=method), rgb @ m.T + b, atol=1e-5)


@pytest.mark.parametrize("method", METHODS)
def test_lattice_points_return_table_values(method):
    lut = Lut3D(RNG.random((4, 4, 4, 3)))
    idx = np.array([[0, 0, 0], [3, 3, 3], [1, 2, 3], [3, 0, 2]])
    out = apply_lut(lut, idx / 3.0, method=method)
    np.testing.assert_allclose(out, lut.table[idx[:, 0], idx[:, 1], idx[:, 2]], atol=1e-6)


def test_tetrahedral_keeps_neutral_axis_on_diagonal_nodes():
    table = RNG.random((5, 5, 5, 3))
    for i in range(5):
        table[i, i, i] = i / 4.0  # neutral nodes map to neutral
    gray = np.repeat(np.linspace(0, 1, 33)[:, None], 3, axis=1)
    out = apply_lut(Lut3D(table), gray, method="tetrahedral")
    np.testing.assert_allclose(out[:, 0], out[:, 1], atol=1e-6)
    np.testing.assert_allclose(out[:, 1], out[:, 2], atol=1e-6)


@pytest.mark.parametrize("method", METHODS)
def test_apply_clamps_out_of_range_input(method):
    lut, _, _ = affine_lut(5)
    out = apply_lut(lut, np.array([[1.5, -0.2, 0.5]]), method=method)
    ref = apply_lut(lut, np.array([[1.0, 0.0, 0.5]]), method=method)
    np.testing.assert_allclose(out, ref, atol=1e-6)


def test_apply_preserves_leading_shape_and_returns_float32():
    out = apply_lut(Lut3D.identity(3), RNG.random((4, 5, 3)))
    assert out.shape == (4, 5, 3)
    assert out.dtype == np.float32


def test_apply_rejects_unknown_method():
    with pytest.raises(ValueError):
        apply_lut(Lut3D.identity(2), np.zeros((1, 3)), method="cubic")


def test_apply_rejects_non_rgb_input():
    with pytest.raises(ValueError):
        apply_lut(Lut3D.identity(2), np.zeros((4, 2)))


# --- .cube ----------------------------------------------------------------------------

def test_cube_round_trip(tmp_path):
    lut = Lut3D(RNG.random((6, 6, 6, 3)).astype(np.float32), title="Round Trip")
    path = tmp_path / "x.cube"
    write_cube(lut, path)
    back = read_cube(path)
    assert back.title == "Round Trip"
    np.testing.assert_allclose(back.table, lut.table, atol=1e-6)


def test_cube_red_varies_fastest():
    lines = [ln for ln in format_cube(Lut3D.identity(2)).splitlines() if ln and ln[0].isdigit()]
    assert lines[:3] == ["0.000000 0.000000 0.000000", "1.000000 0.000000 0.000000", "0.000000 1.000000 0.000000"]


def test_parse_cube_accepts_comments_title_and_unit_domain():
    text = "# made by hand\nTITLE \"Hand\"\nLUT_3D_SIZE 2\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n\n" + "\n".join(
        f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)
    )
    lut = parse_cube(text)
    assert lut.title == "Hand"
    np.testing.assert_allclose(lut.table, Lut3D.identity(2).table)


@pytest.mark.parametrize(
    "text",
    [
        "0 0 0\n1 1 1\n",  # no size
        "LUT_3D_SIZE 2\n0 0 0\n",  # too few rows
        "LUT_3D_SIZE 2\n" + "0 0 x\n" * 8,  # not numeric
        "LUT_1D_SIZE 4\n" + "0 0 0\n" * 4,  # 1D unsupported
        "LUT_3D_SIZE 2\nDOMAIN_MAX 2 2 2\n" + "0 0 0\n" * 8,  # non-unit domain
        "LUT_3D_SIZE 2\n" + "0 0\n" * 8,  # wrong column count
    ],
)
def test_parse_cube_rejects_malformed_input(text):
    with pytest.raises(LutFormatError):
        parse_cube(text)


# --- Hald CLUT ------------------------------------------------------------------------

def test_hald_identity_shape():
    img = hald_identity(8)
    assert img.shape == (512, 512, 3)
    assert img.dtype == np.float32


def test_hald_identity_red_varies_fastest():
    img = hald_identity(2)  # cube size 4, image 8x8
    np.testing.assert_allclose(img[0, :4, 0], [0, 1 / 3, 2 / 3, 1], atol=1e-6)
    np.testing.assert_allclose(img[0, :4, 1], 0, atol=1e-6)


def test_hald_round_trip():
    np.testing.assert_allclose(lut_from_hald(hald_identity(4)).table, Lut3D.identity(16).table, atol=1e-6)


def test_lut_from_processed_hald_captures_the_transform():
    img = hald_identity(3)
    lut = lut_from_hald(1.0 - img)
    np.testing.assert_allclose(lut.table, 1.0 - Lut3D.identity(9).table, atol=1e-6)


@pytest.mark.parametrize("shape", [(10, 10, 3), (8, 4, 3), (8, 8)])
def test_lut_from_hald_rejects_bad_shape(shape):
    with pytest.raises(LutFormatError):
        lut_from_hald(np.zeros(shape))


def test_hald_identity_rejects_level_below_two():
    with pytest.raises(LutFormatError):
        hald_identity(1)
