// WO-08 regression coverage: manifest v3 normalization, v2 compatibility,
// per-asset facing, per-frame anchors/durations, hit events and atlas math.
// These tests import the real engine modules (no mocks of the code under test).

import test from "node:test";
import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  DEFAULT_MANIFEST,
  LOAD_RETRY_MS,
  MAX_TEXTURE,
  SpriteLibrary,
  flipFor,
  normalizeManifest,
} from "../../static/public/assets/engine/sprites.js";
import { Animator, STATES, specDuration } from "../../static/public/assets/engine/animation.js";

const manifestUrl = new URL("../../static/public/assets/sprites/manifest.json", import.meta.url);

function approx(actual, expected, epsilon = 1e-9) {
  assert.ok(Math.abs(actual - expected) <= epsilon, `${actual} != ${expected}`);
}

function libraryWith(manifest) {
  const library = new SpriteLibrary();
  library.manifest = normalizeManifest(manifest).manifest;
  return library;
}

test("shipped manifest is manifest v3 with right-facing hero art", async () => {
  const raw = JSON.parse(await readFile(manifestUrl, "utf8"));
  assert.equal(raw.version, 3);
  const { manifest, problems } = normalizeManifest(raw);
  assert.deepEqual(problems, []);
  const expected = { idle: 1.0, run: 1.0, attack: 0.75, repair: 0.8, levelup: 1.0, install: 1.0 };
  let frames = 0;
  for (const [state, seconds] of Object.entries(expected)) {
    const entry = manifest.states[state];
    assert.equal(entry.facing, "right", `${state} facing`);
    assert.equal(entry.fallback.facing, "right", `${state} fallback facing`);
    approx(entry.seconds, seconds);
    assert.ok(entry.fallback.file.endsWith(".png"), `${state} fallback file`);
    frames += entry.frames;
  }
  assert.equal(frames, 64);
  assert.deepEqual(manifest.states.attack.hits, [{ frame: 6, type: "strike" }]);
  // Phase 2.2.1 (WO-01/WO-02): every state ships a 384 px-cell service atlas
  // built from a preserved 768 px-cell authoring master. The manifest is the
  // single source of truth for both resolutions.
  assert.deepEqual(manifest.cell, [384, 384], "service cell");
  assert.deepEqual(raw.authoring.cell, [768, 768], "authoring cell");
  const atlases = {
    idle: { file: "hyangdani-idle-v31-4x2.png", authoring: "static/public/assets/sprites/hyangdani-idle-v3-4x2.png", columns: 4, rows: 2 },
    run: { file: "hyangdani-run-v31-4x3.png", authoring: "art/hyangdani/export/run-v31/hyangdani-run-v31-4x3-768.png", columns: 4, rows: 3 },
    attack: { file: "hyangdani-attack-v31-4x3.png", authoring: "static/public/assets/sprites/hyangdani-attack-v3-4x3.png", columns: 4, rows: 3 },
    repair: { file: "hyangdani-repair-v31-4x2.png", authoring: "static/public/assets/sprites/hyangdani-repair-v3-4x2.png", columns: 4, rows: 2 },
    levelup: { file: "hyangdani-levelup-v31-4x3.png", authoring: "static/public/assets/sprites/hyangdani-levelup-v3-4x3.png", columns: 4, rows: 3 },
    install: { file: "hyangdani-install-v31-4x3.png", authoring: "static/public/assets/sprites/hyangdani-install-v3-4x3.png", columns: 4, rows: 3 },
  };
  for (const [state, expectedSource] of Object.entries(atlases)) {
    const entry = manifest.states[state];
    assert.equal(entry.source.type, "atlas", `${state} source type`);
    assert.equal(entry.source.file, expectedSource.file, `${state} file`);
    assert.equal(entry.source.columns, expectedSource.columns, `${state} columns`);
    assert.equal(entry.source.rows, expectedSource.rows, `${state} rows`);
    assert.equal(entry.source.width, expectedSource.columns * 384, `${state} width`);
    assert.equal(entry.source.height, expectedSource.rows * 384, `${state} height`);
    assert.equal(entry.source.columns * entry.source.rows, entry.frames, `${state} cell count`);
    assert.equal(entry.source.width / entry.source.columns, 384, `${state} service cell width`);
    assert.equal(entry.source.height / entry.source.rows, 384, `${state} service cell height`);
    // The authoring master is declared per state and is exactly twice the
    // service resolution, so a test can tell the two apart without guessing.
    const authoring = raw.states[state].authoring;
    assert.equal(authoring.file, expectedSource.authoring, `${state} authoring file`);
    assert.equal(authoring.width, expectedSource.columns * 768, `${state} authoring width`);
    assert.equal(authoring.height, expectedSource.rows * 768, `${state} authoring height`);
    assert.equal(authoring.width, entry.source.width * 2, `${state} authoring is 2x service`);
    assert.equal(authoring.height, entry.source.height * 2, `${state} authoring is 2x service`);
  }
  // Stage 4 delivered repair (8F, 10 fps, 0.8 s, looping), Stage 5 levelup
  // (12F, 12 fps, 1.0 s, one-shot) and Stage 6 install (12F, 12 fps, 1.0 s,
  // one-shot); every state now has a delivered atlas and none falls back.
  assert.equal(manifest.states.repair.loop, true);
  assert.equal(manifest.states.repair.fps, 10);
  assert.equal(manifest.states.repair.frames, 8);
  assert.equal(manifest.states.levelup.loop, false);
  assert.equal(manifest.states.levelup.fps, 12);
  assert.equal(manifest.states.levelup.frames, 12);
  assert.equal(manifest.states.install.loop, false);
  assert.equal(manifest.states.install.fps, 12);
  assert.equal(manifest.states.install.frames, 12);
  for (const [state, entry] of Object.entries(manifest.states)) {
    assert.ok(entry.source && entry.source.type === "atlas", `${state} is a delivered atlas`);
    assert.ok(entry.fallback.file.endsWith(".png"), `${state} keeps a fallback`);
    // No service source may point at an authoring master: the browser must never
    // download a 768 px-cell file.
    assert.ok(!raw.states[state].source.file.includes("-v3-"), `${state} does not serve the authoring file`);
  }
  assert.equal(manifest.bugs.alive.facing, "left");
  assert.equal(manifest.bugs.hit.facing, "left");
});

