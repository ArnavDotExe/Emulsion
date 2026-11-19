# CLAUDE.md

Context for Claude Code sessions on this repo, on any machine.

## The project

**Emulsion** is an open-source (GPL-3.0-or-later) Android film-emulation camera and editor, in the
spirit of Dazz Cam and Leica LUX, which aren't on Android. The main test device is a Pixel.
Repo: https://github.com/ArnavDotExe/Emulsion

Right now the repo holds the **Python color lab** (`tools/colorlab`), the reference implementation
of the color science. The Android app (Kotlin, Compose, CameraX, OpenGL ES 3 film engine tested
against the Python reference) comes later.

### Naming: Emulsion vs FilmSim

- Only the **app name** (README and docs prose) and the **GitHub repo** are "Emulsion".
- Code, program files, identifiers, folder names and Lightroom preset groups stay **FilmSim**:
  for example the `"FilmSim Bake"` and `"FilmSim Calibration"` groups, the `FilmSim Bake - ` prefix,
  and the local folder name.
- Rename only what the user explicitly names. Never widen a rename to code.

## Privacy rules (the repo is public)

- The user's Lightroom presets are mostly **commercial packs**, and everything derived from them is
  private:
  - `presets/private/`: the presets.
  - `looks/private/`: LUTs baked from them.
  - `tools/colorlab/tests/private/`: tests that name them.
  - `samples/`: test photos.
  - `out/`: all generated output, including calibration bakes.

  All of these are gitignored. Never commit them, quote preset or pack names in tracked files, or
  move them elsewhere.
- **Before every push**, scan tracked files for pack and look names. Build the term list from the
  file names in `presets/private/` and `looks/private/`, for example
  `git grep -n -i -E "<pack>|<look>|..."`, and fix every hit. Don't write that list into any tracked file.
- Commit only as `ArnavDotExe <146160723+ArnavDotExe@users.noreply.github.com>`. Set it in the
  local git config (`git config user.name` / `user.email`) on a new machine. Never use a personal
  email in commits or in requests to outside services.
- Third-party app packages (`*.ipa`, `*.apk`, `*.aab`) are gitignored, kept only for local study.
  The Dazz Cam IPA that was studied is an **untrusted modified build**: never install or run it.
  Its LUT assets are encrypted, and we don't break that encryption. Dazz-style cameras get rebuilt
  from our own LUTs and procedural grain and leaks.
- Copying other apps' color science is fine for personal study, but shipped look names must be our
  own. Don't use Fujifilm or other trademarks as look names.

## How to work

- **Test-first** (RED → GREEN → refactor) with git checkpoint commits:
  - `test: ... (RED)` and `fix: ... (GREEN)`, each commit message stating the evidence.
  - Coverage stays ≥ 80% (currently about 98%).
  - Record evidence in `docs/tdd/colorlab-phase1-core.tdd.md`.
- Tests that need private files go in `tools/colorlab/tests/private/` and skip when the files are absent.
- CI (`.github/workflows/colorlab.yml`) runs the public suite on Python 3.11–3.13.
- On the original Windows machine, `gh` is at `"/c/Program Files/GitHub CLI/gh.exe"` and isn't on PATH.

```bash
cd tools/colorlab
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest --cov                         # everything (private tests skip without private files)
pytest --cov --ignore=tests/private  # what CI runs
```

## colorlab map (`tools/colorlab/src/colorlab`)

