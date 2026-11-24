// Hyangdani motion state machine.
//
// Logical modes (idle / run / attack / repair) drive movement and the matching
// animation state. Level-up and install celebrations are one-shot overlays that
// pause movement, then resume the previous mode.

import { clamp, rand, pick, MOTION, TAU } from "./config.js";
import { Animator, STATES } from "./animation.js";

const MODE_STATE = { idle: "idle", run: "run", attack: "attack", repair: "repair" };

export class HeroController {
  constructor(scene) {
    this.scene = scene;
    this.animator = new Animator("idle");
    this.oneshot = null;
    this.hero = {
      x: 0, y: 0, targetX: 0, targetY: 0,
      mode: "idle", timer: 1.2, dir: -1, target: null, impact: 0, hop: 0, dust: 0,
    };
  }

  get artState() {
    return this.oneshot ? this.oneshot.state : (MODE_STATE[this.hero.mode] || "idle");
  }

  setMode(mode) {
    if (this.hero.mode === mode) return;
    this.hero.mode = mode;
    this.animator.play(MODE_STATE[mode] || "idle", { restart: mode === "attack" });
  }

  playOnce(state) {
    if (!STATES[state] || this.oneshot) return;
    this.oneshot = { state, remaining: STATES[state].seconds };
    this.animator.play(state, { restart: true });
  }

  reset() {
    this.oneshot = null;
    this.hero.mode = "idle";
    this.hero.timer = 1.2;
    this.hero.hop = 0;
    this.animator.play("idle");
    this.home();
  }

  home() {
    const scene = this.scene;
    const spot = scene.heroSpot();
    const hero = this.hero;
    hero.x = spot.x;
    hero.y = spot.y;
    hero.targetX = spot.x;
    hero.targetY = spot.y;
    hero.target = null;
    scene.placed = true;
  }

  clamp() {
    const scene = this.scene;
    const top = scene.floorY + scene.height * 0.075;
    const bottom = scene.height * 0.88;
    const margin = scene.heroMargin;
    this.hero.x = clamp(this.hero.x, margin, Math.max(margin, scene.width - margin));
    this.hero.y = clamp(this.hero.y, top, bottom);
  }

  update(dt) {
    const scene = this.scene;
    this.animator.update(dt);
    if (this.oneshot) {
      this.oneshot.remaining -= dt;
      if (this.oneshot.remaining <= 0) {
        this.oneshot = null;
        this.animator.play(MODE_STATE[this.hero.mode] || "idle");
      }
      return;
    }
    const hero = this.hero;
    const floorTop = scene.floorY + scene.height * 0.075;
    const floorBottom = scene.height * 0.88;

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
      const speed = scene.width * MOTION.speedRatio;
      if (dist < 6) {
        hero.x = hero.targetX;
        hero.y = hero.targetY;
        if (hero.target && hero.target.kind === "bug") {
          hero.impact = 0;
          this.setMode("attack");
          hero.timer = MOTION.attackSeconds;
        } else {
          hero.timer = MOTION.repairSeconds;
          this.setMode("repair");
        }
        return;
      }
      const step = Math.min(dist, speed * dt);
      hero.x += (dx / dist) * step;
      hero.y += (dy / dist) * step;
      hero.dir = dx >= 0 ? 1 : -1;
      hero.hop = Math.abs(Math.sin(scene.time * MOTION.hopHz)) * -7;
      hero.dust -= dt;
      if (hero.dust <= 0) {
        hero.dust = MOTION.dustHz;
        scene.effects.particle(hero.x - hero.dir * 14, hero.y - 4, {
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
      const elapsed = MOTION.attackSeconds - hero.timer;
      const sheet = scene.library?.artFor("attack")?.sheet;
      const hitAt = sheet ? 6 / 16 : 0.16;
      if (hero.impact === 0 && elapsed > hitAt) {
        hero.impact = 1;
        scene.hitTarget();
      }
      if (hero.timer <= 0) {
        hero.timer = rand(0.7, 1.6);
        hero.hop = 0;
        this.setMode("idle");
      }
      return;
    }

    if (hero.mode === "repair") {
      hero.timer -= dt;
      if (hero.timer <= 0) {
        const { x, y } = hero;
        scene.effects.burst(x, y - scene.bugSize * 0.8, "#8b6bff", 8, 110);
        scene.effects.ring(x, y - scene.bugSize * 0.8, "#8b6bff", 46);
        hero.hop = 0;
        hero.timer = rand(0.5, 1.3);
        this.setMode("idle");
      }
    }
  }

  chooseTask() {
    const scene = this.scene;
    const hero = this.hero;
    const margin = scene.heroMargin;
    const alive = scene.bugs.filter((bug) => bug.state === "alive");
    if (alive.length) {
      const target = pick(alive);
      hero.target = { kind: "bug", ref: target };
      hero.targetX = clamp(target.x, margin, Math.max(margin, scene.width - margin));
      hero.targetY = clamp(target.y + 2, scene.floorY + scene.height * 0.075, scene.height * 0.88);
      this.setMode("run");
      hero.timer = 0;
      return;
    }
    const racks = scene.racks.filter((rack) => rack.tier !== "cache");
    const rack = racks.length ? pick(racks) : scene.racks[0];
    hero.target = { kind: "rack", ref: rack };
    hero.targetX = clamp((rack ? rack.x : scene.width * 0.5) + rand(-30, 30), margin, Math.max(margin, scene.width - margin));
    hero.targetY = clamp((rack ? rack.base : scene.floorY) + rand(30, 70), scene.floorY + 70, scene.height * 0.87);
    this.setMode("run");
    hero.timer = 0;
  }
}

export { TAU };
