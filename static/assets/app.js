const $ = (id) => document.getElementById(id);
const api = "./api/v1";
const root = document.documentElement;
const formatter = new Intl.NumberFormat("ko-KR");
const rebootDialog = $("reboot-dialog");
const refreshInterval = 15000;
const agentStatus = {
  coding: "코딩 중", testing: "테스트 중", blocked: "차단됨", waiting: "대기 중",
  completed: "완료", failed: "실패", stale: "관측 만료",
};

let registry = null;
let snapshot = null;
let selected = null;
let intent = null;
let busy = false;
let fetching = false;
let requestVersion = 0;
let csrf = "";

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function age(at) {
  if (!at || !Number.isFinite(at)) return "관측 기록 없음";
  const minutes = Math.max(0, Math.floor((Date.now() / 1000 - at) / 60));
  if (minutes < 1) return "1분 이내";
  if (minutes < 60) return `${minutes}분 전`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}시간 전`;
  return `${Math.floor(minutes / 1440)}일 전`;
}

async function sessionToken() {
  try {
    const response = await fetch("/auth/session", {
      credentials: "same-origin",
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
      headers: { Accept: "application/json" },
    });
    if (response.ok) {
      const session = await response.json();
      if (session.authenticated && typeof session.csrf === "string") return session.csrf;
    }
  } catch (error) { /* The auth proxy remains authoritative when a token is unavailable. */ }
  return "";
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  const write = options.method === "POST";
  if (write) {
    csrf = csrf || await sessionToken();
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const settings = {
    credentials: "same-origin",
    cache: "no-store",
    signal: AbortSignal.timeout(12000),
    ...options,
    headers,
  };
  let response = await fetch(path, settings);
  if (write && response.status === 403) {
    const updated = await sessionToken();
    if (updated && updated !== csrf) {
      csrf = updated;
      headers.set("X-CSRF-Token", updated);
      response = await fetch(path, { ...settings, signal: AbortSignal.timeout(12000) });
    }
  }
  if (response.status === 401) csrf = "";
  return response;
}

function isSnapshot(data) {
  return data?.version === 1 && Array.isArray(data.projects);
}

let storage = null;
try { storage = window.sessionStorage; } catch (error) { /* blocked storage */ }
const operations = window.HD2OperationCoordinator.createOperationCoordinator({
  fetch: request,
  storage,
  key: "hd2lab-idle-ops-r3-reboot",
  isSuccess: isSnapshot,
});

function setSync(message, mode) {
  $("sync-state").textContent = message;
  $("sync-state").className = `sync is-${mode}`;
}

function report(message, good = false) {
  const notice = $("notice");
  notice.textContent = message;
  notice.dataset.good = String(good);
  notice.hidden = false;
}

function authRequired() {
  $("auth-panel").hidden = false;
  $("console").hidden = true;
  $("error-banner").hidden = true;
  $("notice").hidden = true;
  if (rebootDialog.open) rebootDialog.close();
  setSync("로그인 필요", "error");
}

function updateRepoList() {
  const projects = registry.projects;
  $("collector-status").textContent = registry.collectorConfigured ? "GitHub 수집기 설정됨" : "GitHub 수집기 설정 대기";
  const empty = $("repo-empty");
  empty.textContent = "아직 등록된 저장소가 없습니다. 연동 설정 안내에서 등록 절차를 확인할 수 있습니다.";
  empty.hidden = projects.length > 0;
  $("repo-list").replaceChildren(...projects.map((project) => {
    const item = node("li");
    const copy = node("div");
    const refs = project.trackedRefs?.length ? project.trackedRefs.join(", ") : "등록된 ref 없음";
    const delayed = project.stale || !project.observedAt;
    const freshness = delayed && project.observedAt ? `관측 지연 · ${age(project.observedAt)}` : age(project.observedAt);
    copy.append(
      node("h3", project.name),
      node("p", `${project.public ? "공개 관전 허용" : "관리자 전용"} · ${refs}`),
      node("p", `${project.repository} · ${freshness}`, delayed ? "is-stale" : ""),
    );
    const backoff = project.retryAt > Date.now() / 1000;
    if (backoff) {
      const retry = new Date(project.retryAt * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
      copy.append(node("p", `다음 수집 가능 시각 ${retry}`, "is-stale"));
    }
    const button = node("button", project.pending ? "동기화 대기 중" : "동기화 요청", "button");
    button.type = "button";
    button.disabled = busy || project.pending || backoff || !registry.collectorConfigured;
    button.setAttribute("aria-label", `${project.name} ${button.textContent}`);
    button.addEventListener("click", () => reconcile(project.id));
    item.append(copy, button);
    return item;
  }));

  $("agent-empty").textContent = "등록된 에이전트가 없습니다.";
  $("agent-empty").hidden = registry.agents.length > 0;
  const names = new Map(projects.map(({ id, name }) => [String(id), name]));
  const observations = new Map();
  for (const observed of snapshot?.agents ?? []) {
    const records = observations.get(observed.name) ?? [];
    records.push(observed);
    observations.set(observed.name, records);
  }
  $("agent-list").replaceChildren(...registry.agents.map((agent) => {
    const item = node("li");
    const copy = node("div");
    const scopes = (agent.projectIds ?? []).map((id) => names.get(String(id)) ?? String(id));
    copy.append(node("h3", agent.id), node("p", scopes.join(", ") || "등록된 프로젝트 없음"));
    const records = observations.get(agent.id) ?? [];
    if (!records.length) copy.append(node("p", "관측 기록 없음"));
    for (const observed of records) {
      const status = observed.stale ? "stale" : observed.status;
      const project = names.get(String(observed.projectId)) ?? observed.projectId;
      const progress = status !== "stale" && Number.isFinite(observed.progress) ? ` ${observed.progress}%` : "";
      copy.append(node("p", `${project} · ${agentStatus[status] ?? "상태 확인 중"}${progress} · ${age(observed.observedAt)}`,
        observed.stale ? "is-stale" : ""));
    }
    item.append(copy);
    return item;
  }));
}

function currentProject(id = selected) {
  return snapshot?.projects.find((item) => String(item.id) === id);
}

function updateReboot() {
  const projects = snapshot?.projects ?? [];
  if (!currentProject()) selected = projects.length ? String(projects[0].id) : null;
  const picker = $("reboot-project");
  const options = projects.length ? projects.map(({ id, name }) => [String(id), name]) : [["", "연결된 프로젝트 없음"]];
  if (options.length !== picker.options.length || options.some(([id, name], i) => {
    return picker.options[i].value !== id || picker.options[i].textContent !== name;
  })) {
    picker.replaceChildren(...options.map(([id, name]) => {
      const option = node("option", name);
      option.value = id;
      return option;
    }));
  }
  picker.value = selected ?? "";
  picker.disabled = !projects.length || busy;

  const project = currentProject();
  const eligible = project?.rebootEligible === true;
  const progress = $("reboot-progress");
  const required = project?.rebootRequired ?? 100;
  const remaining = Math.max(0, required - (project?.runCommits ?? 0));
  $("reboot-metrics").hidden = !project;
  if (project) {
    $("project-level").textContent = formatter.format(project.level);
    $("project-bonus").textContent = `×${formatter.format(project.bonusBps / 10000)}`;
    $("project-reboots").textContent = formatter.format(project.reboots);
    progress.setAttribute("aria-label", "Reboot에 필요한 새 커밋");
    progress.max = required;
    progress.value = project.runCommits;
  }
  progress.hidden = !project;
  const status = $("reboot-eligibility");
  status.dataset.ready = String(eligible);
  status.textContent = eligible ? "Reboot 가능" : project
    ? `현재 주기 ${formatter.format(project.runCommits)} / ${required} 커밋 · ${remaining}개 남음`
    : "연결된 프로젝트가 없습니다.";
  $("resume-op").hidden = !operations.hasPending();
  $("resume-op").disabled = busy;
  $("reboot-open").disabled = !eligible || operations.hasPending() || busy;
  $("reboot-confirm").disabled = busy || !currentProject(intent)?.rebootEligible;
  $("reboot-cancel").disabled = busy;
}

async function load({ force = false } = {}) {
  if ((fetching || busy) && !force) return;
  fetching = true;
  const version = ++requestVersion;
  $("refresh").disabled = true;
  try {
    const responses = await Promise.all([request(`${api}/state`), request(`${api}/integrations`)]);
    if (version !== requestVersion) return;
    if (responses.some((response) => response.status === 401)) {
      authRequired();
      return;
    }
    if (responses.some((response) => !response.ok)) {
      throw new Error(responses.some((response) => response.status === 403)
        ? "Idle Ops 관리자 권한을 확인하지 못했습니다."
        : "연동 정보를 불러오지 못했습니다.");
    }
    const [state, integrations] = await Promise.all(responses.map((response) => response.json()));
    if (version !== requestVersion) return;
    if (!isSnapshot(state) || !Array.isArray(integrations.projects) || !Array.isArray(integrations.agents)) {
      throw new Error("연동 응답 형식을 확인하지 못했습니다.");
    }
    snapshot = state;
    registry = integrations;
    $("auth-panel").hidden = true;
    $("console").hidden = false;
    $("error-banner").hidden = true;
    updateRepoList();
    updateReboot();
    setSync("연결됨", "live");
  } catch (error) {
    if (version !== requestVersion) return;
    $("error-text").textContent = snapshot ? "연결이 지연되어 마지막 상태를 표시합니다." :
      (error.message.includes("Idle Ops 관리자") ? error.message : "관제 운영 정보를 불러오지 못했습니다.");
    $("error-banner").hidden = false;
    setSync(snapshot ? "연결 지연" : "연결 실패", "stale");
  } finally {
    if (version === requestVersion) {
      fetching = false;
      $("refresh").disabled = false;
    }
  }
}

async function reboot(recover = false) {
  if (busy) return;
  const project = currentProject(intent);
  if (!recover && (!project?.rebootEligible || operations.hasPending())) return;
  busy = true;
  updateReboot();
  try {
    const result = recover ? await operations.recover() : await operations.submit({
      path: `${api}/actions`,
      signature: `reboot:${project.id}`,
      fields: { action: { type: "reboot", projectId: project.id } },
    });
    if (result.status === "ok") {
      snapshot = result.data;
      report("Reboot를 반영했습니다.", true);
      if (rebootDialog.open) rebootDialog.close();
    } else if (result.status === "auth") {
      authRequired();
    } else if (result.status === "rejected") {
      report("Reboot 요청이 거부되었습니다. 프로젝트 자격과 로그인 권한을 확인해 주세요.");
      if (rebootDialog.open) rebootDialog.close();
    } else if (result.status === "retry") {
      report("Reboot 응답을 확인하지 못했습니다. 이전 요청 확인에서 같은 요청을 이어서 확인할 수 있습니다.");
      if (rebootDialog.open) rebootDialog.close();
    }
  } finally {
    busy = false;
    updateReboot();
    if ($("auth-panel").hidden) load({ force: true });
  }
}

async function reconcile(projectId) {
  if (busy) return;
  busy = true;
  updateRepoList();
  updateReboot();
  try {
    const response = await request(`${api}/reconcile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ projectId }),
    });
    if (response.status === 401) {
      authRequired();
    } else if (response.ok) {
      report("저장소 동기화를 요청했습니다.", true);
    } else {
      report("동기화 요청이 거부되었습니다. 수집기 설정과 관리자 권한을 확인해 주세요.");
    }
  } catch (error) {
    report("동기화 요청 상태를 확인할 수 없습니다. 저장소 상태를 새로고침해 주세요.");
  } finally {
    busy = false;
    updateRepoList();
    updateReboot();
    if ($("auth-panel").hidden) load({ force: true });
  }
}