// WO-02: the manifest must describe the files that are actually on disk, and a
// test must be able to tell the 768 px authoring master from the 384 px service
// export without hard-coding either resolution outside the manifest.
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

async function pngSize(relative) {
  const file = path.join(repoRoot, relative);
  const bytes = await readFile(file);
  assert.equal(bytes.subarray(1, 4).toString("latin1"), "PNG", `${relative} is a PNG`);
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20), bytes: bytes.length };
}

test("service atlases and authoring masters on disk match the manifest", async () => {
  const raw = JSON.parse(await readFile(manifestUrl, "utf8"));
  const serviceCell = raw.cell[0];
  const authoringCell = raw.authoring.cell[0];
  assert.equal(serviceCell, 384);
  assert.equal(authoringCell, 768);
  for (const [state, entry] of Object.entries(raw.states)) {
    const service = await pngSize(`static/public/assets/sprites/${entry.source.file}`);
    assert.equal(service.width, entry.source.width, `${state} service width on disk`);
    assert.equal(service.height, entry.source.height, `${state} service height on disk`);
    assert.equal(service.width, entry.source.columns * serviceCell, `${state} service cell`);
    assert.equal(service.height, entry.source.rows * serviceCell, `${state} service cell`);
    // WO-02 budget: a state's service atlas stays at or under 1 MiB.
    assert.ok(service.bytes <= 1024 * 1024, `${state} service atlas is ${service.bytes} bytes (budget 1 MiB)`);
    const master = await pngSize(entry.authoring.file);
    assert.equal(master.width, entry.authoring.width, `${state} authoring width on disk`);
    assert.equal(master.height, entry.authoring.height, `${state} authoring height on disk`);
    assert.equal(master.width, entry.source.columns * authoringCell, `${state} authoring cell`);
    assert.equal(master.height, entry.source.rows * authoringCell, `${state} authoring cell`);
    assert.notEqual(entry.authoring.file, entry.source.file, `${state} keeps the master separate from the service file`);
  }
  // The five atlases delivered before Phase 2.2.1 are preserved untouched in
  // the sprite directory even though the browser no longer requests them.
  for (const legacy of [
    "hyangdani-idle-v3-4x2.png", "hyangdani-run-v3-4x3.png", "hyangdani-attack-v3-4x3.png",
    "hyangdani-repair-v3-4x2.png", "hyangdani-levelup-v3-4x3.png", "hyangdani-install-v3-4x3.png",
  ]) {
    const info = await stat(path.join(repoRoot, "static/public/assets/sprites", legacy));
    assert.ok(info.size > 0, `${legacy} still exists`);
  }
});

