const SECTION_META = {
  character: { label: "角色", kicker: "CHARACTER", singular: "角色" },
  clothing: { label: "服装", kicker: "CLOTHING", singular: "服装" },
  pose: { label: "姿势", kicker: "POSE", singular: "姿势" },
  background: { label: "背景", kicker: "BACKGROUND", singular: "背景" },
};
const NATIVE_FACET_LABELS = {
  clothing: { categories: "服装分类", traits: "服装特征" },
  pose: { categories: "姿势分类", traits: "姿势特征" },
  background: { categories: "场景分类", traits: "场景特征" },
};
const SECTIONS = Object.keys(SECTION_META);
const DRAFT_KEY = "anima-random-studio:draft:v1";
const VIEW_KEY = "anima-random-studio:pool-view:v1";
const ui = Object.fromEntries([
  "connection", "startButton", "stopButton", "batchState", "progressCount", "progressFill", "batchError",
  "settingsForm", "count", "female_count", "male_count", "peopleTotal", "dimensionList", "poolOverview",
  "character_detail", "manual_artist", "quality_prompt", "extra_prompt", "negative_prompt", "width", "height",
  "steps", "cfg", "gallery", "emptyState", "historyCount", "prevPage", "nextPage", "pageLabel", "detailDialog",
  "closeDialog", "detailImage", "detailMeta", "detailStats", "detailSelection", "detailPositive", "detailNegative",
  "restoreSettings", "deleteRecord", "toast", "poolDrawer", "drawerBackdrop", "closePool", "poolTitle", "poolKicker", "poolMeta",
  "poolSearch", "poolSort", "poolLanguage", "togglePoolSidebar", "selectedOnly", "clearFilters", "selectPage", "selectAllPool", "clearPool", "addCustom",
  "poolSidebar", "poolGrid", "poolEmpty", "poseConflict", "poolSelectionLabel", "poolPrev", "poolNext", "poolPageLabel", "confirmPool",
  "customDialog", "customForm", "closeCustom", "cancelCustom", "customDialogTitle", "customId", "customTitle", "customSubtitle",
  "customGenderField", "customGender", "customCharacterMeta", "customHair", "customEye", "customSeries", "customPoseMeta",
  "customCategory", "customTraits", "customPrompt", "deleteCustom", "loraSummary", "loraList", "loraEmpty", "addLora", "resetLoras",
  "loraDialog", "closeLora", "cancelLora", "loraSearch", "loraCatalog", "loraCatalogEmpty", "loraCatalogMeta",
  "draftStatus", "resetSettings", "clearDraft", "favoriteDialog", "favoriteForm", "favoriteTitle", "favoriteGroups",
  "favoriteNickname", "removeFavorite", "closeFavorite", "cancelFavorite", "groupDialog", "groupForm", "groupDialogTitle",
  "groupId", "groupName", "deleteGroup", "closeGroup", "cancelGroup"
].map(id => [id, document.getElementById(id)]));

let defaults = null;
let config = { catalog: { counts: {} } };
let currentBatch = null;
let selectedRecord = null;
let historyPage = 1;
let historyPages = 1;
let lastTerminalBatch = "";
let toastTimer = null;
let persistTimer = null;
let initialized = false;
let draft = { modes: {}, counts: {}, fixed: {}, pools: {}, loras: [] };
let activeSection = "character";
let poolPage = 1;
let poolPages = 1;
let poolItems = [];
let poolFacets = {};
let poolRequest = 0;
let editingCustom = null;
let editingFavoriteItem = null;
let loraInventory = [];
let loraInventoryLoaded = false;
let loraInventoryError = "";
let favoritesData = { groups: [], items: [], favorite_keys: [] };
let favoritesAvailable = false;
const poseItemCache = new Map();

function defaultView() {
  return { query: "", sort: "", language: "zh", selectedOnly: false, scrollTop: 0, filters: { gender: "", hair: "", eye: "", series: "", categories: [], traits: [], collection: "" } };
}
let poolViews = Object.fromEntries(SECTIONS.map(section => [section, defaultView()]));

async function request(path, options = {}) {
  const response = await fetch(path, { headers: options.body ? { "Content-Type": "application/json" } : undefined, ...options });
  let payload = null;
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

function escapeHtml(value) {
  const span = document.createElement("span");
  span.textContent = value == null ? "" : String(value);
  return span.innerHTML;
}

function toast(message) {
  ui.toast.textContent = message;
  ui.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ui.toast.classList.remove("show"), 3200);
}

function clone(value) { return JSON.parse(JSON.stringify(value)); }
function defaultPools() { return Object.fromEntries(SECTIONS.map(section => [section, { mode: "include", ids: [], excluded_ids: [] }])); }
function currentView() { return poolViews[activeSection] || (poolViews[activeSection] = defaultView()); }

function normalizeSettings(raw) {
  const merged = { ...defaults, ...raw };
  const hasPools = raw && raw.pools && typeof raw.pools === "object";
  merged.pools = hasPools ? clone(raw.pools) : defaultPools();
  for (const section of SECTIONS) {
    if (!merged.pools[section]) merged.pools[section] = { mode: "include", ids: [], excluded_ids: [] };
    if (!hasPools && raw?.[`random_${section}`]) merged.pools[section].mode = "all";
  }
  const hasLoras = raw && Object.prototype.hasOwnProperty.call(raw, "loras");
  merged.loras = clone(hasLoras ? (raw.loras || []) : (defaults?.loras || []));
  return merged;
}