function setTheme(theme) {
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  $("theme-toggle").setAttribute("aria-pressed", String(theme === "dark"));
  document.querySelector('meta[name="theme-color"]').content = theme === "dark" ? "#0d0d15" : "#f5f5fb";
}

function bindShell() {
  const apply = (shell) => {
    const sync = () => root.style.setProperty("--hq-shell-h", `${Math.round(shell.getBoundingClientRect().height)}px`);
    sync();
    if ("ResizeObserver" in window) new ResizeObserver(sync).observe(shell);
  };
  const existing = document.querySelector("hq-admin-shell");
  if (existing) {
    apply(existing);
    return;
  }
  const observer = new MutationObserver(() => {
    const shell = document.querySelector("hq-admin-shell");
    if (shell) {
      observer.disconnect();
      apply(shell);
    }
  });
  observer.observe(document.body, { childList: true });
}

if (window.self !== window.top) {
  window.hd2LauncherControls = {
    goBack() {
      const dialog = document.querySelector("dialog[open]");
      if (!dialog || (dialog === rebootDialog && busy)) return false;
      dialog.close();
      return true;
    },
  };
  window.addEventListener("message", (event) => {
    if (event.source !== window.parent) return;
    if (event.data?.type === "hd2:viewport" && Number.isFinite(event.data.height)) {
      root.style.setProperty("--app-vh", `${Math.round(event.data.height)}px`);
    }
  });
}