test("manifest v2 still loads: sheet string becomes a strip source", () => {
  const v2 = {
    version: 2,
    facing: "left",
    sheetFormat: "horizontal-strip",
    cell: [320, 640],
    states: {
      idle: { sheet: "idle-sheet.png", fallback: "idle.png", frames: 8, fps: 8, loop: true },
      attack: { sheet: "attack-sheet.png", fallback: "strike.png", frames: 12, fps: 16, loop: false, hitFrame: 6 },
    },
  };
  const { manifest, problems } = normalizeManifest(v2);
  assert.deepEqual(problems, []);
  assert.equal(manifest.version, 2);
  assert.deepEqual(manifest.states.idle.source, { type: "strip", file: "idle-sheet.png", width: null });
  assert.deepEqual(manifest.states.idle.fallback, { file: "idle.png", facing: "left" });
  assert.equal(manifest.states.attack.hits[0].frame, 6);
  assert.equal(manifest.states.attack.seconds, 0.75);
  // States absent from a partial v2 manifest keep the built-in fallbacks.
  assert.equal(manifest.states.run.fallback.file, "hyangdani-run.png");
});

test("v3 per-state facing, per-frame anchors and a differently facing fallback", () => {
  const raw = {
    version: 3,
    facing: "right",
    anchor: [0.5, 0.9],
    states: {
      run: {
        frames: 4,
        fps: 8,
        facing: "right",
        anchor: [0.4, 0.95],
        anchors: [[0.4, 0.95], [0.5, 0.95], [0.55, 0.95], [0.45, 0.95]],
        source: { type: "strip", file: "run-strip.png" },
        fallback: { file: "run.png", facing: "left" },
      },
    },
  };
  const { manifest, problems } = normalizeManifest(raw);
  assert.deepEqual(problems, []);
  const run = manifest.states.run;
  assert.equal(run.source.type, "strip");
  assert.equal(run.seconds, 0.5);
  assert.equal(run.facing, "right");
  assert.deepEqual(run.anchors[2], [0.55, 0.95]);
  assert.deepEqual(run.fallback, { file: "run.png", facing: "left" });
  assert.equal(manifest.states.idle.facing, "right");
});

test("per-frame durations drive seconds and hit timing", () => {
  const raw = {
    version: 3,
    states: {
      attack: {
        frames: 4,
        fps: 4,
        durations: [0.1, 0.2, 0.3, 0.4],
        hits: [{ frame: 2, type: "strike" }],
      },
    },
  };
  const { manifest, problems } = normalizeManifest(raw);
  assert.deepEqual(problems, []);
  const attack = manifest.states.attack;
  approx(attack.seconds, 1.0);
  const library = libraryWith(raw);
  approx(library.hitSeconds("attack"), 0.3);
  approx(library.durationOf("attack"), 1.0);
});

