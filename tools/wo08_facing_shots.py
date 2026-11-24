"""Capture WO-08 facing evidence: the hero running/attacking left and right.

Requires a running Idle Ops instance that serves the current static tree:

    python3 tools/wo08_facing_shots.py --url http://127.0.0.1:8795/idle-ops/

Screenshots land in docs/screenshots/wo08/ and the script prints the manifest
facts it observed in the browser (facing, hit time, load problems).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

POSES = [
    # state, direction, elapsed seconds, file name
    ("run", 1, 0.25, "run-right"),
    ("run", -1, 0.25, "run-left"),
    ("attack", 1, 0.38, "attack-right"),
    ("attack", -1, 0.38, "attack-left"),
    ("idle", 1, 0.1, "idle-right"),
    ("idle", -1, 0.1, "idle-left"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8795/idle-ops/")
    parser.add_argument("--out", default="docs/screenshots/wo08")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
        errors: list[str] = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(args.url, wait_until="networkidle")
        page.wait_for_function("window.__scene && window.__scene.loaded === true", timeout=20000)
        # Art loads lazily per state; preload every state so a screenshot is not
        # taken mid-load (which would show the idle fallback).
        page.evaluate(
            """async () => {
              const library = window.__scene.library;
              await Promise.all(Object.keys(library.manifest.states).map((state) => library.ensureState(state)));
            }"""
        )

        info = page.evaluate(
            """() => {
              const scene = window.__scene;
              const library = scene.library;
              const states = library.manifest.states;
              return {
                version: library.manifest.version,
                problems: library.problems,
                facing: Object.fromEntries(Object.entries(states).map(([k, v]) => [k, v.facing])),
                fallbackFacing: Object.fromEntries(Object.entries(states).map(([k, v]) => [k, v.fallback && v.fallback.facing])),
                hitSeconds: library.hitSeconds("attack"),
                attackSeconds: library.durationOf("attack"),
                bugFacing: [library.manifest.bugs.alive.facing, library.manifest.bugs.hit.facing],
              };
            }"""
        )

        for state, direction, elapsed, name in POSES:
            page.evaluate(
                """([state, direction, elapsed]) => {
                  const scene = window.__scene;
                  scene.setMotion(false);
                  scene.hero.oneshot = null;
                  scene.hero.hero.mode = state === "attack" ? "attack" : state === "run" ? "run" : "idle";
                  scene.hero.animator.play(state, { restart: true });
                  scene.hero.animator.elapsed = elapsed;
                  scene.hero.hero.dir = direction;
                  scene.hero.hero.x = scene.width * 0.5;
                  scene.hero.hero.y = scene.floorY + Math.min(scene.height * 0.16, 96);
                  scene.hero.hero.hop = 0;
                  scene.hero.hero.impact = state === "attack" ? 1 : 0;
                  scene.dirty = true;
                  scene.render();
                }""",
                [state, direction, elapsed],
            )
            page.wait_for_timeout(120)
            box = page.evaluate(
                """() => {
                  const scene = window.__scene;
                  const width = 460;
                  const height = 380;
                  const hero = scene.hero.hero;
                  return {
                    x: Math.max(0, Math.round(hero.x - width / 2)),
                    y: Math.max(0, Math.round(hero.y - height + 40)),
                    width,
                    height,
                  };
                }"""
            )
            page.screenshot(path=str(out / f"{name}.png"), clip=box)
            print(f"{name}: {box}")

        browser.close()

    print(json.dumps({"info": info, "consoleErrors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors and not info["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
