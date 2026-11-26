#!/usr/bin/env python3
"""Import a delivered Phase 2.2.x atlas into the repository (WO-06).

The Phase 2.2 branch landed three near-identical ``install_phase22_*.py``
scripts that pinned one SHA-256 each and wrote a single resolution. This is the
same gate, generalised to the delivery spec in ``docs/ASSET_REQUIREMENTS_V3.md``
as of Phase 2.2.1:

  * the bundle holds the **768 px-cell authoring atlas** (the production canvas)
  * the importer validates it (PNG-32, exact grid, alpha in every cell, no
    duplicated frames, nothing clipped by a cell edge)
  * it stores the master under ``art/hyangdani/export/<state>-v31/`` (outside
    the web root) and writes the **384 px-cell service atlas** next to the
    other runtime files with a new file name
  * it rewrites only that state's ``authoring`` and ``source`` in
    ``manifest.json``; every other state, the fallbacks and the facing stay put
  * it refuses to overwrite an existing file with different pixels

    python3 tools/import_phase22_atlas.py --state run --bundle /abs/path/delivery.zip --check
    python3 tools/import_phase22_atlas.py --state run --bundle /abs/path/delivery

The bundle may be a ZIP or a directory holding ``hyangdani-<state>-*.png``.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SPRITES = ROOT / "static/public/assets/sprites"
MANIFEST = SPRITES / "manifest.json"
AUTHORING_ROOT = ROOT / "art/hyangdani/export"

AUTHORING_CELL = 768
SERVICE_CELL = 384
BUDGET = 1024 * 1024
GRIDS = {
    "idle": (4, 2), "run": (4, 3), "attack": (4, 3),
    "repair": (4, 2), "levelup": (4, 3), "install": (4, 3),
}
FRAMES = {"idle": 8, "run": 12, "attack": 12, "repair": 8, "levelup": 12, "install": 12}


class Refused(Exception):
    """The delivery does not satisfy the spec; nothing was written."""


def _png_size(data: bytes):
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def read_bundle(bundle: Path, state: str) -> bytes:
    """Pick the authoring atlas out of a delivery.

    A bundle may also contain the individual frames, the service export or a
    previous revision with the same prefix, so candidates are narrowed to the
    ones whose pixel size is the declared 768 px grid for this state.
    """
    pattern = f"hyangdani-{state}"
    columns, rows = GRIDS[state]
    expected = (columns * AUTHORING_CELL, rows * AUTHORING_CELL)
    if bundle.is_dir():
        candidates = [(p.name, p.read_bytes()) for p in sorted(bundle.rglob("*.png"))
                      if p.name.startswith(pattern)]
    else:
        with zipfile.ZipFile(bundle) as archive:
            candidates = [(Path(n).name, archive.read(n)) for n in archive.namelist()
                          if Path(n).name.startswith(pattern) and n.endswith(".png")]
    if not candidates:
        raise Refused(f"no hyangdani-{state}-*.png inside {bundle}")
    matches = [(name, data) for name, data in candidates if _png_size(data) == expected]
    if len(matches) == 1:
        return matches[0][1]
    if len(matches) > 1:
        raise Refused(f"ambiguous delivery: {[name for name, _ in matches]}")
    raise Refused(f"no {expected[0]}x{expected[1]} atlas in the bundle "
                  f"({[name for name, _ in candidates]})")


def validate(data: bytes, state: str) -> Image.Image:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise Refused("not a PNG")
    if data[12:16] != b"IHDR":
        raise Refused("malformed PNG header")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    depth, colour = data[24], data[25]
    columns, rows = GRIDS[state]
    expected = (columns * AUTHORING_CELL, rows * AUTHORING_CELL)
    if (width, height) != expected:
        raise Refused(f"{state} atlas is {width}x{height}, the spec is {expected[0]}x{expected[1]}")
    if (depth, colour) != (8, 6):
        raise Refused("atlas must be an 8-bit RGBA (PNG-32) image")
    image = Image.open(io.BytesIO(data)).convert("RGBA")
    cells = []
    for index in range(columns * rows):
        r, c = divmod(index, columns)
        cell = np.array(image.crop((c * AUTHORING_CELL, r * AUTHORING_CELL,
                                    (c + 1) * AUTHORING_CELL, (r + 1) * AUTHORING_CELL)))
        alpha = cell[:, :, 3]
        ys, xs = np.nonzero(alpha > 16)
        if len(xs) < 1000:
            raise Refused(f"{state} frame {index} is empty")
        if xs.min() == 0 or ys.min() == 0 or xs.max() == AUTHORING_CELL - 1 or ys.max() == AUTHORING_CELL - 1:
            raise Refused(f"{state} frame {index} is clipped by the cell border")
        cells.append(cell.tobytes())
    if len(set(cells)) != len(cells):
        raise Refused(f"{state} contains duplicated frames")
    if len(cells) < FRAMES[state]:
        raise Refused(f"{state} needs {FRAMES[state]} frames, the atlas has {len(cells)}")
    return image


def encode_service(image: Image.Image, columns: int, rows: int) -> tuple[bytes, str]:
    service = image.resize((columns * SERVICE_CELL, rows * SERVICE_CELL), Image.LANCZOS)
    buffer = io.BytesIO()
    service.save(buffer, "PNG", optimize=True)
    data = buffer.getvalue()
    encoding = "png32"
    if len(data) > BUDGET:
        quantised = service.quantize(colors=256, method=Image.Quantize.FASTOCTREE,
                                     dither=Image.Dither.FLOYDSTEINBERG)
        qbuffer = io.BytesIO()
        quantised.save(qbuffer, "PNG", optimize=True)
        if len(qbuffer.getvalue()) < len(data):
            data, encoding = qbuffer.getvalue(), "png8-256"
    if len(data) > BUDGET:
        raise Refused(f"service atlas is {len(data)} B, over the 1 MiB budget")
    return data, encoding


def atomic_write(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def plan(state: str, bundle: Path, repo: Path):
    raw = read_bundle(bundle, state)
    image = validate(raw, state)
    columns, rows = GRIDS[state]
    service, encoding = encode_service(image, columns, rows)
    manifest_path = repo / "static/public/assets/sprites/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["states"][state]
    if entry.get("frames") != FRAMES[state]:
        raise Refused(f"manifest declares {entry.get('frames')} frames for {state}")
    fallback = entry.get("fallback")
    if not isinstance(fallback, dict) or not fallback.get("file", "").endswith(".png"):
        raise Refused(f"{state} must keep its PNG fallback")
    master_rel = f"art/hyangdani/export/{state}-v31/hyangdani-{state}-v31-{columns}x{rows}-768.png"
    service_rel = f"hyangdani-{state}-v31-{columns}x{rows}.png"
    revised = json.loads(json.dumps(manifest))
    revised["states"][state]["authoring"] = {
        "file": master_rel, "width": columns * AUTHORING_CELL, "height": rows * AUTHORING_CELL,
    }
    revised["states"][state]["source"] = {
        "type": "atlas", "file": service_rel, "columns": columns, "rows": rows,
        "width": columns * SERVICE_CELL, "height": rows * SERVICE_CELL,
    }
    return {
        "master": raw, "master_rel": master_rel,
        "service": service, "service_rel": service_rel, "encoding": encoding,
        "manifest_path": manifest_path,
        "manifest": (json.dumps(revised, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    }


def install(state: str, bundle: Path, repo: Path, check: bool) -> dict:
    job = plan(state, bundle, repo)
    master_path = repo / job["master_rel"]
    service_path = repo / "static/public/assets/sprites" / job["service_rel"]
    for path, data, label in ((master_path, job["master"], "authoring master"),
                              (service_path, job["service"], "service atlas")):
        if path.exists() and path.read_bytes() != data:
            raise Refused(f"{path.relative_to(repo)} already exists with different pixels ({label})")
    if check:
        return {"state": state, "master": job["master_rel"], "service": job["service_rel"],
                "encoding": job["encoding"], "bytes": len(job["service"]), "written": False}
    master_path.parent.mkdir(parents=True, exist_ok=True)
    if not master_path.exists():
        atomic_write(master_path, job["master"])
    if not service_path.exists():
        atomic_write(service_path, job["service"])
    if job["manifest_path"].read_bytes() != job["manifest"]:
        atomic_write(job["manifest_path"], job["manifest"])
    return {"state": state, "master": job["master_rel"], "service": job["service_rel"],
            "encoding": job["encoding"], "bytes": len(job["service"]), "written": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, choices=sorted(GRIDS))
    parser.add_argument("--bundle", required=True, type=Path, help="delivered ZIP or extracted directory")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="validate only, write nothing")
    args = parser.parse_args()
    try:
        summary = install(args.state, args.bundle.resolve(), args.repo.resolve(), args.check)
    except Refused as error:
        print(f"refused: {error}")
        return 2
    verb = "would import" if args.check else "imported"
    print(f"{verb} {summary['state']}: {summary['master']} -> {summary['service']} "
          f"({summary['encoding']}, {summary['bytes']:,} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