test("hitSeconds falls back to frame/fps and is null without hits", () => {
  const library = new SpriteLibrary();
  library.manifest = normalizeManifest(DEFAULT_MANIFEST).manifest;
  approx(library.hitSeconds("attack"), 6 / 16);
  approx(library.durationOf("attack"), 0.75);
  assert.equal(library.hitSeconds("idle"), null);
  assert.equal(library.hitSeconds("levelup"), null);
});

test("strip, atlas and sequence sources compute frame rectangles", () => {
  const library = new SpriteLibrary();

  library.manifest = normalizeManifest({
    version: 3,
    states: { run: { frames: 4, source: { type: "strip", file: "strip.png" } } },
  }).manifest;
  library.images["strip.png"] = { width: 400, height: 200 };
  const strip = library.primaryArt(library.entry("run"));
  assert.equal(strip.kind, "strip");
  assert.deepEqual(strip.rect(2), { image: library.images["strip.png"], sx: 200, sy: 0, sw: 100, sh: 200 });
  assert.equal(strip.rect(99).sx, 300, "frame index clamps to the last cell");

  library.manifest = normalizeManifest({
    version: 3,
    states: {
      idle: {
        frames: 8,
        source: { type: "atlas", file: "atlas.png", columns: 4, rows: 2, width: 3072, height: 1536 },
      },
    },
  }).manifest;
  library.images["atlas.png"] = { width: 3072, height: 1536 };
  const atlas = library.primaryArt(library.entry("idle"));
  assert.equal(atlas.kind, "atlas");
  assert.deepEqual(atlas.rect(5), { image: library.images["atlas.png"], sx: 768, sy: 768, sw: 768, sh: 768 });
  assert.deepEqual(atlas.rect(7), { image: library.images["atlas.png"], sx: 2304, sy: 768, sw: 768, sh: 768 });

  library.manifest = normalizeManifest({
    version: 3,
    states: {
      run: { frames: 3, source: { type: "sequence", files: ["r0.png", "r1.png", "r2.png"] } },
    },
  }).manifest;
  library.images["r0.png"] = { width: 10, height: 20 };
  library.images["r1.png"] = { width: 10, height: 20 };
  library.images["r2.png"] = { width: 10, height: 20 };
  const sequence = library.primaryArt(library.entry("run"));
  assert.equal(sequence.kind, "sequence");
  assert.equal(sequence.frames, 3);
  assert.equal(sequence.rect(1).image, library.images["r1.png"]);
  assert.equal(sequence.rect(1).sw, 10);
});

test("atlas and file validation reports problems instead of loading bad art", () => {
  const small = normalizeManifest({
    version: 3,
    states: { idle: { frames: 8, source: { type: "atlas", file: "a.png", columns: 2, rows: 2 } } },
  });
  assert.ok(small.problems.some((p) => p.includes("holds 4 cells for 8 frames")));
  assert.equal(small.manifest.states.idle.source, null, "an invalid atlas is not used");

  const huge = normalizeManifest({
    version: 3,
    states: {
      idle: { frames: 8, source: { type: "atlas", file: "a.png", columns: 4, rows: 2, width: MAX_TEXTURE * 2, height: 1536 } },
    },
  });
  assert.ok(huge.problems.some((p) => p.includes(`exceeds ${MAX_TEXTURE}`)));
  assert.equal(huge.manifest.states.idle.source, null);

  const uneven = normalizeManifest({
    version: 3,
    states: {
      idle: { frames: 8, source: { type: "atlas", file: "a.png", columns: 4, rows: 2, width: 3001, height: 1536 } },
    },
  });
  assert.ok(uneven.problems.some((p) => p.includes("does not divide evenly")));
  assert.equal(uneven.manifest.states.idle.source, null);

  const traversal = normalizeManifest({
    version: 3,
    states: { run: { frames: 2, source: { type: "strip", file: "../escape.png" }, fallback: "/etc/passwd" } },
  });
  assert.ok(traversal.problems.some((p) => p.includes("inside the sprite directory")));
  assert.equal(traversal.manifest.states.run.source, null);
  assert.equal(traversal.manifest.states.run.fallback, null);
});

