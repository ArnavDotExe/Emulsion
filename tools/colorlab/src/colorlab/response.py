"""Lightroom's measured slider responses.

For some sliders the shape comes straight from single-slider Lightroom bakes
(``python -m colorlab.calibration response``) rather than from a formula:

* Tone sliders (Contrast, Whites, Blacks and the four parametric regions): the
  neutral curve at each baked amount, sampled at ``SAMPLES`` even points. Between
  baked amounts the black and white clip points move linearly and the shape
  between them is blended, which keeps every curve monotonic. Amounts outside
  the baked range clamp to its ends.
* Exposure: a shoulder ``y = T(2**(strength * ev) * T⁻¹(x))`` with
  ``T(s) = 1 - (1 + s/p)**-p``. Black and white stay put, highlights roll off,
  and nothing clips.
* Vibrance: a chroma gain against chroma, scaled per hue band, applied around a
  pivot between each color's min and max channel.
* White balance (incremental temperature/tint): stops per channel on the exposure
  shoulder, per baked amount.

The numbers are measurements of Lightroom's behaviour on synthetic calibration
images; they contain nothing from any preset.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import numpy as np

from .errors import ColorlabError

SAMPLES = 64
X = np.arange(SAMPLES) / (SAMPLES - 1)
TONE_KEYS = ("Contrast2012", "Whites2012", "Blacks2012",
             "ParametricShadows", "ParametricDarks", "ParametricLights", "ParametricHighlights")

_CLIP_EPS = 1e-3  # a measured value this close to 0 or 1 counts as clipped


def _bracket(amounts: list[float], amount: float) -> tuple[float, float, float]:
    """Neighbouring measured amounts around ``amount`` (clamped) and the blend between them."""
    a = float(np.clip(amount, amounts[0], amounts[-1]))
    j = int(np.clip(np.searchsorted(amounts, a), 1, len(amounts) - 1))
    a0, a1 = amounts[j - 1], amounts[j]
    return a0, a1, (a - a0) / (a1 - a0)


def _as_float_map(table: Mapping[float, np.ndarray], length: int | None, what: str) -> dict[float, np.ndarray]:
    out = {}
    for amount, values in table.items():
        arr = np.asarray(values, dtype=np.float64)
        if arr.ndim != 1 or (length is not None and len(arr) != length):
            raise ColorlabError(f"{what} at {amount:g} has {arr.size} values, expected {length}")
        if float(amount) == 0:
            raise ColorlabError(f"{what}: amount 0 is the identity and must not be listed")
        out[float(amount)] = arr
    if not out:
        raise ColorlabError(f"{what} lists no amounts")
    return out


# --- tone sliders ---------------------------------------------------------------------------------

def _clip_points(y: np.ndarray) -> tuple[float, float]:
    """Where the curve leaves black (last x still at 0) and reaches white (first x at 1)."""
    black = np.flatnonzero(np.cumprod(y <= _CLIP_EPS))
    white = np.flatnonzero(np.cumprod((y >= 1 - _CLIP_EPS)[::-1]))
    b = X[black[-1]] if len(black) else 0.0
    w = X[SAMPLES - 1 - white[-1]] if len(white) else 1.0
    return (0.0, 1.0) if w <= b else (float(b), float(w))


@dataclass(frozen=True, eq=False)
class CurveFamily:
    """Neutral curves of one tone slider at its baked amounts (identity at 0 is implied).

    Between amounts the black and white clip points move linearly and the shape
    between them is blended, so a curve that crushes [0, 0.1] at -50 and
    [0, 0.3] at -100 crushes about [0, 0.2] at -75.
    """

    curves: Mapping[float, np.ndarray]
    _parts: dict[float, tuple[float, float, np.ndarray]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        curves = _as_float_map(self.curves, SAMPLES, "tone curve")
        object.__setattr__(self, "curves", curves)
        parts = {0.0: (0.0, 1.0, X.copy())}
        for a, y in curves.items():
            y = np.maximum.accumulate(y)
            b, w = _clip_points(y)
            parts[a] = (b, w, np.interp(b + X * (w - b), X, y))  # the shape between the clip points
        object.__setattr__(self, "_parts", parts)

    def curve(self, amount: float) -> np.ndarray:
        """The neutral curve at ``amount``, sampled at ``X``."""
        a0, a1, t = _bracket(sorted(self._parts), amount)
        for a, weight in ((a0, 1 - t), (a1, t)):
            if weight == 1:
                return X.copy() if a == 0 else self.curves[a].copy()
        (b0, w0, g0), (b1, w1, g1) = self._parts[a0], self._parts[a1]
        b, w = (1 - t) * b0 + t * b1, (1 - t) * w0 + t * w1
        return np.interp(np.clip((X - b) / (w - b), 0.0, 1.0), X, (1 - t) * g0 + t * g1)

    def apply(self, values: np.ndarray, amount: float) -> np.ndarray:
        """Apply the curve at ``amount`` to every value (encoded, 0-1)."""
        if amount == 0:
            return values
        return np.interp(np.clip(values, 0.0, 1.0), X, self.curve(amount))


# --- exposure -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ExposureShoulder:
    """Exposure as a stretch of ``T⁻¹(x)``; ``shoulder`` is p, ``strength`` scales the stops."""

    shoulder: float
    strength: float

    def apply(self, values: np.ndarray, ev: float | np.ndarray) -> np.ndarray:
        """``ev`` stops on every value, or one ``ev`` per channel (broadcast over the last axis)."""
        ev = np.asarray(ev, dtype=np.float64)
        if not np.any(ev):
            return values
        p = self.shoulder
        x = np.clip(values, 0.0, 1.0)
        s = p * (np.maximum(1.0 - x, 1e-300) ** (-1.0 / p) - 1.0) * 2.0 ** (self.strength * ev)
        out = np.where(x >= 1.0, 1.0, 1.0 - (1.0 + s / p) ** -p)
        return np.where(ev == 0, values, out)  # channels with no stops pass through exactly

    def stops(self, before: np.ndarray, after: np.ndarray) -> np.ndarray:
        """The stops that take ``before`` to ``after`` (inverse of ``apply``; values inside (0, 1))."""
        def stretch(v):
            return self.shoulder * ((1.0 - v) ** (-1.0 / self.shoulder) - 1.0)
        return np.log2(stretch(after) / stretch(before)) / self.strength


# --- white balance --------------------------------------------------------------------------------

def _table_at(table: Mapping[float, np.ndarray], amount: float) -> np.ndarray:
    zero = np.zeros_like(next(iter(table.values())))
    a0, a1, t = _bracket(sorted([0.0, *table]), amount)
    v0, v1 = (zero if a == 0 else table[a] for a in (a0, a1))
    return (1 - t) * v0 + t * v1


@dataclass(frozen=True, eq=False)
class WhiteBalanceResponse:
    """Incremental temperature/tint as stops per channel (R, G, B) on the exposure shoulder.

    On a rendered image Lightroom brightens each channel along the same shoulder as
    exposure, so white stays white. Temperature and tint add in stops.
    """

    temperature: Mapping[float, np.ndarray]
    tint: Mapping[float, np.ndarray]

    def __post_init__(self) -> None:
        object.__setattr__(self, "temperature", _as_float_map(self.temperature, 3, "temperature stops"))
        object.__setattr__(self, "tint", _as_float_map(self.tint, 3, "tint stops"))

    def evs(self, temperature: float = 0.0, tint: float = 0.0) -> np.ndarray:
        out = np.zeros(3)
        if temperature:
            out = out + _table_at(self.temperature, temperature)
        if tint:
            out = out + _table_at(self.tint, tint)
        return out


# --- vibrance -------------------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class VibranceResponse:
    """Vibrance chroma gain per baked amount, against ``chroma`` knots and the eight hue bands."""

    chroma: tuple[float, ...]
    pivots: tuple[float, float]  # where between min (0) and max (1) channel: negative, positive amounts
    gains: Mapping[float, np.ndarray]
    bands: Mapping[float, np.ndarray]

    def __post_init__(self) -> None:
        object.__setattr__(self, "chroma", tuple(float(c) for c in self.chroma))
        object.__setattr__(self, "pivots", (float(self.pivots[0]), float(self.pivots[1])))
        object.__setattr__(self, "gains", _as_float_map(self.gains, len(self.chroma), "vibrance gain"))
        object.__setattr__(self, "bands", _as_float_map(self.bands, 8, "vibrance band factors"))
        if set(self.gains) != set(self.bands):
            raise ColorlabError("vibrance gains and band factors must list the same amounts")

    def pivot(self, amount: float) -> float:
        return self.pivots[0] if amount < 0 else self.pivots[1]

    def _at(self, amount: float, chroma: np.ndarray, band_weights: np.ndarray) -> np.ndarray:
        if amount == 0:
            return np.ones_like(chroma)
        base = np.interp(chroma, self.chroma, self.gains[amount])
        return 1.0 + (base - 1.0) * (band_weights @ self.bands[amount])

    def gain(self, chroma: np.ndarray, band_weights: np.ndarray, amount: float) -> np.ndarray:
        """Chroma gain for colors of ``chroma`` with hue-band ``band_weights`` (last axis 8)."""
        chroma = np.asarray(chroma, dtype=np.float64)
        if amount == 0:
            return np.ones_like(chroma)
        a0, a1, t = _bracket(sorted([0.0, *self.gains]), amount)
        return (1 - t) * self._at(a0, chroma, band_weights) + t * self._at(a1, chroma, band_weights)


# --- the whole set --------------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class Response:
    exposure: ExposureShoulder
    tone: Mapping[str, CurveFamily]
    vibrance: VibranceResponse
    white_balance: WhiteBalanceResponse

    def to_dict(self) -> dict:
        def table(m: Mapping[float, np.ndarray], digits: int) -> dict[str, list[float]]:
            return {f"{a:g}": [round(float(v), digits) for v in arr] for a, arr in sorted(m.items())}

        return {
            "format": 1,
            "samples": SAMPLES,
            "exposure": {"shoulder": self.exposure.shoulder, "strength": self.exposure.strength},
            "tone": {key: table(self.tone[key].curves, 6) for key in TONE_KEYS},
            "vibrance": {
                "chroma": list(self.vibrance.chroma),
                "pivots": list(self.vibrance.pivots),
                "gains": table(self.vibrance.gains, 5),
                "bands": table(self.vibrance.bands, 5),
            },
            "white_balance": {
                "temperature": table(self.white_balance.temperature, 5),
                "tint": table(self.white_balance.tint, 5),
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> Response:
        try:
            if data.get("samples") != SAMPLES:
                raise ColorlabError(f"response has {data.get('samples')} samples per curve, expected {SAMPLES}")
            missing = [k for k in TONE_KEYS if k not in data["tone"]]
            if missing:
                raise ColorlabError(f"response is missing tone families: {', '.join(missing)}")

            def table(d: Mapping[str, list[float]]) -> dict[float, np.ndarray]:
                return {float(a): np.asarray(v, dtype=np.float64) for a, v in d.items()}

            vib, wb = data["vibrance"], data["white_balance"]
            return cls(
                white_balance=WhiteBalanceResponse(table(wb["temperature"]), table(wb["tint"])),
                exposure=ExposureShoulder(float(data["exposure"]["shoulder"]), float(data["exposure"]["strength"])),
                tone={k: CurveFamily(table(data["tone"][k])) for k in TONE_KEYS},
                vibrance=VibranceResponse(tuple(vib["chroma"]), tuple(vib["pivots"]),
                                          table(vib["gains"]), table(vib["bands"])),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ColorlabError(f"malformed slider response data: {exc!r}") from exc


def identity() -> Response:
    """A response in which every measured slider does nothing; the base for a first measurement."""
    flat = {100.0: X.copy()}
    return Response(
        exposure=ExposureShoulder(shoulder=5.0, strength=1.0),
        tone={k: CurveFamily(flat) for k in TONE_KEYS},
        vibrance=VibranceResponse(chroma=(0.0, 1.0), pivots=(0.5, 0.5),
                                  gains={100.0: np.ones(2)}, bands={100.0: np.ones(8)}),
        white_balance=WhiteBalanceResponse(temperature={100.0: np.zeros(3)}, tint={100.0: np.zeros(3)}),
    )


def default_path() -> Path:
    return Path(str(resources.files("colorlab") / "data" / "lightroom_response.json"))


def load(path: str | Path | None = None) -> Response:
    """Read a response file; by default the one shipped with colorlab."""
    path = Path(path) if path is not None else default_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ColorlabError(f"cannot read slider responses from {path}: {exc}") from exc
    return Response.from_dict(data)


def save(response: Response, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(response.to_dict(), indent=1) + "\n", encoding="utf-8")
    return path