| Module | Role |
|---|---|
| `colorspace` | RGB spaces, Bradford conversions, transfer curves |
| `lut` | `Lut3D`, tetrahedral/trilinear lookup, `.cube`, Hald images |
| `adobe_table` | Lightroom `crs:Table_*` RGBTable decode/encode/apply; a look profile's `crs:LookTable` hue/sat/value map (`HueSatMap`, DNG HueSatMap semantics on linear ProPhoto) |
| `preset` | `.xmp` / mobile `.dng` presets → typed `Preset`, look resolution (also from installed profile folders, `load_profiles`) |
| `pipeline` | Reference model of a preset's global color in Lightroom order; `render()`, `render_lut()` |
| `response` | Lightroom's **measured** slider shapes (exposure shoulder, white-balance stops per Bradford cone, tone and parametric curves per amount), stored in `data/lightroom_response.json` |
| `calibration` | 147 single-slider calibration presets; `response` measures shapes from bakes, `fit` fits `K_SCENE`, the one remaining strength |
| `spatial_calibration` | Spatial calibration kit: 70 one-slider chart images (scene, levels, checker) with embedded sidecar XMP for highlights/shadows, clarity, texture, dehaze, sharpening, grain, noise reduction and vignette; `collect` checks and copies the exports |
| `bake` | Lightroom bake kits (presets + Hald TIFFs with an embedded sidecar-style XMP, which Lightroom auto-applies on import) and `collect` of exports into `.cube` |
| `film` | Halation (scatter + bounces, scene-anchored highlight boost) and particle grain, sized in µm on the film; ported from spektrafilm and darktable (credited in the header); `apply` CLI for TIFFs |
| `local_contrast` | Highlights/Shadows (a tone table over the pixel and a Gaussian base of width/32, per amount, in `data/local_tone.json`), Clarity (midtone-weighted detail around a Gaussian base of width/96, on Y^(1/2.2)) and Texture (16 px guided filter on Y, Cb, Cr) and Dehaze (a monotone local tone table and a linear-light color gain, mixed for positive amounts with a dark channel prior), fitted to Lightroom's spatial exports; also `local_laplacian` (from darktable, credited) and `guided_filter`; `apply` CLI |
| `finish` | Vignette (measured mask profile plus a value × weight table per amount; midpoint, feather, roundness, styles, highlights), sharpening (clamped unsharp mask on luma, fixed in pixels), noise reduction (guided filter on Y, Gaussian on CbCr), Lightroom-statistic grain (seeded `G_s − b G_2s` noise, frame-relative size), all in `data/finish.json`; `apply_settings` runs every spatial pass a preset names in Lightroom's order; `render_preset` runs local passes and vignette before the look, grain after, with the look profile's Highlights/Shadows folded in (`spatial_settings`); `apply` CLI |
| `preset_check` | Whole-preset check: `kit` writes full preset copies (geometry neutralized) on the scene chart as embedded sidecars; `compare` scores Lightroom's exports against `finish.render_preset` (global, spatial passes after or before the look, blurred, and on Lightroom's baked LUTs), saving its JSON per preset and resuming from it |
| `metrics` | sRGB → Lab, CIEDE2000 |

The pipeline works in ProPhoto primaries. Stages pass sRGB-curve encoded values, in Lightroom's order:
Whites/Blacks (with exposure), saturation, vibrance + HSL (or B&W), then contrast, parametric, point
curves, profile table and color grading.
Saturation, vibrance and HSL work on scene-linear values: the exposure shoulder's stretch, applied
hue-preservingly (min and max mapped, middle kept in place; `pipeline._to_scene`). The colour
stages use `_to_color_scene`: the same stretch up to `COLOR_KNEE` (0.94 encoded), then straight
on, because the shoulder runs off to infinity at white and wiped out near-white tints. White
balance and exposure are gains on that same scene scale: per Bradford cone for white balance, so
white stays white. Neutral grays pass through unchanged between sRGB and the working space, so a
bake's neutral ramp *is* Lightroom's tone curve.

### Calibration loop (needs Lightroom; all output under `out/`)

```bash
python -m colorlab.calibration presets out/cal-presets            # or: presets <new dir> --only-missing out/cal-luts
python -m colorlab.bake kit out/cal-presets out/cal-kit           # import into Lightroom, export per kit README
python -m colorlab.bake collect <exports> out/cal-kit out/cal-luts
python -m colorlab.calibration response out/cal-luts --out src/colorlab/data/lightroom_response.json
python -m colorlab.calibration fit out/cal-luts --json out/cal-fit.json   # then copy K_* into pipeline.py
```

Spatial loop (task 6b): `python -m colorlab.spatial_calibration kit out/spatial-kit`, import
`images/` into Lightroom and export per the kit README, then
`python -m colorlab.spatial_calibration collect <exports> out/spatial-kit out/spatial-exports`.
Each export is compared with its chart's `Spatial Identity` export. `kit --scale-test` writes the image-size test (the same effects at
768×512 and 3072×2048) into a separate kit.

