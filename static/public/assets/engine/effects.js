// Capped effect pools: particles, floating text, rings and slash arcs.

import { TAU, clamp, rand, LIMITS } from "./config.js";

export class Effects {
  constructor() {
    this.particles = [];
    this.texts = [];
    this.rings = [];
    this.slashes = [];
  }

  clear() {
    this.particles = [];
    this.texts = [];
    this.rings = [];
    this.slashes = [];
  }

  particle(x, y, options) {
    if (this.particles.length >= LIMITS.particles) this.particles.shift();
    const life = options.life || 0.6;
    this.particles.push({
      x, y,
      vx: options.vx || 0,
      vy: options.vy || 0,
      size: options.size || 2,
      life,
      max: life,
      color: options.color || "#fff",
      gravity: options.gravity ?? 0,
      glow: Boolean(options.glow),
    });
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

  floatText(x, y, text, color) {
    if (this.texts.length >= LIMITS.texts) this.texts.shift();
    this.texts.push({ x, y, text, color, life: 0.9, max: 0.9, size: rand(15, 20) });
  }

  ring(x, y, color, maxRadius) {
    if (this.rings.length >= LIMITS.rings) this.rings.shift();
    this.rings.push({ x, y, r: 4, speed: maxRadius * 2.2, life: 0.5, max: 0.5, color });
  }

  slash(x, y, dir) {
    if (this.slashes.length >= LIMITS.slashes) this.slashes.shift();
    this.slashes.push({ x, y, dir, life: 0.28, max: 0.28, angle: rand(-0.4, 0.2) });
  }

  update(dt) {
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
}

export function roundRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

export function hexAlpha(hex, alpha) {
  const value = hex.replace("#", "");
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, alpha))})`;
}
