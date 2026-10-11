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

`--resize N` downscales the flood-filled output to an N×N square before saving -- N must be a
power of 2 (16, 32, 64, 128, ...) or the tool exits with an error; any other value is rejected rather
than silently rounded. Optional -- omitting it keeps the source resolution. The source file itself is
never modified, only the generated output copy. Minecraft item icons need a square, power-of-two
texture to render/mip cleanly through `item/generated`; author source art at a larger size for clean
edges, then use `--resize` to downscale to the real in-game resolution (16 to match vanilla, higher
for an intentionally higher-res resource-pack look).

RGB and alpha are resized with **different filters** (`resize_preserving_alpha`) -- RGB with LANCZOS
for quality, alpha with BILINEAR specifically to avoid ringing. LANCZOS has negative side-lobes and
can overshoot/undershoot near a sharp transparent-to-opaque edge, which showed up as a real bug:
a 16x16 downscale produced a row at alpha=0 sandwiched between rows trending toward opaque on both
sides -- a single smooth source edge became a fake double-edge, and `--black-border` rendered it as
two black bars with a transparent gap between them. BILINEAR is a convex combination of its inputs
and can't produce a value outside the local source min/max, so it can't ring. Found and fixed
2026-10-11 from a real user-reported screenshot at `--resize 16 --black-border`.

`--solidify-black` snaps any dark pixel that's partially (not fully) transparent to solid opaque
black. Anti-aliasing -- either already in the source art, or introduced by `--resize`'s LANCZOS
downscaling -- commonly leaves black line edges as a faded, semi-transparent dark gray instead of a
crisp line; this cleans that up. `--solidify-black-threshold` (default 64) controls how dark a pixel
must be (max R/G/B value) to count as "black-ish." Applied after any resize.

`--snap-alpha` is the color-agnostic generalization of the same fix -- `--solidify-black` only
touches near-black pixels by design, so a faded edge on a *colored* shape's own silhouette (not
black line art) survives it untouched. `--snap-alpha` instead snaps every partially-transparent
pixel to fully opaque (color kept as-is) or fully transparent, based on whether its alpha is at/above
`--snap-alpha-threshold` (default 128). Use this whenever any fringing remains after
`--solidify-black` alone -- confirmed via a real test image (a blueprint icon with a blue silhouette
fill) where antialiasing left faded *blue* edge pixels that the black-only check correctly skipped.

`--black-border` takes a different stance on the same antialiased-edge-pixel set: instead of keeping
each pixel's blended color (what `--snap-alpha` does), it forces every partially-transparent pixel
that's actually adjacent to real transparency (alpha==0, checked via its 8-connected neighbors) to
opaque black outright, regardless of its own color -- giving the icon a defined black outline rather
than a color-accurate hard edge. Applied first, before `--solidify-black`/`--snap-alpha`.

Deliberately **not** a blanket "any pixel with 0 < alpha < 255" check (an earlier version was, and it
was wrong) -- LANCZOS resampling (`--resize`) has negative side-lobes/ringing that can leave pixels
at alpha~250-252 several pixels away from any real edge, sandwiched between fully-opaque neighbors.
Confirmed live on a real test image: a 16x16 downscale had two whole interior rows at alpha~251 with
255-ish neighbors on both sides -- not a border, a resampling artifact -- and the blanket check
painted them as fake extra lines partway into the icon. The neighbor-adjacency check fixes this by
only touching pixels that genuinely border real transparency.

`--overlay PATH` composites a second PNG centered on top of the base, after every other processing
step (flood-fill, resize, black-border, solidify-black, snap-alpha) -- so none of those effects ever
touch the overlay's own pixels. The overlay is NOT flood-filled itself; pass an already-transparent
source (a vanilla Minecraft item texture already has real alpha, which is the expected use case --
e.g. stamping `assets/minecraft/textures/item/wooden_sword.png` onto a custom base icon). It's resized
with NEAREST rather than LANCZOS so pixel-art sources stay crisp instead of blurring.
`--overlay-scale` (default 1.0) controls what fraction of the base canvas's size the overlay's
longest side should occupy once centered -- 1.0 means same size as the base (exact overlap for two
16x16 sources), smaller values shrink the overlay into a badge/corner-icon look.

`--item-definition` also writes `assets/<namespace>/items/<item_id>.json` -- the top-level
item-model-definition file Minecraft's newer item-model system actually needs to bind a real
registered item id to the generated model (a plain `{"model": {"type": "minecraft:model", "model":
"<namespace>:item/<item_id>"}}` wrapper). Without this file a generated model is unreachable in-game
even if the texture/model themselves are fine -- confirmed the hard way, this exact shape was
hand-authored once already for Lyfe's Research Notes before being centralized here. **Off by
default** -- deliberately opt-in, since a model meant to be one case inside a hand-authored
`minecraft:select` model (e.g. Research Notes' 62 per-target icons, which all share ONE real item id
switched by a data component) must NOT each get their own top-level definition file; only pass this
flag for a plain one-model-per-item-id case.

`--resolutions 16,32,64,128` (any comma-separated power-of-2 list) generates every size in one pass,
each written as the **correct kind of artifact for what it is** -- this is the actual mechanism behind
"ship higher-res icons for players with an HD texture pack," not a bundled alternate-resolution asset
inside the mod itself:
- The **smallest** size is written exactly like `--resize` always has -- a texture + model (+
  `--item-definition` if passed) under `assets/<namespace>/...`, meant to be copied into the mod's
  own `src/main/resources`. This is what every player sees by default, with no resource pack applied.
- Every **larger** size is written into its own `resourcepack_<N>x/` folder -- a complete, real,
  independently-distributable Minecraft resource pack (gets an auto-generated `pack.mcmeta`,
  `--pack-format` default 84) containing **only the texture PNG**, nothing else. A resource pack only
  needs to supply the file paths it wants to override; the model/item-definition JSON stay inherited
  from whatever the mod itself ships, so they're deliberately not duplicated into the pack folder.
  This is the exact same mechanism any "Faithful"-style HD pack already uses to override vanilla item
  textures, applied identically to a modded one -- nothing mod-side needs to change for it to work,
  since Minecraft resolves texture overrides purely by matching resource path, regardless of
  resolution (its atlas stitching is per-sprite, not a fixed grid, so mixed resolutions across
  different items/packs is never a problem).
- `resourcepack_<N>x/` is a complete, valid pack on its own once zipped -- distribute it as an
  optional, separate download/toggle (same as any other HD texture pack a player opts into), never
  merged into the mod jar or auto-applied. Mutually exclusive with `--resize` (pick one).
- Running this for every item in a batch (e.g. Lyfe's 62 Research Note icons) naturally produces one
  shared `resourcepack_64x/` (etc.) folder per resolution tier across the whole batch, each call just
  adding its own texture + idempotently rewriting that folder's `pack.mcmeta` -- call it once per item
  into the same `--output-dir` and the per-resolution pack folders accumulate correctly.

## Status

Built 2026-10-11. Flood-fill, resize, edge-cleanup (`--solidify-black`/`--snap-alpha`/
`--black-border`), `--overlay`, `--item-definition`, and `--resolutions` (multi-tier + standalone HD
resource pack generation) all implemented and tested against real source art. First real-mod use:
Lyfe's 62 Research Note icons (see Lyfe's own `context/classes/CraftingOverhaul.md`), generated via
direct CLI calls -- that use case predates `--item-definition`/`--resolutions` and used a
hand-authored `minecraft:select` model at a single resolution instead, so neither flag has been
exercised on a real mod's assets yet, only on test images.
