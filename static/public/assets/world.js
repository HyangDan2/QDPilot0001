import { WorldScene } from "./engine.js";

const $ = (id) => document.getElementById(id);
const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)");
const MOTION_KEY = "idle-ops-motion";
const POLL_MS = 8000;
const FEED_LIMIT = 9;

const TIERS = [
  { id: "cache", letter: "C", name: "캐시 노드", short: "캐시", color: "#6ee7f7" },
  { id: "app", letter: "A", name: "앱 서버", short: "앱", color: "#7ef7b1" },
  { id: "db", letter: "D", name: "DB 노드", short: "DB", color: "#ffd166" },
  { id: "gpu", letter: "G", name: "GPU 노드", short: "GPU", color: "#b58bff" },
  { id: "quantum", letter: "Q", name: "양자 랙", short: "양자", color: "#ff7ce5" },
];
const TIER_BY_ID = new Map(TIERS.map((tier) => [tier.id, tier]));
const UPGRADES = {
  network: { letter: "N", color: "#6ee7f7" },
  cooling: { letter: "C", color: "#7ef7b1" },
  balancer: { letter: "L", color: "#ffd166" },
  overclock: { letter: "O", color: "#ff7c95" },
};
const ACHIEVEMENT_NAMES = {
  first_server: "첫 랙 설치",
  ten_servers: "작은 클러스터",
  rps_100: "워밍 캐시",
  rps_10k: "트래픽 폭주",
  lifetime_1m: "백만 요청",
  level_10: "시니어 오퍼레이터",
  first_reboot: "클린 재시작",
  tokens_10: "업타임 수집가",
};

const els = {
  sync: $("sync-state"),
  live: document.querySelector(".hud-live"),
  level: $("hud-level"),
  levelBonus: $("hud-level-bonus"),
  levelTrack: $("level-track"),
  levelBar: $("level-bar"),
  levelNext: $("level-next"),
  rps: $("stat-rps"),
  requests: $("stat-requests"),
  heat: $("stat-heat"),
  tokens: $("stat-tokens"),
  feedList: $("feed-list"),
  feedEmpty: $("feed-empty"),
  drawer: $("drawer"),
  drawerOpen: $("drawer-open"),
  drawerClose: $("drawer-close"),
  scrim: $("scrim"),
  drawerSummary: $("drawer-summary"),
  serverList: $("server-list"),
  upgradeList: $("upgrade-list"),
  achievementList: $("achievement-list"),
  tabs: Array.from(document.querySelectorAll(".tab")),
  panels: Array.from(document.querySelectorAll(".tab-panel")),
  menu: $("menu"),
  menuOpen: $("menu-open"),
  help: $("help-dialog"),
  helpOpen: $("help-open"),
  toast: $("toast-region"),
  loading: $("loading"),
  error: $("error"),
  retry: $("retry"),
};

let scene = null;
let data = null;
let cursor = 0;
let primed = false;
let seen = new Set();
let feed = [];
let lastLevel = null;
let levelSeen = null;
let failures = 0;
let fetching = false;
let pollTimer = 0;
let drawerIsOpen = false;
let menuIsOpen = false;
let renderSignature = "";
let baseRequests = 0;
let baseAt = 0;
let liveRps = 0;
let receivedWall = Date.now();
let serverNow = 0;

function compact(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "–";
  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  for (const [size, suffix] of [[1e12, "조"], [1e8, "억"], [1e4, "만"]]) {
    if (abs >= size) {
      const scaled = abs / size;
      const digits = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2;
      return sign + trimDecimal(scaled, digits) + suffix;
    }
  }
  if (abs >= 100) return sign + Math.round(abs).toLocaleString("ko-KR");
  return sign + trimDecimal(abs, abs < 10 ? 1 : 0);
}

function trimDecimal(value, digits) {
  return String(parseFloat(value.toFixed(digits)));
}

