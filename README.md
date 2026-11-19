# Emulsion

An open-source film-emulation camera for Android, in the spirit of apps like
Dazz Cam and Leica LUX, which don't exist on Android. Emulsion aims to give
Android photographers analog-style looks, film simulations and filters,
with a color pipeline that is open, measurable and hackable.

> **Status: early development.** The repo holds the Python *color lab* that defines and
> tests the color science, and the first Android app: a camera with 43 bundled looks, a live
> GPU preview of the selected look, manual controls (EV, ISO, shutter, focus, white balance) and
> full-resolution captures saved to `DCIM/Emulsion`. The camera screen's layout follows
> [PhotonCamera](https://github.com/eszdman/PhotonCamera) (GPL-3.0), an open-source Android camera.

## What's here

| Path | What it is |
|---|---|
| [`tools/colorlab/`](tools/colorlab/) | Python reference implementation: color spaces, 3D LUTs (`.cube`, Hald), Lightroom/Camera Raw preset reading (`.xmp`, mobile `.dng`), Adobe RGBTable decoding |
| [`docs/`](docs/) | Design notes and test evidence |
| [`android/`](android/) | The Android app (Kotlin, Compose, CameraX, OpenGL ES 3) and its `:look` module, tested against the color lab |
| [`android/app/src/main/assets/looks/`](android/app/src/main/assets/looks/) | The bundled looks, as `.fsim.json` files |

## Roadmap

1. **Color lab** (done): the reference film pipeline (tone, curves, HSL, color grading, look
   tables, local contrast, grain, vignette), checked against Lightroom renders.
2. **Android app** (in progress): Kotlin, Jetpack Compose and CameraX, with an OpenGL ES 3 film
   engine whose output is tested against the Python reference. The look's colour runs in the
   preview and on captures; local contrast, vignette and grain come next.
3. **Cameras**: recipes combining a look, grain, light leaks, dust, frames, date stamps
   and more, with every overlay generated procedurally.
4. **Editor**: apply looks to existing photos; import `.xmp`, `.dng`, `.cube` and `.fsim` looks.
5. Fuji-style film simulations, RAW processing and lens/depth effects.

## Looks and presets

The bundled looks are baked by the color lab into Emulsion's own `.fsim.json` format and carry
names of our own that say what they evoke (Portrait 400, Slide 200, Cine 500T, Fine Mono 100,
Chrome 64, Instant Muted, ...), without any maker's trademarks. Raw preset files are never committed: put Lightroom presets you own in
`presets/private/`. That folder, `looks/private/` and `samples/` are gitignored.

## Build the app

Needs JDK 21 and the Android SDK (platform 37), with `sdk.dir` in `android/local.properties`:

```bash
cd android
./gradlew :look:test            # the look module against the color lab's fixtures
./gradlew :app:assembleDebug    # app/build/outputs/apk/debug/app-debug.apk
```

## Develop the color lab

```bash
cd tools/colorlab
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest --cov
```

Python 3.11 or newer. Coverage must stay at or above 80%.

The install adds a `colorlab` command (also `python -m colorlab`):

```bash
colorlab info my-preset.xmp                       # the look and the sliders a preset moves
colorlab render my-preset.xmp photo.tif out.tif   # the whole preset on an sRGB TIFF
colorlab lut my-preset.xmp my-look.cube           # its global colour as a 3D LUT
colorlab look my-preset.xmp my-look.fsim.json     # an Emulsion look file
colorlab apply my-look.fsim.json photo.tif out.tif
colorlab --help                                   # and the calibration tools
```

## Credits

Film halation and grain (`tools/colorlab`, `colorlab.film`) are ported from
[spektrafilm](https://github.com/andreavolpato/spektrafilm) by Andrea Volpato (GPL-3.0) and
from [darktable](https://www.darktable.org/)'s port of it (GPL-3.0-or-later).

## License

[GPL-3.0-or-later](LICENSE). Fork it, improve it, and keep it open.

Emulsion isn't affiliated with Dazz Cam, Leica, Fujifilm or Adobe. Their names
are used only to describe what this project is inspired by or interoperates with.