function applySettings(raw) {
  const settings = normalizeSettings(raw || defaults || {});
  for (const [name, value] of Object.entries(settings)) {
    const element = ui[name];
    if (element && !["object", "undefined"].includes(typeof value)) element.value = value;
  }
  draft = {
    modes: Object.fromEntries(SECTIONS.map(section => [section, settings[`random_${section}`] ? "pool" : (settings[`fixed_${section}`] ? "fixed" : "off")])),
    counts: Object.fromEntries(SECTIONS.map(section => [section, Number(settings[`random_${section}_count`] || 1)])),
    fixed: Object.fromEntries(SECTIONS.map(section => [section, settings[`fixed_${section}`] || ""])),
    pools: clone(settings.pools || defaultPools()),
    loras: clone(settings.loras || []),
  };
  renderDimensions(); renderLoras(); updatePeopleTotal(); updatePoolOverview();
}

function readSettings() {
  const settings = {
    count: Number(ui.count.value), female_count: Number(ui.female_count.value), male_count: Number(ui.male_count.value),
    character_detail: ui.character_detail.value, manual_artist: ui.manual_artist.value, quality_prompt: ui.quality_prompt.value,
    extra_prompt: ui.extra_prompt.value, negative_prompt: ui.negative_prompt.value, width: Number(ui.width.value),
    height: Number(ui.height.value), steps: Number(ui.steps.value), cfg: Number(ui.cfg.value), pools: clone(draft.pools), loras: clone(draft.loras),
  };
  for (const section of SECTIONS) {
    settings[`random_${section}`] = draft.modes[section] === "pool";
    settings[`random_${section}_count`] = Number(draft.counts[section] || 1);
    settings[`fixed_${section}`] = draft.modes[section] === "fixed" ? (draft.fixed[section] || "") : "";
  }
  return settings;
}

function formatSavedTime(timestamp) {
  try { return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(timestamp)); }
  catch { return "刚刚"; }
}

function persistNow() {
  if (!initialized) return;
  const savedAt = new Date().toISOString();
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ version: 1, savedAt, settings: readSettings() }));
    localStorage.setItem(VIEW_KEY, JSON.stringify({ version: 1, activeSection, views: poolViews }));
    ui.draftStatus.textContent = `本地草稿已保存 ${formatSavedTime(savedAt)}`;
  } catch { ui.draftStatus.textContent = "浏览器未允许保存草稿"; }
}

function schedulePersist() { clearTimeout(persistTimer); persistTimer = setTimeout(persistNow, 180); }

function loadStoredView() {
  try {
    const payload = JSON.parse(localStorage.getItem(VIEW_KEY) || "null");
    if (payload?.version !== 1 || typeof payload.views !== "object") return;
    for (const section of SECTIONS) poolViews[section] = { ...defaultView(), ...(payload.views[section] || {}), filters: { ...defaultView().filters, ...(payload.views[section]?.filters || {}) } };
    if (SECTIONS.includes(payload.activeSection)) activeSection = payload.activeSection;
  } catch { localStorage.removeItem(VIEW_KEY); }
}

function loadStoredDraft() {
  try {
    const payload = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
    if (payload?.version !== 1 || !payload.settings || typeof payload.settings !== "object") return false;
    applySettings(payload.settings);
    ui.draftStatus.textContent = `已恢复草稿 ${formatSavedTime(payload.savedAt)}`;
    return true;
  } catch { localStorage.removeItem(DRAFT_KEY); return false; }
}

function selectedCount(section) {
  const selection = draft.pools[section] || { mode: "include", ids: [], excluded_ids: [] };
  return selection.mode === "all" ? Math.max(0, Number(config.catalog?.counts?.[section] || 0) - selection.excluded_ids.length) : selection.ids.length;
}

function renderDimensions() {
  ui.dimensionList.replaceChildren(...SECTIONS.map(section => {
    const meta = SECTION_META[section]; const mode = draft.modes[section] || "off"; const count = draft.counts[section] || 1; const selected = selectedCount(section);
    const article = document.createElement("article"); article.className = `dimension-row mode-${mode}`; article.dataset.section = section;
    article.innerHTML = `<div class="dimension-heading"><div><span class="dimension-icon">${meta.label.slice(0, 1)}</span><strong>${meta.label}</strong><small>${selected} 项已选</small></div><button class="manage-pool" type="button" data-manage="${section}">管理池 <span aria-hidden="true">→</span></button></div>
      <div class="dimension-mode" role="group" aria-label="${meta.label}模式"><button type="button" data-mode="off" class="${mode === "off" ? "active" : ""}">关闭</button><button type="button" data-mode="pool" class="${mode === "pool" ? "active" : ""}">随机池</button><button type="button" data-mode="fixed" class="${mode === "fixed" ? "active" : ""}">固定</button></div>
      <div class="dimension-body"><label class="draw-count"><span>抽取数量</span><input type="number" min="1" max="5" step="1" value="${count}" data-count="${section}" ${mode !== "pool" ? "disabled" : ""}></label><span class="mode-note">${mode === "pool" ? `${selected} 项候选` : mode === "fixed" ? "使用固定提示词" : "不加入提示词"}</span></div>
      <label class="field dimension-fixed"><span>固定提示词</span><input type="text" value="${escapeHtml(draft.fixed[section] || "")}" data-fixed="${section}" placeholder="留空则不加入" ${mode !== "fixed" ? "disabled" : ""}></label>`;
    article.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => { draft.modes[section] = button.dataset.mode; renderDimensions(); schedulePersist(); }));
    article.querySelector("[data-count]").addEventListener("input", event => { draft.counts[section] = Math.max(1, Math.min(5, Number(event.target.value || 1))); schedulePersist(); updatePoseConflict(); });
    article.querySelector("[data-fixed]").addEventListener("input", event => { draft.fixed[section] = event.target.value; schedulePersist(); });
    article.querySelector("[data-manage]").addEventListener("click", () => openPool(section));
    return article;
  }));
}

function updatePeopleTotal() { const total = Number(ui.female_count.value || 0) + Number(ui.male_count.value || 0); ui.peopleTotal.textContent = total ? `共 ${total} 人` : "不限人数"; }
function updatePoolOverview() { ui.poolOverview.textContent = `${SECTIONS.reduce((sum, section) => sum + selectedCount(section), 0)} 项已选`; }

