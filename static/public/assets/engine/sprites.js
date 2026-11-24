// Sprite sheet loader with single-image fallback.
//
// The manifest describes the target animation sheets (frame count, fps, loop,
// hit frame, anchor, facing). Until those sheets exist, each state draws its
// declared fallback image, so the engine is fully testable without new art.

import { STATES } from "./animation.js";

const BASE = new URL("../sprites/", import.meta.url);
const MANIFEST_URL = new URL("manifest.json", BASE);

export const DEFAULT_MANIFEST = {
  version: 1,
  anchor: [0.5, 1],
  facing: "left",
  states: {
    idle:    { sheet: null, fallback: "hyangdani-idle.png", frames: 8, fps: 8, loop: true },
    run:     { sheet: null, fallback: "hyangdani-run.png", frames: 12, fps: 12, loop: true },
    attack:  { sheet: null, fallback: "hyangdani-strike.png", frames: 12, fps: 16, loop: false, hitFrame: 6 },
    repair:  { sheet: null, fallback: "hyangdani-idle.png", frames: 8, fps: 10, loop: true },
    levelup: { sheet: null, fallback: "hyangdani-idle.png", frames: 12, fps: 12, loop: false },
    install: { sheet: null, fallback: "hyangdani-idle.png", frames: 12, fps: 12, loop: false },
  },
  bugs: { alive: "bug-alive.png", hit: "bug-hit.png" },
};

export class SpriteLibrary {
  constructor(base = BASE, manifestUrl = MANIFEST_URL) {
    this.base = base;
    this.manifestUrl = manifestUrl;
    this.manifest = DEFAULT_MANIFEST;
    this.images = {};
    this.loaded = false;
  }

  async load() {
    try {
      const response = await fetch(this.manifestUrl, { cache: "no-cache" });
      if (response.ok) {
        const data = await response.json();
        this.manifest = { ...DEFAULT_MANIFEST, ...data, states: { ...DEFAULT_MANIFEST.states, ...(data.states || {}) } };
      }
    } catch {
      this.manifest = DEFAULT_MANIFEST;
    }
    const sources = new Set();
    for (const entry of Object.values(this.manifest.states)) {
      for (const key of ["sheet", "fallback"]) {
        if (entry[key]) sources.add(entry[key]);
      }
    }
    if (this.manifest.bugs?.alive) sources.add(this.manifest.bugs.alive);
    if (this.manifest.bugs?.hit) sources.add(this.manifest.bugs.hit);
    await Promise.all([...sources].map((name) => this.loadImage(name)));
    this.loaded = true;
  }

  loadImage(name) {
    return new Promise((resolve) => {
      const image = new Image();
      image.decoding = "async";
      image.onload = () => {
        this.images[name] = image;
        resolve();
      };
      image.onerror = () => {
        this.images[name] = null;
        resolve();
      };
      image.src = new URL(name, this.base).href;
    });
  }

  get anchor() {
    const [x, y] = this.manifest.anchor || [0.5, 1];
    return { x, y };
  }

  get facesLeft() {
    return this.manifest.facing !== "right";
  }

  entry(state) {
    return this.manifest.states[state] || this.manifest.states.idle || {};
  }

  artFor(state) {
    const entry = this.entry(state);
    const spec = STATES[state] || STATES.idle;
    const sheet = entry.sheet ? this.images[entry.sheet] : null;
    if (sheet && sheet.width > 0 && spec.frames > 1) {
      return { image: sheet, frames: spec.frames, sheet: true };
    }
    const fallback = entry.fallback ? this.images[entry.fallback] : null;
    if (fallback) return { image: fallback, frames: 1, sheet: false };
    return null;
  }

  bugArt(state) {
    const name = state === "hit" ? this.manifest.bugs?.hit : this.manifest.bugs?.alive;
    return (name && this.images[name]) || null;
  }
}
