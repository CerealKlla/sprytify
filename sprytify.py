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


def power_of_two(value: str) -> int:
    n = int(value)
    if n <= 0 or (n & (n - 1)) != 0:
        raise argparse.ArgumentTypeError(f"{value!r} is not a power of 2 (e.g. 16, 32, 64, 128)")
    return n


def write_model_json(path: Path, namespace: str, item_id: str) -> None:
    model = {
        "parent": "minecraft:item/generated",
        "textures": {"layer0": f"{namespace}:item/{item_id}"},
    }
    path.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Source PNG with a white/near-white background")
    parser.add_argument("item_id", help="Item id, e.g. 'recallcinite_stone' (no namespace)")
    parser.add_argument("--namespace", default="minecraft", help="Mod namespace, e.g. 'myssions'")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="Root output directory")
    parser.add_argument("--threshold", type=int, default=240, help="Min R/G/B value (0-255) to count as background")
    parser.add_argument("--feather", type=int, default=0, help="Soft-alpha falloff band width near the threshold, for anti-aliased edges (0 = hard cutoff)")
    parser.add_argument("--resize", type=power_of_two, default=None, help="Downscale the output to NxN pixels; N must be a power of 2 (e.g. 16, 32, 64, 128). Omit to keep the source resolution. The source file is never modified.")
    parser.add_argument("--solidify-black", action="store_true", help="Snap any dark, partially-transparent pixel (faded black edges from anti-aliasing) to fully opaque black, for crisper line art.")
    parser.add_argument("--solidify-black-threshold", type=int, default=64, help="Max R/G/B value (0-255) for a pixel to count as 'black-ish' for --solidify-black (default 64)")
    parser.add_argument("--snap-alpha", action="store_true", help="Snap every partially-transparent pixel to fully opaque or fully transparent, regardless of color -- removes all antialiased edge fringing (not just black), for a crisp hard-edged silhouette.")
    parser.add_argument("--snap-alpha-threshold", type=int, default=128, help="Alpha value (0-255) at/above which a partially-transparent pixel becomes opaque instead of transparent, for --snap-alpha (default 128)")
    parser.add_argument("--black-border", action="store_true", help="Force every partially-transparent (antialiased border) pixel to opaque black, regardless of its original color -- gives the icon a solid black outline. Runs before --solidify-black/--snap-alpha, so combining them is redundant (no partially-transparent pixels will be left for those to act on).")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 1

    img = Image.open(args.input)
    result = flood_fill_background(img, args.threshold, args.feather)

    if args.resize is not None:
        result = resize_preserving_alpha(result, args.resize)

    if args.black_border:
        result = border_to_black(result)

    if args.solidify_black:
        result = solidify_black(result, args.solidify_black_threshold)

    if args.snap_alpha:
        result = snap_alpha(result, args.snap_alpha_threshold)

    textures_dir = args.output_dir / "assets" / args.namespace / "textures" / "item"
    models_dir = args.output_dir / "assets" / args.namespace / "models" / "item"
    textures_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)

    texture_path = textures_dir / f"{args.item_id}.png"
    model_path = models_dir / f"{args.item_id}.json"

    result.save(texture_path)
    write_model_json(model_path, args.namespace, args.item_id)

    print(f"wrote texture: {texture_path}")
    print(f"wrote model:   {model_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
