"""Write the conformance fixtures the Kotlin look module is tested against.

The fixtures come from colorlab, the Python reference: a synthetic look (our own settings, no
private preset), sample colours with the reference LUT lookup, and invalid look files that the
Python reader rejects. Run from the repository root with colorlab installed:

    python android/look/src/test/fixtures/make_conformance.py

and commit the files it writes to ``android/look/src/test/resources/conformance``.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from colorlab import fsim
from colorlab.errors import FsimFormatError
from colorlab.lut import apply_lut
from colorlab.pipeline import render_lut
from colorlab.preset import parse_preset

OUT = Path(__file__).resolve().parents[1] / "resources" / "conformance"

# A warm, faded look with its own settings, so every LUT axis bends: a tone lift, split toning,
# HSL moves and a saturation cut.
SETTINGS = {
    "PresetType": "Normal", "UUID": "0000000000000000000000000000F001", "ProcessVersion": "11.0",
    "Contrast2012": "-20", "Blacks2012": "+25", "Saturation": "-15", "Vibrance": "+10",
    "SplitToningShadowHue": "200", "SplitToningShadowSaturation": "20",
    "SplitToningHighlightHue": "45", "SplitToningHighlightSaturation": "25",
    "HueAdjustmentRed": "+10", "SaturationAdjustmentBlue": "-30", "LuminanceAdjustmentGreen": "-20",
}


def _xmp(attrs: dict[str, str]) -> str:
    attr_xml = "\n".join(f'   crs:{k}="{v}"' for k, v in attrs.items())
    return f"""<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
{attr_xml}>
   <crs:Name><rdf:Alt><rdf:li xml:lang="x-default">Conformance Warm Fade</rdf:li></rdf:Alt></crs:Name>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    preset = parse_preset(_xmp(SETTINGS))
    look = fsim.Look(name="Conformance Warm Fade", lut=render_lut(preset, 9),
                     description="Synthetic look for the Kotlin conformance tests.",
                     author="Emulsion", license="GPL-3.0-or-later",
                     local={"clarity": 10}, vignette={"amount": -20}, grain={"amount": 12, "size": 30})
    (OUT / "warm_fade.fsim.json").write_text(fsim.dumps(look), encoding="utf-8")

    rng = np.random.default_rng(20261119)
    samples = np.concatenate([
        rng.random((240, 3)),
        np.array([[0, 0, 0], [1, 1, 1], [0.5, 0.5, 0.5], [1, 0, 0], [0, 1, 0], [0, 0, 1],
                  [-0.2, 0.5, 1.3], [0.999, 0.001, 0.5]], dtype=np.float64),  # corners and clamping
    ])
    expected = apply_lut(fsim.loads(fsim.dumps(look)).lut, samples).astype(np.float64)
    (OUT / "warm_fade_samples.json").write_text(json.dumps({
        "note": "colorlab.lut.apply_lut (tetrahedral) of the decoded look on each input",
        "inputs": samples.round(10).tolist(), "outputs": expected.round(10).tolist()}, indent=1) + "\n",
        encoding="utf-8")

    valid = json.loads(fsim.dumps(look))
    small = {"format": "fsim", "version": 1, "name": "x",
             "color": {"lut": valid["color"]["lut"]}}
    cases = {
        "unknown_section": {**small, "extra": {}},
        "wrong_format": {**small, "format": "cube"},
        "version_2": {**small, "version": 2},
        "version_bool": {**small, "version": True},
        "no_name": {k: v for k, v in small.items() if k != "name"},
        "blank_name": {**small, "name": "   "},
        "long_name": {**small, "name": "n" * 81},
        "no_color": {k: v for k, v in small.items() if k != "color"},
        "lut_size_1": {**small, "color": {"lut": {"size": 1, "data": ""}}},
        "lut_size_66": {**small, "color": {"lut": {"size": 66, "data": ""}}},
        "lut_bad_base64": {**small, "color": {"lut": {"size": 9, "data": "@@@"}}},
        "lut_short_data": {**small, "color": {"lut": {"size": 9, "data": valid["color"]["lut"]["data"][:-8]}}},
        "lut_extra_key": {**small, "color": {"lut": {**valid["color"]["lut"], "gamma": 1}}},
        "unknown_setting": {**small, "local": {"sparkle": 3}},
        "out_of_range": {**small, "grain": {"amount": 101}},
        "not_a_number": {**small, "vignette": {"amount": "-20"}},
        "bool_setting": {**small, "grain": {"amount": True}},
        "fractional_style": {**small, "vignette": {"style": 1.5}},
        "section_not_object": {**small, "local": [1, 2]},
        "not_an_object": [1, 2, 3],
    }
    invalid = OUT / "invalid"
    invalid.mkdir(exist_ok=True)
    for name, doc in cases.items():
        text = json.dumps(doc)
        try:
            fsim.loads(text)
        except FsimFormatError:
            pass
        else:
            raise SystemExit(f"colorlab accepted the invalid case {name}")
        (invalid / f"{name}.fsim.json").write_text(text + "\n", encoding="utf-8")
    (invalid / "not_json.fsim.json").write_text("{not json\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
