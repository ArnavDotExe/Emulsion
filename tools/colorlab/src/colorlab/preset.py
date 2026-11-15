"""Lightroom / Camera Raw presets: ``.xmp`` files and mobile ``.dng`` presets.

Only the *top-level* settings are read. Lightroom also embeds a copy of the
applied preset under ``crs:Preset/crs:Parameters``; that copy is ignored.
XML is parsed with :mod:`defusedxml` (no entity expansion, no external DTDs).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET

from .adobe_table import RGBTable, decode_rgb_table
from .errors import PresetFormatError, TableDecodeError

CRS = "{http://ns.adobe.com/camera-raw-settings/1.0/}"
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
IDENTITY_CURVE: tuple[tuple[int, int], ...] = ((0, 0), (255, 255))
PRESET_SUFFIXES = (".xmp", ".dng")
_ALT_FIELDS = ("Name", "Group", "ShortName", "SortName", "Description")
_XMP_TAG = 700


@dataclass(frozen=True, eq=False)
class LookRef:
    """A ``crs:Look`` reference: inline, stubbed (defined by another preset) or Adobe built-in."""

    name: str
    uuid: str
    amount: float
    stubbed: bool
    cluster: str = ""
    table_ref: str | None = None
    table: RGBTable | None = None
    source: Preset | None = None
    parameters: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def resolved(self) -> bool:
        """True when the look's color data is actually available to us."""
        return self.source is not None or self.table is not None or (not self.stubbed and self.table_ref is None)