test("invalid durations, hit frames and sequence lengths are reported", () => {
  const durations = normalizeManifest({
    version: 3,
    states: { attack: { frames: 4, durations: [0.1, 0.2] } },
  });
  assert.ok(durations.problems.some((p) => p.includes("durations has 2 entries")));

  const mismatch = normalizeManifest({
    version: 3,
    states: { attack: { frames: 4, seconds: 2, durations: [0.1, 0.2, 0.3, 0.4] } },
  });
  assert.ok(mismatch.problems.some((p) => p.includes("seconds must equal the sum of durations")));
  approx(mismatch.manifest.states.attack.seconds, 1.0);

  const consistent = normalizeManifest({
    version: 3,
    states: { attack: { frames: 4, seconds: 1, durations: [0.1, 0.2, 0.3, 0.4] } },
  });
  assert.deepEqual(consistent.problems, []);
  approx(consistent.manifest.states.attack.seconds, 1.0);

  const hits = normalizeManifest({
    version: 3,
    states: { attack: { frames: 4, hits: [{ frame: 9 }] } },
  });
  assert.ok(hits.problems.some((p) => p.includes("hit frame out of range")));
  assert.deepEqual(hits.manifest.states.attack.hits, []);

  const sequence = normalizeManifest({
    version: 3,
    states: { run: { frames: 4, source: { type: "sequence", files: ["a.png"] } } },
  });
  assert.ok(sequence.problems.some((p) => p.includes("1 frames, expected 4")));

  const version = normalizeManifest({ version: 9 });
  assert.ok(version.problems.some((p) => p.includes("unsupported manifest version")));
  assert.equal(version.manifest.version, 3);
});

test("flipFor mirrors only when direction differs from the art facing", () => {
  assert.equal(flipFor("right", 1), 1);
  assert.equal(flipFor("right", -1), -1);
  assert.equal(flipFor("left", -1), 1);
  assert.equal(flipFor("left", 1), -1);
});

test("animator honours seconds, per-frame durations and hit frames", () => {
  const idle = new Animator("idle");
  approx(idle.duration(), 1.0);
  idle.update(0.125);
  assert.equal(idle.frameIndex(), 1);

  const attack = new Animator("attack");
  attack.play("attack", { restart: true });
  attack.update(0.2);
  assert.equal(attack.frameIndex(), 3);
  assert.equal(attack.consumeHit(), false);
  attack.update(0.25);
  assert.equal(attack.frameIndex(), 7);
  assert.equal(attack.consumeHit(), true);
  assert.equal(attack.consumeHit(), false, "hit fires once");
  attack.update(0.35);
  assert.equal(attack.done, true);
  assert.equal(attack.frameIndex(), 11);

  const run = new Animator("run");
  run.setSpecs({ run: { frames: 4, durations: [0.1, 0.2, 0.3, 0.4], loop: true } });
  run.elapsed = 0.05;
  assert.equal(run.frameIndex(), 0);
  run.elapsed = 0.15;
  assert.equal(run.frameIndex(), 1);
  run.elapsed = 0.35;
  assert.equal(run.frameIndex(), 2);
  run.elapsed = 0.65;
  assert.equal(run.frameIndex(), 3);
  run.elapsed = 1.05;
  assert.equal(run.frameIndex(), 0, "loop wraps over the duration sum");

  const scripted = new Animator("attack");
  scripted.setSpecs({ attack: { frames: 8, hits: [{ frame: 3 }, { frame: 5 }] } });
  scripted.play("attack", { restart: true });
  scripted.update(0.2);
  assert.equal(scripted.frameIndex(), 2);
  assert.equal(scripted.consumeHit(), false);
  scripted.update(0.1);
  assert.equal(scripted.frameIndex(), 3);
  assert.equal(scripted.consumeHit(), true);
});