function rate(value) {
  const n = Number(value) || 0;
  if (n >= 100) return compact(n);
  return trimDecimal(n, n >= 10 ? 1 : 2);
}

function ago(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 5) return "방금";
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
  return `${Math.floor(s / 86400)}일 전`;
}

function since(value) {
  return serverNow ? serverNow - Number(value || 0) : 0;
}

function setSync(text, state = "") {
  if (!els.sync) return;
  els.sync.textContent = text;
  els.live?.classList.toggle("is-stale", state === "stale");
  els.live?.classList.toggle("is-error", state === "error");
}

function getLowMotion() {
  try {
    return localStorage.getItem(MOTION_KEY) === "low";
  } catch (error) {
    return false;
  }
}

function setLowMotion(low) {
  try {
    localStorage.setItem(MOTION_KEY, low ? "low" : "auto");
  } catch (error) { /* private mode */ }
  document.documentElement.dataset.motion = low ? "low" : "auto";
  if (scene) {
    scene.lowMotion = low;
    scene.setMotion(!prefersReduced.matches && !low);
  }
  const item = els.menu.querySelector('[data-action="motion"]');
  if (item) {
    item.setAttribute("aria-pressed", low ? "true" : "false");
    item.textContent = low ? "연출 켜기" : "연출 줄이기";
  }
}

function toast(message, gold = false) {
  if (!els.toast) return;
  const item = document.createElement("p");
  item.className = gold ? "toast is-gold" : "toast";
  item.textContent = message;
  els.toast.append(item);
  while (els.toast.children.length > 3) els.toast.firstElementChild.remove();
  setTimeout(() => {
    item.style.opacity = "0";
    item.style.transform = "translateY(6px)";
    setTimeout(() => item.remove(), 260);
  }, 4200);
}

function tierLabel(id) {
  return TIER_BY_ID.get(id)?.short || id || "설비";
}

function upgradeName(id) {
  const found = data?.catalog?.upgrades?.find((upgrade) => upgrade.id === id);
  return found?.name || "업그레이드";
}

function eventNode(event) {
  const li = document.createElement("li");
  li.className = `ev-${event.kind}`;
  const time = document.createElement("time");
  time.dateTime = new Date((event.at || 0) * 1000).toISOString();
  time.dataset.at = String(event.at || 0);
  time.textContent = ago(since(event.at));
  const text = document.createElement("span");

  if (event.kind === "install") {
    const bold = document.createElement("b");
    bold.textContent = `${tierLabel(event.tier)} ×${event.qty ?? 1}`;
    text.append(bold, " 설치");
  } else if (event.kind === "upgrade") {
    const bold = document.createElement("b");
    bold.textContent = `${upgradeName(event.upgrade)} Lv.${event.level ?? "?"}`;
    text.append(bold, " 업그레이드");
  } else if (event.kind === "levelup") {
    const bold = document.createElement("b");
    bold.textContent = `레벨 ${event.level ?? "?"}`;
    text.append(bold, " 달성");
  } else if (event.kind === "achievement") {
    const bold = document.createElement("b");
    bold.textContent = ACHIEVEMENT_NAMES[event.achievement] || "업적";
    text.append("업적 달성 · ", bold);
  } else if (event.kind === "reboot") {
    const bold = document.createElement("b");
    bold.textContent = `토큰 +${event.tokens ?? 0}`;
    text.append("월드 Reboot · ", bold);
  } else if (event.kind === "reset") {
    text.append("월드가 초기화되었습니다");
  } else {
    text.append("월드 이벤트");
  }
  li.append(time, text);
  return li;
}

function renderFeed(newestId = null) {
  if (!els.feedList) return;
  els.feedList.replaceChildren();
  if (!feed.length) {
    els.feedEmpty.hidden = false;
    els.feedList.hidden = true;
    return;
  }
  els.feedEmpty.hidden = true;
  els.feedList.hidden = false;
  for (const event of feed) {
    const node = eventNode(event);
    if (newestId !== null && event.id === newestId) node.classList.add("is-new");
    els.feedList.append(node);
  }
}