function loraItem(filename) { return loraInventory.find(item => item.filename === filename) || null; }
function renderLoras() {
  const loras = Array.isArray(draft.loras) ? draft.loras : []; const enabled = loras.filter(item => item.enabled).length;
  ui.loraSummary.textContent = `${enabled} 启用 / ${loras.length} 配置`; ui.loraEmpty.hidden = loras.length > 0;
  ui.loraList.replaceChildren(...loras.map((item, index) => {
    const available = !loraInventoryLoaded || Boolean(loraItem(item.filename)); const high = Math.abs(Number(item.strength)) > 2; const row = document.createElement("div");
    row.className = `lora-row${available ? "" : " missing"}`;
    row.innerHTML = `<input class="lora-toggle" type="checkbox" ${item.enabled ? "checked" : ""} title="启用 LoRA" aria-label="启用 ${escapeHtml(item.filename)}"><div class="lora-name"><strong title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</strong><small class="${available && !high ? "" : "lora-warning"}">${available ? (high ? "高强度 · 模型 + CLIP" : "模型 + CLIP") : "文件不存在"}</small></div><input class="lora-strength" type="number" min="-100" max="100" step="0.05" value="${Number(item.strength)}" title="LoRA strength" aria-label="${escapeHtml(item.filename)} 强度"><div class="lora-actions"><button class="icon-button dark" type="button" data-lora-up title="上移" aria-label="上移">↑</button><button class="icon-button dark" type="button" data-lora-down title="下移" aria-label="下移">↓</button><button class="icon-button dark" type="button" data-lora-remove title="移除" aria-label="移除">×</button></div>`;
    row.querySelector(".lora-toggle").addEventListener("change", event => { item.enabled = event.target.checked; renderLoras(); schedulePersist(); });
    row.querySelector(".lora-strength").addEventListener("change", event => { const value = Number(event.target.value); if (!Number.isFinite(value) || value < -100 || value > 100) { toast("LoRA strength 必须在 -100 到 100 之间"); renderLoras(); return; } item.strength = Math.round(value * 100) / 100; renderLoras(); schedulePersist(); });
    row.querySelector("[data-lora-up]").disabled = index === 0; row.querySelector("[data-lora-down]").disabled = index === loras.length - 1;
    row.querySelector("[data-lora-up]").addEventListener("click", () => moveLora(index, -1)); row.querySelector("[data-lora-down]").addEventListener("click", () => moveLora(index, 1));
    row.querySelector("[data-lora-remove]").addEventListener("click", () => { draft.loras.splice(index, 1); renderLoras(); renderLoraCatalog(); schedulePersist(); }); return row;
  }));
}
function moveLora(index, delta) { const next = index + delta; if (next < 0 || next >= draft.loras.length) return; [draft.loras[index], draft.loras[next]] = [draft.loras[next], draft.loras[index]]; renderLoras(); schedulePersist(); }
async function loadLoraInventory() { try { const data = await request("/api/loras"); loraInventory = data.items || []; loraInventoryLoaded = true; loraInventoryError = ""; renderLoras(); renderLoraCatalog(); } catch (error) { loraInventoryLoaded = false; loraInventoryError = error.message; renderLoraCatalog(); } }
function renderLoraCatalog() { if (!ui.loraCatalog) return; const query = String(ui.loraSearch.value || "").trim().toLowerCase(); const configured = new Set(draft.loras.map(item => item.filename)); const items = loraInventory.filter(item => !query || `${item.filename} ${item.display_name || ""}`.toLowerCase().includes(query)); ui.loraCatalogEmpty.hidden = Boolean(loraInventoryError) || items.length > 0; ui.loraCatalog.replaceChildren(...items.map(item => { const button = document.createElement("button"); button.type = "button"; button.className = "lora-catalog-item"; button.disabled = configured.has(item.filename); button.innerHTML = `<span><strong>${escapeHtml(item.display_name || item.filename)}</strong><small>${escapeHtml(item.filename)}${configured.has(item.filename) ? " · 已配置" : ""}</small></span><span aria-hidden="true">${configured.has(item.filename) ? "✓" : "+"}</span>`; button.addEventListener("click", () => { draft.loras.push({ filename: item.filename, enabled: true, strength: 1 }); renderLoras(); renderLoraCatalog(); schedulePersist(); }); return button; })); ui.loraCatalogMeta.textContent = loraInventoryError || `${loraInventory.length} 个本地 LoRA`; }
async function openLoraDialog() { ui.loraSearch.value = ""; ui.loraDialog.showModal(); if (!loraInventoryLoaded) await loadLoraInventory(); renderLoraCatalog(); }

