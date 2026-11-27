#!/usr/bin/env python3
"""Phase 2.2.1 asset audit (WO-03): verify the sprite atlases with Pillow.

Checks, for every state in ``static/public/assets/sprites/manifest.json``:

  * frame count, atlas size and cell size match the manifest and the file
  * every cell actually contains visible alpha pixels
  * no two frames are identical (duplicate detection by cell hash)
  * nothing is clipped by, or touches, the cell border
  * sole (contact) line, character height spread and horizontal ground point
  * the declared anchor matches where the art is actually standing
  * Attack index 6 is a strike pose (the hammer reaches forward at target
    height and the swing continues afterwards)

Every number in the JSON report is measured from the pixels; nothing is
hard-coded per state except the expectations the manifest already declares.

    python3 tools/wo0221_asset_audit.py                     # audit service atlases
    python3 tools/wo0221_asset_audit.py --authoring         # audit 768 masters
    python3 tools/wo0221_asset_audit.py --json out.json --strict
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SPRITES = ROOT / "static/public/assets/sprites"
MANIFEST = SPRITES / "manifest.json"
BUDGET = 1024 * 1024
EDGE_MARGIN = 6          # px of clear space we want inside every cell
ALPHA_FLOOR = 16


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def cells_of(image: Image.Image, columns: int, rows: int):
    width, height = image.size
    cw, ch = width // columns, height // rows
    for index in range(columns * rows):
        r, c = divmod(index, columns)
        yield index, np.array(image.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch)))


def purple_mask(rgba: np.ndarray) -> np.ndarray:
    a = rgba.astype(np.int16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    opaque = a[:, :, 3] > 128
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    return opaque & (b > r) & (r > g) & (b - g > 45) & (sat > 0.35) & (mx > 70)


def largest_blob(mask: np.ndarray, min_area: int = 500):
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    best = None
    for y0 in range(h):
        for x0 in np.nonzero(mask[y0] & ~seen[y0])[0]:
            if seen[y0, x0]:
                continue
            queue = deque([(y0, x0)])
            seen[y0, x0] = True
            pts = []
            while queue:
                y, x = queue.popleft()
                pts.append((y, x))
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        queue.append((ny, nx))
            if len(pts) >= min_area and (best is None or len(pts) > len(best)):
                best = pts
    if not best:
        return None
    ys = np.array([p[0] for p in best])
    xs = np.array([p[1] for p in best])
    return {"area": len(best), "x0": int(xs.min()), "x1": int(xs.max()),
            "y0": int(ys.min()), "y1": int(ys.max()), "cx": float(xs.mean()), "cy": float(ys.mean())}


def iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter / union) if union else 1.0


# --------------------------------------------------------------------------- #
# per-state audit
# --------------------------------------------------------------------------- #
def audit_state(state: str, entry: dict, raw_entry: dict, service_cell: int,
                enforce_budget: bool = True) -> dict:
    problems: list[str] = []
    warnings: list[str] = []
    source = entry["source"]
    file = SPRITES / source["file"]
    result = {"state": state, "file": source["file"], "problems": problems, "warnings": warnings}

    if not file.is_file():
        problems.append(f"{source['file']} is missing")
        return result
    result["bytes"] = file.stat().st_size
    result["budget_applies"] = enforce_budget
    if enforce_budget and result["bytes"] > BUDGET:
        problems.append(f"service atlas is {result['bytes']} B, over the 1 MiB budget")

    image = Image.open(file)
    result["mode"] = image.mode
    result["size"] = list(image.size)
    if image.mode not in ("RGBA", "P"):
        problems.append(f"unexpected mode {image.mode}")
    image = image.convert("RGBA")

    columns, rows = source["columns"], source["rows"]
    cw, ch = image.size[0] // columns, image.size[1] // rows
    result["cell"] = [cw, ch]
    if cw != service_cell or ch != service_cell:
        problems.append(f"cell {cw}x{ch} does not match manifest cell {service_cell}")
    if source.get("width") and source["width"] != image.size[0]:
        problems.append(f"declared width {source['width']} != file width {image.size[0]}")
    if source.get("height") and source["height"] != image.size[1]:
        problems.append(f"declared height {source['height']} != file height {image.size[1]}")
    if columns * rows < entry["frames"]:
        problems.append(f"atlas holds {columns * rows} cells for {entry['frames']} frames")

    frames = []
    masks = []
    hashes = {}
    for index, cell in cells_of(image, columns, rows):
        if index >= entry["frames"]:
            break
        alpha = cell[:, :, 3]
        mask = alpha > ALPHA_FLOOR
        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            problems.append(f"frame {index} has no visible pixels")
            continue
        coverage = float(len(xs)) / (cw * ch)
        if coverage < 0.01:
            problems.append(f"frame {index} covers only {coverage:.3%} of the cell")
        margins = {"left": int(xs.min()), "top": int(ys.min()),
                   "right": cw - 1 - int(xs.max()), "bottom": ch - 1 - int(ys.max())}
        if min(margins.values()) < 0:
            problems.append(f"frame {index} is clipped by the cell border {margins}")
        # The bottom edge is the floor line the art stands on, so only the other
        # three sides tell us whether a frame is about to be cut in half.
        side = min(margins["left"], margins["right"], margins["top"])
        if side < EDGE_MARGIN:
            warnings.append(f"frame {index} comes within {side} px of the cell border")
        digest = hashlib.sha256(np.ascontiguousarray(cell).tobytes()).hexdigest()
        if digest in hashes:
            problems.append(f"frame {index} duplicates frame {hashes[digest]}")
        hashes[digest] = index
        soles = xs[ys >= ys.max() - 2]
        frames.append({
            "frame": index,
            "bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
            "coverage": round(coverage, 4),
            "height": int(ys.max() - ys.min() + 1),
            "width": int(xs.max() - xs.min() + 1),
            "sole_y": int(ys.max()),
            "sole_x": round(float(np.median(soles)), 1),
            "margins": margins,
        })
        masks.append(mask)

    heights = [f["height"] for f in frames]
    soles = [f["sole_y"] for f in frames]
    sole_xs = [f["sole_x"] for f in frames]
    result["frames"] = frames
    result["frame_count"] = len(frames)
    result["height"] = {"min": min(heights), "max": max(heights),
                        "spread": max(heights) - min(heights),
                        "spread_pct": round(100 * (max(heights) - min(heights)) / max(heights), 1)}
    result["sole_y"] = {"min": min(soles), "max": max(soles), "spread": max(soles) - min(soles)}
    result["sole_x"] = {"mean": round(float(np.mean(sole_xs)), 1),
                        "min": min(sole_xs), "max": max(sole_xs)}
    result["loop_iou"] = round(iou(masks[-1], masks[0]), 3) if len(masks) > 1 else None
    if len(masks) > 1:
        pairs = [round(iou(masks[i], masks[(i + 1) % len(masks)]), 3) for i in range(len(masks))]
        result["step_iou"] = pairs
        result["step_iou_mean"] = round(float(np.mean(pairs)), 3)

    # --- anchor vs. where the art actually stands -------------------------- #
    anchor = entry.get("anchor") or [0.5, 1.0]
    anchor_y = anchor[1] * ch
    # Grounded frames define the contact line; airborne frames sit above it.
    counts = {}
    for value in soles:
        counts[value] = counts.get(value, 0) + 1
    contact = max(counts, key=lambda value: (counts[value], value))
    tolerance = max(2.0, ch * 0.01)
    if abs(contact - anchor_y) > tolerance:
        problems.append(f"contact line {contact} misses anchor y={anchor_y:.1f} by "
                        f"{abs(contact - anchor_y):.1f} px (tolerance {tolerance:.1f})")
    lifted = [f["frame"] for f in frames if contact - f["sole_y"] >= 10]
    result["anchor"] = {"declared": anchor, "contact_line": contact,
                        "contact_offset_px": round(contact - anchor_y, 1),
                        "lifted_frames": lifted,
                        "horizontal_offset": round(float(np.mean(sole_xs)) - anchor[0] * cw, 1)}
    if abs(result["anchor"]["horizontal_offset"]) > cw * 0.25:
        warnings.append(f"horizontal ground point is {result['anchor']['horizontal_offset']:.0f} px off the anchor")

    # --- Attack hit frame -------------------------------------------------- #
    if state == "attack":
        hits = raw_entry.get("hits") or []
        hit_frames = [h["frame"] if isinstance(h, dict) else h for h in hits]
        result["hit_frames"] = hit_frames
        if 6 in hit_frames:
            trajectory = []
            for index, cell in cells_of(image, columns, rows):
                if index >= entry["frames"]:
                    break
                hammer = largest_blob(purple_mask(cell), 500)
                trajectory.append({"frame": index,
                                   "hammer_cx": round(hammer["cx"], 1) if hammer else None,
                                   "hammer_cy": round(hammer["cy"], 1) if hammer else None,
                                   "hammer_x1": hammer["x1"] if hammer else None})
            result["attack_hammer"] = trajectory
            pose = trajectory[6]
            cys = [t["hammer_cy"] for t in trajectory if t["hammer_cy"] is not None]
            cxs = [t["hammer_cx"] for t in trajectory if t["hammer_cx"] is not None]
            checks = {
                "hammer_forward_of_body": pose["hammer_cx"] > cw * 0.5 if pose["hammer_cx"] else False,
                "hammer_at_target_height": min(cys) < pose["hammer_cy"] < max(cys) if pose["hammer_cy"] else False,
                "reach_ranks_top3": pose["hammer_cx"] >= sorted(cxs)[-3] if pose["hammer_cx"] else False,
                "swing_continues_after_hit": pose["hammer_cy"] is not None
                and trajectory[7]["hammer_cy"] is not None
                and trajectory[7]["hammer_cy"] > pose["hammer_cy"],
                "hit_frame_is_distinct": result.get("step_iou", [1.0] * 12)[6] < 0.9,
            }
            result["attack_hit_check"] = checks
            for name, ok in checks.items():
                if not ok:
                    problems.append(f"attack frame 6 fails {name}")
            if pose["hammer_cy"] is not None:
                result["attack_hit_frame_detail"] = {
                    "hammer_cx": pose["hammer_cx"], "hammer_cy": pose["hammer_cy"],
                    "cycle_cy_range": [min(cys), max(cys)], "cycle_cx_max": max(cxs),
                }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authoring", action="store_true",
                        help="audit the 768 px masters the manifest declares instead of the service files")
    parser.add_argument("--json", default="docs/screenshots/wo0221/asset-audit.json")
    parser.add_argument("--strict", action="store_true", help="exit non-zero on warnings too")
    args = parser.parse_args()

    raw = json.loads(MANIFEST.read_text())
    service_cell = raw["cell"][0]
    authoring_cell = raw["authoring"]["cell"][0]
    cell = authoring_cell if args.authoring else service_cell
    results = []
    problems = 0
    warnings = 0
    print(f"{'state':8s} {'frames':>6s} {'cell':>9s} {'bytes':>9s} {'height min-max (spread)':>24s} "
          f"{'sole y':>8s} {'loop IoU':>8s}")
    for state, entry in raw["states"].items():
        if args.authoring:
            master = entry["authoring"]
            entry = dict(entry)
            entry["source"] = {**entry["source"], "file": master["file"], "width": master["width"],
                               "height": master["height"]}
            entry["anchor"] = entry.get("anchor") or raw.get("anchor")
            # the authoring file may live outside the sprite directory
            path = ROOT / master["file"]
            if not path.is_file():
                path = SPRITES / Path(master["file"]).name
            row = audit_state(state, {**entry, "source": {**entry["source"], "file": str(path)}},
                              raw["states"][state], cell, enforce_budget=False)
        else:
            row = audit_state(state, entry, raw["states"][state], cell)
        results.append(row)
        problems += len(row["problems"])
        warnings += len(row["warnings"])
        h = row.get("height", {})
        print(f"{state:8s} {row.get('frame_count', 0):>6d} {row.get('cell', ['-', '-'])[0]:>4}x"
              f"{row.get('cell', ['-', '-'])[1]:<4d} {row.get('bytes', 0):>9,} "
              f"{h.get('min', 0):>10d}-{h.get('max', 0):<5d} ({h.get('spread_pct', 0):>4.1f}%) "
              f"{row.get('sole_y', {}).get('min', 0):>3d}-{row.get('sole_y', {}).get('max', 0):<4d} "
              f"{row.get('loop_iou', 0):>8}")

    out = Path(args.json)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"scope": "authoring" if args.authoring else "service",
                               "problems": problems, "warnings": warnings,
                               "states": results}, ensure_ascii=False, indent=2) + "\n")

    for row in results:
        for message in row["problems"]:
            print(f"  FAIL {row['state']}: {message}")
        for message in row["warnings"]:
            print(f"  WARN {row['state']}: {message}")
    attack = next((r for r in results if r["state"] == "attack"), None)
    if attack and attack.get("attack_hit_check"):
        print("  attack frame 6: " + ", ".join(f"{k}={'ok' if v else 'FAIL'}"
                                               for k, v in attack["attack_hit_check"].items()))
        print(f"  attack hammer at hit: cx={attack['attack_hit_frame_detail']['hammer_cx']} "
              f"cy={attack['attack_hit_frame_detail']['hammer_cy']} "
              f"(cycle cy {attack['attack_hit_frame_detail']['cycle_cy_range']})")
    shown = out.relative_to(ROOT) if ROOT in out.parents else out
    print(f"report: {shown}  problems={problems} warnings={warnings}")
    return 1 if problems or (args.strict and warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
