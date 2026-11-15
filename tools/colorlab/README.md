# colorlab

The color science behind FilmSim, written as plain, tested Python. It is the
reference the Android GPU engine has to match.

## Modules

| Module | Purpose |
|---|---|
| `colorspace` | sRGB, Adobe RGB, Display P3, ProPhoto and Rec.2020; Bradford-adapted conversions; odd-symmetric transfer curves |
| `lut` | `Lut3D` (indexed `[r, g, b]`), tetrahedral and trilinear lookup, `.cube` read/write, Hald CLUT images |
| `adobe_table` | Decode and encode Lightroom `crs:Table_*` RGBTables (base-85, zlib, identity deltas); apply one with amount and gamut handling |
| `preset` | Parse `.xmp` and mobile `.dng` presets into a typed `Preset` (settings, point curves, looks); resolve stubbed looks to their profile presets |
| `pipeline` | Reference model of a preset's global color sliders in Lightroom order: white balance, exposure, contrast/whites/blacks, parametric and point curves, HSL, color grading, look table, saturation/vibrance, B&W mixer. `render()` an image or `render_lut()` a LUT |
| `calibration` | 143 single-slider Lightroom presets for baking; measures slider shapes from their bakes and fits each remaining `pipeline` strength |
| `response` | Lightroom's measured slider responses (exposure shoulder, tone and parametric curves per amount, vibrance gains), shipped in `data/lightroom_response.json` |
| `metrics` | CIE Lab (D65) from sRGB and CIEDE2000 color difference |
| `bake` | Turn presets into exact LUTs through Lightroom: a kit of bake presets (spatial and geometry settings zeroed) plus same-named 16-bit Hald TIFFs, then `.cube` LUTs from the exports |
| `errors` | `ColorlabError` and its subclasses; every message names the file or setting |

## Baking presets into LUTs with Lightroom

```bash
python -m colorlab.bake kit presets/private out/bake-kit        # then follow out/bake-kit/README.txt
python -m colorlab.bake collect <exports> out/bake-kit looks/private/lightroom
```

The LUTs capture a preset's global color: white balance, exposure, tone curves, HSL,
color grading, profiles and looks, saturation. Grain, vignette, sharpening, noise reduction
and local tone (highlights/shadows, clarity, texture, dehaze) are zeroed for the bake,
because they'd smear the Hald image. The FilmSim engine applies those itself.

## Calibrating the pipeline against Lightroom

```bash
python -m colorlab.calibration presets out/cal-presets
python -m colorlab.bake kit out/cal-presets out/cal-kit        # Lightroom: follow out/cal-kit/README.txt
python -m colorlab.bake collect <exports> out/cal-kit out/cal-luts
python -m colorlab.calibration response out/cal-luts --out src/colorlab/data/lightroom_response.json
python -m colorlab.calibration fit out/cal-luts --json out/cal-fit.json
```

Each calibration preset moves one slider. `response` measures the shapes of exposure,
Contrast/Whites/Blacks, the parametric regions and vibrance straight from their bakes. Sliders
without bakes keep the shipped curves. `fit` then fits each remaining strength only from the
bakes that use it. It prints the suggested `K_*` values and the remaining error for each preset.
A large remaining error means that stage's shape, not just its strength, differs from Lightroom.
To re-bake only what is missing: `calibration presets <new folder> --only-missing out/cal-luts`.

## Quick look

```python
from colorlab.preset import load_preset_dir
from colorlab.lut import write_cube

for p in load_preset_dir("presets/private"):
    table = p.rgb_table or (p.look.table if p.look else None)
    print(p.name, p.number("Exposure2012"), "table" if table else "")
    if table:
        write_cube(table.lut, f"looks/private/{p.name}.cube")
```

> Note: a raw RGBTable LUT expects input in the table's own primaries and
> encoding (`table.space`, `table.transfer`). Use `adobe_table.apply_rgb_table`
> to handle that conversion for you.

## Tests

```bash
pytest --cov                          # everything, including the private tests if present
pytest --cov --ignore=tests/private   # public suite (what CI runs)
```

`tests/private/` is gitignored. It runs integration checks against presets
in `presets/private/` and is skipped when they're missing.