function renderBatch(batch) { currentBatch = batch; const active = Boolean(batch && ["running", "stopping"].includes(batch.status)); const completed = batch?.completed || 0; const total = batch?.total || 0; const labels = { running: "正在生成", stopping: "完成当前张后停止", completed: "批次完成", stopped: "已停止", error: "执行失败" }; ui.batchState.textContent = batch ? (labels[batch.status] || batch.status) : "等待任务"; ui.progressCount.textContent = `${completed} / ${total}`; ui.progressFill.style.width = total ? `${Math.min(100, completed / total * 100)}%` : "0%"; ui.stopButton.disabled = !active || batch.status === "stopping"; ui.startButton.disabled = active || !ui.connection.classList.contains("online"); ui.batchError.hidden = !batch?.error; ui.batchError.textContent = batch?.error || ""; if (batch && !active && lastTerminalBatch !== `${batch.id}:${batch.status}:${completed}`) { lastTerminalBatch = `${batch.id}:${batch.status}:${completed}`; loadHistory(1); } }
async function refreshConnection() { try { const status = await request("/api/status"); ui.connection.className = `status-badge ${status.online ? "online" : "offline"}`; ui.connection.querySelector("span").textContent = status.online ? `ComfyUI ${status.version}` : "ComfyUI 离线"; ui.startButton.disabled = !status.online || Boolean(currentBatch && ["running", "stopping"].includes(currentBatch.status)); } catch { ui.connection.className = "status-badge offline"; ui.connection.querySelector("span").textContent = "ComfyUI 离线"; ui.startButton.disabled = true; } }
async function pollBatch() { try { renderBatch((await request("/api/batches/current")).batch); } catch (error) { toast(error.message); } }
function card(record, index) { const article = document.createElement("article"); article.className = "image-card"; article.style.animationDelay = `${Math.min(index * 25, 250)}ms`; const button = document.createElement("button"); button.type = "button"; button.innerHTML = `<img loading="lazy" src="/api/images/${record.id}" alt="Anima 生成结果"><div class="card-copy"><strong>${escapeHtml(record.filename)}</strong><span>#${record.sequence} · Seed ${record.sample_seed}</span></div>`; button.addEventListener("click", () => openDetail(record)); button.querySelector("img").addEventListener("error", event => event.target.classList.add("image-failed")); article.append(button); return article; }
async function loadHistory(page = historyPage) { try { const data = await request(`/api/history?page=${page}&limit=24`); historyPage = data.page; historyPages = data.pages; ui.gallery.replaceChildren(...data.items.map(card)); ui.emptyState.hidden = data.items.length > 0; ui.pageLabel.textContent = `${historyPage} / ${historyPages}`; ui.prevPage.disabled = historyPage <= 1; ui.nextPage.disabled = historyPage >= historyPages; ui.historyCount.textContent = `${data.total} 张图片`; } catch (error) { toast(error.message); } }
function openDetail(record) { selectedRecord = record; const settings = normalizeSettings(record.settings || {}); ui.detailImage.src = `/api/images/${record.id}`; ui.detailMeta.textContent = `${record.created_at || ""} · ${record.filename || ""}`; ui.detailPositive.value = record.positive_prompt || record.resolved_prompt || ""; ui.detailNegative.value = record.negative_prompt || ""; const stats = [["尺寸", `${settings.width} × ${settings.height}`], ["采样", `${settings.steps} steps / CFG ${settings.cfg}`], ["图像种子", record.sample_seed], ["提示词种子", record.prompt_seed], ["人物", `${settings.female_count || 0} 女 / ${settings.male_count || 0} 男`]]; ui.detailStats.innerHTML = stats.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join(""); const selected = record.resolved_selection || {}; const activeLoras = (settings.loras || []).filter(item => item.enabled); const loraText = activeLoras.length ? activeLoras.map(item => `${escapeHtml(item.filename)} × ${escapeHtml(item.strength)}`).join("、") : "未使用"; ui.detailSelection.innerHTML = `<span class="kicker">ACTUAL DRAW</span><div>${SECTIONS.map(section => `<span><b>${SECTION_META[section].label}</b>${(selected[section] || []).map(item => escapeHtml(item.title)).join("、") || "未使用"}</span>`).join("")}<span><b>LoRA</b>${loraText}</span></div>`; ui.detailDialog.showModal(); }

function selectionHas(section, id) { const selection = draft.pools[section]; return selection.mode === "all" ? !selection.excluded_ids.includes(id) : selection.ids.includes(id); }
function toggleSelection(section, id, checked) { const selection = draft.pools[section]; if (selection.mode === "all") selection.excluded_ids = checked ? selection.excluded_ids.filter(value => value !== id) : [...new Set([...selection.excluded_ids, id])]; else if (checked) selection.ids = [...new Set([...selection.ids, id])]; else selection.ids = selection.ids.filter(value => value !== id); renderPoolItems(); renderDimensions(); updatePoolOverview(); updatePoseConflict(); schedulePersist(); }

function favoriteKey(item) { return String(item.favorite_key || ""); }
function favoriteMap() { const map = new Map(); for (const item of favoritesData.items || []) { const key = activeSection === "character" ? String(item.name || item.id || "") : String(item.id || item.name || ""); if (key) map.set(key, item); } return map; }
function isFavorite(item) { return Boolean(favoriteMap().get(favoriteKey(item))?.groupIds?.length); }

async function loadFavorites(section = activeSection) { try { favoritesData = await request(`/api/favorites/${section}`); favoritesAvailable = true; } catch (error) { favoritesData = { groups: [], items: [], favorite_keys: [] }; favoritesAvailable = false; if (currentView().filters.collection) currentView().filters.collection = ""; if (currentView().sort === "favorite-first") currentView().sort = ""; toast(`收藏不可用：${error.message}`); } }

async function openPool(section) {
  activeSection = section; poolPage = 1; const view = currentView();
  ui.poolDrawer.classList.add("open"); ui.poolDrawer.setAttribute("aria-hidden", "false"); ui.drawerBackdrop.hidden = false;
  ui.poolTitle.textContent = `${SECTION_META[section].label}池`; ui.poolKicker.textContent = SECTION_META[section].kicker;
  ui.poolSearch.value = view.query; ui.poolSort.value = view.sort; ui.poolLanguage.value = view.language; ui.selectedOnly.checked = view.selectedOnly;
  await loadFavorites(section); await loadPool(); schedulePersist();
}
function closePool() { currentView().scrollTop = ui.poolGrid.scrollTop; setPoolSidebarOpen(false); ui.poolDrawer.classList.remove("open"); ui.poolDrawer.setAttribute("aria-hidden", "true"); ui.drawerBackdrop.hidden = true; schedulePersist(); }
function setPoolSidebarOpen(open) { ui.poolSidebar.classList.toggle("mobile-open", open); ui.togglePoolSidebar.setAttribute("aria-expanded", String(open)); ui.togglePoolSidebar.title = open ? "关闭分类导航" : "打开分类导航"; ui.togglePoolSidebar.setAttribute("aria-label", ui.togglePoolSidebar.title); }

