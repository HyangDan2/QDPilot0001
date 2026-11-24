// Sprite manifest normalization and lazy sprite loading.
//
// Manifest v3 supports three source layouts per state — a horizontal strip, a
// grid atlas, or an individual-PNG sequence — plus per-state/per-fallback
// facing, per-frame anchors, per-frame durations and hit events. Manifest v2
// (single ``sheet`` string + ``fallback`` string) still loads: it is normalized
// into the same internal shape.
//
// Until the final art exists, every state declares a fallback image, so the
// engine runs unchanged on the current PNGs. Art files load lazily per state and
// a failed load keeps whatever image was already decoded, so the scene never
// goes blank after a transient 404.

import { STATES, specDuration } from "./animation.js";

const BASE = new URL("../sprites/", import.meta.url);
const MANIFEST_URL = new URL("manifest.json", BASE);
const FACINGS = new Set(["left", "right"]);
const SOURCE_TYPES = new Set(["strip", "atlas", "sequence"]);
export const MAX_TEXTURE = 4096;
// A missing sprite must not be re-requested on every animation frame; retry
// after this cooldown so a deploy that adds the file can still recover.
export const LOAD_RETRY_MS = 30000;

// Mirror the sprite only when the requested direction differs from the art's
// declared facing. Returns a canvas scale factor (-1 mirrors horizontally).
export function flipFor(facing, dir) {
  return (facing === "left") === (dir < 0) ? 1 : -1;
}

export const DEFAULT_MANIFEST = {
  version: 3,
  character: "hyangdani",
  anchor: [0.5, 1],
  facing: "right",
  maxTexture: MAX_TEXTURE,
  cell: [768, 768],
  states: {
    idle:    { source: null, fallback: { file: "hyangdani-idle.png" },   frames: 8,  fps: 8,  loop: true,  seconds: 1.0 },
    run:     { source: null, fallback: { file: "hyangdani-run.png" },    frames: 12, fps: 12, loop: true,  seconds: 1.0 },
    attack:  { source: null, fallback: { file: "hyangdani-strike.png" }, frames: 12, fps: 16, loop: false, seconds: 0.75, hits: [{ frame: 6, type: "strike" }] },
    repair:  { source: null, fallback: { file: "hyangdani-idle.png" },   frames: 8,  fps: 10, loop: true,  seconds: 0.8 },
    levelup: { source: null, fallback: { file: "hyangdani-idle.png" },   frames: 12, fps: 12, loop: false, seconds: 1.0 },
    install: { source: null, fallback: { file: "hyangdani-idle.png" },   frames: 12, fps: 12, loop: false, seconds: 1.0 },
  },
  bugs: {
    alive: { file: "bug-alive.png", facing: "left" },
    hit:   { file: "bug-hit.png", facing: "left" },
  },
};

function asNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function positive(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function point(value) {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const x = Number(value[0]);
  const y = Number(value[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return [x, y];
}

function safeFile(name, label, problems) {
  if (typeof name !== "string" || !name) {
    problems.push(`${label} must be a file name`);
    return null;
  }
  if (name.startsWith("/") || name.includes("..") || name.includes("\\")) {
    problems.push(`${label} must stay inside the sprite directory: ${name}`);
    return null;
  }
  return name;
}

function normalizeHits(raw, frames, base, problems) {
  if (raw.hits != null) {
    if (!Array.isArray(raw.hits)) {
      problems.push("hits must be a list");
      return [];
    }
    const hits = [];
    for (const item of raw.hits) {
      const frame = Math.round(asNumber(typeof item === "object" && item !== null ? item.frame : item, NaN));
      if (!Number.isFinite(frame) || frame < 0 || frame >= frames) {
        problems.push(`hit frame out of range for ${frames} frames: ${JSON.stringify(item)}`);
        continue;
      }
      const type = typeof item === "object" && item !== null && typeof item.type === "string" ? item.type : "strike";
      hits.push({ frame, type });
    }
    return hits;
  }
  const frame = asNumber(raw.hitFrame, asNumber(base.hitFrame, NaN));
  if (Number.isFinite(frame)) {
    const clamped = Math.min(frames - 1, Math.max(0, Math.round(frame)));
    return [{ frame: clamped, type: "strike" }];
  }
  return [];
}

function normalizeSource(state, raw, frames, ctx, problems) {
  const legacy = raw.sheet;
  const descriptor = raw.source;
  if (descriptor == null) {
    if (typeof legacy === "string") {
      const file = safeFile(legacy, `${state}.sheet`, problems);
      return file ? { type: "strip", file, width: null } : null;
    }
    return null;
  }
  if (typeof descriptor !== "object") {
    problems.push(`${state}.source must be an object`);
    return null;
  }
  const type = descriptor.type || (typeof legacy === "string" ? "strip" : null);
  if (!SOURCE_TYPES.has(type)) {
    problems.push(`${state}.source.type must be one of ${[...SOURCE_TYPES].join(", ")}`);
    return null;
  }
  if (type === "sequence") {
    if (!Array.isArray(descriptor.files) || descriptor.files.length === 0) {
      problems.push(`${state}.source.files must list the individual PNGs`);
      return null;
    }
    if (descriptor.files.length !== frames) {
      problems.push(`${state}.source has ${descriptor.files.length} frames, expected ${frames}`);
    }
    const files = descriptor.files.map((name, index) => safeFile(name, `${state}.source.files[${index}]`, problems)).filter(Boolean);
    if (!files.length) return null;
    return { type, files };
  }
  if (descriptor.file == null) return null;
  const file = safeFile(descriptor.file, `${state}.source.file`, problems);
  if (!file) return null;
  if (type === "strip") {
    const declared = positive(descriptor.width, null);
    if (declared && declared > ctx.maxTexture) {
      problems.push(`${state}.source.width exceeds ${ctx.maxTexture}: ${declared}`);
    }
    return { type, file, width: declared || null };
  }
  const columns = Math.round(positive(descriptor.columns, 0));
  const rows = Math.round(positive(descriptor.rows, 0));
  if (!columns || !rows) {
    problems.push(`${state}.source needs positive columns and rows`);
    return null;
  }
  if (columns * rows < frames) {
    problems.push(`${state}.source atlas holds ${columns * rows} cells for ${frames} frames`);
  }
  const width = positive(descriptor.width, null);
  const height = positive(descriptor.height, null);
  if (width && height) {
    const cellW = width / columns;
    const cellH = height / rows;
    if (width > ctx.maxTexture || height > ctx.maxTexture) {
      problems.push(`${state}.source atlas ${width}x${height} exceeds ${ctx.maxTexture}`);
    }
    if (cellW < 1 || cellH < 1) {
      problems.push(`${state}.source atlas cell is empty`);
    }
    if (width !== columns * Math.round(cellW) || height !== rows * Math.round(cellH)) {
      problems.push(`${state}.source atlas ${width}x${height} does not divide evenly into ${columns}x${rows} cells`);
    }
  }
  return { type, file, columns, rows, width, height };
}

function normalizeFallback(state, raw, stateFacing, ctx, problems) {
  const descriptor = raw.fallback;
  if (descriptor == null) return null;
  if (typeof descriptor === "string") {
    const file = safeFile(descriptor, `${state}.fallback`, problems);
    return file ? { file, facing: stateFacing } : null;
  }
  if (typeof descriptor !== "object") {
    problems.push(`${state}.fallback must be a file name or object`);
    return null;
  }
  const file = descriptor.file == null ? null : safeFile(descriptor.file, `${state}.fallback.file`, problems);
  if (!file) return null;
  const facing = FACINGS.has(descriptor.facing) ? descriptor.facing : (FACINGS.has(raw.fallbackFacing) ? raw.fallbackFacing : stateFacing);
  return { file, facing };
}

function normalizeState(state, raw, ctx, problems) {
  const base = STATES[state] || STATES.idle;
  const spec = raw && typeof raw === "object" ? raw : {};
  const frames = Math.max(1, Math.round(positive(spec.frames, base.frames)));
  const fps = positive(spec.fps, base.fps);
  let durations = null;
  if (Array.isArray(spec.durations)) {
    if (spec.durations.length !== frames) {
      problems.push(`${state}.durations has ${spec.durations.length} entries, expected ${frames}`);
    } else if (!spec.durations.every((value) => Number.isFinite(Number(value)) && Number(value) > 0)) {
      problems.push(`${state}.durations must all be positive seconds`);
    } else {
      durations = spec.durations.map(Number);
    }
  }
  const uniform = frames / fps;
  const total = durations ? durations.reduce((sum, value) => sum + value, 0) : null;
  let seconds = positive(spec.seconds, total ?? uniform);
  if (durations && spec.seconds != null) {
    const declared = Number(spec.seconds);
    if (!Number.isFinite(declared) || declared <= 0 || Math.abs(declared - total) > 1e-6) {
      problems.push(`${state}.seconds must equal the sum of durations`);
      seconds = total;
    } else {
      seconds = declared;
    }
  }
  const loop = typeof spec.loop === "boolean" ? spec.loop : Boolean(base.loop);
  const facing = FACINGS.has(spec.facing) ? spec.facing : ctx.facing;
  const anchor = point(spec.anchor) || ctx.anchor;
  let anchors = null;
  if (spec.anchors != null) {
    if (!Array.isArray(spec.anchors) || spec.anchors.length !== frames) {
      problems.push(`${state}.anchors must list ${frames} [x, y] points`);
    } else {
      const points = spec.anchors.map(point);
      if (points.some((value) => value === null)) {
        problems.push(`${state}.anchors contains an invalid [x, y] point`);
      } else {
        anchors = points;
      }
    }
  }
  const beforeSource = problems.length;
  const source = normalizeSource(state, spec, frames, ctx, problems);
  const sourceValid = problems.length === beforeSource;
  const hits = normalizeHits(spec, frames, base, problems);
  return {
    frames,
    fps,
    seconds,
    durations,
    loop,
    facing,
    anchor,
    anchors,
    hits,
    source: sourceValid ? source : null,
    fallback: normalizeFallback(state, spec, facing, ctx, problems),
  };
}

function normalizeBug(raw, fallbackEntry, maxTexture, problems) {
  if (typeof raw === "string") return { file: safeFile(raw, "bugs", problems), facing: fallbackEntry.facing, frames: 1, anchor: [0.5, 1] };
  if (!raw || typeof raw !== "object") return { ...fallbackEntry };
  const file = raw.file == null ? null : safeFile(raw.file, "bugs.file", problems);
  if (!file) return { ...fallbackEntry };
  const frames = Math.max(1, Math.round(positive(raw.frames, 1)));
  if (raw.width && positive(raw.width, 0) > maxTexture) {
    problems.push(`bug sheet width exceeds ${maxTexture}: ${raw.width}`);
  }
  return {
    file,
    facing: FACINGS.has(raw.facing) ? raw.facing : fallbackEntry.facing,
    frames,
    anchor: point(raw.anchor) || [0.5, 1],
  };
}

/**
 * Normalize a raw manifest (v1/v2/v3) into the internal shape.
 * Pure: no DOM, no network — usable from Node tests.
 */
export function normalizeManifest(raw) {
  const problems = [];
  const data = raw && typeof raw === "object" ? raw : {};
  const version = Math.round(asNumber(data.version, 2));
  if (![1, 2, 3].includes(version)) problems.push(`unsupported manifest version: ${version}`);
  const maxTexture = Math.max(256, Math.round(positive(data.maxTexture, MAX_TEXTURE)));
  const facingRaw = FACINGS.has(data.facing) ? data.facing : "right";
  if (data.facing != null && !FACINGS.has(data.facing)) problems.push(`facing must be left or right, got ${data.facing}`);
  const anchor = point(data.anchor) || [0.5, 1];
  const cell = point(data.cell) || [768, 768];
  const ctx = { facing: facingRaw, anchor, maxTexture, cell };
  const states = {};
  for (const state of Object.keys(STATES)) {
    const base = DEFAULT_MANIFEST.states[state] || {};
    const provided = data.states?.[state];
    let merged = provided == null ? { ...base } : { ...base, ...provided };
    // The built-in seconds are only a template; a manifest that declares its own
    // frames/durations must compute its own total unless it states seconds.
    if (provided && provided.seconds == null) delete merged.seconds;
    if (provided && provided.hits == null && provided.hitFrame != null) delete merged.hits;
    if (provided && provided.hits == null && provided.hitFrame == null
        && provided.frames != null && Number(provided.frames) !== base.frames) {
      delete merged.hits;
    }
    states[state] = normalizeState(state, merged, ctx, problems);
  }
  const bugsRaw = data.bugs || {};
  const bugs = {
    alive: normalizeBug(bugsRaw.alive, DEFAULT_MANIFEST.bugs.alive, maxTexture, problems),
    hit: normalizeBug(bugsRaw.hit, DEFAULT_MANIFEST.bugs.hit, maxTexture, problems),
  };
  return {
    manifest: {
      version: Math.min(3, Math.max(1, version)),
      character: typeof data.character === "string" && data.character ? data.character : DEFAULT_MANIFEST.character,
      anchor,
      facing: facingRaw,
      maxTexture,
      cell,
      states,
      bugs,
    },
    problems,
  };
}

export class SpriteLibrary {
  constructor(base = BASE, manifestUrl = MANIFEST_URL) {
    this.base = base;
    this.manifestUrl = manifestUrl;
    this.manifest = normalizeManifest(DEFAULT_MANIFEST).manifest;
    this.problems = [];
    this.images = {};
    this.inflight = new Map();
    this.failures = new Map();
    this.stateLoads = new Map();
    this.stateReady = new Set();
    this.stateGeneration = new Map();
    this.generation = 0;
    this.loaded = false;
  }

  async load() {
    try {
      const response = await fetch(this.manifestUrl, { cache: "no-cache" });
      if (response.ok) {
        const data = await response.json();
        const result = normalizeManifest(data);
        this.manifest = result.manifest;
        this.problems = result.problems;
      }
    } catch {
      this.manifest = normalizeManifest(DEFAULT_MANIFEST).manifest;
      this.problems = [];
    }
    // The scene renders before the manifest arrives; those early loads used the
    // built-in defaults. Invalidate them so states with newly declared art load
    // their real source instead of reusing a fallback-only "ready" mark.
    this.generation += 1;
    this.stateReady.clear();
    this.stateGeneration.clear();
    this.stateLoads.clear();
    await Promise.all([
      this.ensureState("idle"),
      this.loadImage(this.manifest.bugs.alive.file),
      this.loadImage(this.manifest.bugs.hit.file),
    ]);
    this.loaded = true;
  }

  loadImage(name) {
    if (!name) return Promise.resolve(null);
    const cached = this.images[name];
    if (cached) return Promise.resolve(cached);
    if (this.inflight.has(name)) return this.inflight.get(name);
    const failedAt = this.failures.get(name);
    if (failedAt && Date.now() - failedAt < LOAD_RETRY_MS) return Promise.resolve(null);
    const promise = new Promise((resolve) => {
      const image = new Image();
      image.decoding = "async";
      image.onload = () => {
        this.images[name] = image;
        this.failures.delete(name);
        resolve(image);
      };
      image.onerror = () => {
        this.failures.set(name, Date.now());
        resolve(null);
      };
      image.src = new URL(name, this.base).href;
    }).finally(() => this.inflight.delete(name));
    this.inflight.set(name, promise);
    return promise;
  }

  // Load one state's art (and its fallback) on demand. Failures are not cached
  // so a later render can retry; previously decoded images are never dropped.
  ensureState(state) {
    if (this.stateGeneration.get(state) === this.generation) return Promise.resolve();
    const cached = this.stateLoads.get(state);
    if (cached && cached.generation === this.generation) return cached.promise;
    const entry = this.entry(state);
    const names = new Set();
    if (entry.source?.type === "sequence") {
      for (const file of entry.source.files) names.add(file);
    } else if (entry.source?.file) {
      names.add(entry.source.file);
    }
    if (entry.fallback?.file) names.add(entry.fallback.file);
    const generation = this.generation;
    const promise = Promise.all([...names].map((name) => this.loadImage(name)))
      .then((images) => {
        if (generation === this.generation && images.every((image) => image && image.width > 0)) {
          this.stateGeneration.set(state, generation);
          this.stateReady.add(state);
        }
      })
      .finally(() => {
        const current = this.stateLoads.get(state);
        if (current && current.promise === promise) this.stateLoads.delete(state);
      });
    this.stateLoads.set(state, { generation, promise });
    return promise;
  }

  get anchor() {
    const [x, y] = this.manifest.anchor || [0.5, 1];
    return { x, y };
  }

  // Kept for callers that predate per-asset facing; new code should read
  // ``art.facing`` instead.
  get facesLeft() {
    return this.manifest.facing === "left";
  }

  entry(state) {
    return this.manifest.states[state] || this.manifest.states.idle || DEFAULT_MANIFEST.states.idle;
  }

  specs() {
    const specs = {};
    for (const [state, entry] of Object.entries(this.manifest.states)) {
      specs[state] = {
        frames: entry.frames,
        fps: entry.fps,
        seconds: entry.seconds,
        durations: entry.durations,
        loop: entry.loop,
        hits: entry.hits,
        hitFrame: entry.hits.length ? entry.hits[0].frame : undefined,
      };
    }
    return specs;
  }

  durationOf(state) {
    const entry = this.entry(state);
    return entry.seconds || specDuration(STATES[state] || STATES.idle);
  }

  // Milliseconds into the state at which the first hit event fires.
  hitSeconds(state) {
    const entry = this.entry(state);
    if (!entry.hits || !entry.hits.length) return null;
    const frame = Math.min(...entry.hits.map((hit) => hit.frame));
    if (entry.durations && entry.durations.length === entry.frames) {
      return entry.durations.slice(0, frame).reduce((sum, value) => sum + value, 0);
    }
    return frame / (entry.fps > 0 ? entry.fps : 12);
  }

  artFor(state) {
    this.ensureState(state);
    const entry = this.entry(state);
    const art = this.primaryArt(entry) || this.fallbackArt(entry);
    if (art) return art;
    if (state !== "idle") {
      const idle = this.entry("idle");
      return this.primaryArt(idle) || this.fallbackArt(idle);
    }
    return null;
  }

  primaryArt(entry) {
    const source = entry?.source;
    if (!source) return null;
    if (source.type === "sequence") {
      const images = source.files.map((file) => this.images[file] || null);
      if (!images.length || images.some((image) => !image || !image.width)) return null;
      return this.makeArt(entry, "sequence", {
        images,
        frames: images.length,
        sheet: true,
        rect(index) {
          const image = images[Math.min(index, images.length - 1)];
          return { image, sx: 0, sy: 0, sw: image.width, sh: image.height };
        },
      });
    }
    const image = this.images[source.file];
    if (!image || !image.width || !image.height) return null;
    if (source.type === "atlas") {
      const columns = Math.max(1, Math.round(source.columns));
      const rows = Math.max(1, Math.round(source.rows));
      if (entry.frames > columns * rows) return null;
      const frameWidth = image.width / columns;
      const frameHeight = image.height / rows;
      if (frameWidth < 1 || frameHeight < 1) return null;
      return this.makeArt(entry, "atlas", {
        image,
        frames: entry.frames,
        sheet: true,
        columns,
        rows,
        frameWidth,
        frameHeight,
        rect(index) {
          const cell = Math.min(index, entry.frames - 1);
          return {
            image,
            sx: (cell % columns) * frameWidth,
            sy: Math.floor(cell / columns) * frameHeight,
            sw: frameWidth,
            sh: frameHeight,
          };
        },
      });
    }
    const frames = Math.max(1, entry.frames);
    const frameWidth = image.width / frames;
    if (!(frameWidth >= 1)) return null;
    return this.makeArt(entry, "strip", {
      image,
      frames,
      sheet: true,
      frameWidth,
      frameHeight: image.height,
      rect(index) {
        const cell = Math.min(index, frames - 1);
        return { image, sx: cell * frameWidth, sy: 0, sw: frameWidth, sh: image.height };
      },
    });
  }

  fallbackArt(entry) {
    const file = entry?.fallback?.file;
    const image = file ? this.images[file] : null;
    if (!image || !image.width) return null;
    return this.makeArt(entry, "fallback", {
      image,
      frames: 1,
      sheet: false,
      facing: entry.fallback.facing,
      rect() {
        return { image, sx: 0, sy: 0, sw: image.width, sh: image.height };
      },
    });
  }

  makeArt(entry, kind, extra) {
    if (!extra.facing) extra.facing = entry.facing;
    extra.kind = kind;
    extra.anchor = entry.anchor;
    extra.anchors = entry.anchors;
    extra.anchorAt = (index) => (entry.anchors && entry.anchors[index]) || entry.anchor;
    return extra;
  }

  bugArt(state = "alive") {
    const entry = this.manifest.bugs[state] || this.manifest.bugs.alive;
    if (!entry?.file) return null;
    const image = this.images[entry.file];
    if (!image) {
      this.loadImage(entry.file);
      return null;
    }
    const frames = Math.max(1, entry.frames || 1);
    return {
      image,
      facing: entry.facing || "left",
      frames,
      rect(index = 0) {
        const cell = Math.min(index, frames - 1);
        const width = image.width / frames;
        return { image, sx: cell * width, sy: 0, sw: width, sh: image.height };
      },
    };
  }
}
