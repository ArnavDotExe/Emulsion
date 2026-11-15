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
| `response` | Lightroom's **measured** slider shapes (exposure shoulder, white-balance stops per channel, tone and parametric curves per amount, vibrance gains), stored in `data/lightroom_response.json` |
| `calibration` | 147 single-slider calibration presets; `response` measures shapes from bakes, `fit` fits the remaining `K_*` strengths |
| `spatial_calibration` | Spatial calibration kit: 70 one-slider chart images (scene, levels, checker) with embedded sidecar XMP for highlights/shadows, clarity, texture, dehaze, sharpening, grain, noise reduction and vignette; `collect` checks and copies the exports |
| `bake` | Lightroom bake kits (presets + Hald TIFFs with an embedded sidecar-style XMP, which Lightroom auto-applies on import) and `collect` of exports into `.cube` |
| `metrics` | sRGB → Lab, CIEDE2000 |

The pipeline works in ProPhoto primaries, and every stage runs on sRGB-curve encoded values. White
balance and exposure are stops on one shared highlight shoulder: per channel for white balance, so
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
Each export is compared with its chart's `Spatial Identity` export.

Lightroom quirks:
- It ignores a lone `IncrementalTemperature` or `IncrementalTint`; set both, with `WhiteBalance=Custom`.
- It ignores a lone channel curve; list all four curves, using identity for the unused ones.

## Status and roadmap

- Phase 1 tasks 1–5 (colorspace, lut, adobe_table, preset, bake) are done. Task 6a (global-color
  pipeline) is done and calibrated against 143 single-slider bakes.
- Accuracy against the user's 44 private presets: median ΔE2000 3.18, mean 4.39.
- All 147 calibration bakes are collected (mean 2.22 ΔE); no re-bake is pending.
- 6b has started. The spatial calibration kit is built (`out/spatial-kit`, 70 images) and is waiting
  for the user to export it from Lightroom. Next: `collect`, then measure each effect and model it.
- Biggest single-slider gaps:
  - Temperature −100 13.4, Temperature +100 6.3. Lightroom isn't purely per-channel on saturated colors.
  - Exposure +2 4.9, Whites +100 4.2.
  - Saturation −50 3.0. Saturation is still a formula and could be measured the same way.
- Then:
  - 6b: spatial effects (grain, vignette, highlights/shadows, clarity, texture, dehaze, sharpening,
    noise reduction). The worst presets are dominated by these.
  - 7: check against the Fuji DNG presets' embedded Lightroom previews.
  - 8: the `.fsim.json` look format.
  - 9: the CLI.
  - After that, the Android app.
- Deferred by the user: Fuji in-camera film simulations and Leica LUX.
