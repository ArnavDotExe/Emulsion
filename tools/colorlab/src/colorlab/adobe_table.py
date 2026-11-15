"""Adobe Camera Raw / Lightroom ``RGBTable`` payloads (``crs:Table_<id>``).

Wire format (as used by Lightroom profile/look presets, cf. the Adobe DNG SDK
``dng_big_table``):

* text: Adobe's base-85 alphabet, 5 chars -> 4 bytes, little-endian digits;
  a final group of ``k + 1`` chars carries ``k`` bytes.
* blob: ``uint32`` uncompressed length, then a zlib stream.
* payload (little-endian): ``uint32`` table type (1 = RGB table), version (1),
  dimensions (3), divisions ``N``; then ``N**3`` entries of three ``uint16``
  stored as deltas from the identity value, red outermost / blue innermost;
  then optional ``uint32`` primaries, gamma, gamut and ``float64`` min/max amount.

Enum values for primaries/gamma follow the DNG SDK ordering. sRGB/sRGB and
Adobe RGB/gamma 2.2 are confirmed against real presets; the others are
taken from the SDK ordering and not yet confirmed by a sample.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from . import colorspace as cs
from .errors import ParameterError, TableDecodeError
from .lut import Lut3D, apply_lut

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?`'|()[]{}@%$#"
_DIGIT = {c: i for i, c in enumerate(ALPHABET)}
_ENTITIES = (("&quot;", '"'), ("&apos;", "'"), ("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"))

TABLE_TYPE_RGB = 1
TABLE_VERSION = 1
MAX_DIVISIONS = 64


class Primaries(IntEnum):
    SRGB = 0
    ADOBE_RGB = 1
    PROPHOTO = 2
    DISPLAY_P3 = 3
    REC2020 = 4


class Gamma(IntEnum):
    LINEAR = 0
    SRGB = 1
    GAMMA_18 = 2
    GAMMA_22 = 3
    REC2020 = 4


class Gamut(IntEnum):
    CLIP = 0
    EXTEND = 1


_SPACE = {
    Primaries.SRGB: cs.SRGB,
    Primaries.ADOBE_RGB: cs.ADOBE_RGB,
    Primaries.PROPHOTO: cs.PROPHOTO_RGB,
    Primaries.DISPLAY_P3: cs.DISPLAY_P3,
    Primaries.REC2020: cs.REC2020,
}
_TRANSFER = {
    Gamma.LINEAR: cs.Transfer.LINEAR,
    Gamma.SRGB: cs.Transfer.SRGB,
    Gamma.GAMMA_18: cs.Transfer.GAMMA_18,
    Gamma.GAMMA_22: cs.Transfer.GAMMA_22,
    Gamma.REC2020: cs.Transfer.REC2020,
}


@dataclass(frozen=True, eq=False)
class RGBTable:
    lut: Lut3D
    primaries: Primaries
    gamma: Gamma
    gamut: Gamut
    min_amount: float
    max_amount: float

    @property
    def space(self) -> cs.RGBSpace:
        return _SPACE[self.primaries]

    @property
    def transfer(self) -> cs.Transfer:
        return _TRANSFER[self.gamma]


# --- base85 -------------------------------------------------------------------------------

def encode_base85(data: bytes) -> str:
    out = []
    for i in range(0, len(data), 4):
        chunk = data[i : i + 4]
        value = int.from_bytes(chunk.ljust(4, b"\0"), "little")
        digits = []
        for _ in range(5):
            digits.append(ALPHABET[value % 85])
            value //= 85
        out.append("".join(digits[: len(chunk) + 1]))
    return "".join(out)


def decode_base85(text: str) -> bytes:
    text = text.strip()
    for entity, char in _ENTITIES:
        text = text.replace(entity, char)
    out = bytearray()
    for i in range(0, len(text), 5):
        group = text[i : i + 5]
        if len(group) == 1:
            raise TableDecodeError(f"dangling base-85 character at offset {i}")
        value = 0
        for k, ch in enumerate(group):
            try:
                value += _DIGIT[ch] * 85**k
            except KeyError:
                raise TableDecodeError(f"invalid base-85 character {ch!r} at offset {i + k}") from None
        out += (value & 0xFFFFFFFF).to_bytes(4, "little")[: len(group) - 1]
    return bytes(out)


# --- RGBTable ------------------------------------------------------------------------------

def _nominal(divisions: int) -> np.ndarray:
    """Identity value of each lattice index, rounded to nearest (bias = half the divisor)."""
    denom = divisions - 1
    return (np.arange(divisions, dtype=np.int64) * 0xFFFF + (denom >> 1)) // denom


def _add_nominal(samples: np.ndarray, nominal: np.ndarray, sign: int) -> np.ndarray:
    out = samples.astype(np.int64).copy()
    out[..., 0] += sign * nominal[:, None, None]
    out[..., 1] += sign * nominal[None, :, None]
    out[..., 2] += sign * nominal[None, None, :]
    return out & 0xFFFF


def decode_rgb_table(text: str) -> RGBTable:
    blob = decode_base85(text)
    if len(blob) < 4:
        raise TableDecodeError("RGBTable blob is too short")
    declared = int.from_bytes(blob[:4], "little")
    try:
        data = zlib.decompress(blob[4:])
    except zlib.error as exc:
        raise TableDecodeError(f"RGBTable zlib stream is corrupt: {exc}") from exc
    if len(data) != declared:
        raise TableDecodeError(f"RGBTable size mismatch: declared {declared}, got {len(data)}")
    if len(data) < 16:
        raise TableDecodeError("RGBTable header is truncated")
    kind, version, dims, div = struct.unpack_from("<4I", data)
    if kind != TABLE_TYPE_RGB:
        raise TableDecodeError(f"not an RGB table (type {kind})")
    if version != TABLE_VERSION:
        raise TableDecodeError(f"unsupported RGBTable version {version}")
    if dims != 3:
        raise TableDecodeError(f"only 3D RGB tables are supported (got {dims}D)")
    if not 2 <= div <= MAX_DIVISIONS:
        raise TableDecodeError(f"RGBTable divisions {div} outside 2..{MAX_DIVISIONS}")
    count = div**3 * 3
    end = 16 + 2 * count
    if len(data) < end:
        raise TableDecodeError(f"RGBTable samples truncated: need {end} bytes, have {len(data)}")
    deltas = np.frombuffer(data, dtype="<u2", count=count, offset=16).reshape(div, div, div, 3)
    values = _add_nominal(deltas, _nominal(div), +1) / 65535.0

    tail = data[end:]
    primaries, gamma, gamut, lo, hi = Primaries.SRGB, Gamma.SRGB, Gamut.CLIP, 0.0, 1.0
    try:
        if len(tail) >= 12:
            p, g, m = struct.unpack_from("<3I", tail)
            primaries, gamma, gamut = Primaries(p), Gamma(g), Gamut(m)
        if len(tail) >= 28:
            lo, hi = struct.unpack_from("<2d", tail, 12)
    except ValueError as exc:
        raise TableDecodeError(f"unknown RGBTable color space value: {exc}") from exc
    return RGBTable(Lut3D(values), primaries, gamma, gamut, float(lo), float(hi))


def encode_rgb_table(table: RGBTable) -> str:
    div = table.lut.size
    values = np.clip(np.rint(table.lut.table.astype(np.float64) * 65535), 0, 65535).astype(np.int64)
    deltas = _add_nominal(values, _nominal(div), -1).astype("<u2")
    body = (
        struct.pack("<4I", TABLE_TYPE_RGB, TABLE_VERSION, 3, div)
        + deltas.tobytes()
        + struct.pack("<3I2d", table.primaries, table.gamma, table.gamut, table.min_amount, table.max_amount)
    )
    return encode_base85(struct.pack("<I", len(body)) + zlib.compress(body, 9))


def apply_rgb_table(
    table: RGBTable,
    rgb: np.ndarray,
    space: cs.RGBSpace,
    amount: float = 1.0,
    method: str = "tetrahedral",
) -> np.ndarray:
    """Apply ``table`` to *linear* ``rgb`` expressed in ``space``; returns linear float32.

    The lookup happens in the table's own primaries and encoding. ``amount``
    blends between input and table output in that encoded space. With
    ``Gamut.EXTEND`` the out-of-gamut residual is carried through untouched.
    """
    if not table.min_amount <= amount <= table.max_amount:
        raise ParameterError(f"amount {amount} outside table range [{table.min_amount}, {table.max_amount}]")
    lin = cs.convert(rgb, space, table.space)
    clipped = np.clip(lin, 0.0, 1.0)
    residual = lin - clipped if table.gamut is Gamut.EXTEND else 0.0
    enc = cs.encode(clipped, table.transfer)
    looked = apply_lut(table.lut, enc, method=method).astype(np.float64)
    mixed = enc + amount * (looked - enc)
    out = cs.decode(mixed, table.transfer) + residual
    return cs.convert(out, table.space, space).astype(np.float32)
