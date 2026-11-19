"""The .fsim.json look format: a preset's global colour as a 3D LUT plus its spatial passes."""
import base64
import json

import numpy as np
import pytest
from conftest import make_xmp

from colorlab import finish
from colorlab.errors import ColorlabError
from colorlab.lut import Lut3D
from colorlab.preset import parse_preset

RNG = np.random.default_rng(5)
IMAGE = np.clip(RNG.random((48, 72, 3)) * 0.6 + np.linspace(0.1, 0.4, 72)[None, :, None], 0, 1)
SPATIAL = {"Clarity2012": "+30", "Texture": "-20", "Shadows2012": "+40", "Highlights2012": "-25",
           "PostCropVignetteAmount": "-30", "PostCropVignetteFeather": "70", "GrainAmount": "20",
           "GrainSize": "30", "Sharpness": "40", "LuminanceSmoothing": "10"}


def preset(attrs):
    return parse_preset(make_xmp({"ProcessVersion": "15.4", **attrs}))


def test_a_look_from_a_preset_round_trips_through_its_json_text():
    from colorlab.fsim import FORMAT_VERSION, dumps, from_preset, loads
    look = from_preset(preset({"Saturation": "+20", "Contrast2012": "+15", **SPATIAL}), name="Test Look", lut_size=9)
    text = dumps(look)
    doc = json.loads(text)
    assert doc["format"] == "fsim" and doc["version"] == FORMAT_VERSION == 1 and doc["name"] == "Test Look"
    assert doc["local"]["clarity"] == 30 and doc["vignette"]["amount"] == -30 and doc["grain"]["size"] == 30
    assert "dehaze" not in doc["local"]  # defaults are left out
    back = loads(text)
    assert back.name == "Test Look" and back.local == look.local and back.vignette == look.vignette
    np.testing.assert_allclose(back.lut.table, look.lut.table, atol=1 / 65535)
    assert dumps(back) == text


def test_applying_a_look_matches_the_preset_it_came_from():
    from colorlab.fsim import apply, from_preset
    spatial_only = preset(SPATIAL)
    np.testing.assert_allclose(apply(from_preset(spatial_only, name="S"), IMAGE, seed=3),
                               finish.render_preset(spatial_only, IMAGE, seed=3), atol=2e-4)
    p = preset({"Saturation": "+30", "Vibrance": "-20", "Contrast2012": "+25", "HueAdjustmentRed": "+20", **SPATIAL})
    from colorlab.metrics import delta_e_2000, srgb_to_lab
    d = delta_e_2000(srgb_to_lab(apply(from_preset(p, name="C"), IMAGE)), srgb_to_lab(finish.render_preset(p, IMAGE)))
    assert d.mean() < 0.15 and d.max() < 1.5  # only the 33-point LUT's interpolation differs


def test_a_look_profiles_own_highlights_and_shadows_are_folded_in():
    from colorlab.fsim import from_preset
    from colorlab.preset import LookRef
    profile = parse_preset(make_xmp({"PresetType": "Look", "Shadows2012": "+40", "UUID": "AA"}))
    import dataclasses
    p = dataclasses.replace(preset({"Shadows2012": "+20"}),
                            look=LookRef(name="L", uuid="AA", amount=0.5, stubbed=False, source=profile))
    assert from_preset(p, name="F", lut_size=2).local["shadows"] == 40


def test_files_and_the_command_line(tmp_path):
    import tifffile
    from colorlab.fsim import main, read, write, from_preset
    p = preset({"Saturation": "+20", "GrainAmount": "10"})
    write(from_preset(p, name="Disk", lut_size=5), tmp_path / "a.fsim.json")
    assert read(tmp_path / "a.fsim.json").name == "Disk"
    (tmp_path / "p.xmp").write_text(make_xmp({"ProcessVersion": "15.4", "Saturation": "+20"}), encoding="utf-8")
    assert main(["from-preset", str(tmp_path / "p.xmp"), str(tmp_path / "b.fsim.json"), "--name", "Cli", "--size", "5"]) == 0
    assert read(tmp_path / "b.fsim.json").lut.size == 5
    tifffile.imwrite(tmp_path / "in.tif", (IMAGE * 65535).astype(np.uint16))
    assert main(["apply", str(tmp_path / "b.fsim.json"), str(tmp_path / "in.tif"), str(tmp_path / "out.tif")]) == 0
    assert tifffile.imread(tmp_path / "out.tif").shape == IMAGE.shape
    assert main(["apply", str(tmp_path / "missing.fsim.json"), str(tmp_path / "in.tif"), str(tmp_path / "o.tif")]) == 1


def _doc(**changes):
    from colorlab.fsim import dumps, from_preset
    doc = json.loads(dumps(from_preset(preset({"Clarity2012": "+10"}), name="V", lut_size=2)))
    for path, value in changes.items():
        *parents, key = path.split("__")
        node = doc
        for k in parents:
            node = node[k]
        if value is _DELETE:
            del node[key]
        else:
            node[key] = value
    return json.dumps(doc)


_DELETE = object()
BAD = {
    "not json": lambda: "{",
    "not an object": lambda: "[1, 2]",
    "wrong format": lambda: _doc(format="cube"),
    "future version": lambda: _doc(version=2),
    "missing name": lambda: _doc(name=_DELETE),
    "empty name": lambda: _doc(name=""),
    "unknown section": lambda: _doc(halation={}),
    "unknown setting": lambda: _doc(local__clarty=10),
    "out of range": lambda: _doc(local__clarity=150),
    "not a number": lambda: _doc(local__clarity="10"),
    "boolean": lambda: _doc(grain__amount=True),
    "bad style": lambda: _doc(vignette__style=4),
    "lut too small": lambda: _doc(color__lut__size=1),
    "lut too large": lambda: _doc(color__lut__size=129),
    "bad base64": lambda: _doc(color__lut__data="!!!"),
    "short data": lambda: _doc(color__lut__data=base64.b64encode(b"\0" * 10).decode()),
    "missing lut": lambda: _doc(color=_DELETE),
}


@pytest.mark.parametrize("case", list(BAD))
def test_malformed_looks_are_rejected(case):
    from colorlab.errors import FsimFormatError
    from colorlab.fsim import loads
    with pytest.raises(FsimFormatError):
        loads(BAD[case]())
    assert issubclass(FsimFormatError, ColorlabError)


def test_oversized_text_is_rejected_before_parsing():
    from colorlab.errors import FsimFormatError
    from colorlab.fsim import MAX_TEXT_BYTES, loads
    with pytest.raises(FsimFormatError):
        loads(" " * (MAX_TEXT_BYTES + 1))


def test_a_look_needs_a_name_and_a_lut_to_build():
    from colorlab.errors import FsimFormatError
    from colorlab.fsim import Look
    with pytest.raises(FsimFormatError):
        Look(name=" ", lut=Lut3D.identity(2))
    with pytest.raises(FsimFormatError):
        Look(name="x" * 200, lut=Lut3D.identity(2))
