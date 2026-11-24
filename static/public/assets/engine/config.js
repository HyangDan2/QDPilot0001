// Shared math, tier definitions and tuning constants for the public scene.

export const TAU = Math.PI * 2;
export const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
export const rand = (min, max) => min + Math.random() * (max - min);
export const pick = (list) => list[Math.floor(Math.random() * list.length)];

export const TIERS = {
  cache: { label: "캐시", color: "#6ee7f7", cap: 3, priority: 0, height: 0.105, units: 3 },
  app: { label: "앱", color: "#7ef7b1", cap: 3, priority: 1, height: 0.135, units: 4 },
  db: { label: "DB", color: "#ffd166", cap: 2, priority: 2, height: 0.17, units: 4 },
  gpu: { label: "GPU", color: "#b58bff", cap: 2, priority: 3, height: 0.2, units: 5 },
  quantum: { label: "양자", color: "#ff7ce5", cap: 1, priority: 4, height: 0.235, units: 5 },
};
export const TIER_ORDER = ["cache", "app", "db", "gpu", "quantum"];

export const BOX_COLORS = {
  heroGlow: "#8b6bff",
  hit: "#b58bff",
  gold: "#ffd166",
  cyan: "#6ee7f7",
};

export const LIMITS = {
  particles: 160,
  texts: 24,
  rings: 14,
  slashes: 8,
  bugs: 3,
  dpr: 2,
  fpsWindow: 60,
};

export const MOTION = {
  speedRatio: 0.32,
  hopHz: 12,
  dustHz: 0.09,
  attackSeconds: 0.75,
  repairSeconds: 0.8,
  bugSpawn: [6.5, 12.5],
  bugSpeed: [10, 34],
};

// Compact Korean count formatting so huge server totals stay readable while
// still showing the real number.
export function formatCount(value) {
  const number = Math.max(0, Math.floor(Number(value) || 0));
  if (number >= 1e12) return `${trim(number / 1e12)}조`;
  if (number >= 1e8) return `${trim(number / 1e8)}억`;
  if (number >= 1e4) return `${trim(number / 1e4)}만`;
  return String(number);
}

function trim(value) {
  return value >= 100 ? String(Math.round(value)) : String(Math.round(value * 10) / 10);
}
