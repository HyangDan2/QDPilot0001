// Animation state machine for the Hyangdani sprite.
//
// Specs come from the manifest (via SpriteLibrary.specs) but fall back to the
// built-in table so the engine runs before any manifest has loaded. The
// animator only tracks time; SpriteLibrary decides how to slice the loaded art.

export const STATES = {
  idle:    { frames: 8,  fps: 8,  loop: true,  seconds: 1.0 },
  run:     { frames: 12, fps: 12, loop: true,  seconds: 1.0 },
  attack:  { frames: 12, fps: 16, loop: false, seconds: 0.75, hitFrame: 6 },
  repair:  { frames: 8,  fps: 10, loop: true,  seconds: 0.8 },
  levelup: { frames: 12, fps: 12, loop: false, seconds: 1.0 },
  install: { frames: 12, fps: 12, loop: false, seconds: 1.0 },
};

export const HERO_STATES = Object.keys(STATES);

export function specDuration(spec) {
  if (!spec) return 0;
  if (Number.isFinite(spec.seconds) && spec.seconds > 0) return spec.seconds;
  if (Array.isArray(spec.durations) && spec.durations.length) {
    return spec.durations.reduce((sum, value) => sum + value, 0);
  }
  const frames = Math.max(1, Number(spec.frames) || 1);
  const fps = Number(spec.fps) > 0 ? Number(spec.fps) : 12;
  return frames / fps;
}

export class Animator {
  constructor(state = "idle", specs = STATES) {
    this.specs = { ...STATES, ...specs };
    this.state = state;
    this.elapsed = 0;
    this.done = false;
    this.hitFired = false;
  }

  setSpecs(specs) {
    this.specs = { ...STATES, ...(specs || {}) };
  }

  spec(state = this.state) {
    return this.specs[state] || STATES[state] || STATES.idle;
  }

  duration(state = this.state) {
    return specDuration(this.spec(state));
  }

  play(state, { restart = false } = {}) {
    if (!this.spec(state)) state = "idle";
    if (state === this.state && !restart) return;
    this.state = state;
    this.elapsed = 0;
    this.done = false;
    this.hitFired = false;
  }

  update(dt) {
    if (this.done) return this.snapshot();
    const spec = this.spec();
    this.elapsed += dt;
    if (!spec.loop && this.elapsed >= this.duration()) {
      this.elapsed = this.duration();
      this.done = true;
    }
    return this.snapshot();
  }

  // Returns true exactly once when a one-shot animation reaches its hit frame.
  consumeHit() {
    const spec = this.spec();
    if (spec.loop || this.hitFired) return false;
    const hitFrame = this.hitFrame(spec);
    if (hitFrame === null) return false;
    if (this.frameIndex() < hitFrame) return false;
    this.hitFired = true;
    return true;
  }

  hitFrame(spec = this.spec()) {
    if (Array.isArray(spec.hits) && spec.hits.length) {
      const frames = spec.hits
        .map((hit) => (typeof hit === "number" ? hit : hit.frame))
        .filter((frame) => Number.isFinite(frame));
      return frames.length ? Math.min(...frames) : null;
    }
    return Number.isFinite(spec.hitFrame) ? spec.hitFrame : null;
  }

  frameIndex() {
    const spec = this.spec();
    const frames = Math.max(1, Number(spec.frames) || 1);
    if (Array.isArray(spec.durations) && spec.durations.length === frames) {
      const total = spec.durations.reduce((sum, value) => sum + value, 0);
      if (total <= 0) return 0;
      let t = spec.loop ? this.elapsed % total : Math.min(this.elapsed, total);
      for (let i = 0; i < spec.durations.length; i += 1) {
        if (t < spec.durations[i]) return i;
        t -= spec.durations[i];
      }
      return frames - 1;
    }
    const fps = Number(spec.fps) > 0 ? Number(spec.fps) : 12;
    if (spec.loop) return Math.floor((this.elapsed * fps) % frames);
    return Math.min(frames - 1, Math.floor(this.elapsed * fps));
  }

  snapshot() {
    return { state: this.state, frame: this.frameIndex(), elapsed: this.elapsed, done: this.done };
  }
}
