import struct

import numpy as np
import pytest

from colorlab.adobe_table import Gamma, Gamut, Primaries, RGBTable, encode_rgb_table
from colorlab.errors import PresetFormatError
from colorlab.lut import Lut3D
from colorlab.preset import (
    IDENTITY_CURVE,
    load_preset,
    load_preset_dir,
    parse_preset,
    read_dng_xmp,
    resolve_looks,
)

from conftest import curve_xml, make_xmp


def table_text(fill: float = 0.25) -> str:
    lut = Lut3D(np.full((2, 2, 2, 3), fill))
    return encode_rgb_table(RGBTable(lut, Primaries.SRGB, Gamma.SRGB, Gamut.CLIP, 0.0, 2.0))


# --- basic fields ----------------------------------------------------------------------

def test_parses_identity_fields():
    p = parse_preset(make_xmp({"ProcessVersion": "11.0", "UUID": "ABC"}))
    assert p.name == "Test Preset"
    assert p.group == "Test Group"
    assert p.uuid == "ABC"
    assert p.preset_type == "Normal"
    assert p.process_version == "11.0"


@pytest.mark.parametrize(("raw", "value"), [("+0.14", 0.14), ("-25", -25.0), ("0", 0.0), ("35", 35.0)])
def test_numbers_accept_signed_text(raw, value):
    p = parse_preset(make_xmp({"Exposure2012": raw}))
    assert p.number("Exposure2012") == pytest.approx(value)


def test_missing_number_returns_default():
    p = parse_preset(make_xmp())
    assert p.number("Highlights2012") == 0.0
    assert p.number("GrainSize", default=25.0) == 25.0


def test_non_numeric_value_raises_with_setting_name():
    p = parse_preset(make_xmp({"Exposure2012": "bright"}))
    with pytest.raises(PresetFormatError, match="Exposure2012"):
        p.number("Exposure2012")


@pytest.mark.parametrize(("raw", "value"), [("True", True), ("False", False), ("true", True)])
def test_flags(raw, value):
    assert parse_preset(make_xmp({"ConvertToGrayscale": raw})).flag("ConvertToGrayscale") is value


def test_missing_flag_defaults_false():
    assert parse_preset(make_xmp()).flag("ConvertToGrayscale") is False


def test_text_settings():
    p = parse_preset(make_xmp({"CameraProfile": "Default Color"}))
    assert p.text("CameraProfile") == "Default Color"
    assert p.text("WhiteBalance") is None


def test_has_reports_presence():
    p = parse_preset(make_xmp({"GrainAmount": "35"}))
    assert p.has("GrainAmount")
    assert not p.has("Dehaze")


# --- curves ----------------------------------------------------------------------------

def test_parses_point_curves():
    children = curve_xml("ToneCurvePV2012", [(4, 38), (121, 129), (255, 242)]) + curve_xml(
        "ToneCurvePV2012Red", [(0, 0), (255, 255)]
    )
    p = parse_preset(make_xmp(children=children))
    assert p.curve("ToneCurvePV2012") == ((4, 38), (121, 129), (255, 242))
    assert p.curve("ToneCurvePV2012Red") == ((0, 0), (255, 255))


def test_missing_curve_is_identity():
    assert parse_preset(make_xmp()).curve("ToneCurvePV2012Blue") == IDENTITY_CURVE


def test_malformed_curve_point_raises():
    with pytest.raises(PresetFormatError, match="ToneCurvePV2012"):
        parse_preset(make_xmp(children="<crs:ToneCurvePV2012><rdf:Seq><rdf:li>12</rdf:li></rdf:Seq></crs:ToneCurvePV2012>"))


# --- nested copies are ignored ---------------------------------------------------------

def test_nested_preset_parameters_do_not_override_top_level():
    nested = """<crs:Preset><rdf:Description crs:Name="Inner">
      <crs:Parameters><rdf:Description crs:Exposure2012="+3.00" crs:Grain="1"/></crs:Parameters>
    </rdf:Description></crs:Preset>"""
    p = parse_preset(make_xmp({"Exposure2012": "+0.50"}, children=nested))
    assert p.number("Exposure2012") == 0.5
    assert not p.has("Grain")


