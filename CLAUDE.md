# Sprytify

Standalone Python CLI tool, not a Minecraft mod. Turns a flat PNG (drawn on a white/near-white
background) into a Minecraft item texture (background flood-filled to transparent) plus a model
JSON. Companion to the other standalone tools in this suite (BluepryntImager, Dyspatch) -- same
"own private context repo, separate from the mod suite's Gradle projects" convention, but this one
has no Gradle/NeoForge scaffolding at all since it doesn't run inside Minecraft.

## Why there's no custom 3D mesh generation here

Minecraft's built-in `minecraft:item/generated` model parent already auto-extrudes a flat
alpha-masked texture into a paper-thin 3D shape (front/back faces plus 1-pixel-thick side walls
traced around the silhouette), purely from the texture's own alpha channel. So the only real work
is producing a clean texture (background removed) and a trivial model JSON that points at it with
that parent -- there is no custom mesh/OBJ output, and none is needed. See
`context/decisions.md` for the decision record.

## Usage

```
python sprytify.py <input.png> <item_id> --namespace <mod_namespace> --output-dir <dir>
```

Writes `assets/<namespace>/textures/item/<item_id>.png` and
`assets/<namespace>/models/item/<item_id>.json` under `--output-dir`, in the same path shape a mod's
own `src/main/resources/` expects -- the output tree can be copied straight into a mod project.

Flood-fill starts from the four image edges and spreads inward through any background-colored
(white/near-white, configurable via `--threshold`) pixel run, so interior white (e.g. a highlight or
an eye) that never touches the border survives untouched. `--feather` adds a soft-alpha falloff band
near the threshold for anti-aliased edges instead of a hard cutoff; default is 0 (hard cutoff).

`--resize N` downscales the flood-filled output to an N×N square (LANCZOS) before saving -- N
must be a power of 2 (16, 32, 64, 128, ...) or the tool exits with an error; any other value is
rejected rather than silently rounded. Optional -- omitting it keeps the source resolution. The
source file itself is never modified, only the generated output copy. Minecraft item icons need a
square, power-of-two texture to render/mip cleanly through `item/generated`; author source art at a
larger size for clean edges, then use `--resize` to downscale to the real in-game resolution (16 to
match vanilla, higher for an intentionally higher-res resource-pack look).

`--solidify-black` snaps any dark pixel that's partially (not fully) transparent to solid opaque
black. Anti-aliasing -- either already in the source art, or introduced by `--resize`'s LANCZOS
downscaling -- commonly leaves black line edges as a faded, semi-transparent dark gray instead of a
crisp line; this cleans that up. `--solidify-black-threshold` (default 64) controls how dark a pixel
must be (max R/G/B value) to count as "black-ish." Applied after any resize.

`--snap-alpha` is the color-agnostic generalization of the same fix -- `--solidify-black` only
touches near-black pixels by design, so a faded edge on a *colored* shape's own silhouette (not
black line art) survives it untouched. `--snap-alpha` instead snaps every partially-transparent
pixel to fully opaque (color kept as-is) or fully transparent, based on whether its alpha is at/above
`--snap-alpha-threshold` (default 128). Applied last, after `--solidify-black`, so any pixel that
flag already resolved to solid black is left alone. Use this whenever any fringing remains after
`--solidify-black` alone -- confirmed via a real test image (a blueprint icon with a blue silhouette
fill) where antialiasing left faded *blue* edge pixels that the black-only check correctly skipped.

## Status

Built 2026-10-11. Core flood-fill + model-JSON generation implemented; not yet used on a real asset
in any mod.