Lightroom quirks:
- It ignores a lone `IncrementalTemperature` or `IncrementalTint`; set both, with `WhiteBalance=Custom`.
- It ignores a lone channel curve; list all four curves, using identity for the unused ones.

## Status and roadmap

- Phase 1 tasks 1–5 (colorspace, lut, adobe_table, preset, bake) are done. Task 6a (global-color
  pipeline) is done and calibrated against 143 single-slider bakes.
- Accuracy against the user's 44 private presets: median ΔE2000 0.75, mean 1.12 (on a 17³ grid
  against their baked LUTs the colour scale's knee took the median from 0.750 to 0.705).
- Tone is split the way Lightroom splits it (`pipeline.whites_blacks`, `pipeline.curve_tone`):
  - Whites/Blacks run first, with exposure. Whites + uses linear min/max, Whites − and Blacks +
    one scene-scale gain at the (R + 2G + B)/4 key, and Blacks − each channel on the scene scale.
  - Contrast, parametric and master curve use linear min/max before process version 15.4. From
    15.4 Lightroom combines them into one curve on sRGB-encoded values with min/max pulled toward
    the middle channel (`rgb_tone(pull=True)`). Pulled tones compose exactly, so applying them in
    turn is the same.
  - `CurveRefineSaturation` below 100 (PV ≥ 15.4) sends the point curve through the luminance mode
    (`pipeline.luma_curve`): one 0.30/0.59/0.11 luma shift for all channels, scaled into range,
    blended by (100 − value)/100. All the private presets use 100.
  - Per-channel curves stay per channel.
  - Tone bakes are now 0.05 (worst Blacks −100 0.39). The 9 presets that got worse all have custom
    master curves; see the TDD doc.
- White balance is measured stops per Bradford cone, as gains on the scene scale
  (`pipeline.white_balance_stops`); exposure is the same gain on all channels. Both keep the hue of
  linear values. Fitted against the ProPhoto exports, Bradford beat CAT02, CAT16, HPE and XYZ.
- Color grading is reconstructed, not fitted (`pipeline.grade_curves`): three per-channel 1-D curves
  on linear working values, matching all 24 grading bakes to 0.007 ΔE. Since they depend only on
  the look's settings, the app bakes them into the look's 3D LUT, at no per-frame cost. Split-toning
  keys without `ColorGradeBlending` mean Blending 100.
- Saturation (a luminance-keeping opponent matrix) and vibrance (a min/max kernel that keeps hue)
  are reconstructed too, on the scene scale: Saturation bakes 0.43, Vibrance 0.56. Moving them,
  HSL and B&W before the tone curve, as Lightroom orders them, took the presets from median 2.53 to 1.63.
- HSL is reconstructed (`pipeline.hsl_adjust`):
  - Vibrance and the eight bands run as one stage on scene min/max/hexcone hue.
  - Band centers are 0/30/55/95/160/230/275/330°, with linear tables (monotone splines and a
    smooth hue model from `ProcessVersion` 15.4).
  - Hue moves at most max(0.9 × gap, 30°).
  - One fitted scale, `K_SCENE` = 0.141, replaced `K_HUE`, `K_LUMINANCE` and `K_VIBRANCE`.
  - HSL bakes 0.11 (hue 0.008).
- The B&W mixer is reconstructed (`pipeline.grayscale`):
  - The gray is `lo + w × (hi − lo)` on the HSL stage's scene scale. `w` is the per-hue band weight.
  - Defaults are the scene luminance of each band center's pure color, derived in `_mixer_defaults`.
  - Sliders follow a parabola to −2 and to default + 2.25, and negative weights darken along a
    perceptual curve.
  - B&W bakes are 0.34, and `K_MIXER` is gone.
  - Process version 15.4's own hue table isn't reconstructed; it needs a B&W bake at PV 15.4.
- ProPhoto exports of all 147 bakes are in `out/cal-exports-prophoto` (16-bit, gamma 1.8). They
  show that Lightroom's sRGB export is a plain clip of its ProPhoto result (0.006 ΔE), with no
  gamut mapping, and they keep out-of-sRGB colors for checking the model.
