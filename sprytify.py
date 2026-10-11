#!/usr/bin/env python3
"""Sprytify: turn a flat PNG into a Minecraft item texture + model.

Flood-fills white/near-white pixels connected to the image border (treating
them as background), clears them to transparent, and writes out a texture
PNG alongside a model JSON that uses minecraft:item/generated as its parent
-- Minecraft's own built-in model auto-extrudes a flat alpha-masked texture
into a paper-thin 3D shape, so no custom mesh needs generating here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

from PIL import Image


def is_background(pixel: tuple[int, int, int, int], threshold: int) -> bool:
    r, g, b, a = pixel
    if a == 0:
        return True
    return r >= threshold and g >= threshold and b >= threshold


def flood_fill_background(img: Image.Image, threshold: int, feather: int) -> Image.Image:
    img = img.convert("RGBA")
    width, height = img.size
    pixels = img.load()

    background = [[False] * width for _ in range(height)]
    queue: deque[tuple[int, int]] = deque()

    for x in range(width):
        for y in (0, height - 1):
            if is_background(pixels[x, y], threshold) and not background[y][x]:
                background[y][x] = True
                queue.append((x, y))
    for y in range(height):
        for x in (0, width - 1):
            if is_background(pixels[x, y], threshold) and not background[y][x]:
                background[y][x] = True
                queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < width and 0 <= ny < height and not background[ny][nx]:
                if is_background(pixels[nx, ny], threshold):
                    background[ny][nx] = True
                    queue.append((nx, ny))

    out = img.copy()
    out_pixels = out.load()
    for y in range(height):
        for x in range(width):
            if background[y][x]:
                r, g, b, _ = pixels[x, y]
                out_pixels[x, y] = (r, g, b, 0)
            elif feather > 0:
                r, g, b, a = pixels[x, y]
                brightness = min(r, g, b)
                if brightness >= threshold - feather:
                    fade = (threshold - brightness) / feather
                    fade = max(0.0, min(1.0, fade))
                    out_pixels[x, y] = (r, g, b, int(a * fade))

    return out


def solidify_black(img: Image.Image, rgb_threshold: int) -> Image.Image:
    """Snap any dark, partially-transparent pixel to fully opaque black.

    Anti-aliasing (whether in the source art or introduced by --resize's
    downscaling) commonly leaves black line edges as a faded, semi-transparent
    dark gray instead of a crisp line. This finds any pixel whose color is at
    or below the threshold (black-ish) and whose alpha is >0 but <255, and
    forces it to solid (0, 0, 0, 255).
    """
    out = img.convert("RGBA").copy()
    pixels = out.load()
    width, height = out.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if 0 < a < 255 and max(r, g, b) <= rgb_threshold:
                pixels[x, y] = (0, 0, 0, 255)
    return out


def snap_alpha(img: Image.Image, alpha_threshold: int) -> Image.Image:
    """Snap every partially-transparent pixel to fully opaque or fully transparent.

    Unlike solidify_black, this is color-agnostic: it fixes antialiased edge
    fringing on ANY shape (not just black line art), e.g. a faded colored edge
    left over from downscaling or from the source art's own antialiasing.
    Pixels with alpha >= threshold become fully opaque (color unchanged);
    pixels below it become fully transparent.
    """
    out = img.convert("RGBA").copy()
    pixels = out.load()
    width, height = out.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if 0 < a < 255:
                pixels[x, y] = (r, g, b, 255) if a >= alpha_threshold else (r, g, b, 0)
    return out


def border_to_black(img: Image.Image) -> Image.Image:
    """Force partially-transparent pixels that actually border transparency to opaque black.

    A naive "any pixel with 0 < alpha < 255" check is too broad: resampling
    filters like LANCZOS (used by --resize) have negative side-lobes/ringing,
    which can leave pixels alpha=250-252 even many pixels away from any real
    edge, with fully-opaque (alpha 254-255) neighbors on both sides -- not a
    border at all, just a resampling artifact. Confirmed this live: a 16x16
    downscale of a real test image had two whole interior rows at alpha~251
    sandwiched between alpha~255 rows, which a blanket check painted as fake
    extra black lines partway into the icon.

    This only recolors a partially-transparent pixel if at least one of its
    8-connected neighbors is *fully* transparent (alpha == 0) -- i.e. it's
    actually adjacent to real background/a real cutout, not just a resampling
    dip. Neighbor lookups read from the original (pre-recolor) pixel data so
    the result doesn't depend on scan order.
    """
    src = img.convert("RGBA")
    out = src.copy()
    src_pixels = src.load()
    out_pixels = out.load()
    width, height = out.size
    for y in range(height):
        for x in range(width):
            _, _, _, a = src_pixels[x, y]
            if not (0 < a < 255):
                continue
            borders_transparent = False
            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    if (nx, ny) != (x, y) and src_pixels[nx, ny][3] == 0:
                        borders_transparent = True
                        break
                if borders_transparent:
                    break
            if borders_transparent:
                out_pixels[x, y] = (0, 0, 0, 255)
    return out


def resize_preserving_alpha(img: Image.Image, size: int) -> Image.Image:
    """Downscale an RGBA image without letting the alpha channel ring.

    LANCZOS (used for its sharp, high-quality color downscaling) has negative
    side-lobes: near a sharp transparent-to-opaque transition it can overshoot
    past fully opaque AND undershoot back past fully transparent, producing a
    spurious dip to alpha=0 a row or two past the real edge. Confirmed live on
    a real test image: a 16x16 downscale had row 2's alpha at 0 despite rows 1
    (alpha~1) and 3 (alpha~198) on either side both trending toward opaque --
    one smooth transition became a fake double-edge. Combined with
    --black-border (which paints any pixel bordering true alpha=0), that fake
    dip was rendered as two separate black bars with a transparent gap between
    them, instead of the source's single line.

    Fix: resize color and alpha separately. RGB keeps LANCZOS for quality;
    alpha uses BILINEAR, which is a convex combination of its inputs and so
    can never overshoot/undershoot past the real source min/max -- no ringing.
    """
    img = img.convert("RGBA")
    rgb = img.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
    alpha = img.getchannel("A").resize((size, size), Image.Resampling.BILINEAR)
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def overlay_centered(base: Image.Image, overlay: Image.Image, scale: float) -> Image.Image:
    """Alpha-composite `overlay` centered on top of `base`.

    `overlay` is resized (preserving its own aspect ratio, NEAREST so a pixel
    -art source like a vanilla item texture doesn't get blurred) so its
    longest side is `scale` fraction of base's matching dimension, then
    centered and composited with full alpha blending -- transparent overlay
    pixels let the base show through untouched.
    """
    base = base.convert("RGBA")
    overlay = overlay.convert("RGBA")
    base_w, base_h = base.size
    ov_w, ov_h = overlay.size

    target_long_side = max(base_w, base_h) * scale
    ov_scale = target_long_side / max(ov_w, ov_h)
    new_w = max(1, round(ov_w * ov_scale))
    new_h = max(1, round(ov_h * ov_scale))
    if (new_w, new_h) != (ov_w, ov_h):
        overlay = overlay.resize((new_w, new_h), Image.Resampling.NEAREST)

    out = base.copy()
    paste_x = (base_w - new_w) // 2
    paste_y = (base_h - new_h) // 2
    out.alpha_composite(overlay, (paste_x, paste_y))
    return out


def power_of_two(value: str) -> int:
    n = int(value)
    if n <= 0 or (n & (n - 1)) != 0:
        raise argparse.ArgumentTypeError(f"{value!r} is not a power of 2 (e.g. 16, 32, 64, 128)")
    return n


def power_of_two_list(value: str) -> list[int]:
    """Parses a comma-separated list of power-of-two sizes, e.g. '16,32,64,128'."""
    sizes = [power_of_two(part.strip()) for part in value.split(",") if part.strip()]
    if not sizes:
        raise argparse.ArgumentTypeError("--resolutions needs at least one size")
    return sorted(set(sizes))


def write_pack_mcmeta(path: Path, pack_format: int, description: str) -> None:
    meta = {"pack": {"pack_format": pack_format, "description": description}}
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def write_model_json(path: Path, namespace: str, item_id: str) -> None:
    model = {
        "parent": "minecraft:item/generated",
        "textures": {"layer0": f"{namespace}:item/{item_id}"},
    }
    path.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")


def write_item_definition_json(path: Path, namespace: str, item_id: str) -> None:
    """The top-level `assets/<namespace>/items/<item_id>.json` Minecraft's item-model-definition
    system requires (separate from the model file itself, which lives under `models/item/`) --
    the file that actually binds a real registered item id to its model. A model with no matching
    item-definition file is effectively unreachable in-game; centralizing this here (gated behind
    --item-definition, since not every output is meant to map 1:1 onto a single real item id -- e.g.
    a select-model's many per-case sub-icons shouldn't each get their own top-level definition) means
    this exact small, easy-to-typo JSON shape never needs hand-authoring again.
    """
    definition = {
        "model": {
            "type": "minecraft:model",
            "model": f"{namespace}:item/{item_id}",
        }
    }
    path.write_text(json.dumps(definition, indent=2) + "\n", encoding="utf-8")


def process(base: Image.Image, args, size: int | None) -> Image.Image:
    """Runs the full edge-cleanup/resize/overlay pipeline once, at a given target `size` (or
    `None` to keep the source resolution) -- shared by both the single-resolution and
    --resolutions (multi-resolution) code paths so there is exactly one place this logic lives.
    """
    result = base
    if size is not None:
        result = resize_preserving_alpha(result, size)
    if args.black_border:
        result = border_to_black(result)
    if args.solidify_black:
        result = solidify_black(result, args.solidify_black_threshold)
    if args.snap_alpha:
        result = snap_alpha(result, args.snap_alpha_threshold)
    if args.overlay is not None:
        overlay_img = Image.open(args.overlay)
        result = overlay_centered(result, overlay_img, args.overlay_scale)
    return result


def write_base_output(result: Image.Image, output_dir: Path, namespace: str, item_id: str, item_definition: bool) -> None:
    """Writes texture + model (+ optional item-definition) straight into the mod-resources shape
    -- the lowest/default resolution's own output, meant to be copied into a mod's
    src/main/resources directly (unchanged from Sprytify's original single-resolution behavior).
    """
    textures_dir = output_dir / "assets" / namespace / "textures" / "item"
    models_dir = output_dir / "assets" / namespace / "models" / "item"
    textures_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)

    texture_path = textures_dir / f"{item_id}.png"
    model_path = models_dir / f"{item_id}.json"
    result.save(texture_path)
    write_model_json(model_path, namespace, item_id)
    print(f"wrote texture: {texture_path}")
    print(f"wrote model:   {model_path}")

    if item_definition:
        items_dir = output_dir / "assets" / namespace / "items"
        items_dir.mkdir(parents=True, exist_ok=True)
        item_definition_path = items_dir / f"{item_id}.json"
        write_item_definition_json(item_definition_path, namespace, item_id)
        print(f"wrote item definition: {item_definition_path}")


def write_resource_pack_override(
    result: Image.Image, output_dir: Path, namespace: str, item_id: str, size: int, pack_format: int
) -> None:
    """Writes ONLY a texture PNG into a standalone `resourcepack_<N>x/` folder -- a real,
    independently-distributable Minecraft resource pack (gets its own auto-generated
    `pack.mcmeta`) that overrides just this one texture path. A resource pack only needs to
    supply the files it wants to change; the model/item-definition JSON stay inherited from
    whatever the mod itself ships (the base/default resolution's output), so neither is
    duplicated here -- this is the same mechanism any "HD texture pack" uses for vanilla items,
    applied to a modded one. `pack.mcmeta` is rewritten (idempotently) on every call so repeated
    invocations across many items for the same pack folder all converge correctly.
    """
    pack_dir = output_dir / f"resourcepack_{size}x"
    textures_dir = pack_dir / "assets" / namespace / "textures" / "item"
    textures_dir.mkdir(parents=True, exist_ok=True)

    texture_path = textures_dir / f"{item_id}.png"
    result.save(texture_path)
    write_pack_mcmeta(pack_dir / "pack.mcmeta", pack_format, f"{namespace} {size}x{size} icon overrides")
    print(f"wrote {size}x{size} override: {texture_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Source PNG with a white/near-white background")
    parser.add_argument("item_id", help="Item id, e.g. 'recallcinite_stone' (no namespace)")
    parser.add_argument("--namespace", default="minecraft", help="Mod namespace, e.g. 'myssions'")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="Root output directory")
    parser.add_argument("--threshold", type=int, default=240, help="Min R/G/B value (0-255) to count as background")
    parser.add_argument("--feather", type=int, default=0, help="Soft-alpha falloff band width near the threshold, for anti-aliased edges (0 = hard cutoff)")
    parser.add_argument("--resize", type=power_of_two, default=None, help="Downscale the output to NxN pixels; N must be a power of 2 (e.g. 16, 32, 64, 128). Omit to keep the source resolution. The source file is never modified. Mutually exclusive with --resolutions.")
    parser.add_argument("--resolutions", type=power_of_two_list, default=None, help="Comma-separated list of power-of-2 sizes to generate all at once, e.g. '16,32,64,128'. The SMALLEST is treated as the mod's own default/base output (texture + model, same shape as --resize); every larger size is written as its own standalone resource-pack folder (resourcepack_<N>x/, with an auto-generated pack.mcmeta) that overrides just that texture -- the correct, standard way to offer higher-resolution art without forcing it on every player. Mutually exclusive with --resize.")
    parser.add_argument("--pack-format", type=int, default=84, help="pack_format value written into each generated resourcepack_<N>x/pack.mcmeta (default 84, this environment's current resource pack format -- see net.minecraft.SharedConstants.RESOURCE_PACK_FORMAT_MAJOR). Bump this via the flag, not code, whenever the target Minecraft version's format changes.")
    parser.add_argument("--solidify-black", action="store_true", help="Snap any dark, partially-transparent pixel (faded black edges from anti-aliasing) to fully opaque black, for crisper line art.")
    parser.add_argument("--solidify-black-threshold", type=int, default=64, help="Max R/G/B value (0-255) for a pixel to count as 'black-ish' for --solidify-black (default 64)")
    parser.add_argument("--snap-alpha", action="store_true", help="Snap every partially-transparent pixel to fully opaque or fully transparent, regardless of color -- removes all antialiased edge fringing (not just black), for a crisp hard-edged silhouette.")
    parser.add_argument("--snap-alpha-threshold", type=int, default=128, help="Alpha value (0-255) at/above which a partially-transparent pixel becomes opaque instead of transparent, for --snap-alpha (default 128)")
    parser.add_argument("--black-border", action="store_true", help="Force every partially-transparent (antialiased border) pixel to opaque black, regardless of its original color -- gives the icon a solid black outline. Runs before --solidify-black/--snap-alpha, so combining them is redundant (no partially-transparent pixels will be left for those to act on).")
    parser.add_argument("--overlay", type=Path, default=None, help="Optional second PNG to composite centered on top of the base, after all other processing. Its own background is NOT flood-filled -- pass an already-transparent source (e.g. a vanilla Minecraft item texture).")
    parser.add_argument("--overlay-scale", type=float, default=1.0, help="Fraction (0-1+) of the base's size the overlay's longest side should occupy once centered (default 1.0 = same size as the base canvas)")
    parser.add_argument("--item-definition", action="store_true", help="Also write the top-level assets/<namespace>/items/<item_id>.json Minecraft's item-model-definition system requires to actually bind a real item id to the generated model (a plain 'minecraft:model' wrapper pointing at it). Optional -- off by default, since a one-off model meant to be referenced from a hand-authored select-model (multiple icons sharing one real item id) shouldn't each get their own top-level definition file.")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 1
    if args.resize is not None and args.resolutions is not None:
        print("error: --resize and --resolutions are mutually exclusive", file=sys.stderr)
        return 1
    if args.overlay is not None and not args.overlay.exists():
        print(f"error: overlay file not found: {args.overlay}", file=sys.stderr)
        return 1

    img = Image.open(args.input)
    base = flood_fill_background(img, args.threshold, args.feather)

    if args.resolutions is not None:
        base_size, *pack_sizes = args.resolutions
        write_base_output(
            process(base, args, base_size), args.output_dir, args.namespace, args.item_id, args.item_definition
        )
        for size in pack_sizes:
            write_resource_pack_override(
                process(base, args, size), args.output_dir, args.namespace, args.item_id, size, args.pack_format
            )
        return 0

    write_base_output(
        process(base, args, args.resize), args.output_dir, args.namespace, args.item_id, args.item_definition
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
