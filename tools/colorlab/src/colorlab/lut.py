"""3D LUTs: the in-memory table, interpolation, .cube files and Hald CLUT images.

A :class:`Lut3D` table is indexed ``table[r, g, b] -> (R, G, B)`` over the unit
cube. ``.cube`` files and Hald images both store red as the fastest-varying axis.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .errors import LutFormatError

_METHODS = ("tetrahedral", "trilinear")


@dataclass(frozen=True, eq=False)
class Lut3D:
    table: np.ndarray
    title: str = ""

    def __post_init__(self) -> None:
        t = np.array(self.table, dtype=np.float32)  # always a private copy
        if t.ndim != 4 or t.shape[3] != 3 or not (t.shape[0] == t.shape[1] == t.shape[2]):
            raise LutFormatError(f"LUT table must have shape (N, N, N, 3), got {t.shape}")
        if t.shape[0] < 2:
            raise LutFormatError(f"LUT size must be at least 2, got {t.shape[0]}")
        if not np.all(np.isfinite(t)):
            raise LutFormatError("LUT table contains NaN or infinite values")
        t.flags.writeable = False
        object.__setattr__(self, "table", t)

    @property
    def size(self) -> int:
        return int(self.table.shape[0])

    @classmethod
    def identity(cls, size: int, title: str = "") -> Lut3D:
        if size < 2:
            raise LutFormatError(f"LUT size must be at least 2, got {size}")
        axis = np.linspace(0.0, 1.0, size)
        return cls(np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1), title)


# --- interpolation -----------------------------------------------------------------------

def apply_lut(lut: Lut3D, rgb: np.ndarray, method: str = "tetrahedral") -> np.ndarray:
    """Look up ``rgb`` (any ``(..., 3)`` shape, clamped to [0, 1]); returns float32."""
    if method not in _METHODS:
        raise ValueError(f"unknown interpolation {method!r}; expected one of {_METHODS}")
    rgb = np.asarray(rgb, dtype=np.float64)
    if rgb.shape[-1] != 3:
        raise ValueError(f"expected (..., 3) RGB input, got shape {rgb.shape}")
    shape = rgb.shape
    n = lut.size
    x = np.clip(rgb.reshape(-1, 3), 0.0, 1.0) * (n - 1)
    i0 = np.minimum(np.floor(x).astype(np.intp), n - 2)
    f = x - i0
    table = lut.table.astype(np.float64)
    out = _tetrahedral(table, i0, f) if method == "tetrahedral" else _trilinear(table, i0, f)
    return out.reshape(shape).astype(np.float32)


def _trilinear(t: np.ndarray, i0: np.ndarray, f: np.ndarray) -> np.ndarray:
    out = np.zeros((i0.shape[0], 3))
    for dr in (0, 1):
        wr = f[:, 0] if dr else 1 - f[:, 0]
        for dg in (0, 1):
            wg = f[:, 1] if dg else 1 - f[:, 1]
            for db in (0, 1):
                wb = f[:, 2] if db else 1 - f[:, 2]
                corner = t[i0[:, 0] + dr, i0[:, 1] + dg, i0[:, 2] + db]
                out += (wr * wg * wb)[:, None] * corner
    return out


def _tetrahedral(t: np.ndarray, i0: np.ndarray, f: np.ndarray) -> np.ndarray:
    # Walk from the base corner towards (1,1,1), stepping along the axis with the
    # largest fractional part first; weights are the gaps between sorted fractions.
    order = np.argsort(-f, axis=1, kind="stable")
    fs = np.take_along_axis(f, order, axis=1)
    weights = (1 - fs[:, 0], fs[:, 0] - fs[:, 1], fs[:, 1] - fs[:, 2], fs[:, 2])
    idx = i0.copy()
    rows = np.arange(idx.shape[0])
    out = weights[0][:, None] * t[idx[:, 0], idx[:, 1], idx[:, 2]]
    for k in range(3):
        idx[rows, order[:, k]] += 1
        out += weights[k + 1][:, None] * t[idx[:, 0], idx[:, 1], idx[:, 2]]
    return out


# --- .cube ----------------------------------------------------------------------------------

def format_cube(lut: Lut3D) -> str:
    lines = []
    if lut.title:
        lines.append(f'TITLE "{lut.title}"')
    lines += [f"LUT_3D_SIZE {lut.size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    rows = lut.table.transpose(2, 1, 0, 3).reshape(-1, 3)  # red fastest
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in rows]
    return "\n".join(lines) + "\n"


def parse_cube(text: str) -> Lut3D:
    size: int | None = None
    title = ""
    rows: list[tuple[float, float, float]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, _, rest = line.partition(" ")
        if head == "TITLE":
            title = rest.strip().strip('"')
        elif head == "LUT_3D_SIZE":
            size = _parse_int(rest, lineno)
        elif head == "LUT_1D_SIZE":
            raise LutFormatError(f"line {lineno}: 1D .cube LUTs are not supported")
        elif head in ("DOMAIN_MIN", "DOMAIN_MAX"):
            expected = 0.0 if head == "DOMAIN_MIN" else 1.0
            values = _parse_floats(rest.split(), lineno)
            if len(values) != 3 or any(abs(v - expected) > 1e-9 for v in values):
                raise LutFormatError(f"line {lineno}: only a unit domain is supported, got {head} {rest}")
        elif head[0].isalpha() or head[0] == "_":
            continue  # other vendor keywords (e.g. LUT_3D_INPUT_RANGE handled loosely)
        else:
            values = _parse_floats(line.split(), lineno)
            if len(values) != 3:
                raise LutFormatError(f"line {lineno}: expected 3 values, got {len(values)}")
            rows.append((values[0], values[1], values[2]))
    if size is None:
        raise LutFormatError("missing LUT_3D_SIZE")
    if len(rows) != size**3:
        raise LutFormatError(f"expected {size ** 3} rows for LUT_3D_SIZE {size}, got {len(rows)}")
    data = np.array(rows).reshape(size, size, size, 3).transpose(2, 1, 0, 3)
    return Lut3D(data, title)


def _parse_int(text: str, lineno: int) -> int:
    try:
        return int(text.strip())
    except ValueError as exc:
        raise LutFormatError(f"line {lineno}: not an integer: {text!r}") from exc


def _parse_floats(parts: list[str], lineno: int) -> list[float]:
    try:
        return [float(p) for p in parts]
    except ValueError as exc:
        raise LutFormatError(f"line {lineno}: not numeric: {' '.join(parts)!r}") from exc


def read_cube(path: str | Path) -> Lut3D:
    return parse_cube(Path(path).read_text(encoding="utf-8-sig"))


def write_cube(lut: Lut3D, path: str | Path) -> None:
    Path(path).write_text(format_cube(lut), encoding="utf-8")


# --- Hald CLUT ----------------------------------------------------------------------------------

def hald_identity(level: int) -> np.ndarray:
    """Identity Hald image for ``level`` (cube size level**2, image level**3 square)."""
    if level < 2:
        raise LutFormatError(f"Hald level must be at least 2, got {level}")
    n = level * level
    width = level**3
    idx = np.arange(n**3)
    rgb = np.stack([idx % n, (idx // n) % n, idx // (n * n)], axis=-1) / (n - 1)
    return rgb.reshape(width, width, 3).astype(np.float32)


def lut_from_hald(image: np.ndarray) -> Lut3D:
    """Recover a LUT from a (processed) Hald identity image."""
    img = np.asarray(image, dtype=np.float64)
    if img.ndim != 3 or img.shape[2] != 3 or img.shape[0] != img.shape[1]:
        raise LutFormatError(f"Hald image must be square (W, W, 3), got {img.shape}")
    width = img.shape[0]
    level = round(width ** (1 / 3))
    if level < 2 or level**3 != width:
        raise LutFormatError(f"Hald image width {width} is not a cube of an integer level >= 2")
    n = level * level
    return Lut3D(img.reshape(n, n, n, 3).transpose(2, 1, 0, 3))
