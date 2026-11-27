// WorldScene: fixed 60 Hz simulation, requestAnimationFrame rendering, DPR
// capped, paused while hidden, static under reduced motion.

import { TAU, clamp, rand, LIMITS, MOTION, TIERS } from "./config.js";
import { SpriteLibrary, flipFor } from "./sprites.js";
import { Effects, roundRect } from "./effects.js";
import { HeroController } from "./motion.js";
import { Animator } from "./animation.js";
import { RoomPainter, layoutProjectRacks, rackBudget, drawRack, drawEmptySlot } from "./room.js";

const MAX_AGENTS = 4;
const MAX_ENCOUNTERS = 6;
const AGENT_COLORS = ["#6ee7f7", "#7ef7b1", "#ffd166", "#b58bff"];

function bugKey(projectId, bugId) {
  return JSON.stringify([String(projectId), String(bugId)]);
}

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
    this.dpr = 1;
    this.width = 1;
    this.height = 1;
    this.floorY = 1;
    this.raf = 0;
    this.last = 0;
    this.accumulator = 0;
    this.running = true;
    this.projects = [];
    this.visibleProjects = [];
    this.projectId = null;
    this.page = 0;
    this.agents = [];
    this.agentSprites = new Map();
    this.activeBugs = new Map();
    this.encounters = new Map();
    this.celebrations = new Map();
    this.eventCursor = 0;

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
    if (this.motion && !document.hidden) this.start();
  }

  get bugSize() {
    return clamp(this.height * 0.085, 42, 80);
  }

  // Wide states (run/attack) are ~0.78× as wide as they are tall; keep the
  // sprite fully on screen instead of letting big poses clip at the edges.
  get heroHeight() {
    return clamp(Math.min(this.height * 0.31, this.width * 0.6), 150, 300);
  }

  get heroMargin() {
    return clamp(this.heroHeight * 0.42, 60, 140);
  }

  async loadSprites() {
    try {
      await this.library.load();
    } finally {
      this.ready();
    }
  }

  ready() {
    this.animator.setSpecs(this.library.specs());
    this.loaded = true;
    for (const sprite of this.agentSprites.values()) sprite.animator.setSpecs(this.library.specs());
    this.render();
  }

  setMotion(motion) {
    this.motion = Boolean(motion);
    if (!this.motion) {
      this.stop();
      this.clearEncounters();
      this.syncBugs();
    } else if (!document.hidden) {
      this.start();
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
      this.clearEncounters();
      this.syncBugs();
    } else if (this.motion) {
      this.start();
    }
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
    this.syncBugs();
    for (const bug of this.bugs) {
      bug.x = clamp(bug.x, width * 0.12, width * 0.88);
      bug.y = clamp(bug.y, this.floorY + height * 0.055, height * 0.9);
    }
    this.render();
  }

  heroSpot() {
    const margin = this.heroMargin;
    return {
      x: clamp(this.width * 0.73, margin, Math.max(margin, this.width - margin)),
      y: this.floorY + Math.min(this.height * 0.21, 126),
    };
  }

  get pageCount() {
    return Math.max(1, Math.ceil(this.projects.length / rackBudget(this.width)));
  }

  setPage(index) {
    const next = clamp(Math.trunc(Number(index) || 0), 0, this.pageCount - 1);
    if (next === this.page) return;
    this.page = next;
    this.clearEncounters();
    this.layoutRacks();
    this.syncBugs();
    this.render();
  }

  setSnapshot(payload, { projectId = null, animate = false } = {}) {
    const selected = projectId == null || projectId === "" ? null : String(projectId);
    if (selected !== this.projectId) {
      this.projectId = selected;
      this.page = 0;
      this.clearEncounters();
    }
    if (payload.cursorReset || payload.eventGap) {
      this.eventCursor = 0;
      this.clearEncounters();
    }
    this.projects = (payload.projects || []).filter((project) => selected === null || selected === String(project.id));
    this.agents = payload.agents || [];
    this.layoutRacks();

    const events = payload.events || [];
    if (animate && this.motion && !document.hidden && !payload.cursorReset && !payload.eventGap) {
      this.queueEvents(events);
    }
    for (const event of events) this.eventCursor = Math.max(this.eventCursor, Number(event.id) || 0);
    this.eventCursor = Math.max(this.eventCursor, Number(payload.cursor) || 0);
    this.syncBugs();
    this.render();
  }

  layoutRacks() {
    const geometry = { width: this.width, height: this.height, floorY: this.floorY };
    this.page = Math.min(this.page, this.pageCount - 1);
    const budget = rackBudget(this.width);
    this.visibleProjects = this.projects.slice(this.page * budget, (this.page + 1) * budget);
    const layout = layoutProjectRacks({ ...geometry, projects: this.visibleProjects });
    this.activeBugs = new Map(this.visibleProjects.flatMap((project) =>
      (project.bugs || []).map((bug) => [bugKey(project.id, bug.id), { projectId: project.id }])
    ));
    for (const rack of layout.racks) {
      if (!rack.projectId) continue;
      const previous = this.racks.find((item) => item.projectId === rack.projectId);
      if (previous && previous.tier === rack.tier) {
        rack.pulse = previous.pulse;
        rack.installing = previous.installing;
      }
    }
    this.racks = layout.racks;
    this.emptySlot = layout.emptySlot;
    this.stats.racks = this.racks.length;
  }

  clearEncounters() {
    this.encounters.clear();
    this.celebrations.clear();
    this.bugs = [];
    this.effects.clear();
    this.stats.particles = 0;
    this.flash = 0;
    this.shake = 0;
    this.hero.reset();
  }

  queueEvents(events) {
    const visible = new Set(this.visibleProjects.map((project) => String(project.id)));
    const latest = new Map();
    const fresh = events.filter((event) => Number(event.id) > this.eventCursor)
      .sort((a, b) => Number(a.id) - Number(b.id));
    for (const event of fresh) {
      if (event.projectId != null && !visible.has(String(event.projectId))) continue;
      if (["bug_opened", "bug_resolved", "bug_dismissed"].includes(event.type) && event.entityId != null) {
        latest.set(bugKey(event.projectId, event.entityId), event);
      } else if (["install", "levelup", "reboot"].includes(event.type)) {
        this.celebrations.set(event.type, event);
      }
    }
    for (const [key, event] of latest) {
      if (event.type !== "bug_resolved" || this.activeBugs.has(key)) {
        this.encounters.delete(key);
      } else if (this.encounters.size < MAX_ENCOUNTERS && !this.bugs.some((bug) => bug.key === key && bug.state === "hit")) {
        this.encounters.set(key, { projectId: event.projectId });
      }
    }
  }

  syncBugs() {
    const visible = new Set(this.visibleProjects.map((project) => String(project.id)));
    for (const [key, encounter] of this.encounters) {
      if (this.activeBugs.has(key) || !visible.has(String(encounter.projectId))) this.encounters.delete(key);
    }
    const hits = this.bugs.filter((bug) => bug.state === "hit" && !this.activeBugs.has(bug.key)
      && visible.has(String(bug.projectId)));
    const keep = new Set(hits.map((bug) => bug.key));
    const alive = new Map(this.bugs.filter((bug) => bug.state === "alive").map((bug) => [bug.key, bug]));
    this.bugs = hits;
    for (const records of [this.encounters, this.activeBugs]) {
      for (const [key, record] of records) {
        if (this.bugs.length >= LIMITS.bugs) break;
        if (keep.has(key)) continue;
        const old = alive.get(key);
        if (old) this.bugs.push(old);
        else this.spawnBug(key, record.projectId);
        keep.add(key);
      }
    }
    this.stats.bugs = this.bugs.length;
  }

  canAttack(bug) {
    return this.bugs.includes(bug) && bug.state === "alive" && this.encounters.has(bug.key);
  }

  repairTarget(key = null) {
    const worker = this.agents.find((agent) => agent.bugId != null && !agent.stale && ["coding", "testing"].includes(agent.status)
      && this.activeBugs.has(bugKey(agent.projectId, agent.bugId))
      && (key === null || key === bugKey(agent.projectId, agent.bugId)));
    if (!worker) return null;
    const rack = this.racks.find((item) => item.projectId === String(worker.projectId));
    return rack ? { rack, key: bugKey(worker.projectId, worker.bugId) } : null;
  }

  playCelebration() {
    if (!this.celebrations.size || this.hero.hero.mode !== "idle" || this.hero.oneshot) return;
    const [kind, event] = this.celebrations.entries().next().value;
    this.celebrations.delete(kind);
    if (event.projectId != null && !this.racks.some((rack) => rack.projectId === String(event.projectId))) return;
    this.handleEvent({ ...event, kind });
  }

  frame(timestamp) {
    if (!this.running || !this.motion) return;
    const delta = Math.min(0.1, (timestamp - this.last) / 1000 || 0);
    this.last = timestamp;
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
    this.playCelebration();
    this.hero.update(dt);
    this.effects.update(dt);
    this.stats.particles = this.effects.particles.length;
    this.stats.bugs = this.bugs.length;
  }

  updateBugs(dt) {
    const floorTop = this.floorY + this.height * 0.055;
    const floorBottom = this.height * 0.9;
    let cleanup = false;
    for (const bug of this.bugs) {
      bug.age += dt;
      bug.bob = Math.sin(this.time * 5 + bug.phase) * 4;
      if (bug.state === "hit") {
        bug.dead += dt;
        if (bug.dead > 0.55) cleanup = true;
        continue;
      }
      bug.x += bug.vx * dt;
      if (bug.x < this.width * 0.12 || bug.x > this.width * 0.88) bug.vx *= -1;
      bug.y = clamp(bug.y + Math.sin(this.time * 0.7 + bug.phase) * 1.6 * dt, floorTop, floorBottom);
      bug.dir = bug.vx < 0 ? -1 : 1;
    }
    if (cleanup) {
      this.bugs = this.bugs.filter((bug) => !(bug.state === "hit" && bug.dead > 0.55));
      this.syncBugs();
    }
  }

  spawnBug(key, projectId) {
    const rack = this.racks.find((item) => item.projectId === String(projectId));
    const x = (rack?.x || this.width * 0.5) + rand(-20, 20);
    this.bugs.push({
      key, projectId,
      x: clamp(x, this.width * 0.12, this.width * 0.88),
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
    if (!this.canAttack(target)) return;
    this.encounters.delete(target.key);
    target.state = "hit";
    target.dead = 0;
    const hitX = target.x;
    const hitY = target.y - this.bugSize * 0.5;
    this.effects.slash(hitX, hitY, this.hero.hero.dir);
    this.effects.burst(hitX, hitY, "#b58bff", 18, 220);
    this.effects.burst(hitX, hitY, "#ffffff", 7, 150);
    this.effects.floatText(hitX, hitY - 12, "해결", "#ffe6a3");
    this.effects.ring(hitX, hitY, "#8b6bff", 56);
    this.shake = Math.max(this.shake, 5);
  }

  onInstall(rack) {
    rack.pulse = 1;
    rack.installing = 1;
    const { color, label } = TIERS[rack.tier];
    this.effects.ring(rack.x, rack.base - rack.h * 0.4, color, rack.w * 1.3);
    this.effects.burst(rack.x, rack.base - 6, color, 12, 130);
    this.effects.floatText(this.width * 0.5, this.height * 0.5, `${label} 랙 설치`, color);
    this.hero.playOnce("install");
  }

  handleEvent(event) {
    if (!event || !event.kind) return;
    if (event.kind === "levelup") {
      const x = this.hero.hero.x;
      const y = this.hero.hero.y - this.height * 0.22;
      this.effects.ring(x, y, "#ffd166", 140);
      this.effects.ring(x, y, "#8b6bff", 190);
      this.effects.burst(x, y, "#ffd166", 28, 260);
      this.effects.floatText(this.width * 0.5, this.height * 0.42, `LEVEL ${event.level ?? this.visibleProjects.find((project) => String(project.id) === String(event.projectId))?.level ?? 1}`, "#ffe9a8");
      this.shake = 8;
      this.flash = 0.35;
      this.hero.playOnce("levelup");
    } else if (event.kind === "reboot") {
      this.flash = 0.65;
      this.shake = 10;
      this.hero.reset();
    } else if (event.kind === "install") {
      const rack = this.racks.find((item) => item.projectId === String(event.projectId));
      if (rack) this.onInstall(rack);
    }
  }

  render() {
    const ctx = this.ctx;
    const width = this.width;
    const height = this.height;
    if (!width || !height) return;
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
    this.addAgents(entities);
    entities.push({ y: this.hero.hero.y, draw: () => this.drawHero(ctx) });
    entities.sort((a, b) => a.y - b.y);
    for (const entity of entities) entity.draw();

    this.effects.drawSlashes(ctx);
    this.effects.drawParticles(ctx);
    this.effects.drawRings(ctx);
    this.effects.drawTexts(ctx);
    if (this.flash > 0.01) {
      ctx.fillStyle = `rgba(255,255,255,${this.flash * 0.5})`;
      ctx.fillRect(0, 0, width, height);
    }
    ctx.restore();
    if (this.showFps) this.drawFps(ctx);
  }

  addAgents(entities) {
    const visible = this.agents.filter((agent) => this.racks.some((rack) => rack.projectId === String(agent.projectId)))
      .sort((a, b) => Number(Boolean(a.stale)) - Number(Boolean(b.stale))
        || (b.observedAt || 0) - (a.observedAt || 0)).slice(0, MAX_AGENTS);
    const sprites = new Map();
    for (const [index, agent] of visible.entries()) {
      const key = JSON.stringify([agent.projectId, agent.id]);
      let sprite = this.agentSprites.get(key);
      if (!sprite) {
        sprite = { animator: new Animator("idle", this.library.specs()), time: this.time };
      }
      const rack = this.racks.find((item) => item.projectId === String(agent.projectId));
      const engaged = !agent.stale && ["coding", "testing"].includes(agent.status);
      const repairs = engaged && agent.bugId != null && this.activeBugs.has(bugKey(agent.projectId, agent.bugId));
      const state = this.motion && engaged ? (repairs ? "repair" : "run") : "idle";
      sprite.animator.play(state);
      sprite.animator.update(this.motion ? Math.max(0, this.time - sprite.time) : 0);
      sprite.time = this.time;
      sprites.set(key, sprite);
      const phase = this.time * 1.2 + index * 2;
      const drift = state === "run" ? Math.sin(phase) * 20 : 0;
      const direction = state === "run" && Math.cos(phase) < 0 ? -1 : 1;
      const lane = (index % 2 ? 1 : -1) * Math.max(48, rack.w * 0.65);
      const x = clamp(rack.x + lane + drift, 35, this.width - 35);
      const y = rack.base + 24 + Math.floor(index / 2) * 26;
      const color = AGENT_COLORS[index];
      entities.push({ y, draw: () => this.drawAgent(agent, sprite, x, y, color, direction) });
    }
    this.agentSprites = sprites;
  }

  drawAgent(agent, sprite, x, y, color, direction) {
    const ctx = this.ctx;
    const art = this.library.artFor(sprite.animator.state);
    const frame = sprite.animator.frameIndex() % Math.max(1, art?.frames || 1);
    const rect = art?.rect(frame);
    const height = Math.min(104, this.heroHeight * 0.5);
    ctx.save();
    ctx.translate(x, y);
    if (rect?.image && rect.sw > 0 && rect.sh > 0) {
      const w = rect.sw * (height / rect.sh);
      const [ax, ay] = art.anchorAt(frame);
      ctx.save();
      ctx.scale(flipFor(art.facing, direction), 1);
      ctx.drawImage(rect.image, rect.sx, rect.sy, rect.sw, rect.sh, -w * ax, -height * ay, w, height);
      ctx.restore();
    } else {
      this.drawFallbackHero(ctx, height, direction);
    }
    ctx.fillStyle = "#121525";
    roundRect(ctx, -34, 1, 68, 13, 6);
    ctx.fill();
    ctx.fillStyle = color;
    ctx.font = "600 9px system-ui";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(agent.name || `Agent ${String(agent.id).slice(-6)}`, 0, 8, 62);
    ctx.restore();
  }

  drawBug(ctx, bug) {
    const size = this.bugSize;
    const bob = bug.bob || 0;
    const art = this.library.bugArt(bug.state === "hit" ? "hit" : "alive");
    const flip = flipFor(art ? art.facing : "left", bug.dir);
    ctx.save();
    ctx.globalAlpha = bug.state === "hit" ? Math.max(0, 1 - bug.dead / 0.55) : 1;
    ctx.translate(bug.x, bug.y + bob);
    if (bug.state === "hit") {
      ctx.rotate(-0.25 * flip);
      ctx.scale(1.08, 0.92);
    } else {
      ctx.rotate(Math.sin(this.time * 3 + bug.phase) * 0.05);
    }
    this.shadow(ctx, size * 0.38);
    if (art) {
      const rect = art.rect(0);
      const h = size;
      const w = rect.sw * (h / rect.sh);
      ctx.scale(flip, 1);
      ctx.drawImage(rect.image, rect.sx, rect.sy, rect.sw, rect.sh, -w / 2, -h, w, h);
    } else {
      this.drawFallbackBug(ctx, size, flip);
    }
    ctx.restore();
  }

  drawHero(ctx) {
    const hero = this.hero.hero;
    const state = this.hero.artState;
    const height = this.heroHeight;
    const bob = state === "idle" && this.motion ? Math.sin(this.time * 2.4) * 2.5 : 0;
    const art = this.library.artFor(state);
    ctx.save();
    ctx.translate(hero.x, hero.y + (hero.hop || 0) + bob);
    this.shadow(ctx, height * 0.2);
    const frame = art ? this.animator.frameIndex() % Math.max(1, art.frames) : 0;
    const rect = art?.rect(frame);
    if (rect?.image && rect.sw > 0 && rect.sh > 0) {
      const drawW = rect.sw * (height / rect.sh);
      const [ax, ay] = art.anchorAt(frame);
      // Art faces its declared direction; mirror only when moving the other way.
      const flip = flipFor(art.facing, hero.dir);
      let lunge = 0;
      let tilt = 0;
      if (state === "attack") {
        const duration = this.animator.duration("attack") || MOTION.attackSeconds;
        const progress = clamp(this.animator.elapsed / duration, 0, 1);
        lunge = Math.sin(progress * Math.PI) * 26 * hero.dir;
        tilt = Math.sin(progress * Math.PI) * 0.1 * hero.dir;
      }
      ctx.translate(lunge, 0);
      ctx.rotate(tilt);
      ctx.scale(flip, 1);
      ctx.drawImage(
        rect.image,
        rect.sx, rect.sy, rect.sw, rect.sh,
        -drawW * ax, -height * ay, drawW, height,
      );
    } else {
      this.drawFallbackHero(ctx, height, hero.dir);
    }
    ctx.restore();
  }

  drawFallbackHero(ctx, height, dir) {
    const s = height / 220;
    ctx.save();
    // The procedural stand-in faces right, like the shipped hero art.
    ctx.scale(dir < 0 ? -1 : 1, 1);
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