function pushEvents(events, animate) {
  const fresh = events
    .filter((event) => Number.isFinite(event.id) && !seen.has(event.id))
    .sort((a, b) => a.id - b.id);
  for (const event of fresh) {
    seen.add(event.id);
    feed.unshift(event);
    if (animate) {
      if (event.kind !== "install") scene?.handleEvent(event);
      announceEvent(event);
    }
  }
  feed = feed.slice(0, FEED_LIMIT);
  renderFeed(fresh.length ? fresh[fresh.length - 1].id : null);
}

function announceEvent(event) {
  if (event.kind === "levelup") {
    toast(`운영 레벨 ${event.level ?? "?"} 달성`, true);
  } else if (event.kind === "achievement") {
    toast(`업적 달성 · ${ACHIEVEMENT_NAMES[event.achievement] || "업적"}`, true);
  } else if (event.kind === "install") {
    toast(`${tierLabel(event.tier)} ×${event.qty ?? 1} 설치가 반영되었습니다`);
  } else if (event.kind === "upgrade") {
    toast(`${upgradeName(event.upgrade)} Lv.${event.level ?? "?"} 업그레이드`);
  } else if (event.kind === "reboot") {
    toast(`월드 Reboot · 가동 토큰 +${event.tokens ?? 0}`, true);
  } else if (event.kind === "reset") {
    toast("월드가 초기화되었습니다", true);
  }
}

function renderHud() {
  if (!data) return;
  const { state, derived } = data;
  els.level.textContent = derived.level;
  els.levelBonus.textContent = `+${Math.round((derived.level_bonus || 0) * 100)}% RPS`;
  const progress = Math.round((derived.level_progress || 0) * 100);
  els.levelBar.style.width = `${progress}%`;
  els.levelTrack.setAttribute("aria-valuenow", String(progress));
  const remaining = Math.max(0, (derived.next_level_at || 0) - (state.total_lifetime || 0));
  els.levelNext.textContent = `다음 레벨까지 ${compact(remaining)} 요청`;
  els.rps.textContent = rate(derived.rps);
  els.rps.title = `${Number(derived.rps).toLocaleString("ko-KR")} RPS`;
  baseRequests = Number(state.requests) || 0;
  baseAt = performance.now();
  els.requests.textContent = compact(baseRequests);
  els.requests.title = `${Math.floor(baseRequests).toLocaleString("ko-KR")} 요청`;
  const thermal = Math.round((derived.thermal_pct || 0) * 100);
  els.heat.textContent = `${thermal}%`;
  els.heat.title = `열 ${Math.round(derived.heat || 0)} · 냉각 ${Math.round(derived.cooling || 0)}`;
  els.tokens.textContent = compact(state.tokens || 0);
  els.tokens.title = `${Number(state.tokens || 0).toLocaleString("ko-KR")} 토큰`;
}

function renderSignatureNow() {
  if (!data) return "";
  const state = data.state;
  return JSON.stringify([state.servers, state.upgrades, state.achievements, data.derived.level, state.tokens]);
}

