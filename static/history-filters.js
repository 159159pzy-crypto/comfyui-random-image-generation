/* Browsing state only. These controls never borrow a generation-pool selection. */
const HistoryFilterModel = (() => {
  const pools = ["character", "clothing", "pose", "background", "expression"];
  const attributes = ["gender", "hair", "eye", "series"];
  const kinds = ["item", "favorite_group", "custom_group", "category", "trait", ...attributes];
  const key = token => JSON.stringify([token.pool, token.kind, token.value]);
  function token(value) {
    if (!value || !pools.includes(value.pool) || !kinds.includes(value.kind)
      || typeof value.value !== "string" || !value.value.trim() || value.value.length > 1000
      || /[\u0000-\u001f\u007f-\u009f\ud800-\udfff]/u.test(value.value)
      || (attributes.includes(value.kind) && value.pool !== "character")) return null;
    return { pool: value.pool, kind: value.kind, value: value.value };
  }
  function normalize(value) {
    const result = { include: [], exclude: [] }, seen = new Set();
    // Exclusions take precedence if corrupted/old browser storage contains both.
    for (const mode of ["exclude", "include"]) for (const candidate of (Array.isArray(value?.[mode]) ? value[mode] : []).slice(0, 128)) {
      const entry = token(candidate); if (!entry || seen.has(key(entry))) continue;
      seen.add(key(entry)); result[mode].push(entry);
    }
    return result;
  }
  function toggle(filters, candidate, mode) {
    const next = normalize(filters), entry = token(candidate);
    if (!entry || !["include", "exclude"].includes(mode)) return next;
    const id = key(entry), existed = next[mode].some(item => key(item) === id);
    for (const side of ["include", "exclude"]) next[side] = next[side].filter(item => key(item) !== id);
    if (!existed) {
      if (next[mode].length >= 128) throw new RangeError(`最多同时${mode === "include" ? "包含" : "排除"} 128 个条件，请先移除部分条件。`);
      next[mode].push(entry);
    }
    if (JSON.stringify(next).length > 60000) throw new RangeError("筛选条件过长，请先移除部分条件。");
    return next;
  }
  function canonical(filters) {
    const result = normalize(filters);
    for (const mode of ["include", "exclude"]) result[mode].sort((a, b) => key(a).localeCompare(key(b), "en"));
    return result;
  }
  function active(query = {}) {
    const filters = normalize(query.contentFilters);
    return Boolean(String(query.q || "").trim() || filters.include.length || filters.exclude.length);
  }
  function dynamic(query = {}) {
    const filters = normalize(query.contentFilters);
    return [...filters.include, ...filters.exclude].some(item => ["favorite_group", "custom_group", "category", "trait"].includes(item.kind));
  }
  function params(query = {}) {
    const result = new URLSearchParams(), filters = canonical(query.contentFilters), q = String(query.q || "").trim();
    if (q) result.set("q", q);
    if (filters.include.length || filters.exclude.length) result.set("content_filters", JSON.stringify(filters));
    if (dynamic(query) && query.groupRevision) result.set("group_revision", query.groupRevision);
    return result.size ? `&${result}` : "";
  }
  function requestLength(query = {}) {
    // URLSearchParams output is ASCII: its length is the encoded byte length.
    // Reserve the eventual SHA-256 revision before the first group response.
    return params({ ...query, groupRevision: query.groupRevision || (dynamic(query) ? "0".repeat(64) : "") }).length;
  }
  function ensureRequestBudget(query = {}) {
    // Leave >1 KB for /api/history, page/cursor, workbench and folder parameters
    // below aiohttp's default 8190-byte request-line limit.
    if (requestLength(query) > 7000) throw new RangeError("搜索与筛选条件过长，请缩短搜索词、减少条件，或改用整组筛选。");
  }
  function restore(raw) {
    try {
      const saved = JSON.parse(raw);
      if (!saved || saved.version !== 1 || ["include", "exclude"].some(mode => (saved.contentFilters?.[mode]?.length || 0) > 128)
        || JSON.stringify(saved.contentFilters || {}).length > 60000) return null;
      return { source: ["", "random"].includes(saved.source) ? saved.source : "",
        q: String(saved.q || "").slice(0, 500), contentFilters: normalize(saved.contentFilters),
        labels: saved.labels && typeof saved.labels === "object" && !Array.isArray(saved.labels) ? saved.labels : {} };
    } catch { return null; }
  }
  return { pools, attributes, key, token, normalize, toggle, canonical, active, dynamic, params, requestLength, ensureRequestBudget, restore };
})();
if (typeof module !== "undefined" && module.exports) module.exports = HistoryFilterModel;

