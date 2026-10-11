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

## Status

Built 2026-10-11. Core flood-fill + model-JSON generation implemented; not yet used on a real asset
in any mod.
