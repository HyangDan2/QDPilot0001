import assert from "node:assert/strict";
import test from "node:test";

import { WorldScene } from "../../static/public/assets/engine/scene.js";
import { HeroController } from "../../static/public/assets/engine/motion.js";
import { projectTier } from "../../static/public/assets/engine/room.js";

globalThis.document = { hidden: false };

const bug = { id: "bug-one", kind: "issue" };

function project(id = "project-one", bugs = []) {
  return { id, name: id, level: 1, bugs };
}

function event(id, type, entityId = bug.id) {
  return { id, type, entityId, projectId: "project-one" };
}

function setup() {
  const scene = Object.assign(Object.create(WorldScene.prototype), {
    width: 390, height: 440, floorY: 270,
    motion: true, eventCursor: 0, projectId: null, page: 0, time: 0, flash: 0, shake: 0,
    bugs: [], racks: [], agents: [], projects: [], stats: {},
    encounters: new Map(), celebrations: new Map(), activeBugs: new Map(),
    effects: {
      clear() {}, update() {}, particle() {}, slash() {},
      burst() {}, floatText() {}, ring() {}, particles: [],
    },
    render() {},
  });
  scene.hero = new HeroController(scene);
  scene.hero.reset();
  return scene;
}

function tick(scene, seconds = 4) {
  for (let frame = 0; frame < seconds * 60; frame += 1) scene.update(1 / 60);
}

test("open issue remains untouched and a fresh verified resolution alone triggers defeat", () => {
  const scene = setup();
  scene.setSnapshot({ projects: [project(undefined, [bug])], events: [event(1, "bug_opened")], cursor: 1 });
  const visible = scene.bugs[0];
  tick(scene, 16);
  assert.equal(scene.bugs.length, 1);
  assert.equal(scene.bugs[0], visible);
  assert.equal(visible.state, "alive");
  assert.equal(scene.hero.artState, "idle");

  scene.setSnapshot({ projects: [project()], events: [event(2, "bug_resolved")], cursor: 2 }, { animate: true });
  assert.equal(scene.bugs[0], visible);
  tick(scene);
  assert.equal(visible.state, "hit");
  assert.equal(scene.bugs.length, 0);
  scene.setSnapshot({ projects: [project()], events: [event(2, "bug_resolved")], cursor: 2 }, { animate: true });
  assert.equal(scene.bugs.length, 0);
});

test("reopen cancels in-flight defeat while dismissal removes the entity without a strike", () => {
  const scene = setup();
  scene.setSnapshot({ projects: [project(undefined, [bug])], cursor: 1 });
  scene.setSnapshot({ projects: [project()], events: [event(2, "bug_resolved")], cursor: 2 }, { animate: true });
  scene.setSnapshot({ projects: [project(undefined, [bug])], events: [event(3, "bug_opened")], cursor: 3 }, { animate: true });
  tick(scene);
  assert.equal(scene.bugs[0].state, "alive");
  assert.equal(scene.encounters.size, 0);
  const visible = scene.bugs[0];
  scene.setSnapshot({ projects: [project()], events: [event(4, "bug_dismissed")], cursor: 4 }, { animate: true });
  tick(scene);
  assert.equal(visible.state, "alive");
  assert.equal(scene.bugs.length, 0);
});

test("snapshots and event gaps skip historical combat and keep rack pages bounded", () => {
  const scene = setup();
  const projects = Array.from({ length: 8 }, (_, index) => project(`project-${index}`));
  scene.setSnapshot({ projects, events: [event(8, "bug_resolved")], cursor: 8 });
  assert.equal(scene.bugs.length, 0);
  assert.equal(scene.racks.length, 3);
  assert.equal(scene.pageCount, 3);
  scene.setPage(2);
  assert.deepEqual(scene.racks.map((rack) => rack.projectId), ["project-6", "project-7"]);
  scene.setSnapshot({ projects, events: [event(9, "bug_resolved")], cursor: 9, eventGap: true },
    { projectId: "project-7", animate: true });
  assert.equal(scene.page, 0);
  assert.deepEqual(scene.racks.map((rack) => rack.projectId), ["project-7"]);
  assert.equal(scene.bugs.length, 0);
});

test("repair requires a fresh association with the open bug in the same project", () => {
  const scene = setup();
  const projects = [project(undefined, [bug])];
  const agent = { id: "agent", projectId: "project-one", status: "testing" };
  for (const agents of [[agent], [{ ...agent, bugId: bug.id, stale: true }]]) {
    scene.setSnapshot({ projects, agents });
    tick(scene);
    assert.equal(scene.hero.artState, "idle");
  }
  scene.setSnapshot({ projects, agents: [{ ...agent, bugId: bug.id }] });
  let repaired = false;
  for (let frame = 0; frame < 180; frame += 1) {
    scene.update(1 / 60);
    if (scene.hero.artState === "repair") repaired = true;
  }
  assert.equal(repaired, true);
  assert.equal(scene.bugs[0].state, "alive");
});

test("racks use the server tier and the visible encounter queue stays bounded", () => {
  assert.equal(projectTier({ level: 1, tier: "gpu" }), "gpu");
  assert.equal(projectTier({ level: 40, tier: "unknown" }), "cache");
  const scene = setup();
  scene.setSnapshot({ projects: [project()], cursor: 1 });
  const events = Array.from({ length: 20 }, (_, index) => event(index + 2, "bug_resolved", `bug-${index}`));
  scene.setSnapshot({ projects: [project()], events, cursor: 21 }, { animate: true });
  assert.equal(scene.bugs.length, 3);
  assert.equal(scene.encounters.size, 6);
});
