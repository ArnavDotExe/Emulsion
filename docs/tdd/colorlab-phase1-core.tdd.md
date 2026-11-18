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
  (Superseded: white balance is now measured per-channel stops; see "White balance" below.)
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
  becomes 11.0. **Confirmed in Lightroom (2026-11-15):** it auto-applies the embedded settings
  on import. The 30-preset redo kit came back with all 30 bakes changed and none unchanged.

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

Re-bake with the 30 missing variants (`out/cal-redo3-kit`, all 143 bakes now present):
- `calibration response` measures the ±25/±50 tables, and exposure at ±0.5/±1/±2 gives a shoulder
  of 5.55 and strength 0.875.
- The channel curves now match within 0.01 ΔE.
- 44 presets: median **3.39**, mean **4.51**.
- Temperature/tint were fitted for the first time (K 0.00472 / 0.0025). They're still 12–26 ΔE at
  ±50/±100, so the white-balance *model* is wrong, not just its strength. That's the next shape to fix.

Known gaps: the remaining tone error (Whites +100 4.2, Parametric Lights +100 3.5) is Lightroom not
acting purely per channel at the extremes. Exposure +2 is still 4.9.

### White balance (`WhiteBalanceResponse`)

Journey: *As a maintainer, I want temperature and tint to match Lightroom on rendered images, because
the luminance-keeping gain model was 12–26 ΔE off at ±50/±100 even with fitted strengths.*

What the bakes showed (neutral ramp, per channel, inverted through the exposure shoulder):
- Each channel gets a constant number of stops from shadows to highlights. It's a per-channel
  exposure on the same shoulder, so white stays white.
- The weakest channel barely moves: blue for warm, green for magenta.
- The stops grow non-linearly. Temperature +50 is R 1.01 / G 0.84 / B 0.02, while −100 is
  R 1.44 / G 0.96 / B 3.71.

| Step | Command | Result |
|---|---|---|
| RED | `pytest --continue-on-collection-errors` | `test_response.py` cannot import `WhiteBalanceResponse`. 10 runtime failures: white doesn't stay white, the Lightroom gray targets are missed, `K_TEMPERATURE`/`K_TINT` still exist, and there are no ±25 variants (commit 0a6e29a) |
| GREEN | `pytest --cov` | One failure: a channel with 0 stops wasn't bit-identical after the curve round trip, so it now passes through. Then everything passes, **98% coverage** (commit fa9d9a8) |
| Real run | `calibration response out/cal-luts` | Temperature and tint stops measured at ±50/±100 |
| Real run | `calibration fit out/cal-luts` | Mean over 143 bakes 2.67 → **2.25**. Temperature −100 25.8 → 13.4, +100 18.6 → 6.3, Tint −100 13.1 → 4.9, the ±50 bakes 1.7–4.4 (were 3.5–12.0) |
| Real run | 44 private presets | median **3.18**, mean **4.39** (threshold 3.25 / 4.45) |

Guarantees:
- White balance keeps black and white, and every channel stays monotonic.
- +temperature warms while keeping blue, and +tint adds magenta while keeping green.
- Four Lightroom gray targets match within 0.03.
- Stops are 0 at 0, exact at baked amounts, linear in between, clamped outside, and temperature and
  tint add.
- The shoulder takes one EV per channel, and a channel with 0 stops passes through exactly.
- `measure_response` recovers known per-channel stops from synthetic working-space bakes.

Re-bake of Temperature/Tint ±25 (`out/cal-redo4-kit`, all 147 bakes present):
- Measured stops: Temperature +25 R 0.57 / G 0.46 / B 0.00, −25 R 0.04 / G 0.14 / B 0.67.
- Those bakes now score 0.7–2.1 ΔE, and the mean over 147 bakes is **2.22**.
- The 44 presets are unchanged (median 3.18, mean 4.39); blending between 0 and ±50 was already close.

Known gaps: Temperature −100 (13.4) and ±100 still miss on saturated colors. Lightroom's white balance isn't
purely per-channel there, and the sRGB output clips.

### Review fixes (PR #1): regeneration without a readable base

Journey: as the maintainer, I can always regenerate `lightroom_response.json` with
`calibration response`, even after the file breaks or its format gains a key, and a bake that
can't measure a white-balance channel never silently ships "0 stops".

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | `colorlab.calibration` imports when the shipped response can't be read | `test_calibration_imports_even_when_the_shipped_response_is_unreadable` | PASS |
| 2 | `pipeline` reads the response on first use and reports a broken file as `ColorlabError` | `test_pipeline_reads_the_response_on_first_use_and_reports_a_broken_file` | PASS |
| 3 | Without a readable base, `measure_response` starts from `response.identity()` and warns | `test_measure_response_without_a_readable_base_uses_identity_and_warns` | PASS |
| 4 | A white-balance bake with no usable samples keeps the base stops and warns | `test_white_balance_bake_without_usable_samples_keeps_the_base_and_warns` | PASS |
| 5 | The `response` CLI prints each warning | `test_cli_response_prints_warnings` | PASS |

- RED `0bb61e6`: all 5 failed for the intended reasons. The import died at
  `pipeline.py:37` (`response.load()` at import), there was no `ResponseReport.warnings`, and
  the CLI exited 1.
- GREEN: `RESPONSE` is loaded lazily through `pipeline._response()`. `response.identity()` is
  the fallback base. `_white_balance_stops` marks channels it can't measure as NaN, and
  `measure_response` replaces them with the base and records a warning.
- Results: the public suite passes at 97.49% coverage; the private accuracy suite passes unchanged.

### Hue-preserving tone curves (`pipeline.rgb_tone`)

Journey: as a photographer, bright saturated colors keep their hue under exposure, contrast,
whites/blacks, the parametric curve and the master point curve. Pushed red stays red; it
doesn't turn orange.

The method is the "RGB tone" approach from Adobe's public DNG SDK: the curve maps each pixel's
largest and smallest channel, and the middle channel keeps its relative place between them.
White balance and per-channel point curves stay per channel.

| # | What is guaranteed | Test (`tests/test_pipeline.py`) | Result |
|---|---|---|---|
| 1 | Exposure ±1, Contrast, Whites/Blacks, the parametric curve and the master curve keep each color's hue and channel order | `test_tone_stages_keep_each_colors_hue` (6 cases) | PASS |
| 2 | Neutrals still map exactly through the curve | `test_hue_preserving_tone_still_maps_neutrals_through_the_curve` | PASS |
| 3 | Per-channel point curves stay per channel | `test_per_channel_curves_still_act_per_channel` | PASS |

- RED `141213d`: all 6 hue cases failed with per-channel curves.
- GREEN: full suite (public and private) 363 passed, 98% coverage.
- Single-slider bakes (44 tone variants): mean ΔE 1.44 → 0.67. Exposure +2 4.86 → 1.76;
  Whites +100 4.19 → 0.57; Parametric Lights +100 3.51 → 0.50.
- Private presets: median 3.18 → 2.92, mean 4.39 → 4.13. The private regression bound is now ≤ 2.98 / ≤ 4.20.

### White balance in Bradford cone space (`pipeline.white_balance_stops`)

Journey: as a photographer, Temperature and Tint shift colors the way Lightroom does, not only
grays. Per-channel stops in the working (ProPhoto) channels matched grays but missed saturated
colors. A basis test over the 12 Temperature/Tint bakes, refitting the stops by ΔE in each
basis, gave: ProPhoto 3.36, Rec.2020 3.11, free 3×3 matrix 3.30, Bradford cones 3.01.

The stops now move Bradford cone responses, scaled so white is (1, 1, 1) (a von Kries-style
adaptation), on the same exposure shoulder. `calibration response` starts from the gray-ramp
median per cone, then refines all three stops by mean CIEDE2000 over a 17³ color grid plus a
gray ramp at equal weight. On colors alone the neutrals drift: grays 2.32 ΔE.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | `WhiteBalanceResponse.space` defaults to `"working"`, rejects unknown spaces, round-trips, and older files read as working | `test_response.py`: `test_white_balance_space_defaults_to_working_and_rejects_unknown_spaces`, `test_white_balance_space_round_trips_and_older_files_mean_working` | PASS |
| 2 | The shipped white balance is measured in Bradford cone space | `test_shipped_white_balance_is_measured_in_bradford_cone_space` | PASS |
| 3 | Cone-space stops keep black and white, are the identity at zero, differ from working-space stops on colors, and reject unknown spaces | `test_pipeline.py::test_white_balance_stops_in_cone_space_keep_black_and_white_and_mix_channels_on_colors` | PASS |
| 4 | `measure_response` recovers known cone-space stops from synthetic bakes | `test_calibration.py::test_measure_response_recovers_white_balance_stops_in_cone_space` | PASS |

- RED `f467cf5`: 5 failed (no `space` field, no `white_balance_stops`).
- GREEN: full suite (public and private) 367 passed, 98% coverage.
- 12 WB bakes: colors 3.88 → 3.23 ΔE, gray ramp 1.45 → 1.37. Temperature −100 is 11.5; its
  error sits in colors pushed past 1 (the over-range stage). All 147 bakes: 1.99 → 1.94.
- Two older guarantees loosened, both to tiny or local amounts:
  - Per-channel monotonicity on grays now allows −1e-5. The cone mix wiggles by 2.5e-6 near
    white at Temperature −100, where the S cone has clipped.
  - The mid-gray Lightroom match is now atol 0.035, up from 0.03: Tint −100's red is 0.032 off
    there (1.2 ΔE). The gray ramp as a whole is better than before.
- Private presets: unchanged at median 2.92 / mean 4.13, since few of them use incremental
  Temperature/Tint.

### White-balance hue preservation per amount (the "over-range" stage)

Journey: as a photographer, a strong Temperature or Tint keeps saturated colors' hues the way
Lightroom does, instead of mixing channels. The planned fix was a shared over-range/gamut-rolloff
stage, on the theory that the error sat in colors pushed past 1. Measuring disproved that:

- Lightroom's share of output at 0 or 1 matches ours, so the final clip is fine.
- Temperature −100's error is the same in range (11.4) as out of range (11.6).
- The real cause is mixing. Per-cone stops brighten every channel of a saturated color: pure
  green gains 0.66 of red where Lightroom keeps 0.

The fix is hue preservation in the white-balance rolloff, after the DNG SDK RGB-tone method. On the
shoulder's stretched scale (`ExposureShoulder.stretch`/`rolloff`, where stops are a gain), each
pixel's largest and smallest channel roll off on their own. The middle channel is blended, by
`hue_preservation` ∈ [0, 1], toward its place between them. One constant for all amounts failed:
0.5 helped the ±100 bakes but took Tint ±25 from 0.6 to 1.8 ΔE. So it is measured per bake
(`WhiteBalanceResponse.temperature_hue`/`tint_hue`). `calibration response` refits the stops at each
of 9 values in [0, 1] and keeps the best. The best value grows with the amount:

| Amount | −100 | −50 | −25 | +25 | +50 | +100 |
|---|---|---|---|---|---|---|
| Temperature | 1.0 | 0.75 | 0.375 | 0.25 | 0.375 | 0.625 |
| Tint | 0.5 | 0.25 | 0.125 | 0.0 | 0.125 | 0.75 |