- All 147 PV 11 calibration bakes are collected (mean 0.18 ΔE).
- The 11 PV 15.4 bakes (`Cal PV15 …`) are collected. They came back as ProPhoto and were
  converted to sRGB (raw cubes in `out/cal-luts-pv15-prophoto`; `bake collect` doesn't check the
  ICC profile, so export sRGB).
  - The combined curve stage and the luminance mode (Contrast, Parametric, Master S, Red,
    Refine 0/50) match to 0.01.
  - Known gap: Whites +100 at 15.4 is 1.85. Lightroom lifts the low channel of bright saturated
    colors toward white, and PV 11 doesn't. Whites −100 0.35, Blacks +100 0.29, Blacks −100 0.61.
- Spatial facts measured from the exports (`out/spatial-exports`, `out/spatial-scale-exports`):
  - Identity exports match the charts (TIFFs get no default sharpening or NR), and grain is
    deterministic (repeat exports are bit-identical).
  - Image size, from the same charts at 768, 1536 and 3072 px wide:
    - Vignette is relative to the frame: the same falloff at every size.
    - Clarity −50's halo grows with the image (7/14/27 px).
    - Texture (4 px) and Sharpening (1 px) are fixed in pixels.
    - Shadows shows no halo (edge-preserving).
    - Grain grows slowly with image size: correlation 0.44/0.47/0.61 px, std 0.087/0.074/0.051 at Grain 50.
    - Dehaze's halo grows to about 45 px, then stops growing.
  - For the app: Texture, Sharpening and grain must render at the output resolution, or be
    compensated, for previews to match exports.
- 6b: the spatial kit is exported (`out/spatial-exports`, `out/spatial-scale-exports`).
  Clarity and Texture are modelled (`colorlab.local_contrast`, d3dec13). Mean ΔE on the scene chart,
  ours against Lightroom's change from identity:
  - Clarity: −100 0.55/2.29, −50 0.33/1.29, +50 1.06/2.86, +100 1.88/5.38. It holds at 768 and
    3072 px, because its base follows the frame.
  - Texture: ±100 0.30–0.33/0.70, fixed 16 px.
  - The local Laplacian fitted worse for Clarity (−100 0.85, +100 2.06). It moved the bright side
    of an edge where Lightroom lifts the dark side. It lost for Highlights/Shadows too and is kept
    only as a tested filter.
  - Gap: positive Clarity's wide one-sided local contrast (about 200 px).
  - Highlights/Shadows (`local_contrast.highlights_shadows`, bad0f97): a 17 × 9 table of the change of
    Y^(1/2.2) over the pixel and a width/32 Gaussian base, one per slider at ±25/50/100, kept
    monotone. Lightroom's change depends on the neighbourhood (gradients a few hundred px long in
    flat patches) but has no halo at edges. Shadows −100 0.27/4.23, +100 0.66/6.87; Highlights
    −100 1.02/9.72, +100 0.55/6.14. It holds at 768 and 3072 px. The local Laplacian lost here
    too (1.1–1.7). Gaps: the color carry, and Highlights + moving bright saturated colors toward white.
  - Vignette, sharpening, noise reduction and grain are in `colorlab.finish`, and Dehaze in
    `local_contrast.dehaze` (e6a9e8a). Mean ΔE, ours against Lightroom's change from identity:
    - Vignette: ±25–100 0.07–0.21 (to 13.9); Midpoint 0 0.47, Feather 0 0.57, roundness ±100 0.16,
      Paint Overlay 0.32. It holds at 768 and 3072 px.
    - Sharpening: 25/50/100 0.03/0.06/0.11 (to 0.49), Radius 3 0.10, Detail 100 0.17. It is fixed
      in pixels; Masking isn't modelled.
    - Noise reduction: Luminance 0.16–0.30 (to 1.02), Color 0.52 (1.6). Color is at its floor:
      Lightroom's leftover chroma is mostly incoherent with the input, and even its measured linear
      response only reaches 0.43 (see the TDD doc).
    - Grain: Lightroom's statistics, not its pattern. Its sd is within 0.5% (2.6% at 768 px) and
      its lag-1 within 0.02, with an effective size of `size · (width/1536)^1.8`.
    - Dehaze: −100 0.83 (23.4), ±25–50 0.31–0.71, +100 1.60 (13.3). Positive amounts mix in a dark
      channel prior with a smooth haze map (`dehaze_radii`: width/128 min filter, width/16 guided
      filter), which keeps a saturated color's main channel as Lightroom does (cbc4577).
  - Whole-preset check (`colorlab.preset_check`), on the user's export of `out/preset-kit`
    (`out/preset-kit/exports`, report `out/preset-compare-v2.json`), blurred σ 2 px: median 0.85 /
    mean 1.00 with our look, 0.63 / 0.82 on Lightroom's baked LUTs. `render_preset` runs the
    local passes and then the vignette before the look and the grain after it (local first wins on
    38 presets and loses on none). `finish.spatial_settings` folds a look profile's own
    Highlights/Shadows (at the look's amount) into the local pass. Lightroom ignores a look's
    Sharpness. `compare` saves its JSON after every preset and resumes from it.
    Gaps: one preset (4.88, 1.62 on its LUT) uses Adobe's built-in "Vintage 05" look. It now
    resolves from Camera Raw's installed profiles (`load_preset_dir(profile_dirs=...)`,
    `compare --profiles`), with the table at look amount × `RGBTableAmount` and then the
    profile's `LookTable` hue/sat map (`adobe_table.HueSatMap`): 1.81. Measured from the
    decomposition bake in `out/decomp-luts`. The PV 15.4 colour bake (`out/brightsat-kit`, sRGB and
    ProPhoto exports) showed PV 15.4 HSL is right and gave the colour scale its knee: grid 3.31 ->
    2.92. PV 15.4's positive Saturation has its own shoulder and pivot
    (`pipeline.POSITIVE_SATURATION_SHOULDER` 3.2, `POSITIVE_SATURATION_PIVOT`): ProPhoto +50/+100
    0.95/1.37 -> 0.77/1.01, grid 2.78. Left: bright yellows under that look;
    strong Highlights +
    Clarity + Dehaze presets are 1.5–2.5. Looks whose profile carries Highlights/Shadows have
    baked LUTs that hold a Hald-specific version of that pass.
  - `finish.apply_settings` runs every spatial pass in a preset in Lightroom's order (NR,
    Highlights/Shadows, Dehaze, Texture, Clarity, sharpening, vignette, grain) on the encoded
    image (`finish.render_preset` orders them around the look as above).
