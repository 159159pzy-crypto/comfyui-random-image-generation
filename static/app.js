const SECTION_META = {
  character: { label: "角色", kicker: "CHARACTER", singular: "角色" },
  clothing: { label: "服装", kicker: "CLOTHING", singular: "服装" },
  pose: { label: "姿势", kicker: "POSE", singular: "姿势" },
  background: { label: "背景", kicker: "BACKGROUND", singular: "背景" },
  expression: { label: "表情", kicker: "EXPRESSION", singular: "表情" },
};
const NATIVE_FACET_LABELS = {
  clothing: { categories: "服装分类", traits: "服装特征" },
  pose: { categories: "姿势分类", traits: "姿势特征" },
  background: { categories: "场景分类", traits: "场景特征" },
  expression: { categories: "表情分类", traits: "表情特征" },
};
const SECTIONS = Object.keys(SECTION_META);
const DRAFT_KEY = "anima-random-studio:draft:v2";
const LEGACY_DRAFT_KEY = "anima-random-studio:draft:v1";
const VIEW_KEY = "anima-random-studio:pool-view:v1";
const THEME_KEY = "anima-random-studio:theme:v1";
const GROUP_TREE_KEY = "anima-random-studio:favorite-tree:v1";
const INTERNAL_HIRES_PERCENT = 45;
const LEGACY_DEFAULT_LORAS = [
  { filename: "anima-highres-aesthetic-boost.safetensors", enabled: true, strength: 0.75 },
  { filename: "BlueArchiveStyleB1.safetensors", enabled: true, strength: 0.95 },
  { filename: "Cunnyfunkyv3.safetensors", enabled: true, strength: 0.75 },
];
const ui = Object.fromEntries([
  "connection", "startButton", "stopButton", "batchState", "progressCount", "progressFill", "batchError",
  "settingsForm", "count", "female_count", "male_count", "peopleTotal", "dimensionList", "poolOverview",
  "character_detail", "manual_artist", "manageArtists", "artistFavorites", "quality_prompt", "extra_prompt", "negative_prompt", "width", "height",
  "steps", "cfg", "gallery", "emptyState", "historyCount", "prevPage", "nextPage", "pageLabel", "detailDialog",
  "closeDialog", "detailImage", "detailMeta", "detailStats", "detailSelection", "detailPositive", "detailNegative",
  "restoreSettings", "deleteRecord", "toast", "poolDrawer", "drawerBackdrop", "closePool", "poolTitle", "poolKicker", "poolMeta",
  "poolSearch", "poolSort", "poolLanguage", "togglePoolSidebar", "selectedOnly", "clearFilters", "selectPage", "selectAllPool", "clearPool", "addCustom", "importCustom",
  "poolSidebar", "poolGrid", "poolEmpty", "poseConflict", "poolSelectionLabel", "poolPrev", "poolNext", "poolPageLabel", "confirmPool",
  "customDialog", "customForm", "closeCustom", "cancelCustom", "customDialogTitle", "customId", "customTitle", "customSubtitle",
  "customGenderField", "customGender", "customCharacterMeta", "customHair", "customEye", "customSeries", "customPoseMeta",
  "customCategory", "customTraits", "customPrompt", "customGroupChecks", "deleteCustom", "loraSummary", "loraList", "loraEmpty", "addLora", "resetLoras",
  "loraDialog", "closeLora", "cancelLora", "loraSearch", "loraCatalog", "loraCatalogEmpty", "loraCatalogMeta",
  "draftStatus", "resetSettings", "clearDraft", "favoriteDialog", "favoriteForm", "favoriteTitle", "favoriteGroups",
  "favoriteNickname", "removeFavorite", "closeFavorite", "cancelFavorite", "groupDialog", "groupForm", "groupDialogTitle",
  "groupId", "groupName", "groupNameField", "deleteGroup", "closeGroup", "cancelGroup", "saveGroupButton", "importChildGroup",
  "childGroupDialog", "childGroupForm", "childGroupTitle", "childGroupHint", "childGroupSearch", "childGroupList", "childGroupEmpty", "childGroupError", "closeChildGroup", "cancelChildGroup", "confirmChildGroup",
  "deleteGroupDialog", "deleteGroupForm", "deleteGroupTitle", "deleteGroupSummary", "deleteGroupStats", "deleteGroupKeep", "deleteGroupKeepHint", "deleteGroupItemsOption", "deleteGroupItems", "deleteGroupItemsTitle", "deleteGroupItemsHint", "deleteGroupError", "closeDeleteGroup", "cancelDeleteGroup", "confirmDeleteGroup",
  "customGroupDialog", "customGroupForm", "customGroupDialogTitle",
  "customGroupId", "customGroupName", "deleteCustomGroup", "closeCustomGroup", "cancelCustomGroup", "importDialog", "importDialogTitle", "importFile",
  "importJsonTemplate", "importCsvTemplate", "importTargetGroups", "closeImport", "cancelImport", "importHint", "importSummary", "importRows", "commitImport", "artistDialog", "closeArtists",
  "cancelArtists", "saveCurrentArtists", "artistFavoriteList"
  , "artistFavoriteCount", "presetSummary", "presetName", "savePreset", "presetError", "presetList", "presetEmpty",
  "model_name", "hires_enabled", "hires_model_name", "hiresFields", "repairSummary", "resourceWarning",
  "detailer_hand", "detailer_nsfw", "detailer_face", "detailer_eyes", "themeToggle", "appError"
].map(id => [id, document.getElementById(id)]));

const missingUi = Object.entries(ui).filter(([, element]) => !element).map(([id]) => id);
if (missingUi.length) {
  const message = `界面资源版本不一致，缺少控件：${missingUi.join("、")}。请刷新页面。`;
  const errorPanel = document.getElementById("appError");
  if (errorPanel) { errorPanel.textContent = message; errorPanel.hidden = false; }
  throw new Error(message);
}

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
let editingCustomGroup = null;
let editingFavoriteItem = null;
let loraInventory = [];
let loraInventoryLoaded = false;
let loraInventoryError = "";
let favoritesData = { groups: [], items: [], favorite_keys: [] };
let favoritesAvailable = false;
let customGroups = [];
let artistFavoritesData = { groups: [], items: [] };
let stylePresets = [];
let resources = { models: [], upscale_models: [] };
let resourceError = "";
let resourcesLoaded = false;
let hiresPercent = INTERNAL_HIRES_PERCENT;
let importPreview = null;
let editingGroup = null;
let childGroupParent = null;
let selectedChildSource = "";
let pendingGroupDelete = null;
let collapsedFavoriteGroups = (() => {
  try {
    const value = JSON.parse(localStorage.getItem(GROUP_TREE_KEY) || "{}");
    return Object.fromEntries(SECTIONS.map(section => [section, new Set(value[section] || [])]));
  } catch {
    return Object.fromEntries(SECTIONS.map(section => [section, new Set()]));
  }
})();
const poseItemCache = new Map();

function defaultView() {
  return { query: "", sort: "", language: "zh", selectedOnly: false, scrollTop: 0, filters: { gender: "", hair: "", eye: "", series: "", categories: [], traits: [], collection: "", customGroup: "" } };
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

function applyTheme(theme, persist = false) {
  const next = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  document.querySelector('meta[name="theme-color"]').content = next === "dark" ? "#1c1c1e" : "#f5f5f7";
  ui.themeToggle.title = next === "dark" ? "切换浅色主题" : "切换深色主题";
  ui.themeToggle.setAttribute("aria-label", ui.themeToggle.title);
  ui.themeToggle.setAttribute("aria-pressed", String(next === "dark"));
  ui.themeToggle.querySelector("span").textContent = next === "dark" ? "☀" : "☾";
  if (persist) localStorage.setItem(THEME_KEY, next);
}

function initialTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

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
  merged.hires = { ...(defaults?.hires || { enabled: true, model_name: "", percent: INTERNAL_HIRES_PERCENT }), ...(raw?.hires || {}) };
  merged.detailers = { ...(defaults?.detailers || { hand: false, nsfw: false, face: false, eyes: false }), ...(raw?.detailers || {}) };
  merged.manual_artist = canonicalArtists(merged.manual_artist);
  return merged;
}

function applySettings(raw) {
  const settings = normalizeSettings(raw || defaults || {});
  for (const [name, value] of Object.entries(settings)) {
    const element = ui[name];
    if (element && !["object", "undefined"].includes(typeof value)) element.value = value;
  }
  ensureSelectValue(ui.model_name, settings.model_name);
  ensureSelectValue(ui.hires_model_name, settings.hires.model_name);
  ui.hires_enabled.checked = Boolean(settings.hires.enabled);
  hiresPercent = Number(settings.hires.percent) || INTERNAL_HIRES_PERCENT;
  for (const name of ["hand", "nsfw", "face", "eyes"]) ui[`detailer_${name}`].checked = Boolean(settings.detailers[name]);
  draft = {
    modes: Object.fromEntries(SECTIONS.map(section => [section, settings[`random_${section}`] ? "pool" : (settings[`fixed_${section}`] ? "fixed" : "off")])),
    counts: Object.fromEntries(SECTIONS.map(section => [section, Number(settings[`random_${section}_count`] || 1)])),
    fixed: Object.fromEntries(SECTIONS.map(section => [section, settings[`fixed_${section}`] || ""])),
    pools: clone(settings.pools || defaultPools()),
    loras: clone(settings.loras || []),
  };
  renderDimensions(); renderLoras(); updatePeopleTotal(); updatePoolOverview(); updateRepairControls();
}

