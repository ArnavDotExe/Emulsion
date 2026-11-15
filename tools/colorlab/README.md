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
| `errors` | `ColorlabError` and its subclasses; every message names the file or setting |

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
