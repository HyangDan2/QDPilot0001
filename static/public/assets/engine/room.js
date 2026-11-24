// Server-room layout and painting: rack slots derived from real totals and a
// cached static background so per-frame work stays small.

import { TAU, TIERS, TIER_ORDER, formatCount } from "./config.js";
import { roundRect, hexAlpha } from "./effects.js";

export function layoutRacks({ width, height, floorY, counts }) {
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
    } else if (present.every((t) => slots.filter((slot) => slot.tier === t).length >= TIERS[t].cap)) {
      break;
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
  const usable = width - margin * 2;
  const maxW = width < 640 ? 78 : 108;
  const gap = count > 1 ? Math.min(34, (usable - count * maxW) / (count - 1)) : 0;
  const rackW = Math.min(maxW, count > 1 ? (usable - gap * (count - 1)) / count : maxW * 1.1);
  const totalW = rackW * count + gap * Math.max(0, count - 1);
  const startX = (width - totalW) / 2;

  const racks = slots.map((slot, index) => {
    const tier = TIERS[slot.tier];
    return {
      tier: slot.tier,
      def: tier,
      x: startX + index * (rackW + gap) + rackW / 2,
      w: rackW,
      h: height * tier.height,
      base: floorY + 2 + (slot.index % 2) * 2,
      total: counts[slot.tier] || 0,
      groupCount: perGroup[slot.tier][slot.index] ?? counts[slot.tier] ?? 0,
      phase: index * 0.83,
      pulse: 0,
      installing: 0,
    };
  });
  let emptySlot = racks.length < budget
    ? { x: startX + count * (rackW + gap) + rackW * 0.55, y: floorY, w: rackW * 0.9 }
    : null;
  if (emptySlot && emptySlot.x + emptySlot.w / 2 > width - 12) emptySlot = null;
  return { racks, emptySlot, budget };
}

export class RoomPainter {
  constructor() {
    this.canvas = null;
    this.key = "";
  }

  draw(ctx, { width, height, floorY, dpr, motion, time }) {
    const key = `${width}x${height}@${dpr}`;
    if (key !== this.key) {
      this.canvas = paintStatic(width, height, floorY, dpr);
      this.key = key;
    }
    if (this.canvas) ctx.drawImage(this.canvas, 0, 0, width, height);
    if (motion) {
      drawBeam(ctx, width, height, floorY, time);
      drawMotes(ctx, width, height, time);
    }
  }
}

function paintStatic(width, height, floorY, dpr) {
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width * dpr));
  canvas.height = Math.max(1, Math.round(height * dpr));
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const wall = ctx.createLinearGradient(0, 0, 0, height);
  wall.addColorStop(0, "#090916");
  wall.addColorStop(0.35, "#101026");
  wall.addColorStop(0.62, "#171335");
  wall.addColorStop(1, "#07070f");
  ctx.fillStyle = wall;
  ctx.fillRect(0, 0, width, height);

  const glow = ctx.createRadialGradient(width * 0.5, floorY * 0.7, 10, width * 0.5, floorY * 0.7, width * 0.62);
  glow.addColorStop(0, "rgba(110, 85, 255, 0.2)");
  glow.addColorStop(0.55, "rgba(80, 60, 200, 0.05)");
  glow.addColorStop(1, "rgba(0, 0, 0, 0)");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, width, height);

  for (let i = 0; i < 3; i += 1) {
    const y = height * (0.05 + i * 0.035);
    const inset = width * (0.1 + i * 0.05);
    ctx.fillStyle = "rgba(139, 107, 255, 0.06)";
    ctx.fillRect(inset, y, width - inset * 2, 2);
  }

  const panel = width / 8;
  for (let i = 0; i < 8; i += 1) {
    const x = i * panel;
    ctx.fillStyle = i % 2 ? "rgba(255,255,255,0.012)" : "rgba(0,0,0,0.09)";
    ctx.fillRect(x, height * 0.12, panel, floorY - height * 0.12);
    ctx.strokeStyle = "rgba(151, 138, 255, 0.06)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, height * 0.12);
    ctx.lineTo(x, floorY);
    ctx.stroke();
  }
  ctx.fillStyle = "rgba(6, 6, 15, 0.85)";
  ctx.fillRect(0, height * 0.12, width, 10);
  ctx.fillStyle = "rgba(139, 107, 255, 0.12)";
  ctx.fillRect(0, floorY - 3, width, 3);

  const floor = ctx.createLinearGradient(0, floorY, 0, height);
  floor.addColorStop(0, "#171331");
  floor.addColorStop(0.5, "#0d0b1d");
  floor.addColorStop(1, "#07070f");
  ctx.fillStyle = floor;
  ctx.fillRect(0, floorY, width, height - floorY);

  ctx.save();
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(139, 107, 255, 0.13)";
  for (let i = -8; i <= 8; i += 1) {
    const x = width / 2 + i * width * 0.09;
    ctx.beginPath();
    ctx.moveTo(width / 2 + i * width * 0.02, floorY);
    ctx.lineTo(x * 1.6 - width * 0.3, height);
    ctx.stroke();
  }
  for (let i = 1; i < 7; i += 1) {
    const t = i / 7;
    const y = floorY + Math.pow(t, 1.7) * (height - floorY);
    ctx.strokeStyle = `rgba(139, 107, 255, ${0.12 - t * 0.06})`;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
  ctx.restore();
  return canvas;
}

