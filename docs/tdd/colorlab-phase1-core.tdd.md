# TDD evidence: colorlab Phase 1 core

**Source plan:** the Phase 1 "Python color lab" plan agreed in the planning session.
This report covers tasks 1–5 (repo setup, color spaces, LUTs, the RGBTable decoder and
the preset reader) and task 6a (global color pipeline). Still open: spatial effects (6b), checks against Lightroom renders (7),
`.fsim` (8) and the command-line tool (9).

## User journeys

- As a photographer, I want my Lightroom presets (`.xmp` and mobile `.dng`) read accurately,
  so that Emulsion can reproduce their looks.
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
a LUT, so that Emulsion matches Lightroom without reverse-engineering its math.*

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

## Task 6a: reference pipeline, global color (`colorlab.pipeline`)

Journey: *As a photographer, I want a preset's color sliders reproduced by Emulsion's own
engine, so that I can edit looks in-app and not only replay fixed LUTs.*

| Step | Command | Result |
|---|---|---|
| RED | `pytest tests/test_pipeline.py` | `ModuleNotFoundError: No module named 'colorlab.pipeline'` (commit 695c2be) |
| GREEN | `pytest tests/test_pipeline.py`, then `pytest --cov` | First run: 5 failed. One was a real bug: neutral detection used the new chroma, so Saturation −100 did nothing. One spline case wasn't actually collinear. The parametric bumps were too steep near 0 and 1. After fixes: all pass; full suite **241 passed, 97.80%**; CI view **236 passed, 97.50%** |

Before GREEN, two fixtures were changed to match the spec, not the code: white-balance luminance is
now checked in the working space (it was Rec.709 luma after a D50→D65 conversion), and the "pure red"
sample sits on the red band's center.

Guarantees (`tests/test_pipeline.py`):
- Every stage is exactly identity at its defaults, and out-of-range sliders raise `ParameterError`.
- Point curves pass through their control points and are straight for collinear points. They hold
  their end values outside the points, stay smooth, and reject bad points.
- Tone, parametric and point curves keep 0 and 1 fixed. Tone and parametric stay monotonic even at ±100.
- Contrast pivots around middle gray; Blacks work low and Whites high; each parametric region moves its own tones.
- White balance keeps working-space luminance; +temperature warms; +tint adds magenta.
- HSL, saturation/vibrance and the B&W mixer leave neutrals neutral. Saturation −100 gives gray.
  Vibrance favors muted colors. Each HSL band moves only its own hues.
- Color grading tints the right zones, and Balance moves the split.
- `render` is identity for an empty preset and keeps the image shape. Exposure +1 doubles linear light.
  Curves apply in the encoded domain. Look tables apply at their amount, and the look profile's own
  settings are applied.

Accuracy against Lightroom (private test `tests/private/test_pipeline_vs_lightroom.py`, 44 baked presets,
17³ grid, ΔE2000): **median 4.25, mean 5.28** with first-guess strengths. Table-only looks stay at 0.00.
Fitting the strengths to the composite presets only reached a mean of 4.30. It also picked an
implausible stage order and a Blacks strength that would break monotonicity, so it was **not adopted**.
Stages can't be told apart from composite presets. Calibration needs single-slider bakes (next step).

## Calibration kit (`colorlab.calibration`, `colorlab.metrics`, sidecar bakes)

Journey: *As a maintainer, I want each pipeline strength fitted to Lightroom from bakes that
move only that slider, so that calibration isn't fooled by presets that move many sliders at once.*

| Step | Command | Result |
|---|---|---|
| RED | `pytest tests/test_metrics.py tests/test_calibration.py tests/test_bake.py` | 3 collection errors: `No module named 'colorlab.metrics'`, `No module named 'colorlab.calibration'`, `cannot import name 'make_sidecar_xmp'` (commit 60fdc4b) |
| GREEN | `pytest --cov` | One failure: Lab differed from colour-science by up to 0.02. colour-science uses the IEC 61966-2-1 rounded matrix and ours uses the exact primaries, so the tolerance became 0.05. Then **267 passed, 97.75%**; CI view **262 passed, 97.50%** (commit 7ded2be) |
| Real run | `calibration presets out/cal-presets` then `bake kit out/cal-presets out/cal-kit` | 123 presets, 123 Hald TIFFs (186 MB); embedded XMP parses and carries the slider |

Guarantees:
- `metrics`: Lab matches colour-science. CIEDE2000 matches the Sharma reference pairs within 1e-4
  and colour-science within 1e-6, is symmetric, and is zero for equal colors.
- `calibration`: variant names are unique and file-safe. Every `K_*` strength has search bounds
  and at least one variant (tone shapes are measured instead; see below). Every slider family and HSL band
  is covered. B&W variants set the whole mixer, so Lightroom's auto-mix can't leak in. Variant XMPs are
  complete presets that feed `bake kit` directly.
- The fitter recovers known strengths from synthetic bakes within 2%, restores the pipeline's
  strengths afterwards, reports missing bakes, and still scores variants that inform no strength.
  The command-line tool writes presets, prints fitted constants and JSON, and fails cleanly with no bakes.
- `bake`: kit TIFFs embed a Lightroom-sidecar-style XMP (`crs:AlreadyApplied="False"`, metadata dates,
  a distinct document ID each). Existing values are replaced, not duplicated. A missing process version
  becomes 11.0. That Lightroom then auto-applies the settings is a **hypothesis** and still has to be
  confirmed in Lightroom.

## Measured slider shapes (`colorlab.response`)

Journey: *As a maintainer, I want exposure, Whites/Blacks/Contrast, the parametric curve and
vibrance to follow Lightroom's own response curves, measured from the single-slider bakes,
because fitting one strength to a guessed formula left most of the remaining error in those stages.*