Between amounts it is linear, and 0 at a zero slider, so no change stays no change. With both
sliders set, the larger of the two applies, since no combined bakes measure the mix.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | The exposure shoulder is a gain between `stretch` and `rolloff`, and `apply`/`stops` use them | `test_response.py::test_exposure_shoulder_is_a_gain_between_stretch_and_rolloff` | PASS |
| 2 | Hue preservation is per amount: 0 with no tables or at zero, linear between amounts, the larger of temperature's and tint's; values outside [0, 1] are rejected | `test_white_balance_hue_preservation_is_per_amount_zero_at_zero_and_the_larger_of_the_two` | PASS |
| 3 | The tables round-trip, and files without them (or with an older scalar) mean per channel | `test_white_balance_hue_tables_round_trip_and_older_files_mean_per_channel` | PASS |
| 4 | The shipped tables preserve more hue at ±100 than at ±25 | `test_shipped_white_balance_preserves_more_hue_at_larger_amounts` | PASS |
| 5 | The rolloff keeps each pixel's largest and smallest channel, places the middle one, and is per channel at 0 | `test_pipeline.py::test_white_balance_hue_preservation_keeps_max_and_min_and_places_the_middle_channel` | PASS |
| 6 | `measure_response` recovers a known per-amount hue preservation (0 at 25, 0.75 at 100) and its stops from synthetic bakes | `test_calibration.py::test_measure_response_recovers_white_balance_hue_preservation_per_amount` | PASS |

- RED `d8de30f` and `5a24409`: these failed because `stretch`/`rolloff` and the hue tables
  didn't exist yet. `5a24409` revised `d8de30f`'s single constant to per-amount tables.
- 12 WB bakes: colors 3.23 → 2.08 ΔE, gray ramp 1.37 → 0.88.
  - Temperature −100: 11.5 → 5.88.
  - Temperature +100: 5.0 → 3.07.
  - Tint ±100: 4.1 → 2.5–3.0.
  - The ±25 bakes stay at or under 1.3.
- All 147 bakes: 1.94 → 1.84.
- Private presets: median 2.92 → 2.90, mean 4.13 → 4.12. Few of them use incremental
  Temperature/Tint.
- `calibration response` now takes about 2 minutes, since it runs 9 fits per white-balance bake.
- The mid-gray Lightroom match is back to atol 0.03, from 0.035; the worst gray is now 0.023 off.

### Color grading as per-channel curves (`pipeline.grade_curves`)

Journey: as a photographer, a look's split toning (a converted preset's, or one of our own film
looks) renders the way Lightroom renders it, and bakes into the look's 3D LUT so the camera pays
nothing for it per frame. Emulsion is a camera, not an editor: grading only has to be faithful
once baked, and the wheel sliders (hue, saturation and luminance per zone, plus Balance and
Blending) stay as the vocabulary for authoring looks.

The old stage added a fitted tint to luma-weighted zones (`K_GRADE_SAT`, `K_GRADE_LUM`) and
scored 3.19 ΔE on the 24 Color Grading bakes. It is replaced by a reconstruction of Lightroom's
behaviour. Color grading is three per-channel 1-D curves on linear working values, applied after
the tone curves and the look table:

- Each wheel's hue becomes per-channel factors of unit ProPhoto luminance, spread by saturation.
- Shadows and highlights shape cubic zone curves split at a balance point (the sRGB-decoded
  0.5 − 0.4 · Balance). Blending mixes the split toward one smooth curve.