$("theme-toggle").addEventListener("click", () => {
  const theme = root.dataset.theme === "dark" ? "light" : "dark";
  setTheme(theme);
  try { localStorage.setItem("hd2lab-theme", theme); } catch (error) { /* blocked storage */ }
});
window.addEventListener("storage", (event) => {
  if (event.key === "hd2lab-theme" && ["dark", "light"].includes(event.newValue)) setTheme(event.newValue);
});
$("setup-open").addEventListener("click", () => $("setup-dialog").showModal());
$("reboot-project").addEventListener("change", (event) => {
  selected = event.target.value;
  updateReboot();
});
$("reboot-open").addEventListener("click", () => {
  const project = currentProject();
  if (!project?.rebootEligible) return;
  intent = selected;
  $("reboot-confirm-copy").textContent = `${project.name}의 이번 주기 커밋 수를 초기화합니다. 랙, 기존 XP, 해결되지 않은 버그는 유지됩니다.`;
  updateReboot();
  rebootDialog.showModal();
});
rebootDialog.addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
$("reboot-cancel").addEventListener("click", () => rebootDialog.close());
$("reboot-confirm").addEventListener("click", () => reboot());
$("resume-op").addEventListener("click", () => reboot(true));
$("retry-button").addEventListener("click", () => load());
$("refresh").addEventListener("click", () => load());
window.addEventListener("online", () => load());
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
setInterval(() => { if (!document.hidden && $("auth-panel").hidden) load(); }, refreshInterval);
setTheme(root.dataset.theme);
bindShell();
if (operations.hasPending()) reboot(true);
else load();
