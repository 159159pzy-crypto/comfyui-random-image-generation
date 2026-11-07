/* History filtering and the ordinary image detail/delete surface. */
const historyDetailState = {
  ready: false, busy: false, view: "detail", record: null, owner: null,
  homeQuery: { source: "", folderId: "" }, viewRevision: 0,
  viewFocus: {}, viewFocusFallback: {}, viewOffsets: new Map()
};
const historyDetailElement = (id) => document.getElementById(id);
const HISTORY_DETAIL_VIEWS = { detail: "historyDetailView", delete: "historyDeletePanel" };
function historyDetailFocus(element) {
  if (!element || element.isConnected === false || element.disabled || element.hidden || element.inert || element.closest?.("[hidden], [inert]")) return false;
  if (element.getClientRects && !element.getClientRects().length) return false;
  element.focus({ preventScroll: true });return true;
}
function historyDetailPresence(element, visible, { immediate = false, x = 0, y = 6, onComplete } = {}) {
  if (!element) return;
  if (typeof StudioMotion === "undefined") {element.hidden = !visible;element.inert = !visible;onComplete?.();return;}
  return StudioMotion.presence(element, visible, { from: { opacity: 0, x, y }, response: .3, damping: 1, immediate, onComplete });
}
function historyQueryParams(view = historyViewState()) {
  return (view.query.source ? `&workbench=${encodeURIComponent(view.query.source)}` : "") + (
  view.query.folderId ? `&folder_id=${encodeURIComponent(view.query.folderId)}` : "");
}
function historyQueryKey(view = historyViewState()) {return `${view.kind}:${view.queryEpoch}:${historyQueryParams(view)}`;}
function historyDetailWritable() {return config?.storage_status?.history?.available !== false && !config?.storage_status?.history?.read_only;}
function invalidateHistoryView(view = historyViewState()) {
  ++view.queryEpoch;view.cache.clear();++view.requestId;view.dirty = true;
  if (typeof imagePreview !== "undefined" && (!imagePreview.context || previewHistoryView() === view)) {
    const context = imagePreview.context,navigating = imagePreview.navigationCursor && ["switching", "opening"].includes(imagePreview.phase);
    const requestId = ++imagePreview.navigationRequest;
    imagePreview.navigationCursor = null;
    if (typeof clearPreviewCache === "function") clearPreviewCache();
    // A completed batch can change page boundaries while a neighbor is decoding.
    // Restore its last committed bitmap; an obsolete cursor must neither repaint
    // a cached page nor leave a cancelled fade invisible or stuck in switching.
    if (navigating && typeof restorePreviewCommittedImage === "function") {
      void Promise.resolve(restorePreviewCommittedImage(requestId, context)).then(() => {
        if (requestId !== imagePreview.navigationRequest || context !== imagePreview.context || ["closing", "closed"].includes(imagePreview.phase)) return;
        imagePreview.phase = "open";
        ui.imagePreviewDialog.classList.remove("is-navigating");
        updatePreviewNavigation();
      });
    }
  }
}
function setHistoryDetailView(view, { immediate = false, returning = false, focus = true, focusTarget = null } = {}) {
  const state = historyDetailState,dialog = historyDetailElement("detailDialog");
  if (!Object.hasOwn(HISTORY_DETAIL_VIEWS, view)) return;
  const previous = state.view,changed = previous !== view,revision = ++state.viewRevision;
  immediate ||= !dialog?.open;
  if (changed) {
    state.viewFocus[previous] = document.activeElement;
    const actionPanel = document.activeElement?.closest?.("[data-detail-panel]");
    state.viewFocusFallback[previous] = actionPanel ? document.querySelector?.(`[data-detail-action="${actionPanel.dataset.detailPanel}"]`) : null;
  }
  if (previous === "detail" && changed) {
    if (typeof invalidateHistoryTransfer === "function") invalidateHistoryTransfer();
    if (typeof closeDetailActionPanels === "function") closeDetailActionPanels({ immediate: true });
    if (typeof closeRegenerationDrawers === "function") closeRegenerationDrawers(false, true);
  }
  state.view = view;
  if (dialog) {
    dialog.classList.toggle("is-history-delete", view === "delete");
    dialog.dataset.historyView = view;
    const title = view === "delete" ? "删除作品" : "作品详情";
    dialog.setAttribute("aria-label", title);
    if (historyDetailElement("historyDetailTitle")) historyDetailElement("historyDetailTitle").textContent = title;
    if (view !== "detail") dialog.classList.remove("is-regenerating");
    if (changed) dialog.scrollTop = 0;
  }
  const back = historyDetailElement("historyDetailBack");
  if (back) {back.hidden = view === "detail";back.textContent = "返回";}
  for (const [name, id] of Object.entries(HISTORY_DETAIL_VIEWS)) {
    const node = historyDetailElement(id);if (!node) continue;
    const visible = view === name,running = typeof StudioMotion !== "undefined" && StudioMotion.snapshot(node, "presence")?.active;
    // A reversal keeps the existing spatial path as well as its value/velocity.
    // Changing the path while a spring is active would itself create a jump.
    if (!running) state.viewOffsets.set(name, (returning ? -1 : 1) * (visible ? 12 : -12));
    node.classList.toggle("history-view-exiting", !visible && !node.hidden && !immediate);
    historyDetailPresence(node, visible, { immediate: immediate || !changed && !running, x: state.viewOffsets.get(name), y: 0,
      onComplete() {if (revision === state.viewRevision) node.classList.remove("history-view-exiting");}
    });
  }
  if (changed && focus && dialog?.open) {
    const target = focusTarget || (returning ? state.viewFocus[view] : null);
    if (!historyDetailFocus(target) && !(returning && historyDetailFocus(state.viewFocusFallback[view]))) historyDetailFocus(historyDetailElement("historyDetailTitle")) || historyDetailFocus(back);
  }
}

