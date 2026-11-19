# Contributing to Emulsion

Thanks for helping. Emulsion is free and open source (GPL-3.0-or-later), and so is everything you
contribute to it.

## Ways to help

- **Try it and report what you see.** Open an issue with your phone model, Android version and, for
  a camera problem, what the preview and the saved photo looked like.
- **Make a look.** A look is one `.fsim.json` file (see "Use and change the looks" in the
  [README](README.md)). Pull requests that add looks are welcome, as long as the look is yours to
  share: made from your own LUT or settings, with a name of your own that doesn't use a
  camera-maker's or film-stock's trademark. Never submit presets, LUTs or packs you bought or don't
  have the rights to.
- **Fix a bug or build a roadmap item** (vignette, grain and local contrast in the app, the editor,
  recipes). Say in an issue what you plan to do first, so nobody builds the same thing twice.
- **Test on your phone.** Emulsion is built and tested on a Pixel; other makers' camera stacks
  differ, and reports from them are valuable.

## Build and test

```bash
# the Android app (JDK 21 and the Android SDK, platform 37; sdk.dir in android/local.properties)
cd android
./gradlew :look:test :app:testDebugUnitTest :app:lintDebug :app:assembleDebug

# the colour lab (Python 3.11+)
cd tools/colorlab
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest --cov --ignore=tests/private
```

CI runs both on every push and pull request.

## How we work

- **Test first.** Write the failing test, see it fail for the right reason, make it pass. Say in
  the commit message what failed and what passes now. Coverage stays at 80% or higher.
- **The lab is the reference.** The Kotlin look reader and the lookup are checked against the Python
  colour lab's output (`android/look/src/test/fixtures/make_conformance.py` regenerates the
  fixtures; CI fails if they are out of date). Changes to the colour maths start in
  `tools/colorlab`.
- **Keep it fast.** The per-pixel path (`Lut3D.applyArgb`) must not allocate: a 12 MP photo has 12
  million pixels. `ArgbTest` pins it to the reference lookup.
- **Keep it private.** The app asks only for the camera and has no network access. Please keep it
  that way.
- **Names.** Looks, houses and code use our own names, not a maker's trademark.
- Small, focused commits and pull requests are easier to review. Describe what you checked on a
  device, with the model.

## License

By contributing you agree that your work is released under
[GPL-3.0-or-later](LICENSE), like the rest of the project.
