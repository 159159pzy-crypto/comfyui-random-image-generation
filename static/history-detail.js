/* History filtering and the ordinary image detail/delete surface. */
const historyDetailState = {
  ready: false, busy: false, record: null, owner: null, session: 0, fileOutcome: null, batch: null,
  homeQuery: { source: "", folderId: "" },
};
const historyDetailElement = id => document.getElementById(id);
const HISTORY_SOURCES = new Set(["", "random"]);
function historyDetailFocus(element) {
  if (!element || element.isConnected === false || element.disabled || element.hidden || element.inert || element.closest?.("[hidden], [inert]")) return false;
  if (element.getClientRects && !element.getClientRects().length) return false;
  element.focus({ preventScroll: true }); return true;
}
function renderHistorySource({ immediate = false } = {}) {
  const group = historyDetailElement("historyWorkbenchFilter");
  const buttons = [...(group?.querySelectorAll?.("[data-history-source]") || [])];
  for (const button of buttons) {
    const checked = button.dataset.historySource === historyDetailState.homeQuery.source;
    button.setAttribute("aria-checked", String(checked)); button.tabIndex = checked ? 0 : -1;
  }
  const indicator = group?.querySelector?.(".history-source-thumb"), selected = buttons.find(button => button.dataset.historySource === historyDetailState.homeQuery.source);
  if (indicator && selected && typeof animateStudioIndicator === "function") animateStudioIndicator(indicator, { x: selected.offsetLeft - indicator.offsetLeft, width: selected.offsetWidth }, { immediate });
}
function historyQueryParams(view = historyViewState()) {
  return (view.query.source ? `&workbench=${encodeURIComponent(view.query.source)}` : "")
    + (view.query.folderId ? `&folder_id=${encodeURIComponent(view.query.folderId)}` : "")
    + (typeof historyFiltersQueryParams === "function" ? historyFiltersQueryParams(view) : "");
}
function historyQueryKey(view = historyViewState()) { return `${view.kind}:${view.queryEpoch}:${historyQueryParams(view)}`; }
function setHistorySource(source) {
  if (!HISTORY_SOURCES.has(source) || source === historyDetailState.homeQuery.source) return;
  historyDetailState.homeQuery = { ...historyDetailState.homeQuery, source };
  if (typeof historyFiltersPersist === "function") historyFiltersPersist();
  invalidateHistoryView(); renderHistorySource(); void loadHistory(1);
}
function historyDetailWritable() { return config?.storage_status?.history?.available !== false && !config?.storage_status?.history?.read_only; }
function invalidateHistoryView(view = historyViewState()) {
  ++view.queryEpoch; view.cache.clear(); ++view.requestId; view.dirty = true;
  if (typeof imagePreview !== "undefined" && (!imagePreview.context || previewHistoryView() === view)) {
    const context = imagePreview.context, navigating = imagePreview.navigationCursor && ["switching", "opening"].includes(imagePreview.phase);
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
function showHistoryDetail() { resetHistoryDetail(); }
function historyDetailVisible() {
  return Boolean(historyDetailElement("detailDialog")?.open) && !historyDetailElement("historyDeleteDialog")?.open;
}
function closeHistoryDelete({ restoreFocus = true, immediate = false, force = false } = {}) {
  const state = historyDetailState, dialog = historyDetailElement("historyDeleteDialog");
  if (!force && (state.busy || (!state.record && !state.batch))) return false;
  const record = state.record, owner = state.owner, batch = state.batch, session = ++state.session;
  state.record = state.owner = state.fileOutcome = state.batch = null;
  renderHistoryDelete();
  if (!dialog?.open) return false;
  const onComplete = () => {
    if (batch) {
      if (restoreFocus && session === state.session && !dialog.open) historyDetailFocus(historyDetailElement("artworkBatchDelete")) || historyDetailFocus(historyDetailElement("artworkSelect"));
      return;
    }
    if (!restoreFocus || session !== state.session || dialog.open || !historyDetailElement("detailDialog")?.open
      || detailContext !== owner || String(selectedRecord?.id) !== String(record?.id)) return;
    historyDetailFocus(document.querySelector('[data-detail-action="more"]')) || historyDetailFocus(historyDetailElement("historyDetailTitle"));
  };
  motionDialog(dialog, false, { immediate, restoreFocus: false, onComplete });
  if (typeof StudioMotion === "undefined") onComplete();
  return true;
}
function resetHistoryDetail() { closeHistoryDelete({ immediate: true, restoreFocus: false, force: true }); }
function revealHistoryWorkbench() {
  const dialog = historyDetailElement("detailDialog");
  if (dialog?.open) motionDialog(dialog, false, { immediate: true, restoreFocus: false });
  resetHistoryDetail();
}
function openHistoryDelete(record) {
  if (!record || historyDetailState.busy || !historyDetailWritable()) return;
  const state = historyDetailState;
  ++state.session; state.record = record; state.owner = detailContext; state.fileOutcome = null; state.batch = null;
  if (typeof invalidateHistoryTransfer === "function") invalidateHistoryTransfer();
  if (typeof closeArtworkPopovers === "function") closeArtworkPopovers({ restoreFocus: false, immediate: true });
  if (typeof closeDetailActionPanels === "function") closeDetailActionPanels({ immediate: true });
  if (typeof closeRegenerationDrawers === "function") closeRegenerationDrawers(false, true);
  const results = historyDetailElement("historyDeleteResults");
  results.textContent = ""; results.classList.remove("is-error");
  renderHistoryDelete();
  motionDialog(historyDetailElement("historyDeleteDialog"), true, { restoreFocus: false });
  historyDetailFocus(historyDetailElement("cancelHistoryDelete"));
}
function openHistoryBatchDelete() {
  const state = historyDetailState, selection = artworkFavoritesState;
  if (state.busy || selection.busy || !selection.selecting || !selection.selected.size || !historyDetailWritable()) return;
  ++state.session; state.record = state.owner = state.fileOutcome = null;
  state.batch = { records: [...selection.selected.values()], fileOutcomes: new Map(), results: new Map() };
  closeArtworkPopovers({ restoreFocus: false, immediate: true });
  const results = historyDetailElement("historyDeleteResults");
  results.textContent = ""; results.classList.remove("is-error");
  renderHistoryDelete();
  motionDialog(historyDetailElement("historyDeleteDialog"), true, { restoreFocus: false });
  historyDetailFocus(historyDetailElement("cancelHistoryDelete"));
}
function renderHistoryDelete() {
  const state = historyDetailState;
  // Keep the filename visible while a closing animation finishes.
  if (state.record) historyDetailElement("historyDeleteSummary").textContent = state.record.filename || `作品 #${state.record.id}`;
  if (state.record || state.batch) historyDetailElement("historyDeleteTitle").textContent = state.batch ? "批量删除作品" : "删除作品";
  if (state.batch) {
    const visible = new Set((typeof historyItems === "undefined" ? [] : historyItems).map(record => String(record.id)));
    const hidden = state.batch.records.filter(record => !visible.has(String(record.id))).length;
    historyDetailElement("historyDeleteSummary").textContent = `已选 ${state.batch.records.length} 张作品${hidden ? `，其中 ${hidden} 张在其他页面或来源` : ""}。请选择删除方式。`;
  }
  for (const id of ["historyDeleteRecord", "historyDeleteFile"]) {
    historyDetailElement(id).disabled = !(state.record || state.batch?.records.length) || state.busy || !historyDetailWritable();
  }
  for (const id of ["closeHistoryDelete", "cancelHistoryDelete"]) historyDetailElement(id).disabled = state.busy;
  historyDetailElement("historyDeleteDialog").setAttribute("aria-busy", String(state.busy));
}
function containHistoryDeleteFocus(event) {
  const dialog = historyDetailElement("historyDeleteDialog");
  if (event.defaultPrevented || event.key !== "Tab" || !dialog.open) return;
  const buttons = [...dialog.querySelectorAll("button")].filter(button => !button.disabled && button.getClientRects().length);
  const index = buttons.indexOf(document.activeElement);
  if (!buttons.length) { event.preventDefault(); historyDetailFocus(historyDetailElement("historyDeleteResults")); }
  else if (index < 0 || (event.shiftKey ? index === 0 : index === buttons.length - 1)) {
    event.preventDefault(); historyDetailFocus(event.shiftKey ? buttons.at(-1) : buttons[0]);
  }
}
function removeHistoryViewRecord(view, record) { removeHistoryViewRecords(view, [record]); }
function removeHistoryViewRecords(view, records) {
  const unique = new Map(records.map(record => [String(record.id), record]));
  const cached = [...view.cache.values()], elements = view.elements;
  const present = new Set(view.items.map(item => String(item.id)));
  const removedCount = [...unique].filter(([id, record]) => present.has(id) || (
    (!view.query.source || (record.workbench || record.settings?.workbench || "random") === view.query.source)
    && (!view.query.folderId || (record.folder_ids || []).includes(view.query.folderId)))).length;
  const total = Number(view.cache.get(view.page)?.total ?? view.total);
  view.items = view.items.filter(item => !unique.has(String(item.id)));
  for (const page of cached) page.items = (page.items || []).filter(item => !unique.has(String(item.id)));
  for (const node of [...(elements?.gallery?.children || [])]) if (unique.has(node.dataset.recordId)) {
    if (typeof ArtworkThumbnails !== "undefined") ArtworkThumbnails.remove(node);
    node.remove();
  }
  // Group rules are resolved by the server and can change independently of a
  // selected record. Do not guess whether off-page deleted records matched.
  const filtered = typeof historyFiltersActive === "function" && historyFiltersActive(view);
  if (!filtered && Number.isFinite(total) && removedCount) {
    view.total = Math.max(0, total - removedCount); view.pages = Math.max(1, Math.ceil(view.total / 24)); view.page = Math.min(view.page, view.pages);
    if (elements?.historyCount) elements.historyCount.textContent = `${view.total} 张图片`;
    if (elements?.pageLabel) elements.pageLabel.textContent = `${view.page} / ${view.pages}`;
    if (elements?.prevPage) elements.prevPage.disabled = view.page <= 1;
    if (elements?.nextPage) elements.nextPage.disabled = view.page >= view.pages;
  }
  if (elements?.emptyState) elements.emptyState.hidden = view.items.length > 0;
  if (unique.size && typeof historyFiltersHistoryChanged === "function") historyFiltersHistoryChanged();
  invalidateHistoryView(view);
}
async function applyHistoryDeleted(record, owner, { beforeDetailUpdate } = {}) {
  const id = String(record.id), view = historyViewState(), warnings = [];
  removeHistoryViewRecord(view, record);
  if (typeof artworkRecordRemoved === "function") artworkRecordRemoved(record.id);
  const ownsDetail = () => detailContext === owner && String(selectedRecord?.id) === id;
  if (ownsDetail()) closeImagePreview({ immediate: true, restoreFocus: false });
  beforeDetailUpdate?.();
  if (ownsDetail()) { selectedRecord = null; motionDialog(ui.detailDialog, false); }
  historyDirty = true;
  try {
    const data = await loadHistory(view.page, view);
    if (!data) warnings.push("作品列表刷新失败，删除已完成；可稍后刷新");
  } catch (error) { warnings.push(`作品列表刷新失败：${error.message}；删除已完成`); }
  return warnings;
}
async function applyHistoryBatchDeleted(records) {
  if (!records.length) return [];
  const view = historyViewState(), warnings = [];
  removeHistoryViewRecords(view, records);
  if (typeof artworkRecordsRemoved === "function") {
    try { warnings.push(...await artworkRecordsRemoved(records.map(record => record.id))); }
    catch (error) { warnings.push(`作品已删除，收藏状态刷新失败：${error.message}`); }
  }
  historyDirty = true;
  try {
    if (!await loadHistory(view.page, view)) warnings.push("作品已删除，作品列表刷新失败，可稍后刷新");
  } catch (error) { warnings.push(`作品已删除，列表刷新失败：${error.message}`); }
  return warnings;
}
function historyDeleteSuccessMessage(mode, result = {}) {
  if (result.code === "already_missing") return "作品记录已不存在，列表已更新";
  if (result.file_deleted) return "记录及原图已删除";
  if (result.file_missing) return "记录已删除，原图原已不存在";
  if (mode === "record") return "记录已删除，原图保留";
  return "记录已删除";
}
function historyDeleteFailureMessage(error) {
  const result = error.payload?.results?.[0];
  let message = String(result?.error || error.message || "删除未完成").replace(/[。；\s]+$/, "");
  if (result?.file_deleted && !result.record_deleted && !/原(?:图|文件)已删除/.test(message)) {
    message += "。原图已删除，但记录仍保留；可重试完成记录删除";
  }
  if (!message.includes("重试")) message += "。可重试";
  return `${message}。`;
}
async function confirmHistoryDelete(mode) {
  if (historyDetailState.batch) return confirmHistoryBatchDelete(mode);
  const state = historyDetailState, record = state.record, owner = state.owner, session = state.session;
  if (!["record", "file"].includes(mode) || !record || state.busy || !historyDetailWritable()) return;
  if (detailContext !== owner || String(selectedRecord?.id) !== String(record.id)) {
    closeHistoryDelete({ immediate: true, restoreFocus: false }); return;
  }
  const current = () => state.session === session && state.record === record && state.owner === owner;
  const results = historyDetailElement("historyDeleteResults"), button = historyDetailElement(mode === "file" ? "historyDeleteFile" : "historyDeleteRecord");
  results.textContent = "正在删除…"; results.classList.remove("is-error");
  state.busy = true; button.setAttribute("aria-busy", "true"); renderHistoryDelete();
  historyDetailFocus(results);
  try {
    let response;
    try { response = await request(`/api/history/${encodeURIComponent(record.id)}?mode=${mode}`, { method: "DELETE" }); }
    catch (error) {
      if (error.status === 404) response = { deleted: true, results: [{ ...error.payload?.results?.[0], code: "already_missing" }] };
      else throw error;
    }
    if (!response.deleted) {
      const error = new Error("删除未完成"); error.payload = response; throw error;
    }
    const result = { ...response.results?.[0] };
    // A record-only retry must not claim to preserve a file removed on the prior attempt.
    if (state.fileOutcome === "deleted" && current()) result.file_deleted = true;
    else if (state.fileOutcome === "missing" && current()) result.file_missing = true;
    const warnings = result.warning ? [result.warning] : [];
    const closeCommitted = () => { if (current()) closeHistoryDelete({ immediate: true, restoreFocus: false, force: true }); };
    if (current()) results.textContent = "删除已完成，正在更新作品…";
    try { warnings.push(...await applyHistoryDeleted(record, owner, { beforeDetailUpdate: closeCommitted })); }
    catch (error) { warnings.push(`删除已完成，但界面刷新失败：${error.message}`); }
    finally { closeCommitted(); }
    toast([historyDeleteSuccessMessage(mode, result), ...new Set(warnings)].join("。"));
  } catch (error) {
    const message = historyDeleteFailureMessage(error);
    if (current()) {
      const failure = error.payload?.results?.[0];
      if (failure?.file_deleted) state.fileOutcome = "deleted";
      else if (failure?.file_missing && state.fileOutcome !== "deleted") state.fileOutcome = "missing";
      results.textContent = message; results.classList.add("is-error");
    }
    else toast(message);
  } finally {
    state.busy = false; button.removeAttribute("aria-busy"); renderHistoryDelete();
    if (current()) historyDetailFocus(button);
  }
}
function historyBatchDeleteMessage(batch) {
  const results = [...batch.results.values()];
  const count = key => results.filter(result => result[key]).length;
  const missing = results.filter(result => result.code === "already_missing").length;
  const messages = [`已删除 ${count("record_deleted")} 张作品记录`];
  if (missing) messages.push(`${missing} 张记录已不存在`);
  if (count("file_deleted")) messages.push(`已删除 ${count("file_deleted")} 张原图`);
  if (count("file_missing")) messages.push(`${count("file_missing")} 张原图原已不存在`);
  if (batch.records.length) messages.push(`${batch.records.length} 张未完成，已保留选择供重试`);
  return messages.join("，");
}
async function confirmHistoryBatchDelete(mode) {
  const state = historyDetailState, batch = state.batch, selection = artworkFavoritesState, session = state.session;
  if (!["record", "file"].includes(mode) || !batch?.records.length || state.busy || selection.busy || !historyDetailWritable()) return;
  const current = () => state.session === session && state.batch === batch;
  const records = [...batch.records], results = historyDetailElement("historyDeleteResults");
  const button = historyDetailElement(mode === "file" ? "historyDeleteFile" : "historyDeleteRecord");
  state.busy = selection.busy = true; button.setAttribute("aria-busy", "true");
  results.textContent = `正在删除 ${records.length} 张作品…`; results.classList.remove("is-error");
  renderHistoryDelete(); artworkRenderToolbar(); historyDetailFocus(results);
  let finishSelection = false;
  try {
    const response = await request("/api/history/delete", { method: "POST", body: JSON.stringify({ image_ids: records.map(record => Number(record.id)), mode }) });
    const returned = new Map((response.results || []).map(result => [String(result.id), result]));
    const successful = [], failures = [], warnings = [], explanations = [];
    for (const record of records) {
      const id = String(record.id), result = { ...(returned.get(id) || { id: record.id, error: "未收到此作品的删除结果" }) };
      if (result.file_deleted) batch.fileOutcomes.set(id, "deleted");
      else if (result.file_missing && batch.fileOutcomes.get(id) !== "deleted") batch.fileOutcomes.set(id, "missing");
      if (batch.fileOutcomes.get(id) === "deleted") { result.file_deleted = true; result.file_missing = false; }
      else if (batch.fileOutcomes.get(id) === "missing") result.file_missing = true;
      batch.results.set(id, result);
      if (result.warning) warnings.push(result.warning);
      if (result.record_deleted || result.code === "already_missing") {
        successful.push(record); selection.selected.delete(id);
      } else {
        failures.push(record);
        explanations.push(`${record.filename || `作品 #${record.id}`}：${historyDeleteFailureMessage({ payload: { results: [result] } })}`);
      }
    }
    batch.records = failures;
    try { warnings.push(...await applyHistoryBatchDeleted(successful)); }
    catch (error) { warnings.push(`删除结果已确认，但界面刷新失败：${error.message}`); }
    const message = [historyBatchDeleteMessage(batch), ...new Set(warnings)].join("。 ");
    if (!failures.length) {
      finishSelection = current();
      if (current()) closeHistoryDelete({ immediate: true, restoreFocus: false, force: true });
      toast(message);
    } else if (current()) {
      results.textContent = [message, ...explanations].join("\n"); results.classList.add("is-error");
    } else toast([message, ...explanations].join("\n"));
  } catch (error) {
    const message = `未能确认删除结果：${error.message}。已保留待处理选择，可重试。`;
    if (current()) { results.textContent = message; results.classList.add("is-error"); }
    else toast(message);
  } finally {
    state.busy = selection.busy = false; button.removeAttribute("aria-busy");
    renderHistoryDelete();
    if (finishSelection) artworkSetSelecting(false); else artworkRenderToolbar();
    if (current()) historyDetailFocus(button);
    void artworkSyncExternal();
  }
}
function initializeHistoryDetail() {
  if (historyDetailState.ready) return;
  historyDetailState.ready = true;
  const dialog = historyDetailElement("historyDeleteDialog");
  for (const button of dialog.querySelectorAll("[data-history-delete-mode]")) {
    button.addEventListener("click", () => { void confirmHistoryDelete(button.dataset.historyDeleteMode); });
  }
  for (const id of ["closeHistoryDelete", "cancelHistoryDelete"]) historyDetailElement(id).addEventListener("click", () => closeHistoryDelete());
  dialog.addEventListener("cancel", event => { event.preventDefault(); closeHistoryDelete(); });
  dialog.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      // Repeated native CloseWatcher cancel events can become non-cancelable.
      // Own the key before its default action so a pending deletion stays modal.
      event.preventDefault(); event.stopPropagation(); closeHistoryDelete();
    } else containHistoryDeleteFocus(event);
  });
  let outsidePointer = false;
  const outside = event => {
    if (event.target !== dialog) return false;
    const rect = dialog.getBoundingClientRect();
    return event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
  };
  dialog.addEventListener("pointerdown", event => { outsidePointer = outside(event); });
  dialog.addEventListener("click", event => {
    if (event.target === dialog) {
      // Consume generic dismissal, even during a pending delete or a drag from inside.
      event.preventDefault();
      if (outsidePointer && outside(event)) closeHistoryDelete();
    }
    outsidePointer = false;
  });
  dialog.addEventListener("close", () => {
    if (dialog.open || (!historyDetailState.record && !historyDetailState.batch)) return;
    ++historyDetailState.session;
    historyDetailState.record = historyDetailState.owner = historyDetailState.fileOutcome = historyDetailState.batch = null;
    renderHistoryDelete();
  });
}
