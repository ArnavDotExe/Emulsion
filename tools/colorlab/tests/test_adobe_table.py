import struct
import zlib

import numpy as np
import pytest

from colorlab import colorspace as cs
from colorlab.adobe_table import (
    Gamma,
    Gamut,
    Primaries,
    HueSatMap,
    RGBTable,
    apply_hue_sat_map,
    apply_rgb_table,
    decode_base85,
    decode_hue_sat_map,
    decode_rgb_table,
    encode_base85,
    encode_hue_sat_map,
    encode_rgb_table,
)
from colorlab.errors import ParameterError, TableDecodeError
from colorlab.lut import Lut3D

RNG = np.random.default_rng(3)


def raw_payload(
    size: int,
    deltas: np.ndarray | None = None,
    tail: bytes | None = struct.pack("<3I2d", 0, 1, 0, 0.0, 2.0),
    header: tuple[int, int, int, int] | None = None,
) -> str:
    """Hand-build an RGBTable string independently of the encoder under test."""
    header = header or (1, 1, 3, size)
    if deltas is None:
        deltas = np.zeros((size, size, size, 3), dtype="<u2")
    body = struct.pack("<4I", *header) + deltas.astype("<u2").tobytes() + (tail or b"")
    blob = struct.pack("<I", len(body)) + zlib.compress(body)
    return encode_base85(blob)


# --- base85 ---------------------------------------------------------------------------

@pytest.mark.parametrize("n", range(0, 13))
def test_base85_round_trip(n):
    data = bytes(RNG.integers(0, 256, n, dtype=np.uint8))
    assert decode_base85(encode_base85(data)) == data


def test_base85_uses_little_endian_digits():
    assert encode_base85(b"\x00\x00\x00\x00") == "00000"
    assert encode_base85(b"\x01\x00\x00\x00") == "10000"
    assert encode_base85(b"\x55\x00\x00\x00") == "01000"  # 85 -> second digit


def test_base85_rejects_invalid_character():
    with pytest.raises(TableDecodeError):
        decode_base85("ab~cd")


def test_base85_rejects_dangling_single_character():
    with pytest.raises(TableDecodeError):
        decode_base85("000001")


def test_base85_unescapes_xml_entities():
    data = bytes(range(40))
    text = encode_base85(data).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    assert decode_base85(text) == data


# --- RGBTable decoding ------------------------------------------------------------------

def test_zero_deltas_decode_to_identity_with_metadata():
    table = decode_rgb_table(raw_payload(3))
    np.testing.assert_allclose(table.lut.table, Lut3D.identity(3).table, atol=1 / 65535)
    assert table.primaries is Primaries.SRGB
    assert table.gamma is Gamma.SRGB
    assert table.gamut is Gamut.CLIP
    assert (table.min_amount, table.max_amount) == (0.0, 2.0)


def test_deltas_wrap_modulo_16_bits():
    deltas = np.zeros((2, 2, 2, 3), dtype=np.uint16)
    deltas[0, 0, 0] = 65535  # nominal 0 + 65535 -> 1.0
    deltas[1, 1, 1] = 1  # nominal 65535 + 1 wraps -> 0.0
    table = decode_rgb_table(raw_payload(2, deltas))
    np.testing.assert_allclose(table.lut.table[0, 0, 0], [1, 1, 1])
    np.testing.assert_allclose(table.lut.table[1, 1, 1], [0, 0, 0])


def test_blue_is_the_innermost_axis():
    deltas = np.zeros((2, 2, 2, 3), dtype=np.uint16)
    deltas[0, 0, 1, 0] = 1000  # entry r=0,g=0,b=1: red channel bumped
    table = decode_rgb_table(raw_payload(2, deltas))
    assert table.lut.table[0, 0, 1, 0] == pytest.approx(1000 / 65535)
    assert table.lut.table[0, 0, 1, 2] == pytest.approx(1.0)


def test_missing_tail_uses_conservative_defaults():
    table = decode_rgb_table(raw_payload(2, tail=None))
    assert (table.primaries, table.gamma, table.gamut) == (Primaries.SRGB, Gamma.SRGB, Gamut.CLIP)
    assert (table.min_amount, table.max_amount) == (0.0, 1.0)