- Midtones and the global wheel bend each channel around its middle.
- Zone luminance acts on the sRGB-encoded value. Shadow luminance lifts black (0.056 at +50 in
  Lightroom's bake) and highlight luminance dims white, so only the tint wheels fix both ends.
- Measured on the bakes: settings with `SplitToning*` keys but no `ColorGradeBlending` render
  as Blending 100. `ColorGrade*`-only settings default to 50.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Shadow, highlight, midtone, global-luminance, shadow-luminance, Balance −50 and Blending 0 settings render gray 0.1/0.46/0.8 and a red as Lightroom's bakes do (atol 0.002) | `test_pipeline.py::test_render_color_grading_matches_lightroom` | PASS |
| 2 | Tints keep black and white, including strong opposite tints at Balance −80, Blending 0 | `test_color_grade_tints_keep_black_and_white` | PASS |
| 3 | Every channel's curve is increasing; shadow luminance lifts black and highlight luminance dims white | `test_color_grade_is_monotonic_per_channel_and_luminance_moves_the_ends_like_lightroom` | PASS |
| 4 | Values above 1 keep their excess and stay increasing | `test_color_grade_passes_values_above_white_through_monotonically` | PASS |
| 5 | Legacy split toning without a Blending key renders as Blending 100; `ColorGrade*` settings default to 50 | `test_legacy_split_toning_without_blending_renders_as_blending_100` | PASS |
| 6 | No fitted grading strengths remain; the 24 grading bakes stay as accuracy checks | `test_calibration.py::test_color_grading_is_reconstructed_not_fitted` | PASS |
| 7 | All 24 Color Grading bakes match under 0.05 ΔE (local) | `tests/private/test_grade_vs_bakes.py` | PASS |

- RED `7d1230b`: 15 failed, 136 passed. The failures were the new fixtures and properties, the
  legacy Blending rule, the removed constants and the private bake check. The `fit` tests that
  used `K_GRADE_LUM` as their example strength now use `K_LUMINANCE`.
- GREEN: the same 151 tests pass.
- 24 grading bakes: 3.19 → 0.007 ΔE (max 0.009). All 147 bakes: 1.84 → 1.32.
- Private presets: median 2.90 → 2.53, mean 4.12 → 4.03. 24 of the 50 XMP presets split-tone, and
  all of them set Blending explicitly.
- Running grading after Saturation instead of before changes nothing (presets 2.530/4.025 vs
  2.530/4.026), so the stage order stays.

### Scene-linear saturation and vibrance, in Lightroom's stage order

Journey: as a photographer, a look's Saturation and Vibrance (and its HSL and B&W mix) render as
Lightroom renders them, also when the look adds contrast or curves on top.

Lightroom runs saturation, vibrance and HSL on scene-linear values before the tone curve. For a
rendered image, scene-linear is our exposure shoulder's stretched scale. Our pipeline ran them on
encoded values after the curves, with saturation as a chroma formula and vibrance as a measured
gain table. Both are replaced by reconstructions:

- **Saturation** scales the two opponent axes of linear working RGB, (R − G)/2 and
  (B − luminance)/4, by (Saturation + 100)/100, and keeps luminance (0.288 R + 0.712 G).
- **Vibrance** works on hexcone saturation s = (max − min)/max. It moves each color's largest and
  smallest channel by amounts that peak for mid-saturated colors and fade in deep shadows and
  toward white, and it damps boosts for skin hues. The middle channel keeps its place between them.
  The kernel's unit on the stretched scale, `K_VIBRANCE`, is fitted: `fit` gives 0.198, and
  0.18–0.22 score the same.
- **Order:** saturation, then vibrance and HSL (one Lightroom stage, vibrance first), with B&W in
  HSL's place, all before the tone curve. Then parametric, point curves, profile table, grading.

The measured vibrance table (`VibranceResponse`) and its measurement are gone. Older response
files that still hold one load, and the table is ignored.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Saturation −50/+50 and Vibrance −100/+50 render three in-gamut colors as Lightroom's bakes do (atol 0.02) | `test_pipeline.py::test_render_saturation_and_vibrance_match_lightroom` | PASS |
| 2 | Saturation keeps scene-linear luminance | `test_saturation_keeps_scene_linear_luminance` | PASS |
| 3 | Vibrance keeps each color's middle channel in place on the scene-linear scale, and brightens drained blues | `test_vibrance_keeps_hue_on_the_scene_linear_scale_and_brightens_drained_blues` | PASS |
| 4 | The stages run in Lightroom's order | `test_stages_run_in_lightrooms_order` | PASS |
| 5 | The response has no vibrance table; older files with one still load | `test_response.py::test_response_has_no_vibrance_table_and_older_files_with_one_still_load` | PASS |
| 6 | The Vibrance bakes fit `K_VIBRANCE` and are not measured as a table | `test_calibration.py::test_vibrance_bakes_fit_the_kernel_scale_and_are_not_measured_as_a_table` | PASS |
| 7 | Saturation bakes average under 1.10 and Vibrance under 0.75, each under 1.7 (local) | `tests/private/test_sat_vib_vs_bakes.py` | PASS |

- The earlier vibrance properties still hold unchanged: muted colors gain more, skin less, and a
  third of a vivid color survives −100. Hue is now kept on the scene-linear scale, not the
  encoded one (about 2° apart after the rolloff).
- RED `2bd625e`: 10 failed, 176 passed. The failures were the new properties, two of the four
  Lightroom fixtures, the order, the table removal, the `K_VIBRANCE` fit and the private limits.
- GREEN: the same 186 pass.
- Saturation bakes 2.24 → 1.05, Vibrance 2.15 → 0.67. All 147 bakes 1.32 → 1.23.
- Private presets: median 2.53 → 1.63, mean 4.03 → 3.45. Most of it is the order:
  - The new kernels at the old place give 2.49 / 3.98.
  - Moving saturation and vibrance before the tone curve gives 1.80 / 3.77.
  - Moving HSL with them gives 1.63 / 3.62.
  - Moving B&W there too gives 1.63 / 3.45 (one B&W preset).
  - Vibrance after HSL instead scores 1.63 / 3.60, but the decompiled stage runs vibrance first.
- Known gap: strong saturation that pushes a color out of sRGB gamut. Saturation +100 is 1.63,
  and a red at +50 is 0.07 off where Lightroom clips its blue to 0.

## HSL as Lightroom's band tables on scene-linear min, max and hue

Journey: as the maintainer, I want a look's HSL sliders to move colors as Lightroom moves them, so
a look that retunes greens or skin matches its Lightroom original. Formulas come from study of the
shipped Lightroom engine (behaviour only); the code is our own.

What Lightroom does, now in `pipeline.hsl_adjust`:
- Vibrance and HSL are one stage on scene-linear values (the stretched scale, in units of the
  fitted `K_SCENE`). Each color is split into its min, max and hexcone hue; vibrance moves min and
  max first, then the bands are applied, then the color is rebuilt.
- Band centers sit at 0/30/55/95/160/230/275/330°, not evenly spaced. A band's value fades
  linearly to the next center, in a 360-entry table looked up at the color's original hue.
- Hue moves a band's center toward the neighbouring band by max(0.9 × gap, 30°) at ±100.
- Saturation cuts pull min and max toward their middle (−100 gives gray); boosts lower the min by
  a vibrance-like formula that fades in deep shadows.
- Luminance scales min and max by `1 + (1 − v) × a` twice, weaker for bright channels and for
  near-neutrals.
- From process version 15.4, the tables are monotone splines (Fritsch–Carlson) and colors use a
  smooth hue model: min and max are pulled toward the middle channel before tuning, and the
  inverse undoes it exactly. That covers vibrance too. Older presets use the plain hexcone.
- `K_HUE` and `K_LUMINANCE` are gone. `K_VIBRANCE` became `K_SCENE`, fitted to all 48 HSL bakes
  and the 6 Vibrance bakes: 0.145.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Band centers are Lightroom's eight | `test_pipeline.py::test_hsl_band_centers_are_lightrooms` | PASS |
| 2 | Hue ±100 moves a center by Lightroom's offset (red, blue, green, magenta wrapping) | `test_hsl_hue_moves_a_band_center_by_lightrooms_offset` | PASS |
| 3 | Band values fall off linearly between centers | `test_hsl_band_values_fall_off_linearly_between_centers` | PASS |
| 4 | Saturation −100 at a center gives gray; luminance keeps the scene hue | `test_hsl_saturation_minus_100_...`, `test_hsl_luminance_keeps_...` | PASS |
| 5 | Both color models round-trip exactly | `test_hsl_models_round_trip` | PASS |
| 6 | 15.4 changes band and vibrance results, not other sliders; grays stay gray | `test_process_version_15_4_uses_spline_tables_and_the_smooth_hue_model` | PASS |
| 7 | Four Lightroom bake fixtures within 0.015 | `test_render_hsl_matches_lightroom` | PASS |
| 8 | One fitted scene scale; HSL and Vibrance bakes fit it | `test_calibration.py::test_hsl_and_vibrance_share_one_fitted_scene_scale` | PASS |
| 9 | HSL bakes average under 0.62, each under 2.2 (local) | `tests/private/test_hsl_vs_bakes.py` | PASS |
| 10 | Presets median ≤ 1.20, mean ≤ 1.75 (local) | `tests/private/test_pipeline_vs_lightroom.py` | PASS |

- RED `2a2165e`: 19 failed. They were the missing names (`K_SCENE`, `HSL_CENTERS`,
  `_from_mmh`), fixtures up to 0.114 off, an identical 15.4 render, and the private limits (Hue
  Yellow +100 2.34, presets median 1.63).
- GREEN: 415 pass (406 public), coverage 98%.
- HSL bakes 1.83 → 0.57; all 147 bakes 1.23 → 0.82.
- Private presets: median 1.63 → 1.14, mean 3.45 → 1.67. Of the 28 presets that use HSL, the
  plain tables alone give 1.80 mean; the 15.4 path for its 20 presets gives the rest.
- Cost: Vibrance bakes 0.67 → 0.79, because vibrance now shares the HSL scale (it preferred 0.2,
  the bands 0.15). The private Vibrance limit went from 0.75 to 0.82.
- The stretched scale is unbounded, so the smooth inverse clamps only at 0, not at 1 (1.673 vs
  1.679 mean with the clamp at 1).
- Known gap: colors pushed out of sRGB gamut. In Hue Blue −100 (2.05), in-gamut samples are
  0.41 and out-of-gamut ones 7.4.

## A hue-preserving scene scale, measured from ProPhoto exports

Journey: as the maintainer, I want to know whether the HSL error that was left (Hue Blue −100
2.05, nearly all of it in colors pushed outside sRGB) is our model or Lightroom's export, so I
fix the right thing.

The user re-exported all 147 calibration bakes as 16-bit ProPhoto RGB TIFFs
(`out/cal-exports-prophoto`; ICC: ProPhoto, D50, pure gamma 1.8). Ten names came twice (`-2`): the
redo-kit copies. Each pair was resolved by which one matches the collected sRGB bake.
- Lightroom's sRGB export is its ProPhoto result converted and clipped: 0.006 ΔE over all 147
  bakes. There is no gamut mapping, so our final clip already matches.
- So the error was ours. Saturation and brightness of the min and max matched, but saturated
  colors' hue was off.
- Hue bakes against the ProPhoto exports, by the domain the hue is measured in: per-channel
  stretch 0.60, sRGB-encoded 0.95, gamma 1.8 0.72, **linear 0.008**.
- Band saturation and luminance still need the stretched scale (on linear values Saturation −100
  grays bright colors too dark). Both hold if the stretch is hue-preserving: it maps each color's
  min and max and keeps the middle in place, like Lightroom's tone curve (`rgb_tone`). Hue is then
  that of the linear values.
- `pipeline._to_scene` / `_from_scene` do this for saturation, vibrance and HSL. `K_SCENE`
  refitted to 0.141.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | The scene scale keeps the hue of linear values and inverts exactly | `test_pipeline.py::test_scene_scale_keeps_the_hue_of_linear_values` | PASS |
| 2 | Band-center, fall-off, gray and luminance properties hold on that scale | `test_hsl_*` (helpers `scene`/`unscene` now hue-preserving) | PASS |
| 3 | Four saturated-color Lightroom fixtures (Lum Blue −100, Saturation +50 twice, Hue Blue −100) | `test_render_matches_lightroom_on_saturated_colors` | PASS |
| 4 | All 16 hue bakes within 0.05 of Lightroom's ProPhoto exports (local) | `tests/private/test_hsl_vs_prophoto_exports.py` | PASS |
| 5 | HSL bakes under 0.16, each under 0.8; Saturation under 0.50, Vibrance under 0.62; presets median ≤ 0.95, mean ≤ 1.45 (local) | `tests/private/` | PASS |

- RED `0738d3f`: 13 public tests failed. Band centers were 10–16° off on saturated colors,
  fixtures up to 0.167 off, and `_from_scene` was missing. Private: ProPhoto Hue Red −100 0.38,
  presets median 1.14, Saturation 1.05.
- GREEN: 421 pass (411 public), coverage 98%.
- HSL bakes 0.57 → 0.11 (Hue 0.008, Sat 0.09, Lum 0.24); Saturation 1.05 → 0.43; Vibrance
  0.79 → 0.56; all 147 bakes 0.82 → 0.65.
- Private presets: median 1.14 → 0.88, mean 1.67 → 1.40.
- Left: luminance wants a slightly smaller scale (about 0.13) than vibrance (about 0.2); worst
  HSL bake Lum Green −100 0.67.

## White balance and exposure as gains on the scene scale

Journey: as the maintainer, I want a look's temperature, tint and exposure to move colors as
Lightroom does, because a film look's warmth is often its white balance, and the camera applies
it to every frame.

Three gains per white-balance bake were fitted to Lightroom's ProPhoto exports (`out/cal-exports-prophoto`)
by mean ΔE, with each model below. Mean over the 12 Temperature and Tint bakes:

| Channels, domain | Mean ΔE | Temperature −100 |
|---|---|---|
| Bradford cones, hue-preserving scene scale (`_to_scene`) | **0.67** | 1.31 |
| CAT02 cones, scene scale | 1.13 | 2.80 |
| Bradford cones, stretch on cone values (hue-preserving) | 1.46 | 5.89 |
| The previous model (per-channel shoulder + fitted hue preservation) | 2.14 | 6.20 |
| CAT16 / Hunt-Pointer-Estevez / XYZ, scene scale | 2.17 / 2.57 / 3.45 | 8.2 / 10.0 / 11.3 |

- Lightroom scales scene-linear values. Each color goes to the scene scale (the shoulder's stretch
  applied to its min and max, middle kept in place), then into Bradford cone space, where each
  cone is multiplied by `2 ** (strength * stops)`, and back.
- Equal stops in every channel are exposure, which became the same gain: Exposure bakes 0.87 → 0.51.
- The measured hue preservation per amount (`temperature_hue`/`tint_hue`) is gone. It was compensating
  for applying the stops on the wrong scale. Older response files that hold it still load.
- The stops were re-measured from the bakes (`calibration response`). The fitted gains agree with
  the ProPhoto fit to within 0.03 stops, except Temperature −100's red cone (0.10 vs 0.08).

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Equal stops in either space are exposure; working-space stops scale each scene channel; black and white stay | `test_pipeline.py::test_white_balance_and_exposure_are_gains_on_the_scene_scale` | PASS |
| 2 | Exposure keeps the hue of linear values | `test_exposure_keeps_the_hue_of_linear_values` | PASS |
| 3 | Four saturated-color Lightroom fixtures (Temperature ±100, Tint +100, Exposure +2) | `test_render_white_balance_and_exposure_match_lightroom_on_saturated_colors` | PASS |
| 4 | No hue tables; older files with them load | `test_response.py::test_white_balance_has_no_hue_tables_and_older_files_with_them_still_load` | PASS |
| 5 | Calibration recovers stops on the scene scale | `test_calibration.py::test_measure_response_recovers_white_balance_stops_on_the_scene_scale` | PASS |
| 6 | WB bakes under 0.75, each under 1.6; Exposure under 0.56, each under 1.0; presets mean ≤ 1.43 (local) | `tests/private/test_wb_exposure_vs_bakes.py`, `test_pipeline_vs_lightroom.py` | PASS |

- RED `722fbfb`: 8 public tests failed (fixtures off by up to 0.156, exposure hue drifting, hue
  tables present). Private: Temperature −100 5.88, Exposure +2 1.76.
- GREEN: 424 pass (412 public), coverage 98%.
- `test_tone_stages_keep_each_colors_hue` no longer lists exposure, since it keeps linear hue, not
  encoded hue.
- WB bakes 2.08 → 0.70 (Temperature −100 5.88 → 1.48, +100 3.07 → 1.06, Tint ±100 1.0);
  Exposure 0.87 → 0.51 (+2 1.76 → 0.86); all 147 bakes 0.65 → 0.52.
- Private presets: median 0.88, mean 1.40 → 1.38.

## The B&W mixer as Lightroom's per-hue gray between min and max

Journey: as the maintainer, I want a black-and-white look's mixer to turn colors into the grays
Lightroom gives, so a B&W film look matches its original. Formulas come from study of the
shipped Lightroom engine (behaviour only); the code and the band defaults are our own derivation.

What Lightroom does, now in `pipeline.grayscale`:
- It runs on the HSL stage's scene scale (`_to_scene`, units of `K_SCENE`) and uses the same
  split into min, max and hexcone hue.
- The gray is `lo + w × (hi − lo)`. The weight `w` comes from a 360-entry hue table of the eight
  band weights, linear between the band centers.
- A band's default weight is the scene luminance (`_TINT_LUMA`) of its center's pure color, for
  example 0.2881 for red and 0.7119/6 for blue at 230°. So the neutral mix is luminance at each
  center. These eight values are derived in `_mixer_defaults` and match Lightroom's to 6 digits.
- A slider `s` (−1..1) is first flattened around 0 (`s × (1 − k + k|s|)`, with
  `k = (1 − d²/2)/2`). It then follows the parabola through (−1, −2), (0, d) and (1, d + 2.25).
- Negative weights put the gray below the min along a perceptual curve (linear below 0,
  `3x/(6.75x + 0.25)` up to 1/9, `0.75x + 0.25` above), so it darkens without going negative.
  Grays are unchanged, and the result is clipped to 0..1 on that scale.
- `K_MIXER` is gone, and `K_SCENE` is now the only fitted constant.
- Known gap: from process version 15.4 Lightroom uses another hue table. It isn't plain
  luminance on the smooth hue model (that guess scores 1.32 on the B&W presets against 0.63 for
  the linear table), so every version uses the linear table for now. Measuring it needs a B&W
  bake at process version 15.4.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Neutral mix is scene luminance at all eight band centers | `test_pipeline.py::test_neutral_mix_gives_scene_luminance_at_each_band_center` | PASS |
| 2 | +100 adds 2.25 min-to-max gaps; −100 darkens monotonically and stays ≥ 0; grays unchanged | `test_mixer_plus_100_adds_two_and_a_quarter_gaps_and_minus_100_darkens_smoothly` | PASS |
| 3 | Four Lightroom fixtures (Neutral, Yellow, Blue, Red +100) | `test_render_bw_mixer_matches_lightroom` | PASS |
| 4 | No fitted mixer strength; `K_SCENE` is the only fitted constant | `test_calibration.py::test_bw_mixer_is_reconstructed_not_fitted` | PASS |
| 5 | B&W bakes under 0.40, each under 0.55; presets median ≤ 0.95, mean ≤ 1.38 (local) | `tests/private/test_bw_vs_bakes.py`, `test_pipeline_vs_lightroom.py` | PASS |

- RED `e292ccf`: 14 public tests failed (band-center gray off by up to 0.83, fixtures by up to
  0.17, `K_MIXER` present). Private: BW Neutral Mix 2.54, presets mean 1.38.
- GREEN: 439 pass (426 public), coverage 98%. The fit tests now fit `K_SCENE` from the Vibrance
  bakes.
- B&W bakes 3.42 → 0.34 (every band 0.27–0.46); all 147 bakes 0.52 → 0.33.
- Private presets: median 0.88 → 0.87, mean 1.38 → 1.34 (the one scored B&W preset 2.80 → 0.63).

## Task 6b, step 1: spatial calibration kit (`colorlab.spatial_calibration`)

Journey: as the maintainer, I build one Lightroom import of synthetic charts, each moving one
spatial slider. I export them all at once, and `collect` tells me which exports are missing,
unedited or resized, so the measurements start from clean data.

Amounts come from what the private presets use:
- Highlights/Shadows/Clarity/Texture/Dehaze: ±10 to ±40, swept at ±25/±50/±100.
- Grain: 20–40 at size 25.
- Vignette: about −20, Style 1.
- Sharpness: 25 at radius 1.0.
- Noise reduction: 15–40.

| # | What is guaranteed | Test (`tests/test_spatial_calibration.py`) | Result |
|---|---|---|---|
| 1 | Three full-frame charts in 0–1 with region maps; flat regions are exactly uniform | `test_every_chart_is_a_full_frame_image_in_range`, `test_regions_lie_inside_the_frame_and_flat_regions_are_uniform` | PASS |
| 2 | `scene` holds flat levels from <0.05 to >0.9, gratings, a ramp and colors; `levels` holds flat bands and deterministic luma/color noise; `checker` puts all four grays at every radius | `test_scene_*`, `test_levels_*`, `test_checker_*` | PASS |
| 3 | 70 uniquely named variants, with an identity baseline per chart; each slider swept at its amounts; each family on the right chart | `test_variant_names_*`, `test_every_chart_has_an_identity_baseline`, `test_each_spatial_slider_is_swept`, `test_local_tone_variants_*` | PASS |
| 4 | Grain, vignette and sharpening variants set their whole slider family (Lightroom may ignore a lone member); vignette shape and grain size/roughness are swept | `test_grain_vignette_and_sharpening_*`, `test_vignette_shape_*` | PASS |
| 5 | The kit writes a preset (group "FilmSim Spatial Calibration") and a 16-bit chart TIFF per variant. The TIFF embeds the settings as a sidecar with `crs:AlreadyApplied="False"`. The kit refuses a non-empty folder | `test_kit_*` | PASS |
| 6 | `collect` copies edited exports, reports missing ones and unedited non-baseline ones, and rejects resized exports | `test_collect_*` | PASS |
| 7 | The CLI `kit`/`collect` summaries; errors exit 1 | `test_cli_*` | PASS |

- RED `012dd16`: collection failed with `ModuleNotFoundError: colorlab.spatial_calibration`.
- GREEN: 28 passed. Full suite (public and private): 351 passed, 98% coverage (`spatial_calibration` 99%).
- Refactors: `bake.write_rgb_tiff16` and `bake.read_rgb_tiff` are now shared with the Hald
  helpers, and `calibration.variant_xmp` takes a `group`. The bake and calibration tests still pass.
- Open question: Lightroom's local-tone radii may scale with image size. The charts are 1536×1024;
  a second size may be needed once the first measurements are in.

### Image-size test kit (`--scale-test`)

Journey: as the maintainer, I can find out whether Lightroom's spatial effects scale with image
size. The same charts are rendered at 768×512 and 3072×2048 (half and double the frame), with the
same settings as the 1536×1024 kit.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | `charts(size)` scales every feature with the frame: region boxes, bar periods and checker cell | `test_charts_scale_with_the_frame` | PASS |
| 2 | `SCALE_VARIANTS` repeat 11 base variants (3 identities, Shadows, Clarity ±50, Texture, Dehaze, Sharpen, Grain, Vignette) at both sizes with identical settings | `test_scale_variants_repeat_base_settings_at_each_size` | PASS |
| 3 | The kit writes each image at its own size and records it per manifest entry; `collect` checks each entry against its own size | `test_scale_kit_writes_each_image_at_its_own_size_and_collect_checks_it` | PASS |
| 4 | `kit --scale-test` writes the size-test variants | `test_cli_kit_scale_test` | PASS |

- RED `57d8897`: `ImportError: cannot import name 'SCALE_SIZES'`.
- GREEN: 32 spatial tests pass; full suite 355 passed, 98% coverage.
- The kit is built at `out/spatial-scale-kit`: 22 images, 421 MB.

## Film halation and grain, sized on the film (`colorlab.film`)

Journey: as a photographer using the Emulsion camera, I want halation around lights and film
grain that look the same in the preview and in the full-size capture, and that depend on the
film format rather than on the sensor's pixel count.

Ported from spektrafilm (Andrea Volpato, GPL-3.0: `model/diffusion.py`, `model/grain.py`, the
default parameters in `runtime/params_schema.py`) and darktable's port of it
(`src/common/spektra_core.c`, GPL-3.0-or-later), credited in the module header. No spektrafilm
profile data (CC BY-SA 4.0) is used.

- `halation` on scene-linear light, before the look: an optional highlight boost, then
  energy-preserving emulsion scatter (Gaussian core plus an exponential tail drawn as three
  Gaussians), then halation: `strength` (R 0.05, G 0.015, B 0) of the light added back as three
  bounces of width 65 µm × √k, weighted 4/7, 2/7, 1/7, renormalized so flat areas keep their level.
- `boost_highlights` follows darktable's version, anchored to mid-gray × 2^protect rather than
  to the frame's brightest pixel (spektrafilm's), so a pixel's boost never depends on the rest
  of the frame.
