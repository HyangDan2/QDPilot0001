#!/usr/bin/env python3
"""Phase 2.2.1 browser QA (WO-04): drive the real game and measure the assets.

Against a running local instance:

    python3 tools/wo0221_browser_qa.py --url http://127.0.0.1:8795/idle-ops/

What it does, in one browser session per viewport:

  1. desktop (1440x900) and mobile (390x844, DPR 3) screenshots of the hero
     running and attacking, drawn by the engine from the shipped atlases
  2. measures the running FPS and the decoded bitmap memory of every state
  3. repeats the measurement with the 768 px authoring masters swapped in place
     of the 384 px service files (same manifest, same engine) so the two
     resolutions are compared on identical code
  4. blocks the Run atlas to prove the engine keeps drawing from the fallback
     instead of going blank

Results land in docs/screenshots/wo0221/ with a JSON summary next to them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SPRITES = ROOT / "static/public/assets/sprites"

# service file -> authoring master, used for the 768 px comparison pass
MASTERS = {
    "hyangdani-idle-v31-4x2.png": SPRITES / "hyangdani-idle-v3-4x2.png",
    "hyangdani-run-v31-4x3.png": ROOT / "art/hyangdani/export/run-v31/hyangdani-run-v31-4x3-768.png",
    "hyangdani-attack-v31-4x3.png": SPRITES / "hyangdani-attack-v3-4x3.png",
    "hyangdani-repair-v31-4x2.png": SPRITES / "hyangdani-repair-v3-4x2.png",
    "hyangdani-levelup-v31-4x3.png": SPRITES / "hyangdani-levelup-v3-4x3.png",
    "hyangdani-install-v31-4x3.png": SPRITES / "hyangdani-install-v3-4x3.png",
}

POSE = """([state, elapsed, dir]) => {
  const scene = window.__scene;
  scene.setMotion(false);
  const hero = scene.hero;
  hero.oneshot = null;
  // the mode lives on the sprite state object, not on the controller
  hero.hero.mode = state;
  hero.animator.play(state, { restart: true });
  hero.animator.elapsed = elapsed;
  hero.hero.dir = dir;
  hero.hero.x = scene.width * 0.5;
  hero.hero.y = scene.floorY + Math.min(scene.height * 0.16, 96);
  hero.hero.hop = 0;
  hero.hero.impact = state === "attack" ? 1 : 0;
  scene.dirty = true;
  scene.render();
  // The page keeps polling /state and calling setWorld(), which can swap the
  // hero into another one-shot between this call and the screenshot.
  scene.setWorld = () => {};
  scene.stop();
}"""

MEASURE = """() => {
  const library = window.__scene.library;
  const images = Object.entries(library.images || {});
  const decoded = images.map(([name, image]) => ({
    name, width: image.width, height: image.height, bytes: image.width * image.height * 4,
  }));
  const states = {};
  for (const state of Object.keys(library.manifest.states)) {
    const art = library.artFor(state);
    states[state] = art ? { kind: art.kind, frames: art.frames } : null;
  }
  return {
    cell: library.manifest.cell,
    problems: library.problems,
    decoded,
    decodedTotal: decoded.reduce((sum, item) => sum + item.bytes, 0),
    states,
    fps: window.__scene.stats.fps,
  };
}"""

NON_BLANK = """() => {
  const canvas = document.querySelector("canvas");
  const ctx = canvas.getContext("2d");
  const { width, height } = canvas;
  const data = ctx.getImageData(0, 0, width, height).data;
  let lit = 0;
  for (let i = 0; i < data.length; i += 4 * 37) {
    if (data[i] + data[i + 1] + data[i + 2] > 90) lit += 1;
  }
  return { sampled: Math.floor(data.length / (4 * 37)), lit };
}"""


def start(page, errors):
    page.wait_for_function("window.__scene && window.__scene.loaded === true", timeout=30000)
    page.evaluate("""async () => {
      await Promise.all(Object.keys(window.__scene.library.manifest.states)
        .map((state) => window.__scene.library.ensureState(state)));
    }""")


def measure_fps(page, seconds=4.0):
    return page.evaluate(
        """async (seconds) => {
          const gl = window.__scene;
          let frames = 0;
          const start = performance.now();
          await new Promise((resolve) => {
            const tick = () => {
              frames += 1;
              if (performance.now() - start >= seconds * 1000) resolve();
              else requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
          });
          return { rafFps: Math.round((frames / (performance.now() - start)) * 1000), sceneFps: gl.stats.fps };
        }""",
        seconds,
    )


def run_pass(browser, url, viewport, dpr, label, out, swap_masters=False, block_run=False):
    context = browser.new_context(viewport=viewport, device_scale_factor=dpr)
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    transfers: dict[str, int] = {}
    page.on("response", lambda response: transfers.__setitem__(
        response.url.split("/")[-1], len(response.body()) if response.ok else 0))

    if swap_masters:
        def fulfil(route):
            name = route.request.url.split("/")[-1]
            master = MASTERS.get(name)
            if master and master.is_file():
                route.fulfill(status=200, content_type="image/png", body=master.read_bytes())
            else:
                route.continue_()
        page.route("**/assets/sprites/*-v31-*.png", fulfil)
    if block_run:
        page.route("**/assets/sprites/hyangdani-run-v31-4x3.png", lambda route: route.fulfill(status=404, body=""))

    page.goto(url, wait_until="networkidle")
    start(page, errors)
    info = page.evaluate(MEASURE)
    fps = measure_fps(page)

    shots = []
    for state, elapsed, name in (("run", 0.25, "run"), ("attack", 0.40, "attack")):
        page.evaluate(POSE, [state, elapsed, 1])
        page.wait_for_timeout(60)
        path = out / f"{label}-{name}.jpg"
        page.screenshot(path=str(path), type="jpeg", quality=82)
        box = page.evaluate("""() => {
          const scene = window.__scene;
          const hero = scene.hero.hero;
          const width = 460, height = 400;
          return { x: Math.max(0, Math.round(hero.x - width / 2)),
                   y: Math.max(0, Math.round(hero.y - height + 48)), width, height };
        }""")
        crop = out / f"{label}-{name}-hero.png"
        page.screenshot(path=str(crop), clip=box)
        shots.append(str(crop.relative_to(ROOT)))

    blank = page.evaluate(NON_BLANK)
    context.close()
    return {
        "label": label,
        "viewport": list(viewport.values()),
        "device_scale_factor": dpr,
        "fps": fps,
        "cell": info["cell"],
        "problems": info["problems"],
        "states": info["states"],
        "decoded_total_bytes": info["decodedTotal"],
        "decoded": sorted(info["decoded"], key=lambda item: -item["bytes"]),
        "transfers": transfers,
        "transfer_total": sum(transfers.values()),
        "canvas": blank,
        "screenshots": shots,
        "console_errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8795/idle-ops/")
    parser.add_argument("--out", default="docs/screenshots/wo0221")
    args = parser.parse_args()
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)

    results = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        results["desktop"] = run_pass(browser, args.url, {"width": 1440, "height": 900}, 1,
                                      "desktop-1440", out)
        results["mobile"] = run_pass(browser, args.url, {"width": 390, "height": 844}, 3,
                                     "mobile-390", out)
        results["desktop_768"] = run_pass(browser, args.url, {"width": 1440, "height": 900}, 1,
                                          "desktop-1440-768", out, swap_masters=True)
        results["mobile_768"] = run_pass(browser, args.url, {"width": 390, "height": 844}, 3,
                                         "mobile-390-768", out, swap_masters=True)
        results["fallback"] = run_pass(browser, args.url, {"width": 1440, "height": 900}, 1,
                                       "desktop-1440-fallback", out, block_run=True)
        browser.close()

    summary = {
        "url": args.url,
        "desktop": results["desktop"],
        "mobile": results["mobile"],
        "desktop_768": results["desktop_768"],
        "mobile_768": results["mobile_768"],
        "fallback": results["fallback"],
    }
    (out / "browser-qa.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    for key in ("desktop", "mobile", "desktop_768", "mobile_768", "fallback"):
        row = results[key]
        print(f"{key:12s} cell={row['cell']} fps={row['fps']} decoded={row['decoded_total_bytes'] / 1024 / 1024:.2f} MiB "
              f"transfer={row['transfer_total'] / 1024:.0f} KiB canvas_lit={row['canvas']['lit']}/{row['canvas']['sampled']} "
              f"problems={len(row['problems'])} errors={len(row['console_errors'])}")
    run_state = results["fallback"]["states"].get("run")
    print(f"fallback run art: {run_state}")
    ok = (not results["desktop"]["problems"] and not results["desktop"]["console_errors"]
          and not results["mobile"]["console_errors"] and results["fallback"]["canvas"]["lit"] > 1000
          and run_state and run_state["kind"] == "fallback")
    print("verdict:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
