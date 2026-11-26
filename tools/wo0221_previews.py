#!/usr/bin/env python3
"""Phase 2.2.1 animation previews (WO-04): GIF + contact sheet per state.

Reads the shipped service atlases (384 px cells) and writes, into
docs/screenshots/wo0221/previews/:

  <state>.gif          looping preview at the state's manifest FPS
  <state>-frames.png   every frame on one sheet with its index and floor line

The GIF is upscaled to the in-game display height (max 300 CSS px) so what the
reviewer sees matches what the canvas draws.

    python3 tools/wo0221_previews.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SPRITES = ROOT / "static/public/assets/sprites"
OUT = ROOT / "docs/screenshots/wo0221/previews"
BG = (7, 7, 15)
DISPLAY_HEIGHT = 300


def slices(image: Image.Image, columns: int, rows: int):
    cw, ch = image.width // columns, image.height // rows
    for index in range(columns * rows):
        r, c = divmod(index, columns)
        yield image.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((SPRITES / "manifest.json").read_text())
    written = []
    for state, entry in manifest["states"].items():
        source = entry["source"]
        atlas = Image.open(SPRITES / source["file"]).convert("RGBA")
        frames = list(slices(atlas, source["columns"], source["rows"]))[: entry["frames"]]
        scale = DISPLAY_HEIGHT / (source["height"] // source["rows"])
        shown = [f.resize((max(1, round(f.width * scale)), DISPLAY_HEIGHT), Image.LANCZOS) for f in frames]

        gif_frames = []
        for frame in shown:
            tile = Image.new("RGBA", (frame.width, frame.height), BG + (255,))
            tile.alpha_composite(frame)
            gif_frames.append(tile.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=255))
        delay = max(20, round(1000 / entry["fps"]))
        gif_path = OUT / f"{state}.gif"
        gif_frames[0].save(gif_path, save_all=True, append_images=gif_frames[1:],
                           duration=delay, loop=0, optimize=True, disposal=2)

        cell = source["width"] // source["columns"]
        pad = 8
        cols = 4
        rows = (len(frames) + cols - 1) // cols
        thumb = 192
        sheet = Image.new("RGBA", (cols * (thumb + pad) + pad, rows * (thumb + pad) + pad + 16), BG + (255,))
        draw = ImageDraw.Draw(sheet)
        draw.text((pad, 3), f"{state}  {len(frames)}F  {entry['fps']} fps  loop={entry['loop']}  "
                            f"service cell {cell}px", fill=(220, 220, 240))
        for index, frame in enumerate(frames):
            r, c = divmod(index, cols)
            small = frame.resize((thumb, thumb), Image.LANCZOS)
            x = pad + c * (thumb + pad)
            y = 16 + pad + r * (thumb + pad)
            sheet.alpha_composite(small, (x, y))
            draw.rectangle([x, y, x + thumb - 1, y + thumb - 1], outline=(70, 70, 95))
            draw.text((x + 4, y + 2), f"f{index}", fill=(255, 220, 80))
        sheet_path = OUT / f"{state}-frames.png"
        sheet.convert("RGB").save(sheet_path)
        written.append({"state": state, "gif": str(gif_path.relative_to(ROOT)),
                        "sheet": str(sheet_path.relative_to(ROOT)),
                        "gif_bytes": gif_path.stat().st_size,
                        "delay_ms": delay, "frames": len(frames)})
        print(f"{state:8s} {len(frames):2d}F {entry['fps']:2d}fps  gif {gif_path.stat().st_size / 1024:6.0f} KiB  "
              f"{gif_path.relative_to(ROOT)}  {sheet_path.relative_to(ROOT)}")
    (OUT / "previews.json").write_text(json.dumps(written, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