- `grain` on the finished image, as a positive transparency: density `-log10(linear)` is made
  of grains. spektrafilm draws a Poisson count of grains and then a binomial of it; that is the
  same as one Poisson draw, so the port draws that. Mean density is kept and the variance is
  `(D + Dmin) × (Dtotal / n) × (1 − uniformity × p)`. Clear highlights and black shadows are
  smooth; midtones are grainiest.
- All sizes are micrometres on the film; `pixel_size_um` = frame long side / pixels (36 mm
  for 135). Blurs clamp at the edges, as a GPU texture does.
- `python -m colorlab.film apply in.tif out.tif` previews both on an sRGB TIFF.

| # | What is guaranteed | Test (`tests/test_film.py`) | Result |
|---|---|---|---|
| 1 | Pixel size from format and frame | `test_pixel_size_is_the_frames_long_side_over_its_pixels` | PASS |
| 2 | Blurs keep light and flat areas; Gaussian width; exponential PSF's E[r²] = 6L² and encircled energy | `test_gaussian_blur_…`, `test_exponential_blur_approximates_an_exponential_psf` | PASS |
| 3 | Halation keeps flat areas and total light; red > green, no blue | `test_halation_keeps_flat_areas_and_with_defaults_is_red_then_green_never_blue` | PASS |
| 4 | Halo energy = strength × amount; bounce widths √k with weights 4/7, 2/7, 1/7 | `test_halation_adds_strength_times_the_light_as_bounces_of_widening_gaussians` | PASS |
| 5 | Same halo (within 3%) at 5 µm and 10 µm pixels | `test_halation_is_sized_on_the_film_so_every_resolution_gets_the_same_halo` | PASS |
| 6 | Boost: identity below the protected range, 2^boost at the top, C¹ tangent, independent of the frame | `test_highlight_boost_is_anchored_to_scene_exposure_not_to_the_frame`, `test_halation_feeds_boosted_highlights_into_the_halo` | PASS |
| 7 | Grain keeps the mean density and has the particle model's variance (D 0 to 2.2) | `test_grain_keeps_the_mean_density_and_has_the_particle_models_variance` | PASS |
| 8 | 2 × 2-binned grain at 5 µm matches grain at 10 µm | `test_grain_is_sized_on_the_film_so_averaged_fine_grain_matches_coarse_pixels` | PASS |
| 9 | Variance ∝ particle area; scan blur scales it by Σw² | `test_bigger_particles_give_stronger_grain_and_the_scan_blur_softens_it` | PASS |
| 10 | Seeded, deterministic, channels uncorrelated | `test_grain_is_deterministic_per_seed_and_independent_per_channel` | PASS |
| 11 | Grain strongest in midtones; image and CLI paths | `test_grain_on_a_positive_…`, `test_apply_to_image_…`, `test_cli_…` | PASS |

- RED `32d7f81`: `ModuleNotFoundError: No module named 'colorlab.film'`.
- GREEN `780e2d5`: 21 film tests pass; full suite 460 passed (447 public), coverage 98% (`film.py` 100%).
- A synthetic 3000 × 2000 night scene renders in 41 s (reference speed; the app uses shaders), with
  means kept (linear 0.1887 → 0.1889).
- Known gaps: spektrafilm's sublayer grain, dye-cloud blur and micro-structure clumping aren't
  ported. Grain is applied to the final positive, not to a simulated negative before printing.
  The defaults are spektrafilm's generic ones, not tuned to any look.

## Lightroom's tone methods: Whites/Blacks with exposure, curves by process version

Journey: as a photographer using an Emulsion look made from a Lightroom preset, I want strong
Whites, Blacks and curve settings to tint saturated colors the way Lightroom does, so a look's
deep shadows and bright highlights don't drift in hue.

Neutral grays were already exact (0.01 ΔE) on every tone bake. The errors came only from how
the gray curves reach colors. Each slider family was tried on the encoded, linear and scene
scales, by hue-preserving min/max, per channel and as one ratio for all channels. What matched
the bakes (all clean-room code, our own names):

- `whites_blacks` runs first in `ORDER`, where Lightroom applies them with exposure, before
  saturation and HSL:
  - Whites +: the curve on each color's linear min and max.
  - Whites − and Blacks +: one gain for all channels on the scene scale, the curve's ratio at
    the brightness key (R + 2G + B)/4.
  - Blacks −: per channel on the scene scale, so a saturated color's dark channels reach black
    first.
- `curve_tone` handles Contrast, the parametric and the master point curve:
  - Before PV 15.4 the curve maps each color's linear min and max.
  - From 15.4 Lightroom uses a luminance-mode stage that isn't reconstructed. Encoded min/max
    pulled toward the middle channel (`rgb_tone(pull=True)`) is the measured stand-in. On the
    24 PV 15.4 presets it scored mean 1.26, against 1.34 for plain encoded min/max and 1.44 for
    linear.

