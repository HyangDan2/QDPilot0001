const API = "./api/v1";
const POLL_MS = 15000;
const AWAY_THRESHOLD = 300;
const LEVEL_STEP = 10000;
const TIER_COLORS = {
  cache: "#6f83ff",
  app: "#8b6ce8",
  db: "#2fa98c",
  gpu: "#d98a2b",
  quantum: "#d94a8c",
};

const root = document.documentElement;
const byId = (id) => document.getElementById(id);

const els = {
  sync: byId("sync-state"),
  themeToggle: byId("theme-toggle"),
  helpOpen: byId("help-open"),
  authPanel: byId("auth-panel"),
  errorBanner: byId("error-banner"),
  errorText: byId("error-text"),
  retryButton: byId("retry-button"),
  levelValue: byId("level-value"),
  levelBar: byId("level-bar"),
  levelProgress: byId("level-progress"),
  levelBonus: byId("level-bonus"),
  levelNext: byId("level-next"),
  statRequests: byId("stat-requests"),
  statLifetime: byId("stat-lifetime"),
  statRps: byId("stat-rps"),
  statBaseRps: byId("stat-base-rps"),
  statThermal: byId("stat-thermal"),
  thermalBar: byId("thermal-bar"),
  statCooling: byId("stat-cooling"),
  statTokens: byId("stat-tokens"),
  statReboots: byId("stat-reboots"),
  cpLastCheck: byId("cp-last-check"),
  cpLastReward: byId("cp-last-reward"),
  cpObservedRequests: byId("cp-observed-requests"),
  cpObservedLevel: byId("cp-observed-level"),
  cpObservedGeneration: byId("cp-observed-generation"),
  cpWorldGeneration: byId("cp-world-generation"),
  cpStale: byId("cp-stale"),
  rackGrid: byId("rack-grid"),
  rackEmpty: byId("rack-empty"),
  rackSummary: byId("rack-summary"),
  efficiencyLabel: byId("efficiency-label"),
  buildList: byId("build-list"),
  upgradeList: byId("upgrade-list"),
  qtyButtons: Array.from(document.querySelectorAll("[data-qty]")),
  rebootBar: byId("reboot-bar"),
  rebootInfo: byId("reboot-info"),
  rebootOpen: byId("reboot-open"),
  prestigeTokens: byId("prestige-tokens"),
  prestigeMultiplier: byId("prestige-multiplier"),
  prestigeRun: byId("prestige-run"),
  achievementCount: byId("achievement-count"),
  achievementList: byId("achievement-list"),
  statsList: byId("stats-list"),
  logList: byId("log-list"),
  footerSync: byId("footer-sync"),
  awayDialog: byId("away-dialog"),
  awayTime: byId("away-time"),
  awayRequests: byId("away-requests"),
  awayLevel: byId("away-level"),
  awayLevelWrap: byId("away-level-wrap"),
  helpDialog: byId("help-dialog"),
  rebootDialog: byId("reboot-dialog"),
  rebootGain: byId("reboot-gain"),
  rebootConfirm: byId("reboot-confirm"),
  rebootCancel: byId("reboot-cancel"),
  resetOpen: byId("reset-open"),
  resetDialog: byId("reset-dialog"),
  resetConfirm: byId("reset-confirm"),
  resetCancel: byId("reset-cancel"),
  hardResetOpen: byId("hard-reset-open"),
  hardResetDialog: byId("hard-reset-dialog"),
  hardResetInput: byId("hard-reset-input"),
  hardResetConfirm: byId("hard-reset-confirm"),
  hardResetCancel: byId("hard-reset-cancel"),
  toastRegion: byId("toast-region"),
};

const game = {
  payload: null,
  pending: false,
  authError: false,
  canHardReset: false,
  qty: 1,
  tab: "build",
  failures: 0,
  fetchedAt: 0,
  log: [],
};

let requestSeq = 0;
let appliedSeq = 0;
let rackSignature = "";
const buildRefs = new Map();
let buildQty = null;
let upgradeSignature = "";
const upgradeRefs = new Map();
let recordsSignature = "";