test("early art access before the manifest arrives does not poison lazy loading", async () => {
  const originalImage = globalThis.Image;
  const originalFetch = globalThis.fetch;
  const raw = {
    version: 3,
    facing: "right",
    states: {
      idle: {
        frames: 8,
        fps: 8,
        source: { type: "atlas", file: "idle-atlas.png", columns: 4, rows: 2, width: 3072, height: 1536 },
        fallback: { file: "idle.png", facing: "right" },
      },
    },
  };
  const sizes = {
    "idle-atlas.png": [3072, 1536],
    "idle.png": [379, 640],
    "hyangdani-idle.png": [379, 640],
    "bug-alive.png": [318, 300],
    "bug-hit.png": [314, 300],
  };
  globalThis.Image = class {
    set src(value) {
      const name = String(value).split("/").pop();
      const [width, height] = sizes[name] || [0, 0];
      queueMicrotask(() => {
        this.width = width;
        this.height = height;
        this.complete = width > 0;
        if (width > 0) this.onload?.();
        else this.onerror?.();
      });
    }
  };
  globalThis.fetch = async () => ({ ok: true, json: async () => raw });
  try {
    const library = new SpriteLibrary();
    // The scene draws before load() resolves; this used the built-in defaults.
    await library.ensureState("idle");
    assert.equal(library.stateReady.has("idle"), true);
    await library.load();
    await library.ensureState("idle");
    const art = library.artFor("idle");
    assert.equal(art.kind, "atlas");
    assert.equal(art.frames, 8);
    assert.equal(library.problems.length, 0);
  } finally {
    globalThis.Image = originalImage;
    globalThis.fetch = originalFetch;
  }
});

test("a missing sprite is retried on a cooldown and keeps previously loaded art", async () => {
  const originalImage = globalThis.Image;
  const originalNow = Date.now;
  let constructed = 0;
  let now = 1000;
  globalThis.Image = class {
    constructor() {
      constructed += 1;
      this.width = 0;
      this.height = 0;
    }
    set src(value) {
      const name = String(value).split("/").pop();
      queueMicrotask(() => {
        if (name === "ok.png") {
          this.width = 100;
          this.height = 50;
          this.onload?.();
        } else {
          this.onerror?.();
        }
      });
    }
  };
  Date.now = () => now;
  try {
    const library = new SpriteLibrary();
    library.manifest = normalizeManifest({
      version: 3,
      states: {
        idle: { frames: 4, fps: 4, source: { type: "strip", file: "missing.png" }, fallback: { file: "ok.png" } },
      },
    }).manifest;
    library.images["ok.png"] = { width: 100, height: 50 };
    assert.equal(library.artFor("idle").kind, "fallback");
    await library.ensureState("idle");
    const requests = constructed;
    assert.equal(requests, 1);
    await library.ensureState("idle");
    await library.ensureState("idle");
    assert.equal(constructed, requests, "a failed file is not re-fetched during the cooldown");
    now += LOAD_RETRY_MS + 1;
    await library.ensureState("idle");
    assert.ok(constructed > requests, "the file is retried after the cooldown");
    assert.equal(library.artFor("idle").kind, "fallback", "the previously loaded fallback stays usable");
  } finally {
    globalThis.Image = originalImage;
    Date.now = originalNow;
  }
});

test("specDuration prefers seconds, then durations, then frames/fps", () => {
  approx(specDuration({ frames: 10, fps: 10 }), 1);
  approx(specDuration({ frames: 10, fps: 10, durations: [0.5, 0.5] }), 1);
  approx(specDuration({ frames: 4, fps: 8, seconds: 2 }), 2);
  approx(specDuration(STATES.repair), 0.8);
});
