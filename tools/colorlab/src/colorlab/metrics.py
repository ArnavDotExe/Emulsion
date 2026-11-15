"""Color difference: CIE Lab (D65) from sRGB and the CIEDE2000 formula."""
from __future__ import annotations

import numpy as np

from . import colorspace as cs

_WHITE = cs.SRGB.rgb_to_xyz @ np.ones(3)
_EPS = (6 / 29) ** 3


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB-encoded values (clipped to [0, 1]) to CIE L*a*b* with a D65 white."""
    lin = cs.decode(np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0), cs.Transfer.SRGB)
    t = cs.apply_matrix(lin, cs.SRGB.rgb_to_xyz) / _WHITE
    f = np.where(t > _EPS, np.cbrt(t), t / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def delta_e_2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000 difference between Lab colors (Sharma, Wu & Dalal 2005)."""
    L1, a1, b1 = np.moveaxis(np.asarray(lab1, dtype=np.float64), -1, 0)
    L2, a2, b2 = np.moveaxis(np.asarray(lab2, dtype=np.float64), -1, 0)
    c7 = ((np.hypot(a1, b1) + np.hypot(a2, b2)) / 2) ** 7
    g = 0.5 * (1 - np.sqrt(c7 / (c7 + 25.0**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    chroma_zero = c1p * c2p == 0

    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(chroma_zero, 0.0, dh)
    d_l, d_c = L2 - L1, c2p - c1p
    d_h = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dh) / 2)

    l_bar, c_bar = (L1 + L2) / 2, (c1p + c2p) / 2
    h_sum = h1p + h2p
    h_bar = np.where(np.abs(h1p - h2p) <= 180, h_sum / 2, np.where(h_sum < 360, (h_sum + 360) / 2, (h_sum - 360) / 2))
    h_bar = np.where(chroma_zero, h_sum, h_bar)
    t = (1 - 0.17 * np.cos(np.radians(h_bar - 30)) + 0.24 * np.cos(np.radians(2 * h_bar))
         + 0.32 * np.cos(np.radians(3 * h_bar + 6)) - 0.20 * np.cos(np.radians(4 * h_bar - 63)))
    d_theta = 30 * np.exp(-(((h_bar - 275) / 25) ** 2))
    cb7 = c_bar**7
    r_c = 2 * np.sqrt(cb7 / (cb7 + 25.0**7))
    s_l = 1 + 0.015 * (l_bar - 50) ** 2 / np.sqrt(20 + (l_bar - 50) ** 2)
    s_c = 1 + 0.045 * c_bar
    s_h = 1 + 0.015 * c_bar * t
    r_t = -np.sin(np.radians(2 * d_theta)) * r_c
    lc, cc, hc = d_l / s_l, d_c / s_c, d_h / s_h
    return np.sqrt(lc**2 + cc**2 + hc**2 + r_t * cc * hc)


def delta_e_srgb(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """CIEDE2000 between two sets of sRGB-encoded colors."""
    return delta_e_2000(srgb_to_lab(a), srgb_to_lab(b))