/* ---------- helpers ---------- */

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "style") node.setAttribute("style", value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

function trimNumber(text) {
  return text.replace(/\.0+$/, "").replace(/(\.\d*[1-9])0+$/, "$1");
}

const COMPACT = [
  [1e12, "조"],
  [1e8, "억"],
  [1e4, "만"],
];

function fmt(value) {
  if (!Number.isFinite(value)) return "–";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  for (const [size, suffix] of COMPACT) {
    if (abs >= size) {
      const scaled = abs / size;
      const digits = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2;
      return sign + trimNumber(scaled.toFixed(digits)) + suffix;
    }
  }
  if (abs >= 100) return sign + Math.round(abs).toLocaleString("en-US");
  if (abs >= 1) return sign + trimNumber(abs.toFixed(1));
  return sign + trimNumber(abs.toFixed(2));
}

function fmtInt(value) {
  if (!Number.isFinite(value)) return "–";
  return Math.round(value).toLocaleString("ko-KR");
}

function fmtRate(value) {
  return `${fmt(value)}/s`;
}

function fmtPct(ratio) {
  return `${Math.round(ratio * 100)}%`;
}

function fmtDuration(seconds) {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total}초`;
  const minutes = Math.floor(total / 60);
  if (total < 3600) return `${minutes}분`;
  const hours = Math.floor(minutes / 60);
  if (total < 86400) return `${hours}시간 ${minutes % 60}분`;
  const days = Math.floor(hours / 24);
  return `${days}일 ${hours % 24}시간`;
}

function levelFor(total) {
  return Math.floor(Math.sqrt(Math.max(0, total) / LEVEL_STEP)) + 1;
}

function clockNow() {
  const now = new Date();
  return now.toLocaleTimeString("ko-KR", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function fmtStamp(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "–";
  return new Date(seconds * 1000).toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtGeneration(value) {
  return Number.isFinite(value) ? `${value + 1}세대` : "–";
}

function makeOpId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function currentRequests() {
  if (!game.payload) return 0;
  const elapsed = Math.min(180, Math.max(0, (Date.now() - game.fetchedAt) / 1000));
  return game.payload.state.requests + game.payload.derived.rps * elapsed;
}

function currentLifetime() {
  if (!game.payload) return 0;
  const elapsed = Math.min(180, Math.max(0, (Date.now() - game.fetchedAt) / 1000));
  return {
    run: game.payload.state.run_lifetime + game.payload.derived.rps * elapsed,
    total: game.payload.state.total_lifetime + game.payload.derived.rps * elapsed,
  };
}

/* ---------- API ---------- */

async function request(url, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers,
    });
  } catch (error) {
    const network = new Error("network");
    network.network = true;
    throw network;
  }
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (response.status === 401) {
    const auth = new Error("auth");
    auth.auth = true;
    throw auth;
  }
  if (!response.ok) {
    const failure = new Error((data && data.detail) || `HTTP ${response.status}`);
    failure.status = response.status;
    throw failure;
  }
  if (!data || typeof data !== "object") throw new Error("empty response");
  return data;
}

function localizeError(detail) {
  const text = String(detail || "");
  if (!navigator.onLine) return "오프라인 상태입니다. 연결을 확인하세요.";
  const rules = [
    [/not enough requests/i, "요청이 부족합니다."],
    [/admiral role required/i, "최고 관리자(admiral) 서비스 권한이 필요합니다."],
    [/admiral service role required/i, "최고 관리자(admiral) 서비스 권한이 필요합니다."],
    [/chairman account role required/i, "Chairman 계정만 Hard Reset을 실행할 수 있습니다."],
    [/in progress; retry shortly/i, "다른 작업이 진행 중입니다. 잠시 후 다시 시도하세요."],
    [/already used for a different action/i, "같은 작업 번호가 다른 요청에 사용되었습니다. 새로고침 후 다시 시도하세요."],
    [/already maxed/i, "이미 최대 레벨입니다."],
    [/not available yet/i, "아직 Reboot할 수 없습니다."],
    [/invalid action/i, "지원하지 않는 동작입니다."],
    [/invalid opId/i, "요청 식별자가 올바르지 않습니다."],
    [/admin authentication required/i, "로그인이 필요합니다."],
    [/storage failure|internal error/i, "저장소 오류가 발생했습니다. 잠시 후 다시 시도하세요."],
    [/network|failed to fetch|load failed/i, "서버에 연결하지 못했습니다."],
  ];
  for (const [pattern, message] of rules) {
    if (pattern.test(text)) return message;
  }
  return "요청을 처리하지 못했습니다.";
}

/* ---------- rendering ---------- */

function setSync(mode, text) {
  els.sync.className = `sync${mode ? ` is-${mode}` : ""}`;
  els.sync.textContent = text;
  els.footerSync.textContent = text;
}

function showError(message) {
  els.errorText.textContent = message;
  els.errorBanner.hidden = false;
}

function hideError() {
  els.errorBanner.hidden = true;
}

function showAuth(flag) {
  game.authError = flag;
  els.authPanel.hidden = !flag;
  if (flag) hideError();
}

function renderLevel() {
  if (!game.payload) return;
  const { derived } = game.payload;
  const level = derived.level;
  els.levelValue.textContent = String(level);
  els.levelBonus.textContent = `레벨 보너스 +${Math.round(derived.level_bonus * 100)}%`;
  const progress = Math.max(0, Math.min(1, derived.level_progress));
  els.levelBar.style.width = `${(progress * 100).toFixed(1)}%`;
  els.levelProgress.setAttribute("aria-valuenow", String(Math.round(progress * 100)));
  const remaining = Math.max(0, derived.next_level_at - game.payload.state.total_lifetime);
  els.levelNext.textContent = `다음 레벨까지 ${fmt(remaining)} 요청 · Lv.${level + 1}에서 전체 RPS +${level}%`;
  document.title = `Lv.${level} · HD2Lab Idle Ops`;
}

function renderStats() {
  if (!game.payload) return;
  const { derived, state } = game.payload;
  const lifetime = currentLifetime();
  els.statRequests.textContent = fmt(currentRequests());
  els.statLifetime.textContent = fmt(lifetime.total);
  els.statRps.textContent = fmtRate(derived.rps);
  els.statBaseRps.textContent = fmtRate(derived.base_rps);
  const heatRatio = derived.cooling > 0 ? derived.heat / derived.cooling : 0;
  els.statThermal.textContent = fmtPct(heatRatio);
  els.thermalBar.style.width = `${(Math.min(1, heatRatio) * 100).toFixed(1)}%`;
  els.thermalBar.classList.toggle("is-hot", derived.heat > derived.cooling);
  els.statCooling.textContent = fmt(derived.cooling);
  els.statTokens.textContent = fmtInt(state.tokens);
  els.statReboots.textContent = fmtInt(state.reboots);
}

function renderRacks(force = false) {
  if (!game.payload) return;
  const { catalog, derived } = game.payload;
  const signature = JSON.stringify(game.payload.state.servers) + `|${Math.round(derived.efficiency * 100)}`;
  els.rackSummary.textContent = `${fmtInt(derived.total_servers)}대 가동 · ${fmtRate(derived.rps)}`;
  const overheated = derived.heat > derived.cooling;
  els.efficiencyLabel.textContent = overheated
    ? `효율 ${fmtPct(derived.efficiency)} · 과열`
    : `효율 ${fmtPct(derived.efficiency)}`;
  if (!force && signature === rackSignature) return;
  rackSignature = signature;

  const racks = catalog.tiers.filter((tier) => tier.owned > 0);
  els.rackEmpty.hidden = racks.length > 0;
  const children = racks.map((tier) => {
    const lit = Math.min(tier.owned, 12);
    const slots = [];
    for (let index = 0; index < 12; index += 1) {
      slots.push(h("span", {
        class: index < lit ? "led" : "led is-off",
        style: `--d:${(index * 0.21).toFixed(2)}s`,
      }));
    }
    return h("div", { class: "rack", style: `--tier:${TIER_COLORS[tier.id] || "var(--accent)"}` },
      h("div", { class: "rack-head" },
        h("span", { class: "rack-name", text: tier.name }),
        h("span", { class: "rack-count", text: `${fmtInt(tier.owned)}대` })),
      h("div", { class: "rack-slots", "aria-hidden": "true" }, slots),
      h("p", { class: "rack-foot-line", text: `기본 ${fmtRate(tier.owned * tier.rps)} · 발열 ${fmt(tier.owned * tier.heat)}` }));
  });
  els.rackGrid.replaceChildren(...children);
}

function qtyLabel(tier) {
  if (game.qty === 10) return "10대";
  if (game.qty === "max") return tier.max_count > 0 ? `${fmtInt(tier.max_count)}대` : "0대";
  return "1대";
}

function buildButton(tier) {
  const costSpan = h("span", { class: "cost", text: "–" });
  const sub = h("small", { text: "" });
  const button = h("button", {
    class: "buy-button",
    type: "button",
    "data-buy": tier.id,
  }, costSpan, sub);
  const ref = { row: null, owned: null, button, costSpan, sub };
  return ref;
}

function makeBuildRow(tier) {
  const ref = buildButton(tier);
  const ownedValue = h("strong", { text: "0" });
  const row = h("article", { class: "item" },
    h("div", { class: "item-body" },
      h("h3", { class: "item-title" },
        h("span", { class: "tier-dot", style: `--tier:${TIER_COLORS[tier.id] || "var(--accent)"}` }),
        tier.name),
      h("p", { class: "item-desc", text: tier.desc }),
      h("p", { class: "item-meta" },
        h("span", {}, "보유 ", ownedValue, "대"),
        h("span", {}, `대당 +${fmtRate(tier.rps)}`),
        h("span", {}, `발열 +${tier.heat}/대`))),
    ref.button);
  ref.row = row;
  ref.owned = ownedValue;
  return ref;
}

function updateBuildRow(tier, ref) {
  const qty = game.qty;
  let cost = tier.cost;
  let subText = "1대";
  let maxCount = tier.max_count;
  if (qty === 10) {
    cost = tier.cost10;
    subText = "10대";
  } else if (qty === "max") {
    cost = tier.max_count > 0 ? tier.max_cost : 0;
    subText = tier.max_count > 0 ? `최대 ${fmtInt(tier.max_count)}대` : "구매 불가";
  }
  ref.owned.textContent = fmtInt(tier.owned);
  ref.costSpan.textContent = tier.max_count === 0 && qty === "max" ? "—" : fmt(cost);
  ref.sub.textContent = subText;
  ref.button.dataset.cost = String(cost);
  ref.button.dataset.max = String(maxCount);
  ref.button.dataset.qty = String(qty);
  ref.button.setAttribute(
    "aria-label",
    `${tier.name} ${qtyLabel(tier)} 구매` + (tier.max_count === 0 && qty === "max" ? " 불가" : `, 요청 ${fmtInt(cost)} 필요`),
  );
}

function renderBuild(force = false) {
  if (!game.payload) return;
  const { tiers } = game.payload.catalog;
  const rebuild = force || buildQty !== game.qty || buildRefs.size !== tiers.length;
  if (rebuild) {
    buildQty = game.qty;
    buildRefs.clear();
    const nodes = tiers.map((tier) => {
      const ref = makeBuildRow(tier);
      buildRefs.set(tier.id, ref);
      return ref.row;
    });
    els.buildList.replaceChildren(...nodes);
  }
  for (const tier of tiers) {
    const ref = buildRefs.get(tier.id);
    if (ref) updateBuildRow(tier, ref);
  }
}

function renderUpgrades() {
  if (!game.payload) return;
  const { upgrades } = game.payload.catalog;
  const signature = upgrades.map((item) => `${item.id}:${item.level}`).join("|");
  if (signature !== upgradeSignature) {
    upgradeSignature = signature;
    upgradeRefs.clear();
    const nodes = upgrades.map((item) => {
      const costSpan = h("span", { class: "cost", text: "–" });
      const sub = h("small", { text: "" });
      const button = h("button", { class: "buy-button", type: "button", "data-upgrade": item.id }, costSpan, sub);
      const chip = h("span", { class: "chip", text: `Lv.${item.level} / ${item.max}` });
      const row = h("article", { class: "item" },
        h("div", { class: "item-body" },
          h("h3", { class: "item-title" }, item.name, chip),
          h("p", { class: "item-desc", text: item.desc })),
        button);
      upgradeRefs.set(item.id, { button, costSpan, sub });
      return row;
    });
    els.upgradeList.replaceChildren(...nodes);
  }
  for (const item of upgrades) {
    const ref = upgradeRefs.get(item.id);
    if (!ref) continue;
    const maxed = item.cost === null;
    ref.costSpan.textContent = maxed ? "MAX" : fmt(item.cost);
    ref.sub.textContent = maxed ? "완료" : `Lv.${item.level + 1}`;
    ref.button.dataset.cost = String(maxed ? 0 : item.cost);
    ref.button.dataset.maxed = maxed ? "1" : "0";
    ref.button.setAttribute("aria-label", maxed ? `${item.name} 최대 레벨` : `${item.name} Lv.${item.level + 1} 업그레이드, 요청 ${fmtInt(item.cost)} 필요`);
  }
}

function renderPrestige() {
  if (!game.payload) return;
  const { catalog, state } = game.payload;
  const reboot = catalog.reboot;
  const ratio = Math.min(1, reboot.progress);
  els.rebootBar.style.width = `${(ratio * 100).toFixed(1)}%`;
  els.rebootInfo.textContent = `${fmt(state.run_lifetime)} / ${fmt(reboot.threshold)} · 토큰 +${reboot.tokens}`;
  els.rebootOpen.disabled = game.pending || !reboot.available;
  els.rebootOpen.textContent = reboot.available ? `Reboot · +${reboot.tokens}` : "Reboot";
  els.rebootGain.textContent = String(reboot.tokens);
  els.prestigeTokens.textContent = fmtInt(state.tokens);
  els.prestigeMultiplier.textContent = `+${state.tokens * 2}%`;
  els.prestigeRun.textContent = fmt(state.run_lifetime);
}

function renderRecords() {
  if (!game.payload) return;
  const { achievements } = game.payload.catalog;
  const { state, derived } = game.payload;
  const unlocked = achievements.filter((item) => item.unlocked).length;
  els.achievementCount.textContent = `${unlocked} / ${achievements.length}`;
  const signature = achievements.map((item) => (item.unlocked ? 1 : 0)).join("");
  if (signature !== recordsSignature) {
    recordsSignature = signature;
    const nodes = achievements.map((item, index) => {
      const locked = !item.unlocked;
      return h("div", { class: `ach${locked ? " is-locked" : ""}` },
        h("span", { class: "ach-badge", text: item.unlocked ? "✓" : String(index + 1) }),
        h("div", {},
          h("p", { class: "ach-name", text: item.name }),
          h("p", { class: "ach-desc", text: item.desc })),
        h("span", { class: "ach-bonus", text: `+${Math.round(item.bonus * 100)}% RPS` }));
    });
    els.achievementList.replaceChildren(...nodes);
  }
  const rows = [
    ["운영 레벨", `Lv.${derived.level}`],
    ["누적 요청", fmt(state.total_lifetime)],
    ["이번 런", fmt(state.run_lifetime)],
    ["보유 서버", `${fmtInt(derived.total_servers)}대`],
    ["가동 토큰", fmtInt(state.tokens)],
    ["토큰 배율", `+${state.tokens * 2}%`],
    ["업적 보너스", `+${Math.round(derived.achievement_bonus * 100)}%`],
    ["레벨 보너스", `+${Math.round(derived.level_bonus * 100)}%`],
    ["발열", fmt(derived.heat)],
    ["냉각", fmt(derived.cooling)],
    ["효율", fmtPct(derived.efficiency)],
    ["Reboot", `${fmtInt(state.reboots)}회`],
    ["시작일", new Date(state.created_at * 1000).toLocaleDateString("ko-KR")],
  ];
  els.statsList.replaceChildren(...rows.map(([label, value]) =>
    h("div", {}, h("dt", { text: label }), h("dd", { text: value }))));
}

function renderLog() {
  const entries = game.log.slice(0, 14);
  if (!entries.length) {
    els.logList.replaceChildren(h("li", { class: "log-empty", text: "아직 기록이 없습니다." }));
    return;
  }
  els.logList.replaceChildren(...entries.map((entry) =>
    h("li", {},
      h("span", { class: "log-time", text: entry.time }),
      h("span", { text: entry.text }))));
}

function updateAffordability() {
  if (!game.payload) return;
  const requests = currentRequests();
  for (const button of document.querySelectorAll("[data-buy]")) {
    const cost = Number(button.dataset.cost || "0");
    const maxCount = Number(button.dataset.max || "0");
    const qty = button.dataset.qty;
    const available = qty !== "max" || maxCount > 0;
    button.disabled = game.pending || game.authError || !available || cost > requests;
  }
  for (const button of document.querySelectorAll("[data-upgrade]")) {
    const maxed = button.dataset.maxed === "1";
    const cost = Number(button.dataset.cost || "0");
    button.disabled = game.pending || game.authError || maxed || cost > requests;
  }
}

function render() {
  if (!game.payload) return;
  renderLevel();
  renderStats();
  renderRacks();
  renderBuild();
  renderUpgrades();
  renderPrestige();
  renderRecords();
  renderLog();
  updateAffordability();
}

function renderCheckpoint(stamps) {
  if (!stamps) return;
  els.cpLastCheck.textContent = fmtStamp(stamps.adminLastCheck);
  els.cpLastReward.textContent = fmtStamp(stamps.lastRewardAt);
  els.cpObservedRequests.textContent = Number.isFinite(stamps.observedRequests)
    ? fmtInt(stamps.observedRequests)
    : "–";
  els.cpObservedLevel.textContent = Number.isFinite(stamps.observedLevel)
    ? `Lv.${stamps.observedLevel}`
    : "–";
  els.cpObservedGeneration.textContent = fmtGeneration(stamps.observedGeneration);
  els.cpWorldGeneration.textContent = fmtGeneration(stamps.worldGeneration);
  els.cpStale.hidden = !stamps.observedFromEarlierWorld;
}

function updateLiveNumbers() {
  if (!game.payload || document.hidden) return;
  const lifetime = currentLifetime();
  els.statRequests.textContent = fmt(currentRequests());
  els.statLifetime.textContent = fmt(lifetime.total);
  const remaining = Math.max(0, game.payload.derived.next_level_at - lifetime.total);
  els.levelNext.textContent = `다음 레벨까지 ${fmt(remaining)} 요청 · Lv.${game.payload.derived.level + 1}에서 전체 RPS +${game.payload.derived.level}%`;
  updateAffordability();
}

/* ---------- notifications ---------- */

function addLog(text) {
  game.log.unshift({ time: clockNow(), text });
  game.log = game.log.slice(0, 30);
  renderLog();
}

function toast(text, { error = false, duration = 3200 } = {}) {
  const node = h("div", { class: `toast${error ? " is-error" : ""}`, text });
  els.toastRegion.append(node);
  while (els.toastRegion.children.length > 3) els.toastRegion.firstElementChild.remove();
  window.setTimeout(() => node.remove(), duration);
}

function showAway(data) {
  if (!(data.awaySeconds >= AWAY_THRESHOLD && data.gains.requests >= 1)) return false;
  addLog(`자리를 비운 ${fmtDuration(data.awaySeconds)} 동안 +${fmt(data.gains.requests)} 요청`);
  if (document.querySelector("dialog[open]")) return false;
  els.awayTime.textContent = fmtDuration(data.awaySeconds);
  els.awayRequests.textContent = `+${fmt(data.gains.requests)}`;
  const leveled = data.gains.level_to > data.gains.level_from;
  els.awayLevelWrap.hidden = !leveled;
  if (leveled) els.awayLevel.textContent = `${data.gains.level_from} → ${data.gains.level_to}`;
  if (typeof els.awayDialog.showModal === "function") els.awayDialog.showModal();
  return true;
}

function announceGains(data, { awayVisible }) {
  const gains = data.gains || {};
  if (gains.level_to > gains.level_from) {
    addLog(`운영 레벨 ${gains.level_to} 달성`);
    if (!awayVisible) toast(`운영 레벨 ${gains.level_to} 달성 · RPS 보너스 +${gains.level_to - 1}%`);
  }
  const unlocked = Array.isArray(gains.achievements) ? gains.achievements : [];
  if (unlocked.length) {
    const names = unlocked.map((id) => {
      const found = (game.payload.catalog.achievements || []).find((item) => item.id === id);
      return found ? found.name : id;
    });
    for (const name of names) addLog(`업적 달성 · ${name}`);
    if (!awayVisible) toast(`업적 달성 · ${names.join(", ")}`, { duration: 4400 });
  }
}

function accept(data) {
  game.payload = data;
  game.fetchedAt = Date.now();
  game.failures = 0;
  hideError();
  showAuth(false);
  setSync("live", `동기화 ${clockNow()}`);

  const awayVisible = showAway(data);
  announceGains(data, { awayVisible });
  renderCheckpoint(data.timestamps);

  if (data.message && !data.duplicate) {
    addLog(data.message);
    if (!awayVisible) toast(data.message);
  }
  if (data.duplicate) addLog("중복 요청을 무시했습니다.");

  render();
}

/* ---------- data flow ---------- */

function applyIdentity(data) {
  game.canHardReset = Boolean(data && data.canHardReset);
  els.hardResetOpen.hidden = !game.canHardReset;
  els.hardResetOpen.disabled = !game.canHardReset;
}

async function loadIdentity() {
  try {
    const data = await request(`${API}/whoami`);
    applyIdentity(data);
  } catch (error) {
    // Fail closed: the hard-reset control stays hidden unless the server
    // confirms the chairman account role. The server enforces it again.
    applyIdentity(null);
  }
}

async function load({ initial = false } = {}) {
  if (game.pending || game.authError) return;
  const seq = ++requestSeq;
  try {
    const data = await request(`${API}/state`);
    if (seq < appliedSeq) return;
    appliedSeq = seq;
    accept(data);
  } catch (error) {
    if (seq < appliedSeq) return;
    if (error.auth) {
      setSync("error", "로그인 필요");
      showAuth(true);
      return;
    }
    game.failures += 1;
    setSync(game.payload ? "stale" : "error", game.payload ? "동기화 지연" : "연결 실패");
    if (initial || !game.payload || game.failures >= 2) {
      showError(localizeError(error.message));
    }
  }
}

const opCoordinator = window.HD2OperationCoordinator.createOperationCoordinator({
  storage: (() => {
    try {
      return window.sessionStorage;
    } catch (error) {
      return null;
    }
  })(),
  makeId: makeOpId,
});

function opSignature(path, fields) {
  return `${path}|${JSON.stringify(fields)}`;
}

async function resolvePendingOp() {
  // Recovery takes the same lock as a user action, so a click cannot mint a
  // second opId while the startup replay is still outstanding.
  if (game.pending || !opCoordinator.hasPending()) return;
  game.pending = true;
  render();
  try {
    const result = await opCoordinator.recover();
    if (result.status === "ok") {
      accept(result.data);
      return;
    }
    if (result.status === "auth") {
      setSync("error", "로그인 필요");
      showAuth(true);
      return;
    }
    if (result.status === "rejected") {
      toast(localizeError(result.message || "요청이 거부되었습니다."), { error: true });
      return;
    }
    if (result.status === "retry") {
      setSync("stale", "동기화 지연");
      toast("이전 요청을 확인하지 못했습니다. 연결이 회복되면 같은 작업으로 이어서 처리합니다.", { error: true });
    }
  } finally {
    game.pending = false;
    render();
    if (!game.payload && !game.authError) load({ initial: true });
  }
}

async function submitAction(action, { reset = false, hardReset = false } = {}) {
  if (game.pending || game.authError) return null;
  const path = hardReset ? `${API}/hard-reset` : reset ? `${API}/reset` : `${API}/actions`;
  const fields = hardReset ? { confirm: "HARD RESET" } : reset ? {} : { action };
  game.pending = true;
  hideError();
  render();
  const seq = ++requestSeq;
  try {
    const result = await opCoordinator.submit({
      path,
      fields,
      signature: opSignature(path, fields),
    });
    if (result.status === "ok") {
      if (seq < appliedSeq) return null;
      appliedSeq = seq;
      accept(result.data);
      return result.data;
    }
    if (result.status === "auth") {
      // The opId stays pending; re-login replays it once through recover().
      setSync("error", "로그인 필요");
      showAuth(true);
      return null;
    }
    if (result.status === "rejected") {
      toast(localizeError(result.message || "요청이 거부되었습니다."), { error: true });
      return null;
    }
    if (result.status === "blocked") {
      toast("이전 요청을 확인하지 못했습니다. 연결을 확인한 뒤 다시 시도하세요.", { error: true });
      return null;
    }
    if (result.status === "busy" || result.status === "invalid") return null;
    game.failures += 1;
    setSync("stale", "동기화 지연");
    const message = result.message === "network"
      ? "서버에 연결하지 못했습니다."
      : localizeError(result.message || "동기화 지연");
    showError(message);
    toast(`${message} 같은 작업을 다시 누르면 이어서 처리합니다.`, { error: true });
    return null;
  } finally {
    game.pending = false;
    render();
  }
}

/* ---------- interactions ---------- */

function selectTab(name) {
  game.tab = name;
  for (const button of document.querySelectorAll("[data-tab]")) {
    const active = button.dataset.tab === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  }
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.hidden = panel.id !== `tab-${name}`;
  }
}

function setTheme(theme) {
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  try {
    localStorage.setItem("hd2lab-theme", theme);
  } catch (error) { /* storage may be blocked */ }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "dark" ? "#0d0d15" : "#f5f5fb");
}

function setQty(qty) {
  game.qty = qty;
  for (const button of els.qtyButtons) {
    button.classList.toggle("is-active", button.dataset.qty === String(qty));
  }
  renderBuild(true);
  updateAffordability();
}

function bindDialogs() {
  for (const dialog of document.querySelectorAll("dialog")) {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  }
  els.helpOpen.addEventListener("click", () => els.helpDialog.showModal());
  els.rebootOpen.addEventListener("click", () => {
    if (!game.payload || !game.payload.catalog.reboot.available) return;
    els.rebootGain.textContent = String(game.payload.catalog.reboot.tokens);
    els.rebootDialog.showModal();
  });
  els.rebootCancel.addEventListener("click", () => els.rebootDialog.close());
  els.rebootConfirm.addEventListener("click", async () => {
    const data = await submitAction({ type: "reboot" });
    if (data) els.rebootDialog.close();
  });
  els.resetOpen.addEventListener("click", () => els.resetDialog.showModal());
  els.resetCancel.addEventListener("click", () => els.resetDialog.close());
  els.resetConfirm.addEventListener("click", async () => {
    const data = await submitAction(null, { reset: true });
    if (data) els.resetDialog.close();
  });
  const syncHardResetInput = () => {
    els.hardResetConfirm.disabled = !game.canHardReset || els.hardResetInput.value !== "HARD RESET";
  };
  els.hardResetOpen.hidden = true;
  els.hardResetOpen.disabled = true;
  els.hardResetOpen.addEventListener("click", () => {
    if (!game.canHardReset) return;
    els.hardResetInput.value = "";
    syncHardResetInput();
    els.hardResetDialog.showModal();
    els.hardResetInput.focus();
  });
  els.hardResetInput.addEventListener("input", syncHardResetInput);
  els.hardResetCancel.addEventListener("click", () => els.hardResetDialog.close());
  els.hardResetConfirm.addEventListener("click", async () => {
    if (!game.canHardReset || els.hardResetInput.value !== "HARD RESET") return;
    const data = await submitAction(null, { hardReset: true });
    if (data) {
      els.hardResetDialog.close();
      const backup = String(data.backup || "").split("/").pop();
      toast(backup ? `Hard Reset 완료 · 백업 ${backup}` : "Hard Reset 완료");
    }
  });
}

function bindViewport() {
  const embedded = root.dataset.embedded === "launcher";
  if (!embedded) {
    const update = () => {
      const height = window.visualViewport ? window.visualViewport.height : window.innerHeight;
      root.style.setProperty("--app-vh", `${Math.round(height)}px`);
    };
    update();
    window.addEventListener("resize", update);
    if (window.visualViewport) window.visualViewport.addEventListener("resize", update);
  }
  window.addEventListener("message", (event) => {
    if (event.source !== window.parent) return;
    const data = event.data;
    if (data && data.type === "hd2:viewport" && Number.isFinite(data.height)) {
      root.style.setProperty("--app-vh", `${Math.round(data.height)}px`);
    }
  });
}

function bindAdminShell() {
  const apply = (shellEl) => {
    const sync = () => {
      const height = Math.round(shellEl.getBoundingClientRect().height);
      root.style.setProperty("--hq-shell-h", `${height}px`);
    };
    sync();
    if ("ResizeObserver" in window) new ResizeObserver(sync).observe(shellEl);
  };
  const existing = document.querySelector("hq-admin-shell");
  if (existing) return apply(existing);
  const observer = new MutationObserver(() => {
    const shellEl = document.querySelector("hq-admin-shell");
    if (!shellEl) return;
    observer.disconnect();
    apply(shellEl);
  });
  observer.observe(document.body, { childList: true });
}

function bindBridge() {
  if (window.self === window.top) return;
  root.dataset.embedded = "launcher";
  window.hd2LauncherControls = {
    goBack() {
      const dialogs = Array.from(document.querySelectorAll("dialog[open]"));
      if (dialogs.length) {
        dialogs[dialogs.length - 1].close();
        return true;
      }
      if (game.tab !== "build") {
        selectTab("build");
        return true;
      }
      return false;
    },
  };
}

function bind() {
  els.themeToggle.addEventListener("click", () => {
    setTheme(root.dataset.theme === "dark" ? "light" : "dark");
  });
  els.retryButton.addEventListener("click", () => {
    hideError();
    load({ initial: true });
  });
  document.querySelector('[role="tablist"]').addEventListener("keydown", (event) => {
    const tabs = Array.from(document.querySelectorAll("[data-tab]"));
    const index = tabs.findIndex((tab) => tab.dataset.tab === game.tab);
    let next = null;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    if (next === null) return;
    event.preventDefault();
    const target = tabs[next];
    selectTab(target.dataset.tab);
    target.focus();
  });
  for (const button of document.querySelectorAll("[data-tab]")) {
    button.addEventListener("click", () => selectTab(button.dataset.tab));
  }
  for (const button of els.qtyButtons) {
    button.addEventListener("click", () => setQty(button.dataset.qty === "max" ? "max" : Number(button.dataset.qty)));
  }
  els.buildList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-buy]");
    if (!button || button.disabled) return;
    submitAction({ type: "buyServer", tier: button.dataset.buy, qty: game.qty });
  });
  els.upgradeList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-upgrade]");
    if (!button || button.disabled) return;
    submitAction({ type: "buyUpgrade", id: button.dataset.upgrade });
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) load();
  });
  window.addEventListener("online", () => load());
  window.addEventListener("storage", (event) => {
    if (event.key === "hd2lab-theme" && (event.newValue === "dark" || event.newValue === "light")) {
      root.dataset.theme = event.newValue;
      root.style.colorScheme = event.newValue;
    }
  });
}

function init() {
  setTheme(root.dataset.theme === "dark" ? "dark" : "light");
  bind();
  bindDialogs();
  bindViewport();
  bindBridge();
  bindAdminShell();
  selectTab(game.tab);
  renderLog();
  resolvePendingOp();
  loadIdentity();
  load({ initial: true });
  window.setInterval(() => {
    if (document.hidden || game.pending || game.authError) return;
    load();
  }, POLL_MS);
  window.setInterval(updateLiveNumbers, 250);
}

init();
