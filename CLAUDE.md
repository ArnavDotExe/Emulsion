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
| `adobe_table` | Lightroom `crs:Table_*` RGBTable decode/encode/apply |
| `preset` | `.xmp` / mobile `.dng` presets → typed `Preset`, look resolution |
| `pipeline` | Reference model of a preset's global color in Lightroom order; `render()`, `render_lut()` |
| `response` | Lightroom's **measured** slider shapes (exposure shoulder, white-balance stops per Bradford cone, tone and parametric curves per amount), stored in `data/lightroom_response.json` |
| `calibration` | 147 single-slider calibration presets; `response` measures shapes from bakes, `fit` fits `K_SCENE`, the one remaining strength |
| `spatial_calibration` | Spatial calibration kit: 70 one-slider chart images (scene, levels, checker) with embedded sidecar XMP for highlights/shadows, clarity, texture, dehaze, sharpening, grain, noise reduction and vignette; `collect` checks and copies the exports |
| `bake` | Lightroom bake kits (presets + Hald TIFFs with an embedded sidecar-style XMP, which Lightroom auto-applies on import) and `collect` of exports into `.cube` |
| `metrics` | sRGB → Lab, CIEDE2000 |

The pipeline works in ProPhoto primaries. Stages pass sRGB-curve encoded values, in Lightroom's order:
saturation, vibrance + HSL (or B&W), then tone, parametric, point curves, profile table and color grading.
Saturation, vibrance and HSL work on scene-linear values: the exposure shoulder's stretch, applied
hue-preservingly (min and max mapped, middle kept in place; `pipeline._to_scene`). White
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
- Accuracy against the user's 44 private presets: median ΔE2000 0.87, mean 1.34. Tone curves
  (contrast, whites/blacks, parametric, master curve) are applied hue-preservingly
  (`pipeline.rgb_tone`); per-channel curves stay per channel. White balance is measured stops per
  Bradford cone, as gains on the scene scale (`pipeline.white_balance_stops`); exposure is the same
  gain on all channels. Both keep the hue of linear values. Fitted against the ProPhoto exports,
  Bradford beat CAT02, CAT16, HPE and XYZ.
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
- All 147 calibration bakes are collected (mean 0.33 ΔE); no re-bake is pending.
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
- 6b has started. The spatial calibration kit is built (`out/spatial-kit`, 70 images) and is waiting
  for the user to export it from Lightroom. Next: `collect`, then measure each effect and model it.
- Biggest single-slider gaps:
  - Temperature −100 1.5, Temperature +100 1.1, Tint ±100 1.0 (the 12 WB bakes are 0.70).
  - Vibrance −100 1.5 and Saturation +100 0.6.
  - Tone extremes are now the worst: Blacks +100 2.6, Whites −100 2.3, Parametric Darks −100 1.5.
    Exposure ±2 is 0.8–0.9.
  - HSL is at most 0.67 (Lum Green −100), and B&W at most 0.46 (Mix Green +100).
  - `calibration._WB_GRAY_WEIGHT` sets the white-balance fit's balance of colors against grays.
- Then:
  - 6b: spatial effects (grain, vignette, highlights/shadows, clarity, texture, dehaze, sharpening,
    noise reduction). The worst presets are dominated by these.
  - 7: check against the Fuji DNG presets' embedded Lightroom previews.
  - 8: the `.fsim.json` look format.
  - 9: the CLI.
  - After that, the Android app.
- Deferred by the user: Fuji in-camera film simulations and Leica LUX.
