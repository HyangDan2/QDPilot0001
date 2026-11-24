// WorldScene: fixed 60 Hz simulation, requestAnimationFrame rendering, DPR
// capped, paused while hidden, static under reduced motion.

import { TAU, clamp, rand, LIMITS, MOTION, TIERS, TIER_ORDER } from "./config.js";
import { SpriteLibrary } from "./sprites.js";
import { Effects, roundRect } from "./effects.js";
import { HeroController } from "./motion.js";
import { RoomPainter, layoutRacks, drawRack, drawEmptySlot } from "./room.js";

export class WorldScene {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: false });
    this.library = new SpriteLibrary();
    this.loaded = false;
    this.lowMotion = Boolean(options.lowMotion);
    this.motion = !window.matchMedia("(prefers-reduced-motion: reduce)").matches && !this.lowMotion;
    this.motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    this.onMotionChange = () => this.setMotion(!this.motionQuery.matches && !this.lowMotion);
    this.motionQuery.addEventListener?.("change", this.onMotionChange);

    this.state = null;
    this.derived = null;
    this.level = 1;
    this.heat = 0;
    this.rps = 0;
    this.racks = [];
    this.emptySlot = null;
    this.bugs = [];
    this.placed = false;
    this.effects = new Effects();
    this.painter = new RoomPainter();
    this.hero = new HeroController(this);
    this.animator = this.hero.animator;

    this.shake = 0;
    this.flash = 0;
    this.time = 0;
    this.spawnTimer = 2.5;
    this.dpr = 1;
    this.width = 1;
    this.height = 1;
    this.floorY = 1;
    this.raf = 0;
    this.last = 0;
    this.accumulator = 0;
    this.running = true;
    this.dirty = true;
    this.prevCounts = null;

    this.showFps = new URLSearchParams(window.location.search).has("fps");
    this.frameSamples = [];
    this.stats = { fps: 0, particles: 0, bugs: 0, racks: 0 };

    this.resize = this.resize.bind(this);
    this.frame = this.frame.bind(this);
    this.visibility = this.visibility.bind(this);

    this.resize();
    window.addEventListener("resize", this.resize);
    window.addEventListener("orientationchange", this.resize);
    document.addEventListener("visibilitychange", this.visibility);
    this.loadSprites();
    this.start();
  }

  get bugSize() {
    return clamp(this.height * 0.085, 42, 80);
  }

  async loadSprites() {
    try {
      await this.library.load();
    } finally {
      this.ready();
    }
  }

  ready() {
    this.loaded = true;
    if (this.motion) {
      this.spawnBug(0.3);
      this.spawnBug(0.72);
    }
    this.render();
  }

  setMotion(motion) {
    this.motion = Boolean(motion);
    if (!this.motion) {
      this.bugs = [];
      this.effects.clear();
      this.shake = 0;
      this.hero.reset();
    } else {
      this.spawnTimer = 2;
    }
    this.dirty = true;
    this.render();
  }

  start() {
    if (this.raf) return;
    this.running = true;
    this.last = performance.now();
    this.raf = requestAnimationFrame(this.frame);
  }

  stop() {
    this.running = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  visibility() {
    if (document.hidden) this.stop();
    else this.start();
  }

  resize() {
    const dpr = Math.min(LIMITS.dpr, window.devicePixelRatio || 1);
    const width = Math.max(1, this.canvas.clientWidth || window.innerWidth);
    const height = Math.max(1, this.canvas.clientHeight || window.innerHeight);
    if (width === this.width && height === this.height && dpr === this.dpr) return;
    this.width = width;
    this.height = height;
    this.dpr = dpr;
    this.canvas.width = Math.round(width * dpr);
    this.canvas.height = Math.round(height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.floorY = Math.round(height * 0.615);
    if (!this.placed) this.hero.home();
    else this.hero.clamp();
    this.layoutRacks();
    this.render();
  }

  heroSpot() {
    return {
      x: clamp(this.width * 0.5, 60, this.width - 60),
      y: this.floorY + Math.min(this.height * 0.16, 96),
    };
  }

  setWorld(state, derived) {
    if (!state || !derived) return;
    const previous = this.prevCounts;
    this.state = state;
    this.derived = derived;
    this.level = derived.level;
    this.rps = derived.rps;
    this.heat = clamp(derived.thermal_pct || 0, 0, 1.4);
    const counts = state.servers || {};
    this.prevCounts = { ...counts };
    this.layoutRacks();
    if (previous) {
      for (const tier of TIER_ORDER) {
        const before = previous[tier] || 0;
        const after = counts[tier] || 0;
        if (after > before) this.onInstall(tier, after - before);
        if (after < before) this.dirty = true;
      }
    }
    this.render();
  }

  layoutRacks() {
    const counts = this.state?.servers || {};
    const { racks, emptySlot } = layoutRacks({
      width: this.width,
      height: this.height,
      floorY: this.floorY,
      counts,
    });
    this.racks = racks;
    this.emptySlot = emptySlot;
    this.stats.racks = racks.length;
    this.dirty = true;
  }

  frame(timestamp) {
    if (!this.running) return;
    const delta = Math.min(0.1, (timestamp - this.last) / 1000 || 0);
    this.last = timestamp;
    if (this.motion) {
      this.accumulator += delta;
      const step = 1 / 60;
      let steps = 0;
      while (this.accumulator >= step && steps < 6) {
        this.update(step);
        this.accumulator -= step;
        steps += 1;
      }
      this.sampleFps(delta);
      this.render();
    } else if (this.dirty) {
      this.render();
    }
    this.raf = requestAnimationFrame(this.frame);
  }

  sampleFps(delta) {
    if (!delta) return;
    this.frameSamples.push(1 / delta);
    if (this.frameSamples.length > LIMITS.fpsWindow) this.frameSamples.shift();
    const total = this.frameSamples.reduce((sum, value) => sum + value, 0);
    this.stats.fps = Math.round(total / this.frameSamples.length);
  }

  update(dt) {
    this.time += dt;
    this.shake = Math.max(0, this.shake - dt * 22);
    this.flash = Math.max(0, this.flash - dt * 2.6);
    for (const rack of this.racks) {
      rack.pulse = Math.max(0, rack.pulse - dt * 1.8);
      rack.installing = Math.max(0, rack.installing - dt);
    }
    this.updateBugs(dt);
    this.hero.update(dt);
    this.effects.update(dt);
    this.spawnTimer -= dt;
    if (this.spawnTimer <= 0 && this.bugs.length < LIMITS.bugs && this.rps > 0) {
      this.spawnBug();
      this.spawnTimer = rand(...MOTION.bugSpawn);
    }
    this.stats.particles = this.effects.particles.length;
    this.stats.bugs = this.bugs.length;
  }

  updateBugs(dt) {
    const floorTop = this.floorY + this.height * 0.055;
    const floorBottom = this.height * 0.9;
    for (const bug of this.bugs) {
      bug.age += dt;
      bug.bob = Math.sin(this.time * 5 + bug.phase) * 4;
      if (bug.state === "hit") {
        bug.dead += dt;
        continue;
      }
      bug.x += bug.vx * dt;
      if (bug.x < this.width * 0.12 || bug.x > this.width * 0.88) bug.vx *= -1;
      bug.y = clamp(bug.y + Math.sin(this.time * 0.7 + bug.phase) * 1.6 * dt, floorTop, floorBottom);
      bug.dir = bug.vx < 0 ? -1 : 1;
    }
    this.bugs = this.bugs.filter((bug) => !(bug.state === "hit" && bug.dead > 0.55));
  }

  spawnBug(xRatio) {
    const x = this.width * (xRatio ?? rand(0.16, 0.84));
    this.bugs.push({
      x,
      y: rand(this.floorY + this.height * 0.09, this.height * 0.86),
      vx: rand(...MOTION.bugSpeed) * (Math.random() < 0.5 ? -1 : 1),
      phase: rand(0, TAU),
      age: 0,
      state: "alive",
      dead: 0,
      dir: 1,
      bob: 0,
      flash: 0,
    });
  }

  hitTarget() {
    const target = this.hero.hero.target?.ref;
    if (!target || this.bugs.indexOf(target) === -1) {
      this.effects.slash(this.hero.hero.x + this.hero.hero.dir * 30, this.hero.hero.y - 80, this.hero.hero.dir);
      this.shake = Math.max(this.shake, 2.5);
      return;
    }
    target.state = "hit";
    target.dead = 0;
    const hitX = target.x;
    const hitY = target.y - this.bugSize * 0.5;
    this.effects.slash(hitX, hitY, this.hero.hero.dir);
    this.effects.burst(hitX, hitY, "#b58bff", 18, 220);
    this.effects.burst(hitX, hitY, "#ffffff", 7, 150);
    this.effects.floatText(hitX + rand(-8, 8), hitY - 12, String(Math.round(rand(18, 240) * Math.max(1, this.rps / 40))), "#ffe6a3");
    this.effects.ring(hitX, hitY, "#8b6bff", 56);
    this.shake = Math.max(this.shake, 5);
    this.hero.hero.timer = Math.min(this.hero.hero.timer, 0.34);
  }

  onInstall(tier, amount) {
    const rack = this.racks.find((item) => item.tier === tier);
    if (rack) {
      rack.pulse = 1;
      rack.installing = 1;
      this.effects.ring(rack.x, rack.base - rack.h * 0.4, TIERS[tier].color, rack.w * 1.3);
      this.effects.burst(rack.x, rack.base - 6, TIERS[tier].color, 12, 130);
    }
    this.effects.floatText(this.width * 0.5, this.height * 0.5, `+${amount} ${TIERS[tier].label}`, TIERS[tier].color);
    this.hero.playOnce("install");
    this.dirty = true;
  }

  handleEvent(event) {
    if (!event || !event.kind) return;
    this.dirty = true;
    if (event.kind === "levelup") {
      const x = this.hero.hero.x;
      const y = this.hero.hero.y - this.height * 0.22;
      this.effects.ring(x, y, "#ffd166", 140);
      this.effects.ring(x, y, "#8b6bff", 190);
      this.effects.burst(x, y, "#ffd166", 28, 260);
      this.effects.floatText(this.width * 0.5, this.height * 0.42, `LEVEL ${event.level ?? this.level}`, "#ffe9a8");
      this.shake = 8;
      this.flash = 0.35;
      this.hero.playOnce("levelup");
    } else if (event.kind === "upgrade") {
      this.effects.ring(this.width * 0.5, this.floorY - 10, "#6ee7f7", 260);
      this.effects.burst(this.width * 0.5, this.floorY - 40, "#6ee7f7", 20, 180);
      for (const rack of this.racks) rack.pulse = Math.max(rack.pulse, 0.7);
    } else if (event.kind === "achievement") {
      this.effects.burst(this.width * 0.82, this.height * 0.18, "#ffd166", 24, 150);
    } else if (event.kind === "reboot" || event.kind === "reset") {
      this.flash = 0.65;
      this.shake = 10;
      for (const bug of this.bugs) {
        this.effects.burst(bug.x, bug.y - this.bugSize * 0.4, "#b58bff", 10, 160);
      }
      this.bugs = [];
      this.effects.rings = [];
      this.hero.reset();
      this.dirty = true;
    } else if (event.kind === "install") {
      if (event.tier && TIERS[event.tier]) this.onInstall(event.tier, event.qty || 1);
    }
  }

  render() {
    const ctx = this.ctx;
    const width = this.width;
    const height = this.height;
    if (!width || !height) return;
    this.dirty = false;
    ctx.save();
    const shakeX = this.shake ? rand(-this.shake, this.shake) : 0;
    const shakeY = this.shake ? rand(-this.shake, this.shake) : 0;
    ctx.translate(shakeX, shakeY);

    this.painter.draw(ctx, {
      width, height, floorY: this.floorY, dpr: this.dpr, motion: this.motion, time: this.time,
    });
    for (const rack of this.racks) drawRack(ctx, rack, this.time);
    if (this.emptySlot && !drawEmptySlot(ctx, this.emptySlot, width, height)) this.emptySlot = null;

    const entities = [];
    for (const bug of this.bugs) entities.push({ y: bug.y, draw: () => this.drawBug(ctx, bug) });
    entities.push({ y: this.hero.hero.y, draw: () => this.drawHero(ctx) });
    entities.sort((a, b) => a.y - b.y);
    for (const entity of entities) entity.draw();

    this.effects.drawSlashes(ctx);
    this.effects.drawParticles(ctx);
    this.effects.drawRings(ctx);
    this.effects.drawTexts(ctx);
    this.drawHeat(ctx, width, height);
    if (this.flash > 0.01) {
      ctx.fillStyle = `rgba(255,255,255,${this.flash * 0.5})`;
      ctx.fillRect(0, 0, width, height);
    }
    ctx.restore();
    if (this.showFps) this.drawFps(ctx);
  }

  drawBug(ctx, bug) {
    const size = this.bugSize;
    const bob = bug.bob || 0;
    const image = this.library.bugArt(bug.state === "hit" ? "hit" : "alive");
    ctx.save();
    ctx.globalAlpha = bug.state === "hit" ? Math.max(0, 1 - bug.dead / 0.55) : 1;
    ctx.translate(bug.x, bug.y + bob);
    const flip = bug.dir > 0 ? -1 : 1;
    if (bug.state === "hit") {
      ctx.rotate(-0.25 * flip);
      ctx.scale(1.08, 0.92);
    } else {
      ctx.rotate(Math.sin(this.time * 3 + bug.phase) * 0.05);
    }
    this.shadow(ctx, size * 0.38);
    if (image) {
      const h = size;
      const w = image.width * (h / image.height);
      ctx.scale(flip, 1);
      ctx.drawImage(image, -w / 2, -h, w, h);
    } else {
      this.drawFallbackBug(ctx, size, flip);
    }
    ctx.restore();
  }

  drawHero(ctx) {
    const hero = this.hero.hero;
    const state = this.hero.artState;
    const height = clamp(Math.min(this.height * 0.31, this.width * 0.6), 150, 300);
    const bob = state === "idle" && this.motion ? Math.sin(this.time * 2.4) * 2.5 : 0;
    const art = this.library.artFor(state);
    ctx.save();
    ctx.translate(hero.x, hero.y + (hero.hop || 0) + bob);
    this.shadow(ctx, height * 0.2);
    if (art?.image) {
      const frames = Math.max(1, art.frames);
      const frameW = art.image.width / frames;
      const frameH = art.image.height;
      const drawW = frameW * (height / frameH);
      const flip = (this.library.facesLeft ? hero.dir >= 0 : hero.dir < 0) ? -1 : 1;
      const anchor = this.library.anchor;
      let lunge = 0;
      let tilt = 0;
      if (state === "attack") {
        const progress = clamp(this.animator.elapsed / MOTION.attackSeconds, 0, 1);
        lunge = Math.sin(progress * Math.PI) * 26 * hero.dir;
        tilt = Math.sin(progress * Math.PI) * 0.1 * hero.dir;
      }
      ctx.translate(lunge, 0);
      ctx.rotate(tilt);
      ctx.scale(flip, 1);
      const frame = this.animator.frameIndex() % frames;
      ctx.drawImage(
        art.image,
        frame * frameW, 0, frameW, frameH,
        -drawW * anchor.x, -height * anchor.y, drawW, height,
      );
    } else {
      this.drawFallbackHero(ctx, height, hero.dir);
    }
    ctx.restore();
  }

  drawFallbackHero(ctx, height, dir) {
    const s = height / 220;
    ctx.save();
    ctx.scale(dir >= 0 ? -1 : 1, 1);
    ctx.fillStyle = "#e9e7ff";
    roundRect(ctx, -s * 22, -s * 92, s * 44, s * 84, s * 12);
    ctx.fill();
    ctx.fillStyle = "#f4f2ff";
    ctx.beginPath();
    ctx.arc(0, -s * 118, s * 26, 0, TAU);
    ctx.fill();
    ctx.fillStyle = "#c9c4ee";
    ctx.beginPath();
    ctx.arc(-s * 18, -s * 132, s * 14, 0, TAU);
    ctx.arc(s * 18, -s * 132, s * 14, 0, TAU);
    ctx.fill();
    ctx.fillStyle = "#1a1830";
    ctx.beginPath();
    ctx.arc(-s * 8, -s * 118, s * 3, 0, TAU);
    ctx.arc(s * 8, -s * 118, s * 3, 0, TAU);
    ctx.fill();
    ctx.fillStyle = "#8b6bff";
    ctx.fillRect(s * 34, -s * 70, s * 12, s * 54);
    ctx.fillRect(s * 26, -s * 76, s * 26, s * 14);
    ctx.restore();
  }

  drawFallbackBug(ctx, size, flip) {
    ctx.save();
    ctx.scale(flip, 1);
    ctx.fillStyle = "#8b5cf6";
    ctx.beginPath();
    ctx.ellipse(0, -size * 0.4, size * 0.36, size * 0.4, 0, 0, TAU);
    ctx.fill();
    ctx.fillStyle = "#ffd166";
    ctx.beginPath();
    ctx.arc(-size * 0.1, -size * 0.5, size * 0.06, 0, TAU);
    ctx.arc(size * 0.12, -size * 0.5, size * 0.06, 0, TAU);
    ctx.fill();
    ctx.restore();
  }

  shadow(ctx, radius) {
    const gradient = ctx.createRadialGradient(0, 0, 1, 0, 0, radius);
    gradient.addColorStop(0, "rgba(0,0,0,0.5)");
    gradient.addColorStop(1, "rgba(0,0,0,0)");
    ctx.save();
    ctx.scale(1, 0.28);
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.arc(0, 0, radius, 0, TAU);
    ctx.fill();
    ctx.restore();
  }

  drawHeat(ctx, width, height) {
    if (this.heat < 0.7) return;
    const intensity = clamp((this.heat - 0.7) / 0.6, 0, 1) * 0.22;
    const gradient = ctx.createRadialGradient(width / 2, this.floorY, width * 0.1, width / 2, this.floorY, width * 0.7);
    gradient.addColorStop(0, "rgba(0,0,0,0)");
    gradient.addColorStop(1, `rgba(255, 70, 90, ${intensity})`);
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);
  }

  drawFps(ctx) {
    const text = `${this.stats.fps} FPS · ${this.stats.particles}p · ${this.stats.bugs}b · ${this.stats.racks}r`;
    ctx.save();
    ctx.font = '700 11px "SFMono-Regular", ui-monospace, monospace';
    const w = ctx.measureText(text).width + 14;
    const x = (this.width - w) / 2;
    ctx.fillStyle = "rgba(6, 6, 16, 0.8)";
    roundRect(ctx, x, 76, w, 22, 6);
    ctx.fill();
    ctx.fillStyle = "#9ef7c4";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, this.width / 2, 87);
    ctx.restore();
  }

  destroy() {
    this.stop();
    window.removeEventListener("resize", this.resize);
    window.removeEventListener("orientationchange", this.resize);
    document.removeEventListener("visibilitychange", this.visibility);
    this.motionQuery.removeEventListener?.("change", this.onMotionChange);
  }
}
