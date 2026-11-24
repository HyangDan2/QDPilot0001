import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { createOperationCoordinator } = require(path.join(here, "../../static/assets/op-coordinator.js"));

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
    raw: data,
  };
}

function response(status, body, { invalidJson = false } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      if (invalidJson) throw new SyntaxError("bad json");
      return body;
    },
  };
}

function okPayload() {
  return { state: { requests: 1 }, cursor: 0 };
}

function setup(handler) {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) });
    return handler(calls.length, calls[calls.length - 1]);
  };
  const storage = memoryStorage();
  let tick = 1_700_000_000_000;
  const coordinator = createOperationCoordinator({
    fetch: fetchImpl,
    storage,
    makeId: () => `op_fixture_${calls.length + 1}`,
    now: () => tick,
  });
  return { coordinator, calls, storage, advance: (ms) => { tick += ms; } };
}

const BUY = { path: "/api/actions", fields: { action: { type: "buyServer" } }, signature: "/api/actions|buy" };

test("mints one opId, posts it and clears pending on success", async () => {
  const ctx = setup(() => response(200, okPayload()));
  const result = await ctx.coordinator.submit(BUY);
  assert.equal(result.status, "ok");
  assert.equal(ctx.calls.length, 1);
  assert.match(ctx.calls[0].body.opId, /^op_fixture_/);
  assert.equal(ctx.coordinator.hasPending(), false);
});

test("a lost response keeps the opId and a retry reuses it", async () => {
  const ctx = setup((count) => (count === 1 ? Promise.reject(new Error("offline")) : response(200, okPayload())));
  const first = await ctx.coordinator.submit(BUY);
  assert.equal(first.status, "retry");
  assert.equal(ctx.coordinator.hasPending(), true);
  const opId = ctx.coordinator.pending().opId;
  const second = await ctx.coordinator.submit(BUY);
  assert.equal(second.status, "ok");
  assert.equal(ctx.calls.length, 2);
  assert.equal(ctx.calls[1].body.opId, opId);
});

test("a different action is blocked until the pending op resolves", async () => {
  const other = { path: "/api/actions", fields: { action: { type: "buyUpgrade" } }, signature: "/api/actions|upgrade" };
  const ctx = setup((count) => (count <= 2 ? Promise.reject(new Error("offline")) : response(200, okPayload())));
  await ctx.coordinator.submit(BUY);
  const blocked = await ctx.coordinator.submit(other);
  assert.equal(blocked.status, "blocked");
  assert.equal(ctx.calls.length, 2);
  assert.equal(ctx.calls[1].body.opId, ctx.calls[0].body.opId);
  // Retry of the original settles it; the new action then mints a new opId.
  assert.equal((await ctx.coordinator.submit(BUY)).status, "ok");
  const fresh = await ctx.coordinator.submit(other);
  assert.equal(fresh.status, "ok");
  assert.notEqual(ctx.calls[3].body.opId, ctx.calls[0].body.opId);
});

test("rapid clicks run one request at a time", async () => {
  let release;
  const ctx = setup(() => new Promise((resolve) => { release = () => resolve(response(200, okPayload())); }));
  const first = ctx.coordinator.submit(BUY);
  const second = await ctx.coordinator.submit(BUY);
  assert.equal(second.status, "busy");
  release();
  assert.equal((await first).status, "ok");
  assert.equal(ctx.calls.length, 1);
});

test("4xx rejects and clears, 401 keeps the op for re-login", async () => {
  const ctx = setup((count) => (count === 1
    ? response(400, { detail: "not enough requests" })
    : response(401, { detail: "admin authentication required" })));
  const rejected = await ctx.coordinator.submit(BUY);
  assert.equal(rejected.status, "rejected");
  assert.equal(rejected.code, 400);
  assert.equal(ctx.coordinator.hasPending(), false);

  const auth = await ctx.coordinator.submit(BUY);
  assert.equal(auth.status, "auth");
  assert.equal(ctx.coordinator.hasPending(), true);
  assert.equal((await ctx.coordinator.recover()).status, "auth");
  assert.equal(ctx.coordinator.hasPending(), true);
});

test("an invalid 200 body is retried with the same opId", async () => {
  const ctx = setup((count) => (count === 1
    ? response(200, null, { invalidJson: true })
    : response(200, okPayload())));
  const first = await ctx.coordinator.submit(BUY);
  assert.equal(first.status, "retry");
  const opId = ctx.coordinator.pending().opId;
  const second = await ctx.coordinator.submit(BUY);
  assert.equal(second.status, "ok");
  assert.equal(ctx.calls[1].body.opId, opId);
});

test("blocked storage still dedupes in memory", async () => {
  const broken = {
    getItem() { throw new Error("denied"); },
    setItem() { throw new Error("denied"); },
    removeItem() { throw new Error("denied"); },
  };
  const ctx = setup((count) => (count === 1 ? Promise.reject(new Error("offline")) : response(200, okPayload())));
  const coordinator = createOperationCoordinator({
    fetch: async (url, options) => {
      ctx.calls.push({ url, body: JSON.parse(options.body) });
      return ctx.calls.length === 1 ? Promise.reject(new Error("offline")) : response(200, okPayload());
    },
    storage: broken,
    makeId: () => "op_memory_1",
  });
  assert.equal((await coordinator.submit(BUY)).status, "retry");
  assert.equal(coordinator.hasPending(), true);
  assert.equal((await coordinator.submit(BUY)).status, "ok");
  assert.equal(ctx.calls[0].body.opId, ctx.calls[1].body.opId);
});

test("stale pending entries expire instead of replaying", async () => {
  const ctx = setup(() => response(200, okPayload()));
  await ctx.coordinator.submit(BUY);
  // simulate an entry created 2 days before "now"
  const stored = JSON.parse(ctx.storage.getItem("hd2lab-idle-op") || "null") || {
    opId: "op_old",
    path: "/api/actions",
    body: JSON.stringify({ opId: "op_old" }),
    signature: BUY.signature,
    createdAt: 1000,
  };
  ctx.storage.setItem("hd2lab-idle-op", JSON.stringify({ ...stored, createdAt: 1000 }));
  const coordinator = createOperationCoordinator({
    fetch: async () => response(200, okPayload()),
    storage: ctx.storage,
    makeId: () => "op_new",
    maxAgeMs: 1000,
    now: () => 5000,
  });
  assert.equal(coordinator.hasPending(), false);
  const result = await coordinator.submit(BUY);
  assert.equal(result.status, "ok");
});

test("recovery without pending is a no-op", async () => {
  const ctx = setup(() => response(200, okPayload()));
  assert.equal((await ctx.coordinator.recover()).status, "none");
  assert.equal(ctx.calls.length, 0);
  assert.equal(ctx.coordinator.isBusy(), false);
});