@pytest.mark.parametrize(
    "header",
    [(2, 1, 3, 2), (1, 9, 3, 2), (1, 1, 1, 2), (1, 1, 3, 1), (1, 1, 3, 300)],
)
def test_rejects_bad_headers(header):
    with pytest.raises(TableDecodeError):
        decode_rgb_table(raw_payload(2, header=header))


def test_rejects_truncated_samples():
    body = struct.pack("<4I", 1, 1, 3, 4) + b"\x00" * 10
    text = encode_base85(struct.pack("<I", len(body)) + zlib.compress(body))
    with pytest.raises(TableDecodeError):
        decode_rgb_table(text)


def test_rejects_declared_size_mismatch():
    body = struct.pack("<4I", 1, 1, 3, 2) + b"\x00" * 48
    text = encode_base85(struct.pack("<I", len(body) + 5) + zlib.compress(body))
    with pytest.raises(TableDecodeError):
        decode_rgb_table(text)


def test_rejects_corrupt_zlib_stream():
    with pytest.raises(TableDecodeError):
        decode_rgb_table(encode_base85(struct.pack("<I", 10) + b"not zlib at all"))


def test_rejects_unknown_enum_values():
    with pytest.raises(TableDecodeError):
        decode_rgb_table(raw_payload(2, tail=struct.pack("<3I2d", 42, 1, 0, 0.0, 1.0)))


# --- RGBTable encoding ------------------------------------------------------------------

def test_encode_decode_round_trip():
    lut = Lut3D(np.round(RNG.random((5, 5, 5, 3)) * 65535) / 65535)
    original = RGBTable(lut, Primaries.ADOBE_RGB, Gamma.GAMMA_22, Gamut.EXTEND, 0.0, 2.0)
    back = decode_rgb_table(encode_rgb_table(original))
    np.testing.assert_allclose(back.lut.table, lut.table, atol=0.5 / 65535)
    assert (back.primaries, back.gamma, back.gamut) == (Primaries.ADOBE_RGB, Gamma.GAMMA_22, Gamut.EXTEND)
    assert (back.min_amount, back.max_amount) == (0.0, 2.0)


# --- applying a table ------------------------------------------------------------------

def table_from(fn, primaries=Primaries.SRGB, gamma=Gamma.SRGB, gamut=Gamut.CLIP, size=9) -> RGBTable:
    return RGBTable(Lut3D(fn(Lut3D.identity(size).table)), primaries, gamma, gamut, 0.0, 2.0)


@pytest.mark.parametrize("space", [cs.SRGB, cs.PROPHOTO_RGB])
def test_identity_table_is_noop(space):
    rgb = RNG.random((100, 3)) * 0.8 + 0.1
    out = apply_rgb_table(table_from(lambda t: t, gamut=Gamut.EXTEND), rgb, space)
    np.testing.assert_allclose(out, rgb, atol=1e-5)


def test_amount_zero_is_noop():
    rgb = RNG.random((50, 3))
    out = apply_rgb_table(table_from(lambda t: 1 - t), rgb, cs.SRGB, amount=0.0)
    np.testing.assert_allclose(out, rgb, atol=1e-6)


def test_table_is_applied_in_its_encoded_space():
    rgb = RNG.random((50, 3))
    out = apply_rgb_table(table_from(lambda t: 1 - t), rgb, cs.SRGB)
    expected = cs.decode(1 - cs.encode(rgb, cs.Transfer.SRGB), cs.Transfer.SRGB)
    np.testing.assert_allclose(out, expected, atol=1e-5)


def test_half_amount_blends_in_encoded_space():
    rgb = RNG.random((50, 3))
    out = apply_rgb_table(table_from(lambda t: 1 - t), rgb, cs.SRGB, amount=0.5)
    np.testing.assert_allclose(out, cs.decode(np.full_like(rgb, 0.5), cs.Transfer.SRGB), atol=1e-5)


def test_amount_outside_table_range_is_rejected():
    with pytest.raises(ParameterError):
        apply_rgb_table(table_from(lambda t: t), np.zeros((1, 3)), cs.SRGB, amount=2.5)