function renderDrawer(force = false) {
  if (!data) return;
  const signature = renderSignatureNow();
  if (!force && signature === renderSignature) return;
  renderSignature = signature;
  const { state, derived, catalog } = data;
  const unlocked = new Set(state.achievements || []);
  els.drawerSummary.textContent =
    `${derived.total_servers}대 가동 · RPS ${rate(derived.rps)} · 업적 ${unlocked.size}/${catalog.achievements.length}`;

  els.serverList.replaceChildren();
  for (const tier of TIERS) {
    const cat = catalog.tiers.find((item) => item.id === tier.id) || {};
    const owned = Number(state.servers[tier.id] || 0);
    const card = document.createElement("article");
    card.className = owned ? "card" : "card is-empty";
    const icon = document.createElement("span");
    icon.className = "card-icon";
    icon.style.color = tier.color;
    icon.textContent = tier.letter;
    const body = document.createElement("div");
    const name = document.createElement("h3");
    name.className = "card-name";
    name.textContent = tier.name;
    const meta = document.createElement("p");
    meta.className = "card-meta";
    meta.textContent = `기당 ${rate(cat.rps || 0)} RPS · 발열 ${cat.heat ?? "?"}`;
    const effect = document.createElement("p");
    effect.className = "card-effect";
    effect.textContent = owned
      ? `현재 ${compact(owned * (cat.rps || 0))} RPS 기여 · 다음 증설 ${compact(cat.cost || 0)} 요청`
      : `미설치 · 증설 비용 ${compact(cat.cost || 0)} 요청`;
    body.append(name, meta, effect);
    const count = document.createElement("div");
    count.className = "card-count";
    const strong = document.createElement("strong");
    strong.textContent = owned ? `×${owned}` : "—";
    strong.style.color = owned ? tier.color : "";
    const label = document.createElement("span");
    label.textContent = owned ? "보유" : "대기";
    count.append(strong, label);
    card.append(icon, body, count);
    els.serverList.append(card);
  }

  els.upgradeList.replaceChildren();
  for (const cat of catalog.upgrades) {
    const info = UPGRADES[cat.id] || { letter: "?", color: "#b58bff" };
    const card = document.createElement("article");
    card.className = cat.level ? "card" : "card is-empty";
    const icon = document.createElement("span");
    icon.className = "card-icon";
    icon.style.color = info.color;
    icon.textContent = info.letter;
    const body = document.createElement("div");
    const name = document.createElement("h3");
    name.className = "card-name";
    name.textContent = cat.name;
    const effect = document.createElement("p");
    effect.className = "card-effect";
    effect.textContent = cat.desc;
    const meta = document.createElement("p");
    meta.className = "card-meta";
    meta.textContent = cat.cost === null || cat.cost === undefined
      ? "최대 레벨"
      : `다음 업그레이드 ${compact(cat.cost)} 요청`;
    const pips = document.createElement("div");
    pips.className = "level-pips";
    pips.setAttribute("aria-hidden", "true");
    for (let i = 0; i < cat.max; i += 1) {
      const pip = document.createElement("i");
      if (i < cat.level) pip.className = "on";
      pips.append(pip);
    }
    body.append(name, effect, meta, pips);
    const count = document.createElement("div");
    count.className = "card-count";
    const strong = document.createElement("strong");
    strong.textContent = `${cat.level}/${cat.max}`;
    const label = document.createElement("span");
    label.textContent = "레벨";
    count.append(strong, label);
    card.append(icon, body, count);
    els.upgradeList.append(card);
  }

  els.achievementList.replaceChildren();
  for (const item of catalog.achievements) {
    const unlockedNow = unlocked.has(item.id);
    const row = document.createElement("article");
    row.className = unlockedNow ? "achievement" : "achievement is-locked";
    const badge = document.createElement("span");
    badge.className = "achievement-badge";
    badge.setAttribute("aria-hidden", "true");
    badge.textContent = unlockedNow ? "★" : "·";
    const body = document.createElement("div");
    const name = document.createElement("h3");
    name.textContent = item.name;
    const desc = document.createElement("p");
    desc.textContent = unlockedNow ? item.desc : "잠김 · " + item.desc;
    body.append(name, desc);
    const bonus = document.createElement("span");
    bonus.className = "achievement-bonus";
    bonus.textContent = `+${Math.round((item.bonus || 0) * 100)}%`;
    row.append(badge, body, bonus);
    els.achievementList.append(row);
  }
}

