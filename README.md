# Emulsion

An open-source film-emulation camera for Android, in the spirit of apps like
Dazz Cam and Leica LUX, which don't exist on Android. Emulsion aims to give
Android photographers analog-style looks, film simulations and filters,
with a color pipeline that is open, measurable and hackable.

> **Status: early development.** Right now the repo holds the Python
> *color lab* that defines and tests the color science. The Android app comes next.

## What's here

| Path | What it is |
|---|---|
| [`tools/colorlab/`](tools/colorlab/) | Python reference implementation: color spaces, 3D LUTs (`.cube`, Hald), Lightroom/Camera Raw preset reading (`.xmp`, mobile `.dng`), Adobe RGBTable decoding |
| [`docs/`](docs/) | Design notes and test evidence |
| `looks/` | Open look definitions shipped with the app *(coming)* |

## Roadmap

1. **Color lab** (in progress): preset/LUT tooling done; reference film pipeline next
   (tone, curves, HSL, color grading, look tables, grain, vignette), then
   checks against Lightroom renders.
2. **Android app**: Kotlin, Jetpack Compose and CameraX, with an OpenGL ES 3 film engine whose
   output is tested against the Python reference.
3. **Cameras**: recipes combining a look, grain, light leaks, dust, frames, date stamps
   and more, with every overlay generated procedurally.
4. **Editor**: apply looks to existing photos; import `.xmp`, `.dng`, `.cube` and `.fsim` looks.
5. Fuji-style film simulations, RAW processing and lens/depth effects.

## Bring your own presets

Emulsion ships **no third-party presets or LUTs**. If you own Lightroom presets,
put them in `presets/private/`. That folder, `looks/private/` and `samples/`
are gitignored, so personal or commercial files are never committed.

## Develop the color lab

```bash
cd tools/colorlab
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest --cov
```

Python 3.11 or newer. Coverage must stay at or above 80%.

## License

[GPL-3.0-or-later](LICENSE). Fork it, improve it, and keep it open.

Emulsion isn't affiliated with Dazz Cam, Leica, Fujifilm or Adobe. Their names
are used only to describe what this project is inspired by or interoperates with.