function poolQueryBody() { const view = currentView(); return { page: poolPage, limit: 48, q: view.query, sort: view.sort, collection: view.filters.collection, categories: view.filters.categories, traits: view.filters.traits, gender: view.filters.gender, hair: view.filters.hair, eye: view.filters.eye, series: view.filters.series }; }
async function loadPool() {
  const requestId = ++poolRequest; const view = currentView();
  try {
    const query = poolQueryBody(); let data;
    if (view.selectedOnly) data = await request(`/api/pools/${activeSection}/query`, { method: "POST", body: JSON.stringify({ ...query, selection: draft.pools[activeSection] }) });
    else { const params = new URLSearchParams({ page: query.page, limit: query.limit, q: query.q, sort: query.sort, collection: query.collection, gender: query.gender, hair: query.hair, eye: query.eye, series: query.series }); query.categories.forEach(value => params.append("category", value)); query.traits.forEach(value => params.append("trait", value)); data = await request(`/api/pools/${activeSection}?${params}`); }
    if (requestId !== poolRequest) return;
    poolItems = data.items || []; poolFacets = data.facets || {}; poolPages = data.pages; poolPage = data.page;
    if (activeSection === "pose") poolItems.forEach(item => poseItemCache.set(item.id, item));
    ui.poolPageLabel.textContent = `${data.page} / ${data.pages}`; ui.poolPrev.disabled = data.page <= 1; ui.poolNext.disabled = data.page >= data.pages;
    ui.poolMeta.textContent = `${data.total} 项可用 · ${selectedCount(activeSection)} 项已选${favoritesAvailable ? "" : " · 收藏离线"}`;
    renderPoolSidebar(); renderPoolItems(); updatePoseConflict();
    requestAnimationFrame(() => { if (view.scrollTop && poolPage === 1) { ui.poolGrid.scrollTop = view.scrollTop; view.scrollTop = 0; } });
  } catch (error) { toast(error.message); }
}

function sidebarSection(title, items, kind, activeValues, multi = false) {
  if (!items?.length) return null; const section = document.createElement("section"); section.className = "facet-section";
  section.innerHTML = `<h3>${escapeHtml(title)}</h3><div></div>`; const body = section.querySelector("div"); const active = new Set(Array.isArray(activeValues) ? activeValues : [activeValues].filter(Boolean));
  for (const item of items) { const button = document.createElement("button"); button.type = "button"; button.className = active.has(item.value) ? "active" : ""; button.innerHTML = `<span>${escapeHtml(item.label)}</span><small>${item.count ?? ""}</small>`; button.addEventListener("click", () => { const filters = currentView().filters; if (multi) { const values = new Set(filters[kind] || []); values.has(item.value) ? values.delete(item.value) : values.add(item.value); filters[kind] = [...values]; } else filters[kind] = filters[kind] === item.value ? "" : item.value; poolPage = 1; schedulePersist(); loadPool(); }); body.append(button); }
  return section;
}

function renderPoolSidebar() {
  const view = currentView(); const nav = document.createDocumentFragment();
  const collections = document.createElement("section"); collections.className = "facet-section collections";
  collections.innerHTML = `<div class="facet-heading"><h3>收藏分组</h3><button type="button" title="新建收藏分组" aria-label="新建收藏分组">+</button></div><div></div>`;
  collections.querySelector(".facet-heading button").disabled = !favoritesAvailable; collections.querySelector(".facet-heading button").addEventListener("click", () => openGroupDialog());
  const collectionBody = collections.lastElementChild;
  const allButton = document.createElement("button"); allButton.type = "button"; allButton.className = view.filters.collection ? "" : "active"; allButton.innerHTML = `<span>全部${SECTION_META[activeSection].label}</span><small>${config.catalog?.counts?.[activeSection] || ""}</small>`; allButton.addEventListener("click", () => { view.filters.collection = ""; poolPage = 1; loadPool(); schedulePersist(); }); collectionBody.append(allButton);
  for (const group of favoritesData.groups || []) { const count = (favoritesData.items || []).filter(item => item.groupIds?.includes(group.id)).length; const row = document.createElement("div"); row.className = "collection-row"; const button = document.createElement("button"); button.type = "button"; button.className = view.filters.collection === group.id ? "active" : ""; button.innerHTML = `<span>${group.id === "default" ? "我的收藏" : escapeHtml(group.name)}</span><small>${count}</small>`; button.addEventListener("click", () => { view.filters.collection = group.id; poolPage = 1; loadPool(); schedulePersist(); }); row.append(button); if (!group.isSystem) { const edit = document.createElement("button"); edit.type = "button"; edit.className = "collection-edit"; edit.title = "编辑分组"; edit.setAttribute("aria-label", `编辑分组 ${group.name}`); edit.textContent = "···"; edit.addEventListener("click", () => openGroupDialog(group)); row.append(edit); } collectionBody.append(row); }
  nav.append(collections);
  if (activeSection === "character") {
    nav.append(sidebarSection("角色性别", poolFacets.gender, "gender", view.filters.gender));
    nav.append(sidebarSection("角色发色", poolFacets.hair, "hair", view.filters.hair));
    nav.append(sidebarSection("角色瞳色", poolFacets.eye, "eye", view.filters.eye));
    nav.append(sidebarSection("热门作品系列", poolFacets.series, "series", view.filters.series));
  } else {
    const labels = NATIVE_FACET_LABELS[activeSection];
    nav.append(sidebarSection(labels.categories, poolFacets.categories, "categories", view.filters.categories, true));
    nav.append(sidebarSection(labels.traits, poolFacets.traits, "traits", view.filters.traits, true));
  }
  ui.poolSidebar.replaceChildren(nav);
}

