# darktable study (for task 6b and the camera)

Studied 2026-11-18 from darktable master at commit `1c4eaea9df6d` (a shallow, sparse clone of
`src/iop`, `src/develop` and `src/common`, kept outside this repo). darktable is
**GPL-3.0-or-later**, like Emulsion, so its code can be reused. Any ported code must keep the
original copyright notice ("Copyright (C) … darktable developers") and say where it came from.
No darktable code is in this repo yet. This page lists what each module does and what it could
give us.

The key point for Lightroom fidelity: darktable does **not** reproduce Lightroom. Its importer
(`src/develop/lightroom.c`) maps only a few spatial settings (grain, post-crop vignette, clarity),
through fixed constants. It has nothing for Texture, Dehaze, Sharpening, Highlights/Shadows or
noise reduction. So our measured Lightroom models stay the reference, and darktable is a source of
algorithms (filters, pyramids, spatial effects) to fit to those measurements.

## Modules, by what Emulsion needs

| Need | darktable module | Algorithm | Use for Emulsion |
|---|---|---|---|
| Film look as a whole | `iop/spektrafilm.c`, `common/spektra_sim.c`, `common/spektra_core.c` | Spectral film simulation (below) | **High.** A physically based film engine; see below |
| Grain | `iop/grain.c` | Simplex noise (octaves) at coordinates normalized to the image's short side, added to Lab L through a sigmoid "paper response" LUT, so midtones get the most (`midtones_bias`) | Medium. Lightroom's grain is fixed to the frame differently (it grows slowly with image size, as measured); the paper-response idea is useful, the scaling isn't |
| Grain, physical | spektrafilm `grain` | Poisson particle model per emulsion sublayer, in film density, with dye-cloud blur; seeded per image; sized in micrometres on film, so it keeps its character at any zoom | **High** for a film camera: grain tied to the format (35 mm vs medium format), not the pixel count |
| Halation / scatter | `spektra_core.c::sf_halation` | On linear light: emulsion scatter (core Gaussian plus a three-Gaussian tail) and three base-reflection bounces, each wider, weighted 1, ½, ¼, with per-channel strength (red > green > blue; less on anti-halation stocks) | **High.** Halation is one of the signature effects of a film camera; Lightroom has no such slider |
| Highlight boost | `spektra_core.c::sf_boost_highlights` | Bows the response above `midgray × 2^protect` so clipped highlights feed halation with extra energy; anchored to scene exposure, not to the frame's peak, so previews, tiles and exports match | High, with halation |
| Vignette | `iop/vignette.c` | Superellipse distance `(|x|^(2/s) + |y|^(2/s))^(s/2)`, a raised-cosine ramp from `scale` over `falloff`, then multiply (darken) or add (brighten), plus a saturation change and optional dither | Medium. Same family as Lightroom's post-crop vignette; fit its amount/midpoint/feather/roundness to our spatial exports |
| Clarity / local contrast | `iop/bilat.c`, `common/locallaplacian.c` | Local Laplacian filter (Paris et al.): Gaussian pyramid, six gamma levels, a remapping curve with separate shadows/highlights/clarity terms; or a bilateral grid | **High** for Clarity and Texture: edge-aware and multi-scale; to check whether its halo can follow Lightroom's Clarity halo, which grows with image size |
| Dehaze | `iop/hazeremoval.c` | Dark channel prior (He et al.): dark channel, ambient light `A0` from the haziest pixels, transmission map refined by a guided filter (`common/guided_filter.c`), `strength` and `distance` | **High.** Same method family as Lightroom's dehaze; a fixed guided-filter radius could explain the halo that stops growing (about 45 px), still to be tested |
| Sharpening | `iop/sharpen.c` | Unsharp mask on Lab L: Gaussian radius × 2.5 support, `amount`, `threshold` dead zone | Medium. Lightroom's sharpening is fixed in pixels (1 px measured); USM with a detail mask is the base |
| Highlights / Shadows | `iop/shadhi.c`, `iop/toneequal.c` | `shadhi`: blurred (Gaussian or bilateral) luminance mask, overlay-style blend with `compress`. `toneequal`: exposure per tone band on a guided-filter luminance mask | High. We measured Lightroom's Shadows as halo-free (edge-preserving), which points to a guided or bilateral mask |
| Bloom / diffusion | `iop/bloom.c`, `iop/diffuse.c` | Bloom: threshold, blur, screen blend. Diffuse: multi-scale anisotropic diffusion PDE with presets (bloom, dehaze, denoise, lens deblur) | Low–medium; spektrafilm's diffusion filter is the film-grounded version |
| Noise reduction | `iop/denoiseprofile.c` | Variance-stabilizing transform from per-camera noise profiles, then non-local means or wavelets | Later (Android camera RAW path), not for looks |
| Scene → display | `iop/sigmoid.c`, `iop/agx.c`, `iop/filmicrgb.c` | Log-logistic curve (contrast, skew; per channel or hue preserving); AgX's log encoding, sigmoid and look; filmic's log + spline | Medium for the camera: a scene-referred transform between CameraX's linear frames and the look LUT |