def test_clip_gamut_clamps_out_of_gamut_input():
    out = apply_rgb_table(table_from(lambda t: t, gamut=Gamut.CLIP), np.array([[1.4, -0.1, 0.5]]), cs.SRGB)
    np.testing.assert_allclose(out, [[1.0, 0.0, 0.5]], atol=1e-5)


def test_extend_gamut_preserves_out_of_gamut_residual():
    rgb = np.array([[1.4, -0.1, 0.5]])
    out = apply_rgb_table(table_from(lambda t: t, gamut=Gamut.EXTEND), rgb, cs.SRGB)
    np.testing.assert_allclose(out, rgb, atol=1e-5)


# --- hue/saturation/value maps (crs:LookTable; DNG HueSatMap semantics) -----------------------

def raw_hsv_payload(cells: np.ndarray, version: int = 2, encoding: int = 0, amounts=(0.0, 2.0),
                    header: tuple[int, ...] | None = None, declared: int | None = None) -> str:
    """Hand-build a LookTable string: type 0, version, hue/sat/value divisions, float32 cells
    (value outermost, saturation innermost), then the value encoding (+ amount range in v2)."""
    v, h, s, _ = cells.shape
    tail = struct.pack("<I", encoding) + (struct.pack("<2d", *amounts) if version == 2 else b"")
    body = struct.pack("<5I", *(header or (0, version, h, s, v))) + cells.astype("<f4").tobytes() + tail
    return encode_base85(struct.pack("<I", len(body) if declared is None else declared) + zlib.compress(body))


def uniform_cells(shift=0.0, sat=1.0, val=1.0, h=6, s=3, v=2) -> np.ndarray:
    cells = np.empty((v, h, s, 3))
    cells[...] = (shift, sat, val)
    return cells


def test_hue_sat_map_decodes_both_versions():
    cells = RNG.random((3, 6, 4, 3))
    m = decode_hue_sat_map(raw_hsv_payload(cells, version=1))
    assert m.cells.shape == (3, 6, 4, 3) and not m.srgb_values and (m.min_amount, m.max_amount) == (0.0, 1.0)
    np.testing.assert_allclose(m.cells, cells, atol=1e-6)
    m2 = decode_hue_sat_map(raw_hsv_payload(cells, version=2, encoding=1, amounts=(0.0, 2.0)))
    assert m2.srgb_values and (m2.min_amount, m2.max_amount) == (0.0, 2.0)
    again = decode_hue_sat_map(encode_hue_sat_map(m2))
    np.testing.assert_allclose(again.cells, cells, atol=1e-6)
    assert again.srgb_values and again.max_amount == 2.0


@pytest.mark.parametrize("bad", [
    {"header": (1, 2, 6, 3, 2)},  # an RGB table, not a hue/sat map
    {"header": (0, 3, 6, 3, 2)},  # unknown version
    {"header": (0, 2, 6, 1, 2)},  # needs at least two saturation divisions
    {"header": (0, 2, 9, 3, 2)},  # more cells declared than present
    {"declared": 7},
    {"encoding": 5},
])
def test_hue_sat_map_rejects_bad_payloads(bad):
    with pytest.raises(TableDecodeError):
        decode_hue_sat_map(raw_hsv_payload(uniform_cells(), **bad))


def test_identity_hue_sat_map_leaves_colors_unchanged():
    rgb = RNG.random((200, 3))
    out = apply_hue_sat_map(HueSatMap(uniform_cells()), rgb, cs.PROPHOTO_RGB)
    np.testing.assert_allclose(out, rgb, atol=1e-6)


