/* LoRA library. The host owns generation drafts; this module owns browsing and organization. */
(() => {
  "use strict";
  const API = "/api/lora-library",VIEW_KEY = "anima-random-webui:lora-library-view:v1";
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const identity = (item) => String(item?.id || item?.filename || "");
  const words = (value) => [...new Set(String(value || "").split(/[,，\n\r]+/).map((value) => value.trim()).filter(Boolean))];
  const normalized = (value) => String(value || "").replaceAll("\\", "/").toLocaleLowerCase();
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  function queryString(query) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value === "" && !(key === "folder" && query.root) || value === undefined || value === null) continue;
      if (Array.isArray(value)) value.forEach((item) => params.append(key, item));else
      params.set(key, String(value));
    }
    return params.toString();
  }
  function descendants(groups, id) {
    const found = new Set([id]);
    for (let changed = true; changed;) {
      changed = false;
      for (const group of groups) if (found.has(group.parentId) && !found.has(group.id)) {found.add(group.id);changed = true;}
    }
    return found;
  }
  function groupPath(group, groups) {
    const seen = new Set(),names = [];
    for (let current = group; current && !seen.has(current.id); current = groups.find((item) => item.id === current.parentId)) {
      seen.add(current.id);names.unshift(current.name);
    }
    return names.join(" / ");
  }
  function groupRows(groups, collapsed = new Set()) {
    const rows = [],seen = new Set();
    const visit = (group, depth) => {
      if (seen.has(group.id)) return;
      seen.add(group.id);
      const children = groups.filter((item) => item.parentId === group.id);
      rows.push({ ...group, depth, hasChildren: !!children.length, expanded: !collapsed.has(group.id) });
      if (!collapsed.has(group.id)) children.forEach((child) => visit(child, depth + 1));
    };
    groups.filter((group) => !group.parentId || !groups.some((item) => item.id === group.parentId)).forEach((group) => visit(group, 0));
    return rows;
  }
  function bindingPayload(rows) {
    return rows.map((row) => {
      const strength = Number(row.strength);
      if (row.strength === "" || row.strength === null || !Number.isFinite(strength) || strength < -100 || strength > 100) throw new Error("绑定权重须在 -100 到 100 之间");
      return { id: row.id, strength, enabled: row.enabled !== false, ...(row.rebind === true ? { rebind: true } : {}) };
    });
  }
  function mergeDetailDraft(item, draft) {
    const base = { tags: (item.tags || []).join(", "), notes: item.notes || "", strength: item.default_strength ?? 1, triggers: (item.trigger_words || []).join("\n"), groups: [...(item.groupIds || [])] };
    if (draft) for (const key of Object.keys(base)) if (draft.dirty?.[key]) base[key] = draft[key];return base;
  }
  function detailDirtyFields(item, value) {
    return { tags: JSON.stringify(words(value.tags)) !== JSON.stringify(item.tags || []), notes: value.notes !== (item.notes || ""), strength: value.strength === "" || Number(value.strength) !== Number(item.default_strength ?? 1), triggers: value.triggers !== (item.trigger_words || []).join("\n"), groups: JSON.stringify([...value.groups].sort()) !== JSON.stringify([...(item.groupIds || [])].sort()) };
  }
  function metadataPatch(item, draft) {
    const dirty = detailDirtyFields(item, draft),patch = {};
    if (dirty.tags) patch.tags = words(draft.tags);if (dirty.notes) patch.notes = draft.notes;if (dirty.strength) patch.default_strength = Number(draft.strength);if (dirty.groups) patch.groupIds = [...draft.groups];return patch;
  }
  function submitLibraryLoras(items, addLoras) {
    // Classification, trigger metadata and default strength are part of the host contract.
    return addLoras(items.map((item) => copy(item)));
  }
  function configuredRemoval(ids, known, loras, selectionResources = new Map()) {
    const names = new Set(ids.map((id) => known.get(id)?.filename || selectionResources.get(id)?.filename).filter(Boolean).map(normalized));
    return loras.filter((item) => names.has(normalized(item.filename))).map((item) => item.filename);
  }
  function cardNavigationIndex(index, count, columns, key) {
    if (!count) return -1;columns = Math.max(1, columns);
    const offsets = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: columns, ArrowUp: -columns };
    if (key === "Home") return 0;if (key === "End") return count - 1;
    return key in offsets ? Math.max(0, Math.min(count - 1, index + offsets[key])) : index;
  }
  function createRequestGate() {
    let revision = 0,controller;
    return {
      next() {controller?.abort();controller = typeof AbortController === "undefined" ? null : new AbortController();const token = ++revision;return { signal: controller?.signal, current: () => token === revision };},
      cancel() {++revision;controller?.abort();}
    };
  }
  function createController({ request, getProfile, getLoras = () => [], onChange = () => {} }) {
    const state = { q: "", scope: "current", folder: "", root: "", recursive: true, collection: "", tag: [], tag_exclude: [], tag_mode: "all", base_model: "", in_configured: "", has_preview: "", bound: "", sort: "name", order: "asc", page: 1,
      items: [], total: 0, pages: 1, groups: [], facets: {}, selected: new Set(), selectionResources: new Map(), known: new Map(), loading: false, selectionLoading: false, error: "", profile: getProfile(), revision: null, resultKey: "", offline: false, cached: false, writable: true, lastSyncedAt: null };
    const gate = createRequestGate(),selectionGate = createRequestGate(),queryCache = new Map();
    const query = ({ all = false } = {}) => ({ q: state.q, model_profile: state.scope === "current" ? getProfile() : state.scope, folder: state.folder, root: state.root,
      recursive: state.recursive, collection: state.collection, tag: state.tag, tag_exclude: state.tag_exclude, tag_mode: state.tag_mode,
      in_configured: state.in_configured, ...(state.in_configured === "" ? {} : { configured_filenames: getLoras().map((item) => item.filename) }),
      has_preview: state.has_preview, bound: state.bound, base_model: state.base_model, sort: state.sort, order: state.order,
      ...(all ? {} : { page: state.page, limit: 60 }) });
    const selectionKey = () => JSON.stringify(query({ all: true }));
    function profileChanged() {
      if (state.profile === getProfile()) return false;
      gate.cancel();selectionGate.cancel();state.profile = getProfile();state.items = [];state.known.clear();state.selected.clear();state.selectionResources.clear();
      state.page = 1;state.selectionLoading = false;state.error = "";state.offline = false;state.cached = false;state.writable = true;return true;
    }
    async function load({ refresh = false } = {}) {
      profileChanged();const token = gate.next(),profile = getProfile(),key = JSON.stringify(query());
      state.loading = true;state.error = "";onChange(state);
      try {
        const data = await request(`${API}?${queryString({ ...query(), ...(refresh ? { refresh: true } : {}) })}`, { signal: token.signal });
        if (!token.current() || profile !== getProfile() || key !== JSON.stringify(query())) return false;
        if (state.offline && !data.offline) state.known.clear();
        state.offline = data.offline === true;state.cached = data.cached === true;state.writable = data.writable !== false && !state.offline;state.error = data.error || "";state.lastSyncedAt = data.last_synced_at || (state.offline ? state.lastSyncedAt : new Date().toISOString());
        state.items = data.items || [];state.total = Number(data.total || 0);state.pages = Math.max(1, Number(data.total_pages || data.pages || 1));
        state.page = Number(data.page || state.page);state.groups = data.groups || [];state.facets = data.facets || {};state.revision = data.revision;
        state.resultKey = JSON.stringify(query());
        state.items.forEach((item) => state.known.set(identity(item), item));
        if (!state.offline) {queryCache.set(state.resultKey, copy(data));if (queryCache.size > 24) queryCache.delete(queryCache.keys().next().value);}
        return !state.offline;
      } catch (error) {
        if (token.current() && profile === getProfile() && error.name !== "AbortError") {
          state.error = error.message || "读取失败";state.offline = !error.status || error.status >= 500 || error.status === 408;state.writable = false;
          const cached = state.offline ? queryCache.get(key) : null;state.cached = !!cached;
          if (cached) {state.items = copy(cached.items || []);state.total = Number(cached.total || 0);state.pages = Math.max(1, Number(cached.total_pages || cached.pages || 1));state.groups = copy(cached.groups || []);state.facets = copy(cached.facets || {});state.revision = cached.revision;state.lastSyncedAt = cached.last_synced_at || state.lastSyncedAt;state.resultKey = key;state.items.forEach((item) => state.known.set(identity(item), item));} else
          {state.items = [];state.resultKey = "";}
        }
        return false;
      } finally {if (token.current() && profile === getProfile()) {state.loading = false;onChange(state);}}
    }
    function stage(patch) {
      gate.cancel();selectionGate.cancel();Object.assign(state, patch);state.selectionLoading = false;state.loading = false;
      if (!("page" in patch)) state.page = 1;
    }
    function update(patch, { loadNow = true } = {}) {
      stage(patch);
      return loadNow ? load() : onChange(state);
    }
    async function selectAll() {
      const token = selectionGate.next(),key = selectionKey(),profile = getProfile(),captured = copy(query({ all: true }));
      state.selectionLoading = true;onChange(state);
      try {
        const data = await request(`${API}/selection`, { method: "POST", body: JSON.stringify({ query: captured }), signal: token.signal });
        if (!token.current() || key !== selectionKey() || profile !== getProfile()) return false;
        if (data.offline) {state.offline = true;state.cached = state.items.length > 0;state.writable = false;state.error = data.error || "";state.lastSyncedAt = data.last_synced_at || state.lastSyncedAt;}
        for (const id of data.ids || []) state.selected.add(String(id));
        for (const item of data.resources || []) state.selectionResources.set(String(item.id), { id: String(item.id), filename: item.filename });
        state.selectionRevision = data.revision;return true;
      } finally {if (token.current()) {state.selectionLoading = false;onChange(state);}}
    }
    function toggle(id) {state.selected.has(id) ? state.selected.delete(id) : state.selected.add(id);onChange(state);}
    function selectPage() {state.items.forEach((item) => state.selected.add(identity(item)));onChange(state);}
    function clearSelection() {selectionGate.cancel();state.selectionLoading = false;state.selected.clear();onChange(state);}
    function replace(item) {const id = identity(item);state.known.set(id, item);state.items = state.items.map((old) => identity(old) === id ? item : old);
      if (!item.offline) for (const cached of queryCache.values()) cached.items = (cached.items || []).map((old) => identity(old) === id ? copy(item) : old);
      onChange(state);}
    return { state, query, load, stage, update, selectAll, toggle, selectPage, clearSelection, replace, profileChanged,
      markOffline(error = "") {state.offline = true;state.cached = state.items.length > 0;state.writable = false;state.error = error;onChange(state);},
      cancel() {gate.cancel();selectionGate.cancel();state.loading = false;state.selectionLoading = false;} };
  }
  const core = { createController, createRequestGate, descendants, groupRows, groupPath, bindingPayload, mergeDetailDraft, detailDirtyFields, metadataPatch, submitLibraryLoras, configuredRemoval, cardNavigationIndex, words, queryString };
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  if (typeof document === "undefined") return;

  let host,controller,ui,ready = false,expanded = false,selectionMode = false,view = "grid",detailItem = null,detailEpoch = 0,detailOpener = null;
  let searchTimer,characterTimer,bindingLoraTimer,jobTimer,activeJob,groupEditor,batchEditor,bindingEditor,selectionBusy = false,pendingFocus = null;
  let detailEditing = false,detailSaving = false,restoreTriggerDraft = false,leaveAction = null;
  let compactScroll = 0,compactPaneScroll = 0,expandedScroll = 0,morphRevision = 0;
  const paneRects = new Map();
  const collapsedGroups = new Set(),collapsedFolders = new Set(),detailDrafts = new Map(),mutationBusy = new Set();
  const detailGate = createRequestGate(),characterGate = createRequestGate(),bindingGate = createRequestGate(),bindingLoraGate = createRequestGate();
  const elements = ["loraDialog", "loraSearch", "loraCatalog", "loraCatalogEmpty", "loraCatalogMeta", "closeLora", "cancelLora", "lmTitle", "lmExpand", "lmFilters", "lmFiltersToggle", "lmProfile", "lmPreviewFilter", "lmBoundFilter", "lmTagFilter", "lmSort", "lmSortOrder", "lmClearFilters", "lmGridView", "lmListView", "lmSelectToggle", "lmRefresh", "lmNotice", "lmSidebar", "lmSidebarToggle", "lmGroups", "lmGroupsEmpty", "lmNewGroup", "lmFolders", "lmRecursive", "lmLocation", "lmSelectionCount", "lmSelectionTools", "lmSelectPage", "lmSelectAll", "lmClearSelection", "lmCatalogScroll", "lmEmptyMessage", "lmEmptyReset", "lmBatchBar", "lmBatchAdd", "lmJob", "lmJobTitle", "lmJobCount", "lmJobProgress", "lmJobError", "lmJobCancel", "lmJobRetry", "lmDetail", "lmCloseDetail", "lmDetailBody", "lmFooterHint", "lmPrev", "lmNext", "lmPage", "lmGroupDialog", "lmGroupForm", "lmGroupTitle", "lmGroupName", "lmGroupParent", "lmGroupError", "lmGroupDelete", "lmGroupDeleteConfirm", "lmGroupDeleteCommit", "lmGroupDeleteCancel", "lmBatchDialog", "lmBatchForm", "lmBatchTitle", "lmBatchSummary", "lmBatchFields", "lmBatchError", "lmBindingDialog", "lmBindingTitle", "lmCharacterPicker", "lmCharacterSearch", "lmCharacterResults", "lmCharacterMore", "lmBindingEditor", "lmBindingCharacterName", "lmBindingCharacterId", "lmChangeCharacter", "lmBindingRows", "lmBindingLoraSearch", "lmBindingLoraResults", "lmBindingError", "lmBindingSave", "lmBindingStatus"];
  elements.push("lmTagMode", "lmExcludeTagFilter", "lmConfiguredFilter", "lmBaseModelFilter");
  elements.push("lmCompactPane", "lmExpandedPane", "lmShellSurface", "lmExpandCompact", "lmFilterChips", "lmFilterTagSearch", "lmIncludeTags", "lmExcludeTags", "lmBatchRemove");
  const icons = {
    folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z"/></svg>',
    star: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m12 3 2.8 5.7 6.3.9-4.6 4.5 1.1 6.3-5.6-3-5.6 3 1.1-6.3-4.6-4.5 6.3-.9Z"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    minus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M5 12h14"/></svg>',
    chevron: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg>'
  };
  const api = (path, method = "GET", body, options = {}) => host.request(`${API}${path}`, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }), ...options });
  const showError = (element, message = "") => {element.textContent = message;element.hidden = !message;};
  const notice = (message) => showError(ui.lmNotice, message);
  const notify = (message) => host.notify?.(message);
  const currentState = () => controller.state;
  const writable = () => currentState().writable !== false && !currentState().offline;
  function requireWritable() {if (writable()) return true;if (currentState().offline) offlineNotice(currentState());else notice("LoRA 管理数据当前只读，请先恢复本地存储后重试。");return false;}
  function offlineNotice(state) {
    ui.lmNotice.replaceChildren();ui.lmNotice.hidden = false;ui.lmNotice.dataset.offline = "true";
    const summary = document.createElement("span");summary.textContent = state.cached ? "LoRA Manager 暂时离线，正在显示只读缓存。恢复连接后刷新即可继续修改。" : "LoRA Manager 暂时离线。恢复连接后点击刷新重试。";ui.lmNotice.append(summary);
    if (state.lastSyncedAt) {const time = document.createElement("small"),date = new Date(state.lastSyncedAt);time.textContent = Number.isNaN(date.valueOf()) ? "" : `上次同步 ${date.toLocaleString()}`;ui.lmNotice.append(time);}
    if (state.error) {const details = document.createElement("details"),heading = document.createElement("summary"),text = document.createElement("p");heading.textContent = "连接详情";text.textContent = state.error;details.append(heading, text);ui.lmNotice.append(details);}
  }
  const title = (item) => item.display_name || item.model_name || item.basename || String(item.filename || item.name || "LoRA").split(/[\\/]/).pop();
  const usable = (item) => item.available !== false && item.classification_status !== "unrecognized" && item.model_profile === host.getProfile();
  const configured = (item) => (host.getLoras() || []).some((value) => normalized(value.filename) === normalized(item.filename));
  function safeImage(value) {
    if (!value) return "";
    try {const url = new URL(value, location.href);return /^(https?:)$/.test(url.protocol) ? url.href : "";} catch {return "";}
  }
  function dialog(element, open) {
    if (globalThis.StudioMotion) return StudioMotion.dialog(element, open);
    if (open && !element.open) element.showModal();else if (!open && element.open) element.close();
  }
  function animateIn(element, channel = "library-content") {
    if (!globalThis.StudioMotion || StudioMotion.reduced()) return;
    StudioMotion.spring(element, channel, { opacity: 1 }, { initial: { opacity: .72 }, response: .22, damping: 1,
      onUpdate: (value) => {element.style.opacity = String(value.opacity);}, onComplete: () => element.style.removeProperty("opacity") });
  }
  function persistView() {
    const state = currentState();
    try {localStorage.setItem(VIEW_KEY, JSON.stringify({ view, scope: state.scope, collection: state.collection, folder: state.folder, root: state.root, recursive: state.recursive, sort: state.sort, order: state.order, collapsedGroups: [...collapsedGroups], collapsedFolders: [...collapsedFolders] }));} catch {/* Optional view preference. */}
  }
  function syncFilters() {
    const state = currentState();
    ui.loraSearch.value = state.q;ui.lmProfile.value = state.scope;ui.lmPreviewFilter.value = state.has_preview;ui.lmBoundFilter.value = state.bound;
    ui.lmTagMode.value = state.tag_mode;ui.lmConfiguredFilter.value = state.in_configured;ui.lmBaseModelFilter.value = state.base_model;
    ui.lmSort.value = state.sort;ui.lmRecursive.checked = state.recursive;ui.lmSortOrder.textContent = state.order === "asc" ? "↑" : "↓";ui.lmSortOrder.title = state.order === "asc" ? "升序，点击改为降序" : "降序，点击改为升序";ui.lmSortOrder.setAttribute("aria-label", state.order === "asc" ? "切换为降序" : "切换为升序");
    ui.lmGridView.setAttribute("aria-pressed", String(view === "grid"));ui.lmListView.setAttribute("aria-pressed", String(view === "list"));
    ui.lmSelectToggle.setAttribute("aria-pressed", String(selectionMode));
  }
  function rememberControlFocus() {
    const active = document.activeElement;
    if (active && active !== document.body && active !== document.documentElement) {pendingFocus = null;return active.id && ui.loraDialog.contains(active) ? active : null;}
    return pendingFocus;
  }
  function restoreControlFocus(control) {
    if (!control) return;const current = document.activeElement;
    if (current && current !== document.body && current !== document.documentElement && current !== control) {pendingFocus = null;return;}
    if (!control.isConnected || control.closest("dialog")?.open === false) {pendingFocus = null;return;}
    if (control.disabled || control.closest("[inert]")) {pendingFocus = control;return;}
    if (control.checkVisibility && !control.checkVisibility()) {pendingFocus = null;return;}
    if (current !== control) control.focus({ preventScroll: true });pendingFocus = null;
  }
  async function updateQuery(patch) {if (!allowDetailLeave(() => void updateQuery(patch))) return;clearTimeout(searchTimer);closeDetail({ force: true });await controller.update(patch);syncFilters();persistView();}
  function render(state = currentState()) {
    if (!ready) return;
    const controlFocus = rememberControlFocus();
    const resultsCurrent = state.resultKey === JSON.stringify(controller.query());
    ui.loraCatalog.setAttribute("aria-busy", String(state.loading || !resultsCurrent));
    ui.loraCatalog.inert = state.loading || !resultsCurrent;
    ui.lmRefresh.disabled = state.loading;
    ui.loraCatalogMeta.textContent = state.loading || !resultsCurrent && !state.error ? "正在读取资料库…" : state.offline && state.cached ? `${state.total.toLocaleString()} 个 LoRA · 只读缓存` : state.error ? "资料库暂不可用" : `${state.total.toLocaleString()} 个 LoRA${state.scope === "current" ? ` · ${"Anima"}` : ""}`;
    ui.lmPage.textContent = `${state.page} / ${state.pages}`;
    ui.lmPrev.disabled = state.loading || state.page <= 1;ui.lmNext.disabled = state.loading || state.page >= state.pages;
    renderBreadcrumbs();renderFilterChips();
    if (state.offline) offlineNotice(state);else if (state.error) notice(`${state.error}。可使用右上角刷新重试。`);else if (!state.writable) notice("LoRA 管理数据当前只读，请先恢复本地存储后重试。");else if (ui.lmNotice.dataset.offline) {delete ui.lmNotice.dataset.offline;notice("");}
    if (!state.loading) {
      const focused = document.activeElement,oldFocus = focused?.closest?.("[data-lm-card]")?.dataset.lmCard;
      const focusKind = focused?.hasAttribute?.("data-lm-star") ? "[data-lm-star]" : focused?.hasAttribute?.("data-lm-add") ? "[data-lm-add]" : ".lm-card-open";
      const existing = new Map([...ui.loraCatalog.children].map((card) => [card.dataset.lmCard, card]));
      const cards = state.items.map((item) => {
        const id = identity(item),old = existing.get(id),key = JSON.stringify([title(item), item.preview, item.filename, item.folder, item.base_model, item.model_profile, item.available]);
        const card = old?._renderKey === key ? old : renderCard(item);card._renderKey = key;updateCard(card, item);
        const selected = state.selected.has(id);card.classList.toggle("selected", selected);card.classList.toggle("detail-active", !!detailItem && identity(detailItem) === id);
        if (selectionMode) {card.querySelector(".lm-card-open").setAttribute("aria-pressed", String(selected));card.querySelector(".lm-card-select").textContent = selected ? "✓" : "";}
        return card;
      });
      // Retain decoded images and live control nodes whenever their content is unchanged.
      let cursor = ui.loraCatalog.firstElementChild;
      for (const card of cards) {if (card === cursor) cursor = cursor.nextElementSibling;else ui.loraCatalog.insertBefore(card, cursor);}
      const retained = new Set(cards);for (const card of [...ui.loraCatalog.children]) if (!retained.has(card)) card.remove();
      ui.loraCatalog.classList.toggle("lm-list-view", view === "list");
      if (oldFocus && focused !== document.activeElement) ui.loraCatalog.querySelector(`[data-lm-card="${CSS.escape(oldFocus)}"] ${focusKind}`)?.focus({ preventScroll: true });
    }
    ui.loraCatalogEmpty.hidden = state.loading || state.items.length > 0;
    ui.lmEmptyMessage.textContent = state.offline ? "这个视图暂无已保存的缓存。恢复连接后刷新，或返回之前浏览过的视图。" : state.error || (state.q || state.folder || state.collection || state.tag.length ? "试试其他关键词，或清除筛选。" : "确认 LoRA Manager 已连接，并将 LoRA 放入 ComfyUI 的模型目录。");
    ui.loraCatalogEmpty.querySelector("h3").textContent = state.offline ? "暂时无法读取资料库" : "这里还没有 LoRA";
    renderSelection();renderSidebar();renderTagOptions();
    ui.lmNewGroup.disabled = !writable();ui.lmGroupForm.querySelector('[type="submit"]').disabled = !writable();ui.lmGroupDelete.disabled = !writable();ui.lmGroupDeleteCommit.disabled = !writable();
    ui.lmBatchForm.querySelector('[type="submit"]').disabled = !writable();ui.lmJobRetry.disabled = !writable();
    if (!writable()) {ui.lmBindingSave.disabled = true;if (detailItem && !ui.lmDetail.hidden) applyDetailReadonly();} else
    if (detailItem && !ui.lmDetail.hidden && ui.lmDetail.dataset.readonly === "true") {ui.lmDetail.dataset.readonly = "false";void refreshDetail();}
    restoreControlFocus(controlFocus);
  }
  function renderCard(item) {
    const id = identity(item),preview = safeImage(item.preview);
    const card = document.createElement("article");card.className = "lm-card";card.dataset.lmCard = id;
    card.innerHTML = `<button type="button" class="lm-card-open" data-lm-open="${escape(id)}"><span class="lm-card-preview">${preview ? `<img src="${escape(preview)}" loading="lazy" decoding="async" alt="">` : icons.folder}</span><span class="lm-card-copy"><strong title="${escape(title(item))}">${escape(title(item))}</strong><small title="${escape(item.filename)}"></small></span></button><span class="lm-card-controls"><button class="lm-card-star" type="button" data-lm-star="${escape(id)}">${icons.star}</button><button class="lm-card-add" type="button" data-lm-add="${escape(id)}"></button></span><span class="lm-card-select" aria-hidden="true"></span><span class="lm-card-badge"></span>`;
    card.querySelector("img")?.addEventListener("error", (event) => {event.currentTarget.parentElement.title = "预览暂不可用";event.currentTarget.remove();});
    updateCard(card, item);return card;
  }
  function updateCard(card, item) {
    const selected = currentState().selected.has(identity(item)),added = configured(item),allowed = usable(item);
    card.classList.toggle("selected", selected);card.classList.toggle("unavailable", !allowed);
    card.classList.toggle("detail-active", !!detailItem && identity(detailItem) === identity(item));
    const opener = card.querySelector(".lm-card-open"),star = card.querySelector(".lm-card-star"),add = card.querySelector(".lm-card-add"),check = card.querySelector(".lm-card-select"),badge = card.querySelector(".lm-card-badge");
    opener.setAttribute("aria-label", `${selectionMode ? selected ? "取消选择" : "选择" : "查看详情"} ${title(item)}`);
    if (selectionMode) opener.setAttribute("aria-pressed", String(selected));else opener.removeAttribute("aria-pressed");
    check.hidden = !selectionMode;check.textContent = selected ? "✓" : "";
    star.classList.toggle("is-favorite", !!item.favorite);star.setAttribute("aria-pressed", String(!!item.favorite));star.setAttribute("aria-label", `${item.favorite ? "取消收藏" : "收藏"} ${title(item)}`);star.disabled = !writable();
    add.classList.toggle("is-configured", added);add.setAttribute("aria-label", `${added ? "移出当前配置" : "加入当前配置"} ${title(item)}`);add.title = added ? "移出当前配置" : allowed ? "加入当前配置" : "与当前模型不兼容";
    if (add.dataset.added !== String(added)) {add.innerHTML = added ? icons.minus : icons.plus;add.dataset.added = String(added);}
    add.disabled = !added && (!allowed || !writable());
    card.querySelector(".lm-card-copy small").textContent = !allowed ? `${item.model_profile === "anima" ? "Anima" : "未识别模型"} · 当前不可启用` : item.folder || item.base_model || "根目录";
    badge.hidden = !item.bindings?.length;badge.textContent = `${item.bindings?.length || 0} 个人物`;
  }

  function renderSelection() {
    const controlFocus = rememberControlFocus();
    const state = currentState(),count = state.selected.size;
    ui.lmSelectionCount.textContent = `已选 ${count}`;ui.lmSelectionCount.hidden = !selectionMode;
    ui.lmSelectionTools.hidden = !selectionMode;ui.lmBatchBar.hidden = !selectionMode || !count;
    ui.lmSelectAll.textContent = state.selectionLoading ? "正在获取全部结果…" : "选择全部筛选结果";
    ui.lmSelectAll.disabled = state.selectionLoading || state.loading || !state.total || !writable();
    ui.lmSelectPage.disabled = state.loading || !state.items.length;
    ui.lmBatchAdd.disabled = selectionBusy || !count || !writable();
    ui.lmBatchRemove.disabled = selectionBusy || !configuredRemoval([...state.selected], state.known, host.getLoras(), state.selectionResources).length;
    ui.lmFooterHint.textContent = count && selectionMode ? `已选择 ${count} 个 · 跨页保留` : expanded ? "点击卡片查看详情 · Ctrl K 搜索" : "点击卡片加入当前配置";
    ui.lmBatchBar.querySelectorAll("[data-lm-batch]").forEach((button) => button.disabled = !writable() || button.dataset.lmBatch === "bind" && host.getProfile() !== "anima");
    restoreControlFocus(controlFocus);
  }
  function treeNode({ id, name, depth, hasChildren, expanded: open, count }, kind, active) {
    const row = document.createElement("div");row.className = "lm-tree-row";row.dataset.treeId = id;row.dataset.treeKind = kind;row.style.setProperty("--depth", depth);
    row.setAttribute("role", "treeitem");row.setAttribute("aria-level", String(depth + 1));row.setAttribute("aria-selected", String(active));row.tabIndex = active ? 0 : -1;
    if (hasChildren) row.setAttribute("aria-expanded", String(open));
    row.innerHTML = `<button type="button" class="lm-tree-disclosure" tabindex="-1" data-lm-tree-toggle="${escape(id)}" data-kind="${kind}" aria-label="${open ? "折叠" : "展开"} ${escape(name)}" ${hasChildren ? "" : 'disabled aria-hidden="true"'}><span>${hasChildren ? icons.chevron : ""}</span></button><button type="button" class="lm-tree-label" tabindex="-1" data-lm-tree-select="${escape(id)}" data-kind="${kind}" title="${escape(name)}">${kind === "folder" ? icons.folder : icons.star}${escape(name)}${Number.isFinite(count) ? `<small>${count}</small>` : ""}</button>${kind === "group" ? `<button type="button" class="lm-tree-edit" tabindex="-1" data-lm-group-edit="${escape(id)}" aria-label="编辑 ${escape(name)}">⋯</button>` : ""}`;
    return row;
  }
  function patchNode(target, source) {
    if (target.nodeType === 3) {if (target.data !== source.data) target.data = source.data;return;}
    for (const attr of [...target.attributes]) if (!source.hasAttribute(attr.name)) target.removeAttribute(attr.name);
    for (const attr of [...source.attributes]) if (target.getAttribute(attr.name) !== attr.value) target.setAttribute(attr.name, attr.value);
    const children = [...source.childNodes];children.forEach((node, index) => {const old = target.childNodes[index];if (!old) target.append(node.cloneNode(true));else if (old.nodeType !== node.nodeType || old.nodeName !== node.nodeName) old.replaceWith(node.cloneNode(true));else patchNode(old, node);});
    while (target.childNodes.length > children.length) target.lastChild.remove();
  }
  function replaceTree(container, rows) {
    const focused = document.activeElement?.closest?.(".lm-tree-row"),focusId = focused?.parentElement === container ? focused.dataset.treeId : null;
    const old = new Map([...container.children].map((node) => [node.dataset.treeId, node]));
    const next = rows.map((row) => {
      const node = old.get(row.dataset.treeId);
      if (!node) return row;
      node.className = row.className;[...row.children].forEach((child, index) => {if (node.children[index]) patchNode(node.children[index], child);else node.append(child.cloneNode(true));});while (node.children.length > row.children.length) node.lastElementChild.remove();node.style.setProperty("--depth", row.style.getPropertyValue("--depth"));
      node.setAttribute("role", "treeitem");node.removeAttribute("aria-hidden");node.inert = false;
      for (const attr of ["aria-level", "aria-selected", "aria-expanded"]) {if (row.hasAttribute(attr)) node.setAttribute(attr, row.getAttribute(attr));else node.removeAttribute(attr);}
      node.tabIndex = row.tabIndex;return node;
    });
    const retained = new Set(next),leaving = [...container.children].filter((row) => !retained.has(row)),order = [...next],oldRows = [...container.children];
    for (const row of leaving) {const previous = oldRows.slice(0, oldRows.indexOf(row)).reverse().find((item) => order.includes(item));order.splice(previous ? order.indexOf(previous) + 1 : 0, 0, row);}
    let cursor = container.firstElementChild;for (const row of order) {if (row === cursor) cursor = cursor.nextElementSibling;else container.insertBefore(row, cursor);}
    for (const row of next) if (!old.has(row.dataset.treeId) || row._lmTreeLeaving) animateLibraryTree(row, true);
    for (const row of leaving) if (!row._lmTreeLeaving) animateLibraryTree(row, false);
    if (!next.some((row) => row.tabIndex === 0) && next[0]) next[0].tabIndex = 0;
    if (focusId) next.find((row) => row.dataset.treeId === focusId)?.focus({ preventScroll: true });
  }
  function animateLibraryTree(row, visible) {
    row._lmTreeLeaving = !visible;row.inert = !visible;
    if (!visible) {row.setAttribute("aria-hidden", "true");row.removeAttribute("role");}
    const finish = () => {if (row._lmTreeLeaving === visible) return;if (!visible) {row.remove();return;}for (const key of ["height", "minHeight", "overflow", "opacity", "transform"]) row.style.removeProperty(key.replace(/[A-Z]/g, (char) => `-${char.toLowerCase()}`));};
    if (!globalThis.StudioMotion || StudioMotion.reduced()) {finish();return;}
    const current = StudioMotion.snapshot(row, "library-tree"),height = row._lmTreeHeight || row.getBoundingClientRect().height || 34;row._lmTreeHeight = height;
    row.style.minHeight = "0";row.style.overflow = "hidden";
    StudioMotion.spring(row, "library-tree", { progress: visible ? 1 : 0 }, { initial: current?.active ? undefined : { progress: visible ? 0 : 1 }, response: visible ? .28 : .24, damping: 1,
      onUpdate: ({ progress }) => {const value = Math.max(0, Math.min(1, progress));row.style.height = `${height * value}px`;row.style.opacity = String(value);row.style.transform = `translateX(${-5 * (1 - value)}px)`;}, onComplete: finish });
  }
  function folderRows() {
    const folders = currentState().facets.folders || [],nodes = new Map();
    const roots = [...new Set(folders.map((folder) => String(folder.root || "")))];
    for (const root of roots) nodes.set(`${root}|`, { id: `${root}|`, name: roots.length > 1 ? root || "默认根目录" : "LoRA 根目录", path: "", root, depth: 0, parentId: null, count: folders.filter((folder) => String(folder.root || "") === root).reduce((count, folder) => count + Number(folder.count || 0), 0) });
    for (const folder of folders) {
      const path = String(folder.path ?? folder.name ?? "").replaceAll("\\", "/"),root = String(folder.root || ""),parts = path.split("/").filter(Boolean);
      parts.forEach((part, index) => {const partial = parts.slice(0, index + 1).join("/"),id = `${root}|${partial}`;
        nodes.set(id, { ...nodes.get(id), id, name: part, path: partial, root, depth: index + 1, parentId: index ? `${root}|${parts.slice(0, index).join("/")}` : `${root}|`, ...(index === parts.length - 1 ? { count: folder.count } : {}) });});
    }
    return groupRows([...nodes.values()], collapsedFolders);
  }
  function renderSidebar() {
    const state = currentState();
    ui.lmSidebar.querySelectorAll("[data-lm-collection]").forEach((button) => button.classList.toggle("active", button.dataset.lmCollection === state.collection && !state.folder && !state.root));
    replaceTree(ui.lmGroups, groupRows(state.groups, collapsedGroups).map((group) => treeNode(group, "group", state.collection === group.id && !state.folder)));
    ui.lmGroups.querySelectorAll(".lm-tree-edit").forEach((button) => button.disabled = !writable());
    ui.lmGroupsEmpty.hidden = state.groups.length > 0;
    const rows = folderRows();ui.lmFolders._rows = rows;
    replaceTree(ui.lmFolders, rows.map((folder) => treeNode(folder, "folder", state.folder === folder.path && state.root === folder.root && !state.collection)));
  }
  function renderTagOptions() {
    const state = currentState(),options = state.facets.tags || [],q = ui.lmFilterTagSearch.value.trim().toLocaleLowerCase();
    for (const [element, selected, key] of [[ui.lmIncludeTags, state.tag, "tag"], [ui.lmExcludeTags, state.tag_exclude, "tag_exclude"]]) {
      const all = [...options];for (const name of selected) if (!all.some((value) => value.name === name)) all.push({ name });
      const signature = JSON.stringify([all, selected, q]);if (element.dataset.signature === signature) continue;element.dataset.signature = signature;
      const old = new Map([...element.children].map((node) => [node.dataset.tag, node]));
      const nodes = all.filter((value) => !q || value.name.toLocaleLowerCase().includes(q)).map((value) => {
        let label = old.get(value.name);
        if (!label) {label = document.createElement("label");label.className = "lm-tag-option";label.dataset.tag = value.name;label.innerHTML = '<input type="checkbox"><span></span><small></small>';const input = label.querySelector("input");input.value = value.name;input.addEventListener("change", () => {const values = new Set(currentState()[key]);input.checked ? values.add(value.name) : values.delete(value.name);queueQuery({ [key]: [...values] });});}
        label.querySelector("input").checked = selected.includes(value.name);label.querySelector("span").textContent = value.name;label.querySelector("small").textContent = value.count ?? "";return label;
      });
      let cursor = element.firstElementChild;for (const node of nodes) {if (node === cursor) cursor = cursor.nextElementSibling;else element.insertBefore(node, cursor);}const retained = new Set(nodes);for (const node of [...element.children]) if (!retained.has(node)) node.remove();
    }
    const signature = JSON.stringify(state.facets.base_models || []);
    if (ui.lmBaseModelFilter.dataset.signature !== signature) {ui.lmBaseModelFilter.replaceChildren(new Option("全部基础模型", ""), ...(state.facets.base_models || []).map((value) => new Option(`${value.name} (${value.count ?? 0})`, value.name)));ui.lmBaseModelFilter.dataset.signature = signature;}
    ui.lmBaseModelFilter.value = state.base_model;
  }
  function renderFilterChips() {
    const state = currentState(),chips = [];
    const add = (key, label, value = "") => chips.push({ key, label, value });
    if (state.scope !== "current") add("scope", "Anima", "current");
    if (state.base_model) add("base_model", state.base_model);
    if (state.has_preview !== "") add("has_preview", state.has_preview === "true" ? "有预览" : "无预览");
    if (state.bound !== "") add("bound", state.bound === "true" ? "已绑定人物" : "未绑定人物");
    if (state.in_configured !== "") add("in_configured", state.in_configured === "true" ? "已加入配置" : "未加入配置");
    state.tag.forEach((tag) => add("tag", `包含 ${tag}`, tag));state.tag_exclude.forEach((tag) => add("tag_exclude", `排除 ${tag}`, tag));
    ui.lmFilterChips.hidden = !chips.length;ui.lmFiltersToggle.dataset.count = chips.length || "";
    const key = JSON.stringify(chips);if (ui.lmFilterChips.dataset.key === key) return;ui.lmFilterChips.dataset.key = key;
    ui.lmFilterChips.replaceChildren(...chips.map((chip) => {const button = document.createElement("button");button.type = "button";button.className = "lm-filter-chip";button.textContent = `${chip.label} ×`;button.setAttribute("aria-label", `移除筛选：${chip.label}`);button.onclick = () => queueQuery({ [chip.key]: Array.isArray(state[chip.key]) ? currentState()[chip.key].filter((value) => value !== chip.value) : chip.value });return button;}));
  }
  function renderBreadcrumbs() {
    const state = currentState(),key = JSON.stringify([state.root, state.folder, state.collection, state.groups]);if (ui.lmLocation.dataset.key === key) return;ui.lmLocation.dataset.key = key;
    ui.lmLocation.replaceChildren();
    const add = (name, action) => {const button = document.createElement("button");button.type = "button";button.className = "lm-breadcrumb";button.textContent = name;button.onclick = action;if (ui.lmLocation.childElementCount) {const separator = document.createElement("span");separator.textContent = "/";separator.setAttribute("aria-hidden", "true");ui.lmLocation.append(separator);}ui.lmLocation.append(button);};
    add("全部 LoRA", () => void updateQuery({ root: "", folder: "", collection: "" }));
    if (state.collection) {add(state.collection === "__all__" ? "全部收藏" : state.collection === "__ungrouped__" ? "未分组收藏" : groupPath(state.groups.find((group) => group.id === state.collection) || { name: "收藏" }, state.groups), () => {});return;}
    if (state.root) add(state.root.split(/[\\/]/).filter(Boolean).pop() || "根目录", () => void updateQuery({ root: state.root, folder: "", collection: "" }));
    const parts = state.folder.split("/").filter(Boolean);parts.forEach((part, index) => add(part, () => void updateQuery({ folder: parts.slice(0, index + 1).join("/"), root: state.root, collection: "" })));
  }
  function setFilters(open, { restoreFocus = false } = {}) {
    ui.lmFiltersToggle.setAttribute("aria-expanded", String(open));
    if (globalThis.StudioMotion) StudioMotion.presence(ui.lmFilters, open, { response: .28, damping: 1, from: { opacity: 0, y: 5, scale: 1 } });else ui.lmFilters.hidden = !open;
    if (open) ui.lmFilters.querySelector("select,input,button")?.focus({ preventScroll: true });else if (restoreFocus) ui.lmFiltersToggle.focus({ preventScroll: true });
  }
  function queueQuery(patch) {
    if (!allowDetailLeave(() => queueQuery(patch))) return;
    closeDetail({ force: true });clearTimeout(searchTimer);controller.stage(patch);syncFilters();render();persistView();
    searchTimer = setTimeout(() => void controller.load(), 150);
  }
  function cleanPane(pane) {
    for (const property of ["position", "left", "top", "width", "height", "opacity", "transform", "margin", "clip-path", "border-radius"]) pane.style.removeProperty(property);
  }
  function expand(next) {
    next = !!next;if (expanded === next) return;
    if (!next && !allowDetailLeave(() => expand(false))) return;
    const element = ui.loraDialog,previous = expanded,source = previous ? ui.lmExpandedPane : ui.lmCompactPane,target = next ? ui.lmExpandedPane : ui.lmCompactPane;
    // The opening presence must not leave scaled measurements in the shell's
    // layout endpoints when the expand icon is pressed immediately on opening.
    if (globalThis.StudioMotion?.snapshot(element, "presence")?.active) StudioMotion.finish(element, "presence");
    const running = globalThis.StudioMotion?.snapshot(element, "library-shell"),before = running?.active ? running.value : element.getBoundingClientRect();
    const sourceButton = (previous ? ui.lmExpand : ui.lmExpandCompact).getBoundingClientRect();
    const sourceRadius = parseFloat(getComputedStyle(element).borderTopLeftRadius) || 0;
    if (previous) expandedScroll = ui.lmCatalogScroll.scrollTop;else {compactScroll = ui.lmCompactPane.querySelector(".lora-catalog")?.scrollTop || 0;compactPaneScroll = ui.lmCompactPane.scrollTop;}
    if (!running?.active) paneRects.set(previous, source.getBoundingClientRect());
    expanded = next;const revision = ++morphRevision;
    cleanPane(source);cleanPane(target);element.classList.remove("lm-morphing");element.style.removeProperty("clip-path");element.style.removeProperty("width");element.style.removeProperty("height");
    source.hidden = true;target.hidden = false;target.inert = false;source.inert = true;element.classList.toggle("lm-expanded", next);
    if (next) {ui.loraSearch.value = currentState().q;ui.lmCatalogScroll.scrollTop = expandedScroll;} else
    {document.getElementById("loraSearch").value = currentState().q;host.onCompactRestore?.(currentState().q);const catalog = ui.lmCompactPane.querySelector(".lora-catalog");if (catalog) catalog.scrollTop = compactScroll;ui.lmCompactPane.scrollTop = compactPaneScroll;setSidebar(false);setFilters(false);}
    ui.lmExpandCompact.setAttribute("aria-expanded", String(next));
    const after = element.getBoundingClientRect();paneRects.set(next, target.getBoundingClientRect());
    // Absolute descendants are positioned from the padding edge, while all
    // DOMRects use the border edge. Account for that border in every endpoint.
    const origin = { left: after.left + element.clientLeft, top: after.top + element.clientTop };
    const targetButton = (next ? ui.lmExpand : ui.lmExpandCompact).getBoundingClientRect();
    const radius = parseFloat(getComputedStyle(element).borderTopLeftRadius) || 0;
    const finish = () => {
      if (revision !== morphRevision) return;
      element.classList.remove("lm-morphing");element.style.removeProperty("clip-path");element.style.removeProperty("width");element.style.removeProperty("height");ui.lmShellSurface.removeAttribute("style");
      for (const [mode, pane] of [[false, ui.lmCompactPane], [true, ui.lmExpandedPane]]) {cleanPane(pane);pane.hidden = mode !== expanded;pane.inert = mode !== expanded;}
      ui.lmMorphToggle.hidden = true;
      if (document.activeElement === ui.lmMorphToggle || document.activeElement === document.body) (expanded ? ui.lmExpand : ui.lmExpandCompact).focus({ preventScroll: true });
    };
    if (!globalThis.StudioMotion || StudioMotion.reduced() || !before.width) {finish();return;}
    element.style.width = `${after.width}px`;element.style.height = `${after.height}px`;element.classList.add("lm-morphing");
    for (const [mode, pane] of [[false, ui.lmCompactPane], [true, ui.lmExpandedPane]]) {
      const rect = paneRects.get(mode);if (!rect) continue;pane.hidden = false;pane.inert = mode !== next;
      Object.assign(pane.style, { position: "absolute", left: `${rect.left - origin.left}px`, top: `${rect.top - origin.top}px`, width: `${rect.width}px`, height: `${rect.height}px`, margin: "0", borderRadius: "0" });
    }
    ui.lmMorphToggle.hidden = false;ui.lmMorphToggle.innerHTML = (next ? ui.lmExpand : ui.lmExpandCompact).innerHTML;ui.lmMorphToggle.setAttribute("aria-label", next ? "收起为快速添加" : "展开 LoRA 管理");ui.lmMorphToggle.focus({ preventScroll: true });
    StudioMotion.spring(element, "library-shell", { left: after.left, top: after.top, width: after.width, height: after.height, radius, buttonLeft: targetButton.left, buttonTop: targetButton.top, blend: next ? 1 : 0 }, { initial: running?.active ? undefined : { left: before.left, top: before.top, width: before.width, height: before.height, radius: sourceRadius, buttonLeft: sourceButton.left, buttonTop: sourceButton.top, blend: previous ? 1 : 0 }, response: .35, damping: 1, precision: { left: .15, top: .15, width: .15, height: .15, radius: .05, buttonLeft: .15, buttonTop: .15, blend: .001 }, onUpdate: (value) => {
        Object.assign(ui.lmShellSurface.style, { left: `${value.left - origin.left}px`, top: `${value.top - origin.top}px`, width: `${value.width}px`, height: `${value.height}px`, borderRadius: `${value.radius}px`, right: "auto", bottom: "auto" });
        // Clip only content. Clipping the dialog would cut off the glass shadow
        // during motion and make it suddenly reappear at the final frame.
        for (const [mode, pane] of [[false, ui.lmCompactPane], [true, ui.lmExpandedPane]]) {
          const rect = paneRects.get(mode);if (!rect) continue;
          pane.style.clipPath = `inset(${value.top - rect.top}px ${rect.right - value.left - value.width}px ${rect.bottom - value.top - value.height}px ${value.left - rect.left}px round ${value.radius}px)`;
        }
        ui.lmCompactPane.style.opacity = String(1 - value.blend);ui.lmExpandedPane.style.opacity = String(value.blend);
        Object.assign(ui.lmMorphToggle.style, { position: "absolute", left: `${value.buttonLeft - origin.left}px`, top: `${value.buttonTop - origin.top}px`, zIndex: "20" });
      }, onComplete: finish });
  }
  function setSidebar(open) {
    ui.lmSidebar.classList.toggle("mobile-open", open);ui.lmSidebarToggle.setAttribute("aria-expanded", String(open));
    if (matchMedia("(max-width: 1050px)").matches) {if (globalThis.StudioMotion) StudioMotion.presence(ui.lmSidebar, open, { response: .3, damping: 1, from: { opacity: 0, x: -18 } });else ui.lmSidebar.hidden = !open;} else
    {if (globalThis.StudioMotion) StudioMotion.presence(ui.lmSidebar, true, { immediate: true });ui.lmSidebar.hidden = false;ui.lmSidebar.inert = false;}
    updateLayerInert();
  }
  function updateLayerInert() {
    const sidebar = matchMedia("(max-width:1050px)").matches && ui.lmSidebar.classList.contains("mobile-open"),detail = ui.lmExpandedPane.classList.contains("lm-detail-open");
    ui.loraCatalog.closest(".lm-content").inert = sidebar || detail && matchMedia("(max-width:760px)").matches;
    ui.lmDetail.inert = !detail || sidebar;
  }
  async function open({ expanded: desired = true, query } = {}) {
    if (!ready) return;if (controller.profileChanged()) {detailDrafts.clear();closeDetail({ force: true });}
    if (query !== undefined && String(query) !== currentState().q) controller.stage({ q: String(query) });
    if (!ui.loraDialog.open) {ui.lmCompactPane.hidden = false;ui.lmExpandedPane.hidden = true;ui.loraDialog.classList.remove("lm-expanded");expanded = false;dialog(ui.loraDialog, true);}
    expand(desired);syncFilters();notice("");await controller.load({ refresh: true });
  }
  function allowDetailLeave(action) {
    if (!detailEditing || !detailItem) return true;
    captureDetailDraft();if (!detailDrafts.has(identity(detailItem)) && !restoreTriggerDraft) {detailEditing = false;return true;}
    leaveAction = action;
    let prompt = ui.lmDetailBody.querySelector("#lmLeavePrompt");
    if (!prompt) {prompt = document.createElement("section");prompt.id = "lmLeavePrompt";prompt.className = "lm-inline-confirm";prompt.setAttribute("role", "alertdialog");prompt.setAttribute("aria-label", "保存编辑后离开");prompt.innerHTML = '<p>这份资料还有未保存的编辑。</p><div class="lm-detail-quick"><button class="button primary compact" type="button" data-leave-save>保存并离开</button><button class="button ghost compact" type="button" data-leave-discard>放弃</button><button class="lm-text-button" type="button" data-leave-cancel>继续编辑</button></div>';
      prompt.querySelector("[data-leave-save]").onclick = async () => {if (await saveDetail()) {const next = leaveAction;leaveAction = null;next?.();}};
      prompt.querySelector("[data-leave-discard]").onclick = () => {detailDrafts.delete(identity(detailItem));restoreTriggerDraft = false;detailEditing = false;const next = leaveAction;leaveAction = null;prompt.remove();next?.();};
      prompt.querySelector("[data-leave-cancel]").onclick = () => {leaveAction = null;prompt.remove();ui.lmDetailBody.querySelector('[name="tags"]')?.focus({ preventScroll: true });};ui.lmDetailBody.prepend(prompt);
    }
    prompt.scrollIntoView({ block: "nearest" });prompt.querySelector("button").focus({ preventScroll: true });return false;
  }
  function closeDetail({ restoreFocus = false, force = false } = {}) {
    if (!force && !allowDetailLeave(() => closeDetail({ restoreFocus, force: true }))) return;
    const id = detailItem && identity(detailItem);++detailEpoch;detailGate.cancel();detailItem = null;detailEditing = false;restoreTriggerDraft = false;
    ui.lmExpandedPane.classList.remove("lm-detail-open");
    if (globalThis.StudioMotion) StudioMotion.presence(ui.lmDetail, false, { response: .30, damping: 1, from: { opacity: 0, x: 20 } });else ui.lmDetail.hidden = true;
    updateLayerInert();
    ui.loraCatalog.querySelectorAll(".detail-active").forEach((node) => node.classList.remove("detail-active"));
    if (restoreFocus) {const target = detailOpener?.isConnected ? detailOpener : id ? ui.loraCatalog.querySelector(`[data-lm-card="${CSS.escape(id)}"] .lm-card-open`) : ui.loraSearch;target?.focus({ preventScroll: true });}detailOpener = null;
  }
  async function openDetail(id, { force = false } = {}) {
    if (!force && !allowDetailLeave(() => void openDetail(id, { force: true }))) return;
    detailEditing = false;restoreTriggerDraft = false;detailOpener = document.activeElement;expand(true);
    const token = detailGate.next(),epoch = ++detailEpoch,profile = host.getProfile(),cached = currentState().known.get(id);
    ui.lmExpandedPane.classList.add("lm-detail-open");
    if (globalThis.StudioMotion) StudioMotion.presence(ui.lmDetail, true, { response: .30, damping: 1, from: { opacity: 0, x: 20 } });else ui.lmDetail.hidden = false;
    updateLayerInert();
    if (cached) {detailItem = cached;renderDetail(cached);} else {detailItem = null;ui.lmDetailBody.innerHTML = '<p class="lm-muted" role="status">正在读取详情…</p>';}
    ui.lmCloseDetail.focus({ preventScroll: true });
    try {
      const item = currentState().offline && cached ? { ...cached, offline: true, cached: true, writable: false } : await api(`/items/${encodeURIComponent(id)}?model_profile=${encodeURIComponent(profile)}`, "GET", undefined, { signal: token.signal });
      if (!token.current() || epoch !== detailEpoch || profile !== host.getProfile()) return;
      if (item.offline) controller.markOffline(item.error || currentState().error);
      reconcileDetailItem(item);
    } catch (error) {if (token.current() && error.name !== "AbortError") {if (cached) notice(`详情更新失败：${error.message}`);else ui.lmDetailBody.innerHTML = `<p class="field-error">${escape(error.message)}</p><button class="button ghost compact" type="button" data-lm-detail-retry="${escape(id)}">重试</button>`;}}
  }
  function detailDraft(item) {
    return mergeDetailDraft(item, detailDrafts.get(identity(item)));
  }
  function groupChecks(groups, selected, name = "groups") {return groups.map((group) => `<label class="lm-chip"><input type="checkbox" name="${name}" value="${escape(group.id)}" ${selected.includes(group.id) ? "checked" : ""}><span>${escape(groupPath(group, groups))}</span></label>`).join("");}
  function captureDetailFocus() {
    const focused = document.activeElement;if (!focused || !ui.lmDetailBody.contains(focused)) return null;
    let selector = focused.id ? `#${CSS.escape(focused.id)}` : focused.name ? `[name="${CSS.escape(focused.name)}"]` : focused.hasAttribute("data-lm-star") ? "[data-lm-star]" : focused.hasAttribute("data-lm-add") ? "[data-lm-add]" : focused.closest("#lmMetadataForm") ? '#lmMetadataForm [type="submit"]' : focused.closest("#lmTriggersForm") ? '#lmTriggersForm [type="submit"]' : null;
    if (focused.type === "checkbox") selector = `[name="${CSS.escape(focused.name)}"][value="${CSS.escape(focused.value)}"]`;
    return selector ? { selector, start: focused.selectionStart, end: focused.selectionEnd } : null;
  }
  function restoreDetailFocus(focus) {
    if (!focus) return;const target = ui.lmDetailBody.querySelector(focus.selector);if (!target || target.disabled) return;
    target.focus({ preventScroll: true });if (focus.start != null && target.setSelectionRange) try {target.setSelectionRange(focus.start, focus.end);} catch {/* Number inputs do not expose text selection. */}
  }
  function detailQuickActions(item) {
    const id = identity(item),added = configured(item);
    return `<div class="lm-detail-quick"><button class="button ${added ? "ghost" : "primary"} compact" type="button" data-lm-add="${escape(id)}" ${added || usable(item) && writable() ? "" : "disabled"}>${added ? "移出当前配置" : "加入当前配置"}</button><button class="button ghost compact" type="button" data-lm-star="${escape(id)}" aria-pressed="${!!item.favorite}" ${writable() ? "" : "disabled"}>${item.favorite ? "已收藏" : "收藏"}</button></div>`;
  }
  function renderDetail(item, { focus = captureDetailFocus() } = {}) {
    const id = identity(item),draft = detailDraft(item),preview = safeImage(item.preview),groups = currentState().groups;
    if (detailEditing) {
      ui.lmDetailBody.innerHTML = `<div class="lm-detail-heading"><div><span class="lm-muted">编辑资料</span><h3>${escape(title(item))}</h3></div></div><form id="lmMetadataForm" class="lm-detail-editor"><label class="field"><span>标签</span><input name="tags" value="${escape(draft.tags)}" placeholder="用逗号分隔"></label><label class="field"><span>备注</span><textarea name="notes" rows="3" placeholder="场景、搭配或使用心得">${escape(draft.notes)}</textarea></label><label class="field"><span>默认权重</span><input name="strength" type="number" min="-100" max="100" step="0.05" value="${escape(draft.strength)}" required><small class="lm-muted">仅用于之后添加，不改变现有配置和绑定。</small></label><fieldset class="lm-read-section"><legend>收藏分组</legend><div class="lm-read-tags">${groups.length ? groupChecks(groups, draft.groups) : '<span class="lm-muted">暂无分组，可在左侧创建。</span>'}</div></fieldset><label class="field"><span>自动触发词</span><textarea name="triggers" rows="4" placeholder="每行一个触发词">${escape(draft.triggers)}</textarea><small class="lm-muted">留空并保存会禁用自动触发词。</small></label><button id="lmRestoreTriggers" class="lm-text-button" type="button" ${item.trigger_override ? "" : "disabled"}>${restoreTriggerDraft ? "保存时恢复插件原值" : "恢复插件原值"}</button><p id="lmMetadataError" class="field-error" role="alert" hidden></p><div class="lm-edit-footer"><button id="lmCancelEdit" class="button ghost compact" type="button">取消</button><button class="button primary compact" type="submit">保存</button></div></form>`;
      ui.lmDetailBody.querySelector("#lmMetadataForm").addEventListener("submit", (event) => {event.preventDefault();void saveDetail();});
      ui.lmDetailBody.querySelector("#lmCancelEdit").onclick = () => {detailDrafts.delete(id);restoreTriggerDraft = false;detailEditing = false;renderDetail(item);ui.lmDetailBody.querySelector("#lmEditDetail")?.focus({ preventScroll: true });};
      ui.lmDetailBody.querySelector("#lmRestoreTriggers").onclick = (event) => {restoreTriggerDraft = !restoreTriggerDraft;event.currentTarget.textContent = restoreTriggerDraft ? "保存时恢复插件原值 · 取消恢复" : "恢复插件原值";event.currentTarget.setAttribute("aria-pressed", String(restoreTriggerDraft));};
    } else {
      const size = Number(item.size || 0),modified = Number(item.modified || 0),date = modified ? new Date(modified < 1e12 ? modified * 1000 : modified) : null;
      ui.lmDetailBody.innerHTML = `<div class="lm-detail-overview"><button class="lm-detail-preview" type="button" ${preview ? "" : "disabled"} aria-label="放大预览">${preview ? `<img src="${escape(preview)}" alt="${escape(title(item))}">` : icons.folder}</button><div class="lm-detail-heading"><h3 id="lmDetailTitle">${escape(title(item))}</h3><button id="lmEditDetail" class="button ghost compact" type="button" ${writable() ? "" : "disabled"}>编辑</button></div><p class="lm-detail-path" title="${escape(item.filename)}">${escape(item.filename || item.normalized_path)}</p>${detailQuickActions(item)}${!usable(item) ? '<p class="lm-muted">当前模型不可启用，仍可查看和整理。</p>' : ""}<section class="lm-read-section"><h4>标签</h4><div class="lm-read-tags">${(item.tags || []).length ? item.tags.map((tag) => `<span class="lm-read-tag">${escape(tag)}</span>`).join("") : '<span class="lm-muted">暂无标签</span>'}</div></section><section class="lm-read-section"><h4>备注</h4><p class="lm-read-value">${escape(item.notes || "暂无备注")}</p></section><section class="lm-read-section"><h4>默认权重</h4><p class="lm-read-value">${Number(item.default_strength ?? 1)} <small class="lm-muted">用于之后添加</small></p></section><section class="lm-read-section"><h4>自动触发词${item.trigger_override ? '<small class="lm-muted"> · 已自定义</small>' : ""}</h4><p class="lm-read-value">${escape((item.trigger_words || []).join(", ") || "不自动添加触发词")}</p></section><section class="lm-read-section"><h4>收藏分组</h4><div class="lm-read-tags">${(item.groupIds || []).length ? (item.groupIds || []).map((groupId) => `<span class="lm-read-tag">${escape(groupPath(groups.find((group) => group.id === groupId) || { name: "分组" }, groups))}</span>`).join("") : '<span class="lm-muted">未分组</span>'}</div></section><section class="lm-read-section"><div class="lm-detail-heading"><h4>人物绑定</h4><button id="lmBindFromDetail" class="button ghost compact" type="button" ${item.model_profile === "anima" && writable() ? "" : "disabled"}>选择人物</button></div><div class="lm-read-tags">${(item.bindings || []).map((binding) => `<button class="lm-filter-chip" type="button" data-lm-bound-character="${escape(binding.character_id)}">${escape(binding.name || binding.character_id)}${binding.enabled === false ? " · 已停用" : ""}</button>`).join("") || '<span class="lm-muted">尚未绑定人物</span>'}</div><p class="lm-muted">${item.model_profile === "anima" ? "抽中人物时自动启用，绑定可独立调整。" : "人物池绑定适用于 Anima。"}</p></section><details class="lm-resource-info"><summary>模型信息</summary><dl><dt>基础模型</dt><dd>${escape(item.base_model || "未识别")}</dd><dt>文件大小</dt><dd>${size ? `${(size / 1048576).toLocaleString(undefined, { maximumFractionDigits: 1 })} MB` : "未知"}</dd><dt>修改时间</dt><dd>${date && !Number.isNaN(date.valueOf()) ? escape(date.toLocaleString()) : "未知"}</dd><dt>根目录</dt><dd>${escape(item.root || "—")}</dd><dt>使用次数</dt><dd>${Number(item.usage_count || 0)}</dd></dl></details></div>`;
      ui.lmDetailBody.querySelector("#lmEditDetail").onclick = () => {detailEditing = true;renderDetail(detailItem);ui.lmDetailBody.querySelector('[name="tags"]')?.focus({ preventScroll: true });};
      ui.lmDetailBody.querySelector("#lmBindFromDetail").onclick = () => void openBinding({ lora: detailItem });
      const previewButton = ui.lmDetailBody.querySelector(".lm-detail-preview");
      previewButton.onclick = () => {if (preview) openPreview(preview, title(item));};
      previewButton.querySelector("img")?.addEventListener("error", () => {
        previewButton.disabled = true;previewButton.setAttribute("aria-label", "预览暂不可用");previewButton.textContent = "预览暂不可用";
      });
    }




    ui.lmDetail.dataset.readonly = String(!writable());if (!writable()) applyDetailReadonly();restoreDetailFocus(focus);
  }
  function openPreview(src, label) {
    if (!ui.lmPreviewDialog) {const element = document.createElement("dialog");element.id = "lmPreviewDialog";element.className = "lm-preview-dialog";element.innerHTML = '<button class="icon-button dark" type="button" aria-label="关闭预览">×</button><img>';document.body.append(element);element.querySelector("button").onclick = () => dialog(element, false);ui.lmPreviewDialog = element;}
    const image = ui.lmPreviewDialog.querySelector("img");image.src = src;image.alt = label;dialog(ui.lmPreviewDialog, true);
  }
  function applyDetailReadonly() {
    ui.lmDetail.dataset.readonly = "true";
    ui.lmDetailBody.querySelectorAll("#lmEditDetail,#lmBindFromDetail,[data-lm-star],input,textarea,select,#lmMetadataForm [type=submit],#lmRestoreTriggers").forEach((element) => element.disabled = true);
    ui.lmDetailBody.querySelectorAll("[data-lm-add]").forEach((element) => element.disabled = !configured(detailItem));
    if (!ui.lmDetailBody.querySelector(".lm-readonly-detail")) ui.lmDetailBody.insertAdjacentHTML("afterbegin", `<p class="lm-readonly-detail lm-muted">${currentState().offline ? "当前显示上次同步的资料；已加入项仍可移出配置。" : "本地管理数据受保护，当前仅供查看。"}</p>`);
  }
  function reconcileDetailItem(saved) {
    if (!detailEditing) {detailItem = saved;controller.replace(saved);renderDetail(saved);return;}
    captureDetailDraft();const id = identity(saved),draft = mergeDetailDraft(saved, detailDrafts.get(id));draft.dirty = detailDirtyFields(saved, draft);
    if (Object.values(draft.dirty).some(Boolean)) detailDrafts.set(id, draft);else detailDrafts.delete(id);
    detailItem = saved;ui.lmDetail.dataset.readonly = String(!writable());controller.replace(saved);
    const form = ui.lmDetailBody.querySelector("#lmMetadataForm");if (!form) {renderDetail(saved);return;}
    for (const key of ["tags", "notes", "strength", "triggers"]) {const control = form.elements[key];if (control && control.value !== String(draft[key])) control.value = draft[key];}
    form.querySelectorAll('[name="groups"]').forEach((input) => input.checked = draft.groups.includes(input.value));
    form.querySelectorAll("input,textarea,button").forEach((input) => input.disabled = !writable() || detailSaving);
    const restore = form.querySelector("#lmRestoreTriggers");if (restore) restore.disabled = !saved.trigger_override || !writable() || detailSaving;

    if (writable()) ui.lmDetailBody.querySelector(".lm-readonly-detail")?.remove();else applyDetailReadonly();
  }
  function captureDetailDraft() {
    if (!detailItem || !detailEditing) return;
    const form = ui.lmDetailBody.querySelector("#lmMetadataForm");if (!form) return;
    const value = { tags: form.elements.tags.value, notes: form.elements.notes.value, strength: form.elements.strength.value, triggers: form.elements.triggers.value, groups: [...form.querySelectorAll('[name="groups"]:checked')].map((input) => input.value) };
    value.dirty = detailDirtyFields(detailItem, value);
    if (!Object.values(value.dirty).some(Boolean)) detailDrafts.delete(identity(detailItem));else detailDrafts.set(identity(detailItem), value);
  }
  async function saveDetail() {
    if (!detailItem || detailSaving || !requireWritable()) return false;captureDetailDraft();
    const original = detailItem,id = identity(original),draft = detailDraft(original),epoch = detailEpoch,form = ui.lmDetailBody.querySelector("#lmMetadataForm"),errors = [];
    if (draft.strength === "" || !Number.isFinite(Number(draft.strength)) || Math.abs(Number(draft.strength)) > 100) {showError(form.querySelector("#lmMetadataError"), "默认权重须在 -100 到 100 之间");return false;}
    const patch = metadataPatch(original, draft),triggerChanged = restoreTriggerDraft || detailDirtyFields(original, draft).triggers;
    detailSaving = true;form.querySelectorAll("input,textarea,button").forEach((control) => control.disabled = true);
    let saved = original;
    try {
      if (Object.keys(patch).length) {try {saved = await api(`/items/${encodeURIComponent(id)}`, "PATCH", patch);} catch (error) {errors.push(`资料：${error.message}`);}}
      if (triggerChanged) {try {
          const profile = original.model_profile || host.getProfile();
          if (restoreTriggerDraft) await host.request(`/api/loras/triggers?${queryString({ filename: original.filename, model_profile: profile })}`, { method: "DELETE" });else
          await host.request("/api/loras/triggers", { method: "PUT", body: JSON.stringify({ filename: original.filename, model_profile: profile, triggerWords: words(draft.triggers) }) });
          const refreshed = await api(`/items/${encodeURIComponent(id)}?model_profile=${encodeURIComponent(host.getProfile())}`);
          saved = refreshed;draft.triggers = (saved.trigger_words || []).join("\n");restoreTriggerDraft = false;
        } catch (error) {errors.push(`触发词：${error.message}`);}}
      draft.dirty = detailDirtyFields(saved, draft);if (Object.values(draft.dirty).some(Boolean)) detailDrafts.set(id, draft);else detailDrafts.delete(id);
      controller.replace(saved);host.onInventoryChanged?.();
      if (epoch !== detailEpoch || !detailItem || identity(detailItem) !== id) return !errors.length;
      detailItem = saved;
      if (errors.length) {renderDetail(saved);showError(ui.lmDetailBody.querySelector("#lmMetadataError"), errors.join("；"));return false;}
      detailEditing = false;detailDrafts.delete(id);renderDetail(saved);notify("资料已保存");ui.lmDetailBody.querySelector("#lmEditDetail")?.focus({ preventScroll: true });return true;
    } finally {detailSaving = false;form.querySelectorAll("input,textarea,button").forEach((control) => control.disabled = false);}
  }
  async function toggleFavorite(id) {
    if (mutationBusy.has(id) || !requireWritable()) return;const item = currentState().known.get(id) || detailItem;if (!item) return;
    mutationBusy.add(id);const favorite = !item.favorite;
    try {const saved = await api(`/items/${encodeURIComponent(id)}`, "PATCH", { favorite });controller.replace(saved);
      if (detailItem && identity(detailItem) === id) reconcileDetailItem(saved);host.onInventoryChanged?.();
      if (currentState().collection) await controller.load();
      notify(favorite ? "已加入收藏" : saved.groupIds?.length ? "已取消收藏，再次收藏后恢复原分组" : "已取消收藏");
    } catch (error) {notice(error.message);} finally {mutationBusy.delete(id);}
  }
  async function addItems(ids) {
    if (selectionBusy || !requireWritable()) return;selectionBusy = true;renderSelection();const profile = host.getProfile(),items = [],failures = [];let cursor = 0;
    try {
      await Promise.all(Array.from({ length: Math.min(4, ids.length) }, async () => {while (cursor < ids.length) {const id = ids[cursor++];try {let item = currentState().known.get(id);if (!item) item = await api(`/items/${encodeURIComponent(id)}?model_profile=${encodeURIComponent(profile)}`);if (profile !== host.getProfile()) return;if (usable(item)) items.push(item);else failures.push(title(item));} catch (error) {failures.push(id);}}}));
      if (profile !== host.getProfile()) return;
      const byId = new Map(items.map((item) => [identity(item), item])),ordered = ids.map((id) => byId.get(id)).filter(Boolean);
      const result = await submitLibraryLoras(ordered, host.addLoras);
      const added = Array.isArray(result?.added) ? result.added.length : Number(result?.added ?? ordered.length),skipped = failures.length + (Array.isArray(result?.skipped) ? result.skipped.length : Number(result?.skipped || 0));
      notify(`已加入 ${added} 个 LoRA${skipped ? ` · ${skipped} 个已配置或不可用` : ""}`);render();if (detailItem && !detailEditing) renderDetail(detailItem);
    } catch (error) {notice(error.message);} finally {selectionBusy = false;renderSelection();}
  }
  async function removeItems(ids) {
    if (selectionBusy) return;
    const filenames = configuredRemoval(ids, currentState().known, host.getLoras(), currentState().selectionResources);
    if (!filenames.length) {notify("所选 LoRA 尚未加入当前配置");return;}
    try {const result = await host.removeLoras(filenames);const count = Array.isArray(result?.removed) ? result.removed.length : Number(result?.removed ?? filenames.length);notify(`已从当前配置移出 ${count} 个 LoRA`);render();if (detailItem && !detailEditing) renderDetail(detailItem);if (currentState().in_configured !== "") void controller.load();}
    catch (error) {notice(error.message);}
  }
  function toggleConfigured(id) {const item = currentState().known.get(id) || detailItem;if (item && configured(item)) void removeItems([id]);else void addItems([id]);}
  function openGroup(id = "") {
    if (!requireWritable()) return;
    const groups = currentState().groups,group = groups.find((item) => item.id === id);groupEditor = { id, revision: currentState().revision };
    ui.lmGroupTitle.textContent = group ? "编辑分组" : "新建分组";ui.lmGroupName.value = group?.name || "";
    const invalid = id ? descendants(groups, id) : new Set();ui.lmGroupParent.replaceChildren(new Option("顶层", ""), ...groups.filter((item) => !invalid.has(item.id)).map((item) => new Option(groupPath(item, groups), item.id)));
    ui.lmGroupParent.value = group?.parentId || "";ui.lmGroupDelete.hidden = !group;ui.lmGroupDeleteConfirm.hidden = true;showError(ui.lmGroupError);dialog(ui.lmGroupDialog, true);ui.lmGroupName.focus();
  }
  async function saveGroup(event) {
    event.preventDefault();const editor = groupEditor;if (!editor || !requireWritable()) return;const name = ui.lmGroupName.value.trim();if (!name) {showError(ui.lmGroupError, "请输入分组名称");return;}
    const button = ui.lmGroupForm.querySelector('[type="submit"]');button.disabled = true;
    try {await api(editor.id ? `/groups/${encodeURIComponent(editor.id)}` : "/groups", editor.id ? "PATCH" : "POST", { name, parentId: ui.lmGroupParent.value || null });if (groupEditor === editor) {dialog(ui.lmGroupDialog, false);groupEditor = null;}await controller.load();notify("分组已保存");}
    catch (error) {showError(ui.lmGroupError, error.message);} finally {button.disabled = false;}
  }
  async function deleteGroup() {
    const editor = groupEditor;if (!editor?.id || !requireWritable()) return;ui.lmGroupDeleteCommit.disabled = true;
    const removed = descendants(currentState().groups, editor.id);
    try {await api(`/groups/${encodeURIComponent(editor.id)}`, "DELETE");if (removed.has(currentState().collection)) currentState().collection = "__all__";removed.forEach((id) => collapsedGroups.delete(id));dialog(ui.lmGroupDialog, false);groupEditor = null;await controller.load();persistView();notify("分组已删除，收藏已保留");}
    catch (error) {showError(ui.lmGroupError, error.message);} finally {ui.lmGroupDeleteCommit.disabled = false;}
  }
  function openBatch(action) {
    const ids = [...currentState().selected];if (!ids.length || !requireWritable()) return;
    if (action === "favorite" || action === "unfavorite") {void startJob(ids, "favorite", { favorite: action === "favorite" });return;}
    batchEditor = { action, ids, profile: host.getProfile() };const names = { groups: "批量整理分组", tags: "批量编辑标签", strength: "批量默认权重", bind: "批量绑定人物" };
    ui.lmBatchTitle.textContent = names[action];ui.lmBatchSummary.textContent = `将应用到 ${ids.length} 个所选 LoRA，目标已固定，不受之后的筛选变化影响。`;
    const mode = '<label class="field"><span>操作方式</span><select name="mode"><option value="add">追加</option><option value="remove">移除</option><option value="replace">替换全部</option></select></label>';
    if (action === "groups") ui.lmBatchFields.innerHTML = `${mode}<div class="lm-chips">${groupChecks(currentState().groups, [])}</div><p class="lm-muted">加入分组时会同步收藏；移出分组保留星标。</p>`;
    if (action === "tags") ui.lmBatchFields.innerHTML = `${mode}<label class="field"><span>标签</span><textarea name="tags" rows="3" placeholder="用逗号或换行分隔"></textarea></label>`;
    if (action === "strength") ui.lmBatchFields.innerHTML = '<label class="field"><span>默认权重</span><input name="strength" type="number" min="-100" max="100" step="0.05" value="1" required></label><p class="lm-muted">仅影响以后添加的 LoRA，不改变当前配置。</p>';
    if (action === "bind") ui.lmBatchFields.innerHTML = '<button id="lmBatchChooseCharacter" class="button ghost compact" type="button">选择人物</button><p id="lmBatchCharacterChoice" class="lm-muted">尚未选择人物</p><label class="field"><span>新绑定权重</span><input name="strength" type="number" min="-100" max="100" step="0.05" value="1" required></label><p class="lm-muted">已有绑定的权重、顺序和启停保持不变。</p>';
    showError(ui.lmBatchError);dialog(ui.lmBatchDialog, true);
    if (action === "bind") ui.lmBatchFields.querySelector("#lmBatchChooseCharacter").onclick = async () => {const editor = batchEditor;const targets = await host.openCharacterPicker({ selectionMode: "single", title: "选择绑定人物" });if (batchEditor !== editor || !targets?.length) return;editor.character_id = targets[0].id;ui.lmBatchFields.querySelector("#lmBatchCharacterChoice").textContent = `已选：${targets[0].title_zh || targets[0].title || targets[0].name || targets[0].id}`;};
  }
  async function submitBatch(event) {
    event.preventDefault();const editor = batchEditor;if (!editor || editor.profile !== host.getProfile()) return;const form = event.currentTarget,action = editor.action;let values;
    if (action === "groups") values = { groupIds: [...form.querySelectorAll('[name="groups"]:checked')].map((input) => input.value), mode: form.elements.mode.value };
    if (action === "tags") values = { tags: words(form.elements.tags.value), mode: form.elements.mode.value };
    if (action === "strength") values = { default_strength: Number(form.elements.strength.value) };
    if (action === "bind") {if (!editor.character_id) {showError(ui.lmBatchError, "请先选择人物");return;}values = { character_id: editor.character_id, strength: Number(form.elements.strength.value) };}
    const button = form.querySelector('[type="submit"]');button.disabled = true;
    try {await startJob(editor.ids, action, values, true);dialog(ui.lmBatchDialog, false);batchEditor = null;} catch (error) {showError(ui.lmBatchError, error.message);} finally {button.disabled = false;}
  }
  async function startJob(ids, action, values, throwError = false) {
    if (!writable()) {const error = new Error("LoRA Manager 暂时离线，恢复连接并刷新后可继续修改");if (throwError) throw error;notice(error.message);return;}
    if (activeJob && ["queued", "running"].includes(activeJob.status)) {const error = new Error("请等待当前批量任务完成，或先取消剩余任务");if (throwError) throw error;notice(error.message);return;}
    try {activeJob = await api("/jobs", "POST", { ids: [...ids], action, values });renderJob();pollJob();}
    catch (error) {if (throwError) throw error;notice(error.message);}
  }
  function renderJob() {
    const job = activeJob;if (!job) {ui.lmJob.hidden = true;return;}ui.lmJob.hidden = false;
    const running = ["queued", "running"].includes(job.status);
    ui.lmJobTitle.textContent = running ? job.cancel_requested ? "正在取消剩余任务…" : "正在整理 LoRA" : job.status === "cancelled" ? "已取消剩余任务" : "整理完成";
    ui.lmJobCount.textContent = `${job.completed || 0} / ${job.total || 0}`;ui.lmJobProgress.max = Math.max(1, job.total || 1);ui.lmJobProgress.value = job.completed || 0;
    ui.lmJobError.textContent = job.failed ? `${job.failed} 项失败：${(job.results || []).find((item) => item.status === "failed")?.error || "可仅重试失败项"}` : `${job.succeeded || 0} 项成功`;
    ui.lmJobCancel.hidden = !running;ui.lmJobCancel.disabled = !!job.cancel_requested;ui.lmJobRetry.hidden = running || !job.failed;
  }
  function pollJob() {
    clearTimeout(jobTimer);const job = activeJob;if (!job) return;
    if (!["queued", "running"].includes(job.status)) {void controller.load();void refreshDetail();host.onInventoryChanged?.();return;}
    jobTimer = setTimeout(async () => {try {const next = await api(`/jobs/${encodeURIComponent(job.id)}`);if (activeJob?.id !== job.id) return;activeJob = next;renderJob();pollJob();} catch (error) {notice(`批量任务状态读取失败：${error.message}；任务可能仍在后台执行。`);jobTimer = setTimeout(pollJob, 2000);}}, 650);
  }
  async function refreshDetail() {
    if (!detailItem || ui.lmDetail.hidden) return;const id = identity(detailItem),epoch = detailEpoch,profile = host.getProfile();
    try {const saved = await api(`/items/${encodeURIComponent(id)}?model_profile=${encodeURIComponent(profile)}`);if (epoch !== detailEpoch || profile !== host.getProfile() || !detailItem || identity(detailItem) !== id) return;
      if (saved.offline) controller.markOffline(saved.error || currentState().error);
      reconcileDetailItem(saved);
    } catch (error) {if (epoch === detailEpoch) notice(`资料已更新，详情刷新失败：${error.message}`);}
  }
  function pickerButton(item, onClick) {
    const button = document.createElement("button");button.type = "button";button.className = "lm-picker-item";button.dataset.id = String(item.id);const preview = safeImage(item.preview);
    const character = !item.filename,metadata = item.metadata || {},series = item.series_zh || item.series || metadata.series_zh || metadata.series || item.source?.series || "";
    const subtitle = character ? [series, item.builtin === false ? "自定义人物池" : "内置人物池"].filter(Boolean).join(" · ") : item.folder || item.filename;
    button.title = String(item.id);
    button.innerHTML = `${preview ? `<img src="${escape(preview)}" loading="lazy" alt="">` : ""}<span><strong>${escape(item.title_zh || item.title || item.display_name || item.name || item.filename)}</strong><small>${escape(subtitle)}</small></span><span aria-hidden="true">＋</span>`;
    button.addEventListener("click", onClick);button.querySelector("img")?.addEventListener("error", (event) => event.currentTarget.remove());return button;
  }
  async function openBinding({ character, lora } = {}) {
    if (!requireWritable()) return;
    if (!character) {
      if (!lora || !host.openCharacterPicker) return;
      const resourceId = identity(lora),strength = Number(lora.default_strength ?? 1),alreadyBoundIds = (lora.bindings || []).map((binding) => binding.character_id);
      const targets = await host.openCharacterPicker({ alreadyBoundIds, title: `为 ${title(lora)} 选择人物` });
      if (targets?.length) await startJob(targets.map((item) => item.id), "bind_characters", { resource_id: resourceId, strength });return;
    }
    bindingEditor = { character, pendingLora: lora, rows: [], loading: false, page: 1, q: "" };
    ui.lmBindingTitle.textContent = "人物的 LoRA 绑定";ui.lmBindingLoraSearch.value = "";ui.lmBindingLoraResults.replaceChildren();ui.lmCharacterPicker.hidden = true;ui.lmBindingEditor.hidden = false;ui.lmChangeCharacter.hidden = true;
    ui.lmBindingSave.disabled = true;ui.lmBindingStatus.textContent = "";showError(ui.lmBindingError);dialog(ui.lmBindingDialog, true);await chooseCharacter(character);
  }
  async function searchCharacters({ more = false } = {}) {
    const editor = bindingEditor;if (!editor) return;const token = characterGate.next();if (!more) editor.page = 1;else editor.page++;
    editor.q = ui.lmCharacterSearch.value;ui.lmCharacterMore.disabled = true;
    try {const data = await api(`/characters?${queryString({ q: editor.q, page: editor.page, limit: 30 })}`, "GET", undefined, { signal: token.signal });if (!token.current() || bindingEditor !== editor) return;
      const nodes = (data.items || []).map((item) => pickerButton(item, () => chooseCharacter(item)));if (more) ui.lmCharacterResults.append(...nodes);else ui.lmCharacterResults.replaceChildren(...nodes);
      if (!nodes.length && !more) ui.lmCharacterResults.innerHTML = '<p class="lm-muted">没有匹配的人物，试试名字或系列。</p>';
      ui.lmCharacterMore.hidden = editor.page >= Number(data.pages || data.total_pages || 1);
    } catch (error) {if (token.current() && error.name !== "AbortError") showError(ui.lmBindingError, error.message);} finally {ui.lmCharacterMore.disabled = false;}
  }
  async function chooseCharacter(character) {
    const editor = bindingEditor;if (!editor) return;const token = bindingGate.next();editor.loading = true;editor.character = character;editor.rows = [];
    ui.lmCharacterPicker.hidden = true;ui.lmBindingEditor.hidden = false;ui.lmBindingCharacterName.textContent = character.title_zh || character.title || character.name || "人物";
    ui.lmBindingCharacterId.textContent = character.series_zh || character.series || character.metadata?.series_zh || character.metadata?.series || (character.builtin === false ? "自定义人物池" : "内置人物池");ui.lmBindingCharacterId.title = character.id;
    ui.lmBindingRows.innerHTML = '<p class="lm-muted">正在读取绑定…</p>';ui.lmBindingSave.disabled = true;showError(ui.lmBindingError);
    try {const data = await api(`/bindings/${encodeURIComponent(character.id)}`, "GET", undefined, { signal: token.signal });if (!token.current() || bindingEditor !== editor) return;
      editor.rows = (data.loras || []).map((row) => ({ ...row }));editor.originalStatuses = new Map(editor.rows.map((row) => [row.id, row.status]));editor.characterMissing = !!data.character_missing;editor.loading = false;
      if (editor.characterMissing) showError(ui.lmBindingError, "原人物已从池中移除，可停用或移除旧绑定。");
      if (editor.pendingLora && !editor.rows.some((row) => row.id === identity(editor.pendingLora))) {const item = editor.pendingLora;editor.rows.push({ id: identity(item), filename: item.filename, name: title(item), strength: Number(item.default_strength ?? 1), enabled: true });}
      editor.pendingLora = null;renderBindings();if (document.activeElement === document.body || document.activeElement === document.documentElement) (ui.lmBindingRows.querySelector("input") || ui.lmBindingLoraSearch).focus({ preventScroll: true });await searchBindingLoras();
    } catch (error) {if (token.current() && error.name !== "AbortError") {editor.loading = false;showError(ui.lmBindingError, `无法读取原绑定，未允许覆盖保存：${error.message}`);ui.lmBindingRows.innerHTML = '<p class="lm-muted">可更换人物后重试。</p>';}}
  }
  function renderBindings() {
    const editor = bindingEditor;if (!editor?.character) return;
    ui.lmBindingRows.replaceChildren(...editor.rows.map((row, index) => {const element = document.createElement("div");element.className = "lm-binding-row";element.dataset.bindingIndex = index;
      const status = row.rebind ? "已重新选择 · 保存后更新文件绑定" : row.status === "missing" ? "文件已移动或缺失 · 请移除后重新绑定" : row.status === "changed" ? "文件内容已更改 · 请移除后重新绑定" : row.available === false || row.status && row.status !== "recognized" ? "文件当前不可用" : "抽到人物时自动启用";
      element.innerHTML = `<input type="checkbox" data-binding-enabled ${row.enabled !== false ? "checked" : ""} aria-label="启用 ${escape(row.name || row.filename)}"><span><strong title="${escape(row.filename)}">${escape(row.name || row.filename || row.id)}</strong><small>${status}</small></span><input type="number" data-binding-strength min="-100" max="100" step="0.05" value="${escape(row.strength ?? 1)}" aria-label="绑定权重"><button type="button" data-binding-move="-1" aria-label="上移" ${index ? "" : "disabled"}>↑</button><button type="button" data-binding-move="1" aria-label="下移" ${index < editor.rows.length - 1 ? "" : "disabled"}>↓</button><button type="button" data-binding-remove aria-label="移除绑定">×</button>`;return element;}));
    if (!editor.rows.length) ui.lmBindingRows.innerHTML = '<p class="lm-muted">还没有绑定。在下方选择 LoRA，或直接保存以清空绑定。</p>';
    ui.lmBindingSave.disabled = editor.loading;ui.lmBindingStatus.textContent = `${editor.rows.length} 个绑定 · ${editor.rows.filter((row) => row.enabled !== false).length} 个启用`;
  }
  async function searchBindingLoras() {
    const editor = bindingEditor;if (!editor?.character || editor.loading || editor.characterMissing) {ui.lmBindingLoraResults.replaceChildren();return;}const token = bindingLoraGate.next();
    try {const data = await api(`?${queryString({ q: ui.lmBindingLoraSearch.value, model_profile: "anima", page: 1, limit: 30 })}`, "GET", undefined, { signal: token.signal });if (!token.current() || bindingEditor !== editor) return;
      const items = (data.items || []).filter((item) => !editor.rows.some((row) => row.id === identity(item)));
      ui.lmBindingLoraResults.replaceChildren(...items.map((item) => pickerButton(item, () => {if (bindingEditor !== editor || editor.rows.some((row) => row.id === identity(item))) return;editor.rows.push({ id: identity(item), filename: item.filename, name: title(item), strength: Number(item.default_strength ?? 1), enabled: true, ...(editor.originalStatuses?.get(identity(item)) === "changed" ? { rebind: true } : {}) });renderBindings();ui.lmBindingRows.lastElementChild?.querySelector("[data-binding-strength]")?.focus({ preventScroll: true });void searchBindingLoras();})));
      if (!items.length) ui.lmBindingLoraResults.innerHTML = '<p class="lm-muted">没有其他匹配的 Anima LoRA。</p>';
    } catch (error) {if (token.current() && error.name !== "AbortError") showError(ui.lmBindingError, error.message);}
  }
  async function saveBindings() {
    const editor = bindingEditor;if (!editor?.character || editor.loading || !requireWritable()) return;
    let loras;try {loras = bindingPayload(editor.rows);} catch (error) {showError(ui.lmBindingError, error.message);return;}
    editor.loading = true;ui.lmBindingSave.disabled = true;showError(ui.lmBindingError);
    try {await api(`/bindings/${encodeURIComponent(editor.character.id)}`, "PUT", { loras });
      if (bindingEditor === editor) {dialog(ui.lmBindingDialog, false);bindingEditor = null;}notify("人物绑定已保存");await controller.load();if (detailItem) await openDetail(identity(detailItem));host.onBindingsChanged?.();
    } catch (error) {if (bindingEditor === editor) showError(ui.lmBindingError, error.message);} finally {editor.loading = false;if (bindingEditor === editor) ui.lmBindingSave.disabled = false;}
  }
  function treeKeydown(event) {
    const row = event.target.closest(".lm-tree-row");if (!row || event.target !== row) return;const rows = [...row.parentElement.querySelectorAll('.lm-tree-row')],index = rows.indexOf(row);let target;
    if (event.key === "ArrowDown") target = rows[Math.min(index + 1, rows.length - 1)];else if (event.key === "ArrowUp") target = rows[Math.max(index - 1, 0)];else if (event.key === "Home") target = rows[0];else if (event.key === "End") target = rows.at(-1);else
    if (event.key === "Enter" || event.key === " ") {row.querySelector(".lm-tree-label").click();event.preventDefault();return;} else
    if (event.key === "F2" && row.dataset.treeKind === "group") {event.preventDefault();openGroup(row.dataset.treeId);return;} else
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {const open = row.getAttribute("aria-expanded");if (open !== null && (event.key === "ArrowRight" ? open === "false" : open === "true")) row.querySelector(".lm-tree-disclosure").click();else if (event.key === "ArrowRight") target = rows[index + 1];else {const depth = Number(row.getAttribute("aria-level"));target = rows.slice(0, index).reverse().find((item) => Number(item.getAttribute("aria-level")) < depth);}event.preventDefault();}
    if (target) {event.preventDefault();rows.forEach((item) => item.tabIndex = item === target ? 0 : -1);target.focus();}
  }
  function initialize(eventsHost) {
    if (ready) return;host = eventsHost;ui = Object.fromEntries(elements.map((id) => [id, document.getElementById(id)]));if (!ui.loraDialog) return;
    for (const [oldId, newId] of Object.entries({ loraSearch: "lmSearch", loraCatalog: "lmCatalog", loraCatalogEmpty: "lmCatalogEmpty", loraCatalogMeta: "lmCatalogMeta", closeLora: "lmClose", cancelLora: "lmDone" })) ui[oldId] = document.getElementById(newId);
    ui.lmMorphToggle = document.createElement("button");ui.lmMorphToggle.type = "button";ui.lmMorphToggle.className = "icon-button dark lm-morph-toggle";ui.lmMorphToggle.hidden = true;ui.loraDialog.append(ui.lmMorphToggle);ui.lmMorphToggle.onclick = () => expand(!expanded);
    controller = createController({ request: host.request, getProfile: host.getProfile, getLoras: host.getLoras, onChange: render });
    try {const saved = JSON.parse(localStorage.getItem(VIEW_KEY) || "{}");view = saved.view === "list" ? "list" : "grid";
      for (const key of ["scope", "collection", "folder", "root", "recursive", "sort", "order"]) if (saved[key] !== undefined) controller.state[key] = saved[key];
      (saved.collapsedGroups || []).forEach((id) => collapsedGroups.add(id));(saved.collapsedFolders || []).forEach((id) => collapsedFolders.add(id));
    } catch {/* Start with the library defaults. */}
    ready = true;syncFilters();
    ui.lmExpand.addEventListener("click", () => expand(false));
    ui.lmExpandCompact.addEventListener("click", () => void open({ expanded: true, query: document.getElementById("loraSearch").value }));
    const closeLibrary = () => {if (allowDetailLeave(closeLibrary)) dialog(ui.loraDialog, false);};
    ui.closeLora.addEventListener("click", closeLibrary);ui.cancelLora.addEventListener("click", closeLibrary);
    ui.loraSearch.addEventListener("input", () => queueQuery({ q: ui.loraSearch.value }));
    ui.lmRefresh.addEventListener("click", () => {notice("");void controller.load({ refresh: true });host.onInventoryChanged?.();});
    ui.lmFiltersToggle.addEventListener("click", () => setFilters(ui.lmFiltersToggle.getAttribute("aria-expanded") !== "true"));
    ui.lmFilterTagSearch.addEventListener("input", renderTagOptions);
    ui.loraDialog.addEventListener("pointerdown", (event) => {if (!ui.lmFilters.hidden && !ui.lmFilters.contains(event.target) && !ui.lmFiltersToggle.contains(event.target)) setFilters(false);});
    ui.lmSidebarToggle.addEventListener("click", () => setSidebar(!ui.lmSidebar.classList.contains("mobile-open")));
    const filters = { lmProfile: "scope", lmPreviewFilter: "has_preview", lmBoundFilter: "bound", lmTagMode: "tag_mode", lmConfiguredFilter: "in_configured", lmBaseModelFilter: "base_model", lmSort: "sort" };
    for (const [id, key] of Object.entries(filters)) ui[id].addEventListener("change", () => queueQuery({ [key]: ui[id].value }));
    for (const [id, key] of [["lmTagFilter", "tag"], ["lmExcludeTagFilter", "tag_exclude"]]) ui[id].addEventListener("change", () => void updateQuery({ [key]: [...ui[id].selectedOptions].map((option) => option.value) }));
    ui.lmSortOrder.addEventListener("click", () => {void updateQuery({ order: currentState().order === "asc" ? "desc" : "asc" });syncFilters();});
    const reset = () => {notice("");void updateQuery({ q: "", folder: "", root: "", collection: "", tag: [], tag_exclude: [], tag_mode: "all", base_model: "", in_configured: "", has_preview: "", bound: "", scope: "current" });syncFilters();};
    ui.lmClearFilters.addEventListener("click", reset);ui.lmEmptyReset.addEventListener("click", reset);
    ui.lmRecursive.addEventListener("change", () => void updateQuery({ recursive: ui.lmRecursive.checked }));
    ui.lmGridView.addEventListener("click", () => {view = "grid";syncFilters();render();persistView();});ui.lmListView.addEventListener("click", () => {view = "list";syncFilters();render();persistView();});
    ui.lmSelectToggle.addEventListener("click", () => {selectionMode = !selectionMode;syncFilters();render();});
    ui.lmSelectPage.addEventListener("click", () => controller.selectPage());ui.lmClearSelection.addEventListener("click", () => controller.clearSelection());
    ui.lmSelectAll.addEventListener("click", () => controller.selectAll().catch((error) => notice(error.message)));
    ui.lmBatchAdd.addEventListener("click", () => void addItems([...currentState().selected]));
    ui.lmBatchRemove.addEventListener("click", () => void removeItems([...currentState().selected]));
    ui.lmPrev.addEventListener("click", () => {void controller.update({ page: currentState().page - 1 });ui.lmCatalogScroll.scrollTop = 0;});ui.lmNext.addEventListener("click", () => {void controller.update({ page: currentState().page + 1 });ui.lmCatalogScroll.scrollTop = 0;});
    ui.lmCloseDetail.addEventListener("click", () => {captureDetailDraft();closeDetail({ restoreFocus: true });});
    ui.lmDetailBody.addEventListener("input", captureDetailDraft);ui.lmDetailBody.addEventListener("change", captureDetailDraft);
    ui.lmNewGroup.addEventListener("click", () => openGroup());ui.lmGroupForm.addEventListener("submit", saveGroup);ui.lmGroupDelete.addEventListener("click", () => {ui.lmGroupDeleteConfirm.hidden = false;});ui.lmGroupDeleteCancel.addEventListener("click", () => {ui.lmGroupDeleteConfirm.hidden = true;});ui.lmGroupDeleteCommit.addEventListener("click", deleteGroup);
    ui.lmBatchForm.addEventListener("submit", submitBatch);
    ui.lmJobCancel.addEventListener("click", async () => {if (!activeJob) return;try {activeJob = await api(`/jobs/${encodeURIComponent(activeJob.id)}/cancel`, "POST", {});renderJob();pollJob();} catch (error) {notice(error.message);}});
    ui.lmJobRetry.addEventListener("click", async () => {if (!activeJob) return;try {activeJob = await api(`/jobs/${encodeURIComponent(activeJob.id)}/retry`, "POST", {});renderJob();pollJob();} catch (error) {notice(error.message);}});
    ui.lmCharacterSearch.addEventListener("input", () => {clearTimeout(characterTimer);characterGate.cancel();characterTimer = setTimeout(() => searchCharacters(), 150);});
    ui.lmCharacterMore.addEventListener("click", () => searchCharacters({ more: true }));
    ui.lmChangeCharacter.addEventListener("click", () => {bindingGate.cancel();ui.lmCharacterPicker.hidden = false;ui.lmBindingEditor.hidden = true;ui.lmBindingSave.disabled = true;ui.lmCharacterSearch.focus({ preventScroll: true });void searchCharacters();});
    ui.lmBindingLoraSearch.addEventListener("input", () => {clearTimeout(bindingLoraTimer);bindingLoraGate.cancel();bindingLoraTimer = setTimeout(searchBindingLoras, 150);});
    ui.lmBindingRows.addEventListener("input", (event) => {const index = Number(event.target.closest("[data-binding-index]")?.dataset.bindingIndex),row = bindingEditor?.rows[index];if (!row) return;if (event.target.matches("[data-binding-strength]")) row.strength = event.target.value;});
    ui.lmBindingRows.addEventListener("change", (event) => {const index = Number(event.target.closest("[data-binding-index]")?.dataset.bindingIndex),row = bindingEditor?.rows[index];if (!row) return;if (event.target.matches("[data-binding-enabled]")) {row.enabled = event.target.checked;ui.lmBindingStatus.textContent = `${bindingEditor.rows.length} 个绑定 · ${bindingEditor.rows.filter((item) => item.enabled !== false).length} 个启用`;}});
    ui.lmBindingRows.addEventListener("click", (event) => {const button = event.target.closest("button"),index = Number(button?.closest("[data-binding-index]")?.dataset.bindingIndex);if (!button || !bindingEditor?.rows[index]) return;const rows = bindingEditor.rows;
      if (button.hasAttribute("data-binding-remove")) rows.splice(index, 1);else {const next = index + Number(button.dataset.bindingMove);if (next >= 0 && next < rows.length) [rows[index], rows[next]] = [rows[next], rows[index]];}renderBindings();void searchBindingLoras();});
    ui.lmBindingSave.addEventListener("click", saveBindings);
    for (const element of [ui.lmGroupDialog, ui.lmBatchDialog, ui.lmBindingDialog]) element.addEventListener("click", (event) => {const close = event.target.closest("[data-lm-close]");if (close) dialog(document.getElementById(close.dataset.lmClose), false);});
    ui.loraDialog.addEventListener("click", (event) => {
      const button = event.target.closest("button");if (!button) return;
      if (button.dataset.lmOpen) {if (selectionMode) controller.toggle(button.dataset.lmOpen);else if (!expanded && writable()) void addItems([button.dataset.lmOpen]);else void openDetail(button.dataset.lmOpen);}
      if (button.dataset.lmAdd) toggleConfigured(button.dataset.lmAdd);if (button.dataset.lmStar) void toggleFavorite(button.dataset.lmStar);
      if (button.dataset.lmDetailRetry) void openDetail(button.dataset.lmDetailRetry);
      if (button.hasAttribute("data-lm-collection")) {void updateQuery({ collection: button.dataset.lmCollection, folder: "", root: "" });setSidebar(false);}
      if (button.dataset.lmTreeToggle) {const set = button.dataset.kind === "group" ? collapsedGroups : collapsedFolders,id = button.dataset.lmTreeToggle;set.has(id) ? set.delete(id) : set.add(id);renderSidebar();persistView();}
      if (button.dataset.lmTreeSelect) {if (button.dataset.kind === "group") void updateQuery({ collection: button.dataset.lmTreeSelect, folder: "", root: "" });else {const folder = (ui.lmFolders._rows || []).find((row) => row.id === button.dataset.lmTreeSelect);if (folder) void updateQuery({ folder: folder.path, root: folder.root, collection: "" });}setSidebar(false);}
      if (button.dataset.lmGroupEdit) openGroup(button.dataset.lmGroupEdit);if (button.dataset.lmBatch) openBatch(button.dataset.lmBatch);
      if (button.dataset.lmBoundCharacter) {const binding = detailItem?.bindings?.find((item) => item.character_id === button.dataset.lmBoundCharacter);void openBinding({ character: { id: button.dataset.lmBoundCharacter, title: binding?.name } });}
    });
    ui.lmSidebar.addEventListener("keydown", treeKeydown);
    ui.loraCatalog.addEventListener("keydown", (event) => {
      if (!event.target.matches(".lm-card-open") || event.altKey || event.ctrlKey || event.metaKey || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
      const buttons = [...ui.loraCatalog.querySelectorAll(".lm-card-open")],index = buttons.indexOf(event.target);
      if (index < 0) return;const tracks = getComputedStyle(ui.loraCatalog).gridTemplateColumns.trim(),columns = tracks && tracks !== "none" ? tracks.split(/\s+/).length : 1;
      const next = buttons[cardNavigationIndex(index, buttons.length, columns, event.key)];event.preventDefault();
      next?.focus({ preventScroll: true });next?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "auto" });
    });
    const layerBack = (event) => {
      if (!expanded) return false;
      event.preventDefault();event.stopPropagation();
      if (ui.lmFiltersToggle.getAttribute("aria-expanded") === "true") setFilters(false, { restoreFocus: true });else
      if (ui.lmSidebar.classList.contains("mobile-open")) {setSidebar(false);ui.lmSidebarToggle.focus({ preventScroll: true });} else
      if (ui.lmExpandedPane.classList.contains("lm-detail-open")) closeDetail({ restoreFocus: true });else
      expand(false);return true;
    };
    ui.loraDialog.addEventListener("keydown", (event) => {
      if (!expanded) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {event.preventDefault();ui.loraSearch.focus();ui.loraSearch.select();}
      if (event.key === "Escape") layerBack(event);
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a" && selectionMode && !event.target.matches("input,textarea,select")) {event.preventDefault();void controller.selectAll().catch((error) => notice(error.message));}
    });
    ui.loraDialog.addEventListener("cancel", layerBack, { capture: true });
    ui.loraDialog.addEventListener("click", (event) => {if (event.target === ui.loraDialog && detailEditing && !allowDetailLeave(closeLibrary)) {event.preventDefault();event.stopImmediatePropagation();}}, { capture: true });
    ui.loraDialog.addEventListener("close", () => {
      if (ui.loraDialog.open) return;clearTimeout(searchTimer);controller.cancel();closeDetail({ force: true });setSidebar(false);setFilters(false);persistView();
      ++morphRevision;globalThis.StudioMotion?.freeze(ui.loraDialog, "library-shell");expanded = false;
      ui.loraDialog.classList.remove("lm-expanded", "lm-morphing");for (const name of ["clip-path", "width", "height"]) ui.loraDialog.style.removeProperty(name);ui.lmShellSurface.removeAttribute("style");ui.lmMorphToggle.hidden = true;
      for (const pane of [ui.lmCompactPane, ui.lmExpandedPane]) cleanPane(pane);ui.lmCompactPane.hidden = false;ui.lmCompactPane.inert = false;ui.lmExpandedPane.hidden = true;ui.lmExpandedPane.inert = true;
      host.onCompactRestore?.(currentState().q);
    });
    globalThis.addEventListener("resize", () => {if (ui.loraDialog.classList.contains("lm-morphing")) globalThis.StudioMotion?.finish(ui.loraDialog, "library-shell");setSidebar(ui.lmSidebar.classList.contains("mobile-open"));});
    ui.lmBindingDialog.addEventListener("close", () => {characterGate.cancel();bindingGate.cancel();bindingLoraGate.cancel();clearTimeout(characterTimer);clearTimeout(bindingLoraTimer);bindingEditor = null;});
    ui.lmBatchDialog.addEventListener("close", () => {batchEditor = null;});
    globalThis.addEventListener?.("focus", () => {if (ui.loraDialog.open && expanded && !currentState().loading) void controller.load({ refresh: true });});
  }
  globalThis.LoraManager = Object.freeze({ init: initialize, get ready() {return ready;}, open,
    openCharacter: (character) => ready ? openBinding({ character }) : Promise.resolve(),
    refresh: () => ready && ui.loraDialog.open && expanded ? controller.load() : Promise.resolve(),
    refreshConfigured() {if (!ready) return;render();if (detailItem && !detailEditing) renderDetail(detailItem);if (expanded && currentState().in_configured !== "" && !currentState().loading && currentState().resultKey !== JSON.stringify(controller.query())) void controller.load();},
    profileChanged() {if (!ready) return;controller.profileChanged();detailDrafts.clear();closeDetail({ force: true });if (ui.lmBindingDialog.open) dialog(ui.lmBindingDialog, false);if (ui.lmBatchDialog.open) dialog(ui.lmBatchDialog, false);if (ui.loraDialog.open) void controller.load();}
  });
})();