@dataclass(frozen=True, eq=False)
class Preset:
    name: str
    group: str
    uuid: str
    preset_type: str
    process_version: str
    settings: Mapping[str, str]
    curves: Mapping[str, tuple[tuple[int, int], ...]]
    look: LookRef | None = None
    rgb_table: RGBTable | None = None
    source: Path | None = field(default=None)

    def has(self, key: str) -> bool:
        return key in self.settings

    def text(self, key: str, default: str | None = None) -> str | None:
        return self.settings.get(key, default)

    def number(self, key: str, default: float = 0.0) -> float:
        raw = self.settings.get(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            raise PresetFormatError(f"{self._where()}{key}: not a number: {raw!r}") from None

    def flag(self, key: str) -> bool:
        return self.settings.get(key, "").strip().lower() == "true"

    def curve(self, key: str) -> tuple[tuple[int, int], ...]:
        return self.curves.get(key, IDENTITY_CURVE)

    def _where(self) -> str:
        return f"{self.source}: " if self.source else ""


# --- parsing ----------------------------------------------------------------------------------

def parse_preset(text: str, source: Path | None = None) -> Preset:
    where = f"{source}: " if source else ""
    try:
        root = SafeET.fromstring(text)
    except (ParseError, DefusedXmlException) as exc:
        raise PresetFormatError(f"{where}not a valid XMP document: {exc}") from exc

    rdf = root if root.tag == f"{RDF}RDF" else next(root.iter(f"{RDF}RDF"), None)
    descriptions = [] if rdf is None else rdf.findall(f"{RDF}Description")
    settings: dict[str, str] = {}
    payloads: dict[str, str] = {}
    curves: dict[str, tuple[tuple[int, int], ...]] = {}
    alts: dict[str, str] = {}
    look: LookRef | None = None
    found = False

    for desc in descriptions:
        for key, value in desc.attrib.items():
            if not key.startswith(CRS):
                continue
            found = True
            name = key[len(CRS) :]
            if name.startswith("Table_"):
                payloads[name[len("Table_") :]] = value
            else:
                settings[name] = value
        for child in desc:
            if not child.tag.startswith(CRS):
                continue
            found = True
            name = child.tag[len(CRS) :]
            if name in _ALT_FIELDS:
                alts[name] = _alt_text(child)
            elif name.startswith("ToneCurve"):
                curves[name] = _parse_curve(child, name, where)
            elif name == "Look":
                look = _parse_look(child, where)
            elif len(child) == 0 and child.text and child.text.strip():
                settings[name] = child.text.strip()  # element-form scalar setting

    if not found:
        raise PresetFormatError(f"{where}no Camera Raw (crs:) settings found")

    rgb_table = None
    ref = settings.get("RGBTable")
    if ref:
        if ref not in payloads:
            raise PresetFormatError(f"{where}RGBTable references missing Table_{ref}")
        rgb_table = _decode(payloads[ref], where, f"Table_{ref}")

    return Preset(
        name=alts.get("Name") or (source.stem if source else ""),
        group=alts.get("Group", ""),
        uuid=settings.get("UUID", ""),
        preset_type=settings.get("PresetType", ""),
        process_version=settings.get("ProcessVersion", ""),
        settings=MappingProxyType(settings),
        curves=MappingProxyType(curves),
        look=look,
        rgb_table=rgb_table,
        source=source,
    )


def _alt_text(el: Element) -> str:
    li = el.find(f"{RDF}Alt/{RDF}li")
    return (li.text or "").strip() if li is not None else (el.text or "").strip()


def _parse_curve(el: Element, name: str, where: str) -> tuple[tuple[int, int], ...]:
    points = []
    for li in el.iter(f"{RDF}li"):
        parts = (li.text or "").split(",")
        try:
            if len(parts) != 2:
                raise ValueError
            points.append((int(float(parts[0])), int(float(parts[1]))))
        except ValueError:
            raise PresetFormatError(f"{where}{name}: bad curve point {li.text!r}") from None
    return tuple(points) if points else IDENTITY_CURVE


def _parse_look(el: Element, where: str) -> LookRef:
    desc = el.find(f"{RDF}Description")
    desc = desc if desc is not None else el
    attrs = {k[len(CRS) :]: v for k, v in desc.attrib.items() if k.startswith(CRS)}
    # crs:Parameters appears either with attributes on the element itself
    # (rdf:parseType="Resource" style) or wrapping an rdf:Description.
    parameters: dict[str, str] = {}
    params_el = desc.find(f"{CRS}Parameters")
    if params_el is not None:
        holder = params_el.find(f"{RDF}Description")
        for el in (params_el, holder) if holder is not None else (params_el,):
            parameters.update({k[len(CRS) :]: v for k, v in el.attrib.items() if k.startswith(CRS)})
    merged = {**parameters, **attrs}
    rgb_ref = merged.get("RGBTable")
    ref = rgb_ref or merged.get("LookTable")
    table = None
    if rgb_ref and f"Table_{rgb_ref}" in merged:
        table = _decode(merged[f"Table_{rgb_ref}"], where, f"Look Table_{rgb_ref}")
    try:
        amount = float(attrs.get("Amount", "1"))
    except ValueError:
        raise PresetFormatError(f"{where}Look Amount: not a number: {attrs.get('Amount')!r}") from None
    return LookRef(
        name=attrs.get("Name", ""),
        uuid=attrs.get("UUID", ""),
        amount=amount,
        stubbed=attrs.get("Stubbed", "").strip().lower() == "true",
        cluster=attrs.get("Cluster", ""),
        table_ref=ref,
        table=table,
        parameters=MappingProxyType({k: v for k, v in parameters.items() if not k.startswith("Table_")}),
    )


def _decode(payload: str, where: str, label: str) -> RGBTable:
    try:
        return decode_rgb_table(payload)
    except TableDecodeError as exc:
        raise PresetFormatError(f"{where}{label}: {exc}") from exc


def resolve_looks(presets: Iterable[Preset]) -> list[Preset]:
    """Link stubbed looks to the Look-type preset (same UUID) that defines them."""
    presets = list(presets)
    by_uuid = {p.uuid: p for p in presets if p.preset_type == "Look" and p.uuid}
    out = []
    for p in presets:
        look = p.look
        if look is not None and look.source is None and look.uuid in by_uuid and by_uuid[look.uuid] is not p:
            src = by_uuid[look.uuid]
            p = replace(p, look=replace(look, source=src, table=look.table or src.rgb_table))
        out.append(p)
    return out


# --- files -----------------------------------------------------------------------------------------

def read_dng_xmp(path: str | Path) -> str:
    """Return the XMP packet stored in IFD0 tag 700 of a (mobile preset) DNG."""
    path = Path(path)
    data = path.read_bytes()
    if data[:4] == b"II*\x00":
        bo = "<"
    elif data[:4] == b"MM\x00*":
        bo = ">"
    else:
        raise PresetFormatError(f"{path}: not a TIFF/DNG file")
    try:
        (ifd,) = struct.unpack_from(f"{bo}I", data, 4)
        (count,) = struct.unpack_from(f"{bo}H", data, ifd)
        for k in range(count):
            tag, _typ, n, value = struct.unpack_from(f"{bo}HHII", data, ifd + 2 + 12 * k)
            if tag != _XMP_TAG:
                continue
            start = ifd + 2 + 12 * k + 8 if n <= 4 else value
            if start + n > len(data):
                raise PresetFormatError(f"{path}: XMP packet runs past end of file")
            return data[start : start + n].decode("utf-8").rstrip("\x00")
    except struct.error as exc:
        raise PresetFormatError(f"{path}: truncated TIFF structure") from exc
    except UnicodeDecodeError as exc:
        raise PresetFormatError(f"{path}: XMP packet is not UTF-8") from exc
    raise PresetFormatError(f"{path}: no XMP packet (tag 700) in IFD0")


def load_preset(path: str | Path) -> Preset:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".xmp":
        text = path.read_text(encoding="utf-8-sig")
    elif suffix == ".dng":
        text = read_dng_xmp(path)
    else:
        raise PresetFormatError(f"{path}: unsupported preset type {suffix!r}")
    return parse_preset(text, source=path)


def load_preset_dir(root: str | Path) -> list[Preset]:
    """Load every ``.xmp`` / ``.dng`` preset under ``root`` (sorted) and resolve looks."""
    files = sorted(
        (p for p in Path(root).rglob("*") if p.is_file() and p.suffix.lower() in PRESET_SUFFIXES),
        key=lambda p: str(p).lower(),
    )
    return resolve_looks(load_preset(p) for p in files)
