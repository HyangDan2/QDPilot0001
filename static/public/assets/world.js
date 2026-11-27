import { WorldScene } from "./engine.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const demo = params.get("demo") === "1";
const mobile = window.matchMedia("(max-width: 760px)");
const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
const motionKey = "idle-ops-motion";
const pollInterval = 8000;
const feedLimit = 40;
const formatter = new Intl.NumberFormat("ko-KR");
const activityLabels = { commit: "커밋", issue: "이슈", pull: "PR", ci: "CI", release: "릴리스" };
const eventLabels = {
  commit: "커밋 반영", levelup: "레벨 상승", install: "랙 설치", reboot: "Reboot",
  bug_opened: "버그 감지", bug_resolved: "버그 해결", bug_dismissed: "버그 닫힘",
  agent: "에이전트 상태 변경", pull: "PR 변경", release: "릴리스", repair: "수리 작업",
};
const agentLabels = {
  coding: "코딩 중", testing: "테스트 중", blocked: "차단됨", waiting: "대기 중",
  completed: "완료", failed: "실패", stale: "관측 만료",
};

let snapshot = null;
let feed = [];
let cursor = null;
let selected = null;
let polling = false;
let scene = null;
let lowMotion = false;
let gap = false;
let resumed = false;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function age(at) {
  if (!at || !Number.isFinite(at)) return "관측 기록 없음";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - at));
  if (seconds < 60) return "1분 이내";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 전`;
  return `${Math.floor(seconds / 86400)}일 전`;
}

function timeNode(at) {
  const time = node("time", age(at));
  const date = new Date(at * 1000);
  if (at && Number.isFinite(date.getTime())) {
    time.dateTime = date.toISOString();
    time.title = date.toLocaleString("ko-KR");
  }
  return time;
}

function sync(text, indicator) {
  $("sync-state").textContent = text;
  $("sync-state").dataset.indicator = indicator;
}

function setLowMotion(value) {
  lowMotion = value;
  if (scene) {
    scene.lowMotion = value;
    scene.setMotion(!value && !motion.matches);
  }
  const button = $("motion-toggle");
  button.setAttribute("aria-pressed", String(value));
  button.textContent = value ? "연출 켜기" : "연출 줄이기";
}

function renderProjectPanel(projects) {
  const filter = $("project-filter");
  const options = [["", "전체 프로젝트"], ...snapshot.projects.map(({ id, name }) => [String(id), name])];
  if (options.length !== filter.options.length || options.some(([id, name], index) => {
    const option = filter.options[index];
    return option.value !== id || option.textContent !== name;
  })) {
    filter.replaceChildren(...options.map(([id, name]) => {
      const option = node("option", name);
      option.value = id;
      return option;
    }));
  }
  filter.value = selected ?? "";
  filter.disabled = snapshot.projects.length === 0;

  const details = $("project-details");
  details.replaceChildren();
  if (!projects.length) {
    details.append(node("p", "공개된 프로젝트가 아직 없습니다.", "empty"));
    return;
  }
  const project = selected === null ? null : projects[0];
  const summary = node("div", undefined, "project-summary");
  const title = node("div", undefined, "project-title");
  title.append(node("h3", project?.name ?? "전체 프로젝트"), node("p", project ? `LV ${project.level}` : `${projects.length}개`));
  summary.append(title);
  const observation = node("p", undefined, "project-signal");
  if (project) {
    observation.append(timeNode(project.observedAt));
    if (project.stale) {
      observation.classList.add("is-stale");
      observation.prepend("GitHub 관측 지연 · ");
    } else if (project.observedAt) observation.prepend("최근 GitHub 관측 · ");
  } else {
    const delayed = projects.filter((item) => item.stale || !item.observedAt).length;
    observation.textContent = delayed ? `미관측 또는 관측 지연 ${delayed}개` : "GitHub 관측 정상";
    observation.classList.toggle("is-stale", delayed > 0);
  }
  summary.append(observation);
  const metrics = node("dl", undefined, "project-stats");
  const observed = projects.map((item) => item.development);
  const latest = (field) => observed.reduce((recent, entry) =>
    (entry?.[field] ?? 0) > (recent?.[field] ?? 0) ? entry : recent, null);
  const activity = latest("lastActivityAt");
  const activityDetails = node("div", undefined, "project-activity");
  const commitTime = node("p", "최근 커밋 · ");
  commitTime.append(timeNode(latest("lastCommitAt")?.lastCommitAt));
  const activityTime = node("p", "마지막 개발 활동 · ");
  if (activityLabels[activity?.lastActivityKind]) activityTime.append(activityLabels[activity.lastActivityKind], " · ");
  activityTime.append(timeNode(activity?.lastActivityAt));
  activityDetails.append(commitTime, activityTime);
  summary.append(activityDetails);
  const sumObserved = (value) => observed.every(Boolean)
    ? observed.reduce((sum, item) => sum + value(item), 0) : null;
  const entries = [
    ["PR 열림", sumObserved((item) => item.pulls.open)],
    ["PR 병합 (관측)", sumObserved((item) => item.pulls.merged)],
    ["열린 개발 작업", sumObserved((item) => item.openTasks)],
    ["CI 실행 중", sumObserved((item) => item.ci.running)],
    ["CI 통과", sumObserved((item) => item.ci.passed)],
    ["CI 실패", sumObserved((item) => item.ci.failed)],
    ["미해결 버그", observed.every(Boolean) ? projects.reduce((sum, item) => sum + item.bugCount, 0) : null],
  ];
  if (project) entries.push(["프로젝트 XP", project.xp]);
  for (const [label, value] of entries) {
    const metric = node("div");
    metric.append(node("dt", label), node("dd", Number.isFinite(value) ? formatter.format(value) : "–"));
    metrics.append(metric);
  }
  details.append(summary, metrics);
}

function renderFeed(names) {
  const events = feed.filter((event) => selected === null || String(event.projectId) === selected);
  const list = $("feed-list");
  list.replaceChildren(...events.map((event) => {
    const item = node("li", undefined, event.type);
    const title = node("strong", eventLabels[event.type] ?? "개발 활동");
    const name = names.get(String(event.projectId));
    if (name && selected === null) {
      const text = node("span");
      text.append(title, ` · ${name}`);
      item.append(timeNode(event.at), text);
    } else {
      item.append(timeNode(event.at), title);
    }
    return item;
  }));
  $("feed-scope").textContent = names.get(selected) ?? "전체 프로젝트";
  $("feed-empty").hidden = events.length > 0;
  $("feed-gap").hidden = !gap;
}

function renderAgents(names) {
  const agents = snapshot.agents.filter((agent) => selected === null || String(agent.projectId) === selected);
  $("agent-list").replaceChildren(...agents.map((agent) => {
    const item = node("li");
    const identity = node("div");
    const details = node("p");
    const name = names.get(String(agent.projectId));
    if (name) details.append(`${name} · `);
    details.append(timeNode(agent.observedAt));
    identity.append(node("strong", `에이전트 ${String(agent.id).slice(-6)}`), details);
    const status = agent.stale ? "stale" : agent.status;
    const statusText = agentLabels[status] ?? "상태 확인 중";
    if (status === "stale" && agentLabels[agent.lastStatus]) {
      details.append(` · 마지막 상태 ${agentLabels[agent.lastStatus]}`);
    }
    const progress = agent.progress;
    const confirmed = status !== "stale" && Number.isFinite(progress) && progress >= 0 && progress <= 100;
    const activity = node("span", confirmed ? `${statusText} · ${progress}%` : statusText, "agent-status");
    activity.dataset.status = status;
    item.append(identity, activity);
    return item;
  }));
  $("agent-empty").hidden = agents.length > 0;
}

function renderPages() {
  const page = scene?.page ?? 0;
  const pages = scene?.pageCount ?? 1;
  $("rack-pages").hidden = pages < 2;
  $("rack-page").textContent = `${page + 1} / ${pages}`;
  $("page-prev").disabled = page <= 0;
  $("page-next").disabled = page >= pages - 1;
}

function renderWorldHud(world) {
  $("world-hud").hidden = !world;
  if (!world) return;
  $("world-level").textContent = `월드 Lv ${formatter.format(world.level)}`;
  const progress = $("world-progress");
  progress.max = world.levelXpRequired;
  progress.value = world.levelXp;
  $("world-xp").textContent = `${formatter.format(world.levelXp)} / ${formatter.format(world.levelXpRequired)} XP`;
  $("world-hud").title = `누적 월드 XP ${formatter.format(world.xp)}`;
}

function render(events, animate) {
  renderWorldHud(snapshot.world);
  if (selected !== null && !snapshot.projects.some((item) => String(item.id) === selected)) selected = null;
  const projects = snapshot.projects.filter((item) => selected === null || String(item.id) === selected);
  const names = new Map(snapshot.projects.map((item) => [String(item.id), item.name]));
  const bugs = projects.reduce((sum, item) => sum + (item.bugCount ?? item.bugs?.length ?? 0), 0);
  $("room-caption").textContent = names.get(selected) ?? "실제 개발 활동을 게임 속 랙과 캐릭터로 표시합니다.";
  $("room-counts").textContent = `프로젝트 ${projects.length} · 미해결 버그 ${bugs}`;

  const projectId = projects.find((item) => String(item.id) === selected)?.id ?? null;
  scene?.setSnapshot({ ...snapshot, events }, { projectId, animate });
  renderPages();
  renderProjectPanel(projects);
  renderFeed(names);
  renderAgents(names);
}

async function load() {
  if (polling) return;
  polling = true;
  try {
    const query = new URLSearchParams();
    if (demo) query.set("demo", "1");
    if (cursor !== null) query.set("since", String(cursor));
    const response = await fetch(`./api/v1/state?${query}`, {
      credentials: "omit",
      cache: "no-store",
      signal: AbortSignal.timeout(12000),
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("State request failed");
    const result = await response.json();
    if (result.version !== 1 || !Array.isArray(result.projects) || !Array.isArray(result.agents) ||
        !Array.isArray(result.events) || !Number.isSafeInteger(result.cursor)) {
      throw new Error("Invalid state response");
    }
    const reset = Boolean(result.cursorReset || result.eventGap);
    const animate = !document.hidden && !resumed && snapshot !== null && cursor !== null && !reset;
    resumed = false;
    const fresh = reset || cursor === null ? result.events : result.events.filter((event) => event.id > cursor);
    if (reset) feed = [];
    gap = gap || reset;
    const byId = new Map([...feed, ...fresh].map((event) => [event.id, event]));
    feed = [...byId.values()].sort((a, b) => b.id - a.id).slice(0, feedLimit);
    snapshot = result;
    cursor = result.cursor;
    render(fresh, animate);
    sync(demo ? "데모 연결됨" : "연결됨", "live");
    $("connection-error").hidden = true;
  } catch (error) {
    sync(snapshot ? "연결 지연" : "연결 실패", "stale");
    $("connection-message").textContent = snapshot
      ? "연결이 지연되어 마지막으로 받은 상태를 표시합니다."
      : "서버에 연결하지 못했습니다.";
    $("connection-error").hidden = false;
  } finally {
    polling = false;
  }
}

const theater = $("theater");
const drawer = $("project-drawer");
const projectPanel = $("project-panel");
const projectHome = $("project-home");
const watch = $("watch");

function updateWatch() {
  const active = document.fullscreenElement === theater || theater.classList.contains("is-watch");
  watch.setAttribute("aria-pressed", String(active));
  watch.textContent = active ? "전체 화면 닫기" : "전체 화면";
}

async function toggleWatch() {
  if (document.fullscreenElement === theater) {
    await document.exitFullscreen();
  } else if (theater.classList.contains("is-watch")) {
    theater.classList.remove("is-watch");
  } else {
    try {
      if (!theater.requestFullscreen) throw new Error("Fullscreen unavailable");
      await theater.requestFullscreen();
    } catch (error) {
      theater.classList.add("is-watch");
    }
  }
  updateWatch();
}

try {
  const canvas = $("scene");
  if (!canvas.getContext("2d")) throw new Error("Canvas unavailable");
  scene = new WorldScene(canvas, { demo });
  if ("ResizeObserver" in window) new ResizeObserver(() => {
    scene.resize();
    renderPages();
  }).observe(canvas);
} catch (error) {
  $("canvas-fallback").hidden = false;
}
try { lowMotion = localStorage.getItem(motionKey) === "low"; } catch (error) { /* blocked storage */ }
setLowMotion(lowMotion);
$("motion-toggle").addEventListener("click", () => {
  setLowMotion(!lowMotion);
  try { localStorage.setItem(motionKey, lowMotion ? "low" : "auto"); } catch (error) { /* blocked storage */ }
});
$("demo-badge").hidden = !demo;
if (demo) {
  $("mode-link").href = "./";
  $("mode-link").textContent = "실시간 보기";
}
$("project-filter").addEventListener("change", (event) => {
  selected = event.target.value || null;
  if (snapshot) render([], false);
});
$("drawer-open").addEventListener("click", () => {
  $("drawer-close").hidden = false;
  drawer.append(projectPanel);
  drawer.showModal();
});
$("drawer-close").addEventListener("click", () => drawer.close());
drawer.addEventListener("close", () => {
  $("drawer-close").hidden = true;
  projectHome.append(projectPanel);
  if (mobile.matches) $("drawer-open").focus();
});
mobile.addEventListener("change", (event) => { if (!event.matches && drawer.open) drawer.close(); });
watch.addEventListener("click", toggleWatch);
document.addEventListener("fullscreenchange", updateWatch);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && theater.classList.contains("is-watch")) toggleWatch();
});
$("page-prev").addEventListener("click", () => { scene?.setPage(scene.page - 1); renderPages(); });
$("page-next").addEventListener("click", () => { scene?.setPage(scene.page + 1); renderPages(); });
$("retry").addEventListener("click", load);
window.addEventListener("online", load);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    // The current snapshot refreshes animation state. Events accumulated
    // while the tab was hidden remain readable in the activity feed.
    resumed = true;
    load();
  }
});
setInterval(() => { if (!document.hidden) load(); }, pollInterval);
load();
