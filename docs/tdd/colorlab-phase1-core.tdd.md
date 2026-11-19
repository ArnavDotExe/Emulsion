# TDD evidence: colorlab Phase 1 core

**Source plan:** the Phase 1 "Python color lab" plan agreed in the planning session.
This report covers tasks 1–5 (repo setup, color spaces, LUTs, the RGBTable decoder and
the preset reader) and task 6a (global color pipeline). Tasks 7 (DNG previews), 8 (`.fsim.json`) and 9 (the `colorlab` command) are done too.

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

## Whole-preset results: pass order and the look profile's local tone (`finish.render_preset`)

Journey: as a photographer, I want a whole preset in Emulsion (global look, local contrast,
vignette, grain) to look like Lightroom's export of it, not just its global color.

The user exported `out/preset-kit` (44 presets on the 1536 × 1024 scene chart, 16-bit sRGB).
`preset_check compare` scored them. Scores are mean ΔE2000 after a σ 2 px blur of both images,
since the grain pattern isn't reproduced.

### What the first run showed

- Running the local passes before the look won on 30 presets and lost on none. The blurred median
  was 0.97 against 1.52 for after the look.
- Six presets set no spatial sliders. Four of them still disagreed with Lightroom's own baked LUT:
  2.70, 2.03, 1.43 and 0.42.
  - The difference was a near-uniform shift (a 0.8 gray came out at 0.705, against the LUT's 0.78).
  - Their embedded looks are stubs: the preset names a profile by UUID, and the profile, byte for
    byte the same as the one we read, carries its own Highlights2012 (−10 to −30) and Shadows2012
    (0 to +10).
  - Highlights/Shadows is a neighbourhood pass, so it acts differently on a Hald image than on a
    photo. Our global pipeline folds a look's global settings in, but this one was never applied
    as a local pass.
  - The two matching presets' profiles carry Sharpness 40 and still match the LUT at 0.03, so
    Lightroom ignores a look's sharpening.
- Dropping one spatial setting at a time didn't explain the worst presets. Moving the post-crop
  vignette before the look did: over the 33 presets with grain or a vignette, on Lightroom's LUTs,
  the mean went from 1.13 to 0.93, and the worst preset from 3.13 to 1.75. Grain before the look
  was worse (mean 1.18), so it stays last.
- Our grain matches Lightroom's mean and sd at every level of the levels chart (0.05 to 0.97), so the
  grain model wasn't the problem.

### The changes

- `finish.spatial_settings(preset)` adds the look profile's Highlights2012 and Shadows2012 at the
  look's amount, clamped to −100..100 (`LOOK_SPATIAL_SETTINGS`).
- `render_preset` defaults to `local_first=True` and computes
  `grain(look(vignette(local passes(image))))`. `local_first=False` keeps everything after the look.
  `finishing_passes` is split into `vignette_pass` and `grain_pass`.
- `compare` scores one preset at a time and frees memory in between. It saves `--json` after each
  preset and resumes from it, and `--fresh` starts over. The first run was stopped by the system
  for low memory before writing anything.

### Results over the 44 presets (blurred, local passes first)

| | First run median / mean | Now median / mean |
|---|---|---|
| Our global look + spatial passes | 0.97 / 1.43 | 0.85 / 1.00 |
| Lightroom's baked LUT + our spatial passes | 0.91 / 1.18 | 0.63 / 0.82 |
| Our passes after the look | 1.52 / 1.82 | 1.42 / 1.66 |

- Local first now wins on 38 presets and loses on none.
- The look-profile presets went from 3.13 / 2.84 / 1.86 / 0.50 to 0.24 / 0.91 / 0.63 / 0.20.
- Gaps:
  - The worst preset is 4.88 with our look but 1.62 on Lightroom's LUT, so that gap is in the
    global model.
  - The next ones (1.5–2.5) set strong Highlights, Clarity and Dehaze together.
  - The baked LUTs of looks whose profile carries Highlights/Shadows include a Hald-specific
    version of that pass, so they aren't clean global looks.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | `compare` saves each row as it is scored, resumes from its JSON, and `--fresh` starts over | `test_compare_saves_each_row_as_it_goes_and_resumes_from_the_json` | PASS |
| 2 | A look profile's Highlights/Shadows are added at its amount and clamped, its Sharpness is ignored, and the local passes run first by default | `test_render_preset_folds_in_the_look_profiles_highlights_and_shadows_before_the_look` | PASS |
| 3 | With local passes first the vignette runs before the look and the grain after it | `test_render_preset_runs_the_look_then_the_spatial_passes_in_either_order` | PASS |
| 4 | Four whole presets match Lightroom's exports, with limits just above the measured values; local first never loses (private) | `tests/private/test_preset_check_vs_lightroom.py` | PASS (4) |

Commits:
- Resumable compare: RED `a2eabbf` (`compare_exports()` got an unexpected keyword argument
  `done`), GREEN `cf375ce` (507 pass).
- Look Highlights/Shadows: RED `39e1d01` (cannot import `spatial_settings`), GREEN `596b1d7`
  (508 pass, 98%).
- Vignette before the look: RED `84e12d6`, GREEN `f26d4c9` (508 pass, 98%).

## Built-in looks from Camera Raw's installed profiles (`preset.load_profiles`, `load_preset_dir(profile_dirs=...)`)

Journey: as a photographer whose preset uses one of Lightroom's own creative profiles, I want
Emulsion to apply that profile rather than silently drop it.

- The worst whole preset (4.88 blurred against Lightroom's export, 1.62 on its baked LUT) uses
  Adobe's built-in "Vintage 05" look at amount 0.9. The look isn't stubbed and has no table in
  the preset, so we skipped it: 9.42 ΔE against the baked LUT on a 17³ grid, with grays much too
  bright (0.5 came out at 0.545, Lightroom 0.459).
- Camera Raw installs that profile as a Look `.xmp` under `Settings/Adobe/Profiles`, with the same
  UUID. Resolving to it in place takes the preset to 3.87 on the grid and 2.62 against the export.
  Nothing of Adobe's is copied into the repo; the profiles are read from the user's install.
- `preset.load_profiles(dirs)` reads the Look profiles under the given folders (missing folders,
  unreadable files and non-Look presets are skipped). `resolve_looks(presets, profiles)` uses them
  after the preset folder's own looks. `load_preset_dir(root, profile_dirs=...)` and
  `preset_check compare --profiles DIR` (repeatable) pass them through. The local install holds
  397 Look profiles. Only this preset (`.xmp` and `.dng`) uses a built-in look.
- Still open, waiting for a decomposition bake (`out/decomp-kit`: the look alone at amounts
  100/90/50, the preset without its look, without HSL and without grading):
  - The profile has `RGBTableAmount` 0.75, which we ignore. With the look's amount 0.9, the
    grid scores 3.87 at table amount 0.9, 3.34 at 0.675 (0.9 × 0.75) and 3.13 at 0.75.
  - The profile also has a `LookTable`: a DNG-style hue/saturation/value map (36 × 16 × 16
    cells of hue shift in degrees, saturation scale and value scale, value outer and saturation
    inner, linear value encoding). A scratch implementation from the DNG specification, applied
    after the RGB table, only reaches 3.02, so the remaining gap is mostly elsewhere: reds,
    magentas and warm tones, where this preset also pushes HSL to ±100. Grays match within 0.016.
  - Moving the profile stage anywhere else in the pipeline is worse (5–10).

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | A built-in look resolves from a profile folder (same UUID), skipping broken files and non-Look presets; without the folder it stays unresolved | `test_load_preset_dir_resolves_builtin_looks_from_installed_profile_folders` | PASS |
| 2 | `compare` resolves built-in looks from `profile_dirs` / `--profiles` | `test_compare_resolves_builtin_looks_from_profile_folders` | PASS |
| 3 | Against the local Camera Raw install, every private preset's look resolves (private) | `test_builtin_look_resolves_from_the_installed_camera_raw_profiles` | PASS |

- Profile folders: RED `72691a9` (`load_preset_dir()` got an unexpected keyword argument
  `profile_dirs`), GREEN `a7477a5` (509 pass, 98%).
- `compare --profiles`: RED `5a9c410` (`compare_exports()` got an unexpected keyword argument
  `profile_dirs`), GREEN `a4df3c9` (510 pass, 98%).

## Look profiles' table amount and hue/sat map (`adobe_table.HueSatMap`, `LookRef.table_amount`)

Journey: as a photographer whose preset uses one of Lightroom's built-in creative profiles, I
want Emulsion's version of that profile to match Lightroom's, including both of its tables.

The user exported the decomposition bake (`out/decomp-kit`, collected to `out/decomp-luts`, all
sRGB 16-bit): the worst preset's built-in look alone at amounts 100/90/50, the preset without
its look, without the look and HSL, and without the look and grading, and the look kept with HSL
removed. Scores are mean ΔE2000 on a 17³ grid against each LUT.

- **Table amount.** With the RGB table alone, the best amounts were 0.70/0.65/0.35, which is the
  look amount × the profile's `RGBTableAmount` (0.75). Applying the table at the look amount
  alone scored 6.22/5.22/2.79.
- **Hue/sat map.** The profile's `crs:LookTable` is a DNG HueSatMap. Across the 57 installed
  profiles that carry one:
  - type 0, version 1 or 2;
  - hue, saturation and value divisions;
  - float32 cells holding hue shift in degrees, saturation scale and value scale, with value
    outermost and saturation innermost;
  - a value-encoding flag; version 2 adds a float64 amount range.
- **Where the map goes.** Applied after the RGB table, at the look amount, on linear ProPhoto,
  the look-only bakes score 1.13/1.08/1.08. These were worse:

  | Variant | Mean ΔE |
  |---|---|
  | Map before the table | 2.0 |
  | Map in sRGB primaries | 3.3 |
  | Map in Adobe RGB / P3 / Rec.2020 primaries | 1.3 / 1.5 / 1.2 |
  | sRGB value indexing | 1.6 or more |
  | Negated hue shift | 1.6 or more |
  | Table blended in linear light | 1.5 |
  | Table blended in ProPhoto gamma | 1.3 |

  Scaling the map's cells by the amount instead of blending its output is the same, 1.08 vs 1.08.
- **What the rest of the preset contributes** (decomposition variants, with the look):

  | Variant | Mean ΔE |
  |---|---|
  | Look 90 with HSL removed | 1.59 |
  | No look | 1.28 |
  | No look, no grading | 1.17 |
  | No look, no HSL | 0.96 |
- **Whole preset, scene chart, blurred.**

  | Step | Against the export | On Lightroom's baked LUT |
  |---|---|---|
  | Look unresolved | 4.88 | 1.62 |
  | Look resolved, table at 0.9 | 2.62 | 1.62 |
  | Now | 1.81 | 1.62 |
- **The grid gap.** On the grid the whole preset is still 3.31 (median 1.90), because the Hald
  extremes dominate:
  - Bright saturated yellows: input (1, 1, 0.25) comes out near white, (0.98, 0.93, 0.88)
    against Lightroom's (0.78, 0.59, 0.40).
  - Magentas behave the same way.
  - Without the look, the sRGB export clips these colours (ours (1, 0.96, 0.91), Lightroom
    (1, 0.89, 0.71)). The look's compression makes them visible.
  - Our Saturation +15 on the scene scale drives the low channel of such colours to 0 before HSL.
    This is the same class as the known Whites + and Highlights + gaps for bright saturated
    colours.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | A LookTable decodes (versions 1 and 2, value encoding, amount range), round-trips, and bad headers, sizes, encodings and blobs raise | `test_hue_sat_map_decodes_both_versions`, `test_hue_sat_map_rejects_bad_payloads`, `test_hue_sat_map_rejects_broken_blobs` | PASS |
| 2 | The map leaves colours unchanged at identity; it shifts hue in degrees and scales saturation (clamped to 1) and value | `test_identity_hue_sat_map_leaves_colors_unchanged`, `test_hue_sat_map_shifts_hue_and_scales_saturation_and_value` | PASS |
| 3 | It interpolates over value, wrapping hue and saturation cells, and can index values by their sRGB encoding | `test_hue_sat_map_interpolates_hue_saturation_and_value_cells`, `test_hue_sat_map_can_index_values_by_their_srgb_encoding` | PASS |
| 4 | The amount blends linear output; the map works on ProPhoto from any space and rejects amounts outside its range | `test_hue_sat_map_amount_blends_linear_output_and_works_in_prophoto` | PASS |
| 5 | A profile's map and `RGBTableAmount` are read and carried to looks (the look's own parameter wins); an inline map decodes; a bad map payload raises; an unreadable amount counts as 1 | `test_look_profiles_hue_sat_map_and_table_amount_are_read_and_carried_to_their_users` and three more in `test_preset.py` | PASS |
| 6 | Render applies the table at look amount × table amount, then the map at the look amount; a profile rendered by itself uses its own table amount | `test_render_scales_the_look_table_by_its_table_amount`, `test_render_applies_the_looks_hue_sat_map_after_its_table_at_the_look_amount`, `test_render_of_a_look_profile_itself_uses_its_table_amount` | PASS |
| 7 | The worst preset matches Lightroom's export within 1.9 (1.7 on its LUT), with the look from the installed profiles (private) | `tests/private/test_preset_check_vs_lightroom.py` | PASS (5) |

- Hue/sat map: RED `0e072b3` (cannot import `HueSatMap`), GREEN `ee5817b` (525 pass, 98%;
  it matches the scratch implementation on the installed profile to 4e-16).
- Looks carry it: RED `8668dbe` (5 fail: no `table_amount`, no `hue_sat_map`), GREEN
  `f3f155c` (530 pass, 98%). All 34 private tests pass, so no other preset changed: none of
  the packs' profiles carry a map or a table amount.

## PV 15.4 colour sliders and a finite top for their scene scale (`COLOR_KNEE`, `_to_color_scene`)

Journey: as a photographer whose preset pushes saturation and HSL, I want bright saturated colours
(and near-white tints) to come out the way Lightroom renders them, also under a strong look.

The worst preset still turned bright saturated yellows nearly white under its look. Every PV 11
single-slider bake scored under 1.5 ΔE on bright saturated colours (value ≥ 0.85, saturation
≥ 0.6), but that preset is PV 15.4, whose colour sliders had never been baked. The kit
`out/brightsat-kit` holds 75 bakes:
- 69 `Cal PV15 Color …` calibration variants (`calibration._process_version_15_4_color`):
  Saturation and Vibrance at several amounts, every HSL band's hue, saturation and luminance at
  ±100, and Saturation or Vibrance +50 with five HSL moves;
- 6 private variants of the worst preset: its colour sliders at PV 15.4 and PV 11, its HSL only,
  its Saturation and Vibrance only, its colour sliders under its look, and its tone only.

The user exported it twice, as sRGB and as ProPhoto. The ProPhoto exports (gamma 1.8) keep the
colours that the sRGB export clips. Scores are mean ΔE2000 on a 17³ grid; for ProPhoto, both
sides are clipped to [0, 1] in ProPhoto and compared in unclipped Lab.

### What the bakes showed

- **PV 15.4 HSL single sliders are fine.** All are at most 0.6 apart from Lum Green −100 (0.64)
  and Lum Blue ±100 (0.36–0.45).
- **Multi-band HSL keeps our semantics.**
  - On the preset's HSL-only bake, looking bands up at the original hue is best (0.55).
  - Luminance at the shifted hue scored 1.52; saturation at the shifted hue 4.47; both 4.91.
  - A linear response to the slider amount beats powers from 0.85 to 1.5.
  - Interpolating partial negative saturation in linear or encoded values instead of scene
    values isn't better either.
- **Positive Saturation was off beyond sRGB.**
  - ProPhoto +15/+50/+100 scored 0.57/1.05/1.50, against 0.24/0.55/0.80 in sRGB.
  - Its worst cells are near-white tints. The ProPhoto tint (0.981, 0.996, 0.882) at +50 came
    out (0.862, 0.996, 0.000); Lightroom gives (0.969, 1.000, 0.772).
  - The cause is the exposure shoulder's stretch, which runs off to infinity at white: linear
    0.99 → 9.2, 0.999 → 16.8, 1.0 → 4153. A tiny linear difference near white becomes a huge
    scene difference, and scaling chroma there wipes out the low channel.
  - Saturation on linear values (+50: 1.95) or encoded values (3.87) is worse, and so is a
    "lift the negative part" variant (1.53).
  - The luminance weights it keeps are already the best fit.
  - A brightness fade of the strength helped positives slightly (1.04 → 0.97) but hurt negatives.

### The change

`COLOR_KNEE = 0.94`. The colour stages (saturation, vibrance/HSL, B&W) work on
`_to_color_scene`: the shoulder's stretch up to the knee (encoded), then a straight line with
the slope there. White balance, exposure and Whites/Blacks keep the plain shoulder.

Knee positions, mean over 12 ProPhoto checks (Saturation ±, Vibrance ±100, four HSL singles, the
preset's HSL, colour part and Saturation + Vibrance):

| Knee | 1.0 (none) | 0.97 | 0.96 | 0.95 | 0.94 | 0.93 | 0.92 | 0.90 |
|---|---|---|---|---|---|---|---|---|
| Mean ΔE | 0.731 | 0.712 | 0.700 | 0.689 | 0.681 | 0.681 | 0.688 | 0.712 |

At 0.94 none of the 12 get worse:
- Saturation +50/+100: 1.05/1.50 → 0.95/1.36;
- the preset's HSL: 0.55 → 0.50;
- the preset's colour part: 0.74 → 0.67.

Applying the knee to every stage instead made Temperature −100 5.1 worse.

| Set (sRGB) | Before median / mean | Knee median / mean | Better / worse |
|---|---|---|---|
| Earlier calibration bakes (134) | 0.035 / 0.193 | 0.034 / 0.187 | 26 / 6, at most +0.05 |
| New colour bakes (75) | 0.164 / 0.304 | 0.146 / 0.281 | 46 / 5, at most +0.03 |
| The 44 private presets | 0.750 / 0.984 | 0.705 / 0.946 | 29 / 1, +0.015 |

- **The worst preset.**
  - The bright yellow (1, 1, 0.25) through its HSL went from 4.3 to 1.0 ΔE, and to 5.7 under
    its look (was 10.1).
  - Its grid score is 2.92 (was 3.31).
  - On the scene chart it is still 1.81 (1.62 on Lightroom's LUT).
- **The tint** from the RED test now comes out with blue 0.82 (Lightroom 0.772, was 0.0).
- **Gaps.**
  - Positive Saturation beyond sRGB: ProPhoto +50 0.95, +100 1.36.
  - Vibrance −100: 1.45.
  - Bright yellows under the look.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | 69 PV 15.4 colour variants cover Saturation, Vibrance, every HSL band at ±100 and Saturation/Vibrance + HSL combinations, and fit nothing | `test_process_version_15_4_color_variants_check_saturation_vibrance_and_hsl_alone_and_combined` | PASS |
| 2 | The colour scene scale equals the shoulder's below the knee, round-trips, rises monotonically and stays finite at white | `test_colour_scene_scale_has_a_finite_top_above_its_knee` | PASS |
| 3 | Saturation +50 keeps a near-white tint's low channel, as Lightroom does | `test_saturation_keeps_a_near_white_tints_low_channel` | PASS |

- Colour variants: RED (`Cal PV15 Color Saturation +50` missing), GREEN `4d455fc` (533 pass, 98%).
- Colour scale: RED `9031c3d` (cannot import `COLOR_KNEE`; the tint's blue 0.0), GREEN `17abbe2`
  (535 pass, 98%). The colour-stage tests' scene helper now models the knee independently
  (`color_scene`, `color_unscene`).

## Positive saturation at PV 15.4 (`POSITIVE_SATURATION_SHOULDER`, `POSITIVE_SATURATION_PIVOT`)

Journey: as a photographer whose PV 15.4 preset adds saturation, I want saturated colours to gain as
much colour as Lightroom gives them, also beyond sRGB.

After the colour scale's knee, positive Saturation was still the largest single-slider gap in the
PV 15.4 colour bakes' ProPhoto exports: +15/+50/+100 0.52/0.95/1.37.

### What the bakes showed

- **Saturation depends on the process version.** Lightroom's own PV 11 and PV 15.4 Saturation
  bakes (sRGB) differ by 0.33-0.48 mean ΔE (max 7.4) at -100/-50/+50/+100, so one fit can't serve both.
- **The form is right.** Saturation in Rec.2020, Adobe RGB, P3 or sRGB primaries (1.08/1.62 at
  +50/+100), per channel (1.63/2.16) or on log ratios (4.18/5.16) is worse. A free 3×3 matrix on
  the scene scale is no better than ours on unclipped cells.
- **The error is in saturated mid-bright colours.** Their effective boost is 1.1-1.16× ours
  (dark saturated ones 0.93×). Lightroom drives their low channel to 0 where ours keeps 0.02-0.06,
  and cells where Lightroom reaches a ProPhoto bound score 1.34 against 0.78 elsewhere (+50).
  By hue, greens and oranges come out too weak, blues and purples slightly too strong.
- **At -100, Lightroom's PV 15.4 gray** is 3-15% above our luminance of saturated mid-tones and
  5-9% below it at the brightest, so the scale's shape differs too, not just the weights.

### The change

From PV 15.4, positive Saturation runs on the colour scene scale with a stronger shoulder
(`POSITIVE_SATURATION_SHOULDER` 3.2 instead of the exposure's 5.55; knee kept) around its own
luminance (`POSITIVE_SATURATION_PIVOT` (0.31, 0.71, -0.02)).

It was fitted jointly over shoulder 2.5-5.55, knee 0.90-0.97 and pivot on the five PV 15.4
Saturation bakes. Negative amounts preferred the existing shoulder and weights (-100 0.479 -> 0.458
at best), so they stay.

| ProPhoto, PV 15.4 | Before | After |
|---|---|---|
| Saturation +15 / +50 / +100 | 0.52 / 0.95 / 1.37 | 0.42 / 0.77 / 1.01 |
| 14 bakes with positive Saturation (mean) | 1.008 | 0.864 (12 better, 1 worse) |
| The same 14 in sRGB (mean) | 0.613 | 0.556 |

- The one worse bake is the worst preset's colour part (0.67 -> 0.70).
- The 10 private presets with positive Saturation: mean 1.122 -> 1.118 (3 better, 2 worse, at
  most +0.11).
- The worst preset: grid 2.92 -> 2.78, scene chart 1.81 (unchanged).
- Applied to PV 11 as well, it improved PV 11's two positive bakes (0.414 -> 0.369) but worsened 6
  of the 10 presets, so PV 11 keeps its calibration.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | PV 15.4 Saturation +50 drives a saturated green's low channel toward Lightroom's; PV 11 and negative amounts are unchanged; grays stay gray | `test_positive_saturation_at_pv_15_4_has_its_own_shoulder_and_pivot` | PASS |

- RED `79e6aec` (the green's blue 0.035; Lightroom 0.002), GREEN `3f6de32` (blue 0.021; 536 pass, 98%).

## Task 6 gaps without new bakes: negative Vibrance, colour NR, Dehaze +100

Journey: as a photographer, I want the remaining single-slider gaps closed where the existing
exports allow it, and the ones that can't be closed explained.

### Negative Vibrance (`NEGATIVE_VIBRANCE_DARKEN`)

On the existing Vibrance -50/-100 bakes (PV 11 sRGB, PV 15.4 sRGB and ProPhoto), the saturation that
negative vibrance keeps matched Lightroom's within a few percent, but the colour's scene max came
out too dark. At -100 Lightroom keeps a mid-saturated colour's max at 0.75 of its value, ours kept
0.69; at -50 0.875 against 0.844. The darkening is now scaled by `NEGATIVE_VIBRANCE_DARKEN` = 0.88
(0.6-0.94 tried; a shadow-fade exponent and the kept-saturation scale didn't help).

| Bake | Before | After |
|---|---|---|
| Vibrance -100, PV 11 sRGB / PV 15.4 sRGB / PV 15.4 ProPhoto | 1.45 / 1.45 / 1.45 | 0.82 / 0.87 / 0.87 |
| Vibrance -50, PV 11 sRGB / PV 15.4 sRGB / PV 15.4 ProPhoto | 0.64 / 0.73 / 0.73 | 0.36 / 0.48 / 0.48 |

- The 5 private presets with negative Vibrance: median 0.774 -> 0.803, 3 better, 2 worse.
- The one clearly worse (+0.29) combines Vibrance -40 with Saturation -44; no bake covers negative
  Saturation with negative Vibrance yet (a candidate for the next bake session).
- What's left at -100 is the darkening's shape: Lightroom's peaks at lower saturation than
  `s(1 - s)`, and PV 11 and PV 15.4 differ for highly saturated colours.

### Colour noise reduction: at its floor

- On the levels chart all three amounts (25/50/100) change the image the same way (1.60).
- Almost all of our 0.52 is chroma: Lightroom's chroma with our luma scores 0.06.
- In the noisy patch Lightroom leaves 28% of the chroma noise (std 0.036 -> 0.010), ours 4%.
- Its response is not a low-pass. It keeps the lowest frequencies (gain 0.7-0.8), removes
  0.01-0.05 cycles/px, passes 0.08-0.24 partly (up to 0.36, a period of 5-6 px) and removes the rest.
- At mid frequencies its output power is 27% of the input's but almost incoherent with it
  (correlation with the input Cb 0.15).
- Gaussians of σ 4-24, a luma-guided filter, and smoothing in linear YCbCr or linear
  chromaticity all stay at 0.50-0.55.
- Even the measured linear response applied by FFT (the best any linear filter can do) only
  reaches 0.43. The rest isn't reproducible pixel for pixel, so the model is unchanged.

### Dehaze +100: lightness inside flat areas

- 88% of Lightroom's change is captured (1.60 of 13.3). With Lightroom's L* ours would score 0.36,
  so the gap is lightness.
- Its mean is small (-0.27), but inside each large flat patch ours is too dark toward the darker
  neighbour and too light toward the brighter one, by up to 6 L*. Lightroom pushes each patch
  away from its neighbours over a few hundred px.
- A wide unsharp term on luma (σ 24-256, strength 0.05-0.3) doesn't help (1.597 at best). It
  also adds contrast at edges, where Lightroom adds none.
- Matching it needs an edge-aware large-scale model of the haze map; left as a known gap.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Negative vibrance darkens a mid-saturated colour's max by the measured strength | `test_negative_vibrance_darkens_a_colors_max_by_the_measured_strength` | PASS |

- RED `94d9a1f` (cannot import `NEGATIVE_VIBRANCE_DARKEN`), GREEN `132b224` (537 pass, 98%).

## Bright saturated colours under a look: an interaction, not a stage

The worst preset's colour sliders under its built-in look (`Bright Color Part Look 90`, ProPhoto) score
1.60 (2.47 on bright saturated colours); without the look the same sliders score 0.70.

- **Our look stage on Lightroom's own colour-part output** still scores 1.26 (1.97). On the plain
  grid it is accurate for the same colours: the cell nearest that output, (0.82, 0.71, 0.51), is
  0.92 off.
- **The look's position is right.** Before Saturation 5.40, first 4.68, after HSL or last 1.60.
  Splitting it, table first and map after HSL scores 4.97, map first and table after 2.61.
- **Lightroom's own look isn't a function of the colour part's output.** Its look-only LUTs
  (`out/decomp-luts`, Look 50/100 interpolated to 90), applied to its own colour-part bake, miss its
  Look 90 bake by 1.12 ΔE (1.01 where the colour part isn't clipped, 2.45 where it is). For the
  bright yellow (1, 1, 0.25), the colour part gives (0.814, 0.681, 0.508). Lightroom's look on that
  gives (0.542, 0.442, 0.387), but on a grid input of nearly the same value it gives
  (0.617, 0.531, 0.467) at amount 100.

So about 1 ΔE of this gap is an interaction between the look and the colour sliders, which no
stage-by-stage model (Lightroom's own LUTs included) reproduces. Finding it needs bakes of the look
with single colour sliders. No code change.

## The `.fsim.json` look format (task 8, `colorlab.fsim`)

Journey: as a photographer, I want a look I made from a preset saved as one open file that the
camera can apply live, so that I don't need Lightroom or the preset to use it.

A look file is one JSON object (version 1):

| Key | Content |
|---|---|
| `format`, `version` | `"fsim"`, `1`; readers reject anything else |
| `name` | 1–80 characters; `description`, `author`, `license` are optional text |
| `color.lut` | `size` (2–65) and `data`: base64 of `size³ × 3` little-endian uint16 (0..65535 for 0..1), in `.cube` order (red fastest), sRGB-encoded in and out |
| `local` | noise reduction, Highlights/Shadows, Dehaze, Texture, Clarity, sharpening, in Lightroom slider units |
| `vignette`, `grain` | post-crop vignette and grain, the same units |

Settings left out take their defaults, and the writer leaves defaults out. A renderer runs the
local passes, then the vignette, then the LUT, then the grain, the order that matched Lightroom's
exports best (`finish.render_preset`). `fsim.apply` is the reference renderer, and
`python -m colorlab.fsim from-preset|apply` is its CLI.

- **Why a LUT.** The global colour (white balance, tone, HSL, B&W, curves, the look's tables,
  colour grading) depends only on the look's settings, so it bakes into one 3D LUT, which the
  app applies in one texture lookup per pixel. The spatial passes depend on the neighbourhood
  and stay parameters.
- **Hostile files.** The reader caps the text at 8 MiB and rejects unknown sections or
  settings, out-of-range, non-finite or non-numeric values (booleans too), fractional vignette
  styles, bad LUT sizes and LUT data of the wrong length or not base64.
- **Fidelity.** On all 59 private presets (scene chart at a quarter size), `apply` of the
  round-tripped look against `render_preset` of the preset: mean ΔE median 0.035, worst 0.097
  (99.9th percentile median 0.38, worst 4.15, at extreme Hald-like colours where the 33-point
  LUT's interpolation differs). A 33-point look file is about 280 KiB.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | A look round-trips through its text, defaults left out, the text stable | `test_a_look_from_a_preset_round_trips_through_its_json_text` | PASS |
| 2 | `apply` equals `render_preset` (2e-4) for spatial-only presets and is within the LUT's interpolation for colour (mean < 0.15) | `test_applying_a_look_matches_the_preset_it_came_from` | PASS |
| 3 | A look profile's own Highlights/Shadows are folded in | `test_a_look_profiles_own_highlights_and_shadows_are_folded_in` | PASS |
| 4 | Files and the CLI (from-preset, apply, a missing file exits 1) | `test_files_and_the_command_line` | PASS |
| 5 | 22 kinds of malformed or hostile documents, oversized text and bad `Look` arguments raise `FsimFormatError` | `test_malformed_looks_are_rejected`, `test_oversized_text_is_rejected_before_parsing`, `test_a_look_needs_a_name_and_a_lut_to_build` | PASS |
| 6 | Text fields round-trip; file errors name the file | `test_text_fields_round_trip_and_files_name_themselves_in_errors` | PASS |
| 7 | All private presets round-trip and render like the preset (median < 0.1, worst < 0.5) | `tests/private/test_fsim_private_presets.py` | PASS |

- RED `e3b76b5` (23 fail: cannot import `colorlab.fsim` or `FsimFormatError`), GREEN `ee95db8`
  (567 pass, 98%; `fsim.py` 100%).

## Mobile DNG presets against their embedded Lightroom previews (task 7)

Journey: as a photographer, I want my mobile (`.dng`) presets to look in Emulsion the way
Lightroom showed them, on real photos and not only on our charts.

A mobile preset DNG carries the photo it was made on and Lightroom's render of it:
- IFD 0 is a 256 px thumbnail;
- SubIFD 0 is the photo as an 8-bit linear-raw proxy (`UniqueCameraModel` "JPEG", profile
  "Embedded", sRGB primaries, baseline exposure 0), whose values are **already sRGB-encoded**:
  read as linear and encoded, the presets score about 16 ΔE; used directly, 2.6;
- SubIFD 1 is Lightroom's render with the preset, a JPEG preview of about 1024 px in sRGB;
- SubIFDs 2 and 3 are 16-bit JPEG XL proxies at 512 and 256 px.

All 15 are PV 15.4. Scores are mean ΔE2000 after a σ 2 px blur, with our render
(`finish.render_preset`) resized to the preview:

| | Median | Mean | Worst |
|---|---|---|---|
| The unedited photo | 9.53 | 11.34 | 22.9 |
| Our global colour only | 2.46 | 3.29 | 5.61 |
| The whole preset | **2.60** | **2.64** | 4.19 |

Every preset comes out at least 3.9 ΔE closer to Lightroom than the unedited photo.

- **What's left is Highlights −.** Removing one spatial pass at a time:
  - without Highlights/Shadows, 5 of the 12 presets that use them get better, by up to 1.9;
  - all five pair Highlights −20 to −35 with Shadows + (one preset with Highlights −35 and
    Shadows +30 goes from 4.13 to 1.86 with Highlights alone set to 0);
  - our render is 4–8 L* too dark in the midtones and highlights of those photos.
  - The vignette is right: removing it costs up to 4.1.
- **Not the process version.** The spatial charts were measured at PV 11, but on the preset
  kit's scene chart, PV 15.4 presets with Highlights − alone fit best at full strength
  (−49: 0.54, −39: 0.37).
- **Not the order of the two sliders.** Highlights then Shadows, or the reverse, instead of
  adding both changes, moves the DNGs' median by 0.02.
- **Likely image-adaptive.** The bright photos want much less Highlights − than the chart
  (best at 0–50%). The darkest photo (69% of pixels below L* 40) needs all of it. Lightroom's
  PV 2012+ tone sliders adapt to the image, and one chart can't measure that. Fitting the 15
  previews would fit the validation set, so it needs bakes: Highlights and Shadows at
  ±25/50/100 on several real photos (bright, dark, low contrast) at PV 15.4.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | All 15 DNG presets match their previews at median < 2.7, mean < 2.75, worst < 4.3, each at least 3.5 closer than the unedited photo (private) | `tests/private/test_dng_previews.py` | PASS |

No code changed: the check found no fix that holds without new bakes.

## Follow-up bake kits for the open gaps (`calibration._followups`, `spatial_calibration.FOLLOWUP_VARIANTS`, `photo_variants`)

Journey: as the person running Lightroom, I want every remaining bake in one session, built and
documented, so the gaps that need Lightroom's answers can close in one pass.

- **Global (Hald) follow-ups**, 19 calibration variants. (The GREEN commit message says 27; the
  true count is 19.)
  - Whites +25/+50 at PV 15.4: is the +100 gap (1.85) a shape or a strength?
  - The B&W mixer at PV 15.4: a neutral mix and each band at +100, for 15.4's own hue table.
  - Saturation − with Vibrance − at PV 11 and PV 15.4: −50/−50, −100/−50, −50/−100, −25/−50.
    One private preset with both got worse after the negative-vibrance fix.

  Each sets two or more keys, so the fit and the measured shapes are unchanged.
  `presets --only-missing` is now repeatable, and a variant is written when no folder has its
  cube: `--only-missing out/cal-luts --only-missing out/brightsat-luts` gives exactly these 19.
- **Spatial follow-ups**, 28 chart variants.
  - The new `flats` chart: 12 grays and 12 colours, 6 of them bright saturated, in 256 px
    patches. It carries Dehaze ±50/±100 (lightness inside flat areas), Clarity +50/+100 (the
    wide one-sided contrast), and Highlights +50/+100 (bright saturated colours moving toward
    white).
  - Pairs on the scene chart: Highlights +50 with Clarity +50 or Dehaze +50, and Highlights −
    with Shadows + (−35/+30, −50/+50).
  - Sharpening Masking 25/100.
  - Highlights/Shadows ±25/50/100 at PV 15.4.
  - Identities for flats and for the scene chart at PV 15.4.
- **Real photos.** `photo_variants(name, size)` gives 11 PV 15.4 variants per photo: identity,
  Highlights −100/−50/−25/+50/+100, Shadows −50/+25/+50/+100, and Highlights −35 with Shadows +30.
  They test whether Lightroom adapts these sliders to the image (task 7).
  `write_spatial_kit(photos=...)` and `kit --followup --photos DIR` take the photos. The base kit
  is unchanged; its identities stay the three original charts.
- **Built kits** (private, under `out/`; `out/BAKE_SESSION.txt` is the one-page guide):
  - `out/next-global-kit`: 27 Hald images, about 41 MB, exported twice (sRGB and ProPhoto). It
    holds the 19 variants and 8 private ones: the worst preset's built-in look at 100 alone and
    with one colour slider each (Saturation ±50, Vibrance ±50, yellow hue, saturation and
    luminance), to measure the look × colour-slider interaction.
  - `out/next-spatial-kit`: 83 images, about 516 MB. It holds the 28 chart variants and 5 photos
    taken from the mobile DNG presets: the brightest, the darkest, the flattest, the most
    contrasty and a middle one.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | The global follow-ups exist with their exact settings and stay out of the fit | `test_followup_variants_cover_the_open_global_bakes` | PASS |
| 2 | `--only-missing` takes several folders | `test_cli_presets_only_missing_accepts_several_lut_folders` | PASS |
| 3 | The flats chart has 12+ grays from below 0.05 to above 0.95, 6+ bright saturated colours, patches of 200+ px | `test_flats_chart_has_large_gray_steps_and_bright_saturated_colors` | PASS |
| 4 | The spatial follow-ups cover flats, pairs, masking and PV 15.4 local tone, with no name shared with the base kit | `test_followup_variants_cover_the_open_spatial_bakes` | PASS |
| 5 | Photo variants are PV 15.4 Highlights/Shadows moves with an identity, at the photo's size; bad names raise | `test_photo_variants_move_highlights_and_shadows_at_pv_15_4_on_the_photo` | PASS |
| 6 | The kit writes photos with their settings, refuses missing or mis-sized photos, and the CLI builds the follow-up kit | `test_kit_writes_photos_with_their_settings_and_cli_builds_the_followup_kit` | PASS |

- RED `d8b560f` (6 fail: no `Cal PV15 Whites +25`, `--only-missing` takes one folder, no
  `flats`, cannot import `FOLLOWUP_VARIANTS`/`photo_variants`), GREEN `5dc4000` (573 pass, 98%).

## The `colorlab` command (task 9, `colorlab.cli`)

Journey: as a photographer or contributor, I want one command for the color lab, so I don't need
to know which module holds which tool.

| Command | Does |
|---|---|
| `colorlab render PRESET IN.tif OUT.tif [--global-only] [--seed N]` | the whole preset (`finish.render_preset`), or only its global colour |
| `colorlab lut PRESET OUT.cube [--size 33]` | the global colour as a `.cube` |
| `colorlab look PRESET OUT.fsim.json [--name] [--size]` | a `.fsim.json` look |
| `colorlab apply LOOK IN.tif OUT.tif [--seed N]` | a `.fsim.json` look or a `.cube` on a TIFF |
| `colorlab info PRESET` | name, group, process version, the look (and whether it resolved), the settings it moves |
| `colorlab bake / calibration / spatial / check / finish / film / local-contrast / fsim ...` | that module's own tool, arguments passed through |

Presets are `.xmp` or mobile `.dng`. A stubbed look resolves from the Look presets in the
preset's own folder and in any `--profiles` folders. `python -m colorlab` is the same command.
Errors exit 1 with `error: ...`; no command exits 2 with the usage.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | `render` equals `render_preset` (and `--global-only` equals `render`) to 1e-4 | `test_render_applies_the_whole_preset_or_only_its_global_colour` | PASS |
| 2 | `lut` writes `render_lut` as a `.cube` | `test_lut_bakes_the_global_colour_into_a_cube` | PASS |
| 3 | `look` then `apply` round-trips; `apply` takes a `.cube` too | `test_look_then_apply_round_trips_a_preset_and_apply_takes_a_cube_too` | PASS |
| 4 | `info` shows the process version, the look and the moved sliders | `test_info_names_the_preset_its_look_and_the_sliders_it_moves` | PASS |
| 5 | Stubbed looks resolve from `--profiles` and from the preset's folder | `test_stubbed_looks_resolve_from_the_presets_folder_and_profile_folders` | PASS |
| 6 | Every module tool is reachable, and every module with a command line is listed | `test_every_module_tool_is_reachable_under_one_name` (8), `test_tools_cover_every_module_with_a_command_line` | PASS |
| 7 | Errors exit 1, no command exits 2, `--version`, `python -m colorlab` | `test_errors_exit_1_with_a_message_and_no_command_shows_usage`, `test_python_dash_m_colorlab_runs_the_same_command` | PASS |

- RED `2832b36` (cannot import `colorlab.cli`), GREEN `3aa2f6b` (589 pass, 98%; `cli.py` 99%).
  On the private presets, `info` resolves a pack's stubbed look from its own folder and `render`
  runs a mobile DNG preset on a photo.

## The follow-up bakes, scored, and the B&W mixer at PV 15.4 (`pipeline.grayscale`)

The user exported both follow-up kits: 27 Hald images twice (sRGB and ProPhoto; ICC profiles
checked) and 83 spatial images, all collected with nothing missing or unedited
(`out/next-luts`, `out/next-spatial-exports`). Mean dE2000 against Lightroom, ours / Lightroom's
change where useful:

- **PV 15.4 Whites +**: +25 0.16, +50 0.59 (bright saturated 1.41), +100 1.85. The gap grows
  faster than the amount, so it is a shape (the lift of bright saturated colours' low channel),
  not a strength.
- **Saturation − with Vibrance −**: PV 11 0.26–0.60, PV 15.4 0.48–0.74. No change made.
- **The built-in look × colour sliders**: the look alone 1.13 (bright saturated 2.01); with one
  colour slider 0.57–1.33. The sliders add at most 0.2 to the look's own error, so the gap is the
  look, not the interaction.
- **Flats chart**: Dehaze −100 2.18 (24.4), −50 1.93, +50 1.80, +100 3.60 (12.4); Clarity +50
  2.21 (2.84) and +100 4.06 (5.28); Highlights +50 1.72 and +100 2.83 (7.18). Inside large flat
  areas our Clarity barely moves while Lightroom's does, and Dehaze + is off by a third.
- **Pairs on the scene chart**: Highlights +50 with Clarity +50 1.01, with Dehaze +50 0.83;
  Highlights −35 with Shadows +30 0.83, −50 with +50 1.40.
- **Sharpening Masking**: 25 0.07, 100 0.21 (Lightroom's change 0.23: Masking 100 all but turns
  sharpening off on the scene chart; Masking isn't modelled).
- **Highlights/Shadows at PV 15.4 on the scene chart**: 0.10–1.03, the same as PV 11. The process
  version doesn't change them.
- **Highlights/Shadows on real photos (task 7)**: our change has the right shape but the wrong
  strength. A least-squares scale `k` of our change against Lightroom's is nearly constant across
  amounts for each photo (correlation 0.8–0.94):

  | Photo | Character | Highlights k | Shadows k |
  |---|---|---|---|
  | 1 | bright (58% of pixels above Y 0.8) | 0.28 | 2.2 |
  | 2 | dark (43% below Y 0.2) | 0.85 | 0.18 |
  | 3 | flat midtones (p95 0.78) | 0.15 | 0.37 |
  | 4 | contrasty | 0.45 | 0.7 |
  | 5 | middle | 0.47 | 0.9 |

  So Lightroom scales Highlights and Shadows per image. Five photos don't pin down what sets the
  scale; `out/photo-strength-kit` (10 more photos from the mobile DNG presets, each at identity
  and Highlights/Shadows ±50, PV 15.4) is built for the next Lightroom session.

**PV 15.4 B&W.** The 9 PV 15.4 B&W bakes scored mean 0.985 / worst 1.64 on the PV 11 mixer (PV
11's own bakes 0.30). Lightroom's PV 15.4 neutral mix is 0.52 away from its PV 11 one: identical
on grays and in the hue-bin medians of the implied gray weight, but up to 1.3 L* darker for bright
saturated colours (encoded max above 0.75, saturation above 0.75). And each band's slider fades
faster away from its centre (Yellow +100 lifts the weight at 75 degrees by 0.35 against PV 11's
0.57, Blue +100 at 255 degrees 0.23 against 0.53). What was tried, mean over the 9 bakes:

| Change | Mean | Worst |
|---|---|---|
| PV 11 mixer | 0.985 | 1.64 |
| PV 15.4 HSL's smooth hue model / spline tables / both | 1.364 / 1.060 / 1.243 | |
| a band's move fades as (linear weight)^p: p 1.5 / 1.6 / 2.0 | 0.558 / 0.540 / 0.735 | 0.61 |
| p 1.6 and a weaker exposure shoulder (3.2) | 0.701 | 0.80 |
| p 1.6 and a stronger shoulder (12) | 0.481 | 0.56 |
| p 1.6 and the colour scale's knee at 0.86 (from 0.94) | **0.452** | **0.56** |

The knee at 0.75 or 0.97 is worse (0.691, 0.570). From PV 15.4, `grayscale` fades each band's
move from its default as its linear weight to the power `BW_BAND_FALLOFF_PV15` = 1.6, and runs on
a colour scale whose straight top starts at `BW_KNEE_PV15` = 0.86 (`_to_color_scene(knee=...)`).
Below the knee and at a band's centre it is PV 11's mixer.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Before PV 15.4 the mixer is unchanged; from 15.4 grays stay, bright saturated colours darken, dark colours don't, and a band's lift is the same at its centre but under 0.75 of PV 11's 20 degrees out | `test_bw_at_pv_15_4_has_narrower_bands_and_a_lower_knee` | PASS |
| 2 | `render` uses the PV 15.4 mixer for PV 15.4 presets | `test_render_uses_the_pv_15_4_mixer_for_pv_15_4_presets` | PASS |
| 3 | The 9 PV 15.4 B&W bakes are under 0.6 each and 0.5 on average (local) | `tests/private/test_bw_vs_bakes.py::test_pv_15_4_bw_mixer_matches_its_bakes` | PASS |

- RED `866b5b7` (2 fail: cannot import `BW_BAND_FALLOFF_PV15`; a PV 15.4 B&W preset renders the
  same as PV 11). The GREEN commit also fixes the RED test's missing `pipeline` import and
  uses darker test colours for the centre check: the bright one's output gray sat above the new
  knee. GREEN: 591 public tests pass, 98%; `pipeline.py` 99%.

## Highlights and Shadows adapt to the image (`local_contrast.image_strength`)

Journey: as a photographer, I want a look's Highlights and Shadows to act on my photo as
Lightroom's do, so a dark or a bright frame isn't over- or under-corrected.

The photo strength kit (`out/photo-strength-kit`, 10 more photos from the mobile DNG presets at
identity and Highlights/Shadows ±50, PV 15.4) was exported and collected
(`out/photo-strength-exports`, 50 of 50, 16-bit sRGB). With the first 5 photos that makes 15.
For each, the least-squares scale `k` of our chart-fitted change against Lightroom's change is
nearly the same at −50 and +50 (correlation 0.8–0.95 within a photo). Highlights' `k` runs
0.15–0.89, Shadows' 0.18–2.28, and they move in opposite directions with the photo's
brightness.

Single and paired image statistics were tried as predictors of log `k` (leave-one-out over the 15
photos; the scene chart, where the tables were fitted, should give `k` = 1):

| Slider | Best statistic | LOO rms of log k (spread before) | Predicts on the scene chart |
|---|---|---|---|
| Highlights | 5th percentile of encoded luminance (the black point) | 0.339 (0.464) | 0.94 |
| Shadows | mean linear luminance | 0.286 (0.634) | 0.87 |

Mean, median, log-average and linear mean all fit Highlights far worse (photo dE 2.06–2.82).
Anchored at the scene chart (`k` = 1 there) with the exponent fitted leave-one-out, mean dE of the
60 photo exports, ours against Lightroom:

| Strength | Highlights ±50 | Shadows ±50 | All |
|---|---|---|---|
| the chart's (k = 1) | 2.68 | 1.61 | 2.14 (worst 5.91) |
| `image_strength` | 0.93 | 0.98 | 0.96 (worst 1.81) |
| each photo's own best k | | | 0.71 (worst 1.20) |

`image_strength(img)` gives `(black / 0.0304) ** −0.57` and `(mean / 0.296) ** 1.16`, with the
black point clamped to 0.02–0.5 and the mean to 0.02–0.8 (the range the photos covered).
`highlights_shadows(..., strength=)` scales each slider's change; its default stays the chart's,
so Dehaze's internal use is unchanged. `finish.local_passes` (and so `apply_settings`,
`render_preset` and `fsim.apply`) passes the image's own strength. For the camera the two
statistics are one downsampled histogram per frame.

The whole mobile DNG presets against Lightroom's embedded previews (task 7): median 2.60 -> 1.96,
mean 2.64 -> 2.07 (worst 4.19 unchanged); 3 of the 15 get slightly worse (by 0.08–0.21). They
are renders of the same 15 photos, but of each preset's full settings.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | The strength is 1 on the scene chart, Highlights' falls with the black point, Shadows' rises with brightness, and both are bounded | `test_image_strength_is_one_on_the_scene_chart_and_follows_black_point_and_brightness` | PASS |
| 2 | `strength` scales each slider's change on its own; (1, 1) is the old result; a negative strength raises | `test_highlights_shadows_scales_each_sliders_change_by_its_strength` | PASS |
| 3 | `apply_settings` uses the image's own strength | `test_apply_settings_adapts_highlights_and_shadows_to_the_image` | PASS |
| 4 | The 60 photo exports average under 1.0, worst under 2.0 (local) | `tests/private/test_photo_strength.py` | PASS |
| 5 | The DNG presets: median under 2.05, mean under 2.15 (local) | `tests/private/test_dng_previews.py` | PASS |

- RED `4ae5794` (3 fail: cannot import `image_strength`; `highlights_shadows` takes no
  `strength`; the local photo test 2.14 against < 1.0). GREEN updates two `render_preset` tests
  whose expected images used the chart's strength.

## The four open gaps: Masking modelled; Whites + at PV 15.4, flat-area Clarity/Dehaze and the built-in look measured

Journey: as the maintainer, I want each remaining gap either closed or explained with evidence,
so the next work goes where it pays.

**Sharpening Masking (`finish.sharpen`).** The fraction of Sharpen 50's change that Lightroom
keeps on the scene chart is 1.00 / 0.97 / 0.48 at Masking 25 / 50 / 100; at 100 about 0.8 on
strong edges and 0.18–0.34 on weaker ones. A soft threshold on the gradient of luma blurred by
1 px, `0.8 * clip((g - 0.05) / 0.1, 0, 1)`, cuts Masking 100's squared error 24-fold (the best of
blurs 0.5–3 px, thresholds 0.005–0.08, widths 0.005–0.1). It is blended in by
`(masking / 100) ** 4`: the fitted blends were 0.008 / 0.054 / 1.07, against 0.004 / 0.062 / 1.
Mean dE: Masking 100 0.202 -> 0.070, 50 0.056 -> 0.060, 25 0.061. The private presets use Masking
0 or 20, where it scales the sharpening by under 0.2%.

| # | What is guaranteed | Test | Result |
|---|---|---|---|
| 1 | Masking 0 changes nothing, 25 and 50 keep nearly all the sharpening, 100 keeps strong edges (0.6–0.85) and drops flat texture (< 0.25), monotone in between | `test_sharpening_masking_keeps_strong_edges_and_drops_flat_texture` | PASS |

- RED `2106d93` (1 fails: Masking 100 keeps all of the sharpening in a flat noisy area),
  GREEN `032f2ee` (595 public tests pass, 98%).

**Whites + at PV 15.4 (not changed).** Against the PV 15.4 Whites + bakes, the hue-keeping curve on
each colour's linear min and max (as now) beats the other ways of applying the same curve: +25 /
+50 0.16 / 0.59 now; pulled encoded min/max 0.18 / 0.63, encoded min/max 0.19 / 0.65, per channel
0.24 / 0.93; one scene-scale gain at the brightness key 0.92 / 2.99 (at the mean 0.89 / 2.87).
Lightroom's extra is a pull of the low and middle channels toward the colour's own max, about the
same fraction for both, growing with amount and saturation but not simply with brightness (at
+100 it peaks for max 0.75–0.85 and vanishes at white). The private PV 15.4 presets use Whites +
only up to +14, where the error is about 0.1, so three bakes don't justify an empirical model.

**Clarity + and Dehaze + inside flat areas (not changed).** A wider detail term on Clarity, fitted
on the flats and scene charts together, improves the flats but costs the scene chart at every
width tried (W/32 to W/6): Clarity +100 flats 4.06 -> 2.80 but scene 1.88 -> 2.25 at W/6. A
symmetric wide term is the wrong shape for Lightroom's one-sided contrast. On the flats, Lightroom
keeps a saturated colour's max channel (0.95 stays 0.94 at Dehaze +100) where ours drops it
(0.87–0.89); grays already match. Moving the haze level or the dark channel's share trades the
same way: share 0.9 gives flats +100 3.60 -> 3.38 for scene +100 1.60 -> 1.62, haze level 0.97 and
share 1.0 flats 2.51 for scene 2.49. The scene chart is closer to photos, so both stay.

**The built-in look (not changed).** On its look-alone bake (1.13; neutrals 0.17) the error sits in
bright reds and yellows (2.81 / 3.17 at max above 0.9); for bright yellows Lightroom has 6.7 more
chroma, 2.7 less L* and 1.8 degrees less hue. The RGB table is in Adobe RGB primaries with gamut
clip, and no grid colour leaves that gamut, so gamut handling isn't it (clip, extend and a clip
toward luminance all give 1.13). Blending the table in linear light instead of encoded is worse
(1.57). The hue/sat map: swapping its order with the table 1.13, the sRGB value key 1.98, and
dropping its value scale, saturation scale or hue shift 1.26 / 1.39 / 1.26; trilinear lookup 1.14.
Each part helps and the current arrangement is the best found; separating the table from the map
needs Lightroom bakes.

## The Android app: the look module, the bundled looks and the camera

The app lives in `android/`. Its `:look` module is plain Kotlin, so its tests run on the JVM against
fixtures written by colorlab (`android/look/src/test/fixtures/make_conformance.py`): a synthetic
look of our own settings, 248 colours with colorlab's tetrahedral lookup, and 21 invalid look files
that colorlab rejects. CI regenerates the fixtures and fails if they differ from the committed ones.

| Commit | Stage | Evidence |
|---|---|---|
| a9777db | RED | `./gradlew :look:test`: compile errors, `FsimReader`, `Section`, `FsimFormatException`, `Lut3D` unresolved |
| fd22338 | GREEN | 5 tests, 0 failures: the reference look's settings and defaults, all 21 invalid files and text over 8 MiB rejected, lookup within 2e-6 of colorlab on 248 colours |
| 7236329 | RED | `LookSource`, `LookLibrary` unresolved; the 57 bundled looks added |
| d0bb20f | GREEN | 8 tests: every bundled look parses as a 33-point LUT under its slug file name with author, licence and description; the library sorts, finds by name, rejects bad files and repeated names |
| 2eb832e | RED | `Lut3D.applyArgb` unresolved |
| 8a880bd | GREEN | 11 tests: `applyArgb` equals the rounded lookup on 4096 pixels with alpha kept, touches only its range, identity changes nothing |
| 371f352 | build | `./gradlew :app:lintDebug :app:assembleDebug`: no lint issues, debug APK with the 57 looks |

The bundled looks are every private preset baked with `fsim.from_preset` (33 points) under names
of our own, grouped by the LUT's character (black and white, warm, cool, neutral), with a
description made from the LUT (contrast, faded blacks, colour, grain, vignette). Two presets built
on Adobe's installed profile looks are left out. The name map stays in `looks/private/`.

The preview renders the LUT on the GPU with the same tetrahedra as `Lut3D.lookup`, and captures
run `Lut3D.applyArgb` at full resolution, so they share the tested lookup. Not yet checked: the app
on a device (orientation, the preview's colour against the capture), and the spatial passes, which
aren't ported yet.

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
