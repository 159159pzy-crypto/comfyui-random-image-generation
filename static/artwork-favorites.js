/* Artwork memberships are shared by both workbenches; selection belongs to the
 * home archive. No image files or generation settings are changed here. */
const artworkFavoritesState = {
  ready: false, loaded: false, loading: false, loadToken: 0, revision: 0,
  folders: [], records: new Map(), memberships: new Map(), selected: new Map(), targets: new Set(), missingRecords: new Set(),
  selecting: false, expanded: false, busy: false, loadError: "", menuError: "",
  popover: null, popoverNode: null, popoverToken: 0, refreshNeeded: false,
  editing: null, editingName: "", deleting: null, newName: "", drag: null, sortAnimation: null,
  externalPending: false, externalSyncing: false
};
const ARTWORK_FAVORITES_EVENT = "anima-random-webui:artwork-favorites-updated";
const artworkToolbarMotion = { initialized: false, selecting: false, expanded: false, width: 0, height: 0, observer: null, observedWidth: 0 };
const ARTWORK_STAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.78 5.63L21 9.54l-4.5 4.38L17.56 20 12 17.13 6.44 20l1.06-6.08L3 9.54l6.22-.91Z"/></svg>';
const ARTWORK_FOLDER = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 7V5.8A1.8 1.8 0 0 1 5.3 4h4.1l2.3 2.5h7A1.8 1.8 0 0 1 20.5 8v9.2a1.8 1.8 0 0 1-1.8 1.8H5.3a1.8 1.8 0 0 1-1.8-1.8V7Z"/><path d="M3.5 9h17"/></svg>';
const artworkElement = (id) => document.getElementById(id);
function artworkNode(tag, className = "", text = "") {
  const node = document.createElement(tag);node.className = className;node.textContent = text;return node;
}
function artworkButton(text, className, action) {
  const button = artworkNode("button", className, text);button.type = "button";
  if (action) button.addEventListener("click", action);return button;
}
function artworkSelectionActive() {return artworkFavoritesState.selecting;}
function artworkFolderQuery() {return typeof historyDetailState === "undefined" ? "" : historyDetailState.homeQuery.folderId || "";}
function artworkWritable() {return typeof historyDetailWritable === "function" ? historyDetailWritable() : true;}
function artworkNotify(message) {if (typeof toast === "function") toast(message);}
function artworkFocus(node) {
  if (node?.isConnected && !node.disabled && !node.hidden && !node.closest?.("[hidden], [inert]")) node.focus({ preventScroll: true });
}
function artworkPresence(node, visible, options = {}) {
  if (!node) return;
  if (typeof StudioMotion !== "undefined") return StudioMotion.presence(node, visible, { from: { opacity: 0, y: 5, scale: .98 }, response: .3, damping: 1, ...options });
  node.hidden = !visible;node.inert = !visible;options.onComplete?.();
}
function artworkRememberRecord(record) {
  if (!record || record.id == null) return null;
  const state = artworkFavoritesState,id = String(record.id);
  if (state.missingRecords.has(id)) return null;
  if (!state.memberships.has(id)) state.memberships.set(id, [...(record.folder_ids || [])].map(String));
  record.folder_ids = [...state.memberships.get(id)];
  state.records.set(id, record);
  if (state.selected.has(id)) state.selected.set(id, record);
  return record;
}
function artworkRecordFolders(record) {
  return artworkFavoritesState.memberships.get(String(record?.id)) || (record?.folder_ids || []).map(String);
}
function artworkStarState(button, record) {
  if (!button || !record) return;
  const folders = artworkRecordFolders(record),favorite = folders.length > 0;
  if (!button.querySelector("svg")) button.innerHTML = ARTWORK_STAR;
  button.classList.toggle("is-favorite", favorite);
  button.setAttribute("aria-label", favorite ? `管理收藏，已加入 ${folders.length} 个收藏夹` : "选择收藏夹");
  button.title = favorite ? "管理收藏" : "收藏到…";
  button.disabled = !artworkWritable() || artworkFavoritesState.missingRecords.has(String(record.id));
}
function decorateArtworkCard(article, record, mainButton = null) {
  const state = artworkFavoritesState;
  artworkRememberRecord(record);mainButton ||= article.querySelector(".image-card-open") || article.querySelector("button");
  let star = article.querySelector(".artwork-star");
  if (!star) {
    star = artworkButton("", "artwork-star artwork-card-star", (event) => {
      event.stopPropagation();openArtworkMembership(article.record, star);
    });
    star.setAttribute("aria-haspopup", "dialog");star.setAttribute("aria-expanded", "false");
    const mark = artworkNode("span", "artwork-selection-mark");mark.setAttribute("aria-hidden", "true");
    mark.append(artworkNode("span", "artwork-selection-check", "✓"));
    article.append(star, mark);
  }
  star.hidden = state.selecting;artworkStarState(star, record);
  const selected = state.selected.has(String(record.id));
  article.classList.toggle("is-artwork-selecting", state.selecting);article.classList.toggle("is-artwork-selected", state.selecting && selected);
  if (mainButton) {
    if (state.selecting) {mainButton.setAttribute("aria-pressed", String(selected));mainButton.setAttribute("aria-label", `${selected ? "取消选择" : "选择"} ${record.filename || "作品"}`);} else
    {mainButton.removeAttribute("aria-pressed");mainButton.setAttribute("aria-label", `查看 ${record.filename || "生成结果"}`);}
  }
}
function renderArtworkDetail(record) {
  artworkRememberRecord(record);artworkStarState(artworkElement("detailFavorite"), record);
}
function artworkRenderCards() {
  for (const article of document.querySelectorAll("#gallery .image-card")) {
    if (artworkFavoritesState.missingRecords.has(String(article.record?.id))) {
      if (typeof card !== "undefined") card.visibilityObserver?.unobserve(article);
      article.remove();
    } else if (article.record) decorateArtworkCard(article, article.record);
  }
  if (typeof selectedRecord !== "undefined" && selectedRecord) renderArtworkDetail(selectedRecord);
}
function artworkActivateCard(record) {
  const state = artworkFavoritesState;
  if (!state.selecting) return false;
  if (state.busy) return true;
  artworkRememberRecord(record);const id = String(record.id);
  if (state.selected.has(id)) state.selected.delete(id);else state.selected.set(id, record);
  artworkRenderCards();artworkRenderToolbar();return true;
}
function artworkSetSelecting(selecting) {
  const state = artworkFavoritesState;
  if (state.busy) return;
  closeArtworkPopovers({ restoreFocus: false });
  state.selecting = Boolean(selecting);state.targets.clear();
  if (!selecting) state.selected.clear();else state.expanded = true;
  artworkRenderCards();artworkRenderToolbar();
  if (!selecting) artworkFocus(artworkElement("artworkSelect"));
}
function artworkSetFolder(id) {
  const state = artworkFavoritesState,key = String(id || "");
  if (state.selecting) {
    if (!key || state.busy) return;
    if (state.targets.has(key)) state.targets.delete(key);else state.targets.add(key);
    artworkRenderToolbar();return;
  }
  if (key === artworkFolderQuery()) return;
  closeArtworkPopovers({ restoreFocus: false });
  historyDetailState.homeQuery = { ...historyDetailState.homeQuery, folderId: key };
  invalidateHistoryView();artworkRenderToolbar();void loadHistory(1);
}
function artworkHistoryRendered(view) {
  const state = artworkFavoritesState;
  for (const record of view?.items || []) artworkRememberRecord(record);
  const folder = state.folders.find((item) => String(item.id) === artworkFolderQuery());
  const title = document.querySelector("#randomArchive .history-title h1");
  if (title) title.textContent = folder?.name || "作品库";
  if (folder && view?.elements?.emptyState && !view.items.length) {
    view.elements.emptyState.querySelector("strong").textContent = "这里还没有作品";
    view.elements.emptyState.querySelector("small").textContent = view.query?.source ? "可切换作品来源，或从作品库收藏图片" : "点击作品上的星星，将喜欢的图片收入这里";
  }
  artworkRenderToolbar();
}
function artworkBuildToolbar() {
  const root = artworkElement("artworkToolbar");if (!root) return;
  const browser = artworkNode("div", "artwork-browser");
  const toggle = artworkButton("收藏夹", "artwork-folder-toggle", () => {
    const state = artworkFavoritesState;state.expanded = !state.expanded;artworkRenderToolbar();
  });
  toggle.id = "artworkFoldersToggle";toggle.setAttribute("aria-controls", "artworkFolderStrip");
  const strip = artworkNode("div", "artwork-folder-strip");strip.id = "artworkFolderStrip";strip.hidden = true;
  const reveal = artworkNode("div", "artwork-folder-reveal");reveal.id = "artworkFolderReveal";reveal.inert = true;reveal.append(strip);
  const manage = artworkButton("···", "artwork-manage", (event) => openArtworkManager(event.currentTarget));
  manage.id = "artworkManage";manage.title = "管理收藏夹";manage.setAttribute("aria-label", "管理收藏夹");manage.setAttribute("aria-haspopup", "dialog");manage.setAttribute("aria-expanded", "false");
  browser.append(toggle, reveal, manage);
  const actions = artworkNode("div", "artwork-selection-actions");
  const summary = artworkNode("span", "artwork-selection-summary");summary.id = "artworkSelectionSummary";summary.setAttribute("role", "status");
  const add = artworkButton("加入收藏夹", "artwork-action artwork-action-primary", () => artworkBatchMembership("add"));add.id = "artworkBatchAdd";
  const remove = artworkButton("移出此夹", "artwork-action", () => artworkBatchMembership("remove"));remove.id = "artworkBatchRemove";
  const clear = artworkButton("清空", "artwork-action", () => {if (!artworkFavoritesState.busy) {artworkFavoritesState.selected.clear();artworkRenderCards();artworkRenderToolbar();}});clear.id = "artworkClear";
  const select = artworkButton("选择", "artwork-action", () => artworkSetSelecting(!artworkFavoritesState.selecting));select.id = "artworkSelect";
  actions.append(summary, add, remove, clear, select);
  const layout = artworkNode("div", "artwork-toolbar-layout");layout.id = "artworkToolbarLayout";
  layout.append(browser, actions);root.append(layout);
}
function artworkCaptureToolbar() {
  const root = artworkElement("artworkToolbar"),actions = root?.querySelector(".artwork-selection-actions");
  if (!root?.getBoundingClientRect || !actions) return null;
  return { height: root.getBoundingClientRect().height, width: artworkElement("artworkFolderReveal")?.getBoundingClientRect().width || 0, actions: actions.getBoundingClientRect() };
}
function artworkLayoutToolbar(before, { immediate = false } = {}) {
  const root = artworkElement("artworkToolbar"),layout = artworkElement("artworkToolbarLayout"),reveal = artworkElement("artworkFolderReveal");
  if (!root || !layout || !reveal || !root.getBoundingClientRect().width) return;
  const state = artworkFavoritesState,motion = artworkToolbarMotion;
  const strip = artworkElement("artworkFolderStrip"),toggle = artworkElement("artworkFoldersToggle"),manage = artworkElement("artworkManage");
  const browser = layout.querySelector(".artwork-browser"),actions = layout.querySelector(".artwork-selection-actions");
  const modeChanged = motion.initialized && motion.selecting !== state.selecting;
  const expandedChanged = motion.initialized && motion.expanded !== state.expanded;
  const instant = immediate || !motion.initialized || typeof StudioMotion === "undefined";
  // Measure intrinsic layout once per update; animation frames only write sizes.
  // The inner row stays at its natural height while its outer shell moves the gallery.
  const available = Math.max(0, browser.getBoundingClientRect().width - toggle.getBoundingClientRect().width - manage.getBoundingClientRect().width - 4);
  const targetWidth = state.expanded ? available : 0;
  const oldHeight = before?.height ?? root.getBoundingClientRect().height;
  strip.hidden = !state.expanded && !motion.expanded && !artworkMotionSnapshot(reveal, "artwork-width")?.active;
  reveal.inert = !state.expanded;
  strip.style.width = `${Math.max(0, available - 4)}px`;
  const widthChanged = Math.abs(targetWidth - motion.width) > .5 || expandedChanged;
  const finishWidth = () => {
    strip.hidden = !artworkFavoritesState.expanded;
    reveal.classList.toggle("is-expanded", artworkFavoritesState.expanded);
    reveal.style.removeProperty("width");reveal.style.removeProperty("opacity");
    reveal.style.removeProperty("flex");strip.style.removeProperty("width");
    reveal.dataset.motionState = artworkFavoritesState.expanded ? "open" : "closed";
    artworkFinishToolbarHeight(root, reveal);
    positionArtworkPopover();
  };
  if (widthChanged || instant) {
    const currentWidth = before?.width ?? reveal.getBoundingClientRect().width;
    reveal.style.flex = "0 0 auto";
    reveal.dataset.motionState = state.expanded ? "opening" : "closing";
    const initial = { width: currentWidth, opacity: motion.expanded ? 1 : 0 };
    const target = { width: targetWidth, opacity: state.expanded ? 1 : 0 };
    if (typeof StudioMotion === "undefined") {finishWidth();} else
    StudioMotion.spring(reveal, "artwork-width", target, {
      initial, immediate: instant, response: .3, damping: 1, precision: { width: .1, opacity: .001 },
      onUpdate(value) {
        reveal.style.width = `${Math.max(0, value.width)}px`;reveal.style.opacity = String(Math.max(0, Math.min(1, value.opacity)));
        positionArtworkPopover();
      }, onComplete: finishWidth
    });
  } else if (!artworkMotionSnapshot(reveal, "artwork-width")?.active) strip.style.removeProperty("width");
  root.style.removeProperty("height");
  const presentedHidden = strip.hidden;
  strip.hidden = !state.expanded;
  const targetHeight = root.getBoundingClientRect().height;
  strip.hidden = presentedHidden;
  const heightChanged = Math.abs(targetHeight - motion.height) > .5;
  // Preserve the presentation height even when a routine count refresh arrives mid-flight.
  if (heightChanged || modeChanged || instant) {
    const finishHeight = () => artworkFinishToolbarHeight(root, reveal);
    root.classList.add("is-layout-moving");
    if (typeof StudioMotion === "undefined") finishHeight();else
    StudioMotion.spring(root, "artwork-height", { height: targetHeight }, {
      initial: { height: oldHeight }, immediate: instant, response: .3, damping: 1, precision: .1,
      onUpdate(value) {root.style.height = `${Math.max(0, value.height)}px`;}, onComplete: finishHeight
    });
  } else {
    const running = artworkMotionSnapshot(root, "artwork-height");
    if (running?.active || artworkMotionSnapshot(reveal, "artwork-width")?.active) root.style.height = `${running?.value.height ?? targetHeight}px`;
  }
  if (modeChanged || instant) {
    // Position belongs to this wrapper; the buttons retain their own press transforms.
    const previous = typeof StudioMotion === "undefined" ? null : StudioMotion.freeze(actions, "artwork-actions");
    actions.style.removeProperty("transform");
    const next = actions.getBoundingClientRect();
    if (typeof StudioMotion !== "undefined") StudioMotion.spring(actions, "artwork-actions", { x: 0, y: 0, opacity: 1 }, {
      initial: { x: before ? before.actions.right - next.right : 0, y: before ? before.actions.bottom - next.bottom : 0, opacity: previous?.active ? previous.value.opacity : modeChanged && state.selecting ? .2 : 1 },
      velocity: previous?.velocity, immediate: instant, response: .3, damping: 1, precision: { x: .1, y: .1, opacity: .001 },
      onUpdate(value) {actions.style.transform = `translate3d(${value.x}px, ${value.y}px, 0)`;actions.style.opacity = String(Math.max(0, Math.min(1, value.opacity)));},
      onComplete() {actions.style.removeProperty("transform");actions.style.removeProperty("opacity");}
    });
  }
  motion.initialized = true;motion.selecting = state.selecting;motion.expanded = state.expanded;motion.width = targetWidth;motion.height = targetHeight;
}
function artworkMotionSnapshot(node, channel) {
  return typeof StudioMotion === "undefined" ? null : StudioMotion.snapshot(node, channel);
}
function artworkFinishToolbarHeight(root, reveal) {
  // A non-overlay horizontal scrollbar may keep the old row taller during exit.
  // Release the shell only after both sizes have reached the latest layout.
  if (artworkMotionSnapshot(root, "artwork-height")?.active || artworkMotionSnapshot(reveal, "artwork-width")?.active) return;
  root.style.removeProperty("height");root.classList.remove("is-layout-moving");
}
function artworkRenderToolbar() {
  const state = artworkFavoritesState,root = artworkElement("artworkToolbar");if (!root || !state.ready) return;
  const before = artworkCaptureToolbar();
  const strip = artworkElement("artworkFolderStrip"),toggle = artworkElement("artworkFoldersToggle"),active = artworkFolderQuery();
  toggle.textContent = state.selecting ? "收入收藏夹" : "收藏夹";
  toggle.setAttribute("aria-expanded", String(state.expanded));
  strip.setAttribute("role", "group");strip.setAttribute("aria-label", state.selecting ? "选择要收入的收藏夹，可多选" : "按收藏夹浏览作品");
  const entries = state.selecting ? state.folders : [{ id: "", name: "全部作品" }, ...state.folders];
  const existing = new Map([...strip.children].map((node) => [node.dataset.folderId, node]));
  const keep = new Set();
  for (const [index, folder] of entries.entries()) {
    const id = String(folder.id);let button = existing.get(id);
    if (!button) {button = artworkButton("", "artwork-folder-chip", () => artworkSetFolder(id));button.dataset.folderId = id;}
    button.textContent = `${folder.name}${folder.count == null ? "" : ` ${folder.count}`}`;
    button.title = folder.name;
    const selected = state.selecting ? state.targets.has(id) : active === id;
    button.classList.toggle("is-active", selected);button.setAttribute("aria-pressed", String(selected));
    button.disabled = state.busy && state.selecting;
    if (strip.children[index] !== button) strip.insertBefore(button, strip.children[index] || null);
    keep.add(button);
  }
  for (const node of existing.values()) if (!keep.has(node)) node.remove();
  if (!entries.length) {
    let empty = strip.querySelector(".artwork-folder-empty");
    if (!empty) {empty = artworkNode("span", "artwork-folder-empty", "还没有收藏夹");strip.append(empty);}
  }
  const ids = new Set((typeof historyItems !== "undefined" ? historyItems : []).map((record) => String(record.id)));
  const hiddenCount = [...state.selected.keys()].filter((id) => !ids.has(id)).length;
  const summary = artworkElement("artworkSelectionSummary");
  summary.textContent = `已选 ${state.selected.size} 张${hiddenCount ? ` · ${hiddenCount} 张在其他页面或来源` : ""}`;summary.hidden = !state.selecting;
  const add = artworkElement("artworkBatchAdd");add.hidden = !state.selecting;add.disabled = state.busy || !state.selected.size || !state.targets.size || !artworkWritable();
  add.textContent = state.busy ? "正在保存…" : state.targets.size ? `加入 ${state.targets.size} 个收藏夹` : "加入收藏夹";
  const remove = artworkElement("artworkBatchRemove");remove.hidden = !state.selecting || !active;remove.disabled = state.busy || !state.selected.size || !artworkWritable();
  const clear = artworkElement("artworkClear");clear.hidden = !state.selecting;clear.disabled = state.busy || !state.selected.size;
  const select = artworkElement("artworkSelect");select.textContent = state.selecting ? "取消" : "选择";select.disabled = state.busy || !artworkWritable();
  artworkElement("artworkManage").disabled = state.busy || !artworkWritable();
  root.classList.toggle("is-selecting", state.selecting);
  artworkLayoutToolbar(before);
}
async function artworkLoadFolders() {
  const state = artworkFavoritesState,token = ++state.loadToken,revision = state.revision;
  state.loading = true;state.loadError = "";
  try {
    const data = await request("/api/artwork-folders", { cache: "no-store" });
    if (token !== state.loadToken || revision !== state.revision) return;
    state.folders = data.folders || [];state.loaded = true;
    const valid = new Set(state.folders.map((folder) => String(folder.id)));
    state.targets = new Set([...state.targets].filter((id) => valid.has(id)));
    if (artworkFolderQuery() && !valid.has(artworkFolderQuery())) {
      historyDetailState.homeQuery = { ...historyDetailState.homeQuery, folderId: "" };
      invalidateHistoryView();void loadHistory(1);
    }
  } catch (error) {if (token === state.loadToken) state.loadError = error.message || "收藏夹加载失败";} finally
  {if (token === state.loadToken) {state.loading = false;artworkRenderToolbar();artworkRenderPopover();}}
}
function artworkApplyPayload(data, { broadcast = true } = {}) {
  const state = artworkFavoritesState;
  ++state.revision;++state.loadToken;state.loading = false;state.loadError = "";state.loaded = true;
  if (Array.isArray(data.folders)) state.folders = data.folders;
  const patches = new Map((data.items || []).map((item) => [String(item.id), (item.folder_ids || []).map(String)]));
  for (const [id, folders] of patches) state.memberships.set(id, [...folders]);
  const update = (record) => {if (record && patches.has(String(record.id))) record.folder_ids = [...patches.get(String(record.id))];};
  state.records.forEach(update);state.selected.forEach(update);
  if (typeof historyItems !== "undefined") historyItems.forEach(update);
  if (typeof historyCache !== "undefined") for (const page of historyCache.values()) (page.items || []).forEach(update);



  if (typeof selectedRecord !== "undefined") update(selectedRecord);
  if (patches.size) {state.refreshNeeded = true;if (typeof invalidateHistoryView === "function") invalidateHistoryView();}
  artworkRenderCards();artworkRenderToolbar();
  if (broadcast) try {localStorage.setItem(ARTWORK_FAVORITES_EVENT, `${Date.now()}:${state.revision}`);} catch {/* Local storage may be unavailable; the server remains authoritative. */}
}
function artworkRefreshHistory() {
  const state = artworkFavoritesState;
  if (!state.refreshNeeded) return;
  state.refreshNeeded = false;
  // Ordinary archive cards can update in place. A filtered view must be re-read
  // after the menu closes because membership changes can move its anchor away.
  if (artworkFolderQuery() && typeof loadHistory === "function" && true) void loadHistory(typeof historyPage === "undefined" ? 1 : historyPage);
}
async function artworkMutateMembership(imageIds, folderIds, action) {
  const ids = [...new Set(imageIds.map(String))],folders = [...new Set(folderIds.map(String))];
  if (!ids.length || !folders.length) throw new Error("请选择作品和收藏夹");
  const data = await request("/api/artwork-folders/members", { method: "POST", body: JSON.stringify({ image_ids: ids.map(Number), folder_ids: folders, action }) });
  artworkApplyPayload(data);return data;
}
async function artworkBatchMembership(action) {
  const state = artworkFavoritesState;
  if (state.busy || !state.selected.size || !artworkWritable()) return;
  const folders = action === "remove" ? [artworkFolderQuery()].filter(Boolean) : [...state.targets];
  if (!folders.length) return;
  const ids = [...state.selected.keys()];state.busy = true;artworkRenderToolbar();
  try {
    const data = await artworkMutateMembership(ids, folders, action);
    state.busy = false;artworkSetSelecting(false);artworkRefreshHistory();
    artworkNotify(data.changed_count ? action === "remove" ? `已从此收藏夹移出 ${data.updated_count} 张作品` : `已将 ${data.updated_count} 张作品加入收藏夹` : action === "remove" ? "所选作品已不在此收藏夹中" : "所选作品已在这些收藏夹中");
  } catch (error) {
    const payload = error.payload || {},missingImages = payload.missing_image_ids || [],missingFolders = payload.missing_folder_ids || [];
    if (payload.code === "artwork_collection_changed" && (missingImages.length || missingFolders.length)) {
      artworkForgetMissing(missingImages);
      for (const id of missingFolders) state.targets.delete(String(id));
      artworkNotify(`已移除 ${missingImages.length} 张失效作品和 ${missingFolders.length} 个失效收藏夹，其余选择已保留，可重试`);
      void artworkLoadFolders();artworkRenderCards();
    } else artworkNotify(`保存失败：${error.message}，已保留本次选择`);
  } finally
  {state.busy = false;artworkRenderToolbar();void artworkSyncExternal();}
}
function artworkPopoverHost(anchor) {return anchor?.closest?.("dialog[open]") || document.body;}
function positionArtworkPopover() {
  const state = artworkFavoritesState,current = state.popover,node = state.popoverNode;
  if (!current || !node || node.hidden) return;
  if (!current.anchor?.isConnected) {closeArtworkPopovers({ restoreFocus: false });return;}
  const host = artworkPopoverHost(current.anchor),bounds = host === document.body ?
    { left: 0, top: 0, width: document.documentElement.clientWidth, height: window.innerHeight } :
    host.getBoundingClientRect();
  const rect = current.anchor.getBoundingClientRect(),gap = 8,edge = 12;
  node.style.position = host === document.body ? "fixed" : "absolute";
  // Anchor to the settled box; the live rectangle includes the entry scale.
  const width = Math.min(current.mode === "manager" ? 360 : 306, bounds.width - edge * 2);
  node.style.width = `${width}px`;
  node.style.maxHeight = `${Math.max(120, bounds.height - edge * 2)}px`;
  const height = Math.min(node.scrollHeight || 340, bounds.height - edge * 2);
  const below = rect.bottom - bounds.top + gap;
  const top = below + height <= bounds.height - edge ? below : Math.max(edge, rect.top - bounds.top - gap - height);
  const left = Math.max(edge, Math.min(rect.right - bounds.left - width, bounds.width - width - edge));
  node.style.left = `${left}px`;node.style.top = `${top}px`;
  node.style.transformOrigin = `${Math.max(12, Math.min(width - 12, rect.left + rect.width / 2 - bounds.left - left))}px ${top >= rect.bottom - bounds.top ? "top" : "bottom"}`;
}
function openArtworkPopover(mode, anchor, record = null) {
  const state = artworkFavoritesState;
  if (!anchor || !artworkWritable()) return;
  if (state.popover?.anchor === anchor && state.popover.mode === mode) {closeArtworkPopovers();return;}
  closeArtworkPopovers({ restoreFocus: false, immediate: true, refresh: false });
  if (!state.popoverNode) {
    const node = artworkNode("section", "artwork-popover");node.id = "artworkPopover";node.hidden = true;node.setAttribute("role", "dialog");node.setAttribute("aria-modal", "false");node.setAttribute("aria-labelledby", "artworkPopoverTitle");
    node.addEventListener("keydown", artworkPopoverKeydown);state.popoverNode = node;
  }
  const token = ++state.popoverToken;
  state.popover = { mode, anchor, recordId: record ? String(record.id) : null, token };
  state.menuError = "";state.newName = "";state.editing = null;state.deleting = null;
  artworkPopoverHost(anchor).append(state.popoverNode);anchor.setAttribute("aria-expanded", "true");anchor.setAttribute("aria-controls", "artworkPopover");
  state.popoverNode.hidden = false;artworkRenderPopover();positionArtworkPopover();
  artworkPresence(state.popoverNode, true, { onComplete() {if (state.popover?.token === token) positionArtworkPopover();} });
  artworkFocus(state.popoverNode.querySelector("[data-artwork-focus]") || state.popoverNode.querySelector("button"));
  if (!state.loaded && !state.loading) void artworkLoadFolders();
  if (state.externalPending) void artworkSyncExternal();
}
function openArtworkMembership(record, anchor) {if (!record) return;artworkRememberRecord(record);openArtworkPopover("members", anchor, record);}
function openArtworkManager(anchor) {openArtworkPopover("manager", anchor);}
function closeArtworkPopovers({ restoreFocus = true, immediate = false, refresh = true } = {}) {
  const state = artworkFavoritesState,current = state.popover;
  if (!current) return false;
  artworkCancelDrag();state.popover = null;++state.popoverToken;
  if (state.sortAnimation) {
    if (typeof StudioMotion !== "undefined") StudioMotion.freeze(state.sortAnimation.row, "artwork-sort");
    state.sortAnimation.row.style.removeProperty("transform");state.sortAnimation = null;
  }
  current.anchor?.setAttribute("aria-expanded", "false");
  artworkPresence(state.popoverNode, false, { immediate });
  if (restoreFocus) artworkFocus(current.anchor);
  if (refresh) artworkRefreshHistory();
  return true;
}
function artworkPopoverKeydown(event) {
  if (event.key === "Escape") {event.preventDefault();event.stopPropagation();closeArtworkPopovers();return;}
  const state = artworkFavoritesState;
  if (event.key === "Tab") {
    const focusable = [...state.popoverNode.querySelectorAll("button, input")].filter((node) => !node.disabled && !node.hidden && !node.closest("[hidden], [inert]"));
    const index = focusable.indexOf(document.activeElement);
    if (!event.shiftKey && index === focusable.length - 1 || event.shiftKey && index === 0) {event.preventDefault();artworkFocus(focusable[event.shiftKey ? focusable.length - 1 : 0]);}
    return;
  }
  if (!state.popover || state.popover.mode !== "members" || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) || event.target.tagName === "INPUT") return;
  const choices = [...state.popoverNode.querySelectorAll("[data-member-folder]")].filter((node) => !node.disabled);
  if (!choices.length) return;
  event.preventDefault();const index = choices.indexOf(document.activeElement);
  const next = event.key === "Home" ? 0 : event.key === "End" ? choices.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + choices.length) % choices.length;
  artworkFocus(choices[next]);
}
function artworkRenderPopover() {
  const state = artworkFavoritesState,current = state.popover,node = state.popoverNode;
  if (!current || !node || state.drag) return;
  if (state.sortAnimation) {
    node.setAttribute("aria-busy", String(state.busy));
    for (const button of node.querySelectorAll("button, input")) if (!button.classList.contains("artwork-popover-close")) button.disabled = state.busy;
    return;
  }
  const focused = node.contains(document.activeElement) ? document.activeElement : null;
  if (focused?.dataset?.focusKey) current.focusKey = focused.dataset.focusKey;
  const focusKey = current.focusKey,selectionStart = focused?.selectionStart,selectionEnd = focused?.selectionEnd,scrollTop = node.scrollTop;
  const header = artworkNode("header", "artwork-popover-heading"),title = artworkNode("h2", "", current.mode === "manager" ? "管理收藏夹" : "收藏到");title.id = "artworkPopoverTitle";
  const close = artworkButton("×", "artwork-popover-close", () => closeArtworkPopovers());close.setAttribute("aria-label", "关闭收藏夹菜单");close.dataset.focusKey = "close";
  header.append(title, close);
  const content = artworkNode("div", "artwork-popover-content");
  if (state.loading && !state.loaded) content.append(artworkNode("p", "artwork-menu-hint", "正在加载收藏夹…"));
  if (state.loadError) {
    content.append(artworkNode("p", "artwork-menu-error", state.loadError), artworkButton("重试", "artwork-action", () => {void artworkLoadFolders();artworkRenderPopover();}));
  } else if (!state.folders.length && state.loaded) content.append(artworkNode("p", "artwork-menu-hint", "新建一个收藏夹，收好喜欢的作品。"));
  if (current.mode === "members") artworkRenderMembershipList(content);else artworkRenderManagerList(content);
  content.append(artworkNewFolderForm());
  const status = artworkNode("p", "artwork-menu-error", state.menuError);status.setAttribute("role", "status");status.hidden = !state.menuError;content.append(status);
  if (state.externalPending || state.externalSyncing) {
    const retry = artworkButton(state.externalSyncing ? "正在同步收藏…" : "重新同步收藏", "artwork-action", () => {void artworkSyncExternal();});
    retry.disabled = state.externalSyncing || state.busy;retry.dataset.focusKey = "sync-retry";content.append(retry);
  }
  node.replaceChildren(header, content);node.classList.toggle("is-manager", current.mode === "manager");node.setAttribute("aria-busy", String(state.busy));
  if (focusKey) {
    const target = [...node.querySelectorAll("[data-focus-key]")].find((item) => item.dataset.focusKey === focusKey);
    artworkFocus(target);if (target?.setSelectionRange && selectionStart != null) target.setSelectionRange(selectionStart, selectionEnd);
  }
  node.scrollTop = scrollTop;positionArtworkPopover();
}
function artworkRenderMembershipList(content) {
  const state = artworkFavoritesState,current = state.popover,ids = state.memberships.get(current.recordId) || [];
  const list = artworkNode("div", "artwork-membership-list");list.setAttribute("role", "group");list.setAttribute("aria-label", "此作品的收藏夹");
  for (const folder of state.folders) {
    const id = String(folder.id),selected = ids.includes(id);
    const button = artworkButton("", "artwork-membership-row", () => artworkToggleMembership(id));
    button.dataset.memberFolder = id;button.dataset.focusKey = `member:${id}`;button.setAttribute("data-artwork-focus", "");
    button.setAttribute("aria-pressed", String(selected));button.disabled = state.busy || state.externalPending || state.externalSyncing;
    const icon = artworkNode("span", "artwork-folder-symbol");icon.innerHTML = ARTWORK_FOLDER;icon.setAttribute("aria-hidden", "true");
    const label = artworkNode("span", "artwork-folder-name", folder.name),count = artworkNode("small", "", String(folder.count || 0));
    const check = artworkNode("span", "artwork-member-check", selected ? "✓" : "");check.setAttribute("aria-hidden", "true");
    button.append(icon, label, count, check);list.append(button);
  }
  content.append(list);
}
async function artworkToggleMembership(folderId) {
  const state = artworkFavoritesState,current = state.popover;
  if (!current || current.mode !== "members" || state.busy) return;
  if (state.externalPending || state.externalSyncing) {void artworkSyncExternal();return;}
  const action = (state.memberships.get(current.recordId) || []).includes(folderId) ? "remove" : "add";
  state.busy = true;state.menuError = "";artworkRenderPopover();artworkRenderToolbar();
  try {await artworkMutateMembership([current.recordId], [folderId], action);}
  catch (error) {if (state.popover?.token === current.token) state.menuError = error.message;else artworkNotify(`收藏保存失败：${error.message}`);} finally
  {
    state.busy = false;artworkRenderPopover();artworkRenderToolbar();
    if (!state.popover) artworkRefreshHistory();
    void artworkSyncExternal();
  }
}
function artworkNewFolderForm() {
  const state = artworkFavoritesState,form = artworkNode("form", "artwork-new-folder");
  const input = artworkNode("input");input.type = "text";input.maxLength = 80;input.placeholder = "新收藏夹名称";input.value = state.newName;input.dataset.focusKey = "new-folder";input.setAttribute("aria-label", "新收藏夹名称");input.disabled = state.busy;
  input.addEventListener("input", () => {state.newName = input.value;});
  const create = artworkButton(state.popover?.mode === "members" ? "新建并收藏" : "新建", "artwork-action");create.disabled = state.busy;create.dataset.focusKey = "new-folder-submit";
  create.type = "submit";form.append(input, create);form.addEventListener("submit", (event) => {event.preventDefault();void artworkCreateFolder();});return form;
}
async function artworkCreateFolder() {
  const state = artworkFavoritesState,current = state.popover,name = state.newName.trim();
  if (state.busy || !current) return;
  if (!name) {state.menuError = "请输入收藏夹名称";artworkRenderPopover();return;}
  state.busy = true;state.menuError = "";artworkRenderPopover();artworkRenderToolbar();
  try {
    const data = await request("/api/artwork-folders", { method: "POST", body: JSON.stringify({ name }) });
    artworkApplyPayload(data);state.newName = "";
    if (current.mode === "members" && data.folder?.id) await artworkMutateMembership([current.recordId], [String(data.folder.id)], "add");else
    if (state.selecting && data.folder?.id) state.targets.add(String(data.folder.id));
    if (state.popover === current) current.focusKey = "new-folder";
  } catch (error) {if (state.popover?.token === current.token) state.menuError = error.message;else artworkNotify(error.message);} finally
  {state.busy = false;artworkRenderPopover();artworkRenderToolbar();if (!state.popover) artworkRefreshHistory();void artworkSyncExternal();}
}
function artworkRenderManagerList(content) {
  const state = artworkFavoritesState,list = artworkNode("div", "artwork-manager-list");list.setAttribute("role", "list");
  for (const folder of state.folders) {
    const id = String(folder.id),row = artworkNode("div", "artwork-manager-row");row.dataset.folderId = id;row.setAttribute("role", "listitem");
    const handle = artworkButton("⠿", "artwork-drag-handle");handle.dataset.focusKey = `handle:${id}`;handle.disabled = state.busy;handle.setAttribute("aria-label", `排序 ${folder.name}，上下方向键移动`);handle.title = "拖动排序，也可用上下方向键";
    handle.addEventListener("pointerdown", (event) => artworkDragStart(event, id, row, handle));
    handle.addEventListener("keydown", (event) => {
      if (!["ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault();event.stopPropagation();void artworkMoveFolder(id, event.key === "ArrowUp" ? -1 : 1);
    });
    row.append(handle);
    if (state.editing === id) {
      const form = artworkNode("form", "artwork-rename-form"),input = artworkNode("input");input.value = state.editingName;input.maxLength = 80;input.setAttribute("aria-label", "收藏夹新名称");input.dataset.focusKey = `rename:${id}`;input.disabled = state.busy;
      input.addEventListener("input", () => {state.editingName = input.value;});
      const save = artworkButton("保存", "artwork-mini-action");save.type = "submit";save.disabled = state.busy;
      const cancel = artworkButton("取消", "artwork-mini-action", () => {state.editing = null;if (state.popover) state.popover.focusKey = `name:${id}`;artworkRenderPopover();});cancel.disabled = state.busy;
      form.append(input, save, cancel);form.addEventListener("submit", (event) => {event.preventDefault();void artworkRenameFolder(id);});row.append(form);
    } else {
      const label = artworkButton(folder.name, "artwork-manager-name", () => {state.editing = id;state.editingName = folder.name;state.deleting = null;artworkRenderPopover();artworkFocus(state.popoverNode.querySelector(".artwork-rename-form input"));});
      label.title = "重命名";label.dataset.focusKey = `name:${id}`;label.disabled = state.busy;
      const count = artworkNode("small", "artwork-manager-count", String(folder.count || 0));
      const remove = artworkButton("−", "artwork-folder-delete", () => {state.deleting = state.deleting === id ? null : id;state.editing = null;artworkRenderPopover();});remove.setAttribute("aria-label", `删除收藏夹 ${folder.name}`);remove.dataset.focusKey = `delete:${id}`;remove.disabled = state.busy;
      row.append(label, count, remove);
    }
    if (state.deleting === id) {
      const confirm = artworkNode("div", "artwork-delete-confirm");
      confirm.append(artworkNode("p", "", `删除“${folder.name}”？仅属于此夹的作品将取消收藏，其他收藏夹的归属会保留。作品和原图不受影响。`));
      const cancel = artworkButton("取消", "artwork-mini-action", () => {state.deleting = null;artworkRenderPopover();});
      const remove = artworkButton("删除收藏夹", "artwork-mini-action danger-text", () => artworkDeleteFolder(id));remove.disabled = cancel.disabled = state.busy;
      confirm.append(cancel, remove);row.append(confirm);
    }
    list.append(row);
  }
  content.append(list);
  if (state.folders.length > 1) content.append(artworkNode("p", "artwork-menu-hint", "拖动左侧手柄排序；聚焦手柄后也可按 ↑ ↓。"));
}
async function artworkRenameFolder(id) {
  const state = artworkFavoritesState,current = state.popover,name = state.editingName.trim();
  if (state.busy) return;
  if (!name) {state.menuError = "请输入收藏夹名称";artworkRenderPopover();return;}
  state.busy = true;state.menuError = "";artworkRenderPopover();
  try {
    const data = await request(`/api/artwork-folders/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ name }) });
    artworkApplyPayload(data);state.editing = null;
    if (current && state.popover === current) current.focusKey = `name:${id}`;
    if (typeof historyViewState === "function") artworkHistoryRendered(historyViewState());
  } catch (error) {if (state.popover === current) state.menuError = error.message;else artworkNotify(error.message);} finally
  {state.busy = false;artworkRenderPopover();artworkRenderToolbar();void artworkSyncExternal();}
}
async function artworkDeleteFolder(id) {
  const state = artworkFavoritesState,current = state.popover;
  if (state.busy) return;
  state.busy = true;state.menuError = "";artworkRenderPopover();
  try {
    const data = await request(`/api/artwork-folders/${encodeURIComponent(id)}`, { method: "DELETE" });
    const items = data.items || [...state.memberships].filter(([, folders]) => folders.includes(id)).map(([key, folders]) => ({ id: key, folder_ids: folders.filter((folder) => folder !== id) }));
    artworkApplyPayload({ ...data, items });state.targets.delete(id);state.deleting = null;
    if (current && state.popover === current) current.focusKey = "new-folder";
    if (artworkFolderQuery() === id) {
      historyDetailState.homeQuery = { ...historyDetailState.homeQuery, folderId: "" };
      state.refreshNeeded = false;invalidateHistoryView();void loadHistory(1);
    }
    artworkNotify("收藏夹已删除，作品和原图已保留");
  } catch (error) {if (state.popover === current) state.menuError = error.message;else artworkNotify(error.message);} finally
  {state.busy = false;artworkRenderPopover();artworkRenderToolbar();void artworkSyncExternal();}
}
async function artworkSaveOrder(ids) {
  const state = artworkFavoritesState;
  if (state.busy || ids.every((id, index) => id === String(state.folders[index]?.id))) return;
  const previous = state.folders,byId = new Map(previous.map((folder) => [String(folder.id), folder]));
  state.folders = ids.map((id) => byId.get(id)).filter(Boolean);
  state.busy = true;state.menuError = "";artworkRenderPopover();artworkRenderToolbar();
  try {artworkApplyPayload(await request("/api/artwork-folders/reorder", { method: "POST", body: JSON.stringify({ folder_ids: ids }) }));}
  catch (error) {state.folders = previous;state.menuError = `排序未保存：${error.message}`;} finally
  {state.busy = false;artworkRenderPopover();artworkRenderToolbar();void artworkSyncExternal();}
}
async function artworkMoveFolder(id, direction) {
  const state = artworkFavoritesState;if (state.busy) return;
  const ids = state.folders.map((folder) => String(folder.id)),index = ids.indexOf(id),next = index + direction;
  if (index < 0 || next < 0 || next >= ids.length) return;
  [ids[index], ids[next]] = [ids[next], ids[index]];await artworkSaveOrder(ids);
  const handle = [...(state.popoverNode?.querySelectorAll(".artwork-drag-handle") || [])].find((node) => node.dataset.focusKey === `handle:${id}`);artworkFocus(handle);
}
function artworkDragStart(event, id, row, handle) {
  const state = artworkFavoritesState;
  if (state.busy || event.button !== 0 || state.editing || state.deleting) return;
  const list = row.parentElement;
  const previous = typeof StudioMotion === "undefined" ? null : StudioMotion.freeze(row, "artwork-sort");
  state.drag = { id, row, handle, list, pointerId: event.pointerId, startY: event.clientY, startTop: row.getBoundingClientRect().top,
    offset: previous?.value?.y || 0, active: false, original: [...list.children], order: state.folders.map((folder) => String(folder.id)) };
  handle.setPointerCapture?.(event.pointerId);
  // Moving a captured element within the DOM can release pointer capture in
  // Chromium. Document listeners keep the release commit attached to the drag.
  document.addEventListener("pointermove", artworkDragMove, true);
  document.addEventListener("pointerup", artworkDragEnd, true);
  document.addEventListener("pointercancel", artworkCancelDrag, true);
}
function artworkDragMove(event) {
  const drag = artworkFavoritesState.drag;if (!drag || event.pointerId !== drag.pointerId) return;
  if (!drag.active && Math.abs(event.clientY - drag.startY) < 6) return;
  drag.active = true;event.preventDefault();drag.row.classList.add("is-dragging");
  const followPointer = () => {
    const layoutTop = drag.row.getBoundingClientRect().top - drag.offset;
    drag.offset = drag.startTop + event.clientY - drag.startY - layoutTop;
    drag.row.style.transform = `translateY(${drag.offset}px)`;
  };
  followPointer();
  const peers = [...drag.list.children].filter((node) => node !== drag.row);
  const before = peers.find((node) => event.clientY < node.getBoundingClientRect().top + node.getBoundingClientRect().height / 2) || null;
  const next = drag.row.nextElementSibling;
  if (next === before) return;
  const positions = new Map(peers.map((node) => [node, node.getBoundingClientRect().top]));
  const velocities = new Map();
  for (const node of peers) {
    if (typeof StudioMotion !== "undefined") velocities.set(node, StudioMotion.freeze(node, "artwork-sort")?.velocity || { y: 0 });
    node.style.removeProperty("transform");
  }
  drag.list.insertBefore(drag.row, before);
  followPointer();
  for (const node of peers) {
    const delta = positions.get(node) - node.getBoundingClientRect().top;
    if (delta && typeof StudioMotion !== "undefined") StudioMotion.spring(node, "artwork-sort", { y: 0 }, { initial: { y: delta }, velocity: velocities.get(node), response: .25, damping: 1, onUpdate: (value) => {node.style.transform = `translateY(${value.y}px)`;}, onComplete: () => node.style.removeProperty("transform") });
  }
}
function artworkDetachDrag() {
  const state = artworkFavoritesState,drag = state.drag;if (!drag) return null;
  state.drag = null;drag.row.classList.remove("is-dragging");
  document.removeEventListener("pointermove", artworkDragMove, true);document.removeEventListener("pointerup", artworkDragEnd, true);document.removeEventListener("pointercancel", artworkCancelDrag, true);
  if (drag.handle.hasPointerCapture?.(drag.pointerId)) drag.handle.releasePointerCapture(drag.pointerId);
  return drag;
}
function artworkDragEnd(event) {
  if (artworkFavoritesState.drag?.pointerId !== event.pointerId) return;
  const drag = artworkDetachDrag();
  if (!drag?.active) return;
  if (typeof StudioMotion !== "undefined") {
    const animation = { row: drag.row };artworkFavoritesState.sortAnimation = animation;
    StudioMotion.spring(drag.row, "artwork-sort", { y: 0 }, { initial: { y: drag.offset }, response: .3, damping: 1,
      onUpdate: (value) => {drag.row.style.transform = `translateY(${value.y}px)`;}, onComplete() {
        drag.row.style.removeProperty("transform");
        if (artworkFavoritesState.sortAnimation === animation) {artworkFavoritesState.sortAnimation = null;artworkRenderPopover();}
      }
    });
  } else drag.row.style.removeProperty("transform");
  void artworkSaveOrder([...drag.list.children].map((node) => node.dataset.folderId));
}
function artworkCancelDrag() {
  const drag = artworkDetachDrag();if (drag) {drag.row.style.removeProperty("transform");for (const node of drag.original) drag.list.append(node);}
}
function artworkRecordRemoved(id) {
  const state = artworkFavoritesState,key = String(id);
  state.missingRecords.add(key);
  state.records.delete(key);state.memberships.delete(key);state.selected.delete(key);
  if (state.popover?.recordId === key) closeArtworkPopovers({ restoreFocus: false });
  artworkRenderToolbar();void artworkLoadFolders();
}
function artworkForgetMissing(ids) {
  const state = artworkFavoritesState,missing = new Set(ids.map(String));
  if (!missing.size) return;
  for (const id of missing) {state.missingRecords.add(id);state.selected.delete(id);state.records.delete(id);state.memberships.delete(id);}
  const keep = (record) => !missing.has(String(record?.id));
  if (typeof historyItems !== "undefined") historyItems = historyItems.filter(keep);
  if (typeof historyCache !== "undefined") for (const page of historyCache.values()) page.items = (page.items || []).filter(keep);







  if (state.popover?.recordId && missing.has(state.popover.recordId)) closeArtworkPopovers({ restoreFocus: false });
  if (typeof selectedRecord !== "undefined" && selectedRecord && missing.has(String(selectedRecord.id))) {
    if (typeof closeImagePreview === "function") closeImagePreview({ immediate: true, restoreFocus: false });
    if (typeof ui !== "undefined" && ui.detailDialog?.open && typeof motionDialog === "function") motionDialog(ui.detailDialog, false, { restoreFocus: false });
    selectedRecord = null;
  }


  artworkRenderCards();
}
function artworkLeaveWorkbench() {
  closeArtworkPopovers({ restoreFocus: false, immediate: true });
  const state = artworkFavoritesState;
  state.selecting = false;state.selected.clear();state.targets.clear();artworkRenderCards();artworkRenderToolbar();
}
async function artworkSyncExternal() {
  const state = artworkFavoritesState;
  if (!state.externalPending || state.externalSyncing || state.busy) return;
  state.externalPending = false;state.externalSyncing = true;
  artworkRenderPopover();
  const revision = state.revision,ids = new Set([...state.records.keys(), ...state.selected.keys()]);
  const remember = (record) => {if (record?.id != null) ids.add(String(record.id));};
  if (typeof historyItems !== "undefined") historyItems.forEach(remember);
  if (typeof selectedRecord !== "undefined") remember(selectedRecord);

  let failed = false;
  try {
    const data = await request("/api/artwork-folders/members/query", { method: "POST", body: JSON.stringify({ image_ids: [...ids].map(Number) }) });
    if (revision !== state.revision || state.busy) {state.externalPending = true;return;}
    artworkForgetMissing(data.missing_image_ids || []);
    artworkApplyPayload(data, { broadcast: false });state.menuError = "";
    const valid = new Set(state.folders.map((folder) => String(folder.id)));
    state.targets = new Set([...state.targets].filter((id) => valid.has(id)));
    if (artworkFolderQuery() && !valid.has(artworkFolderQuery())) {
      historyDetailState.homeQuery = { ...historyDetailState.homeQuery, folderId: "" };
      state.refreshNeeded = false;invalidateHistoryView();void loadHistory(1);
    }
    artworkRenderCards();artworkRenderToolbar();artworkRenderPopover();
    if ((data.missing_image_ids || []).length && true) void loadHistory(typeof historyPage === "undefined" ? 1 : historyPage);else
    if (!state.popover) artworkRefreshHistory();
  } catch (error) {failed = true;state.externalPending = true;state.menuError = `收藏状态同步失败：${error.message}`;artworkRenderPopover();} finally
  {state.externalSyncing = false;artworkRenderPopover();if (state.externalPending && !failed && !state.busy) void artworkSyncExternal();}
}
function initializeArtworkFavorites() {
  const state = artworkFavoritesState;if (state.ready) return;
  state.ready = true;artworkBuildToolbar();artworkRenderToolbar();
  artworkElement("detailFavorite")?.addEventListener("click", (event) => {if (typeof selectedRecord !== "undefined" && selectedRecord) openArtworkMembership(selectedRecord, event.currentTarget);});
  document.addEventListener("pointerdown", (event) => {
    const current = state.popover;
    if (event.target === artworkElement("detailDialog")) return;
    if (current && !state.popoverNode.contains(event.target) && !current.anchor.contains(event.target)) closeArtworkPopovers({ restoreFocus: false });
  }, true);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.popover) {event.preventDefault();event.stopPropagation();closeArtworkPopovers();}
  }, true);
  window.addEventListener("resize", () => {artworkLayoutToolbar(artworkCaptureToolbar(), { immediate: true });positionArtworkPopover();});
  if (typeof ResizeObserver !== "undefined") {
    const root = artworkElement("artworkToolbar");
    if (root) {
      artworkToolbarMotion.observer = new ResizeObserver((entries) => {
        const width = entries[0]?.contentRect.width || 0;
        if (Math.abs(width - artworkToolbarMotion.observedWidth) < .5) return;
        artworkToolbarMotion.observedWidth = width;
        if (width <= 0) return;
        artworkLayoutToolbar(artworkCaptureToolbar(), { immediate: true });
      });
      artworkToolbarMotion.observer.observe(root);
    }
  }
  document.addEventListener("scroll", positionArtworkPopover, true);
  window.addEventListener("storage", (event) => {
    if (event.key !== ARTWORK_FAVORITES_EVENT) return;
    state.externalPending = true;void artworkSyncExternal();
  });
  void artworkLoadFolders();
}