function displayTitle(item) { const lang = currentView().language; if (lang === "en") return item.subtitle || item.title; if (lang === "bilingual" && item.subtitle && item.subtitle !== item.title) return `${item.title} / ${item.subtitle}`; return item.title; }
function itemBadges(item) { const values = []; if (activeSection === "character") { values.push(item.gender === "1girl" ? "女性" : item.gender === "1boy" ? "男性" : "未标注"); if (item.hair) values.push(`${item.hair} hair`); if (item.eye) values.push(`${item.eye} eyes`); } else { values.push(...(item.categories || []).slice(0, 1).map(value => value.replace(/^.*\((.*)\).*$/, "$1"))); values.push(...(item.traits || []).slice(0, 2)); } return values.filter(Boolean); }
function renderPoolItems() {
  const favMap = favoriteMap(); ui.poolEmpty.hidden = poolItems.length > 0;
  ui.poolGrid.replaceChildren(...poolItems.map(item => {
    const article = document.createElement("article"); const selected = selectionHas(activeSection, item.id); const savedFavorite = favMap.get(favoriteKey(item)); const favorite = Boolean(savedFavorite?.groupIds?.length);
    article.className = `pool-item ${selected ? "selected" : ""}${favorite ? " favorite" : ""}`;
    article.innerHTML = `<label><input type="checkbox" ${selected ? "checked" : ""}><span class="pool-thumb">${item.preview ? `<img loading="lazy" src="${escapeHtml(item.preview)}" alt="">` : "<span>+</span>"}</span><span class="pool-item-copy"><strong title="${escapeHtml(displayTitle(item))}">${escapeHtml(displayTitle(item))}</strong><span class="item-badges">${itemBadges(item).map(value => `<em>${escapeHtml(value)}</em>`).join("")}</span><small>${escapeHtml(savedFavorite?.nickname || item.copyright || item.prompt || "自定义项")}</small></span></label><div class="item-tools"><button class="favorite-toggle ${favorite ? "active" : ""}" type="button" title="${favorite ? "取消收藏" : "加入收藏"}" aria-label="${favorite ? "取消收藏" : "加入收藏"}">${favorite ? "♥" : "♡"}</button><button class="favorite-settings" type="button" title="收藏设置" aria-label="收藏设置" ${favorite ? "" : "hidden"}>⋮</button><button class="item-edit" type="button" title="编辑自定义项" aria-label="编辑自定义项" ${item.builtin ? "hidden" : ""}>✎</button></div>`;
    article.querySelector("label input").addEventListener("change", event => toggleSelection(activeSection, item.id, event.target.checked)); article.querySelector("img")?.addEventListener("error", event => event.target.style.display = "none");
    article.querySelector(".favorite-toggle").disabled = !favoritesAvailable; article.querySelector(".favorite-toggle").addEventListener("click", event => { event.stopPropagation(); toggleFavorite(item, !favorite); });
    article.querySelector(".favorite-settings").addEventListener("click", event => { event.stopPropagation(); openFavoriteDialog(item); });
    article.querySelector(".item-edit").addEventListener("click", event => { event.stopPropagation(); openCustom(item); }); return article;
  }));
  ui.poolSelectionLabel.textContent = `已选择 ${selectedCount(activeSection)} 项`;
}

function updatePoseConflict() {
  if (activeSection !== "pose") { ui.poseConflict.hidden = true; return; }
  const selection = draft.pools.pose; if (selection.mode === "all" || Number(draft.counts.pose || 1) <= 1) { ui.poseConflict.hidden = true; return; }
  const selected = selection.ids.map(id => poseItemCache.get(id)).filter(Boolean); const groups = new Map();
  for (const item of selected) for (const slot of item.conflict_slots || []) { if (!groups.has(slot)) groups.set(slot, []); groups.get(slot).push(item.title); }
  const conflicts = [...groups.entries()].filter(([, names]) => names.length > 1); if (!conflicts.length) { ui.poseConflict.hidden = true; return; }
  const labels = { hand_action: "手部动作", body_pose: "整体姿态", interaction: "双人互动" }; ui.poseConflict.hidden = false; ui.poseConflict.innerHTML = `<strong>存在潜在姿势冲突</strong><span>${conflicts.map(([slot, names]) => `${labels[slot] || slot}：${names.join("、")}`).join("；")}</span>`;
}

async function toggleFavorite(item, favorite) { try { favoritesData = await request(`/api/favorites/${activeSection}/item`, { method: "PUT", body: JSON.stringify({ id: item.id, favorite }) }); favoritesAvailable = true; if (!favorite && currentView().filters.collection) await loadPool(); else { renderPoolItems(); renderPoolSidebar(); } toast(favorite ? "已加入 Anima 收藏" : "已取消收藏"); } catch (error) { toast(error.message); } }
function openFavoriteDialog(item) { editingFavoriteItem = item; const saved = favoriteMap().get(favoriteKey(item)) || {}; ui.favoriteTitle.textContent = displayTitle(item); ui.favoriteNickname.value = saved.nickname || ""; ui.favoriteGroups.replaceChildren(...(favoritesData.groups || []).map(group => { const label = document.createElement("label"); label.className = "favorite-group-check"; label.innerHTML = `<input type="checkbox" value="${escapeHtml(group.id)}" ${(saved.groupIds || []).includes(group.id) ? "checked" : ""}><span>${escapeHtml(group.id === "default" ? "我的收藏" : group.name)}</span>`; return label; })); ui.favoriteDialog.showModal(); }
async function saveFavorite(event) { event.preventDefault(); if (!editingFavoriteItem) return; const groupIds = [...ui.favoriteGroups.querySelectorAll("input:checked")].map(input => input.value); try { favoritesData = await request(`/api/favorites/${activeSection}/item`, { method: "PUT", body: JSON.stringify({ id: editingFavoriteItem.id, favorite: true, groupIds, nickname: ui.favoriteNickname.value }) }); ui.favoriteDialog.close(); renderPoolItems(); renderPoolSidebar(); toast("收藏设置已保存"); } catch (error) { toast(error.message); } }
async function removeFavorite() { if (!editingFavoriteItem) return; try { favoritesData = await request(`/api/favorites/${activeSection}/item`, { method: "PUT", body: JSON.stringify({ id: editingFavoriteItem.id, favorite: false }) }); ui.favoriteDialog.close(); await loadPool(); toast("已取消收藏"); } catch (error) { toast(error.message); } }