def test_hue_sat_map_shifts_hue_and_scales_saturation_and_value():
    red = np.array([[0.8, 0.2, 0.2]])  # hue 0, saturation 0.75, value 0.8 (ProPhoto linear)
    np.testing.assert_allclose(apply_hue_sat_map(HueSatMap(uniform_cells(shift=120.0)), red, cs.PROPHOTO_RGB),
                               [[0.2, 0.8, 0.2]], atol=1e-6)
    np.testing.assert_allclose(apply_hue_sat_map(HueSatMap(uniform_cells(sat=0.0)), red, cs.PROPHOTO_RGB),
                               [[0.8, 0.8, 0.8]], atol=1e-6)
    np.testing.assert_allclose(apply_hue_sat_map(HueSatMap(uniform_cells(sat=2.0)), red, cs.PROPHOTO_RGB),
                               [[0.8, 0.0, 0.0]], atol=1e-6)  # saturation is clamped to 1
    np.testing.assert_allclose(apply_hue_sat_map(HueSatMap(uniform_cells(val=0.5)), red, cs.PROPHOTO_RGB),
                               [[0.4, 0.1, 0.1]], atol=1e-6)


def test_hue_sat_map_interpolates_hue_saturation_and_value_cells():
    cells = uniform_cells(h=6, s=2, v=2)
    cells[1, ..., 2] = 0.5  # value scale 1 at value 0, 0.5 at value 1
    gray = np.array([[0.5, 0.5, 0.5]])
    np.testing.assert_allclose(apply_hue_sat_map(HueSatMap(cells), gray, cs.PROPHOTO_RGB), [[0.375] * 3], atol=1e-6)
    cells = uniform_cells(h=6, s=2, v=1)
    cells[0, 1, :, 0] = 60.0  # hue cell 1 (60 degrees) shifts by 60; the wrap from cell 5 back to 0 is smooth
    orange = np.array([[0.8, 0.5, 0.2]])  # hue 30: halfway between cells 0 and 1
    np.testing.assert_allclose(apply_hue_sat_map(HueSatMap(cells), orange, cs.PROPHOTO_RGB), [[0.8, 0.8, 0.2]], atol=1e-6)
    cells = uniform_cells(h=6, s=3, v=1)
    cells[0, :, 2, 1] = 0.0  # full saturation scaled to 0, half saturation untouched
    out = apply_hue_sat_map(HueSatMap(cells), np.array([[0.8, 0.2, 0.2]]), cs.PROPHOTO_RGB)  # sat 0.75 -> x 0.5
    np.testing.assert_allclose(out, [[0.8, 0.5, 0.5]], atol=1e-6)


def test_hue_sat_map_can_index_values_by_their_srgb_encoding():
    cells = uniform_cells(h=6, s=2, v=2)
    cells[1, ..., 2] = 0.5
    gray = np.array([[0.2, 0.2, 0.2]])
    scale = 1 - 0.5 * float(cs.encode(np.array(0.2), cs.Transfer.SRGB))
    out = apply_hue_sat_map(HueSatMap(cells, srgb_values=True), gray, cs.PROPHOTO_RGB)
    np.testing.assert_allclose(out, [[0.2 * scale] * 3], atol=1e-6)


def test_hue_sat_map_amount_blends_linear_output_and_works_in_prophoto():
    red = np.array([[0.8, 0.2, 0.2]])
    half = apply_hue_sat_map(HueSatMap(uniform_cells(sat=0.0)), red, cs.PROPHOTO_RGB, amount=0.5)
    np.testing.assert_allclose(half, [[0.8, 0.5, 0.5]], atol=1e-6)
    srgb = RNG.random((20, 3))
    m = HueSatMap(uniform_cells(shift=30.0))
    np.testing.assert_allclose(apply_hue_sat_map(m, srgb, cs.SRGB),
                               cs.convert(apply_hue_sat_map(m, cs.convert(srgb, cs.SRGB, cs.PROPHOTO_RGB), cs.PROPHOTO_RGB),
                                          cs.PROPHOTO_RGB, cs.SRGB), atol=1e-6)
    with pytest.raises(ParameterError):
        apply_hue_sat_map(m, red, cs.PROPHOTO_RGB, amount=1.5)


@pytest.mark.parametrize("blob", [b"\x01", struct.pack("<I", 9) + b"not zlib", struct.pack("<I", 8) + zlib.compress(b"12345678")])
def test_hue_sat_map_rejects_broken_blobs(blob):
    with pytest.raises(TableDecodeError):
        decode_hue_sat_map(encode_base85(blob))
