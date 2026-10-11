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
    args = parser.parse_args()

    if not args.input.exists():
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 1

    img = Image.open(args.input)
    result = flood_fill_background(img, args.threshold, args.feather)

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