# --- looks and tables -------------------------------------------------------------------

def test_stubbed_look_reference():
    look = """<crs:Look><rdf:Description crs:Name="Kodak Portra 400" crs:Amount="1.000000"
       crs:UUID="96DE" crs:Stubbed="true"/></crs:Look>"""
    p = parse_preset(make_xmp(children=look))
    assert p.look is not None
    assert (p.look.name, p.look.uuid, p.look.amount, p.look.stubbed) == ("Kodak Portra 400", "96DE", 1.0, True)
    assert p.look.table is None


def test_embedded_look_table_is_decoded():
    look = f"""<crs:Look><rdf:Description crs:Name="Inline" crs:Amount="0.9" crs:UUID="11"
       crs:RGBTable="AA" crs:Table_AA="{table_text()}"/></crs:Look>"""
    p = parse_preset(make_xmp(children=look))
    assert p.look.amount == pytest.approx(0.9)
    assert p.look.table is not None
    assert p.look.table.lut.table[0, 0, 0, 0] == pytest.approx(0.25, abs=1e-4)


def test_top_level_rgb_table_is_decoded():
    p = parse_preset(make_xmp({"PresetType": "Look", "RGBTable": "BEEF", "Table_BEEF": table_text(0.5)}))
    assert p.rgb_table is not None
    assert p.rgb_table.lut.size == 2
    assert not p.has("Table_BEEF")  # bulky payloads are not kept as settings


@pytest.mark.parametrize("nested", [False, True])
def test_builtin_look_parameters_are_read_in_both_rdf_forms(nested):
    attrs = 'crs:LookTable="E109" crs:RGBTable="CD58" crs:RGBTableAmount="0.75"'
    params = (
        f"<crs:Parameters><rdf:Description {attrs}/></crs:Parameters>" if nested else f"<crs:Parameters {attrs}/>"
    )
    look = f"""<crs:Look><rdf:Description crs:Name="Vintage 05" crs:Amount="0.9" crs:Cluster="Adobe"
       crs:UUID="981D">{params}</rdf:Description></crs:Look>"""
    p = parse_preset(make_xmp(children=look))
    assert p.look.table_ref == "CD58"
    assert p.look.parameters["RGBTableAmount"] == "0.75"
    assert not p.look.stubbed
    assert not p.look.resolved  # Adobe built-in: its table data is not in the file


def test_rgb_table_reference_without_payload_raises():
    with pytest.raises(PresetFormatError, match="Table_DEAD"):
        parse_preset(make_xmp({"RGBTable": "DEAD"}))


def test_no_look_when_absent():
    assert parse_preset(make_xmp()).look is None


# --- resolving stubbed looks --------------------------------------------------------------

def test_resolve_looks_links_stub_to_look_preset():
    profile = parse_preset(make_xmp({"PresetType": "Look", "UUID": "LOOK1", "RGBTable": "T", "Table_T": table_text(0.75)}))
    stub = """<crs:Look><rdf:Description crs:Name="Film" crs:Amount="1.0" crs:UUID="LOOK1" crs:Stubbed="true"/></crs:Look>"""
    user = parse_preset(make_xmp({"UUID": "USER1"}, children=stub))
    resolved = {p.uuid: p for p in resolve_looks([profile, user])}
    look = resolved["USER1"].look
    assert look.resolved
    assert look.source is not None and look.source.uuid == "LOOK1"
    assert look.table is not None and look.table.lut.table[0, 0, 0, 0] == pytest.approx(0.75, abs=1e-4)


def test_resolve_looks_leaves_unknown_stub_unresolved():
    stub = """<crs:Look><rdf:Description crs:Name="Vintage 05" crs:Amount="0.9" crs:UUID="ADOBE" crs:Stubbed="true"/></crs:Look>"""
    [p] = resolve_looks([parse_preset(make_xmp(children=stub))])
    assert not p.look.resolved
    assert p.look.table is None


