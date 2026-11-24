// Animation state machine for the Hyangdani sprite.
//
// Each state declares its target sheet (frame count, fps, loop, hit frame) and
// the single-image fallback used until that sheet ships. The animator only
// tracks time; SpriteLibrary decides whether to slice a sheet or draw the
// fallback, so the engine works with the current single-frame art.

export const STATES = {
  idle:    { frames: 8,  fps: 8,  loop: true,  seconds: 1.0 },
  run:     { frames: 12, fps: 12, loop: true,  seconds: 1.0 },
  attack:  { frames: 12, fps: 16, loop: false, seconds: 0.75, hitFrame: 6 },
  repair:  { frames: 8,  fps: 10, loop: true,  seconds: 0.8 },
  levelup: { frames: 12, fps: 12, loop: false, seconds: 1.0 },
  install: { frames: 12, fps: 12, loop: false, seconds: 1.0 },
};

export const HERO_STATES = Object.keys(STATES);

export class Animator {
  constructor(state = "idle") {
    this.state = state;
    this.elapsed = 0;
    this.done = false;
    this.hitFired = false;
  }

  play(state, { restart = false } = {}) {
    if (!STATES[state]) state = "idle";
    if (state === this.state && !restart) return;
    this.state = state;
    this.elapsed = 0;
    this.done = false;
    this.hitFired = false;
  }

  update(dt) {
    if (this.done) return this.snapshot();
    const spec = STATES[this.state];
    this.elapsed += dt;
    if (!spec.loop && this.elapsed >= spec.seconds) {
      this.elapsed = spec.seconds;
      this.done = true;
    }
    return this.snapshot();
  }

  // Returns true exactly once when the attack reaches its hit frame.
  consumeHit() {
    const spec = STATES[this.state];
    if (!spec.hitFrame || this.hitFired || spec.loop) return false;
    const frame = this.frameIndex();
    if (frame < spec.hitFrame) return false;
    this.hitFired = true;
    return true;
  }

  frameIndex() {
    const spec = STATES[this.state];
    if (spec.loop) return Math.floor((this.elapsed * spec.fps) % spec.frames);
    return Math.min(spec.frames - 1, Math.floor(this.elapsed * spec.fps));
  }

  snapshot() {
    return { state: this.state, frame: this.frameIndex(), elapsed: this.elapsed, done: this.done };
  }
}