const HISTORY_FILTER_STORAGE = "anima-random-webui:history-filters:v1";
const historyFiltersState = {
  ready: false, open: false, mobile: false, labels: new Map(), knownGroups: new Set(),
  catalog: null, catalogPromise: null, catalogSerial: 0, catalogError: "", catalogAgain: false, refreshTimer: null,
  poolNodes: new Map(), groupNodes: new Map(), panels: new Map(), chips: new Map(),
  index: new Map(), roots: [], expanded: new Set(), lists: new Map(), panelNodes: new Map(), heights: new Map(),
  dirty: new Set(), paintFrame: null, resizeObserver: null, scrollTop: 0, initializedExpansion: false, mountedRevision: null,
  searchTimer: null, composing: false, resultLoading: false, resultError: "", total: 0,
  recoveryIntent: null, recoveryUsed: false, recoveryGeneration: 0, recoveryPending: null,
  inertSiblings: new Map(), bodyOverflow: null, lifecycle: 0, media: null, modalClosing: false, outsidePointer: null,
};
const historyFiltersElement = id => document.getElementById(id);
const historyFiltersPoolLabel = pool => ({ character: "人物", clothing: "服装", pose: "姿势", background: "背景", expression: "表情" })[pool] || pool;
function historyFiltersQuery() { return historyDetailState.homeQuery; }
function historyFiltersActive(view = null) { return HistoryFilterModel.active(view?.query || historyFiltersQuery()); }
function historyFiltersQueryParams(view = null) { return HistoryFilterModel.params(view?.query || historyFiltersQuery()); }
function historyFiltersPersist() {
  if (!historyFiltersState.ready) return;
  const query = historyFiltersQuery(), filters = HistoryFilterModel.normalize(query.contentFilters);
  const keys = new Set([...filters.include, ...filters.exclude].map(HistoryFilterModel.key));
  const labels = Object.fromEntries([...historyFiltersState.labels].filter(([key]) => keys.has(key)));
  try { localStorage.setItem(HISTORY_FILTER_STORAGE, JSON.stringify({ version: 1, source: query.source || "", q: query.q || "", contentFilters: filters, labels })); }
  catch { /* Browsing remains usable when local storage is unavailable. */ }
}
function historyFiltersNode(tag, className = "", text = "") {
  const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
}
function historyFiltersButton(text, className, action) {
  const button = historyFiltersNode("button", className, text); button.type = "button";
  if (action) button.addEventListener("click", action); return button;
}
function historyFiltersReconcile(parent, nodes) {
  const desired = new Set(nodes);
  // Remove obsolete spacers/rows first. Inserting against their old indices
  // would unnecessarily move every retained row, blurring focused descendants
  // in browsers whose insertBefore does not preserve DOM interaction state.
  for (const child of [...parent.children]) if (!desired.has(child)) child.remove();
  nodes.forEach((node, index) => {
    if (parent.children[index] === node) return;
    const before = parent.children[index] || null, moving = node.parentElement === parent;
    if (moving && typeof parent.moveBefore === "function") parent.moveBefore(node, before);
    else {
      const focused = moving && node.contains(document.activeElement) ? document.activeElement : null;
      parent.insertBefore(node, before);
      if (focused?.isConnected && document.activeElement !== focused) historyFiltersFocus(focused);
    }
  });
}
function historyFiltersFocus(node) { if (node?.isConnected && !node.disabled && !node.closest("[hidden], [inert]")) node.focus({ preventScroll: true }); }
function historyFiltersMode(token) {
  const filters = HistoryFilterModel.normalize(historyFiltersQuery().contentFilters), key = HistoryFilterModel.key(token);
  return ["include", "exclude"].find(mode => filters[mode].some(item => HistoryFilterModel.key(item) === key)) || "";
}
function historyFiltersCanPaint() {
  return historyFiltersState.ready && historyFiltersState.open && !document.querySelector?.("dialog[open]");
}
function historyFiltersSchedulePaint(kind = "windows") {
  const state = historyFiltersState; state.dirty.add(kind);
  if (!historyFiltersCanPaint() || state.paintFrame !== null || typeof requestAnimationFrame !== "function") return;
  state.paintFrame = requestAnimationFrame(() => { state.paintFrame = null; historyFiltersFlush(); });
}
function historyFiltersSuspendPainting() {
  const state = historyFiltersState;
  if (state.paintFrame !== null) { cancelAnimationFrame(state.paintFrame); state.paintFrame = null; }
}
function historyFiltersFlush() {
  const state = historyFiltersState; if (!historyFiltersCanPaint()) return;
  const dirty = new Set(state.dirty); state.dirty.clear();
  if (dirty.has("catalog") || (state.catalog && state.mountedRevision === null)) historyFiltersRenderCatalog();
  if (dirty.has("conditions")) historyFiltersRenderConditions();
  if (dirty.has("choices")) historyFiltersRenderChoices();
  if (dirty.has("result")) historyFiltersRenderResult();
  if (dirty.has("status")) historyFiltersCatalogStatus();
  for (const panel of state.panels.values()) if (panel.dirty && state.panelNodes.has(panel.id)) historyFiltersRenderOptions(panel);
  // Nested lists can mount/unmount other lists, so iterate a bounded snapshot.
  if (dirty.has("windows")) for (const list of [...state.lists.values()]) if (list.root.isConnected) historyFiltersRenderList(list);
  for (const [id] of state.panelNodes) {
    const panel = state.panels.get(id);
    if (panel && (!panel.loaded || panel.stale) && !panel.loading && !panel.error) void historyFiltersLoadOptions(panel);
  }
}
function historyFiltersIntent(view = null) {
  const query = view?.query || historyFiltersQuery();
  return JSON.stringify([query.source || "", query.folderId || "", query.q || "", HistoryFilterModel.canonical(query.contentFilters)]);
}
function historyFiltersResetRecovery(view = null, { force = false } = {}) {
  const state = historyFiltersState, intent = historyFiltersIntent(view);
  if (force || intent !== state.recoveryIntent) {
    state.recoveryIntent = intent; state.recoveryUsed = false; ++state.recoveryGeneration; state.recoveryPending = null;
  }
}
function historyFiltersRetryHistory() {
  historyFiltersResetRecovery(null, { force: true }); invalidateHistoryView(); void loadHistory(1);
}
function historyFiltersApply() {
  clearTimeout(historyFiltersState.searchTimer); historyFiltersState.searchTimer = null;
  historyFiltersResetRecovery(null, { force: true });
  historyFiltersPersist(); historyFiltersRenderConditions(); historyFiltersRenderChoices();
  invalidateHistoryView(); void loadHistory(1);
}
function historyFiltersToggle(token, mode, label) {
  const entry = HistoryFilterModel.token(token); if (!entry) return;
  let next;
  try {
    next = HistoryFilterModel.toggle(historyFiltersQuery().contentFilters, entry, mode);
    HistoryFilterModel.ensureRequestBudget({ ...historyFiltersQuery(), contentFilters: next });
  }
  catch (error) { historyFiltersFeedback(error.message); return; }
  historyFiltersFeedback("");
  historyFiltersState.labels.set(HistoryFilterModel.key(entry), String(label || entry.value));
  historyFiltersQuery().contentFilters = next;
  historyFiltersApply();
}
function historyFiltersClear() {
  clearTimeout(historyFiltersState.searchTimer); historyFiltersState.composing = false;
  historyFiltersFeedback("");
  historyFiltersQuery().q = ""; historyFiltersQuery().contentFilters = { include: [], exclude: [] };
  const input = historyFiltersElement("historyFilterSearch"); if (input) input.value = "";
  historyFiltersApply();
}
function historyFiltersFeedback(message) {
  const node = historyFiltersElement("historyFilterFeedback"); if (!node) return;
  node.textContent = message; node.hidden = !message;
}
function historyFiltersMainInput() {
  if (historyFiltersState.composing) return;
  const input = historyFiltersElement("historyFilterSearch"), value = input.value;
  if (value === (historyFiltersQuery().q || "")) return;
  try { HistoryFilterModel.ensureRequestBudget({ ...historyFiltersQuery(), q: value }); }
  catch (error) { input.value = historyFiltersQuery().q || ""; historyFiltersFeedback(error.message); return; }
  historyFiltersFeedback("");
  historyFiltersQuery().q = value;
  historyFiltersResetRecovery(null, { force: true });
  // Invalidate immediately so an old response cannot commit during the debounce.
  invalidateHistoryView(); historyFiltersPersist(); historyFiltersRenderConditions();
  clearTimeout(historyFiltersState.searchTimer);
  historyFiltersState.searchTimer = setTimeout(() => { historyFiltersState.searchTimer = null; void loadHistory(1); }, 180);
}
function historyFiltersRenderConditions() {
  if (!historyFiltersState.ready) return;
  if (!historyFiltersCanPaint()) { historyFiltersSchedulePaint("conditions"); return; }
  const state = historyFiltersState, root = historyFiltersElement("historyFilterConditions"), nodes = [];
  const filters = HistoryFilterModel.normalize(historyFiltersQuery().contentFilters);
  for (const mode of ["include", "exclude"]) for (const entry of filters[mode]) {
    const key = HistoryFilterModel.key(entry), chipKey = `${mode}:${key}`;
    let chip = state.chips.get(chipKey);
    if (!chip) {
      chip = historyFiltersButton("", "history-filter-chip", () => {
        const next = chip.nextElementSibling || chip.previousElementSibling;
        historyFiltersToggle(entry, mode, state.labels.get(key));
        historyFiltersFocus(next?.isConnected ? next : historyFiltersElement("historyFilterSearch"));
      });
      chip.append(historyFiltersNode("span"), historyFiltersNode("span", "", "×"));
      chip.lastElementChild.setAttribute("aria-hidden", "true"); state.chips.set(chipKey, chip);
    }
    const group = ["favorite_group", "custom_group", "category", "trait"].includes(entry.kind);
    const missing = group && state.catalog && !state.knownGroups.has(key);
    const label = `${historyFiltersPoolLabel(entry.pool)} · ${mode === "exclude" ? "排除" : "包含"} ${state.labels.get(key) || entry.value}${missing ? "（分组已不存在）" : ""}`;
    chip.firstElementChild.textContent = label; chip.title = label;
    chip.setAttribute("aria-label", `移除条件：${label}`);
    chip.classList.toggle("is-exclude", mode === "exclude"); chip.classList.toggle("is-missing", Boolean(missing)); nodes.push(chip);
  }
  historyFiltersReconcile(root, nodes); root.hidden = !nodes.length;
  historyFiltersElement("historyFilterClear").disabled = !historyFiltersActive();
}
function historyFiltersChoice(token, label) {
  const root = historyFiltersNode("div", "history-filter-choice"); root.filterToken = token; root.filterLabel = label;
  for (const mode of ["include", "exclude"]) {
    const button = historyFiltersButton(mode === "include" ? "包含" : "排除", "", event => {
      event.preventDefault(); event.stopPropagation(); historyFiltersToggle(root.filterToken, mode, root.filterLabel);
    });
    button.dataset.filterMode = mode; root.append(button);
  }
  historyFiltersUpdateChoice(root); return root;
}
function historyFiltersUpdateChoice(root) {
  const active = historyFiltersMode(root.filterToken);
  for (const button of root.children) {
    button.setAttribute("aria-pressed", String(active === button.dataset.filterMode));
    button.setAttribute("aria-label", `${button.dataset.filterMode === "include" ? "包含" : "排除"}${historyFiltersPoolLabel(root.filterToken.pool)}：${root.filterLabel}`);
  }
}
function historyFiltersRenderChoices() {
  if (!historyFiltersState.ready) return;
  if (!historyFiltersCanPaint()) { historyFiltersSchedulePaint("choices"); return; }
  for (const root of historyFiltersElement("historyFilterDrawer").querySelectorAll(".history-filter-choice")) historyFiltersUpdateChoice(root);
}
function historyFiltersExpanded(details) {
  const target = typeof StudioMotion !== "undefined" ? StudioMotion.target(details) : undefined;
  return target === undefined ? details.open : target;
}
function historyFiltersDisclosure(details, open) {
  const state = historyFiltersState, id = details.filterId, summary = details.firstElementChild;
  if (!open && details.contains(document.activeElement) && !summary.contains(document.activeElement)) historyFiltersFocus(summary);
  if (open) state.expanded.add(id); else state.expanded.delete(id);
  summary.setAttribute("aria-expanded", String(open));
  if (open) {
    if (state.index.get(id)?.isPool) for (const other of state.poolNodes.values()) {
      if (other !== details && state.expanded.has(other.filterId)) historyFiltersDisclosure(other, false);
    }
    historyFiltersMountContents(details);
  }
  const token = details.filterLifecycle = (details.filterLifecycle || 0) + 1;
  const complete = () => {
    if (details.filterReleased || token !== details.filterLifecycle) return;
    if (!open) historyFiltersReleaseContents(details);
    historyFiltersMeasureNode(details); historyFiltersSchedulePaint();
  };
  if (typeof StudioMotion !== "undefined") StudioMotion.disclosure(details, open, { response: .3, damping: 1, groupChange: true, onComplete: complete });
  else { details.open = open; complete(); }
}
function historyFiltersBuildDisclosure(className, label) {
  const details = historyFiltersNode("details", className), summary = historyFiltersNode("summary");
  const chevron = historyFiltersNode("span", "history-filter-chevron", "›"); chevron.setAttribute("aria-hidden", "true");
  summary.append(chevron, historyFiltersNode("span", "history-filter-group-label", label));
  summary.setAttribute("aria-expanded", "false");
  summary.addEventListener("click", event => {
    if (event.target.closest("button, input, select")) return;
    event.preventDefault(); historyFiltersDisclosure(details, !historyFiltersExpanded(details));
  });
  summary.addEventListener("keydown", event => {
    if (event.target !== summary) return;
    if (event.key === "ArrowLeft" && historyFiltersExpanded(details)) { event.preventDefault(); historyFiltersDisclosure(details, false); }
    else if (event.key === "ArrowRight" && !historyFiltersExpanded(details)) { event.preventDefault(); historyFiltersDisclosure(details, true); }
  });
  details.append(summary); return details;
}
// The entire catalog is indexed as data. Missing-condition detection must never
// depend on which pool, branch, or virtual row currently has a DOM node.
function historyFiltersIndexCatalog(data) {
  const state = historyFiltersState, index = new Map(), roots = [];
  state.knownGroups.clear();
  const visit = (section, group, parent) => {
    const id = `${section}:${parent}:${group.kind || "heading"}:${group.value || ""}`;
    const candidate = ["all", ...HistoryFilterModel.attributes].includes(group.kind) || !["heading", "label", undefined].includes(group.kind);
    const rule = ["favorite_group", "custom_group", "category", "trait"].includes(group.kind);
    const entry = { id, section, kind: group.kind, value: String(group.value || ""), label: group.label || group.value || "分组", note: group.note || "", candidate, rule, children: [] };
    index.set(id, entry);
    if (rule) {
      const key = HistoryFilterModel.key({ pool: section, kind: entry.kind, value: entry.value });
      state.knownGroups.add(key); state.labels.set(key, entry.label);
    }
    entry.children = (group.children || []).map(child => visit(section, child, id));
    return id;
  };
  for (const section of data.sections || []) {
    if (!HistoryFilterModel.pools.includes(section.id)) continue;
    const groups = [{ kind: "all", value: "", label: "全部历史词条", children: [] }, ...(section.groups || [])];
    if (section.attributes?.length) groups.push({ kind: "heading", value: "attributes", label: "人物属性", note: "人物名称与以下属性，由同一个人物同时满足。", children: section.attributes.map(attribute => ({ ...attribute, value: "", children: [] })) });
    const id = `pool:${section.id}`;
    index.set(id, { id, section: section.id, isPool: true, label: historyFiltersPoolLabel(section.id), children: groups.map(group => visit(section.id, group, "root")) });
    roots.push(id);
  }
  state.index = index; state.roots = roots;
  if (!state.initializedExpansion) { state.expanded.add("pool:character"); state.initializedExpansion = true; }
  for (const id of state.expanded) if (!index.has(id)) state.expanded.delete(id);
  for (const [id, panel] of state.panels) if (!index.has(id)) { clearTimeout(panel.timer); ++panel.serial; state.panels.delete(id); }
  for (const id of state.heights.keys()) if (!index.has(id)) state.heights.delete(id);
}
function historyFiltersGroupNode(id) {
  const state = historyFiltersState, group = state.index.get(id); if (!group) return null;
  let details = state.groupNodes.get(id);
  if (!details) {
    details = historyFiltersBuildDisclosure(group.isPool ? "history-filter-pool" : "history-filter-group", group.label);
    details.filterId = id; details.dataset.historyFilterNode = id;
    details.append(historyFiltersNode("div", group.isPool ? "history-filter-pool-content" : "history-filter-group-content"));
    state.groupNodes.set(id, details); if (group.isPool) state.poolNodes.set(id, details);
    state.resizeObserver?.observe(details);
  }
  if (details.firstElementChild.children[1].textContent !== group.label) details.firstElementChild.children[1].textContent = group.label;
  details.classList.toggle("is-heading", !group.isPool && !group.candidate);
  if (group.rule) {
    const token = { pool: group.section, kind: group.kind, value: group.value };
    let choice = details.firstElementChild.querySelector(".history-filter-choice");
    if (!choice) { choice = historyFiltersChoice(token, group.label); details.firstElementChild.append(choice); }
    choice.filterToken = token; choice.filterLabel = group.label; historyFiltersUpdateChoice(choice);
  }
  if (state.expanded.has(id)) {
    details.open = true; details.firstElementChild.setAttribute("aria-expanded", "true"); historyFiltersMountContents(details);
  }
  return details;
}
function historyFiltersRenderCatalog() {
  const state = historyFiltersState; if (!state.catalog) return;
  if (!historyFiltersCanPaint()) { historyFiltersSchedulePaint("catalog"); return; }
  if (state.mountedRevision === state.catalog.group_revision) return;
  const focused = document.activeElement, restoring = state.mountedRevision === null;
  for (const [id] of [...state.groupNodes]) if (!state.index.has(id)) historyFiltersReleaseGroup(id);
  const root = historyFiltersElement("historyFilterPools");
  historyFiltersReconcile(root, state.roots.map(historyFiltersGroupNode).filter(Boolean));
  state.mountedRevision = state.catalog.group_revision;
  if (restoring) { const body = historyFiltersElement("historyFilterBody"); if (body) body.scrollTop = state.scrollTop; }
  if (focused && !focused.isConnected) historyFiltersFocus(historyFiltersElement("historyFilterSearch"));
  historyFiltersRenderConditions(); historyFiltersSchedulePaint();
}
function historyFiltersPanel(group) {
  const state = historyFiltersState; let panel = state.panels.get(group.id);
  if (!panel) {
    panel = { id: group.id, section: group.section, kind: group.kind, value: group.value || "", label: group.label,
      items: [], q: "", page: 1, loaded: false, loading: false, more: false, error: "", serial: 0, composing: false, timer: null, dirty: true, recoveryUsed: false };
    state.panels.set(group.id, panel);
  }
  panel.label = group.label; return panel;
}
function historyFiltersCreateOptions(panel) {
  const state = historyFiltersState, root = historyFiltersNode("div", "history-filter-options");
  const label = historyFiltersNode("label", "history-filter-search history-filter-option-search");
  const input = historyFiltersNode("input"); input.type = "search"; input.maxLength = 200; input.autocomplete = "off";
  input.value = panel.q; input.placeholder = `查找${panel.label || "词条"}`; input.setAttribute("aria-label", `${panel.label || "词条"}内搜索候选`); label.append(input);
  const list = historyFiltersNode("div", "history-filter-option-list"), status = historyFiltersNode("p", "history-filter-option-status");
  status.setAttribute("role", "status");
  const nodes = { root, input, list, status, rows: new Map() };
  nodes.moreButton = historyFiltersButton("加载更多", "history-filter-text-button history-filter-option-more", () => { panel.recoveryUsed = false; void historyFiltersLoadOptions(panel, { more: true }); });
  nodes.retry = historyFiltersButton("重试", "history-filter-text-button", () => { panel.recoveryUsed = false; void historyFiltersLoadOptions(panel, { more: panel.failedMore }); });
  nodes.moreButton.hidden = nodes.retry.hidden = true;
  root.append(label, list, status, nodes.moreButton, nodes.retry);
  const search = () => {
    if (panel.composing) return;
    panel.q = input.value; ++panel.serial; panel.loading = false; panel.loaded = false; panel.stale = false; panel.page = 1; panel.recoveryUsed = false; clearTimeout(panel.timer);
    panel.timer = setTimeout(() => { panel.timer = null; void historyFiltersLoadOptions(panel); }, 180);
  };
  input.addEventListener("input", search); input.addEventListener("compositionstart", () => { panel.composing = true; clearTimeout(panel.timer); });
  input.addEventListener("compositionend", () => { panel.composing = false; search(); });
  state.panelNodes.set(panel.id, nodes); historyFiltersRenderOptions(panel); return nodes;
}
function historyFiltersMountContents(details) {
  const state = historyFiltersState, group = state.index.get(details.filterId); if (!group) return;
  const content = details.lastElementChild, children = [];
  if (group.note) {
    let note = content.querySelector(":scope > .history-filter-attribute-note");
    if (!note) note = historyFiltersNode("p", "history-filter-attribute-note", group.note);
    children.push(note);
  }
  if (group.children.length) {
    let list = state.lists.get(group.id);
    if (!list) {
      list = { id: group.id, root: historyFiltersNode("div", "history-filter-group-list"), ids: [], mounted: new Set(), spacers: new Map(), keyboardPin: null };
      list.root.addEventListener("keydown", event => historyFiltersListKeydown(list, event)); state.lists.set(group.id, list);
    }
    list.ids = group.children; children.push(list.root);
  }
  if (group.candidate) {
    const panel = historyFiltersPanel(group), nodes = state.panelNodes.get(group.id) || historyFiltersCreateOptions(panel);
    children.push(nodes.root);
  }
  historyFiltersReconcile(content, children);
  const list = state.lists.get(group.id); if (list) historyFiltersRenderList(list);
  const panel = state.panels.get(group.id);
  if (group.candidate && panel && (!panel.loaded || panel.stale) && !panel.loading && !panel.error && historyFiltersCanPaint()) void historyFiltersLoadOptions(panel);
}
function historyFiltersMeasureNode(node) {
  if (!node?.isConnected || !historyFiltersCanPaint()) return false;
  const height = node.getBoundingClientRect().height, state = historyFiltersState;
  if (height > 0 && Math.abs(height - (state.heights.get(node.filterId) || 0)) > .5) { state.heights.set(node.filterId, height); return true; }
  return false;
}
function historyFiltersRenderList(list) {
  if (!historyFiltersCanPaint()) { historyFiltersSchedulePaint(); return; }
  const state = historyFiltersState, body = historyFiltersElement("historyFilterBody"), ids = list.ids, count = ids.length;
  let segments = [{ type: "range", start: 0, end: count }];
  if (count > 80 && typeof HistoryFilterWindow !== "undefined") {
    const bodyRect = body.getBoundingClientRect(), rect = list.root.getBoundingClientRect(), pinned = [];
    for (const id of list.mounted) {
      const node = state.groupNodes.get(id);
      if (node?.contains(document.activeElement) || (node && typeof StudioMotion !== "undefined" && StudioMotion.snapshot(node, "disclosure")?.active)) pinned.push(ids.indexOf(id));
    }
    if (list.keyboardPin !== null) pinned.push(list.keyboardPin);
    const plan = HistoryFilterWindow.plan({ heights: ids.map(id => state.heights.get(id)), viewportTop: bodyRect.top + (body.clientTop || 0) - rect.top, viewportHeight: body.clientHeight, overscan: 8, pinned });
    segments = plan.segments;
  }
  const desired = new Set(), nodes = [], usedSpacers = new Set();
  for (const segment of segments) {
    if (segment.type === "spacer") {
      const key = `${segment.start}:${segment.end}`; usedSpacers.add(key);
      let spacer = list.spacers.get(key);
      if (!spacer) { spacer = historyFiltersNode("div", "history-filter-virtual-spacer"); spacer.setAttribute("aria-hidden", "true"); list.spacers.set(key, spacer); }
      const height = `${segment.height}px`; if (spacer.style.height !== height) spacer.style.height = height;
      nodes.push(spacer);
    } else for (let index = segment.start; index < segment.end; index++) {
      const id = ids[index]; desired.add(id);
      const node = list.revision !== state.catalog?.group_revision ? historyFiltersGroupNode(id) : state.groupNodes.get(id) || historyFiltersGroupNode(id);
      if (node) nodes.push(node);
    }
  }
  for (const id of list.mounted) if (!desired.has(id)) historyFiltersReleaseGroup(id);
  for (const key of list.spacers.keys()) if (!usedSpacers.has(key)) list.spacers.delete(key);
  list.mounted = desired; list.revision = state.catalog?.group_revision; historyFiltersReconcile(list.root, nodes);
}
function historyFiltersListKeydown(list, event) {
  if (event.defaultPrevented || event.key !== "Tab" || list.ids.length <= 80) return;
  const state = historyFiltersState;
  const index = list.ids.findIndex(id => state.groupNodes.get(id)?.contains(event.target)); if (index < 0) return;
  const row = state.groupNodes.get(list.ids[index]);
  const focusable = [...row.querySelectorAll("summary, button, input, [tabindex='0']")].filter(node => !node.disabled && node.getClientRects().length && !node.closest("[hidden], [inert]"));
  if (event.target !== (event.shiftKey ? focusable[0] : focusable.at(-1))) return;
  const next = index + (event.shiftKey ? -1 : 1);
  if (next < 0 || next >= list.ids.length || state.groupNodes.has(list.ids[next])) return;
  list.keyboardPin = next; historyFiltersRenderList(list);
  const target = state.groupNodes.get(list.ids[next]);
  const controls = [...target.querySelectorAll("summary, button, input, [tabindex='0']")].filter(node => !node.disabled && node.getClientRects().length && !node.closest("[hidden], [inert]"));
  const control = event.shiftKey ? controls.at(-1) : controls[0];
  if (control) { event.preventDefault(); control.focus(); }
  list.keyboardPin = null; historyFiltersSchedulePaint();
}
function historyFiltersReleaseContents(details) {
  const state = historyFiltersState, id = details.filterId, list = state.lists.get(id);
  if (list) {
    for (const child of [...list.mounted]) historyFiltersReleaseGroup(child);
    list.spacers.clear(); state.lists.delete(id);
  }
  const panel = state.panels.get(id), nodes = state.panelNodes.get(id);
  if (panel) { clearTimeout(panel.timer); panel.timer = null; }
  if (panel && nodes && panel.composing) { panel.composing = false; panel.q = nodes.input.value; ++panel.serial; panel.loading = false; panel.loaded = false; panel.stale = false; panel.page = 1; }
  state.panelNodes.delete(id);
  historyFiltersReconcile(details.lastElementChild, []);
}
function historyFiltersReleaseGroup(id) {
  const state = historyFiltersState, node = state.groupNodes.get(id); if (!node) return;
  node.filterReleased = true; state.resizeObserver?.unobserve(node);
  // Finishing an interrupted disclosure disconnects its private ResizeObserver.
  if (typeof StudioMotion !== "undefined") StudioMotion.finish(node, "disclosure");
  historyFiltersReleaseContents(node); node.remove(); state.groupNodes.delete(id); state.poolNodes.delete(id);
}
function historyFiltersReleaseTree() {
  const state = historyFiltersState; historyFiltersSuspendPainting();
  for (const id of [...state.poolNodes.keys()]) historyFiltersReleaseGroup(id);
  for (const id of [...state.groupNodes.keys()]) historyFiltersReleaseGroup(id);
  state.lists.clear(); state.panelNodes.clear(); state.mountedRevision = null;
  const conditions = historyFiltersElement("historyFilterConditions"); if (conditions) historyFiltersReconcile(conditions, []);
  state.chips.clear(); state.dirty.add("catalog"); state.dirty.add("conditions");
}
function historyFiltersRenderOptions(panel) {
  const mounted = historyFiltersState.panelNodes.get(panel.id);
  if (!historyFiltersCanPaint() || !mounted) { panel.dirty = true; historyFiltersSchedulePaint("options"); return; }
  panel.dirty = false;
  const nodes = [];
  for (const item of panel.items) {
    const value = String(item.id), kind = HistoryFilterModel.attributes.includes(panel.kind) ? panel.kind : "item";
    const token = { pool: panel.section, kind, value }, label = item.title_zh || item.title || value;
    historyFiltersState.labels.set(HistoryFilterModel.key(token), label);
    let row = mounted.rows.get(value);
    if (!row) {
      row = historyFiltersNode("div", "history-filter-option"); const copy = historyFiltersNode("div", "history-filter-option-copy");
      copy.append(historyFiltersNode("strong"), historyFiltersNode("small")); row.append(copy, historyFiltersChoice(token, label)); mounted.rows.set(value, row);
    }
    row.firstElementChild.firstElementChild.textContent = label;
    const subtitle = item.subtitle || "", count = Number(item.count);
    row.firstElementChild.lastElementChild.textContent = [subtitle, Number.isFinite(count) ? `${count} 张记录` : ""].filter(Boolean).join(" · ");
    row.lastElementChild.filterLabel = label; historyFiltersUpdateChoice(row.lastElementChild); nodes.push(row);
  }
  const focused = document.activeElement; historyFiltersReconcile(mounted.list, nodes);
  for (const [id, row] of mounted.rows) if (row.parentElement !== mounted.list) mounted.rows.delete(id);
  if (focused && !focused.isConnected) historyFiltersFocus(mounted.input);
  mounted.root.setAttribute("aria-busy", String(panel.loading));
  mounted.status.textContent = panel.error ? `候选加载失败：${panel.error}` : panel.loading ? "正在查找…" : !panel.items.length && panel.loaded ? "暂无匹配的生成记录" : "";
  mounted.moreButton.hidden = !panel.more || Boolean(panel.error); mounted.moreButton.disabled = panel.loading;
  mounted.retry.hidden = !panel.error; mounted.retry.disabled = panel.loading;
  historyFiltersSchedulePaint();
}
async function historyFiltersLoadOptions(panel, { more = false, retryRevision = true } = {}) {
  if (!historyFiltersState.ready || (more && !panel.more) || panel.loading) return;
  const refreshing = Boolean(panel.stale), requestId = ++panel.serial, revision = historyFiltersQuery().groupRevision || "", page = more && !refreshing ? panel.page + 1 : 1;
  const lastPage = refreshing ? Math.max(1, panel.page + (more ? 1 : 0)) : page;
  panel.loading = true; panel.error = ""; panel.failedMore = more; historyFiltersRenderOptions(panel);
  const params = new URLSearchParams({ section: panel.section, q: panel.q, page: String(page), limit: "40" });
  if (panel.kind !== "all") { params.set("group_kind", panel.kind); if (panel.value) params.set("group_id", panel.value); }
  if (revision) params.set("group_revision", revision);
  try {
    const merged = new Map((more && !refreshing ? panel.items : []).map(item => [String(item.id), item]));
    let data;
    for (let current = page; current <= lastPage; current++) {
      params.set("page", String(current)); data = await request(`/api/history/filter-options?${params}`);
      if (requestId !== panel.serial || revision !== (historyFiltersQuery().groupRevision || "")) return;
      for (const item of data.items || []) merged.set(String(item.id), item);
      if (!data.has_more) break;
    }
    panel.items = [...merged.values()]; panel.page = Number(data.page) || page; panel.more = Boolean(data.has_more); panel.loaded = true; panel.stale = false; panel.recoveryUsed = false;
  } catch (error) {
    if (requestId !== panel.serial) return;
    if (error.code === "history_filter_catalog_changed" && retryRevision && !panel.recoveryUsed) {
      panel.recoveryUsed = true;
      const loaded = await historyFiltersLoadCatalog({ force: true });
      if (requestId !== panel.serial) return; // A newer search or catalog now owns it.
      if (loaded && String(loaded.group_revision || "") !== revision) {
        panel.loading = false;
        return historyFiltersLoadOptions(panel, { more, retryRevision: false });
      }
    }
    panel.error = error.message || "请稍后重试";
  } finally { if (requestId === panel.serial) { panel.loading = false; historyFiltersRenderOptions(panel); } }
}
function historyFiltersCatalogStatus() {
  if (!historyFiltersState.ready) return;
  if (!historyFiltersCanPaint()) { historyFiltersSchedulePaint("status"); return; }
  const state = historyFiltersState, status = historyFiltersElement("historyFilterCatalogStatus");
  status.textContent = state.catalogError ? `分组刷新失败：${state.catalogError}` : state.catalogPromise && !state.catalog ? "正在读取分组…" : "";
  status.classList.toggle("is-error", Boolean(state.catalogError));
  historyFiltersElement("historyFilterCatalogRetry").hidden = !state.catalogError;
}
async function historyFiltersLoadCatalog({ force = false, reloadHistory = true, canCommit = null } = {}) {
  const state = historyFiltersState;
  if (state.catalogPromise) return state.catalogPromise;
  if (state.catalog && !force) return state.catalog;
  const serial = ++state.catalogSerial; state.catalogError = "";
  const pending = (async () => {
    try {
      const data = await request("/api/history/filter-catalog");
      if (serial !== state.catalogSerial || (canCommit && !canCommit())) return null;
      const previous = historyFiltersQuery().groupRevision || "", revision = String(data.group_revision || "");
      const changed = !state.catalog || state.catalog.group_revision !== revision;
      historyFiltersQuery().groupRevision = revision;
      if (changed) {
        state.catalog = { ...data, group_revision: revision }; historyFiltersIndexCatalog(state.catalog);
        for (const panel of state.panels.values()) { ++panel.serial; panel.loading = false; panel.stale = true; panel.error = ""; panel.dirty = true; }
        historyFiltersRenderCatalog(); historyFiltersSchedulePaint("options");
      }
      if (previous !== revision) {
        if (reloadHistory && HistoryFilterModel.dynamic(historyFiltersQuery())) { invalidateHistoryView(); void loadHistory(1); }
      }
      return data;
    } catch (error) {
      if (serial === state.catalogSerial && (!canCommit || canCommit())) state.catalogError = error.message || "请稍后重试";
      return null;
    } finally {
      if (serial === state.catalogSerial) {
        state.catalogPromise = null; historyFiltersCatalogStatus();
        if (state.catalogAgain) { state.catalogAgain = false; historyFiltersCatalogChanged(); }
      }
    }
  })();
  state.catalogPromise = pending; historyFiltersCatalogStatus(); return pending;
}
function historyFiltersCatalogChanged() {
  const state = historyFiltersState; if (!state.ready) return;
  if (state.catalogPromise) { state.catalogAgain = true; return; }
  clearTimeout(state.refreshTimer);
  // Every opening revalidates. Keep the last successful data while closed so a
  // later network failure does not erase usable groups or their validity index.
  state.refreshTimer = setTimeout(() => { state.refreshTimer = null; if (state.open || HistoryFilterModel.dynamic(historyFiltersQuery())) void historyFiltersLoadCatalog({ force: true }); }, 180);
}
function historyFiltersHistoryChanged() {
  const state = historyFiltersState; if (!state.ready) return;
  for (const panel of state.panels.values()) { ++panel.serial; panel.loading = false; panel.stale = true; panel.error = ""; panel.dirty = true; panel.recoveryUsed = false; }
  historyFiltersSchedulePaint("options");
}
function historyFiltersHistoryLoading(view = null) {
  if (!historyFiltersState.ready || (view && view.kind !== "home")) return;
  historyFiltersResetRecovery(view);
  historyFiltersState.resultLoading = true; historyFiltersState.resultError = ""; historyFiltersRenderResult();
}
function historyFiltersHistoryResponse(data, view = null) {
  if (!historyFiltersState.ready || (view && view.kind !== "home")) return;
  historyFiltersResetRecovery(view, { force: true });
  const state = historyFiltersState, previous = historyFiltersQuery().groupRevision;
  if (data.group_revision) {
    historyFiltersQuery().groupRevision = data.group_revision;
    if (previous && previous !== data.group_revision && view) { view.cache?.clear(); ++view.queryEpoch; }
  }
  state.total = Number(data.total) || 0; state.resultLoading = false; state.resultError = ""; historyFiltersRenderResult();
}
async function historyFiltersHistoryError(error, view = null) {
  if (!historyFiltersState.ready || (view && view.kind !== "home")) return false;
  const state = historyFiltersState;
  historyFiltersResetRecovery(view);
  const intent = historyFiltersIntent(view), requestId = view?.requestId, generation = state.recoveryGeneration;
  const current = () => !view?.closed && requestId === view?.requestId
    && intent === historyFiltersIntent(view) && generation === state.recoveryGeneration;
  if (error.code === "history_filter_catalog_changed") {
    if (state.recoveryPending?.intent === intent) {
      await state.recoveryPending.promise;
      return true; // The first recovery owns feedback and any replacement read.
    }
    if (state.recoveryUsed) {
      state.resultLoading = false; state.resultError = "分组仍在变化，请稍后点击重试。"; historyFiltersRenderResult(); return false;
    }
    state.recoveryUsed = true;
    const failedRevision = (view?.query || historyFiltersQuery()).groupRevision || "";
    const pending = (async () => {
      let data = await historyFiltersLoadCatalog({ force: true, reloadHistory: false, canCommit: current });
      if (!current()) return true;
      // A preceding recovery for another intent can have been cancelled while
      // we shared its in-flight catalog read. Give this intent its own read.
      if (!data && !state.catalogError && !state.catalogPromise) {
        data = await historyFiltersLoadCatalog({ force: true, reloadHistory: false, canCommit: current });
        if (!current()) return true;
      }
      const revision = String(data?.group_revision || "");
      if (data && revision && revision !== failedRevision) {
        invalidateHistoryView(); void loadHistory(1); return true;
      }
      state.resultLoading = false;
      state.resultError = data ? "分组版本尚未更新，请稍后点击重试。" : state.catalogError || error.message || "分组读取失败，请重试。";
      historyFiltersRenderResult(); return false;
    })();
    state.recoveryPending = { intent, promise: pending };
    try { return await pending; }
    finally { if (state.recoveryPending?.promise === pending) state.recoveryPending = null; }
  }
  if (!current()) return true;
  state.resultLoading = false;
  state.resultError = error.message || "作品加载失败"; historyFiltersRenderResult(); return false;
}
function historyFiltersRenderResult() {
  if (!historyFiltersState.ready) return;
  if (!historyFiltersCanPaint()) { historyFiltersSchedulePaint("result"); return; }
  const state = historyFiltersState, result = historyFiltersElement("historyFilterResult");
  result.textContent = state.resultError ? `更新失败：${state.resultError}` : state.resultLoading ? "正在更新作品…" : `找到 ${state.total} 张作品`;
  result.classList.toggle("is-error", Boolean(state.resultError));
  historyFiltersElement("historyFilterRetry").hidden = !state.resultError;
  historyFiltersElement("historyFilterDrawer").setAttribute("aria-busy", String(state.resultLoading));
}
function historyFiltersOtherModal() {
  return Boolean(document.querySelector('dialog[open], .pool-drawer.open, .artwork-popover:not([hidden])'));
}
function historyFiltersSyncMobile() {
  const state = historyFiltersState, drawer = historyFiltersElement("historyFilterDrawer");
  state.mobile = Boolean(state.media?.matches); const modal = state.mobile && (state.open || state.modalClosing);
  drawer.setAttribute("aria-modal", String(modal));
  if (modal) {
    if (state.bodyOverflow === null) { state.bodyOverflow = document.body.style.overflow; document.body.style.overflow = "hidden"; }
    for (const child of document.body.children) {
      if (child === drawer || ["SCRIPT", "STYLE", "LINK"].includes(child.tagName)) continue;
      if (!state.inertSiblings.has(child)) state.inertSiblings.set(child, child.inert);
      if (!(child.tagName === "DIALOG" && child.open)) child.inert = true;
    }
    if (state.open && !drawer.contains(document.activeElement) && !historyFiltersOtherModal()) historyFiltersFocus(historyFiltersElement("historyFilterSearch"));
  } else {
    for (const [child, inert] of state.inertSiblings) child.inert = inert;
    state.inertSiblings.clear();
    if (state.bodyOverflow !== null) { document.body.style.overflow = state.bodyOverflow; state.bodyOverflow = null; }
  }
}
function historyFiltersSetOpen(open, { restoreFocus = true } = {}) {
  const state = historyFiltersState; if (!state.ready || state.open === open) return;
  const drawer = historyFiltersElement("historyFilterDrawer"), focusInside = drawer.contains(document.activeElement), token = ++state.lifecycle;
  if (!open) { const body = historyFiltersElement("historyFilterBody"); if (body) state.scrollTop = body.scrollTop; historyFiltersSuspendPainting(); }
  state.modalClosing = !open && state.mobile && (state.bodyOverflow !== null || state.inertSiblings.size > 0);
  state.outsidePointer = null;
  state.open = open; drawer.setAttribute("aria-hidden", String(!open)); drawer.inert = !open;
  historyFiltersElement("openHistoryFilters").setAttribute("aria-expanded", String(open));
  if (open) drawer.hidden = false;
  historyFiltersSyncMobile();
  const complete = () => {
    if (token !== state.lifecycle || state.open !== open || open) return;
    state.modalClosing = false; historyFiltersSyncMobile();
    const focused = document.activeElement;
    const focusUnclaimed = !focused || focused === document.body || focused === document.documentElement || drawer.contains(focused);
    if (restoreFocus && focusInside && focusUnclaimed && !historyFiltersOtherModal()) historyFiltersFocus(historyFiltersElement("openHistoryFilters"));
    historyFiltersReleaseTree();
  };
  if (typeof StudioMotion !== "undefined") StudioMotion.presence(drawer, open, { from: { opacity: 0, x: 32 }, response: .3, damping: 1, onComplete: complete });
  else { drawer.hidden = !open; complete(); }
  if (open) {
    historyFiltersHistoryChanged(); historyFiltersFlush();
    historyFiltersFocus(historyFiltersElement("historyFilterSearch")); void historyFiltersLoadCatalog({ force: true });
  }
}
function historyFiltersOutsideCancel() { historyFiltersState.outsidePointer = null; }
function historyFiltersBlankPoint(event) {
  const state = historyFiltersState, drawer = historyFiltersElement("historyFilterDrawer"), trigger = historyFiltersElement("openHistoryFilters");
  if (!state.open || state.mobile || event.defaultPrevented || historyFiltersOtherModal()
    || !Number.isFinite(event.clientX) || !Number.isFinite(event.clientY)) return false;
  const target = event.target?.nodeType === 3 ? event.target.parentElement : event.target;
  if (!target?.closest || drawer.contains(target) || trigger.contains(target) || target.isContentEditable) return false;
  // Outside buttons and artwork retain their normal actions and keep the drawer
  // open. This is a blank-space dismissal, not a modal click-catching overlay.
  const interactive = 'button, a, input, select, textarea, label, summary, img, svg, video, audio, canvas, iframe, object, embed, .image-card, [draggable="true"], [contenteditable]:not([contenteditable="false"]), [tabindex]:not([tabindex="-1"]), [role="button"], [role="link"], [role="checkbox"], [role="radio"], [role="switch"], [role="tab"], [role="menuitem"], [role="option"], [role="textbox"], [role="combobox"], [role="listbox"], [role="slider"], [role="spinbutton"], [role="treeitem"], [role="scrollbar"]';
  if (target.closest(interactive)) return false;
  const viewport = document.documentElement;
  if (event.clientX < 0 || event.clientY < 0 || event.clientX >= viewport.clientWidth || event.clientY >= viewport.clientHeight) return false;
  // A scrollbar is not blank space, even if clicking its thumb doesn't scroll.
  const rect = target.getBoundingClientRect?.();
  if (rect && target.offsetWidth > target.clientWidth + (target.clientLeft || 0) + 2
    && event.clientX >= rect.left + (target.clientLeft || 0) + target.clientWidth) return false;
  if (rect && target.offsetHeight > target.clientHeight + (target.clientTop || 0) + 2
    && event.clientY >= rect.top + (target.clientTop || 0) + target.clientHeight) return false;
  // Inspect direct text geometry, not container.textContent: a large container
  // may contain headings elsewhere while the actual hit is on empty padding.
  for (const child of target.childNodes || []) {
    if (child.nodeType !== 3 || !String(child.textContent || "").trim()) continue;
    if (!document.createRange) return false;
    const range = document.createRange(); range.selectNodeContents(child);
    const onText = [...range.getClientRects()].some(bounds => event.clientX >= bounds.left && event.clientX <= bounds.right
      && event.clientY >= bounds.top && event.clientY <= bounds.bottom);
    range.detach?.(); if (onText) return false;
  }
  return true;
}
function historyFiltersOutsideDown(event) {
  historyFiltersOutsideCancel();
  if (event.button !== 0 || event.isPrimary === false || !historyFiltersBlankPoint(event)) return;
  historyFiltersState.outsidePointer = { id: event.pointerId, x: event.clientX, y: event.clientY };
}
function historyFiltersOutsideMove(event) {
  const pointer = historyFiltersState.outsidePointer;
  if (pointer && (pointer.id !== event.pointerId || Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) >= 8)) historyFiltersOutsideCancel();
}
function historyFiltersOutsideUp(event) {
  const pointer = historyFiltersState.outsidePointer; historyFiltersOutsideCancel();
  if (!pointer || event.button !== 0 || event.isPrimary === false || event.pointerId !== pointer.id
    || Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) >= 8 || !historyFiltersBlankPoint(event)) return;
  historyFiltersSetOpen(false, { restoreFocus: false });
}
function historyFiltersKeydown(event) {
  const state = historyFiltersState, drawer = historyFiltersElement("historyFilterDrawer");
  if (!state.open || event.defaultPrevented || historyFiltersOtherModal()) return;
  if (event.key === "Escape" && (state.mobile || drawer.contains(event.target))) {
    event.preventDefault(); event.stopPropagation(); historyFiltersSetOpen(false); return;
  }
  if (event.key !== "Tab" || !state.mobile) return;
  const focusable = [...drawer.querySelectorAll('button, input, summary, [tabindex="0"]')].filter(node => !node.disabled && node.getClientRects().length && !node.closest("[hidden], [inert]"));
  const first = focusable[0], last = focusable.at(-1);
  if (!first) return;
  if (event.shiftKey && (document.activeElement === first || !drawer.contains(document.activeElement))) { event.preventDefault(); historyFiltersFocus(last); }
  else if (!event.shiftKey && (document.activeElement === last || !drawer.contains(document.activeElement))) { event.preventDefault(); historyFiltersFocus(first); }
}
function historyFiltersInit() {
  const state = historyFiltersState; if (state.ready || !historyFiltersElement("historyFilterDrawer")) return;
  let saved = null;
  try { saved = HistoryFilterModel.restore(localStorage.getItem(HISTORY_FILTER_STORAGE)); } catch { /* optional browser persistence */ }
  const query = historyFiltersQuery();
  Object.assign(query, { q: saved?.q || "", contentFilters: saved?.contentFilters || { include: [], exclude: [] }, groupRevision: "" });
  if (saved) { query.source = saved.source; state.labels = new Map(Object.entries(saved.labels).filter(([, value]) => typeof value === "string")); }
  state.ready = true; const input = historyFiltersElement("historyFilterSearch"); input.value = query.q;
  historyFiltersElement("openHistoryFilters").addEventListener("click", () => historyFiltersSetOpen(!state.open));
  historyFiltersElement("closeHistoryFilters").addEventListener("click", () => historyFiltersSetOpen(false));
  historyFiltersElement("historyFilterShowResults").addEventListener("click", () => historyFiltersSetOpen(false));
  historyFiltersElement("historyFilterClear").addEventListener("click", historyFiltersClear);
  historyFiltersElement("historyFilterRetry").addEventListener("click", historyFiltersRetryHistory);
  historyFiltersElement("historyFilterCatalogRetry").addEventListener("click", () => { historyFiltersResetRecovery(null, { force: true }); void historyFiltersLoadCatalog({ force: true }); });
  input.addEventListener("input", historyFiltersMainInput);
  input.addEventListener("compositionstart", () => { state.composing = true; clearTimeout(state.searchTimer); });
  input.addEventListener("compositionend", () => { state.composing = false; historyFiltersMainInput(); });
  document.addEventListener("keydown", historyFiltersKeydown, true);
  // Window capture observes the top layer before earlier document handlers can
  // dismiss it; that same click must not also dismiss the drawer underneath.
  window.addEventListener("pointerdown", historyFiltersOutsideDown, true);
  document.addEventListener("pointermove", historyFiltersOutsideMove, true);
  document.addEventListener("pointerup", historyFiltersOutsideUp, true);
  document.addEventListener("pointercancel", historyFiltersOutsideCancel, true);
  document.addEventListener("scroll", historyFiltersOutsideCancel, true);
  document.addEventListener("dragstart", historyFiltersOutsideCancel, true);
  document.addEventListener("wheel", historyFiltersOutsideCancel, { capture: true, passive: true });
  window.addEventListener("blur", historyFiltersOutsideCancel);
  document.addEventListener("focusin", event => {
    const drawer = historyFiltersElement("historyFilterDrawer");
    if (state.open && state.mobile && !drawer.contains(event.target) && !historyFiltersOtherModal()) historyFiltersFocus(input);
    if (drawer.contains(event.target)) historyFiltersSchedulePaint();
  });
  const body = historyFiltersElement("historyFilterBody");
  body.addEventListener("scroll", () => {
    if (state.open) { state.scrollTop = body.scrollTop; historyFiltersSchedulePaint(); }
  }, { passive: true });
  document.addEventListener("close", event => { if (event.target.tagName === "DIALOG") historyFiltersSchedulePaint(); }, true);
  window.addEventListener("resize", () => historyFiltersSchedulePaint());
  if (typeof ResizeObserver === "function") state.resizeObserver = new ResizeObserver(entries => {
    if (!historyFiltersCanPaint()) return;
    let changed = false;
    for (const entry of entries) if (entry.target.filterId) changed = historyFiltersMeasureNode(entry.target) || changed;
    if (changed) historyFiltersSchedulePaint();
  });
  state.media = window.matchMedia("(max-width: 700px)");
  state.media.addEventListener("change", historyFiltersSyncMobile); historyFiltersSyncMobile();
  window.addEventListener("focus", historyFiltersCatalogChanged);
  window.addEventListener("storage", event => { if (event.key && /favorite|custom|pool/.test(event.key)) historyFiltersCatalogChanged(); });
  window.addEventListener("anima-studio:favorites-updated", historyFiltersCatalogChanged);
  historyFiltersRenderConditions(); if (typeof renderHistorySource === "function") renderHistorySource({ immediate: true });
}