| # | What is guaranteed | Test (`tests/test_pipeline.py`) | Result |
|---|---|---|---|
| 1 | Before 15.4, curves and Whites + keep the hue of linear values; grays are the same for every version | `test_tone_curves_keep_the_hue_of_linear_values_before_15_4` | PASS |
| 2 | From 15.4, curves match an independent solve of the pulled min/max; same for later versions | `test_from_15_4_curves_pull_min_and_max_toward_the_middle_on_encoded_values` | PASS |
| 3 | The pull changes nothing for an identity curve, and grays get the curve exactly | `test_pulled_rgb_tone_with_an_identity_curve_changes_nothing` | PASS |
| 4 | Whites − and Blacks + scale all channels by one scene ratio | `test_whites_minus_and_blacks_plus_scale_all_channels_by_one_scene_ratio` | PASS |
| 5 | Blacks − maps each channel like a gray on the scene scale | `test_blacks_minus_maps_each_channel_like_a_gray_on_the_scene_scale` | PASS |
| 6 | Grays follow the measured curves; range checks | `test_whites_and_blacks_keep_grays_on_lightrooms_measured_curves` | PASS |
| 7 | Whites/Blacks run first, before saturation | `test_render_applies_whites_and_blacks_in_the_exposure_stage_before_saturation`, `test_stages_run_in_lightrooms_order` | PASS |
| 8 | Six Lightroom bakes (Whites ±100, Blacks ±100, Contrast +100, Parametric Darks −100) at four saturated colors, within 0.015 | `test_render_tone_sliders_match_lightroom_on_saturated_colors` | PASS |
| 9 | Local: Whites/Blacks bakes max < 0.45, mean < 0.16; Contrast/parametric/curve bakes max < 0.30, mean < 0.12; presets median ≤ 0.80, mean ≤ 1.18 | `tests/private/test_tone_vs_bakes.py`, `tests/private/test_pipeline_vs_lightroom.py` | PASS |

- RED `b2e06a6`:
  - `test_pipeline.py` fails to import `whites_blacks`.
  - The local tests fail at runtime: Blacks +100 2.61 > 0.45, Parametric Darks −100 1.54 > 0.30,
    presets median 0.865 > 0.80.
- GREEN `587203e`:
  - 476 passed (461 public), coverage 98% (`pipeline.py` 99%).
  - Two tests were fixed with the implementation: the old `ORDER` test now lists `whites_blacks`
    first, and the before-saturation test renders in the working space.
- Results:
  - 147 bakes: 0.331 → 0.183. Tone bakes mean 0.048.
  - Blacks +100: 2.61 → 0.20. Whites −100: 2.31 → 0.29. Parametric Darks −100: 1.54 → 0.01.
    Curve Master S: 0.64 → 0.01.
  - The worst tone bake is now Blacks −100 at 0.39.
  - Presets: median 0.865 → 0.750, mean 1.335 → 1.123. 25 got better and 9 got worse.
  - By process version (mean): PV 11 (15 presets) 0.55 → 0.58, PV 15.4 (24) 1.68 → 1.26
    (median 1.06 → 0.67), PV 6.7 (5) 2.02 → 2.08.
- Known gaps:
  - Lightroom's 15.4 curve stage: reconstructed in the next section. The pulled method turned out
    to be the stage itself, not a stand-in.
  - All 9 presets that got worse have a custom master point curve, their own or a look's. The
    worst got worse by 0.68, a PV 6.7 preset with a heavy curve. On three PV 11 ones, the pulled
    method fits better than linear (for example 0.65 against 0.88), although the single-curve
    bakes favour linear (0.01). So real presets' curves seem to interact with something the
    single-slider bakes don't isolate. That's open.
  - Whites − and Blacks ± leave 0.1–0.4 on the extremes.

## PV 15.4's curve stage and its luminance mode (Refine Saturation)

Journey: as a photographer using an Emulsion look made from a PV 15.4 Lightroom preset, I want
its tone curve, including a lowered Refine Saturation, to change colors the way Lightroom does.

What Lightroom does from process version 15.4, studied from the decompiled tone stages
(behaviour only; clean-room code, our own names):

- Contrast, the parametric curve and the point curve go to a second tone stage as **one**
  combined curve. That stage encodes linear values with the sRGB curve (a slope-extended
  encode, our working encoding), applies the curve with the pulled min/max method, and decodes.
- The pull is linear in (max, min) for a fixed middle place, so pulled tones compose exactly:
  the three curves applied in turn give the same result. So the method the previous section
  called a stand-in *is* this stage. Checked on the 24 PV 15.4 presets: one combined curve
  scores identically (median 0.750, mean 1.123 overall). Reversing the curve order is worse
  (0.797 / 1.157).
- The stage's luminance mode runs only for the point curve, and only when `CurveRefineSaturation`
  is below its default of 100. The point curve then leaves the combined curve for a stage of its
  own with blend `(100 − value)/100`:
  1. Each channel goes through the curve.
  2. Every channel gets the same shift: the curved values' 0.30 R + 0.59 G + 0.11 B luma minus
     the input's luma.
  3. A result below 0 or above 1 is scaled toward that luma until it fits.
  4. The result is mixed with the pulled tone.
- All 28 private presets that carry the key use 100, so their renders don't change. No bake
  covers PV 15.4 yet, so the luminance mode is reconstructed but not yet checked against
  Lightroom.
- Adding the pull to Whites + for non-Camera profiles at 15.4, which the decompile suggests,
  made 3 presets worse (by up to 0.16), so it isn't in.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Pulled tones compose: one combined curve equals the curves in turn | `test_pulled_tones_compose_so_one_combined_curve_equals_the_curves_in_turn` | PASS (also before the change) |
| 2 | Luminance mode at blend 0 is the pulled tone; grays get the curve at every blend | `test_luma_curve_at_blend_0_is_the_pulled_tone_and_keeps_grays_on_the_curve` | PASS |
| 3 | One luma shift for all channels, scaled into range toward the curved luma, blended; matches an independent per-pixel solve | `test_luma_curve_shifts_every_channel_by_the_curved_luma_and_scales_back_into_range` (blend 1, 0.4) | PASS |
| 4 | Refine Saturation 100 or absent changes nothing; 0 and 25 send only the master curve through the luminance mode, after Contrast; PV 11 ignores it; range check | `test_refine_saturation_below_100_sends_the_master_curve_through_the_luma_path_from_15_4` | PASS |
| 5 | 11 PV 15.4 calibration variants, each with 2+ keys so the measured shapes stay PV 11's | `test_process_version_15_4_variants_check_the_15_4_tone_stages_and_stay_out_of_the_measured_shapes` | PASS |

- RED `a2bbc8f`: 5 failed, 208 passed. Four pipeline tests fail with `ImportError: luma_curve`,
  and the calibration test fails because there's no `Cal PV15 Contrast +100` variant.
- GREEN `d4a3dc5`: 482 passed (467 public), coverage 98% (`pipeline.py` 99%, `calibration.py` 98%).
- Kit: `out/cal-presets-pv15` (11 presets, from `presets … --only-missing out/cal-luts`) and
  `out/cal-pv15-kit`. It's waiting for a Lightroom export, then
  `bake collect <exports> out/cal-pv15-kit out/cal-luts`.
- What the bakes will test (our PV 15.4 model against our PV 11 model, mean / max ΔE):

  | Variant | Mean | Max |
  |---|---|---|
  | Contrast +100 | 0.97 | 6.2 |
  | Parametric Darks −100 | 1.61 | 8.2 |
  | Master S | 0.74 | 4.6 |
  | Refine 0 | 3.18 | 10.0 |
  | Refine 50 | 1.62 | 5.1 |
  | Contrast +50 with Refine 0 | 2.97 | 9.9 |

  Whites/Blacks ±100 and the red curve are predicted the same as PV 11 (0.00), so those bakes
  test that assumption.

### The PV 15.4 bakes (2026-11-18)

- The 11 exports came back as ProPhoto RGB (gamma 1.8), not sRGB, so every bake first scored
  about 8 ΔE, grays included. `bake collect` doesn't read the embedded ICC profile. The raw cubes
  are kept in `out/cal-luts-pv15-prophoto`; the ones in `out/cal-luts` were converted to sRGB with
  a plain clip, which is what Lightroom's own sRGB export does (0.006 ΔE on the PV 11 bakes).
- Our model against Lightroom, mean ΔE2000 on a 17³ grid:

  | Variant | Ours | Our PV 11 model would give |
  |---|---|---|
  | Contrast +100 | **0.012** | 0.97 |
  | Parametric Darks −100 | **0.014** | 1.61 |
  | Curve Master S | **0.010** | 0.74 |
  | Curve Red | **0.008** | 0.00 |
  | Master S, Refine 0 | **0.008** | 3.18 |
  | Master S, Refine 50 | **0.008** | 1.62 |
  | Contrast +50, Master S, Refine 0 | **0.011** | 2.97 |
  | Whites +100 | 1.851 (max 14.2) | same |
  | Whites −100 | 0.348 | same |
  | Blacks +100 | 0.287 | same |
  | Blacks −100 | 0.610 | same |

- The combined pulled curve on sRGB-encoded values and the luminance mode are confirmed to 0.01.
- Known gap, Whites +100 at 15.4: Lightroom's own PV 11 and PV 15.4 bakes differ by 1.85, and the
  PV 11 bake matches our model to 0.011. At 15.4 Lightroom lifts the low channel of bright
  saturated colors toward white: input (0.438, 0, 0.812) gives (0.652, 0.387, 1.0), against our
  (0.58, 0.001, 1.0). The error averages 3.46 for saturation 0.75–1 with max ≥ 0.8. Neither the
  pull on linear values (1.71) nor on encoded values (1.96) explains it. The other three tone
  extremes differ between Lightroom's PV 11 and 15.4 bakes by 0.13–0.32, so they move a little too.
- Private tests: `test_process_version_15_4_curve_stage_and_luminance_mode_match_lightroom`
  (7 bakes, max < 0.05) and `test_process_version_15_4_whites_and_blacks_known_gaps`, which pins
  the gap so it can't silently grow. 17 private tests pass.

## Clarity and Texture (`colorlab.local_contrast`)

Journey: as a photographer using an Emulsion look made from a Lightroom preset with Clarity or
Texture, I want the camera to render them the way Lightroom does, at any capture size.

### What Lightroom does (measured on `out/spatial-exports` and `out/spatial-scale-exports`)

- Clarity changes only luminance. Carrying Lightroom's own luminance change to the identity
  export as a linear-light ratio reproduces its export to 0.107 (+100) and 0.061 (−100).
- Clarity follows the frame. The −50 halo at the 0.35/0.95 edge has the same profile at 768, 1536
  and 3072 px wide when offsets are scaled by the width.
- Negative Clarity leaves flat patches unchanged. Next to a bright area a dark one is lifted, up to
  +0.19 (encoded) over about 60 px at 1536, while the bright side moves −0.04. On 0.2/0.8 bars both
  sides move toward each other. So the pull is weighted toward the midtones: 0.35 and 0.8 move a
  lot, 0.2 and 0.95 little.
- Positive Clarity darkens the midtones of the base: a ramp's 0.23 comes out at 0.14 and 0.8 at
  0.73 at +100, while black and white stay put. Both stripes of wide 0.2/0.8 bars darken by about
  0.1. Fine detail rises (2 px bars ×1.46), and there is a wide, one-sided local contrast that
  stays unmodelled (below).
- Texture is fixed in pixels: radius 16 fits better than a frame-scaled radius at 768 (0.52 vs
  0.58) and 3072 (0.11 vs 0.25). Luminance alone leaves 0.24 at ±100, so Texture also acts on
  chroma: it runs on Y, Cb and Cr.

### The model