function readSettings() {
  const normalizedArtist = canonicalArtists(ui.manual_artist.value);
  if (ui.manual_artist.value !== normalizedArtist) ui.manual_artist.value = normalizedArtist;
  const settings = {
    count: Number(ui.count.value), female_count: Number(ui.female_count.value), male_count: Number(ui.male_count.value),
    character_detail: ui.character_detail.value, manual_artist: normalizedArtist, quality_prompt: ui.quality_prompt.value,
    extra_prompt: ui.extra_prompt.value, negative_prompt: ui.negative_prompt.value, width: Number(ui.width.value),
    height: Number(ui.height.value), steps: Number(ui.steps.value), cfg: Number(ui.cfg.value), pools: clone(draft.pools), loras: clone(draft.loras),
    model_name: ui.model_name.value,
    hires: { enabled: ui.hires_enabled.checked, model_name: ui.hires_model_name.value, percent: hiresPercent || INTERNAL_HIRES_PERCENT },
    detailers: { hand: ui.detailer_hand.checked, nsfw: ui.detailer_nsfw.checked, face: ui.detailer_face.checked, eyes: ui.detailer_eyes.checked },
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
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ version: 2, savedAt, settings: readSettings() }));
    localStorage.removeItem(LEGACY_DRAFT_KEY);
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
    const current = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
    const legacy = current ? null : JSON.parse(localStorage.getItem(LEGACY_DRAFT_KEY) || "null");
    const payload = current || legacy;
    if (![1, 2].includes(payload?.version) || !payload.settings || typeof payload.settings !== "object") return false;
    const settings = clone(payload.settings);
    if (payload.version === 1 && isLegacyDefaultLoras(settings.loras)) settings.loras = [];
    applySettings(settings);
    if (payload.version === 1) {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ version: 2, savedAt: payload.savedAt, settings }));
      localStorage.removeItem(LEGACY_DRAFT_KEY);
    }
    ui.draftStatus.textContent = `已恢复草稿 ${formatSavedTime(payload.savedAt)}`;
    return true;
  } catch { localStorage.removeItem(DRAFT_KEY); localStorage.removeItem(LEGACY_DRAFT_KEY); return false; }
}