function drawBeam(ctx, width, height, floorY, time) {
  const y = height * 0.085;
  const sweep = ((time * 0.08) % 1.4) - 0.2;
  const x = sweep * width;
  const beam = ctx.createLinearGradient(x - 90, 0, x + 90, 0);
  beam.addColorStop(0, "rgba(110,200,255,0)");
  beam.addColorStop(0.5, "rgba(110, 200, 255, 0.08)");
  beam.addColorStop(1, "rgba(110,200,255,0)");
  ctx.fillStyle = beam;
  ctx.fillRect(x - 90, y - 4, 180, floorY - y);
}

function drawMotes(ctx, width, height, time) {
  ctx.save();
  for (let i = 0; i < 22; i += 1) {
    const seed = i * 97.13;
    const x = (seed * 13.7 + time * (6 + (i % 4) * 3)) % width;
    const y = height * 0.18 + ((seed * 7.1 + time * (10 + (i % 3) * 6)) % (height * 0.62));
    const alpha = 0.08 + (Math.sin(time * 1.4 + i) + 1) * 0.04;
    ctx.fillStyle = `rgba(160, 190, 255, ${alpha})`;
    ctx.fillRect(x, y, 1.6, 1.6);
  }
  ctx.restore();
}

export function drawRack(ctx, rack, time) {
  const { x, base, w, h } = rack;
  const def = rack.def;
  const left = x - w / 2;
  const top = base - h;
  const pulse = rack.pulse;

  const glow = ctx.createRadialGradient(x, base, 2, x, base, w * 0.9);
  glow.addColorStop(0, hexAlpha(def.color, 0.24 + pulse * 0.3));
  glow.addColorStop(1, hexAlpha(def.color, 0));
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.ellipse(x, base, w * 0.75, 14, 0, 0, TAU);
  ctx.fill();

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

  ctx.fillStyle = hexAlpha(def.color, 0.78);
  roundRect(ctx, left + 6, top - 4, w - 12, 4, 2);
  ctx.fill();

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
      const phase = time * (1.4 + rack.phase * 0.2) + i * 1.7 + j * 0.9 + rack.phase;
      const on = Math.sin(phase) > (j % 3 === 0 ? -0.2 : 0.35);
      const color = j % 4 === 0 ? "#ff7c95" : def.color;
      ctx.fillStyle = on ? hexAlpha(color, 0.95) : "rgba(255,255,255,0.07)";
      const lx = left + pad + 6 + j * ((innerW - 10) / leds);
      ctx.fillRect(lx, y + rowH * 0.36, 2.6, Math.max(2, rowH * 0.16));
    }
  }

  ctx.fillStyle = "rgba(0,0,0,0.45)";
  for (let i = 0; i < 4; i += 1) {
    ctx.fillRect(left + pad, base - 7 + i * 1.4, innerW, 0.8);
  }

  if (rack.tier === "quantum") {
    const pulseR = 4 + Math.sin(time * 4) * 1.6;
    const core = ctx.createRadialGradient(x, top + h * 0.52, 1, x, top + h * 0.52, w * 0.36);
    core.addColorStop(0, "rgba(255, 230, 255, 0.95)");
    core.addColorStop(0.3, hexAlpha(def.color, 0.7));
    core.addColorStop(1, hexAlpha(def.color, 0));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(x, top + h * 0.52, w * 0.34 + pulseR, 0, TAU);
    ctx.fill();
  }

  const label = `${def.label} ×${formatCount(rack.groupCount)}`;
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
    ctx.fillText(`전체 ${formatCount(rack.total)}`, x, base + 11);
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

export function drawEmptySlot(ctx, slot, width, height) {
  if (!slot) return false;
  const h = height * 0.16;
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
  return !(left < 8 || left + slot.w > width - 8);
}