## spektrafilm in detail

A native port of **spektrafilm** by Andrea Volpato (<https://github.com/andreavolpato/spektrafilm>,
GPL-3.0; film and paper profile data **CC BY-SA 4.0**). The header says it matches the Python
reference to 1e-4. Per pixel, from scene-linear RGB:

1. CAT16 to the film's reference illuminant, xy → spectrum (a spectral-upsampling LUT) × film
   spectral sensitivity: the camera exposure.
2. Highlight boost, diffusion and halation (linear, spatial).
3. Log exposure, then DIR-coupler correction (blurred, so colors interact between layers): the
   development.
4. CMY film density, then grain in density space.
5. Enlarger: dichroic-filtered light through the negative onto print paper's sensitivity,
   balanced at mid-gray: the print exposure.
6. Optional print diffusion, then print density curves (with a contrast morph).
7. Viewing illuminant through the print, CMFs → XYZ → CAT02 → working RGB, then OkLCh gamut
   compression: the scan.

It is a scene-to-display transform: it replaces sigmoid, filmic or AgX rather than stacking on them.
The spectral data ships separately (the `darktable-spektrafilm` data packs: `pack.json`,
`spectra_lut.f32` and per-stock film and paper profiles).

What this means for Emulsion:
- Since the reference is **Python**, it fits `tools/colorlab` directly. It could be studied or
  vendored there, under its GPL notice, and checked against darktable's port.
- Stage 1–7 color depends only on the stock, the print and the settings, so like our Lightroom
  looks it **bakes into a 3D LUT** (with exposure as an input axis or a per-frame gain). The
  spatial parts (grain, halation, diffusion) are a handful of Gaussian blurs and a seeded particle
  field: GPU-shader work, and sized in micrometres on film, so grain and halation follow the
  chosen format.
- The profile data is CC BY-SA 4.0: shipping derived LUTs needs attribution and share-alike for
  that data. The profiles are named after real stocks (trademarks), so shipped looks need our own
  names (see CLAUDE.md).

## Candidates to reuse, ranked

1. **spektrafilm halation + scatter and the physical grain**: no Lightroom equivalent, central
   to a film camera, and small (blurs plus a particle sampler). **Done:** `colorlab.film`.
2. **Local Laplacian** (`common/locallaplacian.c`) as the base for Clarity and Texture, fitted to
   our spatial exports. **Done, with a different outcome:** `colorlab.local_contrast` has the
   filter, but it doesn't fit Lightroom's Clarity, whose wide halo lifts the dark side of an edge,
   and the halo-free local Laplacian can't do that. Clarity became midtone-weighted detail around a
   frame-relative Gaussian base. Texture is a 16 px guided filter on Y, Cb and Cr. Fitted as a
   Highlights/Shadows tone table it also lost (Shadows +100 1.12–1.31 against 0.66), so it stays
   only as a tested filter.
3. **Dark channel + guided filter** (`iop/hazeremoval.c`, `common/guided_filter.c`) for Dehaze.
   **Not tried yet:** `local_contrast.dehaze` reproduces the chart as a monotone local tone table
   with a linear-light color gain (0.33–0.83, but +100 2.22). The dark channel is the next thing to try for
   the +100 gap.
4. **Guided-filter mask** (`toneequal`) for Highlights/Shadows. **Tried:** a guided-filter base
   (radius 48–192) fitted worse than a Gaussian one (Shadows +100 0.61–0.66 against 0.54), so
   `highlights_shadows` uses a table over the pixel and a frame-relative Gaussian base.
5. **The spektrafilm color pipeline** as an optional physically based look family next to the
   Lightroom-derived ones.

Each would come in as its own TDD task, fitted to the spatial exports (`out/spatial-exports`,
`out/spatial-scale-exports`) where Lightroom is the target, with darktable credited in the file
header.