- Clarity, on `y = Y^(1/2.2)` with a Gaussian base `M` (sigma = width / 96) and a fine detail
  (sigma = width / 1536, at least 0.5 px):
  `y' = y + T(M) + D(y)·(y − M) + F(y)·(y − fine(y))`.
  - `D`, `F` and `T` are 11-knot tables, fitted at ±100 by least squares with a second-difference
    penalty (λ = 0.03; unpenalized they gave 0.496/1.791 against 0.497/1.853). They scale as
    |amount/100| to the powers 0.86, 1.34 (negative) and 0.98, 0.875, 0.93 (positive), fitted
    at ±25 and ±50.
  - Negative uses `D` only (a midtone bell down to −0.78) and a small `F`. Positive adds the base
    curve `T` (−0.03 to −0.09).
- Texture is a guided filter (radius 16, eps 0.01, guided by Y) on Y, Cb and Cr:
  `c' = c + g·(c − GF(c))`, with g = 0.617·a^0.56 for positive amounts and −0.621·a^0.73 for
  negative ones (a = |amount|/100).

### Rejected: the local Laplacian filter for Clarity

- A fast local Laplacian (Paris et al. 2011, the sampled form used by darktable's
  `locallaplacian.c`) was fitted first, with an asymmetric capped pull (negative) or an
  edge-aware detail boost plus a curve on the coarse residual (positive), at 3 to 6 levels.
  - Best: Clarity −100 0.85, +100 2.06 (free residual curve) or 2.46 (parametric curve).
  - Its asymmetric pull was put on the wrong side of the edge: at the 0.35/0.95 edge it lifted
    the dark side +0.05 and lowered the bright side −0.15, where Lightroom gives +0.19/−0.04.
    It is halo-free by design, and Lightroom's Clarity has a wide halo.
- It stays in the module as a tested filter, for Highlights/Shadows: Lightroom's Shadows shows no
  halo. A border bug turned up along the way: the zero-insertion upsample wasn't normalized, so a
  residual curve blew up the corners. It is fixed by dividing by the upsampled weight.
- Also tried and rejected for Texture: per-band Laplacian gains (R² 0.66, 0.50 ΔE at +100) and a
  luma-only guided filter (0.44).

### Results (mean ΔE2000 on the 1536 × 1024 scene chart; ours / Lightroom's change from identity)

| Slider | −100 | −50 | −25 | +25 | +50 | +100 |
|---|---|---|---|---|---|---|
| Clarity | 0.55 / 2.29 | 0.33 / 1.29 | 0.18 / 0.68 | 0.57 / 1.48 | 1.06 / 2.86 | 1.88 / 5.38 |
| Texture | 0.33 / 0.68 | 0.20 / 0.41 | 0.12 / 0.22 | 0.15 / 0.27 | 0.21 / 0.46 | 0.30 / 0.71 |

- At other sizes:

  | Size | Clarity −50 | Clarity +50 | Texture +50 |
  |---|---|---|---|
  | 768 px | 0.34 / 1.29 | 1.36 / 3.49 | 0.52 / 0.92 |
  | 3072 px | 0.33 / 1.29 | 1.06 / 2.70 | 0.11 / 0.26 |

- Bar contrast gain at Clarity +100 (Lightroom / ours): 2 px 1.46/1.20, 4 px 0.99/1.07, 8 px+
  within 0.03. At −100, every period is within 0.04.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Local Laplacian with the identity remap reproduces the image; a flat image stays flat; the residual curve applies; small detail is boosted while a big step keeps its height | `test_local_laplacian_*` (3) | PASS |
| 2 | Guided filter smooths texture, keeps strong edges, leaves a flat source flat | `test_guided_filter_smooths_texture_but_keeps_strong_edges` | PASS |
| 3 | Amount 0 is the identity; grays stay gray; range −100..100 | `test_zero_amounts_*`, `test_amounts_outside_*` | PASS |
| 4 | Clarity's reach doubles with the frame (base sigma width/96); Texture's doesn't (radius 16) | `test_clarity_is_relative_to_the_frame_and_texture_is_fixed_in_pixels` | PASS |
| 5 | Negative Clarity lifts the dark side of an edge more than it lowers the bright side; flats unchanged | `test_negative_clarity_lifts_the_dark_side_*` | PASS |
| 6 | Positive Clarity darkens a flat midtone gray, keeps black and white, raises 2 px bar contrast; negative lowers it | `test_positive_clarity_darkens_the_midtones_*` | PASS |
| 7 | Texture is monotone in amount on fine bars and leaves flats unchanged | `test_texture_raises_and_lowers_*` | PASS |
| 8 | `apply_to_image` is Texture then Clarity; the CLI round-trips a 16-bit TIFF and reports errors | `test_apply_to_image_and_cli_round_trip` | PASS |
| 9 | Clarity and Texture against Lightroom at 1536, 768 and 3072 px (private) | `tests/private/test_local_contrast_vs_lightroom.py` (3) | PASS |

- RED `48e180a`: the 11 public tests fail at collection with `ModuleNotFoundError:
  colorlab.local_contrast`; the other 467 pass.
- GREEN `d3dec13`: 478 public + 20 private pass, coverage 98% (`local_contrast.py` 99%).
  In GREEN, the RED test's `clarity_levels` check became `clarity_sigmas`, and the fine-bar check
  uses 2 px bars on a 1536 px frame, as on the chart, once Clarity no longer used pyramid levels.
- Known gaps:
  - Positive Clarity leaves an unmodelled wide (about 200 px at 1536), one-sided local contrast.
    It shows as ±0.07 gradients across the flat patches. Neither tone-of-base nor extra
    symmetric or one-sided scales from 64 to 512 px took +100 below 1.84.
  - The 2 px bar gain is 1.20 against 1.46.
  - Texture at 768 px keeps 0.52 of 0.92.
  - Neither effect is wired into `render` yet: they are image passes, and the app runs them as
    shader passes. Lightroom's order is Texture, then Clarity, around the saturation stage.

## Highlights and Shadows (`colorlab.local_contrast.highlights_shadows`)

Journey: as a photographer using an Emulsion look made from a preset with Highlights or Shadows
(most presets have both), I want the camera to recover highlights and open shadows the way
Lightroom does, at any capture size.

### What Lightroom does (measured on `out/spatial-exports` and `out/spatial-scale-exports`)

- It is mostly a luminance change. Carrying Lightroom's own luminance change to the identity
  export as a linear-light ratio reproduces its export to 0.11 (Shadows +100), 0.07 (−100), 0.36
  (Highlights +100) and 0.18 (−100). Colors carry differently: even with Lightroom's own
  luminance, the color patches keep 0.8–2.9.
- The other end of the range gets a plain curve: at Highlights −100 the 0.03–0.35 flat patches
  move by a constant −0.001 to −0.046.
- The targeted tones depend on the neighbourhood. At Highlights −100 the 0.8 flat patch changes
  from −0.23 at its top to −0.31 at its bottom, next to the 0.95 patch, over 256 px. At Shadows
  +100 the 0.1 patch changes from +0.27 to +0.17 toward the 0.2 patch below it.
- No halo: at Shadows +100 the dark side of the 0.35/0.95 edge is lifted +0.088 to +0.092 right
  up to 2 px from it, and the bright side moves +0.006 throughout.
- Fine detail keeps more of its contrast: at Highlights −100, 2 px bars keep 0.85 of their
  contrast and wide bars 0.57. At Shadows +100 the figures are 0.96 and 0.79.
- Bright saturated colors move toward white at Highlights +100: cyan (0.10, 0.75, 0.80) comes
  out at (0.54, 0.90, 0.94). This looks like the Whites + behavior at PV 15.4.
- The neighbourhood follows the frame. Shadows +50 at 768 and 3072 px fits better with a base of
  width/32 than with a fixed 48 px: 0.351 against 0.361 at 768, and 0.230 against 0.268 at 3072.

### The model

- On `y = Y^(1/2.2)`: `y' = y + T(p, B)`.
  - `p` is the pixel half blurred by 1 px: `(y + gauss(y, 1)) / 2`. This keeps more contrast in
    the finest detail.
  - `B` is a Gaussian base with sigma = width/32.
  - `T` is a 17 × 9 table (bilinear) for each slider at ±25, ±50 and ±100, linear in amount in
    between, with 0 at 0.
  - The tables are fitted by least squares with a second-difference penalty (λ = 1e-4 per
    pixel). Then each base column is made monotone in `p` with a slope of at least 0.2, so no
    tones swap order.
  - They are stored in `src/colorlab/data/local_tone.json`.
  - With both sliders set, the two changes add.
- The change is carried in sRGB-encoded values: luminance moves to its new encoded value, and
  each channel's difference from it is scaled by (new / old)^0.5.
- In `apply_to_image`, Highlights/Shadows run before Texture and Clarity.

### Tried and rejected

| Variant | Shadows +100 | Highlights −100 |
|---|---|---|
| 1-D table on the pixel (ratio carry) | 0.93 | 1.18 |
| 1-D table on the half-blurred pixel, 33 knots | 0.88 | 0.94 |
| 2-D, Gaussian base sigma 16 / 48 / 128 / 256 px | 0.66 / 0.54 / 0.62 / 0.71 | 0.77 / 0.75 / 0.87 / 0.97 |
| 2-D, guided-filter base (r 48–192) | 0.61–0.66 | 0.76–0.89 |
| Two bases (48 + 192 px) | 0.48 | 0.69 |
| Local Laplacian tone table (4–8 levels, sigma 0.05–0.3) | 1.12–1.31 | 1.37–1.67 |

- The local Laplacian, the candidate from the darktable study: the remap shifted detail by the
  table at the reference level and gave edges the full pixel curve, and the residual got the
  table too. Because it is linear in the table, it was fitted exactly, and still lost to the plain
  2-D table. Two bases gained less than 0.07, so one base is used.
- Per-channel curves and the RGB-tone method, pulled or not, scored worse on color than a ratio
  (Highlights −100 color patches 4.0–4.9 against 2.2). The encoded-values carry with exponent 0.5
  was the best single choice across both sliders (Highlights +100 0.54 against 0.49 for its own
  best).
- One table per direction scaled by a power of the amount (fitted at λ = 1e-3) left 0.28–0.64 at
  ±50, against 0.16–0.38 with a table per amount.
- The monotone guard costs 3.91 → 4.26 summed over the 12 exports, mostly Highlights −100
  (0.83 → 1.02). It is kept because unguarded tables had slopes down to −2 in cells the chart
  hardly covers, which would invert tones in photos.

### Results (mean ΔE2000 on the 1536 × 1024 scene chart; ours / Lightroom's change from identity)

| Slider | −100 | −50 | −25 | +25 | +50 | +100 |
|---|---|---|---|---|---|---|
| Shadows | 0.27 / 4.23 | 0.17 / 2.37 | 0.10 / 1.27 | 0.12 / 1.39 | 0.27 / 2.96 | 0.66 / 6.87 |
| Highlights | 1.02 / 9.72 | 0.40 / 4.45 | 0.19 / 2.12 | 0.19 / 1.97 | 0.33 / 3.62 | 0.55 / 6.14 |