function showHistoryDetail() {setHistoryDetailView("detail");}
function historyDetailVisible() {return historyDetailState.view === "detail" && Boolean(historyDetailElement("detailDialog")?.open);}
function historyDetailBack() {
  if (historyDetailState.busy) return true;
  if (historyDetailState.view !== "delete" || !selectedRecord) return false;
  renderDetail(selectedRecord);setHistoryDetailView("detail", { returning: true });return true;
}
function resetHistoryDetail() {
  historyDetailState.record = historyDetailState.owner = null;
  setHistoryDetailView("detail", { immediate: true, focus: false });
  historyDetailState.viewFocus = {};historyDetailState.viewFocusFallback = {};
}
function revealHistoryWorkbench() {
  const dialog = historyDetailElement("detailDialog");
  if (dialog?.open) motionDialog(dialog, false, { immediate: true, restoreFocus: false });
  resetHistoryDetail();
}
function openHistoryDelete(record) {
  if (!record || historyDetailState.busy || !historyDetailWritable()) return;
  historyDetailState.record = record;historyDetailState.owner = detailContext;
  historyDetailElement("historyDeleteMode").value = "record";
  historyDetailElement("historyDeleteResults").textContent = "";
  renderHistoryDelete();setHistoryDetailView("delete", { focusTarget: historyDetailElement("historyDeleteMode") });
}
function renderHistoryDelete() {
  const state = historyDetailState,permanent = historyDetailElement("historyDeleteMode").value === "file";
  historyDetailElement("historyDeleteSummary").textContent = state.record ? `删除作品：${state.record.filename || `#${state.record.id}`}` : "作品已删除。";
  historyDetailElement("historyDeleteWarning").textContent = permanent ? "原图将永久删除，不进入回收站。文件无法访问或仍被其他作品引用时，会保留记录并说明原因。" : "仅删除作品记录和缩略图；原图文件保留。";
  const button = historyDetailElement("historyDeleteSubmit");
  button.textContent = permanent ? "永久删除原图及记录" : "只删除记录";
  button.disabled = !state.record || state.busy || !historyDetailWritable();
  historyDetailElement("historyDeleteMode").disabled = state.busy;
}
function removeHistoryViewRecord(view, record) {
  const key = String(record.id),cached = [...view.cache.values()],elements = view.elements;
  const matches = (!view.query.source || (record.workbench || record.settings?.workbench || "random") === view.query.source) && (
  !view.query.folderId || (record.folder_ids || []).includes(view.query.folderId));
  const present = view.items.some((item) => String(item.id) === key);
  const total = Number(view.cache.get(view.page)?.total ?? view.total);
  view.items = view.items.filter((item) => String(item.id) !== key);
  for (const page of cached) page.items = (page.items || []).filter((item) => String(item.id) !== key);
  for (const node of [...(elements?.gallery?.children || [])]) if (node.dataset.recordId === key) {
    if (typeof card !== "undefined") card.visibilityObserver?.unobserve(node);
    node.remove();
  }
  if (Number.isFinite(total) && (present || matches)) {
    view.total = Math.max(0, total - 1);view.pages = Math.max(1, Math.ceil(view.total / 24));view.page = Math.min(view.page, view.pages);
    if (elements?.historyCount) elements.historyCount.textContent = `${view.total} 张图片`;
    if (elements?.pageLabel) elements.pageLabel.textContent = `${view.page} / ${view.pages}`;
    if (elements?.prevPage) elements.prevPage.disabled = view.page <= 1;
    if (elements?.nextPage) elements.nextPage.disabled = view.page >= view.pages;
  }
  if (elements?.emptyState) elements.emptyState.hidden = view.items.length > 0;
  invalidateHistoryView(view);
}
async function applyHistoryDeleted(record, owner) {
  const id = String(record.id),view = historyViewState(),warnings = [];
  removeHistoryViewRecord(view, record);
  if (typeof artworkRecordRemoved === "function") artworkRecordRemoved(record.id);
  const ownsDetail = () => detailContext === owner && String(selectedRecord?.id) === id;
  if (ownsDetail()) closeImagePreview({ immediate: true, restoreFocus: false });
  let neighbor = null;
  try {}
  catch (error) {warnings.push(`单图列表刷新失败：${error.message}`);}
  if (ownsDetail()) {


    selectedRecord = null;motionDialog(ui.detailDialog, false);
  }
  historyDirty = true;
  // A failed refresh cannot restore the committed deletion from old caches.

  try {
    const data = await loadHistory(view.page, view);
    if (!data) warnings.push("作品列表刷新失败，删除已完成；可稍后刷新");
  } catch (error) {warnings.push(`作品列表刷新失败：${error.message}；删除已完成`);}

  return warnings;
}
async function confirmHistoryDelete(event) {
  event.preventDefault();
  const state = historyDetailState,record = state.record,owner = state.owner;
  if (!record || state.busy || !historyDetailWritable()) return;
  const mode = historyDetailElement("historyDeleteMode").value;
  state.busy = true;renderHistoryDelete();
  const results = historyDetailElement("historyDeleteResults");results.textContent = "正在删除…";
  try {
    let response;
    try {response = await request(`/api/history/${encodeURIComponent(record.id)}?mode=${mode}`, { method: "DELETE" });}
    catch (error) {
      if (error.status === 404) response = { deleted: true };else
      {
        const failure = error.payload?.results?.[0];
        if (failure) throw new Error(`${failure.error || failure.code || error.message}${failure.file_deleted ? "（原文件已删除，记录未删除；可重试完成记录删除）" : ""}`);
        throw error;
      }
    }
    if (!response.deleted) throw new Error(response.results?.[0]?.error || "删除未完成，请重试");
    state.record = null;
    const warnings = await applyHistoryDeleted(record, owner);
    toast(`已删除作品${mode === "file" ? "及对应原文件" : "记录，原文件保留"}${warnings.length ? `。${warnings.join("；")}` : ""}`);
  } catch (error) {
    results.textContent = state.record ? `${error.message}。可重试。` : `删除已完成，但界面刷新失败：${error.message}`;
  } finally {state.busy = false;renderHistoryDelete();}
}
function initializeHistoryDetail() {
  if (historyDetailState.ready) return;
  historyDetailState.ready = true;
  historyDetailElement("historyDetailBack").addEventListener("click", historyDetailBack);
  for (const button of document.querySelectorAll("[data-history-back]")) button.addEventListener("click", historyDetailBack);
  historyDetailElement("historyDeleteForm").addEventListener("submit", confirmHistoryDelete);
  historyDetailElement("historyDeleteMode").addEventListener("change", renderHistoryDelete);
}
