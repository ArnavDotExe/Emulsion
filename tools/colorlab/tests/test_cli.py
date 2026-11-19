"""The ``colorlab`` command line: everyday commands plus every module's tools under one name."""
import json

import numpy as np
import pytest
import tifffile
from conftest import make_xmp

from colorlab import cli, finish, pipeline
from colorlab.adobe_table import Gamma, Gamut, Primaries, RGBTable, encode_rgb_table
from colorlab.lut import Lut3D, read_cube
from colorlab.preset import parse_preset

IMAGE = np.clip(np.random.default_rng(9).random((24, 36, 3)) * 0.8 + 0.1, 0, 1)
ATTRS = {"ProcessVersion": "15.4", "Saturation": "+20", "Contrast2012": "+15", "Clarity2012": "+20",
         "GrainAmount": "15", "PostCropVignetteAmount": "-20"}


@pytest.fixture
def files(tmp_path):
    (tmp_path / "p.xmp").write_text(make_xmp(ATTRS), encoding="utf-8")
    tifffile.imwrite(tmp_path / "in.tif", np.round(IMAGE * 65535).astype(np.uint16), photometric="rgb")
    return tmp_path


def _read(path):
    return tifffile.imread(path) / 65535.0


def test_render_applies_the_whole_preset_or_only_its_global_colour(files):
    preset = parse_preset(make_xmp(ATTRS))
    img = _read(files / "in.tif")
    assert cli.main(["render", str(files / "p.xmp"), str(files / "in.tif"), str(files / "out.tif"), "--seed", "4"]) == 0
    np.testing.assert_allclose(_read(files / "out.tif"), np.clip(finish.render_preset(preset, img, seed=4), 0, 1), atol=1e-4)
    assert cli.main(["render", str(files / "p.xmp"), str(files / "in.tif"), str(files / "g.tif"), "--global-only"]) == 0
    np.testing.assert_allclose(_read(files / "g.tif"), np.clip(pipeline.render(preset, img), 0, 1), atol=1e-4)


def test_lut_bakes_the_global_colour_into_a_cube(files):
    assert cli.main(["lut", str(files / "p.xmp"), str(files / "p.cube"), "--size", "9"]) == 0
    lut = read_cube(files / "p.cube")
    assert lut.size == 9
    np.testing.assert_allclose(lut.table, pipeline.render_lut(parse_preset(make_xmp(ATTRS)), 9).table, atol=1e-5)


def test_look_then_apply_round_trips_a_preset_and_apply_takes_a_cube_too(files):
    assert cli.main(["look", str(files / "p.xmp"), str(files / "p.fsim.json"), "--name", "Mine", "--size", "9"]) == 0
    assert json.loads((files / "p.fsim.json").read_text(encoding="utf-8"))["name"] == "Mine"
    assert cli.main(["apply", str(files / "p.fsim.json"), str(files / "in.tif"), str(files / "a.tif")]) == 0
    assert _read(files / "a.tif").shape == IMAGE.shape
    assert cli.main(["lut", str(files / "p.xmp"), str(files / "p.cube"), "--size", "9"]) == 0
    assert cli.main(["apply", str(files / "p.cube"), str(files / "in.tif"), str(files / "c.tif")]) == 0
    from colorlab.lut import apply_lut
    np.testing.assert_allclose(_read(files / "c.tif"), apply_lut(read_cube(files / "p.cube"), _read(files / "in.tif")), atol=1e-4)


def test_info_names_the_preset_its_look_and_the_sliders_it_moves(files, capsys):
    assert cli.main(["info", str(files / "p.xmp")]) == 0
    out = capsys.readouterr().out
    assert "process version 15.4" in out and "Saturation +20" in out and "Clarity2012 +20" in out
    assert "look: none" in out


def test_stubbed_looks_resolve_from_the_presets_folder_and_profile_folders(tmp_path, capsys):
    table = encode_rgb_table(RGBTable(Lut3D(np.full((2, 2, 2, 3), 0.5)), Primaries.SRGB, Gamma.SRGB, Gamut.CLIP, 0.0, 2.0))
    stub = '<crs:Look><rdf:Description crs:Name="Film" crs:Amount="1.0" crs:UUID="LOOK1" crs:Stubbed="true"/></crs:Look>'
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "film.xmp").write_text(make_xmp({"PresetType": "Look", "UUID": "LOOK1", "RGBTable": "T", "Table_T": table}),
                                       encoding="utf-8")
    (tmp_path / "user").mkdir()
    (tmp_path / "user" / "u.xmp").write_text(make_xmp({"UUID": "U1"}, children=stub), encoding="utf-8")
    assert cli.main(["info", str(tmp_path / "user" / "u.xmp")]) == 0
    assert "look: Film at 100% (not found" in capsys.readouterr().out
    assert cli.main(["info", str(tmp_path / "user" / "u.xmp"), "--profiles", str(profiles)]) == 0
    assert "look: Film at 100%\n" in capsys.readouterr().out
    (tmp_path / "user" / "film.xmp").write_text((profiles / "film.xmp").read_text(encoding="utf-8"), encoding="utf-8")
    assert cli.main(["lut", str(tmp_path / "user" / "u.xmp"), str(tmp_path / "u.cube"), "--size", "3"]) == 0
    np.testing.assert_allclose(read_cube(tmp_path / "u.cube").table, 0.5, atol=0.02)  # the look's table, found next to it


@pytest.mark.parametrize("tool,module", sorted(cli.TOOLS.items()))
def test_every_module_tool_is_reachable_under_one_name(tool, module, monkeypatch):
    import importlib
    seen = []
    monkeypatch.setattr(importlib.import_module(module), "main", lambda argv: seen.append(argv) or 7)
    assert cli.main([tool, "a", "--b", "c"]) == 7
    assert seen == [["a", "--b", "c"]]


def test_tools_cover_every_module_with_a_command_line():
    assert set(cli.TOOLS.values()) == {f"colorlab.{m}" for m in (
        "bake", "calibration", "spatial_calibration", "preset_check", "finish", "film", "local_contrast", "fsim")}


def test_errors_exit_1_with_a_message_and_no_command_shows_usage(files, capsys):
    assert cli.main(["render", str(files / "missing.xmp"), str(files / "in.tif"), str(files / "o.tif")]) == 1
    assert "error:" in capsys.readouterr().err
    (files / "bad.tif").write_bytes(b"not a tiff")
    assert cli.main(["render", str(files / "p.xmp"), str(files / "bad.tif"), str(files / "o.tif")]) == 1
    assert cli.main(["apply", str(files / "p.xmp"), str(files / "in.tif"), str(files / "o.tif")]) == 1  # not a look
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0 and "colorlab" in capsys.readouterr().out


def test_python_dash_m_colorlab_runs_the_same_command(files):
    import subprocess
    import sys
    done = subprocess.run([sys.executable, "-m", "colorlab", "info", str(files / "p.xmp")], capture_output=True, text=True)
    assert done.returncode == 0 and "Saturation +20" in done.stdout