- Shadows +50 at other sizes: 768 px 0.37 / 3.00, 3072 px 0.25 / 3.05.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Amount 0 is the identity; grays stay gray, alone and combined | `test_zero_amounts_change_nothing_and_grays_stay_gray` | PASS |
| 2 | Range −100..100 for both sliders | `test_amounts_outside_the_slider_range_are_rejected` | PASS |
| 3 | Shadows lifts a dark flat more at 25 < 50 < 100, darkens it at −100, leaves 0.95 within 0.015 | `test_shadows_moves_the_darks_and_leaves_the_brights` | PASS |
| 4 | Highlights lowers a 0.8 flat more at −25 < −50 < −100, raises it at +100, leaves 0.05 within 0.01 | `test_highlights_moves_the_brights_and_leaves_the_darks` | PASS |
| 5 | No halo: Shadows +100 lifts 3 px and 40 px from an edge alike | `test_the_change_is_edge_preserving` | PASS |
| 6 | 2 px bars keep more contrast than 64 px bars at Highlights −100 | `test_fine_detail_keeps_more_contrast_than_coarse_detail` | PASS |
| 7 | The base is width/32 | `test_the_base_follows_the_frame` | PASS |
| 8 | `apply_to_image` and the CLI take `--highlights` and `--shadows` | `test_apply_to_image_and_cli_take_highlights_and_shadows` | PASS |
| 9 | Against Lightroom at 1536 px for all 12 exports, and Shadows +50 at 768 and 3072 px (private) | `tests/private/test_local_tone_vs_lightroom.py` (3) | PASS |

- RED `6e08399`: the 8 tests in `tests/test_local_tone.py` fail at collection with `ImportError:
  cannot import name 'highlights_shadows'`; the other 478 public tests pass.
- GREEN `bad0f97`: 8/8 new tests; 486 public + 23 private pass; coverage 98% (`local_contrast.py` 99%).
- Known gaps:
  - Highlights −100 is 1.02, mostly the color patches and the gradients inside the bright flats.
  - Color carry: no single rule matched all four directions, and bright saturated colors moving
    toward white at Highlights + isn't modelled.
  - The tables come from one chart. Cells far from it (a bright pixel in a very dark
    neighbourhood) are smooth extrapolation, checked only for monotonicity.
  - Not wired into `render`. Lightroom applies these before the profile and the look; our pass
    runs on the encoded image like the other spatial passes.

## Vignette, sharpening, noise reduction, grain and Dehaze (`colorlab.finish`, `local_contrast.dehaze`)

Journey: as a photographer using an Emulsion look made from a preset, I want its vignette, grain,
sharpening, noise reduction and Dehaze to come out the way Lightroom renders them, so the look
in the camera matches the preset. All the spatial passes a preset names should run from one call.

### What Lightroom does (measured on `out/spatial-exports` and `out/spatial-scale-exports`)

- Vignette (checker chart):
  - The shape is an ellipse on frame-normalized coordinates. It is the same at 768, 1536 and
    3072 px.
  - Paint Overlay (style 3) at −50 multiplies the encoded value by `1 − 0.5 m`, whatever the level.
  - Highlight Priority (1) and Color Priority (2) give identical results on grays. Negative amounts
    darken bright tones less; positive amounts blend toward white.
  - Feather 0 is a hard edge at d ≈ 1.0, and Midpoint moves the edge.
  - Roundness −100 is a narrow band along the frame's border (a ratio of 0.79 in the outermost
    32 px column, 1.00 inside). Roundness +100 is a circle in pixels.
- Sharpening (scene chart):
  - The overshoot is asymmetric (the bright side is far larger) and saturates: Sharpen 100 is
    close to Sharpen 50.
  - It is fixed in pixels (768 px: 0.10 fixed against 0.42 scaled with the frame).
  - Detail 100 rings, and Masking 50 changes nothing on the chart's clean edges.
- Noise reduction (levels chart's noise band):
  - Luminance NR is threshold-like: faint noise goes before strong noise, and flat patches stay
    unchanged.
  - Color NR takes chroma sd from 0.025 to 0.0074 at 25, 50 and 100 alike.
- Grain:
  - It is monochrome noise added to encoded values, then clipped, with sd linear in the amount
    (0.0147/0.0368/0.0736/0.1460 at 10/25/50/100) and bit-identical across exports.
  - Size and Roughness change its texture (lag-1 correlation −0.13 to +0.25) and its strength.
  - On smaller frames it is finer and stronger: 768 px behaves like Size 10 at 1536 px.
- Dehaze (scene chart):
  - Luminance moves like a local tone curve: +100 takes the flats from 0.5 to 0.21 and has a soft
    gradient near edges.
  - Colors keep their linear-light difference from luminance with a gain, nearly the same for the
    three channels of a patch. At +100, (0.6, 0.45, 0.35) keeps 0.52 of it while its luminance
    falls to 0.27, so it saturates.

### The model

- `vignette`:
  - A weight `m` is read from the measured Paint-Overlay profile over `c0 + (d − c)/s`.
  - Midpoint and feather set `c` and `s` (measured at 0, 50 and 100, linear in between).
  - Roundness blends to a circle in pixels (+) or a superellipse growing into a rectangle (−).
  - Styles 1/2: a 6 value-knot × 11 weight-knot table per amount (±25/50/100), per channel.
  - The highlights slider adds the change measured at −50 with highlights 50 and 100.
  - Style 3: `v (1 + a m)` or `v + (1 − v) a m`.
- `sharpen`: `img + clip(k (Y − G_σ Y), −down, up)` on BT.601 luma.
  - σ = 0.75 · radius^0.8. `k`, `down` and `up` come from a table at amounts 25/50/100.
  - Radius scales `k` by radius^−1.1 and `up` by radius^−0.29.
  - Detail 100 scales σ by 0.78 and `k` by 0.7.
- `noise_reduction`:
  - Luminance: a guided filter on Y (r 3, eps 0.01), blended by (amount/100)^0.5. The fitted
    blends per export were 0.5/0.7/1.0.
  - Color: a Gaussian (σ 8 px) on Cb and Cr, at full strength from 25.
- `lightroom_grain`:
  - Seeded white noise filtered by `G_s − b G_2s`, normalized to unit sd, then scaled by
    sd · amount/50 and added to all channels.
  - `(s, b, sd)` come from tables over size (10/25/50/100) and roughness (0/50/100).
  - The size used is `size · (width/1536)^1.8`.
- `dehaze`:
  - Luminance: `y' = y + T(p, B)`, the same table form and base (width/32) as Highlights/Shadows.
    It is fitted by least squares under a monotone constraint (each base column non-decreasing in
    `p`, solved by projected gradient rather than projected after fitting).
  - Colors: `out_lin = Y' + (lin − Y) · g(y')`, with `g` an 11-knot curve per amount fitted on
    unclipped pixels with a smoothness penalty.
  - The tables are in `data/local_tone.json` (`dehaze`, `dehaze_chroma`).
- `apply_settings(img, preset.settings)` runs, in Lightroom's order: noise reduction,
  Highlights/Shadows, Dehaze, Texture, Clarity, sharpening, vignette, grain.
  - `python -m colorlab.finish apply preset.xmp in.tif out.tif` does the same from a preset.
- The fitted numbers are in `src/colorlab/data/finish.json`.

### Tried and rejected

- Vignette roundness as one superellipse family fitted at ±100. Roundness +100 scored
  0.40–0.62 and −100 scored 0.68–1.34, against Lightroom's own change of 0.64 (worse than doing
  nothing). The circle in pixels (0.16) and the rectangle with a narrow band (0.16) replaced it.
- Plain unsharp mask: Sharpen 50 left 0.36 of Lightroom's 0.43. The clamped mask leaves 0.063.
- Luminance NR as a Wiener-style soft threshold: |dY| 0.0054–0.0066 against the guided filter's
  0.0026–0.0056.
- Color NR as a guided filter on chroma: 0.0036 against the blur's 0.0023.
- Dehaze:
  - Encoded-difference chroma carry: +100 was 3.21 against 2.22 for the linear carry.
  - The Highlights/Shadows guard (projection with slope ≥ 0.2): −100 1.08, +100 3.36. Unguarded:
    0.69 and 2.18, but unguarded tables would invert tones where the chart is sparse. The
    constrained fit gives 0.83 and 2.22.
  - The dark channel prior from the darktable study wasn't tried. The chart response is
    reproduced to 0.33–0.83 except at +100.

### Results (mean ΔE2000; ours / Lightroom's change from identity)

| Export | Result | Export | Result |
|---|---|---|---|
| Vignette −100 | 0.21 / 13.89 | Vignette +100 | 0.08 / 10.18 |
| Vignette −50 | 0.13 / 7.48 | Vignette +50 | 0.13 / 4.31 |
| Vignette −25 | 0.07 / 3.56 | Vignette +25 | 0.08 / 1.31 |
| −50 Midpoint 0 | 0.47 / 15.79 | −50 Midpoint 100 | 0.15 / 0.57 |
| −50 Feather 0 | 0.57 / 5.66 | −50 Feather 100 | 0.16 / 4.59 |
| −50 Roundness +100 | 0.16 / 7.62 | −50 Roundness −100 | 0.16 / 0.64 |
| −50 Style 2 | 0.13 / 7.48 | −50 Style 3 | 0.32 / 8.40 |
| −50 Highlights 50 | 0.10 / 6.72 | −50 Highlights 100 | 0.09 / 6.26 |
| Sharpen 25 | 0.029 / 0.34 | Sharpen 50 | 0.063 / 0.43 |
| Sharpen 100 | 0.114 / 0.49 | 50 Radius 3.0 | 0.098 / 0.52 |
| 50 Detail 100 | 0.172 / 0.49 | 50 Masking 50 | 0.056 / 0.41 |
| Luminance NR 25 | 0.16 / 0.51 | Color NR 25 | 0.52 / 1.60 |
| Luminance NR 50 | 0.21 / 0.67 | Color NR 50 | 0.52 / 1.61 |
| Luminance NR 100 | 0.30 / 1.02 | Color NR 100 | 0.52 / 1.61 |
| Dehaze −100 | 0.83 / 23.36 | Dehaze +100 | 2.22 / 13.34 |
| Dehaze −50 | 0.62 / 10.56 | Dehaze +50 | 0.82 / 5.50 |
| Dehaze −25 | 0.39 / 4.97 | Dehaze +25 | 0.33 / 2.57 |

- Other frame sizes:
  - Vignette −50: 0.13 at 768 and 3072 px.
  - Sharpen 50: 0.10 / 0.63 at 768 px and 0.036 / 0.24 at 3072 px.
  - Dehaze +50: 0.86 / 5.43 at 768 px and 0.86 / 5.54 at 3072 px.