function ingest(payload) {
  if (!payload || !payload.state || !payload.derived || !payload.catalog) return false;
  const wasPrimed = primed;
  const previousLevel = lastLevel;
  data = payload;
  serverNow = Number(payload.serverTime) || Date.now() / 1000;
  receivedWall = Date.now();
  liveRps = Number(payload.derived.rps) || 0;
  if (payload.cursorReset || payload.eventGap) {
    seen.clear();
    feed = [];
    if (payload.cursorReset) cursor = 0;
  }
  cursor = Math.max(cursor, Number(payload.cursor) || 0);
  const events = Array.isArray(payload.events) ? payload.events : [];
  const sawLevelEvent = wasPrimed && events.some((event) => event.kind === "levelup");
  pushEvents(events, wasPrimed && !payload.cursorReset && !payload.eventGap);
  renderHud();
  scene?.setWorld(payload.state, payload.derived);
  renderDrawer();

  if (wasPrimed && previousLevel !== null && payload.derived.level > previousLevel && !sawLevelEvent) {
    scene?.handleEvent({ kind: "levelup", level: payload.derived.level });
    toast(`운영 레벨 ${payload.derived.level} 달성`, true);
  }
  levelSeen = payload.derived.level;
  lastLevel = payload.derived.level;
  primed = true;
  return true;
}

function requestUrl() {
  const sinceValue = primed ? cursor : 0;
  return `api/v1/state?since=${encodeURIComponent(sinceValue)}`;
}

async function fetchState() {
  if (fetching) return;
  clearTimeout(pollTimer);
  if (document.hidden) return;
  fetching = true;
  setSync(primed ? "동기화 중…" : "접속 중…");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(requestUrl(), {
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!ingest(payload)) throw new Error("malformed payload");
    failures = 0;
    els.loading.hidden = true;
    els.error.hidden = true;
    setSync("공유 월드 · 동기화됨");
    schedule(POLL_MS);
  } catch (error) {
    failures += 1;
    setSync("연결 재시도 중", "error");
    if (!primed) {
      els.loading.hidden = true;
      els.error.hidden = false;
    } else if (failures === 1) {
      toast("월드와 동기화하지 못했습니다. 자동으로 재시도합니다.");
    }
    const backoff = Math.min(30000, 3000 * Math.max(1, failures));
    schedule(backoff);
  } finally {
    clearTimeout(timer);
    fetching = false;
  }
}

function schedule(ms) {
  clearTimeout(pollTimer);
  if (document.hidden) return;
  pollTimer = setTimeout(fetchState, ms);
}

function openDrawer() {
  if (drawerIsOpen) return;
  drawerIsOpen = true;
  closeMenu();
  els.drawer.hidden = false;
  els.scrim.hidden = false;
  document.body.classList.add("is-locked");
  renderDrawer(true);
  requestAnimationFrame(() => {
    els.scrim.classList.add("is-open");
    els.drawer.classList.add("is-open");
  });
  els.drawerOpen.setAttribute("aria-expanded", "true");
  setTimeout(() => els.drawerClose.focus(), 30);
}

function closeDrawer(restore = true) {
  if (!drawerIsOpen) return;
  drawerIsOpen = false;
  els.scrim.classList.remove("is-open");
  els.drawer.classList.remove("is-open");
  els.drawerOpen.setAttribute("aria-expanded", "false");
  document.body.classList.remove("is-locked");
  setTimeout(() => {
    if (!drawerIsOpen) {
      els.drawer.hidden = true;
      els.scrim.hidden = true;
    }
  }, 280);
  if (restore) els.drawerOpen.focus();
}

function openMenu() {
  menuIsOpen = true;
  els.menu.hidden = false;
  els.menuOpen.setAttribute("aria-expanded", "true");
  els.menu.querySelector(".menu-item")?.focus();
}

function closeMenu(restore = false) {
  if (!menuIsOpen) return;
  menuIsOpen = false;
  els.menu.hidden = true;
  els.menuOpen.setAttribute("aria-expanded", "false");
  if (restore) els.menuOpen.focus();
}

function selectTab(tab) {
  for (const item of els.tabs) {
    const active = item === tab;
    item.classList.toggle("is-active", active);
    item.setAttribute("aria-selected", active ? "true" : "false");
    item.tabIndex = active ? 0 : -1;
  }
  for (const panel of els.panels) panel.hidden = panel.id !== tab.getAttribute("aria-controls");
}

