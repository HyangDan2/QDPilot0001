"""Long browser run for the Phase 2.1 release gate.

Samples FPS, JS heap, lazy-load readiness, console errors and horizontal
scroll once per interval for ``--seconds`` and writes a JSON summary plus a
final screenshot. Intended for a local preview server serving the current
static tree:

    python3 tools/wo10_browser_soak.py --url http://127.0.0.1:8797/idle-ops/ \
        --seconds 3600 --out docs/screenshots/wo10
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SAMPLE = """() => {
  const scene = window.__scene;
  const heap = performance.memory ? performance.memory.usedJSHeapSize : null;
  return {
    fps: scene.stats.fps,
    particles: scene.stats.particles,
    bugs: scene.stats.bugs,
    racks: scene.stats.racks,
    readyStates: scene.library.stateReady.size,
    problems: scene.library.problems.length,
    heapBytes: heap,
    scrollOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    animator: scene.animator.snapshot(),
  };
}"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8797/idle-ops/")
    parser.add_argument("--seconds", type=int, default=3600)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--out", default="docs/screenshots/wo10")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        errors: list[str] = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(args.url, wait_until="networkidle")
        page.wait_for_function("window.__scene && window.__scene.loaded === true", timeout=20000)

        samples = []
        started = time.monotonic()
        while True:
            remaining = args.seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            sample = page.evaluate(SAMPLE)
            sample["atSeconds"] = round(time.monotonic() - started, 1)
            sample["errors"] = len(errors)
            samples.append(sample)
            page.wait_for_timeout(min(args.interval, max(0.0, remaining)) * 1000)

        screenshot = out / "browser-soak-final.png"
        page.screenshot(path=str(screenshot))
        browser.close()

    fps = [sample["fps"] for sample in samples]
    heaps = [sample["heapBytes"] for sample in samples if sample["heapBytes"]]
    summary = {
        "url": args.url,
        "seconds": round(time.monotonic() - started, 1),
        "samples": len(samples),
        "fps": {"min": min(fps), "max": max(fps), "median": statistics.median(fps)} if fps else None,
        "heapMB": {
            "first": round(heaps[0] / 1048576, 2),
            "last": round(heaps[-1] / 1048576, 2),
            "median": round(statistics.median(heaps) / 1048576, 2),
            "max": round(max(heaps) / 1048576, 2),
        } if heaps else None,
        "errors": errors[:20],
        "errorCount": len(errors),
        "maxScrollOverflow": max((s["scrollOverflow"] for s in samples), default=0),
        "finalReadyStates": samples[-1]["readyStates"] if samples else 0,
        "manifestProblems": samples[-1]["problems"] if samples else None,
        "screenshot": str(screenshot),
        "series": samples,
    }
    (out / "browser-soak.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "series"}, ensure_ascii=False, indent=2))
    return 0 if not errors and (not fps or min(fps) >= 30) else 1


if __name__ == "__main__":
    raise SystemExit(main())
