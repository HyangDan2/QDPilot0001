/* Serialized front-end operation coordinator for the Idle Ops admin console.
 *
 * Every mutating request goes through one coordinator, which:
 *   - mints at most one opId at a time and persists it with its exact body;
 *   - replays an unresolved operation with the same opId instead of minting a
 *     new one, so a lost response is never applied twice;
 *   - refuses a different action until the earlier operation is resolved;
 *   - blocks rapid clicks and concurrent startup recovery;
 *   - keeps a 401 pending for replay after re-login, but drops operations the
 *     server explicitly rejected (4xx) or that outlived maxAgeMs;
 *   - tolerates blocked sessionStorage by falling back to memory.
 *
 * It has no DOM dependency so Node tests can exercise it directly.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.HD2OperationCoordinator = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const DEFAULT_KEY = "hd2lab-idle-op";
  const DEFAULT_MAX_AGE_MS = 24 * 60 * 60 * 1000;

  function fallbackId() {
    if (typeof crypto !== "undefined" && crypto && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
    return `op_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 12)}`;
  }

  function createOperationCoordinator(options = {}) {
    const fetchImpl = options.fetch || (typeof fetch === "function" ? fetch : null);
    const storage = options.storage || null;
    const key = options.key || DEFAULT_KEY;
    const makeId = options.makeId || fallbackId;
    const now = options.now || Date.now;
    const isSuccess = options.isSuccess || ((data) => Boolean(data?.state));
    const maxAgeMs = Number.isFinite(options.maxAgeMs) ? options.maxAgeMs : DEFAULT_MAX_AGE_MS;

    let memory = null;
    let busy = false;

    function normalize(value) {
      if (!value || typeof value !== "object") return null;
      if (typeof value.opId !== "string" || !value.opId) return null;
      if (typeof value.path !== "string" || !value.path) return null;
      if (typeof value.body !== "string" || !value.body) return null;
      if (typeof value.signature !== "string") return null;
      return {
        opId: value.opId,
        path: value.path,
        body: value.body,
        signature: value.signature,
        createdAt: Number.isFinite(value.createdAt) ? value.createdAt : 0,
      };
    }

    function read() {
      let raw = memory;
      if (storage) {
        try {
          const stored = storage.getItem(key);
          if (stored !== null && stored !== undefined) raw = stored;
        } catch (error) { /* storage may be blocked */ }
      }
      if (!raw) return null;
      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch (error) {
        clear();
        return null;
      }
      const pending = normalize(parsed);
      if (!pending) {
        clear();
        return null;
      }
      if (pending.createdAt && now() - pending.createdAt > maxAgeMs) {
        clear();
        return null;
      }
      return pending;
    }

    function write(value) {
      memory = value ? JSON.stringify(value) : null;
      if (!storage) return;
      try {
        if (memory === null) storage.removeItem(key);
        else storage.setItem(key, memory);
      } catch (error) { /* storage may be blocked; memory still works */ }
    }

    function clear() {
      write(null);
    }

    async function send(pending) {
      if (!fetchImpl) return { status: "retry", message: "network unavailable" };
      let response;
      try {
        response = await fetchImpl(pending.path, {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: pending.body,
        });
      } catch (error) {
        return { status: "retry", message: "network" };
      }
      if (response.status === 401) {
        // Keep the operation: after re-login the same opId is replayed once.
        return { status: "auth", message: "admin authentication required" };
      }
      if (response.status >= 400 && response.status < 500) {
        let detail = "";
        try {
          const payload = await response.json();
          if (payload && payload.detail) detail = String(payload.detail);
        } catch (error) { /* no JSON body */ }
        clear();
        return { status: "rejected", code: response.status, message: detail };
      }
      if (!response.ok) {
        // 5xx: the server may or may not have applied it; keep the opId.
        return { status: "retry", message: `HTTP ${response.status}` };
      }
      let data;
      try {
        data = await response.json();
      } catch (error) {
        // Ambiguous success: replay the same opId to learn the recorded result.
        return { status: "retry", message: "invalid response" };
      }
      if (!data || typeof data !== "object" || !isSuccess(data)) {
        return { status: "retry", message: "invalid response" };
      }
      clear();
      return { status: "ok", data };
    }

    async function recover() {
      if (busy) return { status: "busy" };
      const pending = read();
      if (!pending) return { status: "none" };
      busy = true;
      try {
        return await send(pending);
      } finally {
        busy = false;
      }
    }

    async function submit(input) {
      if (busy) return { status: "busy" };
      if (!input || typeof input.path !== "string" || typeof input.signature !== "string") {
        return { status: "invalid" };
      }
      busy = true;
      try {
        let pending = read();
        if (pending && pending.signature !== input.signature) {
          // Never mint a new opId while an earlier one is unresolved.
          const settled = await send(pending);
          if (settled.status === "auth") return settled;
          if (settled.status === "retry") {
            return {
              status: "blocked",
              message: "the previous operation is still unresolved",
              cause: settled,
            };
          }
          pending = read();
        }
        if (!pending || pending.signature !== input.signature) {
          const opId = makeId();
          const body = JSON.stringify(Object.assign({}, input.fields || {}, { opId }));
          pending = {
            opId,
            path: input.path,
            body,
            signature: input.signature,
            createdAt: now(),
          };
          write(pending);
        }
        return await send(pending);
      } finally {
        busy = false;
      }
    }

    return {
      submit,
      recover,
      isBusy: () => busy,
      hasPending: () => Boolean(read()),
      pending: () => read(),
    };
  }

  return { createOperationCoordinator };
});
