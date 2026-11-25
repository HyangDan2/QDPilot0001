// HD2Lab Idle Ops — tiny dependency-free canvas scene.
// Fixed 60 Hz simulation, requestAnimationFrame rendering, DPR capped at 2,
// paused while the tab is hidden, static when the user prefers reduced motion.

const TAU = Math.PI * 2;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const rand = (min, max) => min + Math.random() * (max - min);
const pick = (list) => list[Math.floor(Math.random() * list.length)];

const TIERS = {
  cache: { label: "캐시", color: "#6ee7f7", cap: 3, priority: 0, height: 0.105, units: 3 },
  app: { label: "앱", color: "#7ef7b1", cap: 3, priority: 1, height: 0.135, units: 4 },
  db: { label: "DB", color: "#ffd166", cap: 2, priority: 2, height: 0.17, units: 4 },
  gpu: { label: "GPU", color: "#b58bff", cap: 2, priority: 3, height: 0.2, units: 5 },
  quantum: { label: "양자", color: "#ff7ce5", cap: 1, priority: 4, height: 0.235, units: 5 },
};
const TIER_ORDER = ["cache", "app", "db", "gpu", "quantum"];
const SPRITES = {
  idle: "sprites/hyangdani-idle.png",
  run: "sprites/hyangdani-run.png",
  strike: "sprites/hyangdani-strike.png",
  bug: "sprites/bug-alive.png",
  bugHit: "sprites/bug-hit.png",
};

function spriteUrl(name) {
  return new URL(SPRITES[name], import.meta.url).href;
}