def test_resolve_looks_does_not_mutate_inputs():
    profile = parse_preset(make_xmp({"PresetType": "Look", "UUID": "L", "RGBTable": "T", "Table_T": table_text()}))
    user = parse_preset(make_xmp(children='<crs:Look><rdf:Description crs:Name="x" crs:UUID="L" crs:Stubbed="true"/></crs:Look>'))
    resolve_looks([profile, user])
    assert user.look.table is None


# --- invalid documents ------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "not xml at all",
        "<root/>",
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/></x:xmpmeta>',
    ],
)
def test_invalid_documents_raise(text):
    with pytest.raises(PresetFormatError):
        parse_preset(text)


def test_entity_expansion_is_refused():
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
    with pytest.raises(PresetFormatError):
        parse_preset(evil)


# --- files ----------------------------------------------------------------------------------

def tiff_with_xmp(xmp: bytes, big_endian: bool = False) -> bytes:
    bo = ">" if big_endian else "<"
    magic = b"MM\x00*" if big_endian else b"II*\x00"
    ifd_offset = 8
    data_offset = ifd_offset + 2 + 12 + 4
    ifd = struct.pack(f"{bo}H", 1) + struct.pack(f"{bo}HHII", 700, 7, len(xmp), data_offset) + struct.pack(f"{bo}I", 0)
    return magic + struct.pack(f"{bo}I", ifd_offset) + ifd + xmp


@pytest.mark.parametrize("big_endian", [False, True])
def test_read_dng_xmp_from_tag_700(tmp_path, big_endian):
    xmp = make_xmp({"Exposure2012": "+1.00"}).encode()
    path = tmp_path / "p.dng"
    path.write_bytes(tiff_with_xmp(xmp, big_endian))
    assert read_dng_xmp(path) == xmp.decode()


def test_read_dng_xmp_rejects_non_tiff(tmp_path):
    path = tmp_path / "p.dng"
    path.write_bytes(b"GIF89a....")
    with pytest.raises(PresetFormatError):
        read_dng_xmp(path)


def test_read_dng_xmp_requires_tag_700(tmp_path):
    path = tmp_path / "p.dng"
    path.write_bytes(b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 0) + struct.pack("<I", 0))
    with pytest.raises(PresetFormatError, match="XMP"):
        read_dng_xmp(path)


def test_load_preset_reads_xmp_and_dng(tmp_path):
    (tmp_path / "a.xmp").write_text("﻿" + make_xmp({"Exposure2012": "+0.25"}), encoding="utf-8")
    (tmp_path / "b.dng").write_bytes(tiff_with_xmp(make_xmp({"Exposure2012": "-0.25"}).encode()))
    assert load_preset(tmp_path / "a.xmp").number("Exposure2012") == 0.25
    dng = load_preset(tmp_path / "b.dng")
    assert dng.number("Exposure2012") == -0.25
    assert dng.source == tmp_path / "b.dng"


def test_load_preset_rejects_unknown_suffix(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("x")
    with pytest.raises(PresetFormatError):
        load_preset(path)


def test_load_preset_dir_is_recursive_sorted_and_resolved(tmp_path):
    (tmp_path / "pack").mkdir()
    (tmp_path / "pack" / "profile.xmp").write_text(
        make_xmp({"PresetType": "Look", "UUID": "L", "RGBTable": "T", "Table_T": table_text()}), encoding="utf-8"
    )
    stub = '<crs:Look><rdf:Description crs:Name="x" crs:UUID="L" crs:Stubbed="true"/></crs:Look>'
    (tmp_path / "pack" / "user.xmp").write_text(make_xmp({"UUID": "U"}, children=stub), encoding="utf-8")
    presets = load_preset_dir(tmp_path)
    assert [p.source.name for p in presets] == ["profile.xmp", "user.xmp"]
    assert presets[1].look.resolved