function trapFocus(event) {
  if (event.key !== "Tab" || !drawerIsOpen) return;
  const focusable = Array.from(
    els.drawer.querySelectorAll('a[href], button:not([disabled]), [tabindex="0"]')
  ).filter((element) => element.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function wire() {
  els.drawerOpen.addEventListener("click", openDrawer);
  els.drawerClose.addEventListener("click", () => closeDrawer());
  els.scrim.addEventListener("click", () => closeDrawer());
  els.menuOpen.addEventListener("click", () => (menuIsOpen ? closeMenu(true) : openMenu()));
  els.menu.addEventListener("click", (event) => {
    const item = event.target.closest(".menu-item");
    if (!item) return;
    const action = item.dataset.action;
    if (action === "help") {
      closeMenu();
      els.help.showModal();
    } else if (action === "motion") {
      setLowMotion(!getLowMotion());
    } else if (action === "fullscreen") {
      closeMenu();
      toggleFullscreen();
    } else {
      closeMenu();
    }
  });
  els.helpOpen.addEventListener("click", () => els.help.showModal());
  els.help.addEventListener("click", (event) => {
    if (event.target === els.help) els.help.close();
  });
  els.help.addEventListener("close", () => els.helpOpen.focus());
  els.retry.addEventListener("click", () => {
    els.error.hidden = true;
    els.loading.hidden = false;
    fetchState();
  });
  for (const tab of els.tabs) {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", (event) => {
      const index = els.tabs.indexOf(tab);
      let next = null;
      if (event.key === "ArrowRight") next = els.tabs[(index + 1) % els.tabs.length];
      if (event.key === "ArrowLeft") next = els.tabs[(index - 1 + els.tabs.length) % els.tabs.length];
      if (event.key === "Home") next = els.tabs[0];
      if (event.key === "End") next = els.tabs[els.tabs.length - 1];
      if (next) {
        event.preventDefault();
        selectTab(next);
        next.focus();
      }
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (menuIsOpen) {
        event.preventDefault();
        closeMenu(true);
      } else if (drawerIsOpen) {
        event.preventDefault();
        closeDrawer();
      }
    }
    trapFocus(event);
  });
  document.addEventListener("click", (event) => {
    if (menuIsOpen && !els.menu.contains(event.target) && !els.menuOpen.contains(event.target)) {
      closeMenu();
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearTimeout(pollTimer);
    } else {
      fetchState();
    }
  });
  prefersReduced.addEventListener?.("change", () => {
    setLowMotion(getLowMotion());
  });
  window.addEventListener("pagehide", () => clearTimeout(pollTimer));
}

function toggleFullscreen() {
  if (document.fullscreenElement) {
    document.exitFullscreen?.().catch(() => {});
    return;
  }
  document.documentElement.requestFullscreen?.().catch(() => {
    toast("이 브라우저에서는 전체 화면을 사용할 수 없습니다.");
  });
}

function tick() {
  if (document.hidden || !data) return;
  const elapsed = Math.max(0, (performance.now() - baseAt) / 1000);
  const live = baseRequests + liveRps * elapsed;
  els.requests.textContent = compact(live);
}

function refreshTimes() {
  if (document.hidden || !primed) return;
  const now = serverNow + (Date.now() - receivedWall) / 1000;
  for (const time of els.feedList.querySelectorAll("time")) {
    time.textContent = ago(now - Number(time.dataset.at || 0));
  }
}

function init() {
  const lowMotion = getLowMotion();
  document.documentElement.dataset.motion = lowMotion ? "low" : "auto";
  scene = new WorldScene($("scene"), { lowMotion });
  window.__scene = scene;
  setLowMotion(lowMotion);
  wire();
  setSync("접속 중…");
  fetchState();
  setInterval(tick, 500);
  setInterval(refreshTimes, 15000);
}

init();