function isLegacyDefaultLoras(value) {
  if (!Array.isArray(value) || value.length !== LEGACY_DEFAULT_LORAS.length) return false;
  return value.every((item, index) => {
    const expected = LEGACY_DEFAULT_LORAS[index];
    return normalizedPath(item?.filename) === normalizedPath(expected.filename)
      && Boolean(item?.enabled) === expected.enabled
      && Number(item?.strength) === expected.strength;
  });
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
      <div class="dimension-body"><label class="draw-count"><span>抽取数量</span><input type="number" min="1" max="${section === "expression" ? 1 : 5}" step="1" value="${section === "expression" ? 1 : count}" data-count="${section}" ${mode !== "pool" || section === "expression" ? "disabled" : ""}></label><span class="mode-note">${mode === "pool" ? `${selected} 项候选` : mode === "fixed" ? "使用固定提示词" : "不加入提示词"}</span></div>
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

function normalizedPath(value) { return String(value || "").replaceAll("\\", "/").toLowerCase(); }
function loraItem(filename) {
  const identity = normalizedPath(filename);
  const exact = loraInventory.find(item => normalizedPath(item.filename) === identity);
  if (exact) return exact;
  if (identity.includes("/")) return null;
  const matches = loraInventory.filter(item => normalizedPath(item.filename).split("/").pop() === identity);
  return matches.length === 1 ? matches[0] : null;
}
function loraPresentation(inventoryItem, fallback) {
  const path = String(inventoryItem?.normalized_path || fallback || "").replaceAll("\\", "/");
  const parts = path.split("/").filter(Boolean);
  const basename = inventoryItem?.basename || parts.pop() || "未命名 LoRA";
  const folder = inventoryItem?.folder || parts.join("/") || "根目录";
  return { path, basename, folder, label: `${folder} / ${basename}` };
}
function renderLoras() {
  const loras = Array.isArray(draft.loras) ? draft.loras : []; const enabled = loras.filter(item => item.enabled).length;
  ui.loraSummary.textContent = `${enabled} 启用 / ${loras.length} 配置`; ui.loraEmpty.hidden = loras.length > 0;
  ui.loraList.replaceChildren(...loras.map((item, index) => {
    const available = !loraInventoryLoaded || Boolean(loraItem(item.filename)); const high = Math.abs(Number(item.strength)) > 2; const row = document.createElement("div");
    row.className = `lora-row${available ? "" : " missing"}`;
    const inventoryItem = loraItem(item.filename); const display = loraPresentation(inventoryItem, item.filename);
    row.innerHTML = `<input class="lora-toggle" type="checkbox" ${item.enabled ? "checked" : ""} title="启用 LoRA" aria-label="启用 ${escapeHtml(item.filename)}"><div class="lora-name"><strong title="${escapeHtml(display.path)}">${escapeHtml(display.label)}</strong><small class="${available && !high ? "" : "lora-warning"}">${available ? (high ? "高强度 · 模型 + CLIP" : "模型 + CLIP") : "文件不存在"}</small></div><input class="lora-strength" type="number" min="-100" max="100" step="0.05" value="${Number(item.strength)}" title="LoRA strength" aria-label="${escapeHtml(item.filename)} 强度"><div class="lora-actions"><button class="icon-button dark" type="button" data-lora-up title="上移" aria-label="上移">↑</button><button class="icon-button dark" type="button" data-lora-down title="下移" aria-label="下移">↓</button><button class="icon-button dark" type="button" data-lora-remove title="移除" aria-label="移除">×</button></div>`;
    row.querySelector(".lora-toggle").addEventListener("change", event => { item.enabled = event.target.checked; renderLoras(); schedulePersist(); });
    row.querySelector(".lora-strength").addEventListener("change", event => { const value = Number(event.target.value); if (!Number.isFinite(value) || value < -100 || value > 100) { toast("LoRA strength 必须在 -100 到 100 之间"); renderLoras(); return; } item.strength = Math.round(value * 100) / 100; renderLoras(); schedulePersist(); });
    row.querySelector("[data-lora-up]").disabled = index === 0; row.querySelector("[data-lora-down]").disabled = index === loras.length - 1;
    row.querySelector("[data-lora-up]").addEventListener("click", () => moveLora(index, -1)); row.querySelector("[data-lora-down]").addEventListener("click", () => moveLora(index, 1));
    row.querySelector("[data-lora-remove]").addEventListener("click", () => { draft.loras.splice(index, 1); renderLoras(); renderLoraCatalog(); schedulePersist(); }); return row;
  }));
}
function moveLora(index, delta) { const next = index + delta; if (next < 0 || next >= draft.loras.length) return; [draft.loras[index], draft.loras[next]] = [draft.loras[next], draft.loras[index]]; renderLoras(); schedulePersist(); }
async function loadLoraInventory() { try { const data = await request("/api/loras"); loraInventory = data.items || []; loraInventoryLoaded = true; loraInventoryError = ""; renderLoras(); renderLoraCatalog(); } catch (error) { loraInventoryLoaded = false; loraInventoryError = error.message; renderLoraCatalog(); } updateRepairControls(); }
function renderLoraCatalog() { if (!ui.loraCatalog) return; const query = String(ui.loraSearch.value || "").trim().toLowerCase(); const configured = new Set(draft.loras.map(item => normalizedPath(loraItem(item.filename)?.filename || item.filename))); const items = loraInventory.filter(item => !query || `${item.normalized_path || item.filename} ${item.display_name || ""}`.toLowerCase().includes(query)); ui.loraCatalogEmpty.hidden = Boolean(loraInventoryError) || items.length > 0; ui.loraCatalog.replaceChildren(...items.map(item => { const configuredItem = configured.has(normalizedPath(item.filename)); const display = loraPresentation(item, item.filename); const button = document.createElement("button"); button.type = "button"; button.className = "lora-catalog-item"; button.disabled = configuredItem; button.innerHTML = `<span><strong title="${escapeHtml(display.path)}">${escapeHtml(display.label)}</strong><small>${escapeHtml(item.display_name || display.basename)}${configuredItem ? " · 已配置" : ""}</small></span><span aria-hidden="true">${configuredItem ? "✓" : "+"}</span>`; button.addEventListener("click", () => { draft.loras.push({ filename: item.filename, enabled: true, strength: 1 }); renderLoras(); renderLoraCatalog(); schedulePersist(); }); return button; })); ui.loraCatalogMeta.textContent = loraInventoryError || `${loraInventory.length} 个本地 LoRA`; }
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
async function loadCustomGroups(section = activeSection) { try { customGroups = (await request(`/api/custom-groups/${section}`)).groups || []; } catch (error) { customGroups = []; toast(`自定义分组不可用：${error.message}`); } }

async function openPool(section) {
  activeSection = section; poolPage = 1; const view = currentView();
  ui.poolDrawer.classList.add("open"); ui.poolDrawer.setAttribute("aria-hidden", "false"); ui.drawerBackdrop.hidden = false;
  ui.poolTitle.textContent = `${SECTION_META[section].label}池`; ui.poolKicker.textContent = SECTION_META[section].kicker;
  ui.poolSearch.value = view.query; ui.poolSort.value = view.sort; ui.poolLanguage.value = view.language; ui.selectedOnly.checked = view.selectedOnly;
  await Promise.all([loadFavorites(section), loadCustomGroups(section)]); await loadPool(); schedulePersist();
}
function closePool() { currentView().scrollTop = ui.poolGrid.scrollTop; setPoolSidebarOpen(false); ui.poolDrawer.classList.remove("open"); ui.poolDrawer.setAttribute("aria-hidden", "true"); ui.drawerBackdrop.hidden = true; schedulePersist(); }
function setPoolSidebarOpen(open) { ui.poolSidebar.classList.toggle("mobile-open", open); ui.togglePoolSidebar.setAttribute("aria-expanded", String(open)); ui.togglePoolSidebar.title = open ? "关闭分类导航" : "打开分类导航"; ui.togglePoolSidebar.setAttribute("aria-label", ui.togglePoolSidebar.title); }

function poolQueryBody() { const view = currentView(); return { page: poolPage, limit: 48, q: view.query, sort: view.sort, collection: view.filters.collection, custom_group: view.filters.customGroup, categories: view.filters.categories, traits: view.filters.traits, gender: view.filters.gender, hair: view.filters.hair, eye: view.filters.eye, series: view.filters.series }; }
async function loadPool() {
  const requestId = ++poolRequest; const view = currentView();
  try {
    const query = poolQueryBody(); let data;
    if (view.selectedOnly) data = await request(`/api/pools/${activeSection}/query`, { method: "POST", body: JSON.stringify({ ...query, selection: draft.pools[activeSection] }) });
    else { const params = new URLSearchParams({ page: query.page, limit: query.limit, q: query.q, sort: query.sort, collection: query.collection, custom_group: query.custom_group, gender: query.gender, hair: query.hair, eye: query.eye, series: query.series }); query.categories.forEach(value => params.append("category", value)); query.traits.forEach(value => params.append("trait", value)); data = await request(`/api/pools/${activeSection}?${params}`); }
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

function favoriteChildren(parentId) {
  return (favoritesData.groups || []).filter(group => (group.parentId || null) === (parentId || null));
}
function favoriteGroup(id) { return (favoritesData.groups || []).find(group => group.id === id) || null; }
function favoriteDescendantIds(groupId) {
  const ids = new Set(); const pending = [groupId];
  while (pending.length) { const id = pending.pop(); if (ids.has(id)) continue; ids.add(id); favoriteChildren(id).forEach(group => pending.push(group.id)); }
  return ids;
}
function favoriteGroupPath(group) {
  const names = []; const seen = new Set(); let current = group;
  while (current && !seen.has(current.id)) { seen.add(current.id); names.unshift(current.id === "default" ? "我的收藏" : current.name); current = current.parentId ? favoriteGroup(current.parentId) : null; }
  return names.join(" / ");
}
function persistFavoriteTree() {
  const value = Object.fromEntries(SECTIONS.map(section => [section, [...collapsedFavoriteGroups[section]]]));
  localStorage.setItem(GROUP_TREE_KEY, JSON.stringify(value));
}
function focusFavoriteTreeRow(groupId) {
  queueMicrotask(() => {
    const row = ui.poolSidebar.querySelector(`.favorite-tree-row[data-group-id="${CSS.escape(groupId)}"]`);
    if (!row) return;
    ui.poolSidebar.querySelectorAll('.favorite-tree [role="treeitem"]').forEach(item => { item.tabIndex = item === row ? 0 : -1; });
    row.focus();
  });
}
function toggleFavoriteTreeGroup(groupId, restoreFocus = false) {
  const collapsed = collapsedFavoriteGroups[activeSection];
  collapsed.has(groupId) ? collapsed.delete(groupId) : collapsed.add(groupId);
  persistFavoriteTree(); renderPoolSidebar();
  if (restoreFocus) focusFavoriteTreeRow(groupId);
}
function renderFavoriteTree(container, view) {
  container.className = "favorite-tree"; container.setAttribute("role", "tree"); container.setAttribute("aria-label", "收藏分组树");
  const allRow = document.createElement("div"); allRow.className = `favorite-tree-row favorite-all-row${view.filters.collection ? "" : " active"}`; allRow.setAttribute("role", "treeitem"); allRow.setAttribute("aria-level", "1"); allRow.tabIndex = view.filters.collection ? -1 : 0;
  allRow.innerHTML = `<span class="tree-spacer" aria-hidden="true"></span><button type="button" class="group-select"><span>全部${escapeHtml(SECTION_META[activeSection].label)}</span><small>${config.catalog?.counts?.[activeSection] || ""}</small></button><span></span>`;
  allRow.querySelector(".group-select").addEventListener("click", () => { view.filters.collection = ""; poolPage = 1; loadPool(); schedulePersist(); renderPoolSidebar(); });
  container.append(allRow);

  const collapsed = collapsedFavoriteGroups[activeSection];
  const appendGroup = (group, depth) => {
    const children = favoriteChildren(group.id); const isCollapsed = collapsed.has(group.id); const active = view.filters.collection === group.id;
    const row = document.createElement("div"); row.className = `favorite-tree-row${active ? " active" : ""}`; row.dataset.groupId = group.id; row.dataset.parentId = group.parentId || ""; row.style.setProperty("--tree-depth", String(Math.min(depth, 4))); row.title = favoriteGroupPath(group); row.setAttribute("role", "treeitem"); row.setAttribute("aria-level", String(depth + 1)); row.setAttribute("aria-selected", String(active)); if (children.length) row.setAttribute("aria-expanded", String(!isCollapsed)); row.tabIndex = active ? 0 : -1;
    const disclosure = document.createElement(children.length ? "button" : "span"); disclosure.className = children.length ? "tree-disclosure" : "tree-spacer"; if (children.length) { disclosure.type = "button"; disclosure.tabIndex = -1; disclosure.title = isCollapsed ? "展开子分组" : "折叠子分组"; disclosure.setAttribute("aria-label", disclosure.title); disclosure.textContent = "›"; disclosure.addEventListener("click", event => { event.stopPropagation(); toggleFavoriteTreeGroup(group.id, true); }); }
    const button = document.createElement("button"); button.type = "button"; button.className = "group-select"; button.innerHTML = `<span>${escapeHtml(group.id === "default" ? "我的收藏" : group.name)}</span><small>${group.totalCount ?? 0}</small>`; button.addEventListener("click", () => { view.filters.collection = group.id; poolPage = 1; loadPool(); schedulePersist(); renderPoolSidebar(); });
    const edit = document.createElement("button"); edit.type = "button"; edit.className = "collection-edit"; edit.title = group.isSystem ? "管理子分组" : "管理收藏分组"; edit.setAttribute("aria-label", `${edit.title} ${group.id === "default" ? "我的收藏" : group.name}`); edit.textContent = "···"; edit.addEventListener("click", event => { event.stopPropagation(); openGroupDialog(group); });
    row.append(disclosure, button, edit); container.append(row);
    if (!isCollapsed) children.forEach(child => appendGroup(child, depth + 1));
  };
  favoriteChildren(null).forEach(group => appendGroup(group, 0));
}
function handleFavoriteTreeKeydown(event) {
  const row = event.target.closest('[role="treeitem"]');
  if (!row || !row.closest(".favorite-tree")) return;
  const rows = [...row.closest(".favorite-tree").querySelectorAll('[role="treeitem"]')];
  const index = rows.indexOf(row); const groupId = row.dataset.groupId || ""; const group = groupId ? favoriteGroup(groupId) : null;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); const next = rows[index + (event.key === "ArrowDown" ? 1 : -1)]; if (next) { row.tabIndex = -1; next.tabIndex = 0; next.focus(); } }
  else if (event.key === "ArrowRight" && group) { event.preventDefault(); if (row.getAttribute("aria-expanded") === "false") toggleFavoriteTreeGroup(groupId, true); else { const next = rows[index + 1]; if (next) { row.tabIndex = -1; next.tabIndex = 0; next.focus(); } } }
  else if (event.key === "ArrowLeft" && group) { event.preventDefault(); if (row.getAttribute("aria-expanded") === "true") toggleFavoriteTreeGroup(groupId, true); else if (group.parentId) { const parent = rows.find(item => item.dataset.groupId === group.parentId); if (parent) { row.tabIndex = -1; parent.tabIndex = 0; parent.focus(); } } }
  else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); row.querySelector(".group-select")?.click(); }
}