- Grain is compared by statistics, since its pattern isn't reproduced (sd and lag-1, ours /
  Lightroom):

  | Export | sd | lag-1 |
  |---|---|---|
  | Amount 10–100 | within 0.0001 | −0.065 / −0.060 |
  | Size 10 | 0.0839 / 0.0838 | −0.134 / −0.134 |
  | Size 50 | 0.0616 / 0.0616 | +0.056 / +0.052 |
  | Size 100 | 0.0481 / 0.0481 | +0.208 / +0.216 |
  | Roughness 0 | 0.1272 / 0.1274 | −0.164 / −0.160 |
  | Roughness 100 | 0.0726 / 0.0726 | +0.261 / +0.252 |
  | 768 px | 0.0840 / 0.0863 | −0.136 / −0.148 |
  | 3072 px | 0.0516 / 0.0513 | +0.169 / +0.185 |

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Vignette 0 is the identity; the center is kept; corners order by amount; grays stay gray | `test_vignette_darkens_or_lightens_the_corners_and_keeps_the_center` | PASS |
| 2 | Midpoint grows or shrinks the area, Feather 0 is steeper, +100 is round in pixels, −100 is a border band | `test_vignette_shape_follows_midpoint_feather_and_roundness` | PASS |
| 3 | Highlight Priority spares 0.95 more than Paint Overlay; the highlights slider protects more; range and style checks | `test_vignette_highlight_priority_spares_highlights_more_than_paint_overlay` | PASS |
| 4 | Sharpening overshoots mostly on the bright side, keeps flats, Radius 3 widens it; range check | `test_sharpening_overshoots_edges_mostly_on_the_bright_side_and_keeps_flats` | PASS |
| 5 | Sharpening grows with amount and is fixed in pixels | `test_sharpening_grows_with_amount_and_is_fixed_in_pixels` | PASS |
| 6 | Luminance NR smooths more at 25 < 50 < 100 and keeps edges | `test_luminance_noise_reduction_smooths_noise_monotonically_and_keeps_edges` | PASS |
| 7 | Color NR removes chroma noise, not luma noise; range check | `test_color_noise_reduction_removes_chroma_noise_but_not_luma_noise` | PASS |
| 8 | Grain is monochrome, seeded, sd 0.0736 at Grain 50 on 1536 px, doubling at 100 | `test_lightroom_grain_is_monochrome_deterministic_and_scales_with_amount` | PASS |
| 9 | Size and roughness set the texture; small frames get stronger grain | `test_lightroom_grain_size_roughness_and_frame` | PASS |
| 10 | Dehaze 0 is the identity; + darkens and saturates, − lifts and desaturates; grays stay gray | `test_dehaze_darkens_and_saturates_or_lifts_and_desaturates` | PASS |
| 11 | `apply_settings` runs the passes a preset names, deterministically per seed | `test_apply_settings_runs_the_spatial_passes_named_in_a_preset` | PASS |
| 12 | The CLI applies a preset's passes to a TIFF and rejects bad input | `test_finish_cli_applies_a_presets_spatial_passes` | PASS |
| 13 | Against Lightroom for all the exports above (private) | `tests/private/test_finish_vs_lightroom.py` (6) | PASS |

- RED `c9a62e4`: the tests in `tests/test_finish.py` fail at collection with
  `ModuleNotFoundError: No module named 'colorlab.finish'`; the other 486 public tests pass.
- GREEN: `e6a9e8a`: `tests/test_finish.py` 12 pass; public suite 498 pass at 97.96% coverage; private
  suite 29 pass
- One threshold changed between RED and GREEN. The RED test asked for a Size 100 lag-1 above 0.3.
  Lightroom's own export is 0.216, so the test now asks for more than 0.15.
- Known gaps:
  - Dehaze +100 is 2.22: the bright saturated patches and the gradients near edges.
  - Color NR is 0.52: Lightroom's chroma smoothing leaves more lag-1 correlation (about 0.4) than
    a Gaussian.
  - Vignette Feather 0 is 0.57 and Midpoint 0 is 0.47: the edge profile is the default one,
    stretched.
  - Sharpening Masking isn't modelled, and Detail 100's ringing is only approximated (0.17).
  - Grain reproduces Lightroom's statistics, not its pattern.
  - Style 2 is checked only on grays, where it equals style 1.
  - Not wired into `render`. The passes run on the encoded image after the look; Lightroom runs
    most of them before the profile.

## Whole-preset check against Lightroom (`colorlab.preset_check`, `finish.render_preset`)

Journey: as a photographer using an Emulsion look, I want it to match its preset as Lightroom
renders it in full, global color and spatial effects together, and to see where and why they
differ.

- 36 of the 44 private `.xmp` presets use spatial settings:

  | Setting | Presets |
  |---|---|
  | Highlights / Shadows | 27 / 25 |
  | Grain | 22 |
  | Clarity | 17 |
  | Sharpening | 16 |
  | Color NR / Luminance NR | 13 / 11 |
  | Texture | 10 |
  | Dehaze | 9 |
  | Post-crop vignette | 9 |

  None uses crop, lens profiles or lens vignetting. The Hald bakes zero all of these, so until
  now nothing measured them together.
- `finish.render_preset(preset, image, seed, local_first, look)`:
  - It runs the global look (`pipeline.render`, or `look`, for example Lightroom's own baked LUT),
    then `local_passes` (NR, Highlights/Shadows, Dehaze, Texture, Clarity, sharpening), then
    `finishing_passes` (vignette, grain).
  - With `local_first` the local passes run before the look. That is closer to Lightroom's
    pipeline, but the passes were fitted with no look.
  - `apply_settings` is `finishing_passes(local_passes(...))`.
- `bake.make_bake_xmp(..., group, keep_spatial)`:
  - `NEUTRALIZED_SETTINGS` is now `SPATIAL_SETTINGS` plus `GEOMETRY_SETTINGS`.
  - A full copy keeps the spatial ones and still neutralizes geometry, so the chart stays in place.
- `python -m colorlab.preset_check kit <presets> <kit>` writes a full copy of every normal `.xmp`
  preset (group "FilmSim Full Check", prefix "FilmSim Full - "). With it goes the 1536 × 1024 scene
  chart carrying that copy as a Lightroom sidecar, a manifest and a README.
- `compare <exports> <kit> <presets> [--luts out/...]` scores each export (mean ΔE2000), as
  `global`, `after`, `before` and their `*_smooth` versions.
  - The `*_smooth` scores blur both images (σ 2 px) first, since the grain pattern isn't
    reproduced.
  - With Lightroom's baked LUTs it scores the same as `lut_*`, which isolates the spatial passes
    from the global look.
  - It also lists missing exports and exports Lightroom left unchanged.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | A full copy keeps every spatial setting and still neutralizes geometry, in the named group | `test_full_copy_keeps_spatial_settings_and_still_neutralizes_geometry` | PASS |
| 2 | `render_preset` is the look alone for a plain preset; both orders compose as specified; `look` replaces the global render | `test_render_preset_runs_the_look_then_the_spatial_passes_in_either_order` | PASS |
| 3 | The kit holds full copies, charts with embedded sidecars, a manifest and a README; it skips Look presets and won't overwrite | `test_kit_holds_full_copies_and_charts_with_embedded_sidecars` | PASS |
| 4 | `compare` scores each order, finds missing and unchanged exports | `test_compare_scores_each_order_and_finds_missing_and_unchanged_exports` | PASS |
| 5 | With LUTs it scores the passes on Lightroom's own look | `test_compare_runs_the_spatial_passes_on_lightrooms_own_look_when_given_its_luts` | PASS |
| 6 | The CLI writes the kit and a JSON report and rejects bad input | `test_cli_writes_the_kit_and_a_json_report` | PASS |

- RED `d5e1a3b`: `tests/test_preset_check.py` fails at collection (`cannot import name
  'render_preset'`). The new bake test fails with `cannot import name 'GEOMETRY_SETTINGS'`, and
  the `render_preset` test fails on its import.
- GREEN `7f69705`: public suite 504 pass at 97.84% coverage; private suite 29 pass.
- The kit for the 44 private presets is `out/preset-kit` (397 MB). The results wait for its
  Lightroom export.

## Dehaze's dark channel half (`local_contrast.dehaze`)

Journey: as a photographer using a look with strong Dehaze, I want colors and large flat areas to
come out as Lightroom renders them. Dehaze +100 was the weakest spatial fit (2.22).

### What the +100 residual was (scene chart, by region)

- The flats' centres were too bright. Lightroom takes the 0.5 patch to 0.169 at its centre, while
  ours gave 0.235. Its change varies over about 250 px inside a flat patch, which a base of width/32
  (48 px) can't follow.
- Saturated colors: Lightroom keeps the main channel and crushes the others. Blue
  (0.15, 0.25, 0.85) becomes (0.00, 0.005, 0.828) and yellow (0.9, 0.8, 0.1) becomes
  (0.871, 0.763, 0.00); ours gave (0.04, 0.13, 0.56) and (0.81, 0.60, 0.00). The per-channel
  form `(I − A)/t + A` with one `t` per pixel reproduces them: blue needs t ≈ 0.87, yellow
  0.78, gray 0.5 0.60.

### The model

- Negative amounts are unchanged.
- Positive amounts mix the tone-table result with a dark channel result (He, Sun and Tang,
  2009), written from the paper's formula on encoded values:
  - haze map `D` = guided filter (guide: luma, radius width/16, eps 0.01) of the minimum over
    width/128 px of `0.65 · min(R, G, B) + 0.35 · luma`;
  - `t = max(1 − ω D / A, 0.05)` with A = 1.05, and `J = (I − A)/t + A`;
  - ω = 0.2 / 0.4 / 0.775 and the dark channel's share β = 0.2 / 0.5 / 0.8 at 25 / 50 / 100,
    linear in between.

### Tried and rejected

| Variant | +25 | +50 | +100 |
|---|---|---|---|
| Tone table and color gain alone (previous) | 0.33 | 0.82 | 2.22 |
| Per-pixel dark channel (no neighbourhood), best of encoded and linear | 0.66 | 1.39 | 4.36 |
| Dark channel with a smooth map alone | 0.43 | 0.84 | 1.70 |
| Blend of both (kept) | 0.31 | 0.71 | 1.60 |

- The per-pixel dark channel leaves saturated colors unchanged, since their darkest channel is
  near 0, while Lightroom darkens them.
- On the darkest channel alone the map scored 2.04 at +100. Mixing in luma (0.65 / 0.35) gave 1.70.
- Adding haze for negative amounts, `J = I t + A (1 − t)`, scored 2.5 / 4.8 / 7.7 at
  −25 / −50 / −100 against the tables' 0.39 / 0.62 / 0.83, so the tables stay.
- Fixed radii at 768 px: 1.24 against 0.82 with radii relative to the frame.

### Results (mean ΔE2000; ours / Lightroom's change)

- Dehaze +25 0.31 / 2.57, +50 0.71 / 5.50, +100 1.60 / 13.34; negative amounts unchanged
  (−100 0.83).
- Dehaze +50 at other sizes: 0.72 / 5.43 at 768 px, 0.73 / 5.54 at 3072 px.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Dehaze +100 keeps blue's main channel (> 0.7) and crushes red (< 0.05); grays stay gray | `test_dehaze_keeps_a_saturated_colors_main_channel_like_lightroom` | PASS |
| 2 | The haze map's radii are width/128 and width/16 | `test_dehaze_neighbourhood_follows_the_frame` | PASS |
| 3 | Against Lightroom, with limits tightened to +25 0.35, +50 0.76, +100 1.7 and frame sizes 0.8 (private) | `tests/private/test_finish_vs_lightroom.py::test_dehaze_matches_lightroom_on_the_scene_chart` | PASS |

- RED `cb095b2`: blue's main channel came out 0.468 (Lightroom 0.83), and `dehaze_radii` didn't
  exist.
- GREEN: `cbc4577`: public suite 506 pass at 98% coverage; private `test_finish_vs_lightroom` 6
  pass
- Known gap: +100 is still 1.60, mostly the bright saturated patches and the ramp.

## Coverage and known gaps

- 96.4% public / 97.0% including private tests. Uncovered: a few defensive branches
  (truncated-TIFF `struct.error`, non-UTF-8 XMP packets, empty-sample curves).
- Pipeline: stage strengths (`K_*`) are first guesses until single-slider calibration. Not yet modelled:
  the spatial passes in `render` (task 6b's passes run on the encoded image after the look: `colorlab.local_contrast`, `colorlab.finish`), camera calibration sliders (3 presets use them),
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
