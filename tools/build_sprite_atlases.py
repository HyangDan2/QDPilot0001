#!/usr/bin/env python3
"""Build the service-resolution (384 px cell) sprite atlases from the 768 px
authoring masters, and verify what is committed.

WO-02 (Phase 2.2.1). The authoring masters stay at 768x768 per cell; the runtime
file served to browsers is a separate 384x384-cell export so a state costs about
a quarter of the decoded pixels and a third of the transfer size. The engine
reads the real size from ``manifest.json``; nothing here is hard-coded in the
engine.

    python3 tools/build_sprite_atlases.py            # write the service atlases
    python3 tools/build_sprite_atlases.py --check    # verify committed files

Budget: a state's service atlas should stay at or under 1 MiB. A full-colour
(PNG-32) export is written when it already fits; otherwise the same image is
re-encoded with a 256-colour palette, which is what the 384 px cells need to
meet the budget. Every decision is printed and stored in the JSON report.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SPRITES = ROOT / "static/public/assets/sprites"
AUTHORING = ROOT / "art/hyangdani/export"

AUTHORING_CELL = 768
SERVICE_CELL = 384
BUDGET = 1024 * 1024

# state: (authoring file, service file, columns, rows)
STATES = {
    "idle": ("hyangdani-idle-v3-4x2.png", "hyangdani-idle-v31-4x2.png", 4, 2),
    "run": ("run-v31/hyangdani-run-v31-4x3-768.png", "hyangdani-run-v31-4x3.png", 4, 3),
    "attack": ("hyangdani-attack-v3-4x3.png", "hyangdani-attack-v31-4x3.png", 4, 3),
    "repair": ("hyangdani-repair-v3-4x2.png", "hyangdani-repair-v31-4x2.png", 4, 2),
    "levelup": ("hyangdani-levelup-v3-4x3.png", "hyangdani-levelup-v31-4x3.png", 4, 3),
    "install": ("hyangdani-install-v3-4x3.png", "hyangdani-install-v31-4x3.png", 4, 3),
}


def authoring_path(entry: str) -> Path:
    """Authoring masters live in art/ for new art, in the sprite dir for the
    five atlases delivered before Phase 2.2.1 (kept in place, never deleted)."""
    candidate = AUTHORING / entry
    if candidate.is_file():
        return candidate
    return SPRITES / entry


def encode(im: Image.Image) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    im.save(buffer, "PNG", optimize=True)
    data = buffer.getvalue()
    if len(data) <= BUDGET:
        return data, "png32"
    quantised = im.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG)
    qbuffer = io.BytesIO()
    quantised.save(qbuffer, "PNG", optimize=True)
    qdata = qbuffer.getvalue()
    if len(qdata) < len(data):
        return qdata, "png8-256"
    return data, "png32-over-budget"


def psnr(a: Image.Image, b: Image.Image) -> float:
    x = np.array(a.convert("RGBA"), dtype=np.float32)
    y = np.array(b.convert("RGBA"), dtype=np.float32)
    mse = float(((x - y) ** 2).mean())
    if mse == 0:
        return 100.0
    return round(10 * np.log10(255.0**2 / mse), 2)


def build(check: bool) -> int:
    report = []
    failures = 0
    for state, (entry, service_name, columns, rows) in STATES.items():
        master_path = authoring_path(entry)
        if not master_path.is_file():
            print(f"{state}: MISSING authoring master {master_path}")
            failures += 1
            continue
        master = Image.open(master_path).convert("RGBA")
        expected = (columns * AUTHORING_CELL, rows * AUTHORING_CELL)
        if master.size != expected:
            print(f"{state}: authoring {master.size} != {expected}")
            failures += 1
            continue
        service = master.resize((columns * SERVICE_CELL, rows * SERVICE_CELL), Image.LANCZOS)
        data, encoding = encode(service)
        target = SPRITES / service_name
        row = {
            "state": state,
            "authoring": str(master_path.relative_to(ROOT)),
            "authoring_size": list(master.size),
            "service": service_name,
            "service_size": list(service.size),
            "columns": columns,
            "rows": rows,
            "bytes": len(data),
            "encoding": encoding,
        }
        if check:
            if not target.is_file():
                print(f"{state}: MISSING committed service atlas {service_name}")
                failures += 1
                continue
            committed = Image.open(target).convert("RGBA")
            if committed.size != service.size:
                print(f"{state}: committed {committed.size} != expected {service.size}")
                failures += 1
                continue
            delta = psnr(service, committed)
            row["committed_bytes"] = target.stat().st_size
            row["psnr_vs_rebuilt"] = delta
            row["ok"] = delta >= 30.0 and target.stat().st_size <= BUDGET * 2
            if not row["ok"]:
                failures += 1
            print(f"{state}: {target.stat().st_size:,}B committed, {service.size[0]}x{service.size[1]}, "
                  f"PSNR vs rebuilt {delta} dB")
        else:
            target.write_bytes(data)
            row["committed_bytes"] = target.stat().st_size
            print(f"{state}: wrote {service_name} {service.size[0]}x{service.size[1]} "
                  f"{len(data):,}B ({encoding})")
        report.append(row)

    out = ROOT / "docs/screenshots/wo0221/atlas-build-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    total = sum(row.get("committed_bytes", 0) for row in report)
    print(f"total service bytes: {total:,}B ({total / 1024 / 1024:.2f} MiB)")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify committed service atlases")
    args = parser.parse_args()
    return build(args.check)


if __name__ == "__main__":
    raise SystemExit(main())
