"""Capture the WO-10 UI width matrix (320-1920) from the public viewer.

For each width the script opens the public world, waits for the scene and
lazy idle state, samples FPS/tile state for a few seconds, checks horizontal
overflow and console errors, and saves a JPEG screenshot.

    python3 tools/wo10_ui_matrix.py --url http://127.0.0.1:8797/idle-ops/
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

WIDTHS = [
    (320, 700),
    (390, 844),
    (768, 1024),
    (1440, 900),
    (1920, 1080),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8797/idle-ops/")
    parser.add_argument("--out", default="docs/screenshots/wo10")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width, height in WIDTHS:
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
                is_mobile=width <= 390,
            )
            errors: list[str] = []
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(args.url, wait_until="networkidle")
            page.wait_for_function("window.__scene && window.__scene.loaded === true", timeout=20000)
            page.wait_for_timeout(2500)
            sample = page.evaluate(
                """() => ({
                  fps: window.__scene.stats.fps,
                  particles: window.__scene.stats.particles,
                  bugs: window.__scene.stats.bugs,
                  racks: window.__scene.stats.racks,
                  ready: window.__scene.library.stateReady.size,
                  overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                })"""
            )
            path = out / f"ui-{width}.jpg"
            page.screenshot(path=str(path), type="jpeg", quality=85)
            results.append({"width": width, "height": height, **sample, "errors": errors, "image": str(path)})
            page.close()
        browser.close()

    summary = {"url": args.url, "results": results, "ok": all(
        not row["errors"] and row["overflow"] == 0 and row["fps"] >= 50 for row in results
    )}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
