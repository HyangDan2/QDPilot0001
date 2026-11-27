// One browser gate against a disposable local R3 instance with seeded data.
// Playwright is an optional QA dependency. CHROME_BIN may select a local Chrome.
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { randomBytes, createHash } from "node:crypto";
import { once } from "node:events";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { setTimeout as sleep } from "node:timers/promises";

const modulePath = process.env.PLAYWRIGHT_MODULE;
const { chromium } = await import(modulePath ? pathToFileURL(resolve(modulePath)) : "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const output = resolve(process.argv[2] || join(tmpdir(), "idle-ops-r3-screenshots"));
const scratch = await mkdtemp(join(tmpdir(), "idle-ops-r3-qa-"));
const db = join(scratch, "qa.sqlite3");
const port = process.env.QA_PORT || "8123";
const origin = `http://127.0.0.1:${port}`;
const projects = ["Atlas", "Control Loop", "Data Plane", "Relay", "Studio", "Workbench", "Indexer", "Monitor"];
const env = { ...process.env, IDLE_OPS_REPOSITORIES: JSON.stringify([
  ...projects.map((name, index) => ({
    id: index + 1, name, repo: `fixture/project-${index + 1}`, refs: ["main"], publish: true,
  })),
  { id: 9, name: "QA Private", repo: "fixture/private", refs: ["private-ref"], publish: false },
]) };
delete env.IDLE_OPS_CONFIG;
env.IDLE_OPS_TELEMETRY_TOKENS = JSON.stringify(Object.fromEntries(["qa-coder", "qa-checks"].map((agent) =>
  [agent, { sha256: createHash("sha256").update(randomBytes(32)).digest("hex"), repository_ids: [1] }])));
delete env.IDLE_OPS_GITHUB_WEBHOOK_SECRET;

const fixtures = `
import os, sys, time
from datetime import datetime, timezone
from server import telemetry
from server.store import Store
from server.twin import Twin, emit, registry, signal

db, step = sys.argv[1:]
twin = Twin(Store(db), registry(os.environ["IDLE_OPS_REPOSITORIES"]))
now = time.time()
if step == "initial":
    with twin.transaction(write=True) as con:
        for repo, xp in enumerate((1200, 650, 2200, 180, 4100, 710, 130, 370, 100), 1):
            con.execute("UPDATE twin_projects SET public_verified=?, observed_at=?, xp=?, commit_count=? WHERE repo=?",
                        (int(repo < 9), now - (730 if repo == 8 else 0), xp, xp // 10, repo))
        for repo, key, kind in ((1, "issue:123", "bug"), (2, "ci:qa", "ci"), (9, "issue:456", "bug")):
            signal(con, repo, (key, kind, "open", now, 0), now, True)
        for key, kind, status in (("pull:77", "pull", "closed"), ("release:11", "release", "published")):
            signal(con, 1, (key, kind, status, now, 0), now, False)
        for kind in ("pull", "release", "commit"):
            emit(con, 1, now, kind)
    for agent, status, age, association in (("qa-coder", "coding", 0, 123), ("qa-checks", "testing", 110, None)):
        payload = {"version": 1, "agent_id": agent, "event_id": "seed-" + agent, "repository_id": 1,
                   "run_id": "fixture-run", "task_id": "fixture-check", "sequence": 1,
                   "timestamp": datetime.fromtimestamp(now - age, timezone.utc).isoformat().replace("+00:00", "Z"),
                   "event_type": status}
        if association:
            payload["issue_id"] = association
        twin.ingest_agent(payload, telemetry.Scope(agent, frozenset({1})), now - age)
else:
    with twin.transaction(write=True) as con:
        signal(con, 1, ("issue:123", "bug", "resolved", now, 0), now, True)
        con.execute("UPDATE twin_projects SET xp=2300, observed_at=? WHERE repo=1", (now,))
        emit(con, 1, now, "install", level=24, tier="gpu")
        emit(con, 1, now, "levelup", level=24, tier="gpu")
`;

function seed(step) {
  execFileSync("python3", ["-c", fixtures, db, step], { cwd: root, env });
}

async function snapshot(query = "") {
  const response = await fetch(`${origin}/idle-ops/api/v1/state${query}`);
  assert.equal(response.status, 200);
  return response.json();
}

async function openPage(browser, viewport, dpr, reducedMotion, problems) {
  const page = await browser.newPage({ viewport, deviceScaleFactor: dpr, reducedMotion });
  page.on("pageerror", (error) => problems.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") problems.push(message.text()); });
  page.on("response", (response) => { if (response.status() >= 400) problems.push(`${response.status()} ${response.url()}`); });
  // Capture a scene reference for inspection without shipping a test global
  // or replacing any application state, event, or image responses.
  await page.route("**/assets/engine.js", async (route) => {
    const response = await route.fetch();
    const body = (await response.text()) + `
      import { WorldScene as Scene } from "./engine/scene.js";
      const drawScene = Scene.prototype.render;
      Scene.prototype.render = function () {
        window.qaScene = this;
        window.qaMotionStates ??= new Set();
        window.qaMotionStates.add(this.hero.artState);
        if (this.bugs.some(bug => bug.state === "hit")) window.qaHit = true;
        drawScene.call(this);
      };
    `;
    await route.fulfill({ response, body });
  });
  await page.goto(`${origin}/idle-ops/`);
  await page.waitForFunction(() => window.qaScene?.loaded && window.qaScene.racks.length > 0);
  return page;
}

async function noOverflow(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
}

let child;
let browser;
try {
  await mkdir(output, { recursive: true });
  seed("initial");
  child = spawn("python3", ["-m", "server", "--dev-user", "qa-admin", "--static-root", "static",
    "--public-root", "static/public", "--port", port, "--db", db], { cwd: root, env, stdio: "inherit" });
  let initial;
  for (let attempt = 0; attempt < 75 && !initial; attempt += 1) {
    if (child.exitCode !== null) throw new Error("Idle Ops did not start");
    initial = await snapshot().catch(() => null);
    if (!initial) await sleep(100);
  }
  assert.equal(initial?.projects.length, 8);
  assert.equal(JSON.stringify(initial).includes("QA Private"), false);
  assert.equal(JSON.stringify(initial).includes("fixture-check"), false);
  assert.equal((await snapshot("?demo=1")).demo, true);
  assert.deepEqual((await snapshot()).projects.map((project) => project.xp), initial.projects.map((project) => project.xp));
  assert.equal((await fetch(`${origin}/idle-ops/api/v1/state`, { method: "POST" })).status, 405);

  browser = await chromium.launch({ executablePath: process.env.CHROME_BIN });
  const problems = [];
  const desktop = await openPage(browser, { width: 1440, height: 900 }, 1, "no-preference", problems);
  await noOverflow(desktop);
  assert.equal(await desktop.locator("#project-filter option").count(), 9);
  assert.equal(await desktop.locator("#agent-list li").count(), 2);
  assert.equal(await desktop.locator('.agent-status[data-status="stale"]').count(), 1);
  await desktop.waitForFunction(() => window.qaMotionStates.has("repair"));
  assert.match(await desktop.locator("#room-counts").innerText(), /프로젝트 8.*미해결 버그 2/);
  await desktop.screenshot({ path: join(output, "public-desktop.jpg"), fullPage: true, quality: 88 });
  await desktop.locator("#page-next").click();
  assert.equal(await desktop.locator("#rack-page").innerText(), "2 / 2");
  await desktop.locator("#page-prev").click();
  await desktop.locator("#project-filter").selectOption({ label: "Atlas" });
  assert.deepEqual(await desktop.evaluate(() => window.qaScene.racks.map((rack) => rack.label)), ["Atlas"]);
  await desktop.locator("#watch").click();
  await desktop.waitForFunction(() => document.getElementById("watch").getAttribute("aria-pressed") === "true");
  await desktop.locator("#watch").click();

  seed("resolve");
  await desktop.waitForFunction(() => window.qaHit === true);
  await desktop.waitForFunction(() => ["idle", "run", "repair", "attack", "install", "levelup"]
    .every((state) => window.qaMotionStates.has(state) && window.qaScene.library.stateReady.has(state)));
  const playback = await desktop.evaluate(() => ({
    art: [...window.qaMotionStates].sort().map((state) => [state, window.qaScene.library.artFor(state).kind]),
    fps: window.qaScene.stats.fps,
    problems: window.qaScene.library.problems,
  }));
  assert.equal(playback.art.length, 6);
  assert.ok(playback.art.every(([, kind]) => kind === "atlas"));
  assert.deepEqual(playback.problems, []);
  assert.match(await desktop.locator("#feed-list").innerText(), /버그 해결/);

  const mobile = await openPage(browser, { width: 390, height: 844 }, 3, "reduce", problems);
  await noOverflow(mobile);
  assert.equal(await mobile.evaluate(() => window.qaScene.dpr), 2);
  assert.equal(await mobile.evaluate(() => window.qaScene.motion), false);
  assert.equal(await mobile.evaluate(() => window.qaScene.raf), 0);
  await mobile.locator("#drawer-open").click();
  assert.equal(await mobile.locator("#project-drawer").evaluate((item) => item.open), true);
  await mobile.locator("#project-filter").selectOption({ label: "Control Loop" });
  await mobile.locator("#drawer-close").click();
  assert.deepEqual(await mobile.evaluate(() => window.qaScene.racks.map((rack) => rack.label)), ["Control Loop"]);
  await mobile.screenshot({ path: join(output, "public-mobile.jpg"), fullPage: true, quality: 88 });
  await mobile.setViewportSize({ width: 320, height: 700 });
  await noOverflow(mobile);

  await desktop.goto(`${origin}/idle-ops/?demo=1`);
  await desktop.waitForFunction(() => window.qaScene?.racks.some((rack) => rack.label === "Demo Lab"));
  assert.equal(await desktop.locator("#demo-badge").isVisible(), true);
  assert.equal((await snapshot()).projects.length, 8);
  assert.deepEqual(problems, []);
  console.log(JSON.stringify({ desktop: "1440×900", mobile: ["390×844", "320×700"], ...playback, problems }));
} finally {
  if (browser) await browser.close();
  if (child && child.exitCode === null) {
    child.kill("SIGTERM");
    await once(child, "exit");
  }
  await rm(scratch, { recursive: true, force: true });
}
