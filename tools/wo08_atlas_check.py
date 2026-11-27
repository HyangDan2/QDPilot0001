"""WO-08 browser evidence for manifest v3 atlas slicing and load-failure fallback.

Serves a test-only manifest (number-tile atlases under tests/fixtures/sprites/)
through Playwright network interception, so no placeholder art touches the
production sprite directory. Captures:

  docs/screenshots/wo08/atlas-idle-frame3.png
  docs/screenshots/wo08/atlas-run-frame7.png
  docs/screenshots/wo08/fallback-on-missing-atlas.png

Usage:

    python3 tools/wo08_atlas_check.py --url http://127.0.0.1:8795/idle-ops/
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests/fixtures/sprites"
OUT = ROOT / "docs/screenshots/wo08"

FALLBACKS = {
    "idle": "hyangdani-idle.png",
    "run": "hyangdani-run.png",
    "attack": "hyangdani-strike.png",
    "repair": "hyangdani-idle.png",
    "levelup": "hyangdani-idle.png",
    "install": "hyangdani-idle.png",
}


def base_state(name: str, frames: int, fps: int, loop: bool, seconds: float) -> dict:
    entry = {
        "frames": frames,
        "fps": fps,
        "loop": loop,
        "seconds": seconds,
        "facing": "right",
        "fallback": {"file": FALLBACKS[name], "facing": "right"},
    }
    if name == "attack":
        entry["hits"] = [{"frame": 6, "type": "strike"}]
    return entry


def atlas_manifest(use_atlas: bool, missing: bool = False) -> dict:
    states = {
        "idle": base_state("idle", 8, 8, True, 1.0),
        "run": base_state("run", 12, 12, True, 1.0),
        "attack": base_state("attack", 12, 16, False, 0.75),
        "repair": base_state("repair", 8, 10, True, 0.8),
        "levelup": base_state("levelup", 12, 12, False, 1.0),
        "install": base_state("install", 12, 12, False, 1.0),
    }
    if use_atlas:
        states["idle"]["source"] = {
            "type": "atlas",
            "file": "test-idle-4x2.png" if not missing else "test-missing.png",
            "columns": 4,
            "rows": 2,
            "width": 3072,
            "height": 1536,
        }
        states["run"]["source"] = {
            "type": "atlas",
            "file": "test-run-4x3.png",
            "columns": 4,
            "rows": 3,
            "width": 3072,
            "height": 2304,
        }
    return {
        "version": 3,
        "character": "hyangdani",
        "anchor": [0.5, 1.0],
        "facing": "right",
        "maxTexture": 4096,
        "states": states,
        "bugs": {
            "alive": {"file": "bug-alive.png", "facing": "left"},
            "hit": {"file": "bug-hit.png", "facing": "left"},
        },
    }


def route_manifest(page, manifest: dict) -> None:
    page.route(
        "**/assets/sprites/manifest.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=json.dumps(manifest),
        ),
    )


def route_fixtures(page) -> None:
    def handler(route):
        name = route.request.url.rsplit("/", 1)[-1]
        path = FIXTURES / name
        if path.is_file():
            route.fulfill(status=200, content_type="image/png", body=path.read_bytes())
        else:
            route.fulfill(status=404, body="not found")

    page.route("**/assets/sprites/test-*.png", handler)


def pose(page, state: str, direction: int, elapsed: float) -> None:
    page.evaluate(
        """([state, direction, elapsed]) => {
          const scene = window.__scene;
          scene.setMotion(false);
          scene.hero.oneshot = null;
          scene.hero.hero.mode = state;
          scene.hero.animator.play(state, { restart: true });
          scene.hero.animator.elapsed = elapsed;
          scene.hero.hero.dir = direction;
          scene.hero.hero.x = scene.width * 0.5;
          scene.hero.hero.y = scene.floorY + Math.min(scene.height * 0.16, 96);
          scene.hero.hero.hop = 0;
          scene.dirty = true;
          scene.render();
        }""",
        [state, direction, elapsed],
    )
    page.wait_for_timeout(120)


def clip(page) -> dict:
    return page.evaluate(
        """() => {
          const scene = window.__scene;
          const hero = scene.hero.hero;
          return {
            x: Math.max(0, Math.round(hero.x - 230)),
            y: Math.max(0, Math.round(hero.y - 340)),
            width: 460,
            height: 380,
          };
        }"""
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8795/idle-ops/")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()

        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors: list[str] = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        route_manifest(page, atlas_manifest(use_atlas=True))
        route_fixtures(page)
        page.goto(args.url, wait_until="networkidle")
        page.wait_for_function("window.__scene && window.__scene.loaded === true", timeout=20000)
        page.evaluate(
            """async () => {
              const library = window.__scene.library;
              await Promise.all(Object.keys(library.manifest.states).map((s) => library.ensureState(s)));
            }"""
        )
        report["atlas"] = page.evaluate(
            """() => {
              const library = window.__scene.library;
              const idle = library.artFor("idle");
              const run = library.artFor("run");
              const idle3 = idle.rect(3);
              const run7 = run.rect(7);
              return {
                version: library.manifest.version,
                problems: library.problems,
                idleKind: idle.kind,
                idleFrames: idle.frames,
                idleFrameWidth: idle.frameWidth,
                idleRect3: { sx: idle3.sx, sy: idle3.sy, sw: idle3.sw, sh: idle3.sh },
                runKind: run.kind,
                runFrames: run.frames,
                runRect7: { sx: run7.sx, sy: run7.sy, sw: run7.sw, sh: run7.sh },
              };
            }"""
        )
        pose(page, "idle", 1, 3 / 8 + 0.01)
        page.screenshot(path=str(OUT / "atlas-idle-frame3.png"), clip=clip(page))
        pose(page, "run", 1, 7 / 12 + 0.01)
        page.screenshot(path=str(OUT / "atlas-run-frame7.png"), clip=clip(page))
        report["atlas"]["consoleErrors"] = errors
        page.close()

        failure = browser.new_page(viewport={"width": 1280, "height": 800})
        page_errors: list[str] = []
        failure.on("pageerror", lambda exc: page_errors.append(str(exc)))
        route_manifest(failure, atlas_manifest(use_atlas=True, missing=True))
        route_fixtures(failure)
        failure.goto(args.url, wait_until="networkidle")
        failure.wait_for_function("window.__scene && window.__scene.loaded === true", timeout=20000)
        failure.wait_for_timeout(600)
        report["missing"] = failure.evaluate(
            """() => {
              const library = window.__scene.library;
              const art = library.artFor("idle");
              return {
                problems: library.problems,
                kind: art.kind,
                facing: art.facing,
                frames: art.frames,
              };
            }"""
        )
        pose(failure, "idle", 1, 0.2)
        failure.screenshot(path=str(OUT / "fallback-on-missing-atlas.png"), clip=clip(failure))
        report["missing"]["pageErrors"] = page_errors
        failure.close()
        browser.close()

    ok = (
        report["atlas"]["idleKind"] == "atlas"
        and report["atlas"]["idleFrames"] == 8
        and report["atlas"]["runFrames"] == 12
        and not report["atlas"]["problems"]
        and not report["atlas"]["consoleErrors"]
        and report["missing"]["kind"] == "fallback"
        and not report["missing"]["pageErrors"]
    )
    print(json.dumps({"ok": ok, "report": report}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
