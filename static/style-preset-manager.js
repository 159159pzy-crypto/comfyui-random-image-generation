/* Style library. Preset content, editor sessions and committed writes have separate owners. */
(() => {
  "use strict";
  const INCLUDE = Object.freeze({ size: false, repair: false });
  const includeLabels = { size: "尺寸", repair: "放大修复" };
  const copy = value => JSON.parse(JSON.stringify(value));
  const uniqueWords = value => {
    const seen = new Set();
    return String(value || "").split(/[,，\n]/).map(word => word.trim()).filter(word => {
      const key = word.toLocaleLowerCase();
      if (!word || seen.has(key)) return false;
      seen.add(key); return true;
    });
  };
  const normalizedInclude = value => Object.fromEntries(Object.keys(INCLUDE).map(key => [key, value?.[key] === true]));
  const normalizedView = value => value === "list" ? "list" : "cards";
  function savedView() { try { return normalizedView(globalThis.localStorage?.getItem("anima-random-webui:style-preset-view:v1")); } catch { return "cards"; } }
  function editorDraft(mode, item) {
    return { name: item?.name || "", favorite: !!item?.favorite, groups: [...item?.groups || []], tags: (item?.tags || []).join(", "), cover_id: item?.cover_id || null,
      include: mode === "create" ? { ...INCLUDE } : normalizedInclude(item?.include) };
  }
  function filterItems(items, view, groups = []) {
    const terms = String(view.query || "").trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const selectedTags = (view.tags || []).map(tag => tag.toLocaleLowerCase());
    const names = new Map(groups.map(group => [group.id, group.name]));
    const filtered = items.filter(item => {
      if (view.collection === "favorite" && !item.favorite) return false;
      if (view.collection === "ungrouped" && item.groups?.length) return false;
      if (view.collection?.startsWith("group:") && (!item.favorite || !item.groups?.includes(view.collection.slice(6)))) return false;
      const tags = (item.tags || []).map(tag => tag.toLocaleLowerCase());
      if (!selectedTags.every(tag => tags.includes(tag))) return false;
      const settings = item.settings || {};
      const haystack = [item.name, ...item.tags || [], ...(item.groups || []).map(id => names.get(id) || ""),
        settings.model_name, settings.clip_name, settings.vae_name, settings.model_profile, settings.sampler_name, settings.scheduler,
        ...(settings.loras || []).map(lora => lora.filename || lora.name || lora.lora_name || "")].join(" ").toLocaleLowerCase();
      return terms.every(term => haystack.includes(term));
    });
    return filtered.sort((a, b) => {
      // The API puts favorites first for the homepage. Never inherit that order
      // in the library, even when two records have the same sort value.
      const byId = a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
      if (view.sort === "name") return a.name.localeCompare(b.name, "zh-CN", { numeric: true }) || byId;
      if (view.sort === "created") return String(b.created_at).localeCompare(String(a.created_at)) || byId;
      return String(b.updated_at).localeCompare(String(a.updated_at)) || a.name.localeCompare(b.name, "zh-CN") || byId;
    });
  }
  function editorPayload(session) {
    const draft = session.draft;
    const payload = { model_profile: session.profile, name: draft.name.trim(), favorite: !!draft.favorite,
      groups: [...draft.groups], tags: uniqueWords(draft.tags), cover_id: draft.cover_id || null };
    if (session.mode !== "edit") {
      payload.settings = copy(session.snapshot);
      delete payload.settings.resource_replacements;
      payload.include = normalizedInclude(draft.include);
    }
    return payload;
  }
  function cropRectangle(width, height, zoom = 1, cx = .5, cy = .5) {
    const ratio = width / height, target = 4 / 3, safeZoom = Math.max(1, Math.min(4, zoom));
    const w = Math.min(1, target / ratio) / safeZoom, h = Math.min(1, ratio / target) / safeZoom;
    const x = Math.max(0, Math.min(1 - w, cx - w / 2));
    const y = Math.max(0, Math.min(1 - h, cy - h / 2));
    return { x, y, width: w, height: h };
  }
  // Uploads are preparation: a dismissed editor must not start a later preset write.
  // Once write() starts, it is never aborted by navigation, and committed() always runs.
  async function saveSession(session, services) {
    if (session.busy) return { ignored: true };
    session.busy = true;
    const payload = editorPayload(session), file = session.file, crop = copy(session.crop);
    try {
      if (file) {
        const cover = await services.upload(file, crop);
        if (!services.current(session)) return { abandoned: true };
        payload.cover_id = cover.id;
      }
      if (!services.current(session)) return { abandoned: true };
      const result = await services.write(payload);
      if (services.current(session)) services.success(result);
      await services.committed(result);
      return { committed: true, result };
    } catch (error) {
      if (services.current(session)) services.failure(error);
      else services.detachedFailure?.(error);
      return { error };
    } finally { session.busy = false; services.settled?.(session); }
  }
  const helpers = { INCLUDE, uniqueWords, normalizedInclude, normalizedView, editorDraft, filterItems, editorPayload, cropRectangle, cropPresentation, tagInputState, saveSession };
  if (typeof module !== "undefined" && module.exports) module.exports = helpers;
  if (typeof document === "undefined") return;

  const state = { epoch: 0, profile: "", items: [], groups: [], query: "", collection: "all", tags: [], sort: "updated",
    selected: new Set(), selecting: false, read: 0, abort: null, busy: new Set(), pendingFavorites: new Map(), batchWrites: new Map(), editor: null, small: null, confirmation: null, loading: false, readError: "", view: savedView(), applying: new Map(), applyErrors: new Map(), filtersOpen: false, managerVisible: false };
  const ui = {};
  const profile = () => typeof currentProfile === "function" ? currentProfile() : "anima";
  const profileText = () => "Anima";
  const notify = message => { if (typeof toast === "function") toast(message); };
  const writable = () => !(typeof config !== "undefined" && config.storage_status?.style_presets?.read_only);
  const h = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
  const button = (text, className, action, label) => { const node = h("button", className, text); node.type = "button"; if (label) node.setAttribute("aria-label", label); if (action) node.addEventListener("click", action); return node; };
  const icon = (name) => {
    const paths = { close: '<path d="m6 6 12 12M18 6 6 18"/>', star: '<path d="m12 3 2.8 5.8 6.4.9-4.6 4.5 1.1 6.3-5.7-3-5.7 3 1.1-6.3-4.6-4.5 6.4-.9Z"/>',
      image: '<rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>', plus: '<path d="M12 5v14M5 12h14"/>', edit: '<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-4-4L5 15Z"/>', search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>', cards: '<rect x="3" y="4" width="7" height="6" rx="1.5"/><rect x="14" y="4" width="7" height="6" rx="1.5"/><rect x="3" y="14" width="7" height="6" rx="1.5"/><rect x="14" y="14" width="7" height="6" rx="1.5"/>', list: '<path d="M9 5h12M9 12h12M9 19h12M3 5h1M3 12h1M3 19h1"/>' };
    const node = h("span", "spm-icon"); node.setAttribute("aria-hidden", "true");
    node.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.image}</svg>`;
    return node;
  };
  const dialogReturns = new WeakMap(), dialogRuns = new WeakMap(), dialogFocusIntents = new WeakMap(), dialogOrder = [];
  function flushDialogFocus() {
    const top = [...dialogOrder].reverse().find(node => node.open && globalThis.StudioMotion?.target(node) !== false);
    const intent = top && dialogFocusIntents.get(top);
    if (!intent || intent.run !== dialogRuns.get(top)) return;
    const target = intent.node?.isConnected && !intent.node.disabled ? intent.node : top.querySelector(".spm-heading > .spm-icon-button");
    target?.focus({ preventScroll: true });
    // A still-exiting native modal may keep this layer inert. Retry when that
    // modal actually closes instead of losing the newer session's focus intent.
    if (document.activeElement === target) dialogFocusIntents.delete(top);
  }
  function syncDialogLayers() {
    const opened = dialogOrder.filter(node => node.open);
    const external = [...document.querySelectorAll("dialog[open]")].some(node => !node.classList.contains("spm-dialog"));
    opened.forEach((node, index) => {
      node.dataset.spmLayer = index || external ? "nested" : "base";
      node.classList.toggle("is-covered", index < opened.length - 1);
    });
    for (const node of dialogOrder) if (!node.open) node.classList.remove("is-covered");
  }
  function rememberDialogReturn() {
    const node = document.activeElement, parent = node?.closest("dialog"), card = node?.closest(".spm-card");
    const scope = parent || document;
    const cards = [...scope.querySelectorAll(".spm-card:not([hidden])")];
    const index = cards.indexOf(card);
    const scroll = parent?.querySelector(".spm-scroll, .spm-editor-body, .spm-small-body");
    return { node, parent, parentRun: parent && dialogRuns.get(parent), key: node?.dataset?.focusKey, id: node?.id,
      neighbors: index < 0 ? [] : [...cards.slice(index + 1), ...cards.slice(0, index).reverse()].map(item => item.dataset.presetId),
      scroll, top: scroll?.scrollTop };
  }
  function restoreDialogReturn(saved) {
    if (!saved || (saved.parent && (!saved.parent.open || saved.parentRun !== dialogRuns.get(saved.parent)))) return;
    const top = [...dialogOrder].reverse().find(node => node.open);
    // An older close must not steal focus from a newly opened child or editor.
    if (top && top !== saved.parent) return;
    const scope = saved.parent || document;
    const usable = node => node?.isConnected && !node.disabled && !node.closest("[hidden], [inert]") && node.getClientRects().length;
    let target = usable(saved.node) ? saved.node : null;
    if (!target && saved.key) target = [...scope.querySelectorAll("[data-focus-key]")].find(node => node.dataset.focusKey === saved.key && usable(node));
    if (!target && saved.id) { const node = document.getElementById(saved.id); if (scope.contains(node) && usable(node)) target = node; }
    if (!target) {
      const cards = [...scope.querySelectorAll(".spm-card:not([hidden])")];
      const neighbor = saved.neighbors.map(id => cards.find(node => node.dataset.presetId === id)).find(Boolean);
      target = neighbor?.querySelector(".spm-card-open") || scope.querySelector("#stylePresetSearch, #stylePresetName");
    }
    if (usable(target)) {
      if (saved.scroll?.isConnected) saved.scroll.scrollTop = saved.top;
      target.focus({ preventScroll: true });
    }
  }
  function show(dialog, open, options = {}) {
    const run = (dialogRuns.get(dialog) || 0) + 1; dialogRuns.set(dialog, run);
    if (open) {
      if (!dialog.open) dialogReturns.set(dialog, rememberDialogReturn());
      if (options.initialFocus) dialogFocusIntents.set(dialog, { run, node: options.initialFocus });
      const index = dialogOrder.indexOf(dialog); if (index >= 0) dialogOrder.splice(index, 1);
      dialogOrder.push(dialog);
      dialog.dataset.spmLayer = [...document.querySelectorAll("dialog[open]")].some(node => node !== dialog) ? "nested" : "base";
      const heading = dialog.querySelector(".spm-heading h2"), close = dialog.querySelector(".spm-heading > .spm-icon-button");
      if (heading && close) close.setAttribute("aria-label", `关闭${heading.textContent}`);
    } else dialogFocusIntents.delete(dialog);
    const complete = () => {
      if (dialogRuns.get(dialog) !== run) return;
      syncDialogLayers();
      if (!open && options.restoreFocus !== false) restoreDialogReturn(dialogReturns.get(dialog));
      options.onComplete?.();
      flushDialogFocus();
    };
    if (globalThis.StudioMotion) {
      const result = StudioMotion.dialog(dialog, open, { response: .32, damping: 1, ...options, onComplete: complete });
      syncDialogLayers(); flushDialogFocus(); return result;
    }
    if (open && !dialog.open) dialog.showModal(); else if (!open && dialog.open) dialog.close();
    complete();
  }
  function makeDialog(id, className, title, onClose) {
    const dialog = h("dialog", `spm-dialog ${className}`); dialog.id = id;
    dialog.setAttribute("aria-labelledby", `${id}Title`);
    const heading = h("header", "spm-heading"), text = h("div", "spm-heading-copy");
    const name = h("h2", "", title); name.id = `${id}Title`; text.append(name);
    const close = button("", "spm-icon-button", onClose, `关闭${title}`); close.append(icon("close"));
    close.dataset.focusKey = `close:${id}`;
    heading.append(text, close); dialog.append(heading); document.body.append(dialog);
    dialog.addEventListener("close", () => { if (!dialog.open) { syncDialogLayers(); flushDialogFocus(); } });
    let outsideStart = false;
    const outside = event => { const rect = dialog.getBoundingClientRect(); return event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom); };
    dialog.addEventListener("cancel", event => { event.preventDefault(); event.stopImmediatePropagation(); onClose(); }, true);
    dialog.addEventListener("pointerdown", event => { outsideStart = outside(event); }, true);
    dialog.addEventListener("click", event => { if (outsideStart && outside(event)) { event.preventDefault(); event.stopImmediatePropagation(); onClose(); } outsideStart = false; }, true);
    return { dialog, heading, title: name, copy: text };
  }
  function error(node, message = "") { node.textContent = message; node.hidden = !message; }
  function ensure() {
    if (ui.manager) return;
    const shell = makeDialog("stylePresetManager", "spm-manager", "风格预设", closeManager);
    ui.manager = shell.dialog; ui.managerHeading = shell.heading;
    ui.subtitle = h("p", "spm-subtitle"); shell.copy.append(ui.subtitle);
    const createButton = button("新建预设", "button primary compact", () => create()); createButton.id = "stylePresetNew"; ui.newButton = createButton;
    shell.heading.insertBefore(createButton, shell.heading.lastChild);
    const toolbar = h("div", "spm-toolbar"), search = h("label", "spm-search");
    ui.search = h("input"); ui.search.id = "stylePresetSearch"; ui.search.type = "search"; ui.search.placeholder = "查找名称、标签、模型或 LoRA"; ui.search.setAttribute("aria-label", "查找风格预设");
    ui.search.addEventListener("input", () => { state.query = ui.search.value; renderGrid(); }); search.append(icon("search"), ui.search);
    ui.sort = h("select"); ui.sort.id = "stylePresetSort"; ui.sort.setAttribute("aria-label", "预设排序");
    for (const [value, text] of [["updated", "最近更新"], ["created", "最近创建"], ["name", "名称"]]) ui.sort.append(new Option(text, value));
    ui.sort.addEventListener("change", () => { state.sort = ui.sort.value; renderGrid(); });
    ui.selectToggle = button("选择", "button ghost compact", () => { state.selecting = !state.selecting; state.selected.clear(); renderGrid(); }); ui.selectToggle.id = "stylePresetSelect";
    ui.filterToggle = button("筛选", "button ghost compact spm-mobile-filters", () => { state.filtersOpen = !state.filtersOpen; syncMobileFilters(); });
    ui.filterToggle.id = "stylePresetFiltersToggle"; ui.filterToggle.setAttribute("aria-controls", "stylePresetFilters"); ui.filterToggle.setAttribute("aria-expanded", "false");
    ui.viewToggle = h("div", "spm-view-toggle"); ui.viewToggle.setAttribute("role", "group"); ui.viewToggle.setAttribute("aria-label", "预设显示方式");
    ui.viewButtons = new Map();
    for (const [view, label] of [["cards", "紧凑卡片"], ["list", "列表"]]) {
      const control = button("", "spm-view-button", event => {
        if (state.view === view) return;
        const viewport = ui.scroll.getBoundingClientRect();
        const anchor = [...ui.grid.children].find(node => node.classList.contains("spm-card") && node.getBoundingClientRect().bottom > viewport.top);
        const offset = anchor ? anchor.getBoundingClientRect().top - viewport.top : 0;
        const opacity = ui.viewAnimation?.playState === "running" ? Number(getComputedStyle(ui.grid).opacity) : .65;
        ui.viewAnimation?.cancel();
        state.view = view; try { globalThis.localStorage?.setItem("anima-random-webui:style-preset-view:v1", view); } catch { /* The view remains usable when storage is blocked. */ }
        renderGrid();
        if (anchor?.isConnected) ui.scroll.scrollTop += anchor.getBoundingClientRect().top - ui.scroll.getBoundingClientRect().top - offset;
        if (event.detail && !globalThis.StudioMotion?.reduced() && ui.grid.animate) {
          ui.viewAnimation = ui.grid.animate([{ opacity }, { opacity: 1 }], { duration: 160, easing: "cubic-bezier(.23,1,.32,1)" });
        }
      }, label); control.title = label; control.append(icon(view)); control.dataset.view = view; ui.viewButtons.set(view, control); ui.viewToggle.append(control);
    }
    toolbar.append(search, ui.sort, ui.viewToggle, ui.filterToggle, ui.selectToggle); ui.manager.append(toolbar);
    const body = h("div", "spm-library"); ui.sidebar = h("nav", "spm-sidebar"); ui.sidebar.id = "stylePresetFilters"; ui.sidebar.setAttribute("aria-label", "预设收藏与标签");
    const content = h("section", "spm-content"); ui.notice = h("div", "spm-error"); ui.notice.setAttribute("role", "status"); ui.notice.hidden = true;
    ui.readNotice = h("div", "spm-read-notice"); ui.readNotice.hidden = true; ui.readStatus = h("p"); ui.readStatus.setAttribute("role", "status");
    ui.retryRead = button("重新读取", "spm-text-button", () => void refresh()); ui.retryRead.dataset.focusKey = "read:retry"; ui.readNotice.append(ui.readStatus, ui.retryRead);
    ui.results = h("p", "spm-results"); ui.results.setAttribute("aria-live", "polite");
    ui.grid = h("div", "spm-grid"); ui.grid.id = "stylePresetGrid"; ui.cards = new Map();
    ui.scroll = h("div", "spm-scroll"); ui.scroll.append(ui.grid); content.append(ui.notice, ui.readNotice, ui.results, ui.scroll); body.append(ui.sidebar, content); ui.manager.append(body);
    ui.batchbar = h("footer", "spm-batchbar"); ui.batchbar.hidden = true; ui.manager.append(ui.batchbar);
    const editor = makeDialog("stylePresetEditor", "spm-editor", "新建风格预设", () => void closeEditor());
    ui.editor = editor.dialog; ui.editorTitle = editor.title; ui.editorBody = h("div", "spm-editor-body"); ui.editor.append(ui.editorBody);
    ui.editorError = h("p", "spm-error"); ui.editorError.setAttribute("role", "status"); ui.editorError.hidden = true;
    ui.editorSave = button("保存预设", "button primary", () => void submitEditor()); ui.editorSave.id = "stylePresetSave";
    const editorFooter = h("footer", "spm-footer"); editorFooter.append(ui.editorError, button("取消", "button ghost", () => void closeEditor()), ui.editorSave); ui.editor.append(editorFooter);
    ui.editor.addEventListener("keydown", event => { if (!event.isComposing && (event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); void submitEditor(); } });
    const small = makeDialog("stylePresetOrganize", "spm-small", "整理预设", () => void closeSmall()); ui.small = small.dialog; ui.smallTitle = small.title;
    ui.smallBody = h("div", "spm-small-body"); ui.smallError = h("p", "spm-error"); ui.smallError.setAttribute("role", "status"); ui.smallError.hidden = true;
    ui.smallSave = button("保存", "button primary", () => void submitSmall()); ui.smallSave.id = "stylePresetOrganizeSave";
    const smallFooter = h("footer", "spm-footer"); smallFooter.append(ui.smallError, button("取消", "button ghost", () => void closeSmall()), ui.smallSave); ui.small.append(ui.smallBody, smallFooter);
    ui.small.addEventListener("keydown", event => { if (!event.isComposing && (event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); void submitSmall(); } });
    const confirmShell = makeDialog("stylePresetConfirm", "spm-confirm", "确认操作", () => settleConfirm(false)); ui.confirm = confirmShell.dialog; ui.confirmTitle = confirmShell.title;
    ui.confirmMessage = h("p", "spm-confirm-message"); ui.confirmAccept = button("确认", "button primary", () => settleConfirm(true)); ui.confirmAccept.id = "stylePresetConfirmAccept";
    ui.confirmCancel = button("取消", "button ghost", () => settleConfirm(false)); const confirmFooter = h("footer", "spm-footer"); confirmFooter.append(ui.confirmCancel, ui.confirmAccept); ui.confirm.append(ui.confirmMessage, confirmFooter);
    ui.mobileQuery = matchMedia("(max-width: 720px)"); ui.mobileQuery.addEventListener("change", () => syncMobileFilters(true)); syncMobileFilters(true);
    ui.manager.addEventListener("pointerdown", event => {
      if (ui.mobileQuery.matches && state.filtersOpen && !ui.sidebar.contains(event.target) && !ui.filterToggle.contains(event.target)) { state.filtersOpen = false; syncMobileFilters(); }
    });
  }
  function syncMobileFilters(immediate = false) {
    if (!ui.sidebar) return;
    const visible = !ui.mobileQuery.matches || state.filtersOpen;
    ui.filterToggle.setAttribute("aria-expanded", String(ui.mobileQuery.matches && state.filtersOpen));
    if (globalThis.StudioMotion) StudioMotion.presence(ui.sidebar, visible, { from: { opacity: 0, y: -6, scale: .985 }, response: .28, damping: 1, immediate: immediate || !ui.mobileQuery.matches });
    else { ui.sidebar.hidden = !visible; ui.sidebar.inert = !visible; }
  }
  function confirmAction({ title = "确认操作", message = "", confirmLabel = "确认", danger = false } = {}) {
    ensure(); if (state.confirmation) settleConfirm(false, true);
    ui.confirmTitle.textContent = title; ui.confirmMessage.textContent = message; ui.confirmAccept.textContent = confirmLabel;
    ui.confirmAccept.className = `button ${danger ? "danger" : "primary"}`;
    return new Promise(resolve => { state.confirmation = { resolve }; show(ui.confirm, true, { initialFocus: ui.confirmCancel }); });
  }
  function settleConfirm(value, immediate = false) { const current = state.confirmation; state.confirmation = null; if (ui.confirm) show(ui.confirm, false, { immediate }); current?.resolve(value); }
  function closeManager() { if (!ui.manager) return; state.managerVisible = false; state.read++; state.abort?.abort(); show(ui.manager, false); }
  function currentSession(session) { return state.editor === session && state.epoch === session.epoch && profile() === session.profile; }
  function currentSmall(session) { return state.small === session && state.epoch === session.epoch && profile() === session.profile; }
  function contextChanged() {
    state.epoch++; state.read++; state.abort?.abort(); state.profile = profile(); state.managerVisible = false;
    state.items = []; state.groups = []; state.selected.clear(); state.selecting = false; state.collection = "all"; state.tags = []; state.query = ""; state.filtersOpen = false;
    state.loading = false; state.readError = ""; state.applying.clear(); state.applyErrors.clear();
    discardEditor(true); discardSmall(true); settleConfirm(false, true);
    if (ui.manager) { show(ui.manager, false, { immediate: true, restoreFocus: false }); ui.search.value = ""; syncMobileFilters(true); render(); }
  }
  async function refresh() {
    if (!ui.manager || (!ui.manager.open && !ui.editor.open && !ui.small.open)) return;
    const requestedProfile = profile();
    if (state.profile && state.profile !== requestedProfile) { contextChanged(); return; }
    state.profile = requestedProfile; const epoch = state.epoch, serial = ++state.read;
    state.abort?.abort(); state.abort = new AbortController(); state.loading = true; state.readError = "";
    renderGrid(); error(ui.notice); renderProtection();
    try {
      const data = await request(`/api/style-presets?model_profile=${encodeURIComponent(requestedProfile)}`, { signal: state.abort.signal });
      if (serial !== state.read || epoch !== state.epoch || requestedProfile !== profile()) return;
      // A parallel read can see a write before its PUT response arrives. Keep
      // that record's list position until the write itself is acknowledged.
      const existing = new Map(state.items.map(item => [item.id, item]));
      state.items = (data.items || []).map(item => {
        const pending = state.pendingFavorites.get(item.id);
        return pending && !pending.committed && pending.epoch === epoch && pending.profile === requestedProfile ? existing.get(item.id) || item : item;
      });
      state.groups = data.groups || []; state.loading = false; state.readError = "";
      const ids = new Set(state.items.map(item => item.id)); state.selected = new Set([...state.selected].filter(id => ids.has(id)));
      if (state.collection.startsWith("group:") && !state.groups.some(group => group.id === state.collection.slice(6))) state.collection = "favorite";
      render();
      if (state.editor) { state.editor.draft.groups = state.editor.draft.groups.filter(id => state.groups.some(group => group.id === id)); renderEditorGroups(state.editor); }
    } catch (failure) {
      if (serial !== state.read || epoch !== state.epoch || failure.name === "AbortError") return;
      state.loading = false; state.readError = failure.message || "请检查连接后重试。"; renderGrid();
    }
  }
  async function open() {
    ensure();
    if (state.editor) { await closeEditor(); if (state.editor) return; }
    if (state.small) { await closeSmall(); if (state.small) return; }
    if (state.profile !== profile()) contextChanged(); state.profile = profile();
    if (!ui.manager.open || globalThis.StudioMotion?.target(ui.manager) === false) { state.epoch++; state.applying.clear(); state.applyErrors.clear(); }
    state.managerVisible = true; show(ui.manager, true, { initialFocus: ui.search }); await refresh();
  }
  function render(options) { renderSidebar(); renderGrid(options); renderProtection(); }
  function renderProtection() {
    if (!ui.manager) return;
    const protectedStore = !writable(); ui.newButton.disabled = protectedStore;
    if (protectedStore) error(ui.notice, `风格预设目前为只读，可继续查看和应用。${typeof config !== "undefined" ? config.storage_status?.style_presets?.reason || "" : ""}`);
    ui.sidebar.querySelectorAll(".spm-sidebar-heading button, .spm-group-edit").forEach(node => { node.disabled = protectedStore; });
    if (state.editor) setEditorBusy(state.editor);
    if (state.small) setSmallBusy(state.small);
  }
  function renderSidebar() {
    if (!ui.sidebar) return;
    const focused = ui.sidebar.contains(document.activeElement) ? document.activeElement : null;
    if (!ui.collections) {
      ui.collections = new Map(); ui.groupRows = new Map(); ui.tagButtons = new Map();
      for (const [id, name] of [["all", "全部预设"], ["favorite", "已收藏"], ["ungrouped", "未分组"]]) {
        const node = button("", "spm-nav", () => { state.collection = id; render(); }); node.dataset.collection = id; node.dataset.focusKey = `collection:${id}`;
        const count = h("small"); node.append(h("span", "", name), count); ui.collections.set(id, { node, count }); ui.sidebar.append(node);
      }
      const heading = h("div", "spm-sidebar-heading"), createGroup = button("＋", "spm-text-button", () => openGroup(), "新建收藏分组");
      createGroup.dataset.focusKey = "group:new"; heading.append(h("strong", "", "收藏分组"), createGroup);
      ui.groupList = h("div", "spm-group-list"); ui.noGroups = h("p", "spm-muted", "创建分组，整理常用风格。");
      ui.tagsHeading = h("div", "spm-sidebar-heading"); ui.clearTags = button("清除", "spm-text-button", () => { state.tags = []; render(); }); ui.clearTags.dataset.focusKey = "tag:clear";
      ui.tagsHeading.append(h("strong", "", "标签"), ui.clearTags); ui.tagList = h("div", "spm-tags");
      ui.sidebar.append(heading, ui.groupList, ui.noGroups, ui.tagsHeading, ui.tagList);
    }
    const counts = { all: state.items.length, favorite: state.items.filter(item => item.favorite).length, ungrouped: filterItems(state.items, { collection: "ungrouped" }).length };
    for (const [id, entry] of ui.collections) { entry.node.setAttribute("aria-current", state.collection === id ? "page" : "false"); setText(entry.count, String(counts[id])); }
    const groupNodes = [];
    for (const group of state.groups) {
      let entry = ui.groupRows.get(group.id);
      if (!entry) {
        entry = { row: h("div", "spm-group-row") };
        entry.nav = button("", "spm-nav", () => { state.collection = `group:${entry.group.id}`; render(); }); entry.nav.dataset.focusKey = `group:view:${group.id}`;
        entry.edit = button("", "spm-icon-button spm-group-edit", () => openGroup(entry.group)); entry.edit.dataset.focusKey = `group:edit:${group.id}`; entry.edit.append(icon("edit"));
        entry.row.append(entry.nav, entry.edit); ui.groupRows.set(group.id, entry);
      }
      entry.group = group; setText(entry.nav, group.name); entry.nav.title = group.name; entry.nav.setAttribute("aria-current", state.collection === `group:${group.id}` ? "page" : "false");
      entry.edit.setAttribute("aria-label", `编辑分组 ${group.name}`); groupNodes.push(entry.row);
    }
    reconcileChildren(ui.groupList, groupNodes); ui.noGroups.hidden = !!state.groups.length;
    for (const [id] of ui.groupRows) if (!state.groups.some(group => group.id === id)) ui.groupRows.delete(id);
    const tags = [...new Set(state.items.flatMap(item => item.tags || []))].sort((a, b) => a.localeCompare(b, "zh-CN"));
    ui.tagsHeading.hidden = ui.tagList.hidden = !tags.length; ui.clearTags.hidden = !state.tags.length;
    const tagNodes = [];
    for (const tag of tags) {
      let node = ui.tagButtons.get(tag);
      if (!node) { node = button(tag, "spm-tag", () => { state.tags = state.tags.includes(tag) ? state.tags.filter(value => value !== tag) : [...state.tags, tag]; render(); }); node.dataset.focusKey = `tag:${tag}`; ui.tagButtons.set(tag, node); }
      node.setAttribute("aria-pressed", String(state.tags.includes(tag))); tagNodes.push(node);
    }
    reconcileChildren(ui.tagList, tagNodes);
    for (const [tag] of ui.tagButtons) if (!tags.includes(tag)) ui.tagButtons.delete(tag);
    if (focused && (document.activeElement !== focused || !focused.isConnected || focused.closest("[hidden]"))) {
      (focused.isConnected && !focused.closest("[hidden]") ? focused : ui.collections.get("all").node).focus({ preventScroll: true });
    }
  }
  function setText(node, value) { if (node.textContent !== value) node.textContent = value; }
  function reconcileChildren(parent, nodes) {
    const wanted = new Set(nodes);
    for (const child of [...parent.children]) if (!wanted.has(child)) child.remove();
    let cursor = parent.firstElementChild;
    for (const node of nodes) {
      if (node === cursor) cursor = cursor.nextElementSibling;
      else parent.insertBefore(node, cursor);
    }
  }
  function summary(item) {
    const settings = item.settings || {}, loras = (settings.loras || []).filter(lora => lora.enabled !== false);
    const parts = [settings.steps ? `${settings.steps} 步` : "", settings.cfg !== undefined ? `CFG ${settings.cfg}` : "", loras.length ? `${loras.length} LoRA` : ""].filter(Boolean);
    return parts.join(" · ") || "风格参数";
  }
  function imageNode(item, className) {
    const wrap = h("div", className);
    if (item.cover_url && /^\/api\/style-preset-covers\//.test(item.cover_url)) {
      const image = h("img"); image.src = item.cover_url; image.alt = ""; image.loading = "lazy"; image.decoding = "async";
      image.addEventListener("error", () => { image.remove(); wrap.append(icon("image")); }, { once: true }); wrap.append(image);
    } else wrap.append(icon("image"));
    return wrap;
  }
  function makeCard(item) {
    const entry = { item, card: h("article", "spm-card"), coverKey: undefined };
    entry.card.dataset.presetId = item.id;
    entry.primary = button("", "spm-card-open", () => {
      if (state.selecting) { state.selected.has(entry.item.id) ? state.selected.delete(entry.item.id) : state.selected.add(entry.item.id); renderGrid(); }
      else edit(entry.item);
    }); entry.primary.dataset.focusKey = `open:${item.id}`;
    entry.copy = h("span", "spm-card-copy"); entry.name = h("strong", "spm-card-name"); entry.summary = h("span", "spm-card-summary");
    entry.includes = h("small", "spm-card-includes"); entry.tags = h("span", "spm-card-tags"); entry.tagNodes = new Map(); entry.copy.append(entry.name, entry.summary, entry.includes, entry.tags); entry.primary.append(entry.copy);
    entry.actions = h("div", "spm-card-actions");
    entry.star = button("", "spm-icon-button spm-star", event => void toggleFavorite(entry.item, event)); entry.star.append(icon("star")); entry.star.dataset.focusKey = `star:${item.id}`;
    entry.apply = button("应用", "spm-apply", () => void applyCard(entry)); entry.apply.dataset.focusKey = `apply:${item.id}`;
    entry.actions.append(entry.star, entry.apply); entry.selected = h("span", "spm-selection-indicator"); entry.selected.setAttribute("aria-hidden", "true");
    entry.failure = h("p", "spm-apply-error"); entry.failure.setAttribute("role", "status");
    entry.card.append(entry.primary, entry.actions, entry.selected, entry.failure); return entry;
  }
  function updateCard(entry, item) {
    entry.item = item;
    const selected = state.selected.has(item.id), applying = state.applying.has(item.id);
    const pending = state.pendingFavorites.get(item.id);
    const favorite = pending && !pending.committed && pending.epoch === state.epoch && pending.profile === state.profile ? pending.favorite : !!item.favorite;
    const caption = summary(item), failure = state.applyErrors.get(item.id) || "";
    const presentation = JSON.stringify([item.name, favorite, item.cover_url, item.include, item.tags,
      caption, selected, state.selecting, applying, state.busy.has(item.id), writable(), failure]);
    // Filtering only changes membership/order. Leave unchanged cards and their
    // accessibility trees alone, including when a server refresh returns new objects.
    if (entry.presentation === presentation) return;
    entry.presentation = presentation;
    entry.card.classList.toggle("is-selected", selected); entry.card.classList.toggle("is-applying", applying);
    entry.primary.setAttribute("aria-label", `${state.selecting ? "选择" : "查看"} ${item.name}`); entry.primary.title = item.name;
    if (state.selecting) entry.primary.setAttribute("aria-pressed", String(selected)); else entry.primary.removeAttribute("aria-pressed");
    const coverKey = item.cover_url || "";
    if (entry.coverKey !== coverKey) {
      entry.cover?.remove(); entry.cover = imageNode(item, "spm-card-cover"); entry.primary.insertBefore(entry.cover, entry.copy); entry.coverKey = coverKey;
    }
    setText(entry.name, item.name); setText(entry.summary, caption);
    const labels = Object.entries(normalizedInclude(item.include)).filter(([, yes]) => yes).map(([key]) => includeLabels[key]);
    setText(entry.includes, labels.length ? labels.join("、") : "仅风格 · 其余使用当前设置");
    const tags = item.tags || [], visibleTags = tags.slice(0, 3), nodes = [];
    for (const tag of visibleTags) {
      let node = entry.tagNodes.get(tag); if (!node) { node = h("span", "spm-card-tag", tag); entry.tagNodes.set(tag, node); } nodes.push(node);
    }
    if (tags.length > visibleTags.length) { entry.moreTags ||= h("span", "spm-card-tag"); setText(entry.moreTags, `+${tags.length - visibleTags.length}`); entry.moreTags.title = tags.slice(3).join("、"); nodes.push(entry.moreTags); }
    reconcileChildren(entry.tags, nodes); entry.tags.hidden = !tags.length;
    for (const [tag] of entry.tagNodes) if (!visibleTags.includes(tag)) entry.tagNodes.delete(tag);
    entry.actions.hidden = state.selecting; entry.selected.hidden = !state.selecting; setText(entry.selected, selected ? "✓" : "");
    entry.star.setAttribute("aria-label", `${favorite ? "取消收藏" : "收藏"} ${item.name}`); entry.star.setAttribute("aria-pressed", String(favorite));
    // Keep a saving star focusable; disabling the native button blurs it before
    // the committed refresh can restore its focus. The handler is guarded.
    entry.star.disabled = !writable();
    entry.star.setAttribute("aria-disabled", String(state.busy.has(item.id) || !writable()));
    entry.star.setAttribute("aria-busy", String(state.busy.has(item.id)));
    setText(entry.apply, applying ? "应用中…" : "应用"); entry.apply.disabled = applying; entry.apply.setAttribute("aria-busy", String(applying));
    error(entry.failure, failure);
  }
  async function applyCard(entry) {
    const item = entry.item, epoch = state.epoch, forProfile = state.profile;
    if (state.applying.has(item.id) || typeof applyStylePreset !== "function") return;
    const operation = {}; state.applying.set(item.id, operation); state.applyErrors.delete(item.id); updateCard(entry, item);
    const isCurrent = () => state.managerVisible && state.epoch === epoch && forProfile === profile();
    try {
      const applied = await applyStylePreset(item, { isCurrent, onError: failure => { if (isCurrent()) state.applyErrors.set(item.id, `应用失败，当前设置未更改。${failure.message}`); } });
      if (applied !== false && isCurrent()) closeManager();
    } catch (failure) { if (isCurrent()) state.applyErrors.set(item.id, `应用失败，当前设置未更改。${failure.message}`); }
    finally {
      if (state.applying.get(item.id) === operation) state.applying.delete(item.id);
      const current = forProfile === profile() ? ui.cards.get(item.id) : null;
      if (current) updateCard(current, current.item);
    }
  }
  function renderGrid({ animate = false } = {}) {
    if (!ui.grid) return;
    const items = filterItems(state.items, state, state.groups), focused = document.activeElement, hadFocus = ui.grid.contains(focused);
    const previousNodes = [...ui.grid.children], previousIndex = previousNodes.indexOf(focused?.closest?.(".spm-card"));
    const scrollTop = ui.scroll.scrollTop, positions = new Map(), velocities = new Map();
    if (animate && state.managerVisible && !globalThis.StudioMotion?.reduced()) {
      const viewport = ui.scroll.getBoundingClientRect();
      for (const node of previousNodes) { const rect = node.getBoundingClientRect(); if (rect.bottom > viewport.top && rect.top < viewport.bottom) positions.set(node, rect); }
    }
    const nextNodes = items.map(item => { let entry = ui.cards.get(item.id); if (!entry) { entry = makeCard(item); ui.cards.set(item.id, entry); } updateCard(entry, item); return entry.card; });
    const changedOrder = previousNodes.length !== nextNodes.length || previousNodes.some((node, index) => node !== nextNodes[index]);
    if (changedOrder || ui.grid.dataset.view !== state.view) {
      for (const entry of ui.cards.values()) {
        const previous = globalThis.StudioMotion?.freeze(entry.card, "spm-reorder");
        if (previous?.active) velocities.set(entry.card, previous.velocity);
        entry.card.style.removeProperty("transform");
      }
    }
    ui.grid.dataset.view = state.view; for (const [view, control] of ui.viewButtons) control.setAttribute("aria-pressed", String(state.view === view));
    setText(ui.subtitle, profileText(state.profile || profile()));
    setText(ui.results, state.loading && !state.items.length ? "正在读取预设…" : `${items.length} 个预设${state.tags.length > 1 ? " · 同时匹配所选标签" : ""}${state.loading ? " · 正在刷新…" : ""}`);
    setText(ui.selectToggle, state.selecting ? "完成选择" : "选择"); ui.selectToggle.setAttribute("aria-pressed", String(state.selecting));
    ui.grid.setAttribute("aria-busy", String(state.loading));
    ui.readNotice.hidden = !state.readError;
    setText(ui.readStatus, state.readError ? `${state.items.length ? "暂时无法刷新，已保留当前列表。" : "暂时无法读取预设。"}${state.readError}` : "");
    ui.retryRead.hidden = !state.readError; ui.retryRead.disabled = state.loading;
    if (!items.length && (!state.readError || state.items.length)) {
      if (!ui.empty) {
        ui.empty = h("div", "spm-empty"); ui.emptyTitle = h("h3"); ui.emptyText = h("p");
        ui.emptyAction = button("", "button secondary compact", () => {
          if (!state.items.length) { create(); return; } state.query = ""; state.tags = []; state.collection = "all"; ui.search.value = ""; render(); ui.search.focus({ preventScroll: true });
        }); ui.empty.append(icon("image"), ui.emptyTitle, ui.emptyText, ui.emptyAction);
      }
      setText(ui.emptyTitle, state.loading ? "正在读取预设" : state.items.length ? "没有匹配的预设" : "保存你的第一种风格");
      setText(ui.emptyText, state.loading ? "" : state.items.length ? "试试其他关键词，或清除筛选。" : "保留风格参数，应用时继续使用当前画面描述。");
      ui.emptyAction.hidden = state.loading; ui.emptyAction.disabled = !state.items.length && !writable(); setText(ui.emptyAction, state.items.length ? "清除筛选" : "新建预设"); nextNodes.push(ui.empty);
    }
    reconcileChildren(ui.grid, nextNodes); ui.scroll.scrollTop = scrollTop;
    const ids = new Set(state.items.map(item => item.id));
    for (const [id, entry] of ui.cards) if (!ids.has(id)) { globalThis.StudioMotion?.freeze(entry.card, "spm-reorder"); ui.cards.delete(id); state.applyErrors.delete(id); }
    if (changedOrder && positions.size && globalThis.StudioMotion) {
      for (const node of nextNodes) {
        const before = positions.get(node); if (!before) continue; const after = node.getBoundingClientRect(), x = before.left - after.left, y = before.top - after.top;
        if (Math.abs(x) < .5 && Math.abs(y) < .5) continue;
        StudioMotion.spring(node, "spm-reorder", { x: 0, y: 0 }, { initial: { x, y }, velocity: velocities.get(node), response: .22, damping: 1, precision: .25,
          onUpdate: value => { node.style.transform = `translate3d(${value.x}px, ${value.y}px, 0)`; }, onComplete: () => node.style.removeProperty("transform") });
      }
    }
    if (hadFocus && (document.activeElement !== focused || !focused.isConnected || !ui.grid.contains(focused) || focused.closest("[hidden]"))) {
      const nearby = items[Math.min(Math.max(previousIndex, 0), items.length - 1)];
      const canRestore = focused.isConnected && ui.grid.contains(focused) && !focused.disabled && !focused.closest("[hidden]");
      (canRestore ? focused : nearby ? ui.cards.get(nearby.id).primary : ui.search).focus({ preventScroll: true });
    }
    renderBatchbar(items);
  }
  function renderBatchbar(items) {
    if (!ui.batchCount) {
      ui.batchCount = h("strong"); ui.batchCount.setAttribute("aria-live", "polite");
      ui.selectAll = button("选择当前结果", "spm-text-button", () => { filterItems(state.items, state, state.groups).forEach(item => state.selected.add(item.id)); renderGrid(); }); ui.selectAll.dataset.focusKey = "batch:all";
      ui.clearSelection = button("清空", "spm-text-button", () => { state.selected.clear(); renderGrid(); }); ui.clearSelection.dataset.focusKey = "batch:clear";
      ui.batchbar.append(ui.batchCount, ui.selectAll, ui.clearSelection); ui.batchActions = new Map();
      for (const [action, label] of [["favorite", "收藏"], ["unfavorite", "取消收藏"], ["groups", "整理分组"], ["tags", "编辑标签"]]) {
        const node = button(label, "button ghost compact", () => openBatch(action)); node.dataset.batchAction = action; node.dataset.focusKey = `batch:${action}`; ui.batchActions.set(action, node); ui.batchbar.append(node);
      }
    }
    const appearing = ui.batchbar.hidden && state.selecting;
    if (!state.selecting && !ui.batchbar.hidden && globalThis.StudioMotion) StudioMotion.presence(ui.batchbar, false, { immediate: true });
    else ui.batchbar.hidden = !state.selecting;
    setText(ui.batchCount, `已选择 ${state.selected.size} 项`); ui.selectAll.disabled = !items.length;
    for (const node of ui.batchActions.values()) node.disabled = !state.selected.size || !writable() || state.batchWrites.has(profile());
    if (appearing && globalThis.StudioMotion) StudioMotion.presence(ui.batchbar, true, { from: { opacity: 0, y: 4 }, response: .2, damping: 1 });
  }
  async function changed(forProfile, message) {
    notify(`${profileText(forProfile)}：${message}`);
    if (forProfile !== profile()) return;
    const epoch = state.epoch;
    try { await Promise.all([typeof loadStylePresets === "function" ? loadStylePresets({ refreshManager: false }) : Promise.resolve(), refresh()]); }
    catch (failure) { if (ui.notice && state.epoch === epoch && forProfile === profile()) error(ui.notice, `更改已保存，列表刷新失败：${failure.message}`); }
  }
  async function toggleFavorite(item, event) {
    if (state.busy.has(item.id) || !writable()) return; const forProfile = state.profile, epoch = state.epoch, old = !!item.favorite;
    const operation = { profile: forProfile, epoch, favorite: !old, committed: false };
    const isCurrent = () => state.pendingFavorites.get(item.id) === operation && epoch === state.epoch && forProfile === profile();
    state.read++; state.abort?.abort(); state.loading = false;
    state.pendingFavorites.set(item.id, operation); state.busy.add(item.id); render();
    try {
      const saved = await request(`/api/style-presets/${encodeURIComponent(item.id)}`, { method: "PUT", body: JSON.stringify({ model_profile: forProfile, favorite: !old }) });
      operation.committed = true;
      if (isCurrent()) {
        // Invalidate reads started during the PUT before merging the committed
        // record. Older snapshots must not restore its previous favorite state.
        state.read++; state.abort?.abort(); state.loading = false;
        state.items = state.items.map(current => current.id === item.id ? saved : current);
        render({ animate: !!event?.detail });
      }
      await changed(forProfile, !old ? "已加入收藏" : "已取消收藏");
    } catch (failure) {
      if (isCurrent()) { state.read++; state.abort?.abort(); state.loading = false; error(ui.notice, failure.message); }
      else notify(`${profileText(forProfile)}：${failure.message}`);
    } finally {
      if (state.pendingFavorites.get(item.id) === operation) { state.pendingFavorites.delete(item.id); state.busy.delete(item.id); }
      if (forProfile === profile()) render();
    }
  }
  function field(label, control, hint) {
    const node = h("div", "spm-field"), input = control.spmInput || control, caption = h("label", "", label);
    if (!input.id) input.id = `stylePresetField${++field.serial}`;
    caption.htmlFor = input.id; node.append(caption, control);
    const described = [];
    if (hint) { const help = h("small", "spm-muted", hint); help.id = `${input.id}Hint`; described.push(help.id); node.append(help); }
    const issue = h("small", "spm-field-error"); issue.id = `${input.id}Error`; issue.hidden = true; issue.setAttribute("aria-live", "polite"); node.append(issue);
    input.spmField = { node, issue, described }; if (described.length) input.setAttribute("aria-describedby", described.join(" "));
    return node;
  }
  field.serial = 0;
  function fieldError(control, message = "", focus = false) {
    const input = control?.spmInput || control, data = input?.spmField; if (!data) return;
    error(data.issue, message); data.node.classList.toggle("has-error", !!message);
    if (message) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid");
    const described = [...data.described, ...(message ? [data.issue.id] : [])];
    if (described.length) input.setAttribute("aria-describedby", described.join(" ")); else input.removeAttribute("aria-describedby");
    if (!message) input.spmFocusIssue = false;
    if (focus && message) { if (input.disabled) input.spmFocusIssue = true; else input.focus({ preventScroll: true }); }
  }
  function textInput(value, update, id, maxLength = 100) {
    const input = h("input"); input.type = "text"; input.value = value || ""; input.maxLength = maxLength; if (id) input.id = id;
    input.addEventListener("input", () => { update(input.value); fieldError(input); }); return input;
  }
  function tagInputState(tags, pending = "") {
    const values = uniqueWords([...(Array.isArray(tags) ? tags : uniqueWords(tags)), pending].join(","));
    const message = values.length > 64 ? "最多添加 64 个标签。" : values.some(value => [...value].length > 100) ? "每个标签最多 100 个字符。" : "";
    return { values, message };
  }
  function tagInput(value, update, id) {
    const root = h("div", "spm-tag-input"), tokens = h("span", "spm-tag-tokens"), input = h("input", "spm-tag-entry");
    input.type = "text"; input.id = id; input.autocomplete = "off"; input.maxLength = 4000; input.placeholder = "添加标签";
    root.append(tokens, input); root.spmInput = input;
    let values = uniqueWords(value), composing = false; const tokenNodes = new Map();
    const sync = () => { const next = tagInputState(values, input.value); update(next.values.join(", ")); return next; };
    function render() {
      for (const [word, token] of tokenNodes) if (!values.includes(word)) { token.remove(); tokenNodes.delete(word); }
      values.forEach(word => {
        let token = tokenNodes.get(word);
        if (!token) {
          token = h("span", "spm-tag-token"); const remove = button("", "spm-tag-remove", () => {
            if (input.disabled) return; const index = values.indexOf(word); if (index < 0) return;
            values.splice(index, 1); render(); sync(); fieldError(input);
            (tokens.children[index]?.querySelector("button") || input).focus({ preventScroll: true });
          }, `移除标签 ${word}`);
          remove.append(icon("close")); token.append(h("span", "", word), remove); tokenNodes.set(word, token); tokens.append(token);
        }
        token.lastElementChild.disabled = input.disabled;
      });
    }
    function commit(focus = false) {
      if (composing) return false;
      const next = sync(); fieldError(input, next.message, focus);
      if (next.message) return false;
      values = next.values; input.value = ""; render(); sync(); return true;
    }
    input.addEventListener("compositionstart", () => { composing = true; });
    input.addEventListener("compositionend", () => { composing = false; sync(); if (/[,，\n]/.test(input.value)) commit(); });
    input.addEventListener("input", event => { sync(); if (!composing && !event.isComposing) { fieldError(input); if (/[,，\n]/.test(input.value)) commit(); } });
    input.addEventListener("keydown", event => {
      if (composing || event.isComposing || event.keyCode === 229 || event.ctrlKey || event.metaKey) return;
      if (["Enter", ",", "，"].includes(event.key)) { event.preventDefault(); event.stopPropagation(); commit(); }
      else if (event.key === "Backspace" && !input.value && values.length) { event.preventDefault(); values.pop(); render(); sync(); fieldError(input); }
    });
    input.addEventListener("paste", event => {
      const text = event.clipboardData?.getData("text"); if (!text || composing || !/[,，\n]/.test(text)) return;
      event.preventDefault(); input.setRangeText(text, input.selectionStart ?? input.value.length, input.selectionEnd ?? input.value.length, "end"); commit();
    });
    input.addEventListener("blur", () => { if (!composing) commit(); });
    root.spmTags = { commit, input }; render(); return root;
  }
  function editorDirty(session) { return JSON.stringify(session.draft) !== session.initial || !!session.file || !!session.fileLoading; }
  function revoke(session) {
    if (!session) return; session.fileRead++; session.cover?.cancelDrag();
    if (session.cover?.frame != null) { cancelAnimationFrame(session.cover.frame); session.cover.frame = null; }
    if (session.objectUrl) { URL.revokeObjectURL(session.objectUrl); session.objectUrl = ""; }
    for (const url of session.pendingUrls || []) URL.revokeObjectURL(url); session.pendingUrls?.clear(); session.resize?.disconnect();
    session.summaryAnimation?.cancel();
  }
  function discardEditor(immediate = false) { const old = state.editor; state.editor = null; revoke(old); if (ui.editor) show(ui.editor, false, { immediate, restoreFocus: !immediate }); }
  async function closeEditor() {
    const session = state.editor; if (!session) return;
    if (!session.busy && editorDirty(session) && !await confirmAction({ title: "放弃未保存的更改？", message: "名称、封面和整理信息尚未保存。", confirmLabel: "放弃更改", danger: true })) return;
    if (state.editor === session) discardEditor();
  }
  async function startEditor(mode, item) {
    ensure();
    if (state.editor && !await replaceEditor()) return;
    if (state.profile !== profile()) contextChanged(); state.profile = profile();
    if (item && (item.settings?.model_profile || item.model_profile || "anima") !== state.profile) { notify("请切换到这个预设所属的模型档案后再编辑"); return; }
    const snapshot = mode === "edit" ? null : copy(readSettings());
    const session = { mode, item: item ? copy(item) : null, profile: state.profile, epoch: state.epoch, snapshot, busy: false, file: null, objectUrl: "", fileRead: 0,
      imageWidth: 0, imageHeight: 0, crop: { x: 0, y: 0, width: 1, height: 1 }, zoom: 1, cx: .5, cy: .5,
      draft: editorDraft(mode, item) };
    session.initial = JSON.stringify(session.draft); state.editor = session; renderEditor(session); show(ui.editor, true, { initialFocus: session.nameInput });
    ui.editorBody.querySelector("#stylePresetName")?.focus({ preventScroll: true }); await refresh();
  }
  async function replaceEditor() { const previous = state.editor; await closeEditor(); return !state.editor || state.editor !== previous; }
  const create = () => startEditor("create");
  const edit = item => startEditor("edit", item);
  const overwrite = item => startEditor("overwrite", item);
  function renderEditor(session) {
    ui.editorBody.replaceChildren(); error(ui.editorError); ui.editorSave.textContent = session.mode === "overwrite" ? "更新风格参数" : "保存预设"; ui.editorSave.disabled = false; ui.editor.setAttribute("aria-busy", "false");
    ui.editorTitle.textContent = session.mode === "create" ? "新建风格预设" : session.mode === "overwrite" ? "用当前参数更新预设" : "编辑风格预设";
    const intro = h("p", "spm-editor-intro", session.mode === "edit" ? "整理预设信息，不改变已保存的风格参数。" : "已锁定此刻的风格参数。正文、提示词池、AI 处理和设备设置继续使用当前选择。");
    ui.editorBody.append(intro, h("p", "spm-capture-label", `${profileText(session.profile)} · 随机工作台${session.mode !== "edit" ? " · 使用打开弹窗时的参数" : ""}`));
    const layout = h("div", "spm-editor-columns"), cover = h("section", "spm-cover-panel"), fields = h("div", "spm-editor-fields");
    session.nameInput = textInput(session.draft.name, value => { session.draft.name = value; }, "stylePresetName");
    session.nameInput.required = true; session.nameInput.addEventListener("blur", () => { if (session.draft.name.trim()) validateEditorName(session); });
    fields.append(field("预设名称", session.nameInput));
    const star = h("label", "spm-check-line"), checkbox = h("input"); checkbox.type = "checkbox"; checkbox.checked = session.draft.favorite; checkbox.id = "stylePresetFavorite";
    checkbox.addEventListener("change", () => { session.draft.favorite = checkbox.checked; }); star.append(checkbox, h("span", "", "加入收藏")); fields.append(star);
    const tags = tagInput(session.draft.tags, value => { session.draft.tags = value; }, "stylePresetTags"); session.tagsInput = tags;
    fields.append(field("标签", tags, "输入后按回车或逗号添加，可粘贴多个标签。"));
    const groups = h("fieldset", "spm-group-checks"); groups.id = "stylePresetGroups"; groups.append(h("legend", "", "收藏分组")); session.groupsNode = groups;
    fields.append(groups); layout.append(cover, fields); ui.editorBody.append(layout); renderEditorGroups(session); buildCoverEditor(session, cover);
    if (session.mode !== "edit") {
      const optional = h("section", "spm-optional"); optional.append(h("h3", "", "可选携带参数"), h("p", "spm-muted", "未勾选的项目在应用时保留当前值。双采样中间放大始终属于基础风格。"));
      const descriptions = { size: `${session.snapshot.width || "—"} × ${session.snapshot.height || "—"}`, repair: "生成后的放大修复与后处理" };
      const checks = h("div", "spm-include-grid");
      for (const key of Object.keys(INCLUDE)) {
        const label = h("label", "spm-include"), control = h("input"); control.type = "checkbox"; control.checked = session.draft.include[key]; control.dataset.include = key; control.id = `stylePresetInclude_${key}`;
        const unavailable = false;
        control.disabled = unavailable; if (unavailable) control.dataset.unavailable = "true";
        control.addEventListener("change", () => { session.draft.include[key] = control.checked; }); const text = h("span");
        text.append(h("strong", "", includeLabels[key]), h("small", "", unavailable ? session.draft.include[key] ? "保留原预设的单图参数；切换到单图工作台可更新" : "请在单图工作台保存此项" : descriptions[key])); label.append(control, text); checks.append(label);
      } optional.append(checks); ui.editorBody.append(optional);
    }
    const details = h("details", "spm-settings-summary"), settings = session.snapshot || session.item?.settings || {};
    const summaryToggle = h("summary", "", "查看风格参数摘要"); details.append(summaryToggle); const list = h("dl");
    summaryToggle.setAttribute("aria-expanded", "false"); list.id = "stylePresetSettingsSummary"; summaryToggle.setAttribute("aria-controls", list.id);
    summaryToggle.addEventListener("click", event => {
      // Keep native details semantics, but do not send this local disclosure to
      // the document-wide height spring: that resizes the whole dialog per frame.
      event.preventDefault(); event.stopPropagation(); session.summaryAnimation?.cancel(); details.open = !details.open;
      summaryToggle.setAttribute("aria-expanded", String(details.open));
      if (details.open && event.detail !== 0 && !globalThis.StudioMotion?.reduced() && list.animate) {
        session.summaryAnimation = list.animate([{ opacity: .35 }, { opacity: 1 }], { duration: 160, easing: "cubic-bezier(0.23,1,0.32,1)" });
      }
    });
    const info = [["模型", settings.model_name || profileText(session.profile)], ["采样", `${settings.sampler_name || "—"} · ${settings.scheduler || "—"}`], ["步数 / CFG", `${settings.steps ?? "—"} / ${settings.cfg ?? "—"}`], ["底图方式", settings.base_mode === "dual_sampler" ? "双采样" : "普通采样"], ["LoRA", (settings.loras || []).filter(lora => lora.enabled !== false).map(lora => lora.filename || lora.name || lora.lora_name).filter(Boolean).join("、") || "无"], ["来源", "随机工作台"]];
    for (const [name, value] of info) { const row = h("div"); row.append(h("dt", "", name), h("dd", "", value)); list.append(row); } details.append(list); ui.editorBody.append(details);
    if (session.mode === "edit") {
      const actions = h("div", "spm-editor-secondary"); actions.append(button("用当前参数更新…", "button secondary compact", async () => { const item = session.item; if (await replaceEditor()) overwrite(item); }), button("删除预设…", "spm-text-button spm-danger", () => void deleteItem(session))); ui.editorBody.append(actions);
    }
    setEditorBusy(session);
  }
  function renderEditorGroups(session) {
    if (!currentSession(session) || !session.groupsNode) return;
    if (!session.groupRows) {
      session.groupRows = new Map(); session.groupEmpty = h("p", "spm-muted", "尚未创建分组。");
      session.groupCreate = button("新建分组", "spm-text-button", () => openGroup()); session.groupCreate.dataset.focusKey = "editor-group:new";
      session.groupsNode.append(session.groupEmpty, session.groupCreate);
    }
    const ids = new Set(state.groups.map(group => group.id));
    for (const [id, row] of session.groupRows) if (!ids.has(id)) { row.remove(); session.groupRows.delete(id); }
    session.groupEmpty.hidden = !!state.groups.length;
    for (const group of state.groups) {
      let label = session.groupRows.get(group.id);
      if (!label) {
        label = h("label", "spm-group-check"); const control = h("input"); control.type = "checkbox"; control.value = group.id; control.dataset.focusKey = `editor-group:${group.id}`;
        control.addEventListener("change", () => { session.draft.groups = control.checked ? [...new Set([...session.draft.groups, group.id])] : session.draft.groups.filter(id => id !== group.id); if (control.checked) { session.draft.favorite = true; ui.editorBody.querySelector("#stylePresetFavorite").checked = true; } });
        label.append(control, h("span", "", group.name)); session.groupRows.set(group.id, label); session.groupsNode.insertBefore(label, session.groupCreate);
      }
      label.firstElementChild.checked = session.draft.groups.includes(group.id); label.lastElementChild.textContent = group.name;
    }
    setEditorBusy(session);
  }
  function buildCoverEditor(session, container) {
    container.append(h("h3", "", "预设封面"));
    const drop = h("div", "spm-cover-drop"); drop.id = "stylePresetCoverDrop"; drop.tabIndex = 0; drop.setAttribute("role", "group"); drop.setAttribute("aria-label", "导入封面，拖入图片或使用下方按钮");
    const stage = h("div", "spm-crop-stage"); stage.id = "stylePresetCropStage"; stage.append(imageNode(session.item || {}, "spm-existing-cover"));
    const hint = h("p", "spm-muted", "拖入图片，或选择本地图片。封面比例为 4:3。"), file = h("input"); file.type = "file"; file.accept = "image/png,image/jpeg,image/webp"; file.hidden = true; file.id = "stylePresetCoverFile";
    file.addEventListener("change", () => { if (file.files[0]) void readCover(session, file.files[0]); file.value = ""; });
    const coverError = h("p", "spm-field-error"); coverError.id = "stylePresetCoverError"; coverError.hidden = true; coverError.setAttribute("aria-live", "polite");
    drop.setAttribute("aria-describedby", coverError.id);
    const actions = h("div", "spm-cover-actions"), remove = button("移除", "spm-text-button", () => {
      if (!currentSession(session) || session.busy || !writable()) return; session.fileRead++; session.cover.cancelDrag();
      if (session.cover.frame != null) { cancelAnimationFrame(session.cover.frame); session.cover.frame = null; }
      if (session.objectUrl) URL.revokeObjectURL(session.objectUrl); session.objectUrl = ""; session.file = null; session.fileLoading = false; session.draft.cover_id = null; session.cover.image = null;
      stage.replaceChildren(imageNode({}, "spm-existing-cover")); stage.removeAttribute("tabindex"); stage.removeAttribute("aria-label"); controls.hidden = true; reset.hidden = true; hint.textContent = "拖入图片，或选择本地图片。封面比例为 4:3。"; error(coverError);
      setEditorBusy(session);
    }); remove.dataset.coverRemove = "true"; actions.append(button("选择图片", "button ghost compact", () => file.click()), remove);
    const reset = button("重置裁切", "spm-text-button", () => {
      if (!currentSession(session) || session.busy || !session.file || !writable()) return;
      session.cover.cancelDrag(); session.zoom = 1; session.cx = .5; session.cy = .5; session.cover.zoom.value = "1"; paintCrop(session);
    }); reset.id = "stylePresetCoverReset"; reset.hidden = true; actions.append(reset);
    const controls = h("label", "spm-crop-controls"); controls.hidden = true; const zoom = h("input"); zoom.type = "range"; zoom.min = "1"; zoom.max = "4"; zoom.step = ".01"; zoom.value = "1"; zoom.id = "stylePresetCoverZoom";
    zoom.addEventListener("input", () => { session.cover.cancelDrag(); session.zoom = Number(zoom.value); paintCrop(session); }); controls.append(h("span", "", "缩放"), zoom);
    drop.append(stage, hint); container.append(drop, file, actions, controls, coverError); session.cover = { stage, hint, zoom, controls, reset, issue: coverError, image: null, bounds: null, frame: null, cancelDrag };
    drop.addEventListener("dragover", event => { event.preventDefault(); if (!session.busy) drop.classList.add("is-dragover"); });
    drop.addEventListener("dragleave", event => { if (!drop.contains(event.relatedTarget)) drop.classList.remove("is-dragover"); });
    drop.addEventListener("drop", event => { event.preventDefault(); drop.classList.remove("is-dragover"); if (event.dataTransfer.files[0]) void readCover(session, event.dataTransfer.files[0]); });
    let drag = null;
    function cancelDrag(pointerId) {
      if (!drag || (pointerId !== undefined && pointerId !== drag.id)) return;
      const id = drag.id; drag = null; stage.classList.remove("is-dragging");
      if (stage.hasPointerCapture?.(id)) stage.releasePointerCapture(id);
    }
    stage.addEventListener("pointerdown", event => {
      if (drag || !session.file || session.busy || !writable() || event.button !== 0 || event.isPrimary === false) return;
      const bounds = stage.getBoundingClientRect(); if (!bounds.width || !bounds.height) return;
      event.preventDefault(); stage.setPointerCapture(event.pointerId); stage.classList.add("is-dragging");
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY, cx: session.cx, cy: session.cy, width: bounds.width, height: bounds.height, cropWidth: session.crop.width, cropHeight: session.crop.height };
    });
    stage.addEventListener("pointermove", event => {
      if (!drag || event.pointerId !== drag.id || !currentSession(session)) return;
      session.cx = drag.cx - (event.clientX - drag.x) * drag.cropWidth / drag.width; session.cy = drag.cy - (event.clientY - drag.y) * drag.cropHeight / drag.height; paintCrop(session);
    });
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) stage.addEventListener(type, event => cancelDrag(event.pointerId));
    stage.addEventListener("keydown", event => { const directions = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }; if (!session.file || session.busy || !writable() || !directions[event.key]) return; event.preventDefault(); const [dx, dy] = directions[event.key]; session.cx += dx * .015; session.cy += dy * .015; paintCrop(session); });
    session.resize = new ResizeObserver(entries => {
      const size = entries[0]?.contentRect; if (!size?.width || !size.height || !currentSession(session)) return;
      const previous = session.cover.bounds;
      if (previous?.width === size.width && previous?.height === size.height) return;
      cancelDrag(); session.cover.bounds = { width: size.width, height: size.height }; sizeCropImage(session); if (session.file) paintCrop(session);
    }); session.resize.observe(stage);
  }
  async function readCover(session, file) {
    if (!currentSession(session) || session.busy || !writable()) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) { error(session.cover.issue, "请选择 PNG、JPEG 或 WebP 图片。"); return; }
    if (file.size > 20 * 1024 * 1024) { error(session.cover.issue, "封面图片不能超过 20 MB。"); return; }
    const serial = ++session.fileRead, url = URL.createObjectURL(file), image = new Image(); session.fileLoading = true; setEditorBusy(session);
    session.pendingUrls ||= new Set(); session.pendingUrls.add(url); error(session.cover.issue);
    try {
      image.src = url; await image.decode();
      if (!currentSession(session) || serial !== session.fileRead) { URL.revokeObjectURL(url); return; }
      if (!image.naturalWidth || !image.naturalHeight) throw new Error("图片尺寸无效");
      if (session.objectUrl) URL.revokeObjectURL(session.objectUrl);
      session.cover.cancelDrag(); session.objectUrl = url; session.file = file; session.imageWidth = image.naturalWidth; session.imageHeight = image.naturalHeight; session.zoom = 1; session.cx = .5; session.cy = .5;
      session.cover.image = image; image.alt = "封面裁切预览"; image.draggable = false;
      session.cover.stage.replaceChildren(image); session.cover.stage.tabIndex = 0; session.cover.stage.setAttribute("aria-label", "拖动调整封面裁切，或使用方向键微调"); session.cover.zoom.value = "1"; session.cover.controls.hidden = false; session.cover.reset.hidden = false;
      session.cover.hint.textContent = "拖动调整位置，使用滑块缩放。"; error(session.cover.issue);
      session.cover.bounds ||= { width: session.cover.stage.clientWidth, height: session.cover.stage.clientHeight }; sizeCropImage(session); paintCrop(session);
    } catch (failure) { URL.revokeObjectURL(url); if (currentSession(session) && serial === session.fileRead) error(session.cover.issue, `无法读取图片：${failure.message}`); }
    finally { session.pendingUrls.delete(url); if (serial === session.fileRead) { session.fileLoading = false; setEditorBusy(session); } }
  }
  function cropPresentation(width, height, bounds, crop) {
    const base = Math.max(bounds.width / width, bounds.height / height), imageWidth = width * base, imageHeight = height * base;
    const scale = bounds.width / (crop.width * imageWidth);
    return { width: imageWidth, height: imageHeight, x: -crop.x * imageWidth * scale, y: -crop.y * imageHeight * scale, scale };
  }
  function sizeCropImage(session) {
    const cover = session.cover; if (!cover?.image || !cover.bounds?.width) return;
    const value = cropPresentation(session.imageWidth, session.imageHeight, cover.bounds, cropRectangle(session.imageWidth, session.imageHeight));
    Object.assign(cover.image.style, { width: `${value.width}px`, height: `${value.height}px`, left: "0", top: "0", transformOrigin: "0 0" });
  }
  function paintCrop(session) {
    if (!currentSession(session) || !session.file) return;
    const rect = cropRectangle(session.imageWidth, session.imageHeight, session.zoom, session.cx, session.cy); session.crop = rect; session.cx = rect.x + rect.width / 2; session.cy = rect.y + rect.height / 2;
    const cover = session.cover; if (cover.frame != null) return;
    cover.frame = requestAnimationFrame(() => {
      cover.frame = null; if (!currentSession(session) || !cover.image || !cover.bounds?.width || !session.file) return;
      const value = cropPresentation(session.imageWidth, session.imageHeight, cover.bounds, session.crop);
      cover.image.style.transform = `translate3d(${value.x}px, ${value.y}px, 0) scale(${value.scale})`;
    });
  }
  function setEditorBusy(session) {
    if (!currentSession(session)) return;
    ui.editorSave.disabled = session.busy || session.fileLoading || !writable(); ui.editorSave.textContent = session.busy ? "正在保存…" : session.fileLoading ? "正在读取图片…" : session.mode === "overwrite" ? "更新风格参数" : "保存预设";
    ui.editor.setAttribute("aria-busy", String(session.busy || !!session.fileLoading));
    ui.editorBody.querySelectorAll("input, button, textarea, select").forEach(node => {
      node.disabled = session.busy || !writable() || node.dataset.unavailable === "true";
      if (!node.disabled && node.spmFocusIssue) { node.spmFocusIssue = false; node.focus({ preventScroll: true }); }
    });
    const remove = ui.editorBody.querySelector("[data-cover-remove]"); if (remove) remove.disabled ||= !session.file && !session.draft.cover_id;
    if (session.busy) session.cover?.cancelDrag();
    if (!writable()) error(ui.editorError, "风格预设目前为只读，恢复存储后可以继续编辑。");
  }
  function validateEditorName(session, focus = false) {
    const name = session.draft.name.trim();
    const message = !name ? "请输入预设名称。" : [...name].length > 100 ? "预设名称最多 100 个字符。" :
      state.items.some(item => item.id !== session.item?.id && item.name.trim().toLocaleLowerCase() === name.toLocaleLowerCase()) ? "已有同名预设，请使用其他名称。" : "";
    fieldError(session.nameInput, message, focus); return !message;
  }
  function editorFailure(session, failure) {
    const message = failure.message || "保存失败，请重试。";
    if (/标签/.test(message)) fieldError(session.tagsInput, message, true);
    else if (/名称|同名/.test(message)) fieldError(session.nameInput, message, true);
    else error(ui.editorError, message);
  }
  async function submitEditor() {
    const session = state.editor; if (!session || session.busy || session.fileLoading || !currentSession(session) || !writable()) return;
    if (!validateEditorName(session, true) || !session.tagsInput.spmTags.commit(true)) return;
    error(ui.editorError);
    const saving = saveSession(session, {
      current: currentSession,
      upload: async (file, crop) => { const form = new FormData(); form.append("image", file); form.append("crop", JSON.stringify(crop)); return request("/api/style-preset-covers", { method: "POST", headers: {}, body: form }); },
      write: payload => request(session.mode === "create" ? "/api/style-presets" : `/api/style-presets/${encodeURIComponent(session.item.id)}`, { method: session.mode === "create" ? "POST" : "PUT", body: JSON.stringify(payload) }),
      success: () => discardEditor(),
      committed: result => changed(session.profile, `已保存“${result.name || session.draft.name}”`),
      failure: failure => editorFailure(session, failure), detachedFailure: failure => notify(`${profileText(session.profile)}：保存失败，${failure.message}`), settled: setEditorBusy,
    }); setEditorBusy(session); await saving;
  }
  async function deleteItem(session) {
    if (!currentSession(session) || session.busy) return;
    if (!await confirmAction({ title: `删除“${session.item.name}”？`, message: "预设和封面将从风格库移除，当前生成设置不会改变。", confirmLabel: "删除预设", danger: true }) || !currentSession(session)) return;
    session.busy = true; setEditorBusy(session);
    try { await request(`/api/style-presets/${encodeURIComponent(session.item.id)}?model_profile=${encodeURIComponent(session.profile)}`, { method: "DELETE" }); if (currentSession(session)) discardEditor(); await changed(session.profile, "已删除预设"); }
    catch (failure) { if (currentSession(session)) error(ui.editorError, failure.message); else notify(failure.message); }
    finally { session.busy = false; setEditorBusy(session); }
  }
  function smallDirty(session) { return JSON.stringify(session.draft) !== session.initial; }
  function discardSmall(immediate = false) { state.small = null; if (ui.small) show(ui.small, false, { immediate, restoreFocus: !immediate }); }
  async function closeSmall() {
    const session = state.small; if (!session) return;
    if (!session.busy && smallDirty(session) && !await confirmAction({ title: "放弃未保存的更改？", message: "这次整理尚未保存。", confirmLabel: "放弃更改", danger: true })) return;
    if (state.small === session) discardSmall();
  }
  async function openGroup(group) {
    if (!writable()) return; ensure(); if (state.small) { await closeSmall(); if (state.small) return; }
    const session = { type: "group", profile: profile(), epoch: state.epoch, id: group?.id, draft: { name: group?.name || "" }, busy: false }; session.initial = JSON.stringify(session.draft); state.small = session;
    ui.smallTitle.textContent = group ? "编辑收藏分组" : "新建收藏分组"; ui.smallBody.replaceChildren(); error(ui.smallError); ui.smallSave.disabled = false; ui.smallSave.textContent = "保存分组";
    const input = textInput(session.draft.name, value => { session.draft.name = value; }, "stylePresetGroupName"); session.nameInput = input; input.required = true;
    input.addEventListener("blur", () => { if (session.draft.name.trim()) validateGroupName(session); }); ui.smallBody.append(field("分组名称", input));
    if (group) ui.smallBody.append(button("删除分组…", "spm-text-button spm-danger", () => void deleteGroup(session)));
    setSmallBusy(session); show(ui.small, true, { initialFocus: input });
  }
  async function deleteGroup(session) {
    if (session.busy || !currentSmall(session) || !writable()) return;
    if (!await confirmAction({ title: "删除收藏分组？", message: "只移除分组，预设、标签和收藏状态都会保留。", confirmLabel: "删除分组", danger: true }) || !currentSmall(session)) return;
    session.busy = true; setSmallBusy(session);
    try { await request(`/api/style-preset-groups/${encodeURIComponent(session.id)}?model_profile=${encodeURIComponent(session.profile)}`, { method: "DELETE" }); if (currentSmall(session)) discardSmall(); await changed(session.profile, "已删除分组，收藏已保留"); }
    catch (failure) { if (currentSmall(session)) error(ui.smallError, failure.message); else notify(failure.message); }
    finally { session.busy = false; setSmallBusy(session); }
  }
  async function openBatch(action) {
    if (!state.selected.size || !writable()) return;
    const ids = [...state.selected], forProfile = profile();
    if (action === "favorite" || action === "unfavorite") {
      if (state.batchWrites.has(forProfile)) return; const operation = {}; state.batchWrites.set(forProfile, operation); renderGrid(); const epoch = state.epoch;
      try { await request("/api/style-presets/batch", { method: "POST", body: JSON.stringify({ model_profile: forProfile, ids, action: "favorite", value: action === "favorite" }) }); await changed(forProfile, `已${action === "favorite" ? "收藏" : "取消收藏"} ${ids.length} 个预设`); }
      catch (failure) { if (epoch === state.epoch && forProfile === profile()) error(ui.notice, failure.message); else notify(`${profileText(forProfile)}：${failure.message}`); }
      finally {
        if (state.batchWrites.get(forProfile) === operation) state.batchWrites.delete(forProfile);
        if (forProfile === profile() && state.managerVisible) renderGrid();
      } return;
    }
    if (state.small) { await closeSmall(); if (state.small) return; }
    const session = { type: "batch", action, ids, profile: forProfile, epoch: state.epoch, draft: { mode: "add", values: [], tags: "" }, busy: false }; session.initial = JSON.stringify(session.draft); state.small = session;
    ui.smallTitle.textContent = action === "groups" ? "批量整理分组" : "批量编辑标签"; ui.smallBody.replaceChildren(h("p", "spm-muted", `将修改已选择的 ${ids.length} 个预设，之后的筛选变化不影响本次选择。`)); error(ui.smallError); ui.smallSave.disabled = false; ui.smallSave.textContent = "保存整理";
    const mode = h("select"); for (const [value, text] of [["add", "追加"], ["remove", "移除"], ["replace", "替换全部"]]) mode.append(new Option(text, value)); mode.addEventListener("change", () => { session.draft.mode = mode.value; }); ui.smallBody.append(field("操作方式", mode));
    if (action === "groups") {
      const groups = h("div", "spm-group-checks");
      for (const group of state.groups) { const label = h("label", "spm-group-check"), control = h("input"); control.type = "checkbox"; control.addEventListener("change", () => { session.draft.values = control.checked ? [...session.draft.values, group.id] : session.draft.values.filter(id => id !== group.id); }); label.append(control, h("span", "", group.name)); groups.append(label); }
      if (!state.groups.length) groups.append(h("p", "spm-muted", "请先在收藏分组中创建分组。")); ui.smallBody.append(groups, h("p", "spm-muted", "加入分组会同步收藏；移出分组保留星标。"));
    } else {
      session.tagsInput = tagInput("", value => { session.draft.tags = value; }, "stylePresetBatchTags");
      ui.smallBody.append(field("标签", session.tagsInput, "输入后按回车或逗号添加，可粘贴多个标签。"));
    }
    setSmallBusy(session); show(ui.small, true, { initialFocus: ui.smallBody.querySelector("input, select") });
  }
  function setSmallBusy(session) {
    if (!currentSmall(session)) return;
    ui.small.setAttribute("aria-busy", String(session.busy)); ui.smallSave.disabled = session.busy || !writable();
    ui.smallSave.textContent = session.busy ? "正在保存…" : session.type === "group" ? "保存分组" : "保存整理";
    ui.smallBody.querySelectorAll("input, button, select").forEach(node => {
      node.disabled = session.busy || !writable();
      if (!node.disabled && node.spmFocusIssue) { node.spmFocusIssue = false; node.focus({ preventScroll: true }); }
    });
    if (!writable()) error(ui.smallError, "风格预设目前为只读，恢复存储后可以继续编辑。");
  }
  function validateGroupName(session, focus = false) {
    const name = session.draft.name.trim(), message = !name ? "请输入分组名称。" : [...name].length > 100 ? "分组名称最多 100 个字符。" :
      state.groups.some(group => group.id !== session.id && group.name.trim().toLocaleLowerCase() === name.toLocaleLowerCase()) ? "已有同名分组，请使用其他名称。" : "";
    fieldError(session.nameInput, message, focus); return !message;
  }
  async function submitSmall() {
    const session = state.small; if (!session || session.busy || !currentSmall(session) || !writable()) return;
    if (session.type === "group" && !validateGroupName(session, true)) return;
    if (session.tagsInput && !session.tagsInput.spmTags.commit(true)) return;
    const values = session.action === "tags" ? uniqueWords(session.draft.tags) : session.draft.values;
    if (session.type === "batch" && !values.length && session.draft.mode !== "replace") {
      if (session.tagsInput) fieldError(session.tagsInput, "请至少输入一个标签。", true); else error(ui.smallError, "请选择至少一个分组。"); return;
    }
    const payload = session.type === "group" ? { model_profile: session.profile, name: session.draft.name.trim() } : { model_profile: session.profile, ids: session.ids, action: session.action, value: { mode: session.draft.mode, values } };
    session.busy = true; setSmallBusy(session); error(ui.smallError);
    try {
      await request(session.type === "group" ? `/api/style-preset-groups${session.id ? `/${encodeURIComponent(session.id)}` : ""}` : "/api/style-presets/batch", { method: session.type === "group" && session.id ? "PUT" : "POST", body: JSON.stringify(payload) });
      if (currentSmall(session)) discardSmall(); await changed(session.profile, session.type === "group" ? "分组已保存" : "预设整理已保存");
    } catch (failure) {
      if (currentSmall(session)) {
        if (session.nameInput && /名称|同名/.test(failure.message)) fieldError(session.nameInput, failure.message, true);
        else if (session.tagsInput && /标签/.test(failure.message)) fieldError(session.tagsInput, failure.message, true);
        else error(ui.smallError, failure.message);
      } else notify(`${profileText(session.profile)}：${failure.message}`);
    } finally { session.busy = false; setSmallBusy(session); }
  }
  globalThis.StylePresetManager = Object.freeze({ open, create, edit, overwrite, confirm: confirmAction, refresh, contextChanged });
})();