function renderPoolSidebar() {
  const view = currentView(); const nav = document.createDocumentFragment();
  const collections = document.createElement("section"); collections.className = "facet-section collections";
  collections.innerHTML = `<div class="facet-heading"><h3>收藏分组</h3><button type="button" title="新建收藏分组" aria-label="新建收藏分组">+</button></div><div></div>`;
  collections.querySelector(".facet-heading button").disabled = !favoritesAvailable; collections.querySelector(".facet-heading button").addEventListener("click", () => openGroupDialog());
  const collectionBody = collections.lastElementChild;
  renderFavoriteTree(collectionBody, view);
  nav.append(collections);
  const customSection = document.createElement("section"); customSection.className = "facet-section custom-group-section";
  customSection.innerHTML = `<div class="facet-heading"><h3>自定义分组</h3><button type="button" title="新建自定义分组" aria-label="新建自定义分组">+</button></div><div></div>`;
  customSection.querySelector(".facet-heading button").addEventListener("click", () => openCustomGroupDialog());
  const customBody = customSection.lastElementChild;
  const unfiltered = document.createElement("button"); unfiltered.type = "button"; unfiltered.className = view.filters.customGroup ? "" : "active"; unfiltered.innerHTML = `<span>不限分组</span><small></small>`; unfiltered.addEventListener("click", () => { view.filters.customGroup = ""; poolPage = 1; loadPool(); schedulePersist(); }); customBody.append(unfiltered);
  for (const group of customGroups) { const row = document.createElement("div"); row.className = "collection-row"; const button = document.createElement("button"); button.type = "button"; button.className = view.filters.customGroup === group.id ? "active" : ""; button.innerHTML = `<span>${escapeHtml(group.name)}</span><small>${group.count}</small>`; button.addEventListener("click", () => { view.filters.customGroup = group.id; poolPage = 1; loadPool(); schedulePersist(); }); const edit = document.createElement("button"); edit.type = "button"; edit.className = "collection-edit"; edit.title = "编辑自定义分组"; edit.setAttribute("aria-label", `编辑自定义分组 ${group.name}`); edit.textContent = "···"; edit.addEventListener("click", () => openCustomGroupDialog(group)); row.append(button, edit); customBody.append(row); }
  nav.append(customSection);
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

function reconcileFavoriteCollection() { const view = currentView(); if (view.filters.collection && !favoriteGroup(view.filters.collection)) { view.filters.collection = ""; schedulePersist(); return true; } return false; }
async function toggleFavorite(item, favorite) { try { favoritesData = await request(`/api/favorites/${activeSection}/item`, { method: "PUT", body: JSON.stringify({ id: item.id, favorite }) }); favoritesAvailable = true; const resetCollection = reconcileFavoriteCollection(); if (resetCollection || (!favorite && currentView().filters.collection)) await loadPool(); else { renderPoolItems(); renderPoolSidebar(); } toast(favorite ? "已加入 Anima 收藏" : "已取消收藏"); } catch (error) { toast(error.message); } }
function orderedFavoriteGroups() { const result = []; const append = (group, depth) => { result.push({ group, depth }); favoriteChildren(group.id).forEach(child => append(child, depth + 1)); }; favoriteChildren(null).forEach(group => append(group, 0)); return result; }
function openFavoriteDialog(item) { editingFavoriteItem = item; const saved = favoriteMap().get(favoriteKey(item)) || {}; ui.favoriteTitle.textContent = displayTitle(item); ui.favoriteNickname.value = saved.nickname || ""; ui.favoriteGroups.replaceChildren(...orderedFavoriteGroups().map(({ group, depth }) => { const label = document.createElement("label"); label.className = "favorite-group-check tree-check"; label.style.setProperty("--tree-depth", String(Math.min(depth, 4))); label.title = favoriteGroupPath(group); label.innerHTML = `<input type="checkbox" value="${escapeHtml(group.id)}" ${(saved.groupIds || []).includes(group.id) ? "checked" : ""}><span>${escapeHtml(group.id === "default" ? "我的收藏" : group.name)}</span><small>${group.totalCount ?? 0}</small>`; return label; })); ui.favoriteDialog.showModal(); }
async function saveFavorite(event) { event.preventDefault(); if (!editingFavoriteItem) return; const groupIds = [...ui.favoriteGroups.querySelectorAll("input:checked")].map(input => input.value); try { favoritesData = await request(`/api/favorites/${activeSection}/item`, { method: "PUT", body: JSON.stringify({ id: editingFavoriteItem.id, favorite: true, groupIds, nickname: ui.favoriteNickname.value }) }); ui.favoriteDialog.close(); if (reconcileFavoriteCollection()) await loadPool(); else { renderPoolItems(); renderPoolSidebar(); } toast("收藏设置已保存"); } catch (error) { toast(error.message); } }
async function removeFavorite() { if (!editingFavoriteItem) return; try { favoritesData = await request(`/api/favorites/${activeSection}/item`, { method: "PUT", body: JSON.stringify({ id: editingFavoriteItem.id, favorite: false }) }); ui.favoriteDialog.close(); reconcileFavoriteCollection(); await loadPool(); toast("已取消收藏"); } catch (error) { toast(error.message); } }

function openGroupDialog(group = null) { editingGroup = group; ui.groupId.value = group?.id || ""; ui.groupName.value = group?.name || ""; ui.groupDialogTitle.textContent = !group ? "新建收藏分组" : group.isSystem ? "管理我的收藏" : "编辑收藏分组"; ui.groupNameField.hidden = Boolean(group?.isSystem); ui.groupName.required = !group?.isSystem; ui.deleteGroup.hidden = !group || group.isSystem; ui.importChildGroup.hidden = !group; ui.saveGroupButton.hidden = Boolean(group?.isSystem); ui.groupDialog.showModal(); }
async function saveGroup(event) { event.preventDefault(); const id = ui.groupId.value; try { favoritesData = await request(id ? `/api/favorites/${activeSection}/groups/${encodeURIComponent(id)}` : `/api/favorites/${activeSection}/groups`, { method: id ? "PUT" : "POST", body: JSON.stringify({ name: ui.groupName.value }) }); ui.groupDialog.close(); renderPoolSidebar(); toast(id ? "收藏分组已更新" : "收藏分组已创建"); } catch (error) { toast(error.message); } }
function setChildGroupError(message = "") { ui.childGroupError.textContent = message; ui.childGroupError.hidden = !message; }
function renderChildGroupSources() { const query = ui.childGroupSearch.value.trim().toLocaleLowerCase(); const siblings = favoriteChildren(childGroupParent?.id); const available = customGroups.filter(group => !query || group.name.toLocaleLowerCase().includes(query)); ui.childGroupList.replaceChildren(...available.map(group => { const duplicate = siblings.some(item => item.sourceCustomGroupId === group.id || item.name.trim().toLocaleLowerCase() === group.name.trim().toLocaleLowerCase()); const disabled = !group.count || duplicate; const label = document.createElement("label"); label.className = `child-group-option${disabled ? " disabled" : ""}`; label.title = duplicate ? "当前父组下已经导入" : !group.count ? "空分组不能导入" : `导入 ${group.count} 项`; label.innerHTML = `<input type="radio" name="childGroupSource" value="${escapeHtml(group.id)}" ${selectedChildSource === group.id ? "checked" : ""} ${disabled ? "disabled" : ""}><span><strong>${escapeHtml(group.name)}</strong><small>${duplicate ? "已导入" : !group.count ? "空分组" : `${group.count} 项 · 一次性快照`}</small></span>`; label.querySelector("input").addEventListener("change", () => { selectedChildSource = group.id; ui.confirmChildGroup.disabled = false; setChildGroupError(); }); return label; })); ui.childGroupEmpty.textContent = customGroups.length ? "没有匹配的自定义分组。" : "当前分类还没有自定义分组。"; ui.childGroupEmpty.hidden = available.length > 0; }
function openChildGroupDialog(group = editingGroup) { if (!group) return; childGroupParent = group; selectedChildSource = ""; ui.groupDialog.close(); ui.childGroupTitle.textContent = `导入到“${group.id === "default" ? "我的收藏" : group.name}”`; ui.childGroupHint.textContent = "从当前提示词池的自定义分组创建一次性收藏快照，后续两边独立修改。"; ui.childGroupSearch.value = ""; ui.confirmChildGroup.disabled = true; setChildGroupError(); renderChildGroupSources(); ui.childGroupDialog.showModal(); }
async function importFavoriteChild(event) { event.preventDefault(); if (!childGroupParent || !selectedChildSource || ui.confirmChildGroup.disabled) return; const label = ui.confirmChildGroup.textContent; ui.confirmChildGroup.disabled = true; ui.confirmChildGroup.textContent = "导入中…"; try { favoritesData = await request(`/api/favorites/${activeSection}/groups/${encodeURIComponent(childGroupParent.id)}/children/import`, { method: "POST", body: JSON.stringify({ customGroupId: selectedChildSource }) }); collapsedFavoriteGroups[activeSection].delete(childGroupParent.id); persistFavoriteTree(); ui.childGroupDialog.close(); renderPoolSidebar(); if (currentView().filters.collection === childGroupParent.id) await loadPool(); toast("收藏子分组已导入"); } catch (error) { setChildGroupError(error.message); } finally { ui.confirmChildGroup.disabled = !selectedChildSource; ui.confirmChildGroup.textContent = label; } }

function deleteStat(label, value) { return `<div><strong>${Number(value) || 0}</strong><span>${escapeHtml(label)}</span></div>`; }
function openDeleteGroupDialog(kind, group) { pendingGroupDelete = { kind, group }; const favorite = kind === "favorite"; const subtree = favorite ? favoriteDescendantIds(group.id) : new Set([group.id]); const directCount = favorite ? Number(group.directCount || 0) : Number(group.count || 0); const affectedFavorites = favorite ? (favoritesData.items || []).filter(item => (item.groupIds || []).some(id => subtree.has(id))) : []; const exclusiveCount = favorite ? affectedFavorites.filter(item => !(item.groupIds || []).some(id => !subtree.has(id))).length : Number(group.exclusiveCount || 0); const totalCount = favorite ? affectedFavorites.length : directCount; const sharedCount = Math.max(0, totalCount - exclusiveCount); const childCount = favorite ? Number(group.childCount || 0) : 0; const canDeleteItems = favorite ? Boolean(group.parentId && !childCount && directCount) : directCount > 0; ui.groupDialog.close(); ui.customGroupDialog.close(); ui.deleteGroupTitle.textContent = `删除“${group.id === "default" ? "我的收藏" : group.name}”`; ui.deleteGroupSummary.textContent = favorite && subtree.size > 1 ? `将删除当前父组及 ${subtree.size - 1} 个后代分组，收藏条目默认保留。` : "默认只删除分组结构，条目保持不变。"; ui.deleteGroupStats.innerHTML = favorite ? `${deleteStat("分组", subtree.size)}${deleteStat("聚合收藏", totalCount)}${deleteStat("独占收藏", exclusiveCount)}${deleteStat("共享收藏", sharedCount)}` : `${deleteStat("组内条目", directCount)}${deleteStat("可删除独占项", exclusiveCount)}${deleteStat("保留共享项", sharedCount)}`; ui.deleteGroupKeep.checked = true; ui.deleteGroupKeepHint.textContent = favorite ? `${exclusiveCount} 项失去最后分组的收藏会移入“我的收藏”，其余收藏保留现有分组。` : "所有自定义提示词都会保留，仅解除当前分组关联。"; ui.deleteGroupItemsOption.hidden = !canDeleteItems; ui.deleteGroupItemsTitle.textContent = favorite ? `同时删除 ${exclusiveCount} 项独占收藏` : `同时删除 ${exclusiveCount} 项独占提示词`; ui.deleteGroupItemsHint.textContent = sharedCount ? `${sharedCount} 项共享条目只会解除当前分组关联。` : "没有共享条目。"; ui.deleteGroupError.hidden = true; ui.deleteGroupError.textContent = ""; ui.confirmDeleteGroup.disabled = false; ui.confirmDeleteGroup.textContent = "删除"; ui.deleteGroupDialog.showModal(); }
async function confirmGroupDelete(event) { event.preventDefault(); if (!pendingGroupDelete || ui.confirmDeleteGroup.disabled) return; const { kind, group } = pendingGroupDelete; const deleteItems = !ui.deleteGroupItemsOption.hidden && ui.deleteGroupItems.checked; ui.confirmDeleteGroup.disabled = true; ui.confirmDeleteGroup.textContent = "删除中…"; ui.deleteGroupError.hidden = true; try { if (kind === "favorite") { const payload = await request(`/api/favorites/${activeSection}/groups/${encodeURIComponent(group.id)}?deleteItems=${deleteItems}`, { method: "DELETE" }); favoritesData = payload; if ((payload.deletedGroupIds || []).includes(currentView().filters.collection)) currentView().filters.collection = ""; collapsedFavoriteGroups[activeSection] = new Set([...collapsedFavoriteGroups[activeSection]].filter(id => !(payload.deletedGroupIds || []).includes(id))); persistFavoriteTree(); toast(deleteItems ? `已删除分组和 ${payload.deletedFavoriteCount || 0} 项独占收藏` : `已删除 ${payload.deletedGroupCount || 1} 个分组，条目已保留`); } else { const payload = await request(`/api/custom-groups/${activeSection}/${encodeURIComponent(group.id)}?deleteItems=${deleteItems}`, { method: "DELETE" }); customGroups = payload.groups || []; const deletedIds = new Set(payload.deletedItemIds || []); const selection = draft.pools[activeSection]; selection.ids = selection.ids.filter(id => !deletedIds.has(id)); selection.excluded_ids = selection.excluded_ids.filter(id => !deletedIds.has(id)); deletedIds.forEach(id => poseItemCache.delete(id)); if (currentView().filters.customGroup === group.id) currentView().filters.customGroup = ""; schedulePersist(); toast(deleteItems ? `已删除分组和 ${payload.deletedItemCount || 0} 项独占提示词` : "自定义分组已删除，条目已保留"); } ui.deleteGroupDialog.close(); pendingGroupDelete = null; await loadPool(); renderPoolSidebar(); renderDimensions(); updatePoolOverview(); } catch (error) { ui.deleteGroupError.textContent = error.message; ui.deleteGroupError.hidden = false; } finally { ui.confirmDeleteGroup.disabled = false; ui.confirmDeleteGroup.textContent = "删除"; } }
function deleteFavoriteGroup() { if (editingGroup) openDeleteGroupDialog("favorite", editingGroup); }

function selectCurrentPage(checked) { for (const item of poolItems) { const selection = draft.pools[activeSection]; if (selection.mode === "all") selection.excluded_ids = checked ? selection.excluded_ids.filter(value => value !== item.id) : [...new Set([...selection.excluded_ids, item.id])]; else if (checked) selection.ids = [...new Set([...selection.ids, item.id])]; else selection.ids = selection.ids.filter(value => value !== item.id); } renderPoolItems(); renderDimensions(); updatePoolOverview(); updatePoseConflict(); schedulePersist(); }

function openCustom(item = null) { editingCustom = item; ui.customDialogTitle.textContent = item ? "编辑自定义项" : "新增自定义项"; ui.customId.value = item?.id || ""; ui.customTitle.value = item?.title || ""; ui.customSubtitle.value = item?.subtitle || ""; ui.customGender.value = item?.gender === "1girl" ? "female" : item?.gender === "1boy" ? "male" : "unknown"; ui.customHair.value = item?.hair || ""; ui.customEye.value = item?.eye || ""; ui.customSeries.value = item?.copyright || ""; ui.customCategory.value = item?.categories?.[0] || ""; ui.customTraits.value = (item?.traits || []).join(", "); ui.customPrompt.value = item?.prompt || ""; ui.customGenderField.hidden = activeSection !== "character"; ui.customCharacterMeta.hidden = activeSection !== "character"; ui.customPoseMeta.hidden = activeSection !== "pose"; const selectedGroups = new Set(item?.group_ids || item?.groupIds || []); ui.customGroupChecks.replaceChildren(...customGroups.map(group => { const label = document.createElement("label"); label.className = "favorite-group-check"; label.innerHTML = `<input type="checkbox" value="${escapeHtml(group.id)}" ${selectedGroups.has(group.id) ? "checked" : ""}><span>${escapeHtml(group.name)}</span>`; return label; })); if (!customGroups.length) ui.customGroupChecks.innerHTML = `<div class="inline-empty">尚未创建自定义分组，可在随机池侧栏新建。</div>`; ui.deleteCustom.hidden = !item; ui.customDialog.showModal(); }
async function saveCustom(event) { event.preventDefault(); const payload = { section: activeSection, title: ui.customTitle.value, subtitle: ui.customSubtitle.value, prompt: ui.customPrompt.value, gender: ui.customGender.value, hair: ui.customHair.value, eye: ui.customEye.value, copyright: ui.customSeries.value, categories: ui.customCategory.value ? [ui.customCategory.value] : [], traits: ui.customTraits.value.split(",").map(value => value.trim()).filter(Boolean), groupIds: [...ui.customGroupChecks.querySelectorAll("input:checked")].map(input => input.value) }; try { const item = editingCustom ? await request(`/api/custom-prompts/${editingCustom.id}`, { method: "PUT", body: JSON.stringify(payload) }) : await request("/api/custom-prompts", { method: "POST", body: JSON.stringify(payload) }); if (!selectionHas(activeSection, item.id)) toggleSelection(activeSection, item.id, true); ui.customDialog.close(); await loadCustomGroups(); await loadPool(); toast(editingCustom ? "自定义项已更新" : "自定义项已加入随机池"); } catch (error) { toast(error.message); } }
async function deleteCustomItem() { if (!editingCustom || !confirm("删除这个自定义项？")) return; try { await request(`/api/custom-prompts/${editingCustom.id}`, { method: "DELETE" }); const selection = draft.pools[activeSection]; selection.ids = selection.ids.filter(id => id !== editingCustom.id); selection.excluded_ids = selection.excluded_ids.filter(id => id !== editingCustom.id); ui.customDialog.close(); await loadPool(); renderDimensions(); updatePoolOverview(); schedulePersist(); toast("自定义项已删除"); } catch (error) { toast(error.message); } }

function openCustomGroupDialog(group = null) { editingCustomGroup = group; ui.customGroupId.value = group?.id || ""; ui.customGroupName.value = group?.name || ""; ui.customGroupDialogTitle.textContent = group ? "编辑自定义分组" : "新建自定义分组"; ui.deleteCustomGroup.hidden = !group; ui.customGroupDialog.showModal(); }
async function saveCustomGroup(event) { event.preventDefault(); const id = ui.customGroupId.value; try { const payload = await request(id ? `/api/custom-groups/${activeSection}/${encodeURIComponent(id)}` : `/api/custom-groups/${activeSection}`, { method: id ? "PUT" : "POST", body: JSON.stringify({ name: ui.customGroupName.value }) }); customGroups = payload.groups || []; ui.customGroupDialog.close(); renderPoolSidebar(); toast(id ? "自定义分组已更新" : "自定义分组已创建"); } catch (error) { toast(error.message); } }
function removeCustomGroup() { if (editingCustomGroup) openDeleteGroupDialog("custom", editingCustomGroup); }

function selectedImportGroupIds() { return [...ui.importTargetGroups.querySelectorAll("input:checked")].map(input => input.value); }
function selectedImportGroupNames() { const selected = new Set(selectedImportGroupIds()); return customGroups.filter(group => selected.has(group.id)).map(group => group.name); }
function mergedImportGroupNames(row) { const values = [...(row.groups || []), ...selectedImportGroupNames()]; const seen = new Set(); return values.filter(value => { const key = String(value).trim().toLowerCase(); if (!key || seen.has(key)) return false; seen.add(key); return true; }); }
function renderImportTargetGroups() {
  if (!customGroups.length) {
    ui.importTargetGroups.innerHTML = '<div class="inline-empty">当前池暂无自定义分组，请先在池侧栏新建。</div>';
    return;
  }
  ui.importTargetGroups.replaceChildren(...customGroups.map(group => {
    const label = document.createElement("label");
    label.className = "favorite-group-check";
    label.innerHTML = `<input type="checkbox" value="${escapeHtml(group.id)}"><span>${escapeHtml(group.name)}</span><small>${group.count || 0} 项</small>`;
    label.querySelector("input").addEventListener("change", () => { if (importPreview) renderImportPreview(); });
    return label;
  }));
}
function openImportDialog() {
  importPreview = null;
  const label = SECTION_META[activeSection].label;
  ui.importDialogTitle.textContent = `${label}池批量导入`;
  ui.importJsonTemplate.href = `/api/custom-prompts/templates/${activeSection}/json`;
  ui.importJsonTemplate.textContent = `${label} JSON 模板`;
  ui.importCsvTemplate.href = `/api/custom-prompts/templates/${activeSection}/csv`;
  ui.importCsvTemplate.textContent = `${label} CSV 模板`;
  ui.importFile.value = "";
  ui.importHint.textContent = `仅导入${label}池条目；选择文件后会先校验，不会立即写入。`;
  ui.importSummary.hidden = true;
  ui.importRows.replaceChildren();
  ui.commitImport.disabled = true;
  renderImportTargetGroups();
  ui.importDialog.showModal();
}
async function previewImportFile() {
  const file = ui.importFile.files?.[0];
  if (!file) return;
  const format = file.name.toLowerCase().endsWith(".csv") ? "csv" : file.name.toLowerCase().endsWith(".json") ? "json" : "";
  if (!format) { toast("只支持 JSON 或 CSV 文件"); return; }
  ui.importHint.textContent = `正在校验 ${file.name}`;
  try {
    importPreview = await request("/api/custom-prompts/import/preview", { method: "POST", body: JSON.stringify({ format, content: await file.text(), section: activeSection }) });
    importPreview.filename = file.name;
    renderImportPreview();
  } catch (error) {
    importPreview = null;
    ui.commitImport.disabled = true;
    ui.importHint.textContent = error.message;
    toast(error.message);
  }
}
function renderImportPreview() {
  const summary = importPreview.summary || {};
  ui.importHint.textContent = `${importPreview.filename} · 已完成校验`;
  ui.importSummary.hidden = false;
  ui.importSummary.innerHTML = `<span>新增 ${summary.new || 0}</span><span>冲突 ${summary.conflict || 0}</span><span>错误 ${summary.error || 0}</span>`;
  const labels = { new: "新增", conflict: "冲突", error: "错误" };
  ui.importRows.replaceChildren(...importPreview.rows.map((row, index) => {
    const element = document.createElement("div");
    element.className = "import-row";
    element.dataset.status = row.status;
    const title = row.item?.title || "无法读取";
    const groupNames = mergedImportGroupNames(row);
    const detail = row.error || (row.action === "skip" ? "不导入" : groupNames.join("、") || "未指定分组");
    element.innerHTML = `<span>#${row.row}</span><span class="import-status">${labels[row.status]}</span><strong title="${escapeHtml(title)}">${escapeHtml(title)}</strong><small title="${escapeHtml(detail)}">${escapeHtml(detail)}</small><select aria-label="导入操作" ${row.status === "error" ? "disabled" : ""}>${row.status === "new" ? '<option value="create">导入</option><option value="skip">跳过</option>' : row.status === "conflict" ? '<option value="skip">跳过</option><option value="overwrite">覆盖</option>' : '<option value="skip">跳过</option>'}</select>`;
    const select = element.querySelector("select");
    select.value = row.action;
    select.addEventListener("change", () => { importPreview.rows[index].action = select.value; renderImportPreview(); });
    return element;
  }));
  ui.commitImport.disabled = !importPreview.rows.some(row => row.status !== "error" && row.action !== "skip");
}
async function commitCustomImport() {
  if (!importPreview) return;
  ui.commitImport.disabled = true;
  try {
    const result = await request("/api/custom-prompts/import", { method: "POST", body: JSON.stringify({ rows: importPreview.rows, section: activeSection, targetGroupIds: selectedImportGroupIds() }) });
    ui.importDialog.close();
    await loadCustomGroups();
    await loadPool();
    renderDimensions();
    toast(`导入完成：新增 ${result.imported}，更新 ${result.updated}，跳过 ${result.skipped}`);
  } catch (error) {
    ui.commitImport.disabled = false;
    toast(error.message);
  }
}

function ensureSelectValue(select, value) {
  if (!select || !value) return;
  if (![...select.options].some(option => option.value === value)) select.add(new Option(value, value));
  select.value = value;
}
function populateSelect(select, values, selected) {
  select.replaceChildren(...values.map(value => new Option(value, value)));
  ensureSelectValue(select, selected);
}
function populateResourceSelect(select, values, selected, emptyText) {
  const options = values.map(value => new Option(value, value));
  if (selected && !values.includes(selected)) {
    const missing = new Option(`当前缺失 · ${selected}`, selected);
    missing.dataset.missing = "true";
    options.unshift(missing);
  }
  if (!options.length) options.push(new Option(emptyText, ""));
  select.replaceChildren(...options);
  select.value = selected && [...select.options].some(option => option.value === selected) ? selected : "";
}
function modelLabel(value) {
  const filename = String(value || "未选择模型").replaceAll("\\", "/").split("/").pop();
  return filename.replace(/\.(safetensors|ckpt|pth)$/i, "");
}
async function loadResources() {
  const selectedModel = ui.model_name.value || defaults?.model_name || "";
  const selectedUpscaler = ui.hires_model_name.value || defaults?.hires?.model_name || "";
  resourcesLoaded = false;
  resourceError = "";
  ui.model_name.disabled = true;
  ui.hires_model_name.disabled = true;
  ui.model_name.replaceChildren(new Option("正在读取模型...", ""));
  ui.hires_model_name.replaceChildren(new Option("正在读取放大模型...", ""));
  updateRepairControls();
  try {
    const payload = await request("/api/resources");
    resources = {
      models: Array.isArray(payload.models) ? payload.models : [],
      upscale_models: Array.isArray(payload.upscale_models) ? payload.upscale_models : [],
    };
    resourcesLoaded = true;
    populateResourceSelect(ui.model_name, resources.models, selectedModel, "没有可用主模型");
    populateResourceSelect(ui.hires_model_name, resources.upscale_models, selectedUpscaler, "没有可用放大模型");
    ui.model_name.disabled = resources.models.length === 0;
  } catch (error) {
    resources = { models: [], upscale_models: [] };
    resourceError = `模型资源读取失败：${error.message}`;
    populateResourceSelect(ui.model_name, [], selectedModel, "模型资源离线");
    populateResourceSelect(ui.hires_model_name, [], selectedUpscaler, "放大模型资源离线");
    ui.model_name.disabled = true;
  }
  updateRepairControls();
}
function updateRepairControls() {
  const detailerCount = [ui.detailer_hand, ui.detailer_nsfw, ui.detailer_face, ui.detailer_eyes].filter(input => input.checked).length;
  ui.repairSummary.textContent = `${modelLabel(ui.model_name.value || defaults?.model_name)} · ${ui.hires_enabled.checked ? "高清开启" : "高清关闭"} · ${detailerCount} 项细修`;
  ui.hiresFields.classList.toggle("disabled", !ui.hires_enabled.checked);
  ui.hires_model_name.disabled = !ui.hires_enabled.checked || !resourcesLoaded;
  const settings = readSettings(); const warnings = [];
  if (resourceError) warnings.push(resourceError);
  if (resourcesLoaded && !resources.models.length) warnings.push("ComfyUI 当前没有可用主模型");
  if (resourcesLoaded && !resources.upscale_models.length) warnings.push("ComfyUI 当前没有可用放大模型");
  if (resourcesLoaded && !resources.models.includes(settings.model_name)) warnings.push(`主模型不可用：${settings.model_name}`);
  if (resourcesLoaded && settings.hires.enabled && !resources.upscale_models.includes(settings.hires.model_name)) warnings.push(`高清模型不可用：${settings.hires.model_name}`);
  const missingLoras = settings.loras.filter(item => !loraItem(item.filename)).map(item => item.filename);
  if (loraInventoryLoaded && missingLoras.length) warnings.push(`LoRA 不可用：${missingLoras.join("、")}`);
  ui.resourceWarning.hidden = warnings.length === 0;
  ui.resourceWarning.textContent = warnings.join("；");
}

const PRESET_SETTING_KEYS = ["model_name", "loras", "hires", "detailers", "manual_artist", "quality_prompt", "extra_prompt", "negative_prompt", "width", "height", "steps", "cfg"];
function presetSnapshot() { const settings = readSettings(); return Object.fromEntries(PRESET_SETTING_KEYS.map(key => [key, clone(settings[key])])); }
async function loadStylePresets() { try { const data = await request("/api/style-presets"); stylePresets = data.items || []; renderStylePresets(); } catch (error) { toast(error.message); } }
function setPresetFeedback(message = "", type = "error") {
  ui.presetError.textContent = message;
  ui.presetError.hidden = !message;
  ui.presetError.dataset.type = type;
  ui.presetName.setAttribute("aria-invalid", String(Boolean(message) && type === "error"));
}
function renderStylePresets() {
  ui.presetSummary.textContent = `${stylePresets.length} 个预设`;
  ui.presetEmpty.hidden = stylePresets.length > 0;
  ui.presetList.replaceChildren(...stylePresets.map(item => {
    const row = document.createElement("div"); row.className = `preset-row${item.favorite ? " favorite" : ""}`;
    const activeLoras = (item.settings?.loras || []).filter(lora => lora.enabled).length;
    row.innerHTML = `<button class="preset-apply" type="button"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.settings?.model_name || "未指定模型")} · ${activeLoras} LoRA</small></button><div class="preset-actions"><button type="button" data-preset-favorite title="${item.favorite ? "取消收藏" : "收藏"}" aria-label="${item.favorite ? "取消收藏" : "收藏"}">${item.favorite ? "★" : "☆"}</button><button type="button" data-preset-update title="用当前设置覆盖" aria-label="用当前设置覆盖">↻</button><button type="button" data-preset-rename title="重命名" aria-label="重命名">✎</button><button type="button" data-preset-delete title="删除" aria-label="删除">×</button></div>`;
    row.querySelector(".preset-apply").addEventListener("click", () => { applySettings({ ...readSettings(), ...item.settings }); schedulePersist(); toast(`已应用预设：${item.name}`); });
    row.querySelector("[data-preset-favorite]").addEventListener("click", () => updateStylePreset(item, { favorite: !item.favorite }));
    row.querySelector("[data-preset-update]").addEventListener("click", () => { if (confirm(`用当前设置覆盖“${item.name}”？`)) updateStylePreset(item, { settings: presetSnapshot() }); });
    row.querySelector("[data-preset-rename]").addEventListener("click", () => { const name = prompt("新的预设名称", item.name); if (name?.trim()) updateStylePreset(item, { name: name.trim() }); });
    row.querySelector("[data-preset-delete]").addEventListener("click", () => deleteStylePreset(item));
    return row;
  }));
}
async function createStylePreset() {
  if (ui.savePreset.disabled) return;
  const name = ui.presetName.value.trim();
  if (!name) { setPresetFeedback("请输入预设名称"); ui.presetName.focus(); return; }
  if (stylePresets.some(item => item.name.trim().toLocaleLowerCase() === name.toLocaleLowerCase())) {
    setPresetFeedback("已有同名预设，请改名或使用覆盖按钮"); ui.presetName.focus(); return;
  }
  const originalLabel = ui.savePreset.textContent;
  ui.savePreset.disabled = true;
  ui.savePreset.textContent = "保存中…";
  setPresetFeedback("正在写入本地预设…", "pending");
  try {
    const created = await request("/api/style-presets", { method: "POST", body: JSON.stringify({ name, favorite: false, settings: presetSnapshot() }) });
    const favoriteCount = stylePresets.findIndex(item => !item.favorite);
    stylePresets.splice(favoriteCount < 0 ? stylePresets.length : favoriteCount, 0, created);
    ui.presetName.value = "";
    renderStylePresets();
    setPresetFeedback(`已保存“${created.name}”`, "success");
    toast("风格预设已保存");
  } catch (error) {
    setPresetFeedback(error.message);
    toast(error.message);
  } finally {
    ui.savePreset.disabled = false;
    ui.savePreset.textContent = originalLabel;
  }
}
async function updateStylePreset(item, changes) { try { await request(`/api/style-presets/${encodeURIComponent(item.id)}`, { method: "PUT", body: JSON.stringify(changes) }); await loadStylePresets(); } catch (error) { toast(error.message); } }
async function deleteStylePreset(item) { if (!confirm(`删除预设“${item.name}”？`)) return; try { await request(`/api/style-presets/${encodeURIComponent(item.id)}`, { method: "DELETE" }); await loadStylePresets(); toast("风格预设已删除"); } catch (error) { toast(error.message); } }

function artistName(value) { let name = String(value || "").trim(); if (name.startsWith("@")) name = name.slice(1).trim(); else if (name.toLowerCase().startsWith("by ")) name = name.slice(3).trim(); return name; }
function artistTokensFrom(value) { const values = String(value || "").replaceAll("\r", ",").replaceAll("\n", ",").split(/,|(?=@)/).map(artistName).filter(Boolean); const seen = new Set(); return values.filter(name => { const key = name.toLowerCase(); if (seen.has(key)) return false; seen.add(key); return true; }); }
function canonicalArtists(value) { return artistTokensFrom(value).map(name => `@${name}`).join(", "); }
function artistTokens() { return artistTokensFrom(ui.manual_artist.value); }
function appendArtist(name) { const values = artistTokens(); const keys = new Set(values.map(value => value.toLowerCase())); const clean = artistName(name); if (clean && !keys.has(clean.toLowerCase())) values.push(clean); ui.manual_artist.value = values.map(value => `@${value}`).join(", "); schedulePersist(); }
function artistEditingValue(value) {
  return String(value || "").replaceAll("\r", ",").replaceAll("\n", ",").split(",").map(part => {
    const leading = part.match(/^\s*/)?.[0] || "";
    let name = part.slice(leading.length);
    if (/^by\s+/i.test(name)) name = name.replace(/^by\s+/i, "@");
    else if (name && !name.startsWith("@")) name = `@${name}`;
    return name;
  }).join(", ");
}
function normalizeArtistField(finalize = false) {
  const input = ui.manual_artist;
  const raw = input.value;
  const caret = input.selectionStart ?? raw.length;
  const transform = finalize ? canonicalArtists : artistEditingValue;
  const next = transform(raw);
  if (next === raw) return;
  const nextCaret = transform(raw.slice(0, caret)).length;
  input.value = next;
  if (document.activeElement === input) input.setSelectionRange(nextCaret, nextCaret);
}
async function loadArtistFavorites() { try { artistFavoritesData = await request("/api/favorites/artist"); } catch { artistFavoritesData = { groups: [], items: [] }; } renderArtistFavorites(); }
function renderArtistFavorites() { const items = (artistFavoritesData.items || []).filter(item => item.groupIds?.length); ui.artistFavoriteCount.textContent = `${items.length} 位`; ui.artistFavorites.replaceChildren(...items.slice(0, 12).map(item => { const button = document.createElement("button"); button.type = "button"; button.className = "artist-chip"; button.title = item.nickname ? `追加 @${item.name} · ${item.nickname}` : `追加 @${item.name}`; button.innerHTML = `<strong>@${escapeHtml(item.name)}</strong>${item.nickname ? `<small>${escapeHtml(item.nickname)}</small>` : ""}`; button.addEventListener("click", () => appendArtist(item.name)); return button; })); ui.artistFavoriteList.replaceChildren(...(items.length ? items.map(item => { const row = document.createElement("div"); row.className = "artist-favorite-row"; row.innerHTML = `<button class="artist-favorite-name" type="button" title="追加到固定画师"><strong>@${escapeHtml(item.name)}</strong>${item.nickname ? `<small>${escapeHtml(item.nickname)}</small>` : ""}</button><button class="button ghost compact" type="button">追加</button><button class="icon-button dark" type="button" title="取消收藏" aria-label="取消收藏">×</button>`; row.children[0].addEventListener("click", () => appendArtist(item.name)); row.children[1].addEventListener("click", () => appendArtist(item.name)); row.children[2].addEventListener("click", () => removeArtistFavorite(item.name)); return row; }) : [Object.assign(document.createElement("div"), { className: "artist-favorite-empty", textContent: "还没有收藏画师" })])); }
async function openArtistDialog() { await loadArtistFavorites(); ui.artistDialog.showModal(); }
async function saveCurrentArtistFavorites() { const values = artistTokens(); if (!values.length) { toast("请先输入画师名称"); return; } try { for (const name of values) artistFavoritesData = await request("/api/favorites/artist/item", { method: "PUT", body: JSON.stringify({ name, favorite: true }) }); renderArtistFavorites(); toast(`已收藏 ${values.length} 位画师`); } catch (error) { toast(error.message); } }
async function removeArtistFavorite(name) { try { artistFavoritesData = await request("/api/favorites/artist/item", { method: "PUT", body: JSON.stringify({ name, favorite: false }) }); renderArtistFavorites(); toast("已取消收藏画师"); } catch (error) { toast(error.message); } }

ui.settingsForm.addEventListener("submit", async event => { event.preventDefault(); ui.startButton.disabled = true; persistNow(); try { renderBatch(await request("/api/batches", { method: "POST", body: JSON.stringify(readSettings()) })); toast("批次已开始"); } catch (error) { toast(error.message); await refreshConnection(); } });
ui.settingsForm.addEventListener("input", event => { if (event.target.matches("input, textarea, select")) schedulePersist(); }); ui.settingsForm.addEventListener("change", schedulePersist);
ui.stopButton.addEventListener("click", async () => { if (!currentBatch) return; try { renderBatch(await request(`/api/batches/${currentBatch.id}/stop`, { method: "POST" })); } catch (error) { toast(error.message); } });
ui.prevPage.addEventListener("click", () => loadHistory(historyPage - 1)); ui.nextPage.addEventListener("click", () => loadHistory(historyPage + 1));
ui.closeDialog.addEventListener("click", () => ui.detailDialog.close()); ui.detailDialog.addEventListener("click", event => { if (event.target === ui.detailDialog) ui.detailDialog.close(); });
ui.restoreSettings.addEventListener("click", () => { if (!selectedRecord) return; applySettings(selectedRecord.settings); ui.detailDialog.close(); schedulePersist(); toast("已载入历史设置"); });
ui.deleteRecord.addEventListener("click", async () => { if (!selectedRecord || !confirm("只删除 WebUI 历史记录，图片文件会保留。继续吗？")) return; try { await request(`/api/history/${selectedRecord.id}`, { method: "DELETE" }); ui.detailDialog.close(); selectedRecord = null; await loadHistory(historyPage); } catch (error) { toast(error.message); } });
ui.closePool.addEventListener("click", closePool); ui.confirmPool.addEventListener("click", closePool); ui.drawerBackdrop.addEventListener("click", closePool);
ui.togglePoolSidebar.addEventListener("click", () => setPoolSidebarOpen(!ui.poolSidebar.classList.contains("mobile-open")));
ui.poolSidebar.addEventListener("click", event => { if (window.innerWidth <= 620 && event.target.closest("button") && !event.target.closest(".facet-heading, .collection-edit, .tree-disclosure")) setPoolSidebarOpen(false); });
ui.poolSidebar.addEventListener("keydown", handleFavoriteTreeKeydown);
ui.poolSearch.addEventListener("input", () => { currentView().query = ui.poolSearch.value; poolPage = 1; clearTimeout(ui.poolSearch._timer); ui.poolSearch._timer = setTimeout(loadPool, 220); schedulePersist(); });
ui.poolSort.addEventListener("change", () => { currentView().sort = ui.poolSort.value; poolPage = 1; loadPool(); schedulePersist(); }); ui.poolLanguage.addEventListener("change", () => { currentView().language = ui.poolLanguage.value; renderPoolItems(); schedulePersist(); });
ui.selectedOnly.addEventListener("change", () => { currentView().selectedOnly = ui.selectedOnly.checked; poolPage = 1; loadPool(); schedulePersist(); });
ui.clearFilters.addEventListener("click", () => { const language = currentView().language; poolViews[activeSection] = { ...defaultView(), language }; ui.poolSearch.value = ""; ui.poolSort.value = ""; ui.selectedOnly.checked = false; poolPage = 1; loadPool(); schedulePersist(); });
ui.poolPrev.addEventListener("click", () => { poolPage -= 1; loadPool(); }); ui.poolNext.addEventListener("click", () => { poolPage += 1; loadPool(); });
ui.selectPage.addEventListener("click", () => selectCurrentPage(true)); ui.selectAllPool.addEventListener("click", () => { draft.pools[activeSection] = { mode: "all", ids: [], excluded_ids: [] }; renderPoolItems(); renderDimensions(); updatePoolOverview(); updatePoseConflict(); schedulePersist(); });
ui.clearPool.addEventListener("click", () => { draft.pools[activeSection] = { mode: "include", ids: [], excluded_ids: [] }; renderPoolItems(); renderDimensions(); updatePoolOverview(); updatePoseConflict(); schedulePersist(); }); ui.addCustom.addEventListener("click", () => openCustom()); ui.importCustom.addEventListener("click", openImportDialog);
ui.customForm.addEventListener("submit", saveCustom); ui.cancelCustom.addEventListener("click", () => ui.customDialog.close()); ui.closeCustom.addEventListener("click", () => ui.customDialog.close()); ui.deleteCustom.addEventListener("click", deleteCustomItem);
ui.customGroupForm.addEventListener("submit", saveCustomGroup); ui.deleteCustomGroup.addEventListener("click", removeCustomGroup); ui.closeCustomGroup.addEventListener("click", () => ui.customGroupDialog.close()); ui.cancelCustomGroup.addEventListener("click", () => ui.customGroupDialog.close());
ui.importFile.addEventListener("change", previewImportFile); ui.commitImport.addEventListener("click", commitCustomImport); ui.closeImport.addEventListener("click", () => ui.importDialog.close()); ui.cancelImport.addEventListener("click", () => ui.importDialog.close());
ui.manageArtists.addEventListener("click", openArtistDialog); ui.saveCurrentArtists.addEventListener("click", saveCurrentArtistFavorites); ui.closeArtists.addEventListener("click", () => ui.artistDialog.close()); ui.cancelArtists.addEventListener("click", () => ui.artistDialog.close());
ui.savePreset.addEventListener("click", createStylePreset); ui.presetName.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); createStylePreset(); } });
ui.presetName.addEventListener("input", () => setPresetFeedback());
ui.manual_artist.addEventListener("input", () => { normalizeArtistField(false); schedulePersist(); });
ui.manual_artist.addEventListener("paste", () => queueMicrotask(() => { normalizeArtistField(true); schedulePersist(); }));
ui.manual_artist.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); normalizeArtistField(true); schedulePersist(); } });
ui.manual_artist.addEventListener("blur", () => { normalizeArtistField(true); schedulePersist(); });
for (const control of [ui.model_name, ui.hires_enabled, ui.hires_model_name, ui.detailer_hand, ui.detailer_nsfw, ui.detailer_face, ui.detailer_eyes]) control.addEventListener("change", () => { updateRepairControls(); schedulePersist(); });
ui.themeToggle.addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true));
ui.favoriteForm.addEventListener("submit", saveFavorite); ui.removeFavorite.addEventListener("click", removeFavorite); ui.closeFavorite.addEventListener("click", () => ui.favoriteDialog.close()); ui.cancelFavorite.addEventListener("click", () => ui.favoriteDialog.close());
ui.groupForm.addEventListener("submit", saveGroup); ui.deleteGroup.addEventListener("click", deleteFavoriteGroup); ui.closeGroup.addEventListener("click", () => ui.groupDialog.close()); ui.cancelGroup.addEventListener("click", () => ui.groupDialog.close());
ui.importChildGroup.addEventListener("click", () => openChildGroupDialog()); ui.childGroupForm.addEventListener("submit", importFavoriteChild); ui.childGroupSearch.addEventListener("input", renderChildGroupSources); ui.closeChildGroup.addEventListener("click", () => ui.childGroupDialog.close()); ui.cancelChildGroup.addEventListener("click", () => ui.childGroupDialog.close());
ui.deleteGroupForm.addEventListener("submit", confirmGroupDelete); ui.closeDeleteGroup.addEventListener("click", () => ui.deleteGroupDialog.close()); ui.cancelDeleteGroup.addEventListener("click", () => ui.deleteGroupDialog.close());
ui.addLora.addEventListener("click", openLoraDialog); ui.resetLoras.addEventListener("click", () => { draft.loras = clone(defaults?.loras || []); renderLoras(); schedulePersist(); toast("已恢复模板默认 LoRA"); }); ui.closeLora.addEventListener("click", () => ui.loraDialog.close()); ui.cancelLora.addEventListener("click", () => ui.loraDialog.close()); ui.loraSearch.addEventListener("input", renderLoraCatalog);
ui.female_count.addEventListener("input", updatePeopleTotal); ui.male_count.addEventListener("input", updatePeopleTotal);
ui.resetSettings.addEventListener("click", () => { if (!confirm("恢复全部模板默认设置？")) return; applySettings(defaults); schedulePersist(); toast("已恢复默认设置"); });
ui.clearDraft.addEventListener("click", () => { if (!confirm("清除本地草稿并恢复默认设置？收藏不会被删除。")) return; localStorage.removeItem(DRAFT_KEY); localStorage.removeItem(LEGACY_DRAFT_KEY); localStorage.removeItem(VIEW_KEY); poolViews = Object.fromEntries(SECTIONS.map(section => [section, defaultView()])); applySettings(defaults); ui.draftStatus.textContent = "本地草稿已清除"; toast("已清除本地草稿"); });
document.querySelectorAll("[data-mobile-view]").forEach(button => button.addEventListener("click", () => { const view = button.dataset.mobileView; document.querySelectorAll(".mobile-tabs button").forEach(item => item.classList.toggle("active", item === button)); document.querySelector(".workspace").dataset.mobileView = view; }));
document.addEventListener("keydown", event => { if (event.key === "Escape" && ui.poolDrawer.classList.contains("open")) closePool(); });

async function initialize() {
  applyTheme(initialTheme());
  try {
    const payload = await request("/api/config");
    config = payload;
    defaults = payload.defaults;
    loadStoredView();
    const restored = loadStoredDraft();
    if (!restored) { applySettings(defaults); ui.draftStatus.textContent = "使用模板默认设置"; }
    initialized = true;
  } catch (error) {
    const message = `界面初始化失败：${error.message}`;
    ui.appError.textContent = message;
    ui.appError.hidden = false;
    toast(message);
    return;
  }
  await Promise.allSettled([loadResources(), refreshConnection(), pollBatch(), loadHistory(1), loadLoraInventory(), loadArtistFavorites(), loadStylePresets()]);
  setInterval(pollBatch, 1200); setInterval(refreshConnection, 5000);
}
initialize();
