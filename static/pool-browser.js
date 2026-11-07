/* Shared selection state for the existing pool browser. No generation draft writes. */
(() => {
  "use strict";
  let active = null, serial = 0;
  const copy = value => JSON.parse(JSON.stringify(value));
  function createSession({ initialSelectedIds = [], alreadyBoundIds = [], selectionMode = "multiple", view = {} } = {}) {
    const alreadyBound = new Set(alreadyBoundIds);
    const single = selectionMode === "single";
    const initial = [...new Set(initialSelectedIds)].filter(id => !alreadyBound.has(id));
    const session = {
      id: ++serial, profile: "anima", section: "character", closed: false,
      selectionMode: single ? "single" : "multiple",
      view: copy(view), selection: { mode: "include", ids: single ? initial.slice(0, 1) : initial, excluded_ids: [] },
      alreadyBound, known: new Map(), selectedTotal: null, poolTotal: 0,
      collapsedFavorites: new Set(), collapsedCustom: new Set(), customInitialized: false,
      remember(items) { for (const item of items) if (item?.id) this.known.set(item.id, copy(item)); },
      toggle(id, checked) {
        if (this.closed || this.alreadyBound.has(id)) return;
        if (single && checked) { this.selection.ids = [id]; this.selectedTotal = null; return; }
        const ids = new Set(this.selection.ids); if (checked) ids.add(id); else ids.delete(id);
        if (ids.size > 4096) throw new Error("一次最多选择 4096 位人物，请缩小筛选范围");
        this.selection.ids = [...ids]; this.selectedTotal = null;
      },
      append(ids) {
        if (this.closed) return;
        const combined = [...new Set([...this.selection.ids, ...ids])].filter(id => !this.alreadyBound.has(id));
        if (single) { this.selection.ids = combined.slice(0, 1); this.selectedTotal = null; return; }
        if (combined.length > 4096) throw new Error("一次最多选择 4096 位人物，请缩小筛选范围");
        this.selection.ids = combined; this.selectedTotal = null;
      },
      clear() { if (!this.closed) { this.selection.ids = []; this.selectedTotal = null; } },
      result() { return this.selection.ids.map(id => copy(this.known.get(id) || { id, title: id })); },
    };
    return session;
  }
  const api = {
    createSession,
    get active() { return active; },
    activate(session) { if (active && active !== session) throw new Error("人物选择器已打开"); active = session; },
    release(session) { session.closed = true; if (active === session) active = null; },
  };
  globalThis.PoolBrowser = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