export class WorldScene {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: false });
    this.images = {};
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
    this.hero = {
      x: 0, y: 0, targetX: 0, targetY: 0,
      mode: "idle", timer: 1.2, dir: -1, target: null, impact: 0,
    };
    this.placed = false;
    this.bugs = [];
    this.particles = [];
    this.texts = [];
    this.rings = [];
    this.slashes = [];
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

    this.resize = this.resize.bind(this);
    this.frame = this.frame.bind(this);
    this.visibility = this.visibility.bind(this);

    this.resize();
    window.addEventListener("resize", this.resize);
    window.addEventListener("orientationchange", this.resize);
    window.visualViewport?.addEventListener?.("resize", this.resize);
    if ("ResizeObserver" in window) {
      this.resizeObserver = new ResizeObserver(this.resize);
      this.resizeObserver.observe(this.canvas);
    }
    document.addEventListener("visibilitychange", this.visibility);
    this.loadSprites();
    this.start();
  }

  loadSprites() {
    const names = Object.keys(SPRITES);
    let pending = names.length;
    for (const name of names) {
      const image = new Image();
      image.decoding = "async";
      image.onload = () => {
        this.images[name] = image;
        if (--pending === 0) this.ready();
      };
      image.onerror = () => {
        this.images[name] = null;
        if (--pending === 0) this.ready();
      };
      image.src = spriteUrl(name);
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
      this.particles = [];
      this.texts = [];
      this.rings = [];
      this.slashes = [];
      this.shake = 0;
      this.hero.mode = "idle";
      this.homeHero();
    } else {
      this.spawnTimer = 2;
    }
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
    if (document.hidden) {
      this.stop();
    } else {
      this.start();
    }
  }

  resize() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
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
    if (!this.placed) this.homeHero();
    else this.clampHero();
    this.layoutRacks();
    this.render();
  }

  floorBounds() {
    const bottomPad = this.width <= 640 ? 124 : 72;
    const top = this.floorY + Math.min(this.height * 0.06, 42);
    const bottom = Math.max(top + 28, Math.min(this.height * 0.86, this.height - bottomPad));
    return { top, bottom };
  }

  homeHero() {
    const home = this.heroSpot();
    this.hero.x = home.x;
    this.hero.y = home.y;
    this.hero.targetX = home.x;
    this.hero.targetY = home.y;
    this.placed = true;
  }

  heroSpot() {
    const { top, bottom } = this.floorBounds();
    return {
      x: clamp(this.width * 0.5, 60, this.width - 60),
      y: clamp(this.floorY + Math.min(this.height * 0.14, 84), top, bottom),
    };
  }

  clampHero() {
    const { top, bottom } = this.floorBounds();
    this.hero.x = clamp(this.hero.x, 40, Math.max(40, this.width - 40));
    this.hero.y = clamp(this.hero.y, top, bottom);
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
    const width = this.width;
    const budget = width < 520 ? 3 : width < 760 ? 5 : width < 1080 ? 7 : 9;
    const present = TIER_ORDER.filter((tier) => (counts[tier] || 0) > 0);
    const slots = [];
    if (!present.length) present.push("cache");
    const priority = [...present].sort((a, b) => TIERS[b].priority - TIERS[a].priority);
    for (const tier of priority) {
      if (slots.length < budget) slots.push({ tier, index: 0 });
      if (slots.length >= budget) break;
    }
    let next = 0;
    while (slots.length < budget) {
      const tier = present[next % present.length];
      const used = slots.filter((slot) => slot.tier === tier).length;
      if (used < TIERS[tier].cap) {
        slots.push({ tier, index: used });
      } else if (used >= TIERS[tier].cap) {
        // All groups at cap: stop rather than loop forever.
        if (present.every((t) => slots.filter((slot) => slot.tier === t).length >= TIERS[t].cap)) break;
      }
      next += 1;
    }
    slots.sort((a, b) => TIERS[a.tier].priority - TIERS[b.tier].priority || a.index - b.index);

    const groups = {};
    for (const tier of present) groups[tier] = slots.filter((slot) => slot.tier === tier).length;
    const perGroup = {};
    for (const tier of present) {
      const total = counts[tier] || 1;
      const n = groups[tier] || 1;
      perGroup[tier] = [];
      const base = Math.floor(total / n);
      const extra = total % n;
      for (let i = 0; i < n; i += 1) perGroup[tier].push(base + (i < extra ? 1 : 0));
    }

    const count = slots.length;
    const margin = width * (width < 640 ? 0.08 : 0.11);
    const usable = Math.max(60, width - margin * 2);
    const maxW = width < 640 ? 78 : 108;
    const minGap = width < 640 ? 10 : 14;
    const gap = count > 1 ? clamp((usable - count * maxW) / (count - 1), minGap, 34) : 0;
    const rackW = Math.min(maxW, count > 1 ? (usable - gap * (count - 1)) / count : maxW * 1.1);
    const totalW = rackW * count + gap * Math.max(0, count - 1);
    const startX = (width - totalW) / 2;

    this.racks = slots.map((slot, index) => {
      const tier = TIERS[slot.tier];
      const groupCount = perGroup[slot.tier][slot.index] ?? counts[slot.tier] ?? 0;
      return {
        tier: slot.tier,
        def: tier,
        x: startX + index * (rackW + gap) + rackW / 2,
        w: rackW,
        h: this.height * tier.height,
        base: this.floorY + 2 + (slot.index % 2) * 2,
        total: counts[slot.tier] || 0,
        groupCount,
        phase: index * 0.83,
        pulse: 0,
        installing: 0,
      };
    });
    this.emptySlot = this.racks.length < budget
      ? { x: startX + count * (rackW + gap) + rackW * 0.55, y: this.floorY, w: rackW * 0.9 }
      : null;
    if (this.emptySlot && this.emptySlot.x + this.emptySlot.w / 2 > width - 12) this.emptySlot = null;
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
      this.render();
    } else if (this.dirty) {
      this.render();
    }
    this.raf = requestAnimationFrame(this.frame);
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
    this.updateHero(dt);
    this.updateParticles(dt);
    this.spawnTimer -= dt;
    if (this.spawnTimer <= 0 && this.bugs.length < 3 && this.rps > 0) {
      this.spawnBug();
      this.spawnTimer = rand(6.5, 12.5);
    }
  }

  updateBugs(dt) {
    const { top: floorTop, bottom: floorBottom } = this.floorBounds();
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

  updateHero(dt) {
    const hero = this.hero;
    const floorTop = this.floorY + this.height * 0.075;
    const floorBottom = this.height * 0.88;
    if (hero.mode === "idle") {
      hero.hop = 0;
      hero.timer -= dt;
      if (hero.timer <= 0) this.chooseTask();
      return;
    }
    if (hero.mode === "run") {
      const dx = hero.targetX - hero.x;
      const dy = hero.targetY - hero.y;
      const dist = Math.hypot(dx, dy);
      const speed = this.width * 0.32;
      if (dist < 6) {
        hero.x = hero.targetX;
        hero.y = hero.targetY;
        if (hero.target && hero.target.kind === "bug") {
          hero.mode = "attack";
          hero.timer = 0.52;
          hero.impact = 0;
        } else {
          hero.mode = "fix";
          hero.timer = 0.55;
        }
        return;
      }
      const step = Math.min(dist, speed * dt);
      hero.x += (dx / dist) * step;
      hero.y += (dy / dist) * step;
      hero.dir = dx >= 0 ? 1 : -1;
      hero.hop = Math.abs(Math.sin(this.time * 12)) * -7;
      hero.dust = (hero.dust || 0) - dt;
      if (hero.dust <= 0) {
        hero.dust = 0.09;
        this.particle(hero.x - hero.dir * 14, hero.y - 4, {
          vx: -hero.dir * rand(10, 34),
          vy: rand(-16, -2),
          size: rand(1.5, 3.4),
          life: rand(0.35, 0.7),
          color: "rgba(150,140,220,0.55)",
          gravity: 20,
        });
      }
      return;
    }
    if (hero.mode === "attack") {
      hero.timer -= dt;
      const elapsed = 0.52 - hero.timer;
      if (hero.impact === 0 && elapsed > 0.16) {
        hero.impact = 1;
        this.hitTarget();
      }
      if (hero.timer <= 0) {
        hero.mode = "idle";
        hero.timer = rand(0.7, 1.6);
        hero.hop = 0;
      }
      return;
    }
    if (hero.mode === "fix") {
      hero.timer -= dt;
      if (hero.timer <= 0) {
        this.burst(hero.x, hero.y - this.bugSize * 0.8, "#8b6bff", 8, 110);
        this.ring(hero.x, hero.y - this.bugSize * 0.8, "#8b6bff", 46);
        hero.hop = 0;
        hero.mode = "idle";
        hero.timer = rand(0.5, 1.3);
      }
    }
  }

  chooseTask() {
    const hero = this.hero;
    const { top: floorTop, bottom: floorBottom } = this.floorBounds();
    const alive = this.bugs.filter((bug) => bug.state === "alive");
    if (alive.length) {
      const target = pick(alive);
      hero.target = { kind: "bug", ref: target };
      hero.targetX = target.x;
      hero.targetY = clamp(target.y + 2, floorTop, floorBottom);
      hero.mode = "run";
      hero.timer = 0;
      return;
    }
    const racks = this.racks.filter((rack) => rack.tier !== "cache");
    const rack = racks.length ? pick(racks) : this.racks[0];
    hero.target = { kind: "rack", ref: rack };
    hero.targetX = clamp((rack ? rack.x : this.width * 0.5) + rand(-30, 30), 40, this.width - 40);
    hero.targetY = clamp((rack ? rack.base : this.floorY) + rand(30, 70), floorTop, floorBottom);
    hero.mode = "run";
    hero.timer = 0;
  }

  spawnBug(xRatio) {
    const { top: floorTop, bottom: floorBottom } = this.floorBounds();
    const x = this.width * (xRatio ?? rand(0.16, 0.84));
    this.bugs.push({
      x,
      y: rand(floorTop, floorBottom),
      vx: rand(10, 34) * (Math.random() < 0.5 ? -1 : 1),
      phase: rand(0, TAU),
      age: 0,
      state: "alive",
      dead: 0,
      dir: 1,
      bob: 0,
      flash: 0,
    });
  }

  get bugSize() {
    return clamp(this.height * 0.085, 42, 80);
  }

  hitTarget() {
    const target = this.hero.target?.ref;
    if (!target || this.bugs.indexOf(target) === -1) {
      this.slash(this.hero.x + this.hero.dir * 30, this.hero.y - 80, this.hero.dir);
      this.shake = Math.max(this.shake, 2.5);
      return;
    }
    target.state = "hit";
    target.dead = 0;
    const hitX = target.x;
    const hitY = target.y - this.bugSize * 0.5;
    this.slash(hitX, hitY, this.hero.dir);
    this.burst(hitX, hitY, "#b58bff", 18, 220);
    this.burst(hitX, hitY, "#ffffff", 7, 150);
    this.floatText(hitX + rand(-8, 8), hitY - 12, String(Math.round(rand(18, 240) * Math.max(1, this.rps / 40))), "#ffe6a3");
    this.ring(hitX, hitY, "#8b6bff", 56);
    this.shake = Math.max(this.shake, 5);
    this.hero.timer = Math.min(this.hero.timer, 0.34);
  }

  burst(x, y, color, count, speed) {
    for (let i = 0; i < count; i += 1) {
      const angle = rand(0, TAU);
      const velocity = rand(speed * 0.25, speed);
      this.particle(x, y, {
        vx: Math.cos(angle) * velocity,
        vy: Math.sin(angle) * velocity - 40,
        size: rand(1.6, 4.6),
        life: rand(0.35, 0.9),
        color,
        gravity: 320,
        glow: true,
      });
    }
  }

  particle(x, y, options) {
    if (this.particles.length > 280) this.particles.shift();
    this.particles.push({
      x, y,
      vx: options.vx || 0,
      vy: options.vy || 0,
      size: options.size || 2,
      life: options.life || 0.6,
      max: options.life || 0.6,
      color: options.color || "#fff",
      gravity: options.gravity ?? 0,
      glow: Boolean(options.glow),
    });
  }

  updateParticles(dt) {
    for (const particle of this.particles) {
      particle.life -= dt;
      particle.vy += particle.gravity * dt;
      particle.x += particle.vx * dt;
      particle.y += particle.vy * dt;
    }
    this.particles = this.particles.filter((particle) => particle.life > 0);
    for (const text of this.texts) {
      text.life -= dt;
      text.y -= 44 * dt;
    }
    this.texts = this.texts.filter((text) => text.life > 0);
    for (const ring of this.rings) {
      ring.life -= dt;
      ring.r += ring.speed * dt;
    }
    this.rings = this.rings.filter((ring) => ring.life > 0);
    for (const slash of this.slashes) slash.life -= dt;
    this.slashes = this.slashes.filter((slash) => slash.life > 0);
  }

  floatText(x, y, text, color) {
    this.texts.push({ x, y, text, color, life: 0.9, max: 0.9, size: rand(15, 20) });
  }

  ring(x, y, color, maxRadius) {
    this.rings.push({ x, y, r: 4, speed: maxRadius * 2.2, life: 0.5, max: 0.5, color });
  }

  slash(x, y, dir) {
    this.slashes.push({ x, y, dir, life: 0.28, max: 0.28, angle: rand(-0.4, 0.2) });
  }

  onInstall(tier, amount) {
    const rack = this.racks.find((item) => item.tier === tier);
    if (rack) {
      rack.pulse = 1;
      rack.installing = 1;
      this.ring(rack.x, rack.base - rack.h * 0.4, TIERS[tier].color, rack.w * 1.3);
      this.burst(rack.x, rack.base - 6, TIERS[tier].color, 12, 130);
    }
    this.floatText(this.width * 0.5, this.height * 0.5, `+${amount} ${TIERS[tier].label}`, TIERS[tier].color);
    this.dirty = true;
  }

  handleEvent(event) {
    if (!event || !event.kind) return;
    this.dirty = true;
    if (event.kind === "levelup") {
      const x = this.hero.x;
      const y = this.hero.y - this.height * 0.22;
      this.ring(x, y, "#ffd166", 140);
      this.ring(x, y, "#8b6bff", 190);
      this.burst(x, y, "#ffd166", 28, 260);
      this.floatText(this.width * 0.5, this.height * 0.42, `LEVEL ${event.level ?? this.level}`, "#ffe9a8");
      this.shake = 8;
      this.flash = 0.35;
    } else if (event.kind === "upgrade") {
      this.ring(this.width * 0.5, this.floorY - 10, "#6ee7f7", 260);
      this.burst(this.width * 0.5, this.floorY - 40, "#6ee7f7", 20, 180);
      for (const rack of this.racks) rack.pulse = Math.max(rack.pulse, 0.7);
    } else if (event.kind === "achievement") {
      this.burst(this.width * 0.82, this.height * 0.18, "#ffd166", 24, 150);
    } else if (event.kind === "reboot" || event.kind === "reset") {
      this.flash = 0.65;
      this.shake = 10;
      for (const bug of this.bugs) {
        this.burst(bug.x, bug.y - this.bugSize * 0.4, "#b58bff", 10, 160);
      }
      this.bugs = [];
      this.rings = [];
      this.dirty = true;
    } else if (event.kind === "install") {
      const tier = event.tier;
      if (tier && TIERS[tier]) this.onInstall(tier, event.qty || 1);
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

    this.drawRoom(ctx, width, height);
    for (const rack of this.racks) this.drawRack(ctx, rack);
    if (this.emptySlot) this.drawEmptySlot(ctx, width);

    const entities = [];
    for (const bug of this.bugs) {
      entities.push({ y: bug.y, draw: () => this.drawBug(ctx, bug) });
    }
    entities.push({ y: this.hero.y, draw: () => this.drawHero(ctx) });
    entities.sort((a, b) => a.y - b.y);
    for (const entity of entities) entity.draw();

    this.drawSlashes(ctx);
    this.drawParticles(ctx);
    this.drawRings(ctx);
    this.drawTexts(ctx);
    this.drawHeat(ctx, width, height);
    if (this.flash > 0.01) {
      ctx.fillStyle = `rgba(255,255,255,${this.flash * 0.5})`;
      ctx.fillRect(0, 0, width, height);
    }
    ctx.restore();
  }

  drawRoom(ctx, width, height) {
    const wall = ctx.createLinearGradient(0, 0, 0, height);
    wall.addColorStop(0, "#090916");
    wall.addColorStop(0.35, "#101026");
    wall.addColorStop(0.62, "#171335");
    wall.addColorStop(1, "#07070f");
    ctx.fillStyle = wall;
    ctx.fillRect(0, 0, width, height);

    // rear glow
    const glow = ctx.createRadialGradient(width * 0.5, this.floorY * 0.7, 10, width * 0.5, this.floorY * 0.7, width * 0.62);
    glow.addColorStop(0, "rgba(110, 85, 255, 0.2)");
    glow.addColorStop(0.55, "rgba(80, 60, 200, 0.05)");
    glow.addColorStop(1, "rgba(0, 0, 0, 0)");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, width, height);

    // ceiling strips
    ctx.save();
    for (let i = 0; i < 3; i += 1) {
      const y = height * (0.05 + i * 0.035);
      const inset = width * (0.1 + i * 0.05);
      ctx.fillStyle = "rgba(139, 107, 255, 0.06)";
      ctx.fillRect(inset, y, width - inset * 2, 2);
      if (i === 1 && this.motion) {
        const sweep = ((this.time * 0.08) % 1.4) - 0.2;
        const x = sweep * width;
        const beam = ctx.createLinearGradient(x - 90, 0, x + 90, 0);
        beam.addColorStop(0, "rgba(110,200,255,0)");
        beam.addColorStop(0.5, "rgba(110, 200, 255, 0.08)");
        beam.addColorStop(1, "rgba(110,200,255,0)");
        ctx.fillStyle = beam;
        ctx.fillRect(x - 90, y - 4, 180, this.floorY - y);
      }
    }
    ctx.restore();

    // wall seams and panels
    ctx.save();
    const panel = width / 8;
    for (let i = 0; i < 8; i += 1) {
      const x = i * panel;
      ctx.fillStyle = i % 2 ? "rgba(255,255,255,0.012)" : "rgba(0,0,0,0.09)";
      ctx.fillRect(x, height * 0.12, panel, this.floorY - height * 0.12);
      ctx.strokeStyle = "rgba(151, 138, 255, 0.06)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, height * 0.12);
      ctx.lineTo(x, this.floorY);
      ctx.stroke();
    }
    ctx.fillStyle = "rgba(6, 6, 15, 0.85)";
    ctx.fillRect(0, height * 0.12, width, 10);
    ctx.fillStyle = "rgba(139, 107, 255, 0.12)";
    ctx.fillRect(0, this.floorY - 3, width, 3);
    ctx.restore();

    // floor
    const floor = ctx.createLinearGradient(0, this.floorY, 0, height);
    floor.addColorStop(0, "#171331");
    floor.addColorStop(0.5, "#0d0b1d");
    floor.addColorStop(1, "#07070f");
    ctx.fillStyle = floor;
    ctx.fillRect(0, this.floorY, width, height - this.floorY);

    // perspective grid
    ctx.save();
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(139, 107, 255, 0.13)";
    for (let i = -8; i <= 8; i += 1) {
      const x = width / 2 + i * width * 0.09;
      ctx.beginPath();
      ctx.moveTo(width / 2 + i * width * 0.02, this.floorY);
      ctx.lineTo(x * 1.6 - width * 0.3, height);
      ctx.stroke();
    }
    for (let i = 1; i < 7; i += 1) {
      const t = i / 7;
      const y = this.floorY + Math.pow(t, 1.7) * (height - this.floorY);
      ctx.strokeStyle = `rgba(139, 107, 255, ${0.12 - t * 0.06})`;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
    ctx.restore();

    // ambient motes
    if (this.motion) {
      ctx.save();
      for (let i = 0; i < 22; i += 1) {
        const seed = i * 97.13;
        const x = (seed * 13.7 + this.time * (6 + (i % 4) * 3)) % width;
        const y = height * 0.18 + ((seed * 7.1 + this.time * (10 + (i % 3) * 6)) % (height * 0.62));
        const alpha = 0.08 + (Math.sin(this.time * 1.4 + i) + 1) * 0.04;
        ctx.fillStyle = `rgba(160, 190, 255, ${alpha})`;
        ctx.fillRect(x, y, 1.6, 1.6);
      }
      ctx.restore();
    }
  }

  drawRack(ctx, rack) {
    const { x, base, w, h } = rack;
    const def = rack.def;
    const left = x - w / 2;
    const top = base - h;
    const pulse = rack.pulse;

    // floor glow
    const glow = ctx.createRadialGradient(x, base, 2, x, base, w * 0.9);
    glow.addColorStop(0, hexAlpha(def.color, 0.24 + pulse * 0.3));
    glow.addColorStop(1, hexAlpha(def.color, 0));
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.ellipse(x, base, w * 0.75, 14, 0, 0, TAU);
    ctx.fill();

    // body
    const body = ctx.createLinearGradient(left, top, left + w, top);
    body.addColorStop(0, "#1d1b34");
    body.addColorStop(0.5, "#2a2650");
    body.addColorStop(1, "#141227");
    ctx.fillStyle = body;
    roundRect(ctx, left, top, w, h, Math.min(8, w * 0.1));
    ctx.fill();
    ctx.strokeStyle = hexAlpha(def.color, 0.4 + pulse * 0.4);
    ctx.lineWidth = 1.2;
    roundRect(ctx, left + 0.5, top + 0.5, w - 1, h - 1, Math.min(8, w * 0.1));
    ctx.stroke();

    // top color tab
    ctx.fillStyle = hexAlpha(def.color, 0.78);
    roundRect(ctx, left + 6, top - 4, w - 12, 4, 2);
    ctx.fill();

    // unit rows with LEDs
    const pad = Math.max(5, w * 0.09);
    const innerW = w - pad * 2;
    const rows = def.units;
    const rowH = (h - 18) / rows;
    for (let i = 0; i < rows; i += 1) {
      const y = top + 10 + i * rowH;
      ctx.fillStyle = "rgba(0, 0, 0, 0.35)";
      ctx.fillRect(left + pad, y, innerW, rowH - 3);
      ctx.strokeStyle = "rgba(255, 255, 255, 0.045)";
      ctx.strokeRect(left + pad + 0.5, y + 0.5, innerW - 1, rowH - 4);
      const leds = Math.max(3, Math.floor(innerW / 12));
      for (let j = 0; j < leds; j += 1) {
        const phase = this.time * (1.4 + rack.phase * 0.2) + i * 1.7 + j * 0.9 + rack.phase;
        const on = Math.sin(phase) > (j % 3 === 0 ? -0.2 : 0.35);
        const color = j % 4 === 0 ? "#ff7c95" : def.color;
        ctx.fillStyle = on ? hexAlpha(color, 0.95) : "rgba(255,255,255,0.07)";
        const lx = left + pad + 6 + j * ((innerW - 10) / leds);
        ctx.fillRect(lx, y + rowH * 0.36, 2.6, Math.max(2, rowH * 0.16));
      }
    }

    // vent slits at the bottom
    ctx.fillStyle = "rgba(0,0,0,0.45)";
    for (let i = 0; i < 4; i += 1) {
      ctx.fillRect(left + pad, base - 7 + i * 1.4, innerW, 0.8);
    }

    // quantum core
    if (rack.tier === "quantum") {
      const pulseR = 4 + Math.sin(this.time * 4) * 1.6;
      const core = ctx.createRadialGradient(x, top + h * 0.52, 1, x, top + h * 0.52, w * 0.36);
      core.addColorStop(0, "rgba(255, 230, 255, 0.95)");
      core.addColorStop(0.3, hexAlpha(def.color, 0.7));
      core.addColorStop(1, hexAlpha(def.color, 0));
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(x, top + h * 0.52, w * 0.34 + pulseR, 0, TAU);
      ctx.fill();
    }

    // label pill
    const label = `${def.label} ×${rack.groupCount}`;
    ctx.font = `700 ${Math.max(9, Math.round(w * 0.115))}px "Pretendard", system-ui, sans-serif`;
    const textW = ctx.measureText(label).width;
    const pillW = Math.min(w - 8, textW + 12);
    const pillX = x - pillW / 2;
    const pillY = top - 18;
    ctx.fillStyle = "rgba(6, 6, 16, 0.86)";
    roundRect(ctx, pillX, pillY, pillW, 13, 6);
    ctx.fill();
    ctx.strokeStyle = hexAlpha(def.color, 0.4);
    ctx.stroke();
    ctx.fillStyle = def.color;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, x, pillY + 7);

    if (rack.total > rack.groupCount) {
      ctx.font = `600 ${Math.max(8, Math.round(w * 0.1))}px system-ui, sans-serif`;
      ctx.fillStyle = "rgba(200, 195, 235, 0.65)";
      ctx.fillText(`전체 ${rack.total}`, x, base + 11);
    }

    if (rack.installing > 0) {
      ctx.save();
      ctx.globalAlpha = Math.min(1, rack.installing * 1.6);
      ctx.strokeStyle = hexAlpha(def.color, 0.9);
      ctx.lineWidth = 2;
      roundRect(ctx, left - 3, top - 3, w + 6, h + 6, 10);
      ctx.stroke();
      ctx.restore();
    }
  }

  drawEmptySlot(ctx, width) {
    const slot = this.emptySlot;
    if (!slot) return;
    const h = this.height * 0.16;
    const left = slot.x - slot.w / 2;
    ctx.save();
    ctx.setLineDash([5, 5]);
    ctx.strokeStyle = "rgba(151, 138, 255, 0.24)";
    ctx.lineWidth = 1.2;
    roundRect(ctx, left, slot.y - h, slot.w, h, 8);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(151, 138, 255, 0.35)";
    ctx.font = '600 10px system-ui, sans-serif';
    ctx.textAlign = "center";
    ctx.fillText("빈 슬롯", slot.x, slot.y - h / 2);
    ctx.fillText("증설 대기", slot.x, slot.y - h / 2 + 13);
    ctx.restore();
    if (left < 8 || left + slot.w > width - 8) this.emptySlot = null;
  }

  drawBug(ctx, bug) {
    const size = this.bugSize;
    const bob = bug.bob || 0;
    const image = bug.state === "hit" ? this.images.bugHit : this.images.bug;
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
    const hero = this.hero;
    const height = clamp(Math.min(this.height * 0.31, this.width * 0.6), 150, 300);
    const bob = hero.mode === "idle" && this.motion ? Math.sin(this.time * 2.4) * 2.5 : 0;
    const hop = hero.hop || 0;
    const image = hero.mode === "attack" ? (this.images.strike || this.images.idle)
      : hero.mode === "run" ? (this.images.run || this.images.idle)
      : this.images.idle;
    ctx.save();
    ctx.translate(hero.x, hero.y + hop + bob);
    this.shadow(ctx, height * 0.2);
    if (image) {
      const w = image.width * (height / image.height);
      const flip = hero.dir >= 0 ? -1 : 1;
      let lunge = 0;
      let tilt = 0;
      if (hero.mode === "attack") {
        const progress = 1 - hero.timer / 0.52;
        lunge = Math.sin(progress * Math.PI) * 26 * hero.dir;
        tilt = Math.sin(progress * Math.PI) * 0.1 * hero.dir;
      }
      ctx.translate(lunge, 0);
      ctx.rotate(tilt);
      ctx.scale(flip, 1);
      ctx.drawImage(image, -w / 2, -height, w, height);
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

  drawParticles(ctx) {
    for (const particle of this.particles) {
      ctx.globalAlpha = clamp(particle.life / particle.max, 0, 1);
      ctx.fillStyle = particle.color;
      if (particle.glow) {
        ctx.shadowColor = particle.color;
        ctx.shadowBlur = 8;
      }
      ctx.fillRect(particle.x, particle.y, particle.size, particle.size);
      ctx.shadowBlur = 0;
    }
    ctx.globalAlpha = 1;
  }

  drawRings(ctx) {
    for (const ring of this.rings) {
      const alpha = clamp(ring.life / ring.max, 0, 1);
      ctx.strokeStyle = hexAlpha(ring.color, alpha * 0.8);
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.ellipse(ring.x, ring.y, ring.r, ring.r * 0.5, 0, 0, TAU);
      ctx.stroke();
    }
  }

  drawSlashes(ctx) {
    for (const slash of this.slashes) {
      const progress = 1 - slash.life / slash.max;
      const alpha = Math.sin((1 - progress) * Math.PI);
      ctx.save();
      ctx.translate(slash.x, slash.y);
      ctx.rotate(-0.55 + slash.angle);
      ctx.scale(slash.dir >= 0 ? 1 : -1, 1);
      ctx.strokeStyle = `rgba(205, 180, 255, ${alpha * 0.9})`;
      ctx.lineWidth = 7 * (1 - progress * 0.5);
      ctx.lineCap = "round";
      ctx.shadowColor = "#8b6bff";
      ctx.shadowBlur = 16;
      ctx.beginPath();
      ctx.arc(0, 0, 64, -0.9 + progress * 0.6, 0.7 + progress * 0.5);
      ctx.stroke();
      ctx.restore();
    }
  }

  drawTexts(ctx) {
    for (const text of this.texts) {
      const alpha = clamp(text.life / text.max, 0, 1);
      ctx.globalAlpha = alpha;
      ctx.font = `900 ${text.size}px "Pretendard", system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.fillStyle = text.color;
      ctx.shadowColor = "rgba(0,0,0,0.7)";
      ctx.shadowBlur = 8;
      ctx.fillText(text.text, text.x, text.y);
      ctx.shadowBlur = 0;
    }
    ctx.globalAlpha = 1;
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

  destroy() {
    this.stop();
    window.removeEventListener("resize", this.resize);
    window.removeEventListener("orientationchange", this.resize);
    window.visualViewport?.removeEventListener?.("resize", this.resize);
    this.resizeObserver?.disconnect();
    document.removeEventListener("visibilitychange", this.visibility);
    this.motionQuery.removeEventListener?.("change", this.onMotionChange);
  }
}

function roundRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

function hexAlpha(hex, alpha) {
  const value = hex.replace("#", "");
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, alpha))})`;
}