function openGroupDialog(group = null) { ui.groupId.value = group?.id || ""; ui.groupName.value = group?.name || ""; ui.groupDialogTitle.textContent = group ? "编辑收藏分组" : "新建收藏分组"; ui.deleteGroup.hidden = !group; ui.groupDialog.showModal(); }
async function saveGroup(event) { event.preventDefault(); const id = ui.groupId.value; try { favoritesData = await request(id ? `/api/favorites/${activeSection}/groups/${encodeURIComponent(id)}` : `/api/favorites/${activeSection}/groups`, { method: id ? "PUT" : "POST", body: JSON.stringify({ name: ui.groupName.value }) }); ui.groupDialog.close(); renderPoolSidebar(); toast(id ? "收藏分组已更新" : "收藏分组已创建"); } catch (error) { toast(error.message); } }
async function deleteFavoriteGroup() { const id = ui.groupId.value; if (!id || !confirm("删除这个收藏分组？收藏条目本身不会删除。")) return; try { favoritesData = await request(`/api/favorites/${activeSection}/groups/${encodeURIComponent(id)}`, { method: "DELETE" }); if (currentView().filters.collection === id) currentView().filters.collection = ""; ui.groupDialog.close(); await loadPool(); toast("收藏分组已删除"); } catch (error) { toast(error.message); } }

function selectCurrentPage(checked) { for (const item of poolItems) { const selection = draft.pools[activeSection]; if (selection.mode === "all") selection.excluded_ids = checked ? selection.excluded_ids.filter(value => value !== item.id) : [...new Set([...selection.excluded_ids, item.id])]; else if (checked) selection.ids = [...new Set([...selection.ids, item.id])]; else selection.ids = selection.ids.filter(value => value !== item.id); } renderPoolItems(); renderDimensions(); updatePoolOverview(); updatePoseConflict(); schedulePersist(); }

function openCustom(item = null) { editingCustom = item; ui.customDialogTitle.textContent = item ? "编辑自定义项" : "新增自定义项"; ui.customId.value = item?.id || ""; ui.customTitle.value = item?.title || ""; ui.customSubtitle.value = item?.subtitle || ""; ui.customGender.value = item?.gender === "1girl" ? "female" : item?.gender === "1boy" ? "male" : "unknown"; ui.customHair.value = item?.hair || ""; ui.customEye.value = item?.eye || ""; ui.customSeries.value = item?.copyright || ""; ui.customCategory.value = item?.categories?.[0] || ""; ui.customTraits.value = (item?.traits || []).join(", "); ui.customPrompt.value = item?.prompt || ""; ui.customGenderField.hidden = activeSection !== "character"; ui.customCharacterMeta.hidden = activeSection !== "character"; ui.customPoseMeta.hidden = activeSection !== "pose"; ui.deleteCustom.hidden = !item; ui.customDialog.showModal(); }
async function saveCustom(event) { event.preventDefault(); const payload = { section: activeSection, title: ui.customTitle.value, subtitle: ui.customSubtitle.value, prompt: ui.customPrompt.value, gender: ui.customGender.value, hair: ui.customHair.value, eye: ui.customEye.value, copyright: ui.customSeries.value, categories: ui.customCategory.value ? [ui.customCategory.value] : [], traits: ui.customTraits.value.split(",").map(value => value.trim()).filter(Boolean) }; try { const item = editingCustom ? await request(`/api/custom-prompts/${editingCustom.id}`, { method: "PUT", body: JSON.stringify(payload) }) : await request("/api/custom-prompts", { method: "POST", body: JSON.stringify(payload) }); if (!selectionHas(activeSection, item.id)) toggleSelection(activeSection, item.id, true); ui.customDialog.close(); await loadPool(); toast(editingCustom ? "自定义项已更新" : "自定义项已加入随机池"); } catch (error) { toast(error.message); } }
async function deleteCustomItem() { if (!editingCustom || !confirm("删除这个自定义项？")) return; try { await request(`/api/custom-prompts/${editingCustom.id}`, { method: "DELETE" }); const selection = draft.pools[activeSection]; selection.ids = selection.ids.filter(id => id !== editingCustom.id); selection.excluded_ids = selection.excluded_ids.filter(id => id !== editingCustom.id); ui.customDialog.close(); await loadPool(); renderDimensions(); updatePoolOverview(); schedulePersist(); toast("自定义项已删除"); } catch (error) { toast(error.message); } }

