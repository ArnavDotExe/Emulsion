# TDD evidence: colorlab Phase 1 core

**Source plan:** the Phase 1 "Python color lab" plan agreed in the planning session.
This report covers tasks 1–5: repo setup, color spaces, LUTs, the RGBTable decoder and
the preset reader. Still open: the reference pipeline (task 6), checks against Lightroom renders (7),
`.fsim` (8) and the command-line tool (9).

## User journeys

- As a photographer, I want my Lightroom presets (`.xmp` and mobile `.dng`) read accurately,
  so that FilmSim can reproduce their looks.
- As a photographer, I want presets that embed a color table (profile/look presets) carried over
  exactly, so that table-based looks match Lightroom pixel for pixel in color.
- As a contributor, I want standard `.cube` and Hald LUT exchange, so that looks can be
  made in other tools and shared.
- As a maintainer, I want malformed or hostile files rejected with clear errors, so that
  bad input never fails silently or runs XML entity attacks.

## RED → GREEN log

| Step | Command | Result |
|---|---|---|
| RED 1 | `pytest` (tests written, modules absent) | 4 collection errors: `ImportError: cannot import name 'colorspace'`, `ModuleNotFoundError: No module named 'colorlab.errors'`. This is the intended missing-implementation failure |
| GREEN 1 | `pytest` | 142 passed, 3 failed. Two failures exposed a real bug: identity rounding used `div>>1` instead of `(div-1)>>1`. One showed Adobe looks put `crs:Parameters` attributes on the element itself |
| RED 2 | `pytest tests/test_preset.py -k builtin_look` (new test for both RDF forms) | 2 failed: `AttributeError: 'LookRef' object has no attribute 'parameters'` |
| GREEN 2 | `pytest --cov` | **147 passed, 96.97% coverage** (including local private-corpus tests) |
| CI view | `pytest --cov --ignore=tests/private` | **144 passed, 96.42% coverage** |

## What the tests guarantee

| # | Guarantee | Tests | Type |
|---|---|---|---|
| 1 | Color space matrices match colour-science within 1e-3; xyz/rgb matrices are exact inverses | `test_colorspace.py::test_rgb_to_xyz_matrix_*`, `test_xyz_to_rgb_is_inverse` | unit |
| 2 | Conversions round-trip and match colour-science's Bradford `RGB_to_RGB` | `test_conversion_*` | unit |
| 3 | Every transfer curve round-trips, is odd-symmetric, and sRGB matches colour-science | `test_transfer_*`, `test_srgb_encoding_*` | unit |
| 4 | Tetrahedral and trilinear lookup are exact for identity and affine LUTs and at lattice points; tetrahedral keeps greys neutral | `test_lut.py` interpolation tests | unit |
| 5 | `.cube` round-trips with red varying fastest; malformed files (no size, wrong row count, non-numeric, 1D, non-unit domain, wrong columns) raise `LutFormatError` | `test_cube_*`, `test_parse_cube_*` | unit |
| 6 | Hald identity images use the standard layout and turn processed images back into LUTs | `test_hald_*`, `test_lut_from_*` | unit |
| 7 | Adobe base-85 round-trips every length, uses little-endian digits, rejects invalid or dangling characters | `test_base85_*` | unit |
| 8 | RGBTables decode from **hand-built payloads** (independent of our encoder): identity deltas, 16-bit wrap-around, blue innermost, default tail, all header/size/zlib/enum errors | `test_adobe_table.py` decoding tests | unit |
| 9 | Applying a table happens in its encoded space; amount blends there; amount outside range is rejected; clip vs extend gamut behave as specified | `test_identity_table_is_noop`, `test_*amount*`, `test_*gamut*` | unit |
| 10 | Presets: typed numbers/flags/text, curves, nested `crs:Preset` copies ignored, stubbed, embedded and Adobe built-in looks (both RDF forms), missing table data rejected, entity expansion refused | `test_preset.py` | unit |
| 11 | Mobile `.dng` presets: XMP read from IFD0 tag 700 (both byte orders); non-TIFF and missing-tag errors | `test_read_dng_xmp_*`, `test_load_preset_*` | unit |
| 12 | A local private preset corpus loads and its table-based looks resolve; Adobe built-in looks are reported as unresolved; DNG presets match their XMP twins | `tests/private/` (gitignored, local only) | integration |

## Addendum: Lightroom bake kit (`colorlab.bake`)

Journey: *As a photographer with Lightroom, I want each preset's exact global color as
a LUT, so that FilmSim matches Lightroom without reverse-engineering its math.*

| Step | Command | Result |
|---|---|---|
| RED | `pytest tests/test_bake.py` | `ModuleNotFoundError: No module named 'colorlab.bake'` |
| GREEN | `pytest --cov` | **166 passed, 97.04% coverage** |
| Real run | `python -m colorlab.bake kit presets/private out/bake-kit` | 44 bake presets + 44 Hald TIFFs (67 MB); every bake copy re-parses with grain 0 |

Guarantees (`tests/test_bake.py`):
- Bake copies zero all spatial sliders, reset crop/perspective/orientation, and keep color settings, curves and looks.
- Bake copies get a new name, group and UUID. Sidecar-style XMPs are made importable (`PresetType=Normal`, `HasSettings=True`).
- Hald TIFFs round-trip to identity within 1/65535 and carry the bake settings as XMP (tag 700).
- 8-bit and RGBA exports are accepted; float or wrong-shape exports are rejected.
- The kit refuses a non-empty output folder and de-duplicates names.
- `collect` converts matching exports, reports missing ones, and rejects a level mismatch. The command-line tool returns 0 and prints a summary.

### Regression: untouched exports

The first real Lightroom run baked 15 of 44 presets. The other 29 came back as identity images
(mean deviation 0.0001), because Lightroom picked up the embedded settings for only some of the TIFFs.
`collect` would have quietly written 29 do-nothing LUTs.

| Step | Command | Result |
|---|---|---|
| RED | `pytest tests/test_bake.py` | 3 failed: `AttributeError: 'CollectReport' object has no attribute 'unchanged'`, plus 2 CLI output assertions |
| GREEN | `pytest --cov` | **168 passed, 97.07% coverage** |
| Real run | `python -m colorlab.bake collect out/bake-exports out/bake-kit looks/private/lightroom` | `collected 15, missing 0, unchanged 29`; no identity LUTs written |

Guarantee: exports within 1e-3 mean deviation of the identity Hald are reported as
"unchanged (preset not applied)" and never converted. The kit README no longer suggests
relying on embedded settings.

## Coverage and known gaps

- 96.4% public / 97.0% including private tests. Uncovered: a few defensive branches
  (truncated-TIFF `struct.error`, non-UTF-8 XMP packets, empty-sample curves).
- RGBTable primaries/gamma enum values 2–4 (ProPhoto, P3, Rec.2020, gamma 1.8) follow the
  DNG SDK ordering and haven't been checked against a real sample yet.
- Lightroom's exact amount and gamut-extend behaviour isn't public. The implemented behaviour
  (blend in encoded space, carry the out-of-gamut residual) still needs checking against Lightroom renders.