What the 113 bakes showed (neutral ramps and colored samples, working-space encoded):
- These tone sliders act per channel in our working space. Applying Lightroom's own neutral curve
  per channel reproduces the colored bakes to 1–4 ΔE.
- Exposure keeps white at 1 and rolls the highlights off. `T(2^(0.9·ev)·T⁻¹(x))` with
  `T(s) = 1 − (1 + s/3.6)^−3.6` fits the ±1 EV bakes within 0.009.
- Whites + clips before 1 (from x ≈ 0.75 at +100). Blacks − crushes a whole range (up to x ≈ 0.3 at −100).
- Vibrance pivots between each color's min and max channel, at 0.6 for − and 0.9 for +. Lightroom's
  "luma" barely weights ProPhoto blue, so drained blues brighten. The gain falls with chroma, and +
  protects reds and oranges.

| Step | Command | Result |
|---|---|---|
| RED | `pytest` | `test_response.py` and `test_calibration.py` cannot import `colorlab.response`. 9 `test_pipeline.py` specs fail on the old shapes (commit 5fa4470) |
| GREEN | `pytest`, then `pytest --cov` | First run: 3 failures. A curve blended toward identity never clips, so the interpolation now moves the clip points linearly and blends the shape between them. The vibrance spec's "muted" color sat at a skin hue, which Lightroom protects; the spec now uses a muted green and adds an explicit skin-protection test. Clipped colors skewed the vibrance pivot measure, so they're now left out of the gain estimate. Then everything passes, **98% coverage** (commit be16d19) |
| Real run | `calibration response out/cal-luts --out src/colorlab/data/lightroom_response.json` | All 9 sliders measured: exposure shoulder 3.6, strength 0.9, vibrance pivots 0.6 / 0.9 |
| Real run | `calibration fit out/cal-luts` | Remaining strengths unchanged. Mean ΔE over 113 bakes 2.90 → **2.29**. For example Whites +100 13.65 → 4.19, Blacks −100 9.56 → 1.11, Parametric Lights −100 10.08 → 2.57, Vibrance −100 7.51 → 4.94 |
| Real run | 44 private presets | median ΔE 4.22 → **3.40**, mean 4.90 → **4.54** (private threshold tightened to 3.45 / 4.60) |

Guarantees:
- Tone families are identity at 0, exact at baked amounts, monotonic in between, clamp outside the
  baked range, and move clip points between amounts. Malformed tables are rejected.
- The exposure shoulder keeps 0 and 1, never clips, brightens every midtone for +EV, and ±EV steps undo each other.
- Vibrance gain is 1 at 0, follows the chroma table and band factors, and keeps hue and neutrals.
- The shipped response has every tone family, round-trips through JSON, and refuses a file missing one.
- `measure_response` reads tone curves from neutral ramps, recovers a known exposure shoulder and a
  known vibrance pivot and gain from synthetic bakes, and keeps shipped curves for sliders without bakes.
- The kit now bakes exposure at ±0.5/±1/±2, Contrast/Whites/Blacks and Vibrance at ±25/±50/±100,
  and every parametric region at ±50/±100.

Known gaps: Contrast, Whites, Blacks and Vibrance at ±25, the parametric regions at ±50, and exposure
at ±0.5 and ±2 aren't baked yet. Until they are, those amounts are interpolated (exposure extrapolates
via the shoulder model). The remaining tone error (Whites +100 4.2, Parametric Lights +100 3.5) is
Lightroom not acting purely per channel at the extremes.

## Coverage and known gaps

- 96.4% public / 97.0% including private tests. Uncovered: a few defensive branches
  (truncated-TIFF `struct.error`, non-UTF-8 XMP packets, empty-sample curves).
- Pipeline: stage strengths (`K_*`) are first guesses until single-slider calibration. Not yet modelled:
  Highlights/Shadows, Clarity, Texture, Dehaze (local, task 6b), camera calibration sliders (3 presets use them),
  raw-only Kelvin white balance, and Adobe built-in looks (1 preset).
- RGBTable primaries/gamma enum values 2–4 (ProPhoto, P3, Rec.2020, gamma 1.8) follow the
  DNG SDK ordering and haven't been checked against a real sample yet.
- Lightroom's exact amount and gamut-extend behaviour isn't public. The implemented behaviour
  (blend in encoded space, carry the out-of-gamut residual) still needs checking against Lightroom renders.

### Cross-check against Lightroom bakes (local, private corpus)

After the re-export, `collect` gave `collected 44, missing 0, unchanged 0`. Six table-based profile
looks were compared on a 33³ grid: `apply_rgb_table` on our decoded table vs. Lightroom's baked LUT.

| Profile kind | Mean ΔE2000 | Notes |
|---|---|---|
| sRGB/sRGB/clip table, no other sliders (2 looks) | **0.00** (p95 0.01) | Decoder and lookup match Lightroom exactly |
| Same, plus profile Highlights −30 | 0.64 | Remainder is the profile's own Highlights slider |
| Adobe RGB/γ2.2/extend tables with curve, Highlights/Shadows, Vibrance/Saturation (2 looks) | 1.8–5.0 | Remainder is the profile's own sliders, which come in task 6 |
| Monochrome (Saturation −100, parametric curve, Contrast) | 4.9 | Same |

Plain and "grainy" variants bake to identical LUTs (max diff 0), which confirms grain was zeroed.
The table path is proven only for sRGB/clip at amount 1.0. Adobe RGB/extend gets isolated once task 6
can apply the profiles' own sliders. Profile-level Highlights/Shadows can't be zeroed by the bake
(they live inside the look), so those bakes may carry a little of Lightroom's local tone mapping.