ui.settingsForm.addEventListener("submit", async event => { event.preventDefault(); ui.startButton.disabled = true; persistNow(); try { renderBatch(await request("/api/batches", { method: "POST", body: JSON.stringify(readSettings()) })); toast("批次已开始"); } catch (error) { toast(error.message); await refreshConnection(); } });
ui.settingsForm.addEventListener("input", event => { if (event.target.matches("input, textarea, select")) schedulePersist(); }); ui.settingsForm.addEventListener("change", schedulePersist);
ui.stopButton.addEventListener("click", async () => { if (!currentBatch) return; try { renderBatch(await request(`/api/batches/${currentBatch.id}/stop`, { method: "POST" })); } catch (error) { toast(error.message); } });
ui.prevPage.addEventListener("click", () => loadHistory(historyPage - 1)); ui.nextPage.addEventListener("click", () => loadHistory(historyPage + 1));
ui.closeDialog.addEventListener("click", () => ui.detailDialog.close()); ui.detailDialog.addEventListener("click", event => { if (event.target === ui.detailDialog) ui.detailDialog.close(); });
ui.restoreSettings.addEventListener("click", () => { if (!selectedRecord) return; applySettings(selectedRecord.settings); ui.detailDialog.close(); schedulePersist(); toast("已载入历史设置"); });
ui.deleteRecord.addEventListener("click", async () => { if (!selectedRecord || !confirm("只删除 WebUI 历史记录，图片文件会保留。继续吗？")) return; try { await request(`/api/history/${selectedRecord.id}`, { method: "DELETE" }); ui.detailDialog.close(); selectedRecord = null; await loadHistory(historyPage); } catch (error) { toast(error.message); } });
ui.closePool.addEventListener("click", closePool); ui.confirmPool.addEventListener("click", closePool); ui.drawerBackdrop.addEventListener("click", closePool);
ui.togglePoolSidebar.addEventListener("click", () => setPoolSidebarOpen(!ui.poolSidebar.classList.contains("mobile-open")));
ui.poolSidebar.addEventListener("click", event => { if (window.innerWidth <= 620 && event.target.closest("button") && !event.target.closest(".facet-heading, .collection-edit")) setPoolSidebarOpen(false); });
ui.poolSearch.addEventListener("input", () => { currentView().query = ui.poolSearch.value; poolPage = 1; clearTimeout(ui.poolSearch._timer); ui.poolSearch._timer = setTimeout(loadPool, 220); schedulePersist(); });
ui.poolSort.addEventListener("change", () => { currentView().sort = ui.poolSort.value; poolPage = 1; loadPool(); schedulePersist(); }); ui.poolLanguage.addEventListener("change", () => { currentView().language = ui.poolLanguage.value; renderPoolItems(); schedulePersist(); });
ui.selectedOnly.addEventListener("change", () => { currentView().selectedOnly = ui.selectedOnly.checked; poolPage = 1; loadPool(); schedulePersist(); });
ui.clearFilters.addEventListener("click", () => { const language = currentView().language; poolViews[activeSection] = { ...defaultView(), language }; ui.poolSearch.value = ""; ui.poolSort.value = ""; ui.selectedOnly.checked = false; poolPage = 1; loadPool(); schedulePersist(); });
ui.poolPrev.addEventListener("click", () => { poolPage -= 1; loadPool(); }); ui.poolNext.addEventListener("click", () => { poolPage += 1; loadPool(); });
ui.selectPage.addEventListener("click", () => selectCurrentPage(true)); ui.selectAllPool.addEventListener("click", () => { draft.pools[activeSection] = { mode: "all", ids: [], excluded_ids: [] }; renderPoolItems(); renderDimensions(); updatePoolOverview(); updatePoseConflict(); schedulePersist(); });
ui.clearPool.addEventListener("click", () => { draft.pools[activeSection] = { mode: "include", ids: [], excluded_ids: [] }; renderPoolItems(); renderDimensions(); updatePoolOverview(); updatePoseConflict(); schedulePersist(); }); ui.addCustom.addEventListener("click", () => openCustom());
ui.customForm.addEventListener("submit", saveCustom); ui.cancelCustom.addEventListener("click", () => ui.customDialog.close()); ui.closeCustom.addEventListener("click", () => ui.customDialog.close()); ui.deleteCustom.addEventListener("click", deleteCustomItem);
ui.favoriteForm.addEventListener("submit", saveFavorite); ui.removeFavorite.addEventListener("click", removeFavorite); ui.closeFavorite.addEventListener("click", () => ui.favoriteDialog.close()); ui.cancelFavorite.addEventListener("click", () => ui.favoriteDialog.close());
ui.groupForm.addEventListener("submit", saveGroup); ui.deleteGroup.addEventListener("click", deleteFavoriteGroup); ui.closeGroup.addEventListener("click", () => ui.groupDialog.close()); ui.cancelGroup.addEventListener("click", () => ui.groupDialog.close());
ui.addLora.addEventListener("click", openLoraDialog); ui.resetLoras.addEventListener("click", () => { draft.loras = clone(defaults?.loras || []); renderLoras(); schedulePersist(); toast("已恢复模板默认 LoRA"); }); ui.closeLora.addEventListener("click", () => ui.loraDialog.close()); ui.cancelLora.addEventListener("click", () => ui.loraDialog.close()); ui.loraSearch.addEventListener("input", renderLoraCatalog);
ui.female_count.addEventListener("input", updatePeopleTotal); ui.male_count.addEventListener("input", updatePeopleTotal);
ui.resetSettings.addEventListener("click", () => { if (!confirm("恢复全部模板默认设置？")) return; applySettings(defaults); schedulePersist(); toast("已恢复默认设置"); });
ui.clearDraft.addEventListener("click", () => { if (!confirm("清除本地草稿并恢复默认设置？收藏不会被删除。")) return; localStorage.removeItem(DRAFT_KEY); localStorage.removeItem(VIEW_KEY); poolViews = Object.fromEntries(SECTIONS.map(section => [section, defaultView()])); applySettings(defaults); ui.draftStatus.textContent = "本地草稿已清除"; toast("已清除本地草稿"); });
document.querySelectorAll("[data-mobile-view]").forEach(button => button.addEventListener("click", () => { const view = button.dataset.mobileView; document.querySelectorAll(".mobile-tabs button").forEach(item => item.classList.toggle("active", item === button)); document.querySelector(".workspace").dataset.mobileView = view; }));
document.addEventListener("keydown", event => { if (event.key === "Escape" && ui.poolDrawer.classList.contains("open")) closePool(); });

async function initialize() {
  try { const payload = await request("/api/config"); config = payload; defaults = payload.defaults; loadStoredView(); const restored = loadStoredDraft(); if (!restored) { applySettings(defaults); ui.draftStatus.textContent = "使用模板默认设置"; } initialized = true; }
  catch (error) { toast(error.message); }
  await Promise.all([refreshConnection(), pollBatch(), loadHistory(1), loadLoraInventory()]);
  setInterval(pollBatch, 1200); setInterval(refreshConnection, 5000);
}
initialize();