- Biggest single-slider gaps:
  - Temperature −100 1.5, Temperature +100 1.1, Tint ±100 1.0 (the 12 WB bakes are 0.70).
  - Vibrance −100 0.82–0.87 (`pipeline.NEGATIVE_VIBRANCE_DARKEN` 0.88, from 1.45) and Saturation +100
    0.6 (PV 11; PV 15.4 has its own positive Saturation).
  - Exposure ±2 is 0.8–0.9. Tone extremes are fixed: Blacks −100 0.39 and Whites −100 0.29 are the
    worst, from 2.6 and 2.3.
  - HSL is at most 0.67 (Lum Green −100), and B&W at most 0.46 (Mix Green +100).
  - `calibration._WB_GRAY_WEIGHT` sets the white-balance fit's balance of colors against grays.
- darktable (GPL-3.0-or-later) was studied for 6b and the camera: `docs/study/darktable.md` ranks
  reuse candidates (spektrafilm halation and grain, local Laplacian, dark channel + guided filter).
- spektrafilm halation and grain are ported (`colorlab.film`, 780e2d5):
  - Halation runs on scene-linear light before the look; grain runs on the final image as
    positive-film density.
  - Sizes are µm on the film (`pixel_size_um`, 36 mm for 135), so previews and captures match and
    the format sets grain size.
  - Not ported: sublayer grain, dye clouds, micro-structure, spektrafilm's spectral color and profiles.
- Then:
  - 6b's gaps: Dehaze +100 (1.60: lightness inside flat areas, needs an edge-aware large-scale haze
    map), Saturation − with Vibrance − (needs a bake), and the whole-preset gaps above.
  - 7: check against the Fuji DNG presets' embedded Lightroom previews.
  - 8: the `.fsim.json` look format.
  - 9: the CLI.
  - After that, the Android app.
  - Low priority, after the app's core: Fuji in-camera film simulations (shipped under our own
    look names, no camera-maker trademarks).
- Deferred by the user: Leica LUX.
