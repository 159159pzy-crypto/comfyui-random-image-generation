import { api } from "./api.js";

const V7_ROOT = "/api/v7";
const WORKSPACES = new Set(["random", "natural"]);
export const V7_EVENT_TYPES = Object.freeze([
  "job.created", "job.queued", "job.dispatching", "job.started", "job.updated",
  "job.succeeded", "job.partial", "job.failed", "job.timed_out", "job.cancelled",
  "job.interrupted", "job.retried",
  "preset.created", "preset.updated", "preset.deleted",
  "history.created", "history.deleted", "draft.updated", "asset.changed",
  "asset.created", "intent.created",
]);

function unwrapDraft(value, workspace) {
  const draft = value?.draft || value || {};
  const intent = draft.intent ?? draft.payload ?? draft.settings ?? value?.intent ?? value?.payload ?? value?.settings ?? {};
  const workspaceState = draft.workspace_state ?? draft.ui_state ?? value?.workspace_state ?? value?.ui_state ?? {};
  return {
    workspace,
    revision: Number(draft.revision ?? value?.revision ?? 0),
    digest: String(draft.digest ?? value?.digest ?? ""),
    payload: { ...(workspaceState || {}), ...(intent || {}) },
  };
}

function unwrapItems(value) {
  if (Array.isArray(value)) return value;
  return value?.items || value?.models || value?.loras || [];
}

export class V7State extends EventTarget {
  constructor() {
    super();
    this.available = null;
    this.bootstrap = null;
    this.drafts = new Map();
    this.presets = [];
    this.models = [];
    this.loras = [];
    this.events = null;
    this.initPromise = null;
    this.saveTimers = new Map();
  }

  async init() {
    if (this.initPromise) return this.initPromise;
    this.initPromise = this.#initialize();
    return this.initPromise;
  }

  async #initialize() {
    try {
      const early = window.__animaV7Bootstrap ? await window.__animaV7Bootstrap : null;
      if (early && early.available === false) {
        throw new Error("V7 API 不可用");
      }
      const snapshot = early?.snapshot || await api(`${V7_ROOT}/bootstrap`);
      this.available = true;
      this.bootstrap = snapshot || {};
      for (const workspace of WORKSPACES) {
        const value = snapshot?.drafts?.[workspace];
        if (value) this.drafts.set(workspace, unwrapDraft(value, workspace));
      }
      this.presets = unwrapItems(snapshot?.presets);
      this.models = unwrapItems(snapshot?.assets?.models);
      this.loras = unwrapItems(snapshot?.assets?.loras);
      this.#connectEvents();
      this.dispatchEvent(new CustomEvent("ready", { detail: snapshot }));
      return snapshot;
    } catch (error) {
      this.available = false;
      throw error;
    }
  }

  async request(path, options = {}) {
    await this.init();
    return api(`${V7_ROOT}${path}`, options);
  }

  async loadDraft(workspace, { refresh = false } = {}) {
    if (!WORKSPACES.has(workspace)) throw new Error(`未知工作台：${workspace}`);
    await this.init();
    if (!refresh && this.drafts.has(workspace)) return this.drafts.get(workspace);
    const record = unwrapDraft(await api(`${V7_ROOT}/drafts/${workspace}`), workspace);
    this.drafts.set(workspace, record);
    return record;
  }

  async saveDraft(workspace, payload) {
    if (!WORKSPACES.has(workspace)) throw new Error(`未知工作台：${workspace}`);
    await this.init();
    window.clearTimeout(this.saveTimers.get(workspace));
    this.saveTimers.delete(workspace);
    const current = this.drafts.get(workspace);
    try {
      const result = await api(`${V7_ROOT}/drafts/${workspace}`, {
        method: "PUT",
        body: JSON.stringify({
          revision: current?.revision ?? 0,
          intent: payload,
          workspace_state: payload,
        }),
      });
      const saved = unwrapDraft(result, workspace);
      this.drafts.set(workspace, saved);
      this.dispatchEvent(new CustomEvent("draft", { detail: saved }));
      return saved;
    } catch (error) {
      if (error.status !== 409) throw error;
      const latest = await this.loadDraft(workspace, { refresh: true });
      const conflict = new Error("草稿已在另一个标签页更新，请确认后重试");
      conflict.name = "DraftConflictError";
      conflict.latest = latest;
      throw conflict;
    }
  }

  scheduleDraft(workspace, payload, { delay = 420, onSaved, onError } = {}) {
    window.clearTimeout(this.saveTimers.get(workspace));
    this.saveTimers.set(workspace, window.setTimeout(async () => {
      this.saveTimers.delete(workspace);
      try {
        onSaved?.(await this.saveDraft(workspace, payload));
      } catch (error) {
        onError?.(error);
      }
    }, delay));
  }

  async loadPresets() {
    const payload = await this.request("/presets");
    this.presets = unwrapItems(payload);
    return this.presets;
  }

  async loadAssets({ refresh = false } = {}) {
    await this.init();
    if (!refresh && (this.models.length || this.loras.length)) return { models: this.models, loras: this.loras };
    const [models, loras] = await Promise.all([
      api(`${V7_ROOT}/assets/models`),
      api(`${V7_ROOT}/assets/loras`),
    ]);
    this.models = unwrapItems(models);
    this.loras = unwrapItems(loras);
    return { models: this.models, loras: this.loras };
  }

  #connectEvents() {
    if (!this.available || this.events || typeof EventSource === "undefined") return;
    const cursor = Number(this.bootstrap?.events?.cursor || 0);
    const url = this.bootstrap?.events?.url || `${V7_ROOT}/events`;
    this.events = new EventSource(`${url}?after=${encodeURIComponent(cursor)}`);
    const receive = async (message) => {
      try {
        const event = JSON.parse(message.data || "{}");
        if (!event.type) event.type = event.event || (message.type === "message" ? "studio.event" : message.type);
        await this.#handleEvent(event);
      } catch {
        // A malformed event must not break subsequent live updates.
      }
    };
    this.events.onmessage = receive;
    for (const type of V7_EVENT_TYPES)
      this.events.addEventListener(type, receive);
  }

  async #handleEvent(event) {
    if (event.type === "draft.updated") {
      const workspace = event.workspace || event.data?.workspace;
      if (WORKSPACES.has(workspace)) {
        const incoming = unwrapDraft(event.data || {}, workspace);
        const current = this.drafts.get(workspace);
        if (!current || incoming.revision >= current.revision) this.drafts.set(workspace, incoming);
      }
    }
    if (event.type === "asset.changed") await this.loadAssets({ refresh: true }).catch(() => {});
    if (event.type.startsWith("preset.")) await this.loadPresets().catch(() => {});
    this.dispatchEvent(new CustomEvent("event", { detail: event }));
    window.dispatchEvent(new CustomEvent("studio:v7-event", { detail: event }));
    if (event.type.startsWith("history.")) window.dispatchEvent(new CustomEvent("studio:history-changed", { detail: event.data }));
    if (event.type.startsWith("job.")) window.dispatchEvent(new CustomEvent("studio:jobs-changed", { detail: event.data }));
    if (event.type === "draft.updated") window.dispatchEvent(new CustomEvent("studio:draft-changed", { detail: event.data }));
    if (event.type === "asset.changed") window.dispatchEvent(new CustomEvent("studio:assets-changed", { detail: event.data }));
    if (event.type.startsWith("preset.")) window.dispatchEvent(new CustomEvent("studio:presets-changed", { detail: event.data }));
  }
}

export const v7State = new V7State();
