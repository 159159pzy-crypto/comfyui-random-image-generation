/* Random-pool source changes and cross-tab synchronization. */
const SHARED_FAVORITES_KEY = "anima-random-webui:favorites-updated";
const sharedFavoritesState = { ready: false, epoch: 0, request: 0, refreshTimer: null, changeRevision: 0, reconciled: new Map(), refreshFailed: false };
function sharedFavoritesView() { return { section: typeof activeSection === "undefined" ? "character" : activeSection }; }
function sharedFavoriteUrl(section, suffix = "") { return `/api/favorites/${section}${suffix}${suffix.includes("?") ? "&" : "?"}model_profile=anima`; }
function prunePoolSourceSelections(profile, section, ids) {
  const deleted = new Set(ids || []);
  if (!deleted.size) return;
  for (const state of [typeof draft !== "undefined" ? draft : null, typeof profileDrafts !== "undefined" ? profileDrafts.anima : null]) {
    const selection = state?.pools?.[section];
    if (selection) for (const key of ["ids", "excluded_ids"]) selection[key] = (selection[key] || []).filter(id => !deleted.has(id));
  }
  if (typeof schedulePersist === "function") schedulePersist();
}


function poolSourceChanged(change = {}, broadcast = true) {
  sharedFavoritesChanged({ ...change, sourceChange: true }, broadcast);
}

function poolMutationError(error, context = {}) {
  const payload = error.payload || {};
  if (payload.source_committed === true) {
    poolSourceChanged({ ...context, ...payload });
    return `源词条已提交，关联同步未完成：${error.message}。请先刷新状态，不要重复提交。${payload.recovery_required ? "需要恢复存储后重试同步。" : ""}`;
  }
  return `${payload.source_committed === false ? "更改未提交，编辑内容已保留。" : ""}${error.message}${payload.recovery_required ? " 请恢复存储后重试，编辑内容已保留。" : ""}`;
}

async function ensurePoolReconciled(profile = currentProfile(), section = sharedFavoritesView(profile).section) {
  if (section === "artist") return;
  const key = `${profile}:${section}`, state = sharedFavoritesState;
  if (!state.reconciled.has(key)) {
    const pending = request(sharedFavoriteUrl(section, "/reconcile", profile), { method: "POST", body: "{}" })
      .then(result => {
        state.reconciled.set(key, true);
        if (result.deletedItemIds?.length || result.updatedItemIds?.length || result.favoritesSync?.updatedCount || result.favoritesSync?.deletedCount) sharedFavoritesChanged({ ...result, model_profile: profile, section, refresh: false }, false);
        return result;
      })
      .catch(error => { state.reconciled.delete(key); throw error; });
    state.reconciled.set(key, pending);
  }
  return state.reconciled.get(key);
}

function showPoolRefreshFailure(failed, message = "内容已保存，列表刷新失败。请重新刷新，操作不会重复提交。") {
  sharedFavoritesState.refreshFailed = failed;
  const notice = document.getElementById("poolRefreshNotice");
  if (!notice) return;
  notice.hidden = !failed;
  const text = document.getElementById("poolRefreshMessage");
  if (text) text.textContent = message;
}

async function refreshPoolLifecycle() {
  const state = sharedFavoritesState, revision = state.changeRevision;
  const tasks = [refreshPoolSelectionTotals({ throwOnError: true }), loadArtistFavorites()];
  if (ui.poolDrawer?.classList.contains("open")) {
    const section = activeSection;
    tasks.push(Promise.all([loadFavorites(section, { throwOnError: true }), loadCustomGroups(section, { throwOnError: true })]).then(() => {
      if (section === activeSection && revision === state.changeRevision) return loadPool({ throwOnError: true });
    }));
  }
  const results = await Promise.allSettled(tasks);
  if (revision === state.changeRevision) showPoolRefreshFailure(results.some(result => result.status === "rejected"));
}

function sharedFavoritesChanged(change = {}, broadcast = true) {
  const { model_profile = currentProfile(), section = sharedFavoritesView(model_profile).section, deletedItemIds = [], updatedItemIds = [], refresh = true } = change;
  const state = sharedFavoritesState;
  prunePoolSourceSelections(model_profile, section, deletedItemIds);
  if (broadcast) {
    try { localStorage.setItem(SHARED_FAVORITES_KEY, JSON.stringify({ model_profile, section, deletedItemIds, updatedItemIds, sourceChange: Boolean(change.sourceChange), nonce: `${Date.now()}-${Math.random()}` })); } catch { /* Same-page refresh remains available. */ }
  }
  if (model_profile !== currentProfile()) return;
  state.changeRevision++; state.request++;
  if (typeof favoritesRequest !== "undefined") favoritesRequest++;
  if (typeof customGroupsRequest !== "undefined") customGroupsRequest++;
  if (typeof poolRequest !== "undefined") poolRequest++;
  if (typeof poolSelectionOperation !== "undefined") poolSelectionOperation = null;
  if (typeof poolResultContext !== "undefined") poolResultContext = "";
  if (typeof ui !== "undefined" && ui.poolGrid) ui.poolGrid.inert = true;
  if (typeof favoritesData !== "undefined" && typeof activeSection !== "undefined" && activeSection === section) {
    favoritesData = { groups: [], items: [], favorite_keys: [] }; favoritesAvailable = false;
  }
  if (typeof poolSelectionStats !== "undefined") poolSelectionStats.delete(section);
  if (typeof poseItemCache !== "undefined") for (const id of [...deletedItemIds, ...updatedItemIds]) poseItemCache.delete(id);
  if (!state.ready || !refresh) return;
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(() => { if (model_profile === currentProfile()) void refreshPoolLifecycle(); }, 0);
}

function initializePoolLifecycle() {
  if (sharedFavoritesState.ready) return;
  sharedFavoritesState.ready = true;
  document.getElementById("poolRefreshRetry")?.addEventListener("click", () => void refreshPoolLifecycle());
  window.addEventListener("storage", event => {
    if (event.key !== SHARED_FAVORITES_KEY || !event.newValue) return;
    try { const change = JSON.parse(event.newValue); if (change.model_profile === "anima" && change.section) sharedFavoritesChanged(change, false); } catch { /* Ignore unrelated browser data. */ }
  });
  window.addEventListener("focus", () => sharedFavoritesChanged({ model_profile: "anima" }, false));
}
