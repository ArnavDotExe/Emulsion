"""RGB color spaces, Bradford-adapted conversions and transfer functions.

All arrays are ``(..., 3)`` RGB. Conversions operate on *linear* values;
use :func:`encode` / :func:`decode` to move to and from a transfer curve.
Transfer functions are odd-symmetric so extended-range (negative) values
from wide-gamut conversions survive a round trip.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import cached_property

import numpy as np

_BRADFORD = np.array(
    [
        [0.8951, 0.2664, -0.1614],
        [-0.7502, 1.7135, 0.0367],
        [0.0389, -0.0685, 1.0296],
    ]
)


def _xy_to_xyz(xy: tuple[float, float]) -> np.ndarray:
    x, y = xy
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def _frozen(a: np.ndarray) -> np.ndarray:
    a.flags.writeable = False
    return a


@dataclass(frozen=True)
class RGBSpace:
    """An RGB color space defined by CIE xy primaries and white point."""

    name: str
    primaries: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    white: tuple[float, float]

    @cached_property
    def rgb_to_xyz(self) -> np.ndarray:
        p = np.stack([_xy_to_xyz(c) for c in self.primaries], axis=1)
        scale = np.linalg.solve(p, _xy_to_xyz(self.white))
        return _frozen(p * scale)

    @cached_property
    def xyz_to_rgb(self) -> np.ndarray:
        return _frozen(np.linalg.inv(self.rgb_to_xyz))


D65 = (0.3127, 0.3290)
D50 = (0.3457, 0.3585)

SRGB = RGBSpace("sRGB", ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)), D65)
ADOBE_RGB = RGBSpace("Adobe RGB (1998)", ((0.64, 0.33), (0.21, 0.71), (0.15, 0.06)), D65)
DISPLAY_P3 = RGBSpace("Display P3", ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060)), D65)
PROPHOTO_RGB = RGBSpace("ProPhoto RGB", ((0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)), D50)
REC2020 = RGBSpace("Rec.2020", ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)), D65)


def bradford(src_white: tuple[float, float], dst_white: tuple[float, float]) -> np.ndarray:
    """XYZ -> XYZ chromatic adaptation matrix (Bradford)."""
    if src_white == dst_white:
        return np.eye(3)
    src = _BRADFORD @ _xy_to_xyz(src_white)
    dst = _BRADFORD @ _xy_to_xyz(dst_white)
    return np.linalg.inv(_BRADFORD) @ np.diag(dst / src) @ _BRADFORD


def conversion_matrix(src: RGBSpace, dst: RGBSpace) -> np.ndarray:
    """Linear RGB(src) -> linear RGB(dst) matrix, Bradford-adapted between white points."""
    if src == dst:
        return np.eye(3)
    return dst.xyz_to_rgb @ bradford(src.white, dst.white) @ src.rgb_to_xyz


def apply_matrix(rgb: np.ndarray, m: np.ndarray) -> np.ndarray:
    return np.asarray(rgb, dtype=np.float64) @ m.T


def convert(rgb: np.ndarray, src: RGBSpace, dst: RGBSpace) -> np.ndarray:
    """Convert linear RGB between spaces. Shape is preserved."""
    return apply_matrix(rgb, conversion_matrix(src, dst))


class Transfer(Enum):
    LINEAR = "linear"
    SRGB = "srgb"
    GAMMA_18 = "gamma1.8"
    GAMMA_22 = "gamma2.2"
    PROPHOTO = "romm"  # ProPhoto/ROMM: gamma 1.8 with a linear toe
    REC2020 = "rec2020"


_REC_A = 1.09929682680944
_REC_B = 0.018053968510807


def _encode_abs(a: np.ndarray, tf: Transfer) -> np.ndarray:
    if tf is Transfer.LINEAR:
        return a
    if tf is Transfer.SRGB:
        return np.where(a <= 0.0031308, 12.92 * a, 1.055 * np.power(a, 1 / 2.4) - 0.055)
    if tf is Transfer.GAMMA_18:
        return np.power(a, 1 / 1.8)
    if tf is Transfer.GAMMA_22:
        return np.power(a, 1 / 2.2)
    if tf is Transfer.PROPHOTO:
        return np.where(a < 1 / 512, 16.0 * a, np.power(a, 1 / 1.8))
    if tf is Transfer.REC2020:
        return np.where(a < _REC_B, 4.5 * a, _REC_A * np.power(a, 0.45) - (_REC_A - 1))
    raise ValueError(f"unknown transfer {tf!r}")  # pragma: no cover - Enum is closed


def _decode_abs(v: np.ndarray, tf: Transfer) -> np.ndarray:
    if tf is Transfer.LINEAR:
        return v
    if tf is Transfer.SRGB:
        return np.where(v <= 0.04045, v / 12.92, np.power((v + 0.055) / 1.055, 2.4))
    if tf is Transfer.GAMMA_18:
        return np.power(v, 1.8)
    if tf is Transfer.GAMMA_22:
        return np.power(v, 2.2)
    if tf is Transfer.PROPHOTO:
        return np.where(v < 16 / 512, v / 16.0, np.power(v, 1.8))
    if tf is Transfer.REC2020:
        return np.where(v < 4.5 * _REC_B, v / 4.5, np.power((v + _REC_A - 1) / _REC_A, 1 / 0.45))
    raise ValueError(f"unknown transfer {tf!r}")  # pragma: no cover


def encode(linear: np.ndarray, tf: Transfer) -> np.ndarray:
    """Linear -> encoded (odd-symmetric for negative values)."""
    x = np.asarray(linear, dtype=np.float64)
    return np.sign(x) * _encode_abs(np.abs(x), tf)


def decode(encoded: np.ndarray, tf: Transfer) -> np.ndarray:
    """Encoded -> linear (odd-symmetric for negative values)."""
    v = np.asarray(encoded, dtype=np.float64)
    return np.sign(v) * _decode_abs(np.abs(v), tf)
