const SECTION_META = {
  character: { label: "角色", kicker: "CHARACTER", singular: "角色" },
  clothing: { label: "服装", kicker: "CLOTHING", singular: "服装" },
  pose: { label: "姿势", kicker: "POSE", singular: "姿势" },
  background: { label: "背景", kicker: "BACKGROUND", singular: "背景" },
  expression: { label: "表情", kicker: "EXPRESSION", singular: "表情" }
};
const NATIVE_FACET_LABELS = {
  clothing: { categories: "服装分类", traits: "服装特征" },
  pose: { categories: "姿势分类", traits: "姿势特征" },
  background: { categories: "场景分类", traits: "场景特征" },
  expression: { categories: "表情分类", traits: "表情特征" }
};
const SECTIONS = Object.keys(SECTION_META);
const DRAFT_KEY = "anima-random-webui:draft:v1";

const VIEW_KEY = "anima-random-webui:pool-view:v1";
const THEME_KEY = "anima-random-webui:theme:v1";
// Keep native dialog semantics while sharing one reversible visual lifecycle.
function motionDialog(element, open, options = {}) {
  if (typeof StudioMotion !== "undefined") return StudioMotion.dialog(element, open, {
    ...(element.id === "detailDialog" ? { restoreFocus: false } : {}), ...options
  });
  if (open) {if (!element.open) element.showModal();} else
  element.close(options.returnValue);
}
const GROUP_TREE_KEY = "anima-random-webui:favorite-tree:v1";
const CUSTOM_GROUP_TREE_KEY = "anima-random-webui:custom-group-tree:v1";
const INTERNAL_HIRES_PERCENT = 45;

const ui = Object.fromEntries(
  [
  "connection",
  "startButton",
  "stopButton",
  "batchState",
  "progressCount",
  "progressFill",
  "batchError",
  "sampleProgress",
  "sampleFill",
  "queueBadge",
  "queuePanel",
  "reproduceImage",
  "variantButton",
  "variantCount",
  "replayDrawer",
  "variantDrawer",
  "closeReplayDrawer",
  "closeVariantDrawer",
  "replaySeedSummary",
  "replayStatus",
  "variantStatus",
  "confirmReplay",
  "confirmVariant",
  "variantModes",
  "redrawSections",
  "variantDrawerCount",
  "settingsForm",
  "count",
  "female_count",
  "male_count",
  "peopleTotal",
  "dimensionList",
  "animaPromptPoolControls",

  "poolOverview",
  "character_detail",
  "manual_artist",
  "manageArtists",
  "artistFavorites",
  "quality_prompt",
  "extra_prompt",
  "negative_prompt",
  "width",
  "height",
  "steps",
  "cfg",
  "gallery",
  "emptyState",
  "historyCount",
  "prevPage",
  "nextPage",
  "pageLabel",
  "detailDialog",
  "closeDialog",
  "detailImage",
  "imagePreviewDialog",
  "closeImagePreview",
  "imagePreviewPrevious",
  "imagePreviewNext",
  "imagePreviewStage",
  "imagePreviewTitle",
  "imagePreviewImage",
  "imagePreviewVeil",
  "imagePreviewStatus",
  "imagePreviewAnnouncement",
  "detailMeta",
  "revealImageFile",
  "detailScroll",
  "detailMain",
  "detailDrawerHome",
  "detailStats",
  "detailSelection",
  "detailPositive",
  "detailNegative",
  "restoreSettings",
  "deleteRecord",
  "toast",
  "poolDrawer",
  "drawerBackdrop",
  "closePool",
  "poolTitle",
  "poolKicker",
  "poolMeta",
  "poolSearch",
  "poolSort",
  "poolLanguage",
  "togglePoolSidebar",
  "selectedOnly",
  "clearFilters",
  "selectPage",
  "selectFilteredPool",
  "selectAllPool",
  "poolMoreSelection",
  "poolModeNote",
  "clearPool",
  "favoriteSelection",
  "addCustom",
  "importCustom",
  "poolSidebar",
  "poolGrid",
  "poolEmpty",
  "poseConflict",
  "poolSelectionLabel",
  "poolPrev",
  "poolNext",
  "poolPageLabel",
  "confirmPool",
  "customDialog",
  "customForm",
  "closeCustom",
  "cancelCustom",
  "customDialogTitle",
  "customId",
  "customTitle",
  "customSubtitle",
  "customGenderField",
  "customGender",
  "customCharacterMeta",
  "customHair",
  "customEye",
  "customSeries",
  "customPoseMeta",
  "customCategory",
  "customTraits",
  "customPrompt",
  "customGroupChecks",
  "deleteCustom",
  "loraSummary",
  "loraList",
  "loraEmpty",
  "addLora",
  "resetLoras",
  "loraDialog",
  "closeLora",
  "cancelLora",
  "loraSearch",
  "loraCatalog",
  "loraCatalogEmpty",
  "loraCatalogMeta",
  "loraTriggerDialog",
  "loraTriggerForm",
  "loraTriggerTitle",
  "loraTriggerFilename",
  "loraTriggerWords",
  "loraSourceTriggerWords",
  "closeLoraTriggers",
  "cancelLoraTriggers",
  "resetLoraTriggers",
  "managePromptRules",
  "undoPromptNormalization",
  "promptNormalizationStatus",
  "promptRuleDialog",
  "promptRuleForm",
  "promptRuleList",
  "promptRuleId",
  "promptRuleFrom",
  "promptRuleTo",
  "promptRulePositive",
  "promptRuleNegative",
  "promptRuleLora",
  "promptRuleError",
  "deletePromptRule",
  "clearPromptRule",
  "closePromptRules",
  "cancelPromptRules",
  "draftStatus",
  "resetSettings",
  "clearDraft",
  "favoriteDialog",
  "favoriteForm",
  "favoriteTitle",
  "favoriteGroups",
  "favoriteNickname",
  "removeFavorite",
  "closeFavorite",
  "cancelFavorite",
  "favoriteSelectionDialog",
  "favoriteSelectionForm",
  "favoriteSelectionTitle",
  "favoriteSelectionSummary",
  "favoriteSelectionGroups",
  "favoriteSelectionGroupName",
  "createFavoriteSelectionGroup",
  "favoriteSelectionError",
  "closeFavoriteSelection",
  "cancelFavoriteSelection",
  "confirmFavoriteSelection",
  "groupDialog",
  "groupForm",
  "groupDialogTitle",
  "groupId",
  "groupName",
  "groupNameField",
  "deleteGroup",
  "closeGroup",
  "cancelGroup",
  "saveGroupButton",
  "importChildGroup",
  "childGroupDialog",
  "childGroupForm",
  "childGroupTitle",
  "childGroupHint",
  "childGroupSearch",
  "childGroupList",
  "childGroupEmpty",
  "childGroupError",
  "closeChildGroup",
  "cancelChildGroup",
  "confirmChildGroup",
  "deleteGroupDialog",
  "deleteGroupForm",
  "deleteGroupTitle",
  "deleteGroupSummary",
  "deleteGroupStats",
  "deleteGroupKeep",
  "deleteGroupKeepHint",
  "deleteGroupItemsOption",
  "deleteGroupItems",
  "deleteGroupItemsTitle",
  "deleteGroupItemsHint",
  "deleteGroupAllOption",
  "deleteGroupAll",
  "deleteGroupAllHint",
  "deleteGroupError",
  "closeDeleteGroup",
  "cancelDeleteGroup",
  "confirmDeleteGroup",
  "customGroupDialog",
  "customGroupForm",
  "customGroupDialogTitle",
  "customGroupId",
  "customGroupName",
  "deleteCustomGroup",
  "closeCustomGroup",
  "cancelCustomGroup",
  "importDialog",
  "importDialogTitle",
  "importFile",
  "importBundleName",
  "importJsonTemplate",
  "importCsvTemplate",
  "importTargetGroups",
  "closeImport",
  "cancelImport",
  "importHint",
  "importSummary",
  "importRows",
  "commitImport",
  "artistDialog",
  "closeArtists",
  "cancelArtists",
  "saveCurrentArtists",
  "artistFavoriteList",
  "artistFavoriteCount",
  "presetSummary",
  "presetName",
  "savePreset",
  "presetError",
  "presetList",
  "presetEmpty",
  "model_name",
  "model_profile",
  "modelProfileHint",
  "manageResourceOwnership",
  "resourceOwnershipDialog",
  "closeResourceOwnership",
  "closeResourceOwnershipFooter",
  "ownershipKind",
  "ownershipSearch",
  "ownershipSummary",
  "ownershipList",
  "ownershipError",
  "refreshResourceClassification",
  "base_mode",
  "sampler_name",
  "base_denoise",

  "latent_model_format",
  "latentModelFormatField", "latentDoubleSamplingHint",
  "latent_sampler_steps", "latent_sampler_cfg", "latent_sampler_name", "latent_sampler_scheduler", "latent_sampler_denoise",
  "pixel_hires_enabled", "pixel_hires_model_name", "pixel_hires_percent", "pixel_hires_upscale_method", "pixelHiresFields", "pixelHiresResizeHint", "dlss_enabled", "dlssFields", "pipelineFlow",
  "nvidia_dlss_style",
  "nvidia_dlss_intensity",
  "nvidia_dlss_local_tone",
  "nvidia_dlss_local_struct",
  "nvidia_dlss_output_mix",
  "scheduler",
  "latent_upscale_enabled",
  "latent_upscale_scale",
  "latent_upscale_method",
  "latentUpscaleFields",
  "latent_second_sampler_enabled",
  "latentSecondSamplerFields",
  "latent_second_sampler_steps",
  "latent_second_sampler_cfg",
  "latent_second_sampler_name",
  "latent_second_scheduler",
  "latent_second_sampler_denoise",
  "seedvr2_enabled",
  "seedvr2_model_name",
  "seedvr2_vae_name",
  "seedvr2_weight_dtype",
  "seedvr2_scale_by",
  "seedvr2_color_correction_method",
  "seedvr2_sampler_steps",
  "seedvr2_sampler_cfg",
  "seedvr2_sampler_name",
  "seedvr2_scheduler",
  "seedvr2_sampler_denoise",
  "seedvr2_encode_tile_size",
  "seedvr2_encode_overlap",
  "seedvr2_encode_temporal_size",
  "seedvr2_encode_temporal_overlap",
  "seedvr2_decode_tile_size",
  "seedvr2_decode_overlap",
  "seedvr2_decode_temporal_size",
  "seedvr2_decode_temporal_overlap",
  "seedvr2Fields",
  "nvidia_upscale_enabled",
  "nvidia_resize_type",
  "nvidia_scale",
  "nvidia_quality",
  "nvidiaUpscaleFields",
  "face_refine_enabled",
  "faceRefineFields",
  "face_steps",
  "face_cfg_scale",
  "face_sampler",
  "face_scheduler",
  "face_denoise_strength",
  "face_selection",
  "face_detection_confidence",
  "face_manual_rotation",
  "face_processing_resolution",
  "face_upscaler_model",
  "face_crop_padding",
  "face_blend_softness",
  "face_mask_expansion",
  "face_sampling_mask_blur_size",
  "face_sampling_mask_blur_strength",
  "face_positive_prompt",
  "face_negative_prompt",
  "face_exclusions",
  "face_enable_pre_upscale",
  "face_replace_positive_prompt",
  "face_replace_negative_prompt",
  "face_enable_color_correction",
  "face_enable_segmentation",
  "face_enable_differential_diffusion",
  "face_enable_lightness_rescue",
  "face_enable_final_refinement",
  "face_offload_models_to_cpu",
  "repairSummary",
  "resourceWarning",
  "themeToggle",
  "appError"].
  map((id) => [id, document.getElementById(id)])
);

// 独立状态与进度控件可按页面布局省略。
const OPTIONAL_CONTROLS = new Set([
"sampleProgress",
"sampleFill",
"queueBadge",
"queuePanel",
"reproduceImage",
"variantButton",
"variantCount",
"replayDrawer",
"variantDrawer",
"closeReplayDrawer",
"closeVariantDrawer",
"replaySeedSummary",
"replayStatus",
"variantStatus",
"confirmReplay",
"confirmVariant",
"variantModes",
"redrawSections",
"variantDrawerCount"]
);
const missingUi = Object.entries(ui).
filter(([id, element]) => !element && !OPTIONAL_CONTROLS.has(id)).
map(([id]) => id);
if (missingUi.length) {
  const message = `界面资源版本不一致，缺少控件：${missingUi.join("、")}。请刷新页面。`;
  const errorPanel = document.getElementById("appError");
  if (errorPanel) {
    errorPanel.textContent = message;
    errorPanel.hidden = false;
  }
  throw new Error(message);
}

let defaults = null;
let config = { catalog: { counts: {} } };
let currentBatch = null;
let selectedRecord = null;
let detailContext = null;
const imagePreview = {
  phase: "closed", token: 0, zoom: 1, fit: 1, x: 0, y: 0, width: 0, height: 0,
  renderFrame: null, zoomFrame: null,
  animation: null, pointers: new Map(), gesture: null, tap: null, backdropPointer: null,
  navigationRequest: 0, navigationCursor: null, preloaded: new Map(), swipe: null,
  panSpring: null, revealAnimation: null, unclamped: false, committedSrc: "",
  sourcePresence: null, veilAnimation: null, veilCovered: false,
  context: null
};
let regenerationOptions = null;
let regenerationRequest = 0;
let regenerationTrigger = null;
let revealFileRequest = 0;
let historyPage = 1;
let historyPages = 1;
let historyItems = [];
const historyCache = new Map();
let historyDirty = false;
let lastTerminalBatch = "";
let toastTimer = null;
let persistTimer = null;
let initialized = false;
let draft = { modes: {}, counts: {}, fixed: {}, pools: {}, loras: [], managedTriggers: [] };
// 保留随机池、LoRA 与触发词的完整编辑草稿；仅写入本项目浏览器存储。
let profileDrafts = {};
let profileResources = {};
let activeProfileId = "anima";

let activeSection = "character";
let poolPage = 1;
let poolPages = 1;
let poolItems = [];
let poolFacets = {};
let poolRequest = 0;
let poolContextRevision = 0;
let poolSelectionOperation = null;
let poolResultContext = "";
let poolQueryCount = null;
const poolSelectionStats = new Map();
let poolTotalsRequest = 0;
let editingCustom = null;
let customEditorSession = 0;
let customWriteBusy = false;
let editingCustomGroup = null;
let editingFavoriteItem = null;
let loraInventory = [];
let loraClassificationItems = [];
let loraInventoryLoaded = false;
let loraInventoryError = "";
let editingLoraTrigger = null;
let promptRules = [];
let editingPromptRule = null;
let promptRulesProfile = "";
let promptRulesRequest = 0;
let promptRuleEditorRevision = 0;
let normalizationUndo = null;
let promptNormalizationEpoch = 0;
const promptNormalizationRequests = new Map();
let favoritesData = { groups: [], items: [], favorite_keys: [] };
let favoritesAvailable = false;
let customGroups = [];
let artistFavoritesData = { groups: [], items: [] };
let stylePresets = [];
let stylePresetRequest = 0;
let stylePresetProfile = "";
let resources = { models: [], seedvr2_models: [], upscale_models: [], samplers: [], schedulers: [], model_profiles: {}, nodes: {}, plugins: {} };
let resourceRequest = 0;
let resourceInventoryProfile = "";
let resourceError = "";
let resourcesLoaded = false;
let loraInventoryRequest = 0;
let loraInventoryProfile = "";

let customGroupsRequest = 0;
let importPreview = null;
let editingGroup = null;
let childGroupParent = null;
let selectedChildSource = "";
let pendingGroupDelete = null;
let collapsedFavoriteGroups = (() => {
  try {
    const value = JSON.parse(localStorage.getItem(GROUP_TREE_KEY) || "{}");
    return Object.fromEntries(SECTIONS.map((section) => [section, new Set(value[section] || [])]));
  } catch {
    return Object.fromEntries(SECTIONS.map((section) => [section, new Set()]));
  }
})();
const customTreeInitialized = new Set();
let collapsedCustomGroups = (() => {
  try {
    const value = JSON.parse(localStorage.getItem(CUSTOM_GROUP_TREE_KEY) || "{}");
    Object.keys(value).forEach((section) => customTreeInitialized.add(section));
    return Object.fromEntries(SECTIONS.map((section) => [section, new Set(value[section] || [])]));
  } catch {
    return Object.fromEntries(SECTIONS.map((section) => [section, new Set()]));
  }
})();
const poseItemCache = new Map();

function defaultView() {
  return {
    query: "",
    sort: "",
    language: "zh",
    selectedOnly: false,
    scrollTop: 0,
    filters: { gender: "", hair: "", eye: "", series: "", categories: [], traits: [], collection: "", customGroup: "" }
  };
}
let poolViews = Object.fromEntries(SECTIONS.map((section) => [section, defaultView()]));

async function request(path, options = {}) {
  const favoriteSection = path.match(/^\/api\/favorites\/([^/?]+)/)?.[1];
  const favoriteEpoch = typeof sharedFavoritesState !== "undefined" ? sharedFavoritesState.epoch : 0;
  if (favoriteSection && !/[?&]model_profile=/.test(path)) {
    path += `${path.includes("?") ? "&" : "?"}model_profile=${encodeURIComponent(currentProfile())}`;
  }
  const favoriteProfile = favoriteSection ? new URLSearchParams(path.split("?")[1]).get("model_profile") : null;
  const response = await fetch(path, {
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    ...options
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    if (payload.code === "storage_read_only" && payload.store) {
      config.storage_status ||= {};
      config.storage_status[payload.store] = { read_only: true, reason: payload.error, path: payload.path };
      renderStorageStatus();
    }
    const error = new Error(payload.error || `请求失败 (${response.status})`);
    error.status = response.status;
    error.code = payload.code;
    error.payload = payload;
    throw error;
  }
  if (path.startsWith("/api/favorites/") && !path.split("?")[0].endsWith("/reconcile") && options.method && options.method !== "GET") {
    if (typeof sharedFavoritesChanged === "function") sharedFavoritesChanged({
      model_profile: favoriteProfile, section: favoriteSection,
      ...Object.fromEntries(["updatedItemIds", "deletedItemIds", "favoritesSync"].filter((key) => payload[key] !== undefined).map((key) => [key, payload[key]]))
    });
    if (favoriteProfile !== currentProfile() || typeof sharedFavoritesState !== "undefined" && favoriteEpoch !== sharedFavoritesState.epoch) {
      throw new Error("已切换模型，收藏操作已在原模型完成");
    }
  }
  return payload;
}

async function requestFavorites(path, options = {}) {
  const section = path.match(/\/api\/favorites\/([^/?]+)/)?.[1];
  const profile = currentProfile();
  const value = await request(path, options);
  return section === activeSection && profile === currentProfile() ? value : favoritesData;
}

// Protection lasts until restart. Observe new controls and later enable attempts
// so asynchronous resource/history refreshes cannot re-enable protected writes.
const STORAGE_CONTROLS = {
  history: "#startButton, #confirmReplay, #confirmVariant, #deleteRecord, #historyDeleteSubmit",
  style_presets: "#savePreset, .preset-actions button",
  custom_prompts: '#customForm button[type="submit"], #deleteCustom, #customGroupForm button[type="submit"], #deleteCustomGroup, #commitImport, [data-storage-write="custom_prompts"], [data-favorite-custom-write]',
  lora_triggers: '#loraTriggerForm button[type="submit"], #resetLoraTriggers',
  prompt_rules: '#promptRuleForm button[type="submit"], #deletePromptRule, .prompt-rule-actions input',

  favorite_extensions: '[data-favorite-extension-write]'
};
function protectStorageControls() {
  for (const [store, selector] of Object.entries(STORAGE_CONTROLS)) {
    const status = config.storage_status?.[store];
    if (!status?.read_only) continue;
    for (const control of document.querySelectorAll(selector)) {
      if (!control.disabled) control.disabled = true;
      control.title = status.reason || "此存储已受保护，请修复文件后重启";
    }
  }
}
function renderStorageStatus() {
  const protectedStores = Object.values(config.storage_status || {}).filter((item) => item.read_only);
  const messages = protectedStores.map((item) => `${item.path || item.store}：${item.reason}`);
  const diagnostic = config.catalog?.discovery;
  if (diagnostic?.message) messages.push(diagnostic.message);
  const panel = document.getElementById("storageWarning");
  panel.hidden = messages.length === 0;
  panel.textContent = messages.join("\n");
  for (const dialog of document.querySelectorAll("dialog")) {
    let note = dialog.querySelector(".storage-protection-note");
    if (!note && protectedStores.length) {
      note = document.createElement("p");
      note.className = "storage-protection-note";
      note.setAttribute("role", "status");
      (dialog.querySelector("form") || dialog).prepend(note);
    }
    if (note) note.textContent = protectedStores.map((item) => `${item.path || item.store}：${item.reason}`).join("\n");
  }
  protectStorageControls();
}
new MutationObserver(protectStorageControls).observe(document.body, {
  childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"]
});

function escapeHtml(value) {
  const span = document.createElement("span");
  span.textContent = value == null ? "" : String(value);
  return span.innerHTML.replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function toast(message) {
  ui.toast.textContent = message;
  ui.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ui.toast.classList.remove("show"), 3200);
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}
const PROMPT_FIELD_NAMES = [
"quality_prompt",
"manual_artist",
"fixed_character",
"fixed_clothing",
"fixed_pose",
"fixed_background",
"fixed_expression",
"extra_prompt",
"negative_prompt"];

const PROMPT_FIELD_LABELS = {
  quality_prompt: "质量词",
  manual_artist: "画师",
  fixed_character: "固定角色",
  fixed_clothing: "固定服装",
  fixed_pose: "固定姿势",
  fixed_background: "固定背景",
  fixed_expression: "固定表情",
  extra_prompt: "额外提示词",
  negative_prompt: "负面词",
  custom_prompt: "自定义提示词"
};
function promptFieldForElement(element) {
  if (!element) return "";
  if (element.dataset?.fixed) return `fixed_${element.dataset.fixed}`;
  if (element === ui.customPrompt) return "custom_prompt";
  return PROMPT_FIELD_NAMES.includes(element.id) ? element.id : "";
}
function currentPromptFields() {
  return {
    quality_prompt: ui.quality_prompt.value,
    manual_artist: ui.manual_artist.value,
    ...Object.fromEntries(SECTIONS.map((section) => [`fixed_${section}`, draft.fixed[section] || ""])),
    extra_prompt: ui.extra_prompt.value,
    negative_prompt: ui.negative_prompt.value
  };
}
function applyPromptField(field, value) {
  if (field.startsWith("fixed_")) {
    const section = field.slice(6);
    draft.fixed[section] = value;
    const input = ui.dimensionList.querySelector(`[data-fixed="${section}"]`);
    if (input) input.value = value;
    return;
  }
  if (field === "custom_prompt") {
    ui.customPrompt.value = value;
    return;
  }
  if (ui[field]) ui[field].value = value;
}
function setNormalizationUndo(changes, elements = {}) {
  if (!changes.length) return;
  const mode = "random";
  normalizationUndo = { profile: currentProfile(), mode, changes: changes.map((item) => ({ ...item, element: elements[item.field] || promptElementForField(item.field) })) };
  ui.undoPromptNormalization.hidden = mode !== "random";

  const labels = changes.map((item) => PROMPT_FIELD_LABELS[item.field] || item.field);
  ui.promptNormalizationStatus.textContent = `已规范 ${changes.length} 个字段：${labels.join("、")}`;
}
function clearNormalizationUndo(message = "") {
  normalizationUndo = null;
  ui.undoPromptNormalization.hidden = true;
  ui.promptNormalizationStatus.textContent = message || "编辑结束后自动按 Anima Tag 规范整理。";

}
function undoPromptNormalization() {
  if (!normalizationUndo) return;
  const undo = normalizationUndo;
  const mode = "random";
  if (undo.profile !== currentProfile() || undo.mode !== mode) {clearNormalizationUndo();return;}
  invalidatePromptNormalization();
  let restored = 0;
  for (const change of undo.changes) {
    if (!change.element || change.element.value !== change.after) continue;
    change.element.value = change.before;
    if (mode === "random") applyPromptField(change.field, change.before);
    restored++;
  }
  clearNormalizationUndo(restored ? "已撤销上次提示词规范。" : "提示词已继续编辑，已保留你的新内容。");
  schedulePersist();
}
function promptNormalizationContext() {
  const mode = "random";
  return `${promptNormalizationEpoch}:${currentProfile()}:${mode}`;
}
function invalidatePromptNormalization() {
  promptNormalizationEpoch++;
  promptNormalizationRequests.clear();
}
function promptElementForField(field) {
  if (field.startsWith("fixed_")) return ui.dimensionList.querySelector(`[data-fixed="${field.slice(6)}"]`);
  if (field === "custom_prompt") return ui.customPrompt;
  return ui[field] || document.getElementById(field);
}
async function normalizePromptFields(fields, { apply = true, recordUndo = true, elements = {} } = {}) {
  const context = promptNormalizationContext();
  const captured = Object.fromEntries(Object.entries(fields).map(([field, value]) => {
    const revision = (promptNormalizationRequests.get(field) || 0) + 1;
    promptNormalizationRequests.set(field, revision);
    return [field, { value, revision, element: elements[field] || promptElementForField(field) }];
  }));
  const managedTriggers =

  draft.managedTriggers || [];
  const payload = await request("/api/prompts/normalize", {
    method: "POST",
    body: JSON.stringify({ fields, managedTriggers: clone(managedTriggers), model_profile: currentProfile() })
  });
  const appliedFields = [];
  if (apply && context === promptNormalizationContext()) {
    for (const [field, value] of Object.entries(payload.fields || {})) {
      const owner = captured[field];
      const currentValue = owner?.element?.value ?? (field.startsWith("fixed_") ? draft.fixed[field.slice(6)] : undefined);
      if (!owner || owner.revision !== promptNormalizationRequests.get(field) || currentValue !== owner.value) continue;
      if (elements[field]) owner.element.value = value;else
      applyPromptField(field, value);
      appliedFields.push(field);
    }
    const changes = (payload.changes || []).filter((change) => appliedFields.includes(change.field));
    if (changes.length && recordUndo) setNormalizationUndo(changes);
    if (payload.changed && appliedFields.length) schedulePersist();
  }
  return { ...payload, appliedFields };
}
async function normalizePromptElement(element) {
  const field = promptFieldForElement(element);
  if (!field) return;
  const context = promptNormalizationContext();
  try {
    await normalizePromptFields({ [field]: element.value });
  } catch (error) {
    if (context !== promptNormalizationContext()) return;
    ui.promptNormalizationStatus.textContent = `规范失败：${error.message}`;
    toast(error.message);
  }
}
async function normalizeSettingsPayload(settings) {
  const fields = Object.fromEntries(
    PROMPT_FIELD_NAMES.filter((field) => typeof settings[field] === "string").map((field) => [field, settings[field]])
  );
  const payload = await request("/api/prompts/normalize", {
    method: "POST",
    body: JSON.stringify({ fields, managedTriggers: settings.lora_managed_triggers || [], model_profile: settings.model_profile || currentProfile() })
  });
  return { ...settings, ...(payload.fields || {}) };
}
function defaultPools() {
  return Object.fromEntries(SECTIONS.map((section) => [section, { mode: "include", ids: [], excluded_ids: [] }]));
}
function currentView() {
  const picker = characterPickerSession();
  if (picker) return picker.view;
  return poolViews[activeSection] || (poolViews[activeSection] = defaultView());
}
function characterPickerSession() {
  return typeof PoolBrowser !== "undefined" ? PoolBrowser.active : null;
}
function poolBrowserProfile() {
  return characterPickerSession()?.profile || currentProfile();
}
function poolBrowserSelection(section = activeSection) {
  return characterPickerSession()?.selection || draft.pools[section];
}
function persistPoolView() {
  if (!characterPickerSession()) schedulePersist();
}
function poolBrowserCount() {
  const picker = characterPickerSession();
  return picker ? picker.selectedTotal ?? picker.selection.ids.length : selectedCount(activeSection);
}
function poolCollapsedGroups(kind) {
  const picker = characterPickerSession();
  if (picker) return kind === "favorite" ? picker.collapsedFavorites : picker.collapsedCustom;
  return (kind === "favorite" ? collapsedFavoriteGroups : collapsedCustomGroups)[activeSection];
}

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

function normalizeProfileId() {
  return "anima";
}
function defaultUpscalers(profile = "anima") {
  return {
    pixel_hires: { enabled: false, model_name: "4x_foolhardy_Remacri.pth", percent: 50, upscale_method: "lanczos" },
    post_sampler: { enabled: false, steps: 16, cfg: 4, sampler_name: "er_sde", scheduler: "simple", denoise: .2 },
    dlss: { enabled: false, style: "电影", intensity: 1, local_tone: 1, local_struct: 1, output_mix: 1, output_view: "处理", gpu_gen: "Auto" },
    latent: { enabled: false, scale_by: 1.5, upscale_method: "sesqui", model_format: "Wan 2.1", half_precision: true,
      sampler: { steps: 16, cfg: 4, sampler_name: "er_sde", scheduler: "simple", denoise: .2 },
   },
    seedvr2: { enabled: false, model_name: "", vae_name: "", scale_by: 2, weight_dtype: "default", color_correction_method: "lab", sampler: { steps: 1, cfg: 1, sampler_name: "euler", scheduler: "simple", denoise: 1 }, encode: { tile_size: 512, overlap: 64, temporal_size: 64, temporal_overlap: 8 }, decode: { tile_size: 512, overlap: 128, temporal_size: 4096, temporal_overlap: 8 } },
    nvidia: { enabled: false, resize_type: "scale by multiplier", scale: 2, quality: "ULTRA" },
    face_refine: { enabled: false, steps: 8, cfg_scale: 1, sampler: "res_2m_ode", scheduler: "beta57", denoise_strength: 0.3, face_selection: 0, detection_confidence: 0.75, manual_rotation: "None", processing_resolution: 1024, enable_pre_upscale: true, upscaler_model: "Fast 4x (Lanczos)", crop_padding: 1.6, face_positive_prompt: "", replace_positive_prompt: false, face_negative_prompt: "", replace_negative_prompt: false, exclusions: "", blend_softness: 8, mask_expansion: 2, sampling_mask_blur_size: 21, sampling_mask_blur_strength: 1, enable_color_correction: true, enable_segmentation: true, enable_differential_diffusion: true, enable_lightness_rescue: true, enable_final_refinement: true, offload_models_to_cpu: true }
  };
}
function normalizeUpscalers(value, fallback = null, profile = "anima", pipelineVersion = 4) {
  const base = clone(defaultUpscalers(profile));
  const raw = value && typeof value === "object" ? value : {};
  for (const source of [fallback || {}, raw]) for (const key of Object.keys(base)) {
    if (source[key] && typeof source[key] === "object") {
      const previous = base[key];
      base[key] = { ...previous, ...source[key] };
      for (const nested of ["sampler", "encode", "decode"]) {
        if (previous[nested] && typeof previous[nested] === "object" && source[key][nested] && typeof source[key][nested] === "object")
        base[key][nested] = { ...previous[nested], ...source[key][nested] };
      }
    }
  }
  base.latent.enabled = Boolean(base.latent.enabled);
  base.latent.scale_by = Number.isFinite(Number(base.latent.scale_by)) ?
  Math.max(0.01, Math.min(64, Number(base.latent.scale_by))) :
  1.5;
  base.seedvr2.enabled = Boolean(base.seedvr2.enabled);
  base.seedvr2.model_name = String(base.seedvr2.model_name || "");
  base.seedvr2.vae_name = String(base.seedvr2.vae_name || "");
  base.seedvr2.scale_by = Number.isFinite(Number(base.seedvr2.scale_by)) ? Math.max(0.01, Math.min(64, Number(base.seedvr2.scale_by))) : 2;
  base.nvidia.enabled = Boolean(base.nvidia.enabled);
  // Ignore the deprecated v1 upscale-model choice. RTXVideoSuperResolution
  // uses the installed GPU/plugin directly and has no model-file input.
  delete base.nvidia.model_name;
  base.face_refine.enabled = Boolean(base.face_refine.enabled);
  for (const key of ["pixel_hires", "post_sampler", "dlss"]) base[key].enabled = Boolean(base[key].enabled);
  delete base.latent.second_sampler;
  delete base.nvidia.dlss;
  return base;
}
function profileDefaults(profile) {
  const id = normalizeProfileId(profile);
  const value = resources.model_profiles?.[id] || resources.profiles?.[id] || profileResources?.[id];
  return value?.defaults && typeof value.defaults === "object" ? value.defaults : {};
}
function profileSettingsDefaults(profile) {
  const id = normalizeProfileId(profile);
  const saved = defaults?.profile_settings?.[id] || defaults?.profiles?.[id]?.settings;
  if (saved) return saved;
  if (normalizeProfileId(defaults?.model_profile || "anima") === id) return defaults || {};
  const bound = profileConfig(id);
  return { ...defaults, ...bound.defaults, model_profile: id,
    ...Object.fromEntries(["model_name", "clip_name", "vae_name", "clip_type"].map((key) => [key, bound[key] || ""])),
    pipeline_version: 4, upscalers: normalizeUpscalers(null, null, id), loras: [], lora_managed_triggers: [], pools: defaultPools() };
}
function defaultLorasForProfile(profile) {
  return clone(profileSettingsDefaults(profile)?.loras || []);
}
function profileConfig(profile) {
  const id = normalizeProfileId(profile);
  return resources.model_profiles?.[id] || resources.profiles?.[id] || profileResources?.[id] || (typeof config !== "undefined" ? config?.model_profiles?.[id] : null) || { id };
}
function profileLabel(profile) {
  const value = profileConfig(profile);
  return value.label || "Anima";
}
function profileStateFrom(settings) {
  return {
    modes: Object.fromEntries(SECTIONS.map((section) => [section, settings[`random_${section}`] ? "pool" : settings[`fixed_${section}`] ? "fixed" : "off"])),
    counts: Object.fromEntries(SECTIONS.map((section) => [section, Number(settings[`random_${section}_count`] || 1)])),
    fixed: Object.fromEntries(SECTIONS.map((section) => [section, settings[`fixed_${section}`] || ""])),
    pools: clone(settings.pools || defaultPools()),
    loras: clone(settings.loras || []),
    managedTriggers: clone(settings.lora_managed_triggers || [])
  };
}
function currentProfile() {
  return "anima";
}
function saveCurrentProfileDraft() {
  if (!ui.model_profile) return;
  profileDrafts[currentProfile()] = {
    ...clone(profileDraftFromDraft()),

    extra_prompt: ui.extra_prompt?.value || "",
    ...profileSamplingState()
  };
}
function profileSamplingState() {
  return {
    pipeline_version: 4,
    model_name: ui.model_name?.value || "",
    base_mode: ui.base_mode?.value || "normal",
    dual: typeof readRandomDualControls === "function" ? readRandomDualControls() : null,
    steps: Number(ui.steps?.value || 0),
    cfg: Number(ui.cfg?.value || 0),
    sampler_name: ui.sampler_name?.value || "",
    scheduler: ui.scheduler?.value || "",
    upscalers: readUpscalerControls()
  };
}
function profileDraftFromDraft() {
  return {
    modes: draft.modes || {}, counts: draft.counts || {}, fixed: draft.fixed || {}, pools: draft.pools || defaultPools(),
    loras: draft.loras || [], managedTriggers: draft.managedTriggers || []
  };
}
function restoreProfileDraft(profile, settings = {}, { preferStored = true } = {}) {
  const stored = profileDrafts[normalizeProfileId(profile)];
  const targetProfile = normalizeProfileId(profile);
  const sourceProfile = normalizeProfileId(settings?.model_profile || targetProfile);
  const profileDefault = profileSettingsDefaults(profile);
  const source = (preferStored ? stored : null) || (settings && settings.modes ? settings : profileStateFrom({ ...profileDefault, ...settings }));
  draft = {
    modes: clone(source.modes || {}), counts: clone(source.counts || {}), fixed: clone(source.fixed || {}),
    pools: clone(source.pools || defaultPools()), loras: clone(source.loras || []), managedTriggers: clone(source.managedTriggers || [])
  };
  profileDrafts[normalizeProfileId(profile)] = {
    pipeline_version: 4,
    ...clone(profileDraftFromDraft()),

    extra_prompt: (preferStored ? stored?.extra_prompt : undefined) ?? settings.extra_prompt ?? "",
    model_name: (preferStored ? stored?.model_name : undefined) ?? (sourceProfile === targetProfile ? settings.model_name : null) ?? profileConfig(profile).model_name ?? "",
    base_mode: (preferStored ? stored?.base_mode : undefined) ?? settings.base_mode,
    dual: clone((preferStored ? stored?.dual : undefined) ?? settings.dual ?? null),
    steps: (preferStored ? stored?.steps : undefined) ?? settings.steps,
    cfg: (preferStored ? stored?.cfg : undefined) ?? settings.cfg,
    sampler_name: (preferStored ? stored?.sampler_name : undefined) ?? settings.sampler_name,
    scheduler: (preferStored ? stored?.scheduler : undefined) ?? settings.scheduler,
    upscalers: normalizeUpscalers((preferStored ? stored?.upscalers : null) || settings.upscalers || defaults?.upscalers, null, targetProfile, (preferStored ? stored?.pipeline_version : null) ?? settings.pipeline_version ?? 2)
  };
}
function switchProfileDraft(nextProfile, { applyDefaults = true } = {}) {
  if (typeof invalidatePoolContext === "function") invalidatePoolContext();
  const next = normalizeProfileId(nextProfile);
  const previous = currentProfile();
  if (previous !== next) saveCurrentProfileDraft();
  activeProfileId = next;
  if (ui.model_profile) ui.model_profile.value = next;
  const state = profileDrafts[next];
  if (state) {
    restoreProfileDraft(next, state);

    if (typeof state.extra_prompt === "string") ui.extra_prompt.value = state.extra_prompt;
  } else {
    const source = typeof randomWorkbenchDefaultSettings === "function" ? randomWorkbenchDefaultSettings(next) : profileSettingsDefaults(next);
    restoreProfileDraft(next, source);
    ui.extra_prompt.value = source.extra_prompt || "";
  }
  const selectedState = profileDrafts[next] || profileSettingsDefaults(next);
  ensureSelectValue(ui.model_name, selectedState.model_name || profileConfig(next).model_name || "");
  if (applyDefaults) {
    const values = profileDefaults(next);
    const profileSettings = selectedState;
    ui.base_mode.value = profileSettings.base_mode || values.base_mode || "normal";
    for (const field of ["steps", "cfg", "sampler_name", "scheduler"]) {
      const value = profileSettings[field] ?? values[field];
      if (value !== undefined && ui[field]) setControl(field, value);
    }
    const repair = profileSettings.upscalers || profileSettingsDefaults(next).upscalers || defaults?.upscalers || defaultUpscalers();
    const normalized = normalizeUpscalers(repair, null, next, selectedState.pipeline_version ?? 2);
    normalized.seedvr2.model_name = normalized.seedvr2.model_name || profileConfig(next).seedvr2_model || "";
    normalized.seedvr2.vae_name = normalized.seedvr2.vae_name || profileConfig(next).seedvr2_vae || "";
    setUpscalerControls(normalized);
    if (typeof setRandomDualControls === "function") setRandomDualControls(profileSettings);
  }
  updateProfileResources();
  renderDimensions();
  renderLoras();
  updatePoolOverview();

  updateRepairControls();
  schedulePersist();
}
function normalizeSettings(raw) {
  const merged = { ...defaults, ...raw };

  merged.model_profile = normalizeProfileId("anima");
  const settingsProfile = normalizeProfileId(raw?.model_profile ?? defaults?.model_profile ?? "anima");
  merged.model_name = String(raw?.model_name ?? (settingsProfile === merged.model_profile ? defaults?.model_name : null) ?? profileConfig(merged.model_profile).model_name ?? "");

  merged.base_mode = (raw?.base_mode ?? defaults?.base_mode) === "dual_sampler" ? "dual_sampler" : "normal";
  const hasPools = raw && raw.pools && typeof raw.pools === "object";
  merged.pools = hasPools ? clone(raw.pools) : defaultPools();
  for (const section of SECTIONS) {
    if (!merged.pools[section]) merged.pools[section] = { mode: "include", ids: [], excluded_ids: [] };
    if (!hasPools && raw?.[`random_${section}`]) merged.pools[section].mode = "all";
  }
  const hasLoras = raw && Object.prototype.hasOwnProperty.call(raw, "loras");
  merged.loras = clone(hasLoras ? raw.loras || [] : defaultLorasForProfile(merged.model_profile));
  merged.lora_managed_triggers = normalizeTriggerWords(raw?.lora_managed_triggers || defaults?.lora_managed_triggers || []);
  merged.upscalers = normalizeUpscalers(raw?.upscalers, defaults?.upscalers);
  merged.pipeline_version = 4;
  const profile = profileConfig(merged.model_profile);
  if (!merged.upscalers.seedvr2.model_name) merged.upscalers.seedvr2.model_name = profile.seedvr2_model || "";
  if (!merged.upscalers.seedvr2.vae_name) merged.upscalers.seedvr2.vae_name = profile.seedvr2_vae || "";
  merged.base_denoise = raw?.base_denoise ?? 1;

  merged.manual_artist = canonicalArtists(merged.manual_artist);
  return merged;
}

const UPSCALER_CONTROL_FIELDS = {
  pixel_hires: ["enabled", "model_name", "percent", "upscale_method"],
  dlss: ["enabled"],
  latent: ["enabled", "scale_by", "upscale_method"],
  seedvr2: ["enabled", "model_name", "vae_name", "weight_dtype", "scale_by", "color_correction_method"],
  nvidia: ["enabled", "resize_type", "scale", "quality"],
  face_refine: ["enabled", "steps", "cfg_scale", "sampler", "scheduler", "denoise_strength", "face_selection", "detection_confidence", "manual_rotation", "processing_resolution", "upscaler_model", "crop_padding", "blend_softness", "mask_expansion", "sampling_mask_blur_size", "sampling_mask_blur_strength", "face_positive_prompt", "replace_positive_prompt", "face_negative_prompt", "replace_negative_prompt", "exclusions", "enable_pre_upscale", "enable_color_correction", "enable_segmentation", "enable_differential_diffusion", "enable_lightness_rescue", "enable_final_refinement", "offload_models_to_cpu"]
};
const UPSCALER_CONTROL_IDS = {
  pixel_hires: { enabled: "pixel_hires_enabled", model_name: "pixel_hires_model_name", percent: "pixel_hires_percent", upscale_method: "pixel_hires_upscale_method" },
  dlss: { enabled: "dlss_enabled" },
  latent: { enabled: "latent_upscale_enabled", scale_by: "latent_upscale_scale", upscale_method: "latent_upscale_method" },
  seedvr2: { enabled: "seedvr2_enabled", model_name: "seedvr2_model_name", vae_name: "seedvr2_vae_name", weight_dtype: "seedvr2_weight_dtype", scale_by: "seedvr2_scale_by", color_correction_method: "seedvr2_color_correction_method" },
  nvidia: { enabled: "nvidia_upscale_enabled", resize_type: "nvidia_resize_type", scale: "nvidia_scale", quality: "nvidia_quality" },
  face_refine: { enabled: "face_refine_enabled", steps: "face_steps", cfg_scale: "face_cfg_scale", sampler: "face_sampler", scheduler: "face_scheduler", denoise_strength: "face_denoise_strength", face_selection: "face_selection", detection_confidence: "face_detection_confidence", manual_rotation: "face_manual_rotation", processing_resolution: "face_processing_resolution", upscaler_model: "face_upscaler_model", crop_padding: "face_crop_padding", blend_softness: "face_blend_softness", mask_expansion: "face_mask_expansion", sampling_mask_blur_size: "face_sampling_mask_blur_size", sampling_mask_blur_strength: "face_sampling_mask_blur_strength", face_positive_prompt: "face_positive_prompt", replace_positive_prompt: "face_replace_positive_prompt", face_negative_prompt: "face_negative_prompt", replace_negative_prompt: "face_replace_negative_prompt", exclusions: "face_exclusions", enable_pre_upscale: "face_enable_pre_upscale", enable_color_correction: "face_enable_color_correction", enable_segmentation: "face_enable_segmentation", enable_differential_diffusion: "face_enable_differential_diffusion", enable_lightness_rescue: "face_enable_lightness_rescue", enable_final_refinement: "face_enable_final_refinement", offload_models_to_cpu: "face_offload_models_to_cpu" }
};
const POST_SAMPLER_IDS = { steps: "latent_second_sampler_steps", cfg: "latent_second_sampler_cfg", sampler_name: "latent_second_sampler_name", scheduler: "latent_second_scheduler", denoise: "latent_second_sampler_denoise" };
const LATENT_SAMPLER_IDS = { steps: "latent_sampler_steps", cfg: "latent_sampler_cfg", sampler_name: "latent_sampler_name", scheduler: "latent_sampler_scheduler", denoise: "latent_sampler_denoise" };
const SEED_SAMPLER_IDS = { steps: "seedvr2_sampler_steps", cfg: "seedvr2_sampler_cfg", sampler_name: "seedvr2_sampler_name", scheduler: "seedvr2_scheduler", denoise: "seedvr2_sampler_denoise" };
const SEED_TILE_IDS = { encode: { tile_size: "seedvr2_encode_tile_size", overlap: "seedvr2_encode_overlap", temporal_size: "seedvr2_encode_temporal_size", temporal_overlap: "seedvr2_encode_temporal_overlap" }, decode: { tile_size: "seedvr2_decode_tile_size", overlap: "seedvr2_decode_overlap", temporal_size: "seedvr2_decode_temporal_size", temporal_overlap: "seedvr2_decode_temporal_overlap" } };

function controlValue(id, fallback = "") {
  const element = ui[id];
  if (!element) return fallback;
  if (element.type === "checkbox") return Boolean(element.checked);
  if (element.type === "number") return Number(element.value);
  return element.value;
}
function setControl(id, value) {
  const element = ui[id];
  if (!element || value === undefined || value === null) return;
  if (element.type === "checkbox") element.checked = Boolean(value);else
  if (element.tagName === "SELECT") ensureSelectValue(element, String(value));else
  element.value = value;
}
function setSamplerValues(ids, values) {for (const [key, id] of Object.entries(ids)) setControl(id, values?.[key]);}
function readSamplerValues(ids) {return Object.fromEntries(Object.entries(ids).map(([key, id]) => [key, controlValue(id)]));}
function setTileValues(values) {for (const [group, ids] of Object.entries(SEED_TILE_IDS)) for (const [key, id] of Object.entries(ids)) setControl(id, values?.[group]?.[key]);}
function readTileValues() {return Object.fromEntries(Object.entries(SEED_TILE_IDS).map(([group, ids]) => [group, Object.fromEntries(Object.entries(ids).map(([key, id]) => [key, controlValue(id)]))]));}
function setUpscalerControls(upscalers) {
  if (typeof sharedParameterState !== "undefined") sharedParameterState.latent = {
    model_format: upscalers?.latent?.model_format || "Wan 2.1", half_precision: upscalers?.latent?.half_precision ?? true
  };
  for (const [stage, fields] of Object.entries(UPSCALER_CONTROL_FIELDS)) for (const field of fields) setControl(UPSCALER_CONTROL_IDS[stage][field], upscalers?.[stage]?.[field]);
  setSamplerValues(POST_SAMPLER_IDS, upscalers?.post_sampler);
  setSamplerValues(LATENT_SAMPLER_IDS, upscalers?.latent?.sampler);
  setControl("latent_second_sampler_enabled", upscalers?.post_sampler?.enabled);
  setSamplerValues(SEED_SAMPLER_IDS, upscalers?.seedvr2?.sampler);
  setTileValues(upscalers?.seedvr2);
  if (ui.latent_model_format) ensureSelectValue(ui.latent_model_format, upscalers?.latent?.model_format || "Wan 2.1");
  if (typeof NVIDIA_DLSS_KEYS !== "undefined") for (const key of NVIDIA_DLSS_KEYS) setControl(`nvidia_dlss_${key}`, upscalers?.dlss?.[key]);
  applyNodeResourceControls();
}
function readUpscalerControls() {
  const result = {};
  for (const [stage, fields] of Object.entries(UPSCALER_CONTROL_FIELDS)) result[stage] = Object.fromEntries(fields.map((field) => [field, controlValue(UPSCALER_CONTROL_IDS[stage][field])]));
  result.post_sampler = { enabled: controlValue("latent_second_sampler_enabled"), ...readSamplerValues(POST_SAMPLER_IDS) };
  result.latent.sampler = readSamplerValues(LATENT_SAMPLER_IDS);
  if (typeof sharedParameterState !== "undefined") Object.assign(result.latent, sharedParameterState.latent);
  result.seedvr2.sampler = readSamplerValues(SEED_SAMPLER_IDS);
  Object.assign(result.seedvr2, readTileValues());
  if (ui.latent_model_format) result.latent.model_format = typeof sharedLatentFormat === "function" ? sharedLatentFormat(ui.latent_model_format.value, sharedParameterState.latent.model_format) : ui.latent_model_format.value;
  if (typeof NVIDIA_DLSS_KEYS !== "undefined") result.dlss = { ...defaultUpscalers().dlss, ...sharedParameterState.dlss, ...result.dlss, ...Object.fromEntries(NVIDIA_DLSS_KEYS.map((key) => [key, controlValue(`nvidia_dlss_${key}`)])) };
  return result;
}

function refreshProfileData() {
  const section = activeSection;
  const profile = currentProfile();
  const revision = typeof poolContextRevision === "undefined" ? null : poolContextRevision;
  ++poolRequest;
  return Promise.allSettled([
  loadResources(),
  loadLoraInventory(),
  loadStylePresets(),
  loadCustomGroups(section).then(() => {
    if (section === activeSection && profile === currentProfile() && (revision === null || revision === poolContextRevision)) return loadPool();
  })]
  );
}
function invalidateResourceInventories() {
  for (const load of [loadResources, loadLoraInventory]) {
    if (!load.cache) continue;
    load.cache.generation++;
    load.cache.values.clear();
    load.cache.pending.clear();
  }
  ++resourceRequest;
  ++loraInventoryRequest;
  resourcesLoaded = false;
  loraInventoryLoaded = false;
}

function applySettings(raw, { useProfileDraft = false } = {}) {
  if (typeof invalidatePoolContext === "function") invalidatePoolContext();
  invalidatePromptNormalization();
  const settings = normalizeSettings(raw || defaults || {});
  const previousProfile = currentProfile();
  activeProfileId = settings.model_profile;
  if (ui.model_profile) ui.model_profile.value = settings.model_profile;
  clearNormalizationUndo();
  if (ui.promptRuleDialog?.open) {clearPromptRuleEditor();void loadPromptRules();}

  ensureSelectValue(ui.model_name, settings.model_name);
  ensureSelectValue(ui.base_mode, settings.base_mode);
  for (const [name, value] of Object.entries(settings)) {
    const element = ui[name];
    if (element && !["object", "undefined"].includes(typeof value)) element.value = value;
  }
  ensureSelectValue(ui.sampler_name, settings.sampler_name);
  ensureSelectValue(ui.scheduler, settings.scheduler);
  const upscalers = normalizeUpscalers(settings.upscalers);
  setUpscalerControls(upscalers);
  if (typeof setRandomDualControls === "function") setRandomDualControls(settings);
  if (typeof sharedParametersApply === "function") sharedParametersApply(settings, raw);
  restoreProfileDraft(settings.model_profile, useProfileDraft ? profileDrafts[settings.model_profile] || settings : settings, { preferStored: useProfileDraft });
  if (typeof refreshPoolSelectionTotals === "function") void refreshPoolSelectionTotals();
  const storedProfileState = profileDrafts[settings.model_profile];
  if (useProfileDraft && storedProfileState) {

  }
  const storedExtraPrompt = useProfileDraft ? storedProfileState?.extra_prompt : null;
  if (typeof storedExtraPrompt === "string") ui.extra_prompt.value = storedExtraPrompt;else
  if (typeof settings.extra_prompt === "string") ui.extra_prompt.value = settings.extra_prompt;
  renderDimensions();
  renderLoras();

  updatePeopleTotal();
  updatePoolOverview();
  updateRepairControls();

  if (loraInventoryLoaded) reconcileLoraTriggers(draft.loras.filter((item) => item.enabled));
  if (initialized && previousProfile !== settings.model_profile) void refreshProfileData();
}

function readSettings() {
  saveCurrentProfileDraft();
  const normalizedArtist = canonicalArtists(ui.manual_artist.value);
  if (ui.manual_artist.value !== normalizedArtist) ui.manual_artist.value = normalizedArtist;
  const settings = {
    pipeline_version: 4,
    count: Number(ui.count.value), female_count: Number(ui.female_count.value), male_count: Number(ui.male_count.value),
    character_detail: ui.character_detail.value, manual_artist: normalizedArtist, quality_prompt: ui.quality_prompt.value,
    extra_prompt: ui.extra_prompt.value, negative_prompt: ui.negative_prompt.value, width: Number(ui.width.value), height: Number(ui.height.value),
    steps: Number(ui.steps.value), cfg: Number(ui.cfg.value), sampler_name: ui.sampler_name.value, scheduler: ui.scheduler.value,
    base_denoise: Number(ui.base_denoise?.value ?? 1),

    model_profile: currentProfile(), model_name: ui.model_name.value,

    base_mode: ui.base_mode.value === "dual_sampler" ? "dual_sampler" : "normal",
    dual: typeof readRandomDualControls === "function" ? readRandomDualControls() : null,
    pools: clone(draft.pools), loras: clone(draft.loras), lora_managed_triggers: clone(draft.managedTriggers || []),
    upscalers: readUpscalerControls()
  };
  for (const section of SECTIONS) {
    settings[`random_${section}`] = draft.modes[section] === "pool";
    settings[`random_${section}_count`] = Number(draft.counts[section] || 1);
    settings[`fixed_${section}`] = draft.modes[section] === "fixed" ? draft.fixed[section] || "" : "";
  }
  return { ...settings, model_profile: "anima", workbench: "random", generation_mode: "txt2img" };
}

function formatSavedTime(timestamp) {
  try {
    return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(
      new Date(timestamp)
    );
  } catch {
    return "刚刚";
  }
}

function persistNow() {
  if (!initialized) return;
  const savedAt = new Date().toISOString();
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ version: 1, savedAt, settings: readSettings(), profileDrafts: clone(profileDrafts), seedMode: document.getElementById("randomSeedMode").value, seed: document.getElementById("randomSampleSeed").value, samplingModes: sharedSamplingDrafts() }));
    localStorage.setItem(VIEW_KEY, JSON.stringify({ version: 1, activeSection: characterPickerSession()?.generationSection || activeSection, views: poolViews }));
    ui.draftStatus.textContent = `本地草稿已保存 ${formatSavedTime(savedAt)}`;
  } catch { ui.draftStatus.textContent = "浏览器未允许保存草稿"; }
}

function schedulePersist() {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(persistNow, 180);
}

function loadStoredView() {
  try {
    const payload = JSON.parse(localStorage.getItem(VIEW_KEY) || "null");
    if (payload?.version !== 1 || typeof payload.views !== "object") return;
    for (const section of SECTIONS)
    poolViews[section] = {
      ...defaultView(),
      ...(payload.views[section] || {}),
      filters: { ...defaultView().filters, ...(payload.views[section]?.filters || {}) }
    };
    if (SECTIONS.includes(payload.activeSection)) activeSection = payload.activeSection;
  } catch {
    localStorage.removeItem(VIEW_KEY);
  }
}

function loadStoredDraft() {
  try {
    const payload = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
    if (payload?.version !== 1 || !payload.settings || typeof payload.settings !== "object") return false;
    profileDrafts = payload.profileDrafts && typeof payload.profileDrafts === "object" ? clone(payload.profileDrafts) : {};
    applySettings(payload.settings, { useProfileDraft: true });
    document.getElementById("randomSeedMode").value = payload.seedMode === "random" ? "random" : "fixed";
    document.getElementById("randomSampleSeed").value = payload.seed ?? "0";
    sharedSamplingRestoreDrafts(payload.samplingModes);
    ui.draftStatus.textContent = `已恢复草稿 ${formatSavedTime(payload.savedAt)}`;
    return true;
  } catch { return false; }
}

function selectedCount(section) {
  const selection = draft.pools[section] || { mode: "include", ids: [], excluded_ids: [] };
  const stats = typeof poolSelectionStats !== "undefined" ? poolSelectionStats.get(section) : null;
  if (stats?.selectionKey === JSON.stringify(selection)) return stats.selectedTotal;
  return selection.mode === "all" ?
  Math.max(0, Number(config.catalog?.counts?.[section] || 0) - new Set(selection.excluded_ids).size) :
  new Set(selection.ids).size;
}

async function refreshPoolSelectionTotals({ throwOnError = false } = {}) {
  const requestId = ++poolTotalsRequest;
  // Workbench/history restoration finishes restoring its complete editor draft
  // synchronously after applySettings; count that final state, not its interim one.
  await Promise.resolve();
  if (requestId !== poolTotalsRequest || currentProfile() !== "anima") return;
  const pools = draft.pools;
  const results = await Promise.allSettled(SECTIONS.map(async (section) => {
    const selection = clone(pools[section]);
    const data = await request(`/api/pools/${section}/query`, { method: "POST", body: JSON.stringify({
        model_profile: "anima", page: 1, limit: 1, selection_state: selection
      }) });
    return { section, selectionKey: JSON.stringify(selection), data };
  }));
  if (requestId !== poolTotalsRequest || currentProfile() !== "anima" || pools !== draft.pools) return;
  for (const result of results) {
    if (result.status !== "fulfilled") continue;
    const { section, selectionKey, data } = result.value;
    if (selectionKey !== JSON.stringify(pools[section])) continue;
    if (Number.isFinite(data.pool_total)) config.catalog.counts[section] = data.pool_total;
    if (Number.isFinite(data.selected_total)) poolSelectionStats.set(section, { selectionKey, selectedTotal: data.selected_total });
  }
  renderDimensions();
  updatePoolOverview();
  if (ui.poolDrawer.classList.contains("open")) updatePoolSelectionControls();
  if (throwOnError && results.some((result) => result.status === "rejected")) throw results.find((result) => result.status === "rejected").reason;
}

function renderDimensions() {
  ui.dimensionList.replaceChildren(
    ...SECTIONS.map((section) => {
      const meta = SECTION_META[section];
      const mode = draft.modes[section] || "off";
      const count = draft.counts[section] || 1;
      const selected = selectedCount(section);
      const article = document.createElement("article");
      article.className = `dimension-row mode-${mode}`;
      article.dataset.section = section;
      article.innerHTML = `<div class="dimension-heading"><div><span class="dimension-icon">${meta.label.slice(0, 1)}</span><strong>${meta.label}</strong><small>${selected} 项已选</small></div><button class="manage-pool" type="button" data-manage="${section}">管理池 <span aria-hidden="true">→</span></button></div>
      <div class="dimension-mode" role="group" aria-label="${meta.label}模式"><button type="button" data-mode="off" class="${mode === "off" ? "active" : ""}">关闭</button><button type="button" data-mode="pool" class="${mode === "pool" ? "active" : ""}">随机池</button><button type="button" data-mode="fixed" class="${mode === "fixed" ? "active" : ""}">固定</button></div>
      <div class="dimension-body"><label class="draw-count"><span>抽取数量</span><input type="number" min="1" max="${section === "expression" ? 1 : 5}" step="1" value="${section === "expression" ? 1 : count}" data-count="${section}" ${mode !== "pool" || section === "expression" ? "disabled" : ""}></label><span class="mode-note">${mode === "pool" ? `${selected} 项候选` : mode === "fixed" ? "使用固定提示词" : "不加入提示词"}</span></div>
      <label class="field dimension-fixed"><span>固定提示词</span><input type="text" value="${escapeHtml(draft.fixed[section] || "")}" data-fixed="${section}" placeholder="留空则不加入" ${mode !== "fixed" ? "disabled" : ""}></label>`;
      article.querySelectorAll("[data-mode]").forEach((button) =>
      button.addEventListener("click", () => {
        draft.modes[section] = button.dataset.mode;
        renderDimensions();
        schedulePersist();
      })
      );
      article.querySelector("[data-count]").addEventListener("input", (event) => {
        draft.counts[section] = Math.max(1, Math.min(5, Number(event.target.value || 1)));
        schedulePersist();
        updatePoseConflict();
      });
      const fixedInput = article.querySelector("[data-fixed]");
      fixedInput.addEventListener("input", (event) => {
        draft.fixed[section] = event.target.value;
        schedulePersist();
      });
      fixedInput.addEventListener("blur", () => normalizePromptElement(fixedInput));
      article.querySelector("[data-manage]").addEventListener("click", () => openPool(section));
      return article;
    })
  );
}

function updatePeopleTotal() {
  const total = Number(ui.female_count.value || 0) + Number(ui.male_count.value || 0);
  ui.peopleTotal.textContent = total ? `共 ${total} 人` : "不限人数";
}
function updatePoolOverview() {
  ui.poolOverview.textContent =

  `${SECTIONS.reduce((sum, section) => sum + selectedCount(section), 0)} 项已选`;
}

function normalizedPath(value) {
  return String(value || "").
  replaceAll("\\", "/").
  toLowerCase();
}
function updateFavoriteSelectionButton() {
  const count = selectedCount(activeSection);
  ui.favoriteSelection.textContent = count ? `收藏本次选择 · ${count}` : "收藏本次选择";
  ui.favoriteSelection.disabled = !favoritesAvailable || count === 0;
}
function normalizeTriggerWords(values) {
  const result = [];
  const seen = new Set();
  for (const value of Array.isArray(values) ? values : []) {
    const word = String(value || "").trim();
    const identity = word.toLocaleLowerCase();
    if (word && !seen.has(identity)) {
      result.push(word);
      seen.add(identity);
    }
  }
  return result;
}
function promptTokens(value) {
  return String(value || "").
  replaceAll("\r", ",").
  replaceAll("\n", ",").
  split(",").
  map((item) => item.trim()).
  filter(Boolean);
}
function triggerIdentity(value) {
  return String(value || "").trim().toLocaleLowerCase();
}
function forgetRemovedManagedTriggers() {
  const present = new Set(promptTokens(ui.extra_prompt.value).map(triggerIdentity));
  draft.managedTriggers = normalizeTriggerWords(
    (draft.managedTriggers || []).filter((word) => present.has(triggerIdentity(word)))
  );
}
function reconcileLoraTriggers(addFor = []) {

  if (!loraInventoryLoaded) return;
  const enabledItems = draft.loras.filter((item) => item.enabled);
  const metadataUnknown = enabledItems.some((item) => {
    const inventoryItem = loraItem(item.filename);
    return !inventoryItem || inventoryItem.trigger_metadata_available === false;
  });
  const current = promptTokens(ui.extra_prompt.value);
  const currentIds = new Set(current.map(triggerIdentity));
  const managed = new Map((draft.managedTriggers || []).map((word) => [triggerIdentity(word), word]));
  const required = new Set();
  for (const item of enabledItems) {
    const inventoryItem = loraItem(item.filename);
    for (const word of normalizeTriggerWords(inventoryItem?.trigger_words || [])) required.add(triggerIdentity(word));
  }
  for (const item of addFor) {
    if (!item.enabled) continue;
    const inventoryItem = loraItem(item.filename);
    for (const word of normalizeTriggerWords(inventoryItem?.trigger_words || [])) {
      const identity = triggerIdentity(word);
      required.add(identity);
      if (!currentIds.has(identity)) {
        current.push(word);
        currentIds.add(identity);
        managed.set(identity, word);
      }
    }
  }
  const keptManaged = [];
  for (const [identity, word] of managed) {
    if (required.has(identity) || metadataUnknown) keptManaged.push(word);
  }
  if (!metadataUnknown) {
    const removed = new Set(
      [...managed.keys()].filter((identity) => !required.has(identity))
    );
    if (removed.size) {
      for (let index = current.length - 1; index >= 0; index--) {
        if (removed.has(triggerIdentity(current[index]))) current.splice(index, 1);
      }
    }
  }
  draft.managedTriggers = normalizeTriggerWords(keptManaged);
  const nextPrompt = current.join(", ");
  if (ui.extra_prompt.value !== nextPrompt) ui.extra_prompt.value = nextPrompt;
}
function loraItem(filename) {
  if (loraInventoryProfile !== currentProfile()) return null;
  const identity = normalizedPath(filename);
  const candidates = loraInventory.filter((item) => item.model_profile === currentProfile() && item.classification_status === "recognized");
  return candidates.find((item) => normalizedPath(item.filename) === identity) || null;
}
function classificationItem(items, filename) {
  const identity = normalizedPath(filename);
  const matches = (items || []).filter((item) => normalizedPath(item.name || item.filename) === identity);
  if (matches.length === 1) return matches[0];
  if (identity.includes("/")) return null;
  const basenameMatches = (items || []).filter((item) => normalizedPath(item.name || item.filename).split("/").pop() === identity);
  return basenameMatches.length === 1 ? basenameMatches[0] : null;
}
function classificationReason(item, fallback) {
  if (item?.model_profile && item.model_profile !== currentProfile()) {
    return `LoRA Manager 识别为 ${profileLabel(item.model_profile)}，与当前 ${profileLabel(currentProfile())} 档案不匹配`;
  }
  return item?.classification_reason || fallback;
}
function loraSelectionIssue(filename) {
  if (!loraInventoryLoaded || loraInventoryProfile !== currentProfile()) return loraInventoryError || "正在读取 LoRA Manager 分类";
  if (loraItem(filename)) return "";
  return classificationReason(classificationItem(loraClassificationItems, filename), "文件不在当前 ComfyUI LoRA 清单中");
}
function modelSelectionIssue(filename = ui.model_name?.value || "") {
  if (!resourcesLoaded || resourceInventoryProfile !== currentProfile()) return resourceError || "正在读取 LoRA Manager 分类";
  if (resources.models.includes(filename)) return "";
  if (!normalizedPath(filename).includes("/")) {
    const names = (resources.model_items || []).map((item) => item.name || item.filename);
    const exact = names.some((name) => normalizedPath(name) === normalizedPath(filename));
    if (!exact && names.filter((name) => normalizedPath(name).split("/").pop() === normalizedPath(filename)).length > 1) return "存在多个同名主模型，请选择完整路径";
  }
  return classificationReason(classificationItem(resources.model_items, filename), filename ? "文件不在当前 ComfyUI 主模型清单中" : "没有识别为当前档案的主模型");
}
function generationClassificationIssues(settings = null) {
  const issues = [];
  const modelName = settings?.model_name ?? ui.model_name?.value ?? "";
  const modelIssue = modelSelectionIssue(modelName);
  if (modelIssue) issues.push(`主模型：${modelIssue}`);
  const activeLoras = settings?.loras || draft.loras || [];
  if (!loraInventoryLoaded || loraInventoryProfile !== currentProfile()) issues.push(loraInventoryError || "正在读取 LoRA Manager 分类");else
  for (const item of activeLoras) {
    if (!item.enabled) continue;
    const reason = loraSelectionIssue(item.filename);
    if (reason) issues.push(`${item.filename}：${reason}`);
  }
  if (typeof sharedParameterResourceIssues === "function") issues.push(...sharedParameterResourceIssues(settings || { model_profile: currentProfile(), upscalers: typeof readUpscalerControls === "function" ? readUpscalerControls() : { face_refine: { enabled: ui.face_refine_enabled?.checked, sampler: ui.face_sampler?.value, scheduler: ui.face_scheduler?.value } } }));
  return [...new Set(issues)];
}
function updateGenerationAvailability() {
  if (!ui.startButton) return;
  const issues = generationClassificationIssues();

  const storage = config.storage_status?.history;
  ui.startButton.disabled = submitInFlight || !ui.connection.classList.contains("online") || issues.length > 0 || Boolean(storage?.read_only);
  ui.startButton.title = storage?.read_only ? storage.reason || "历史存储已受保护" : issues.join("；");
}
function loraPresentation(inventoryItem, fallback) {
  const path = String(inventoryItem?.normalized_path || fallback || "").replaceAll("\\", "/");
  const parts = path.split("/").filter(Boolean);
  const basename = inventoryItem?.basename || parts.pop() || "未命名 LoRA";
  const folder = inventoryItem?.folder || parts.join("/") || "根目录";
  return { path, basename, folder, label: `${folder} / ${basename}` };
}
function renderLoras() {
  const loras = Array.isArray(draft.loras) ? draft.loras : [];
  const enabled = loras.filter((item) => item.enabled).length;
  ui.loraSummary.textContent = `${enabled} 启用 / ${loras.length} 配置`;
  ui.loraEmpty.hidden = loras.length > 0;
  ui.loraList.replaceChildren(
    ...loras.map((item, index) => {
      const selectionIssue = loraSelectionIssue(item.filename);
      const available = !selectionIssue;
      const high = Math.abs(Number(item.strength)) > 2;
      const row = document.createElement("div");
      row.className = `lora-row${available ? "" : " missing"}`;
      const inventoryItem = loraItem(item.filename);
      const display = loraPresentation(inventoryItem, item.filename);
      const triggers = normalizeTriggerWords(inventoryItem?.trigger_words || []);
      const triggerLabel = inventoryItem?.trigger_override ? "触发词（已覆盖）" : "触发词";
      row.innerHTML = `<input class="lora-toggle" type="checkbox" ${item.enabled ? "checked" : ""} title="启用 LoRA" aria-label="启用 ${escapeHtml(item.filename)}"><div class="lora-name"><strong title="${escapeHtml(display.path)}">${escapeHtml(display.label)}</strong><small class="${available && !high ? "" : "lora-warning"}">${available ? high ? "高强度 · 模型 + CLIP" : "模型 + CLIP" : escapeHtml(selectionIssue)}</small>${triggers.length || inventoryItem?.trigger_override ? `<small class="lora-trigger-line" title="自动触发词">${triggerLabel}：${triggers.length ? escapeHtml(triggers.join(", ")) : "已禁用"}</small>` : ""}</div><input class="lora-strength" type="number" min="-100" max="100" step="0.05" value="${Number(item.strength)}" title="LoRA strength" aria-label="${escapeHtml(item.filename)} 强度"><div class="lora-actions"><button class="button ghost compact lora-trigger-edit" type="button" data-lora-triggers ${inventoryItem ? "" : "disabled"}>触发词</button><button class="icon-button dark" type="button" data-lora-up title="上移" aria-label="上移">↑</button><button class="icon-button dark" type="button" data-lora-down title="下移" aria-label="下移">↓</button><button class="icon-button dark" type="button" data-lora-remove title="移除" aria-label="移除">×</button></div>`;
      row.querySelector(".lora-toggle").addEventListener("change", (event) => {
        item.enabled = event.target.checked;
        reconcileLoraTriggers(item.enabled ? [item] : []);
        renderLoras();
        schedulePersist();
      });
      row.querySelector(".lora-strength").addEventListener("change", (event) => {
        const value = Number(event.target.value);
        if (!Number.isFinite(value) || value < -100 || value > 100) {
          toast("LoRA strength 必须在 -100 到 100 之间");
          renderLoras();
          return;
        }
        item.strength = Math.round(value * 100) / 100;
        renderLoras();
        schedulePersist();
      });
      row.querySelector("[data-lora-up]").disabled = index === 0;
      row.querySelector("[data-lora-down]").disabled = index === loras.length - 1;
      row.querySelector("[data-lora-up]").addEventListener("click", () => moveLora(index, -1));
      row.querySelector("[data-lora-down]").addEventListener("click", () => moveLora(index, 1));
      row.querySelector("[data-lora-triggers]")?.addEventListener("click", () => openLoraTriggerDialog(item));
      row.querySelector("[data-lora-remove]").addEventListener("click", () => {
        draft.loras.splice(index, 1);
        reconcileLoraTriggers();
        renderLoras();
        renderLoraCatalog();
        schedulePersist();
      });
      return row;
    })
  );
  updateGenerationAvailability();
  if (typeof LoraManager !== "undefined") LoraManager.refreshConfigured?.();
}
function moveLora(index, delta) {
  const next = index + delta;
  if (next < 0 || next >= draft.loras.length) return;
  [draft.loras[index], draft.loras[next]] = [draft.loras[next], draft.loras[index]];
  renderLoras();
  schedulePersist();
}
async function loadLoraInventory({ refresh = false } = {}) {
  const cache = loadLoraInventory.cache ||= { generation: 0, values: new Map(), pending: new Map() };
  const requestedProfile = currentProfile();
  if (refresh && !cache.pending.get(requestedProfile)?.refresh) {cache.generation++;cache.values.clear();cache.pending.clear();}
  const generation = cache.generation;
  const requestId = ++loraInventoryRequest;
  const cached = cache.values.get(requestedProfile);
  if (!cached) loraInventoryLoaded = false;
  loraInventoryError = "";
  if (loraInventoryProfile !== requestedProfile) {
    loraInventoryProfile = requestedProfile;
    loraInventory = [];
    loraClassificationItems = [];
  }
  if (!cached) {renderLoras();renderLoraCatalog();}
  try {
    let pending = cache.pending.get(requestedProfile);
    if (!cached && !pending) {
      pending = request(`/api/loras?model_profile=${encodeURIComponent(requestedProfile)}${refresh ? "&refresh=1" : ""}`).then((data) => {
        if (generation === cache.generation) cache.values.set(requestedProfile, data);
        return data;
      }).finally(() => {if (cache.pending.get(requestedProfile) === pending) cache.pending.delete(requestedProfile);});
      pending.refresh = refresh;
      cache.pending.set(requestedProfile, pending);
    }
    const data = clone(cached || (await pending));
    if (generation !== cache.generation || requestId !== loraInventoryRequest || requestedProfile !== currentProfile()) return;
    loraInventory = data.items || [];
    loraClassificationItems = Array.isArray(data.classification_items) ? data.classification_items : loraInventory;
    loraInventoryLoaded = true;
    loraInventoryError = "";
    reconcileLoraTriggers(draft.loras.filter((item) => item.enabled));
    renderLoras();
    renderLoraCatalog();
  } catch (error) {
    if (generation !== cache.generation || requestId !== loraInventoryRequest || requestedProfile !== currentProfile()) return;
    loraInventory = [];
    loraClassificationItems = [];
    loraInventoryLoaded = false;
    loraInventoryError = error.message;
    renderLoras();
    renderLoraCatalog();
  }
  updateRepairControls();

}
function renderLoraCatalog() {
  if (!ui.loraCatalog) return;
  const query = String(ui.loraSearch.value || "").
  trim().
  toLowerCase();
  const configured = new Set(
    draft.loras.map((item) => normalizedPath(loraItem(item.filename)?.filename || item.filename))
  );
  const items = loraInventory.filter(
    (item) =>
    !query || `${item.normalized_path || item.filename} ${item.display_name || ""} ${(item.trigger_words || []).join(" ")}`.toLowerCase().includes(query)
  );
  ui.loraCatalogEmpty.hidden = Boolean(loraInventoryError) || items.length > 0;
  ui.loraCatalog.replaceChildren(
    ...items.map((item) => {
      const configuredItem = configured.has(normalizedPath(item.filename));
      const display = loraPresentation(item, item.filename);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "lora-catalog-item";
      button.disabled = configuredItem;
      const triggers = normalizeTriggerWords(item.trigger_words || []);
      const preview = item.preview ?
      `<img loading="lazy" decoding="async" src="${escapeHtml(item.preview)}" alt="" data-lora-preview>` :
      "";
      button.innerHTML = `<span class="lora-catalog-preview">${preview}<span class="lora-preview-placeholder" aria-hidden="true">LoRA</span></span><span class="lora-catalog-copy"><strong title="${escapeHtml(item.display_name || display.path)}">${escapeHtml(item.display_name || display.basename)}</strong><small title="${escapeHtml(display.path)}">${escapeHtml(display.folder)} / ${escapeHtml(display.basename)}${configuredItem ? " · 已配置" : ""}</small><span class="lora-catalog-triggers">${item.trigger_override ? "已覆盖 · " : ""}${triggers.length ? `触发词：${escapeHtml(triggers.join(", "))}` : item.trigger_override ? "已禁用触发词" : "无触发词"}</span></span><span class="lora-catalog-add" aria-hidden="true">${configuredItem ? "✓" : "+"}</span>`;
      button.querySelector("[data-lora-preview]")?.addEventListener("error", (event) => {
        event.currentTarget.remove();
      });
      button.addEventListener("click", () => {
        const lora = { filename: item.filename, enabled: true, strength: item.default_strength ?? 1 };
        draft.loras.push(lora);
        reconcileLoraTriggers([lora]);
        renderLoras();
        renderLoraCatalog();
        schedulePersist();
      });
      return button;
    })
  );
  ui.loraCatalogMeta.textContent = loraInventoryError || `${loraInventory.length} 个本地 LoRA`;
}
async function openLoraDialog() {
  ui.loraSearch.value = "";
  motionDialog(ui.loraDialog, true);
  await loadLoraInventory();
  renderLoraCatalog();
}
function addManagedLoras(items) {
  const configured = new Set(draft.loras.map((item) => normalizedPath(item.filename)));
  const added = [],skipped = [];
  for (const item of items || []) {
    const filename = item.filename;
    const inventory = item.classification_status ? item : loraItem(filename) || item;
    const reason = configured.has(normalizedPath(filename)) ? "已配置" :
    inventory.available === false ? "资源当前不可用" :
    inventory.model_profile !== currentProfile() || inventory.classification_status !== "recognized" ? "与当前模型档案不兼容" :
    draft.loras.length >= 64 ? "最多配置 64 个 LoRA" : "";
    if (reason) {skipped.push({ filename, reason });continue;}
    // The library can discover newer metadata than the quick-add cache. Keep
    // classification and effective triggers current before changing the draft.
    replaceLoraInventoryItem(inventory);
    const strength = Number(item.default_strength ?? 1);
    const value = { filename, enabled: true, strength: Number.isFinite(strength) ? Math.min(100, Math.max(-100, strength)) : 1 };
    draft.loras.push(value);
    added.push(value);
    configured.add(normalizedPath(filename));
  }
  reconcileLoraTriggers(added);
  renderLoras();
  schedulePersist();
  return { added, skipped };
}
function removeManagedLoras(items) {
  const names = new Set((items || []).map((item) => normalizedPath(typeof item === "string" ? item : item.filename)));
  const removed = draft.loras.filter((item) => names.has(normalizedPath(item.filename)));
  const found = new Set(removed.map((item) => normalizedPath(item.filename)));
  draft.loras = draft.loras.filter((item) => !names.has(normalizedPath(item.filename)));
  reconcileLoraTriggers();
  renderLoras();
  renderLoraCatalog();
  schedulePersist();
  return { removed, skipped: [...names].filter((name) => !found.has(name)).map((filename) => ({ filename, reason: "未配置" })) };
}
function initializeLoraManager() {
  if (typeof LoraManager === "undefined") return;
  LoraManager.init({
    getProfile: currentProfile,
    getLoras: () => draft.loras,
    addLoras: addManagedLoras,
    removeLoras: removeManagedLoras,
    openCharacterPicker,
    onCompactRestore: (query) => {ui.loraSearch.value = query ?? ui.loraSearch.value;renderLoraCatalog();},
    request,
    notify: toast,
    onInventoryChanged: () => loadLoraInventory({ refresh: true })
  });
}
function parseLoraTriggerInput(value) {
  return normalizeTriggerWords(
    String(value || "").
    replaceAll("\r", ",").
    replaceAll("\n", ",").
    replaceAll("，", ",").
    split(",")
  );
}
function replaceLoraInventoryItem(value) {
  const identity = normalizedPath(value.filename);
  // Trigger overrides are shared by both workbenches. Update every cached
  // profile and reject inventories which began before this edit completed.
  if (typeof loadLoraInventory === "function" && loadLoraInventory.cache) {
    const cache = loadLoraInventory.cache;
    cache.generation++;
    cache.pending.clear();
    ++loraInventoryRequest;
    for (const [profile, saved] of cache.values) {
      const items = (saved.items || []).filter((item) => normalizedPath(item.filename) !== identity);
      if (value.model_profile === profile && value.classification_status === "recognized") items.push(clone(value));
      const classified = (saved.classification_items || saved.items || []).filter((item) => normalizedPath(item.name || item.filename) !== identity);
      classified.push(clone(value));
      cache.values.set(profile, { ...saved, items, count: items.length, classification_items: classified });
    }
  }
  loraClassificationItems = loraClassificationItems.filter((item) => normalizedPath(item.name || item.filename) !== identity);
  loraClassificationItems.push(value);
  loraInventory = loraInventory.filter((item) => normalizedPath(item.filename) !== identity);
  if (value.model_profile === currentProfile() && value.classification_status === "recognized") loraInventory.push(value);
  if (typeof loadLoraInventory === "function" && loadLoraInventory.cache && !loraInventoryLoaded) void loadLoraInventory();
}
function openLoraTriggerDialog(configuredItem) {
  const inventoryItem = loraItem(configuredItem.filename);
  if (!inventoryItem) return;
  editingLoraTrigger = configuredItem;
  const display = loraPresentation(inventoryItem, configuredItem.filename);
  ui.loraTriggerTitle.textContent = `编辑 ${inventoryItem.display_name || display.basename}`;
  ui.loraTriggerFilename.value = inventoryItem.filename;
  ui.loraTriggerWords.value = normalizeTriggerWords(inventoryItem.trigger_words || []).join("\n");
  const source = normalizeTriggerWords(inventoryItem.source_trigger_words || []);
  ui.loraSourceTriggerWords.textContent = source.length ? source.join(", ") : "无";
  ui.resetLoraTriggers.disabled = !inventoryItem.trigger_override;
  motionDialog(ui.loraTriggerDialog, true);
}
function queueLoraTriggerWrite(profile, filename, send) {
  const queues = queueLoraTriggerWrite.queues ||= new Map();
  const key = JSON.stringify([profile, normalizedPath(filename)]);
  const queue = queues.get(key) || { revision: 0, pending: null };
  queues.set(key, queue);
  const revision = ++queue.revision;
  const execute = async () => {
    const value = await send();
    // Every committed write updates the cache, including an earlier write
    // followed by a queued operation that may subsequently fail.
    replaceLoraInventoryItem(value);
    return value;
  };
  const pending = queue.pending ? queue.pending.catch(() => {}).then(execute) : execute();
  queue.pending = pending;
  const settled = () => {if (queue.pending === pending) queue.pending = null;};
  pending.then(settled, settled);
  return { promise: pending, isLatest: () => queue.revision === revision };
}
async function saveLoraTriggers(event) {
  event.preventDefault();
  if (!editingLoraTrigger) return;
  const edited = editingLoraTrigger,profile = currentProfile(),filename = ui.loraTriggerFilename.value;
  const body = JSON.stringify({ filename, model_profile: profile, triggerWords: parseLoraTriggerInput(ui.loraTriggerWords.value) });
  const operation = queueLoraTriggerWrite(profile, filename, () => request("/api/loras/triggers", { method: "PUT", body }));
  const currentEditor = () => operation.isLatest() && profile === currentProfile() && editingLoraTrigger === edited;
  try {
    const value = await operation.promise;
    if (!currentEditor()) return;
    reconcileLoraTriggers(edited.enabled ? [edited] : []);
    renderLoras();
    renderLoraCatalog();
    schedulePersist();
    motionDialog(ui.loraTriggerDialog, false);
    toast(value.trigger_words?.length ? "LoRA 触发词已保存" : "已禁用该 LoRA 的自动触发词");
  } catch (error) {
    if (!currentEditor()) return;
    reconcileLoraTriggers(edited.enabled ? [edited] : []);
    renderLoras();
    renderLoraCatalog();
    schedulePersist();
    toast(error.message);
  }
}
async function resetLoraTriggers() {
  if (!editingLoraTrigger) return;
  const edited = editingLoraTrigger,profile = currentProfile(),filename = ui.loraTriggerFilename.value;
  const url = `/api/loras/triggers?filename=${encodeURIComponent(filename)}&model_profile=${encodeURIComponent(profile)}`;
  const operation = queueLoraTriggerWrite(profile, filename, () => request(url, { method: "DELETE" }));
  const currentEditor = () => operation.isLatest() && profile === currentProfile() && editingLoraTrigger === edited;
  try {
    await operation.promise;
    if (!currentEditor()) return;
    reconcileLoraTriggers(edited.enabled ? [edited] : []);
    renderLoras();
    renderLoraCatalog();
    schedulePersist();
    motionDialog(ui.loraTriggerDialog, false);
    toast("已恢复 LoRA Manager 原始触发词");
  } catch (error) {
    if (!currentEditor()) return;
    reconcileLoraTriggers(edited.enabled ? [edited] : []);
    renderLoras();
    renderLoraCatalog();
    schedulePersist();
    toast(error.message);
  }
}

let currentQueue = [];
let submitInFlight = false;
function renderBatchPayload(payload) {
  renderBatch(payload.batch, payload.queue || []);
}
function setQueuePanelOpen(open) {
  if (!ui.queuePanel) return;
  ui.queuePanel.classList.toggle("open", open);
  ui.queueBadge?.setAttribute("aria-expanded", String(open));
  if (typeof StudioMotion === "undefined") {ui.queuePanel.hidden = !open;return;}
  StudioMotion.presence(ui.queuePanel, open, {
    from: { opacity: 0, y: 8, scale: .985 },
    render: (value) => {
      const inline = false;
      ui.queuePanel.style.opacity = value.opacity;
      ui.queuePanel.style.transform = `${inline ? "" : "translateX(-50%) "}translateY(${value.y}px) scale(${value.scale})`;
    }
  });
}

function renderQueue(queue) {
  currentQueue = queue;
  if (!ui.queueBadge || !ui.queuePanel) return;
  ui.queueBadge.hidden = queue.length === 0;
  ui.queueBadge.textContent = `队列 ${queue.length}`;
  if (queue.length === 0) {
    setQueuePanelOpen(false);
  }
  const existing = new Map([...ui.queuePanel.children].map((row) => [row.dataset.queueId, row]));
  const retained = new Set();
  queue.forEach((entry, index) => {
    const key = String(entry.queue_id);
    let row = existing.get(key);
    if (!row) {
      row = document.createElement("div");
      row.className = "queue-row";
      row.dataset.queueId = key;
      const label = document.createElement("span");
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "icon-button dark";
      remove.title = "移出队列";
      remove.setAttribute("aria-label", "移出队列");
      remove.textContent = "×";
      remove.addEventListener("click", async () => {
        try {
          const payload = await request(`/api/batches/queue/${row.dataset.queueId}`, { method: "DELETE" });
          renderQueue(payload.queue || []);
        } catch (error) {toast(error.message);}
      });
      row.append(label, remove);
    }
    retained.add(row);
    const label = row.firstElementChild;
    label.textContent = `${entry.position}. ${entry.count} 张 · ${entry.model_name}`;
    if (ui.queuePanel.children[index] !== row) ui.queuePanel.insertBefore(row, ui.queuePanel.children[index] || null);
  });
  for (const row of existing.values()) if (!retained.has(row)) row.remove();
}
function batchProgressLabel(batch) {
  if (batch.preview_status === "disconnected") return "进度连接已断开，正在重连";
  if (batch.progress_stage_label) return batch.progress?.max > 0 ? `${batch.progress_stage_label} ${batch.progress.value}/${batch.progress.max}` : `正在处理${batch.progress_stage_label}`;
  const node = String(batch.progress_node || "");
  const sampler = { "5": "底图采样", "691:1240": "底图采样", "701:1245": "第一段采样", "701:1246": "第二段采样", "704:1248": "潜空间二次采样", "712:1208": "SeedVR2 采样" }[node];
  const progress = batch.progress;
  if (progress?.max > 0) return `${sampler || "处理进度"} ${progress.value}/${progress.max}`;
  // Model loading and the first denoising step can take a while. Keep the
  // observed stage visible without inventing a sampling percentage.
  if (sampler) return `${sampler} · 等待首步`;
  if (["691:239", "701:539", "704:359", "712:718"].includes(node)) return "正在解码图像";
  if (node === "v2:output") return "正在保存图片";
  if (node.startsWith("167:")) return "正在准备模型";
  if (node.startsWith("1010:") || node.startsWith("1026:")) return "正在准备提示词";
  if (node === "701:1450" || node.startsWith("704:")) return "正在处理潜空间放大";
  if (node.startsWith("712:")) return "正在进行 SeedVR2 处理";
  if (node.startsWith("1253:")) return "正在进行脸部细化";
  if (node.startsWith("1432:")) return "正在进行 NVIDIA 处理";
  if (node) return "正在处理生成任务";
  if (batch.progress_node === "") return "正在收取生成结果";
  return batch.prompt_id ? "等待生成进度" : "正在提交任务";
}
function loraRuntimeSummary(runtime, warningCount = 0) {
  if (!runtime || typeof runtime !== "object") return "";
  const applied = Array.isArray(runtime.applied) ? runtime.applied : [];
  const skipped = Array.isArray(runtime.skipped) ? runtime.skipped : [];
  const parts = [];
  if (applied.length) parts.push(`本张人物自动启用 ${applied.length} 个 LoRA`);
  if (skipped.length) parts.push(`本张跳过 ${skipped.length} 项：${skipped.map((item) => `${item.character_name || "人物"} · ${item.filename}（${item.message || item.reason || "不可用"}）`).join("；")}`);
  if (warningCount) parts.push(`本批累计跳过 ${warningCount} 项，原因已保存在图片详情`);
  return parts.join("\n");
}
function loraRuntimeMarkup(runtime) {
  if (!runtime || typeof runtime !== "object" || !runtime.version) return "";
  const groups = [["人物自动启用", runtime.applied], ["已跳过", runtime.skipped], ["合并说明", runtime.conflicts]];
  const rows = groups.filter(([, items]) => Array.isArray(items) && items.length).map(([label, items]) =>
  `<section><strong>${label}</strong><ul>${items.map((item) => `<li>${escapeHtml(item.character_name || item.character_id || "人物")} · ${escapeHtml(item.filename)}${item.strength != null ? ` × ${escapeHtml(item.strength)}` : ""}${item.message ? ` — ${escapeHtml(item.message)}` : item.reason === "manual_override" ? " — 使用手动设置" : item.reason === "duplicate" ? " — 已合并，采用首个人物权重" : ""}</li>`).join("")}</ul></section>`).join("");
  return rows ? `<details class="lora-runtime-details"><summary>人物 LoRA · 本张实际应用记录</summary>${rows}<small>绑定规则在入队时固定；复现使用本张实际生效的配置。</small></details>` : "";
}
function setProgressValue(fill, ratio, active, stage) {
  if (!fill) return;
  const value = Math.max(0, Math.min(1, Number(ratio) || 0));
  // A new batch/sampler is a new measurement, not backwards progress.
  const reset = fill.motionStage !== stage || value === 0;
  fill.motionStage = stage;
  fill.style.width = "100%";
  fill.style.transition = reset ? "none" : "";
  fill.style.transform = `scaleX(${value})`;
  fill.parentElement?.classList?.toggle("is-active", Boolean(active));
}
function renderBatch(batch, queue = currentQueue) {
  currentBatch = batch;

  renderQueue(queue);
  const active = Boolean(batch && ["running", "stopping"].includes(batch.status));
  const completed = batch?.completed || 0;
  const total = batch?.total || 0;
  const labels = {
    running: "正在生成",
    stopping: "完成当前张后停止",
    completed: "批次完成",
    stopped: "已停止",
    error: "执行失败"
  };
  ui.batchState.textContent = batch ? labels[batch.status] || batch.status : "等待任务";
  ui.progressCount.textContent = `${completed} / ${total}`;
  setProgressValue(ui.progressFill, total ? completed / total : 0, active, batch?.id || "");
  // 单张采样进度(来自 ComfyUI websocket,尽力而为;旧版页面缺控件时跳过)
  if (ui.sampleFill && ui.sampleProgress) {
    const progress = active ? batch.progress : null;
    const sampleTrack = ui.sampleFill.parentElement;
    ui.sampleProgress.hidden = !active;
    ui.sampleProgress.textContent = active ? batchProgressLabel(batch) : "";
    if (progress && progress.max > 0) {
      sampleTrack.hidden = false;
      setProgressValue(ui.sampleFill, progress.value / progress.max, active, `${batch.id}:${batch.progress_node}`);
    } else {
      sampleTrack.hidden = true;
      setProgressValue(ui.sampleFill, 0, false, `${batch?.id}:${batch?.progress_node}`);
    }
  }
  ui.stopButton.disabled = !active || batch.status === "stopping";
  // 运行中仍可加入队列，但分类有效性与连接状态共用同一守卫。
  updateGenerationAvailability();
  ui.startButton.textContent = active ? "加入队列" : "开始生成";
  const failurePrefix = batch?.failed_stage_label ? `${batch.failed_stage_label}失败：` : "";
  const batchMessage = [batch?.error && (failurePrefix && !String(batch.error).startsWith(failurePrefix) ? `${failurePrefix}${batch.error}` : batch.error), batch?.cancel_warning].filter(Boolean).join("\n");
  ui.batchError.hidden = !batchMessage;
  ui.batchError.textContent = batchMessage;
  const loraNotice = typeof document !== "undefined" ? document.getElementById("loraBatchNotice") : null;
  if (loraNotice) {
    const text = loraRuntimeSummary(batch?.lora_runtime, batch?.lora_warning_count || 0);
    loraNotice.hidden = !text;
    loraNotice.textContent = text;
  }
  if (batch && !active && lastTerminalBatch !== `${batch.id}:${batch.status}:${completed}`) {
    lastTerminalBatch = `${batch.id}:${batch.status}:${completed}`;
    if (batch.status === "completed" && typeof StudioMotion !== "undefined") {
      StudioMotion.spring(ui.progressCount, "complete", { opacity: 1 }, {
        initial: { opacity: .45 }, response: .22,
        onUpdate: (value) => {ui.progressCount.style.opacity = value.opacity;},
        onComplete: () => ui.progressCount.style.removeProperty("opacity")
      });
    }
    // New output can shift every page boundary, including a page being read
    // by the fullscreen viewer. Preserve selections, invalidate only reads.
    if (typeof invalidateHistoryView === "function") invalidateHistoryView();else
    if (typeof historyCache !== "undefined") historyCache.clear();

    const keepPosition = typeof artworkSelectionActive === "function" && artworkSelectionActive() ||
    typeof historyDetailState !== "undefined" && historyDetailState.homeQuery.folderId;
    loadHistory(keepPosition ? historyPage : 1);

  }
}
async function refreshConnection() {
  if (document.hidden) return;
  if (refreshConnection.pending) return refreshConnection.pending;
  refreshConnection.pending = (async () => {
    const previous = refreshConnection.online;
    let online = false;
    try {
      const status = await request("/api/status");
      online = Boolean(status.online);
      ui.connection.className = `status-badge ${status.online ? "online" : "offline"}`;
      ui.connection.querySelector("span").textContent = status.online ? `ComfyUI ${status.version}` : "ComfyUI 离线";
    } catch {
      ui.connection.className = "status-badge offline";
      ui.connection.querySelector("span").textContent = "ComfyUI 离线";
    }
    refreshConnection.online = online;
    if (previous === true && !online) invalidateResourceInventories();
    if (previous === false && online) {
      invalidateResourceInventories();
      await Promise.allSettled([loadResources({ refresh: true }), loadLoraInventory({ refresh: true })]);
    }
    updateGenerationAvailability();
  })();
  try {await refreshConnection.pending;} finally
  {refreshConnection.pending = null;}
}
let pollErrorShown = false;
let pollIdleTicks = 0;
let pollBatchPending = false;
let pollBatchLast = 0;
async function pollBatch() {
  if (document.hidden || pollBatchPending) return;
  const batchActive = Boolean(currentBatch && ["running", "stopping"].includes(currentBatch.status));
  const interval = batchActive ? 1200 : 4800;
  if (pollIdleTicks && Date.now() - pollBatchLast < interval) return;
  pollIdleTicks++;pollBatchLast = Date.now();pollBatchPending = true;
  const batchAtStart = currentBatch;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const payload = await request("/api/batches/current", { signal: controller.signal, cache: "no-store" });
    // request() tolerates unreadable JSON, including an aborted response body.
    controller.signal.throwIfAborted();
    // A submit/stop response can replace the batch while this read is in
    // flight. Its newer state takes precedence over this older snapshot.
    if (currentBatch !== batchAtStart) return;
    renderBatchPayload(payload);
    pollErrorShown = false;
  } catch (error) {
    // 服务端不可达时只提示一次,恢复后自动复位,不再每 1.2 秒刷屏。
    if (!pollErrorShown) {
      toast(error.name === "AbortError" ? "读取生成进度超时，正在重试" : error.message);
      pollErrorShown = true;
    }
  } finally {
    clearTimeout(timeout);
    pollBatchPending = false;
  }
}
function card(record, index, historyView = null) {
  const article = document.createElement("article");
  article.className = "image-card is-loading";
  article.dataset.recordId = String(record.id);
  article.record = record;
  article.historyView = historyView;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "image-card-open";
  button.dataset.recordId = String(record.id);
  button.setAttribute("aria-label", `查看 ${record.filename || "生成结果"}`);
  button.innerHTML = `<img loading="lazy" decoding="async" fetchpriority="low" src="/api/images/${record.id}/thumbnail" alt="Anima 生成结果"><span class="image-failure" hidden>图片加载失败</span><div class="card-copy"><strong>${escapeHtml(record.filename)}</strong><span>#${record.sequence} · Seed ${record.sample_seed}</span></div>`;
  button.addEventListener("click", () => {
    if (typeof artworkActivateCard === "function" && artworkActivateCard(article.record)) return;
    openDetail(article.record, { historyView: article.historyView });
  });
  const image = button.querySelector("img");
  const markLoaded = () => {
    article.classList.remove("is-loading");
    image.classList.add("image-loaded");
  };
  image.addEventListener("load", markLoaded, { once: true });
  const markFailed = (event) => {
    article.classList.remove("is-loading");
    event?.target?.classList.add("image-failed");
    button.querySelector(".image-failure").hidden = false;
  };
  image.addEventListener("error", markFailed, { once: true });
  if (image.complete) {
    if (image.naturalWidth) markLoaded();else
    markFailed({ target: image });
  }
  article.append(button);
  if (typeof decorateArtworkCard === "function") decorateArtworkCard(article, record, button);
  if (typeof IntersectionObserver !== "undefined") {
    card.visibilityObserver ||= new IntersectionObserver((entries) => {
      for (const entry of entries) entry.target.classList.toggle("is-offscreen", !entry.isIntersecting);
    }, { rootMargin: "100px" });
    card.visibilityObserver.observe(article);
  }
  return article;
}
let historyRequest = 0;
function historyViewState(view = null) {
  if (view) return view;
  return historyViewState.home ||= {
    kind: "home", queryEpoch: 0, total: 0,
    get query() {return typeof historyDetailState === "undefined" ? { source: "" } : historyDetailState.homeQuery;},
    get page() {return historyPage;}, set page(value) {historyPage = value;},
    get pages() {return historyPages;}, set pages(value) {historyPages = value;},
    get items() {return historyItems;}, set items(value) {historyItems = value;},
    get cache() {return historyCache;},
    get requestId() {return historyRequest;}, set requestId(value) {historyRequest = value;},
    get dirty() {return historyDirty;}, set dirty(value) {historyDirty = value;},
    get elements() {return ui;}
  };
}
function cacheHistoryPage(data, queryKey = null, historyView = null) {
  const view = historyViewState(historyView);
  queryKey ??= typeof historyQueryKey === "function" ? historyQueryKey(view) : "";
  if (!data || !Number.isFinite(Number(data.page))) return;
  if (typeof historyQueryKey === "function" && queryKey !== historyQueryKey(view)) return;
  data.historyQueryKey = queryKey;
  view.cache.set(Number(data.page), data);
}

function renderHistoryPage(data, historyView = null) {
  const view = historyViewState(historyView),elements = view.elements;
  view.dirty = false;
  const previousPage = view.page;
  view.page = Number(data.page) || 1;
  view.pages = Number(data.pages) || 1;
  view.total = Number(data.total) || 0;
  view.items = Array.isArray(data.items) ? data.items : [];
  cacheHistoryPage(data, null, view);
  const existing = new Map([...elements.gallery.children].map((node) => [node.dataset.recordId, node]));
  const retained = new Set();
  view.items.forEach((record, index) => {
    let node = existing.get(String(record.id));
    const fresh = !node;
    if (!node) node = card(record, index, view);
    node.record = record;
    node.historyView = view;
    const button = node.querySelector(".image-card-open");
    button.setAttribute("aria-label", `查看 ${record.filename || "生成结果"}`);
    node.querySelector(".card-copy strong").textContent = record.filename || "生成结果";
    node.querySelector(".card-copy span").textContent = `#${record.sequence} · Seed ${record.sample_seed}`;
    if (typeof decorateArtworkCard === "function") decorateArtworkCard(node, record, button);
    retained.add(node);
    if (elements.gallery.children[index] !== node) elements.gallery.insertBefore(node, elements.gallery.children[index] || null);
    if (fresh && previousPage === view.page && existing.size && typeof StudioMotion !== "undefined") {
      node.hidden = true;
      StudioMotion.presence(node, true, { from: { opacity: 0, y: 6 }, manageInert: false });
    }
  });
  for (const node of existing.values()) if (!retained.has(node)) {
    card.visibilityObserver?.unobserve(node);
    node.remove();
  }
  if (previousPage !== view.page && typeof StudioMotion !== "undefined") {
    StudioMotion.spring(elements.gallery, "page", { opacity: 1 }, {
      initial: { opacity: .4 }, response: .2,
      onUpdate: (value) => {elements.gallery.style.opacity = value.opacity;},
      onComplete: () => elements.gallery.style.removeProperty("opacity")
    });
  }
  elements.emptyState.hidden = view.items.length > 0;
  const historyUnavailable = config.storage_status?.history?.available === false;
  elements.emptyState.querySelector("strong").textContent = historyUnavailable ? "历史记录暂不可用" : "等待第一张作品";
  elements.emptyState.querySelector("small").textContent = historyUnavailable ?
  "原历史文件已受保护，生成已暂停。请按顶部提示修复文件后重启。" :
  "完成生成后会出现在这里";
  elements.pageLabel.textContent = `${view.page} / ${view.pages}`;
  elements.prevPage.disabled = view.page <= 1;
  elements.nextPage.disabled = view.page >= view.pages;
  elements.historyCount.textContent = historyUnavailable ? "历史存储不可用" : `${data.total || 0} 张图片`;
  if (typeof artworkHistoryRendered === "function") artworkHistoryRendered(view);
  updatePreviewNavigation();
}

async function loadHistory(page = historyPage, historyView = null) {
  const view = historyViewState(historyView);
  // 与 loadPool 相同的竞态守卫:快速翻页时丢弃乱序到达的旧响应。
  const requestId = ++view.requestId;
  const queryKey = typeof historyQueryKey === "function" ? historyQueryKey(view) : "";
  try {
    const params = typeof historyQueryParams === "function" ? historyQueryParams(view) : "";
    const data = await request(`/api/history?page=${page}&limit=24${params}`);
    if (view.closed || requestId !== view.requestId || typeof historyQueryKey === "function" && queryKey !== historyQueryKey(view)) return;
    // Removing a collection's last page must land on a valid page, not an empty
    // offset beyond the new total. The nested read owns a fresh request token.
    if (Number(data.page) > Number(data.pages) && Number(data.pages) > 0) return loadHistory(Number(data.pages), view);
    renderHistoryPage(data, view);
    return data;
  } catch (error) {
    if (view.closed || requestId !== view.requestId) return;
    toast(error.message);
  }
}

function resetRevealImageFile() {
  ++revealFileRequest;
  ui.revealImageFile.disabled = false;
  ui.revealImageFile.textContent = "打开所在文件夹";
  ui.revealImageFile.removeAttribute("aria-busy");
}

async function revealSelectedImageFile() {
  if (!selectedRecord || ui.revealImageFile.disabled) return;
  const record = selectedRecord,requestId = ++revealFileRequest;
  ui.revealImageFile.disabled = true;
  ui.revealImageFile.textContent = "正在定位…";
  ui.revealImageFile.setAttribute("aria-busy", "true");
  try {
    const result = await request(`/api/history/${encodeURIComponent(record.id)}/reveal`, { method: "POST", body: JSON.stringify({}) });
    if (requestId !== revealFileRequest || selectedRecord !== record) return;
    if (!result?.opened) throw new Error("未能打开所在文件夹，请重试。");
    toast("已在文件夹中选中原图");
  } catch (error) {
    if (requestId === revealFileRequest && selectedRecord === record) toast(error.message || "打开所在文件夹失败，请重试。");
  } finally {
    if (requestId === revealFileRequest && selectedRecord === record) resetRevealImageFile();
  }
}

function renderDetail(record) {
  if (typeof renderArtworkDetail === "function") renderArtworkDetail(record);
  resetRevealImageFile();
  ui.detailImage.src = `/api/images/${record.id}`;
  ui.detailImage.alt = record.filename ? `生成结果：${record.filename}` : "生成结果";
  ui.detailMeta.textContent = `${record.created_at || ""} · ${record.filename || ""}`;
  ui.detailPositive.value = record.positive_prompt || record.resolved_prompt || "";
  ui.detailNegative.value = record.negative_prompt || "";

  const settings = normalizeSettings(record.settings || {});
  const stats = [
  ["尺寸", `${settings.width} × ${settings.height}`],
  ["采样", `${settings.sampler_name} / ${settings.scheduler} / ${settings.steps} steps / CFG ${settings.cfg}`],
  ["图像种子", record.sample_seed],
  ["提示词种子", record.prompt_seed],
  ["人物", `${settings.female_count || 0} 女 / ${settings.male_count || 0} 男`]];

  ui.detailStats.innerHTML = stats.
  map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).
  join("");
  const selected = record.resolved_selection || {};
  const activeLoras = (settings.loras || []).filter((item) => item.enabled);
  const loraText = activeLoras.length ?
  activeLoras.map((item) => `${escapeHtml(item.filename)} × ${escapeHtml(item.strength)}`).join("、") :
  "未使用";
  ui.detailSelection.innerHTML = `<span class="kicker">ACTUAL DRAW</span><div>${SECTIONS.map((section) => `<span><b>${SECTION_META[section].label}</b>${(selected[section] || []).map((item) => escapeHtml(item.title)).join("、") || "未使用"}</span>`).join("")}<span><b>LoRA</b>${loraText}</span></div>${loraRuntimeMarkup(record.lora_runtime)}`;
}

function updateDetailRecord(record) {
  if (typeof closeArtworkPopovers === "function") closeArtworkPopovers({ restoreFocus: false, immediate: true });
  selectedRecord = record;
  ++regenerationRequest;
  regenerationOptions = null;
  closeRegenerationDrawers(false, true);
  if (typeof closeDetailActionPanels === "function") closeDetailActionPanels({ immediate: true });
  renderDetail(record);

}
function openDetail(record, { origin = "random", returnFocus = null, historyView = null } = {}) {
  if (!record) return;
  closeImagePreview({ immediate: true, restoreFocus: false });
  detailContext = { origin, returnFocus: returnFocus || document.activeElement, historyView: historyViewState(historyView) };
  if (typeof showHistoryDetail === "function") showHistoryDetail();
  updateDetailRecord(record);
  updatePreviewNavigation();
  motionDialog(ui.detailDialog, true);
}
function suppressDetailReturnFocus() {
  if (detailContext) detailContext.returnFocus = null;
}
function invalidateHistoryTransfer() {

}
function finishDetailClose() {
  if (ui.detailDialog.open) return;
  if (typeof closeArtworkPopovers === "function") closeArtworkPopovers({ restoreFocus: false, immediate: true });

  resetRevealImageFile();
  const context = detailContext;
  closeImagePreview({ immediate: true, restoreFocus: false });
  ui.detailImage.onload = ui.detailImage.onerror = null;
  ui.detailImage.removeAttribute("src");
  ++regenerationRequest;
  regenerationOptions = null;
  detailContext = null;
  closeRegenerationDrawers(false, true);
  if (typeof closeDetailActionPanels === "function") closeDetailActionPanels({ immediate: true });
  if (typeof resetHistoryDetail === "function") resetHistoryDetail();

  if (document.querySelector?.("dialog[open]")) return;
  if (context?.returnFocus?.isConnected && !context.returnFocus.disabled) context.returnFocus.focus({ preventScroll: true });else
  if (context?.returnFocus && context.origin === "random") {
    // Crossing random archive pages replaces the original thumbnail element.
    const target = ui.gallery.querySelector(`button[data-record-id="${selectedRecord?.id}"]`) || ui.gallery.querySelector("button") || document.getElementById("artworkHistoryTitle");
    target?.focus({ preventScroll: true });
  }
}

function setDetailActionPanelVisible(panel, visible, immediate = false) {
  if (typeof DetailActionFold !== "undefined") DetailActionFold.set(panel, visible, immediate);else
  {panel.hidden = !visible;panel.inert = !visible;}
}

function closeDetailActionPanels({ restoreFocus = false, immediate = false } = {}) {
  let trigger = null;
  for (const button of document.querySelectorAll("[data-detail-action]")) {
    if (button.getAttribute("aria-expanded") === "true") trigger = button;
    button.setAttribute("aria-expanded", "false");
  }
  for (const panel of document.querySelectorAll("[data-detail-panel]")) setDetailActionPanelVisible(panel, false, immediate);
  if (restoreFocus) trigger?.focus({ preventScroll: true });
  return Boolean(trigger);
}

function toggleDetailActionPanel(name) {
  if (typeof historyDetailVisible === "function" && !historyDetailVisible()) return;
  const trigger = document.querySelector(`[data-detail-action="${name}"]`);
  const panel = document.querySelector(`[data-detail-panel="${name}"]`);
  if (!trigger || !panel) return;
  const opening = trigger.getAttribute("aria-expanded") !== "true";
  closeRegenerationDrawers(false);
  closeDetailActionPanels();
  if (!opening) return;
  setDetailActionPanelVisible(panel, true);
  trigger.setAttribute("aria-expanded", "true");
}

function containHistoryDetailFocus(event) {
  if (event.defaultPrevented || event.key !== "Tab" || !ui.detailDialog.open) return;
  const focusable = [...ui.detailDialog.querySelectorAll("button, a[href], input, select, textarea, summary, [tabindex]")].filter((element) => {
    if (element.tabIndex < 0 || element.matches(":disabled") || !element.getClientRects().length || element.closest("[hidden], [inert]")) return false;
    if (getComputedStyle(element).visibility === "hidden") return false;
    // Closed disclosures can still report a box for their nested summary.
    for (let parent = element.parentElement; parent && parent !== ui.detailDialog; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS" && !parent.open && !parent.querySelector(":scope > summary")?.contains(element)) return false;
    }
    return true;
  });
  if (!focusable.length) {event.preventDefault();return;}
  const current = focusable.indexOf(document.activeElement);
  if (current < 0 || (event.shiftKey ? current === 0 : current === focusable.length - 1)) {
    event.preventDefault();focusable[event.shiftKey ? focusable.length - 1 : 0].focus({ preventScroll: true });
  }
}

const IMAGE_PREVIEW_MAX_ZOOM = 4;
const imagePreviewReducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const clampPreview = (value, min, max) => Math.min(max, Math.max(min, value));
const previewMotion = (name, fallback) => {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const duration = Number.parseFloat(value);
  return Number.isFinite(duration) ? duration : fallback;
};

function currentPreviewRecord() {
  return selectedRecord;
}
function setCurrentPreviewRecord(record) {

  selectedRecord = record;if (record) renderDetail(record);
}

function previewHistoryView() {return historyViewState(imagePreview.context?.historyView || (typeof detailContext !== "undefined" ? detailContext?.historyView : null));}
function previewRecordIndex(record, items = previewHistoryView().items) {
  if (!record || !Array.isArray(items)) return -1;
  return items.findIndex((item) => String(item.id) === String(record.id));
}

function selectedHistoryLocation() {

  const view = previewHistoryView();
  if (view.query?.folderId && !(selectedRecord?.folder_ids || []).includes(view.query.folderId)) {
    return { page: view.page, items: view.items, index: -1 };
  }
  const currentIndex = previewRecordIndex(selectedRecord);
  if (currentIndex >= 0) return { page: view.page, items: view.items, index: currentIndex };
  for (const [page, data] of view.cache) {
    const items = Array.isArray(data?.items) ? data.items : [];
    const index = previewRecordIndex(selectedRecord, items);
    if (index >= 0) return { page, items, index };
  }
  return { page: view.page, items: view.items, index: -1 };
}

function previewNeighborAvailable(direction) {

  const location = selectedHistoryLocation();
  if (location.index < 0) return false;
  if (location.index >= 0 && location.index + direction >= 0 && location.index + direction < location.items.length) return true;
  const targetPage = location.page + (direction > 0 ? 1 : -1);
  return targetPage >= 1 && targetPage <= previewHistoryView().pages;
}

function updatePreviewNavigation() {
  const active = Boolean((currentPreviewRecord() || false) && imagePreview.phase !== "closed");
  const locked = ["loading", "closing"].includes(imagePreview.phase);
  const navigating = imagePreview.phase === "switching";
  const previousAvailable = active && previewNeighborAvailable(-1);
  const nextAvailable = active && previewNeighborAvailable(1);
  ui.imagePreviewPrevious.disabled = locked || !navigating && !previousAvailable;
  ui.imagePreviewNext.disabled = locked || !navigating && !nextAvailable;
  ui.imagePreviewDialog.classList.toggle("has-navigation", active && (navigating || previousAvailable || nextAvailable));
}

function announcePreview(message) {
  ui.imagePreviewAnnouncement.textContent = message;
}

function previewPositionLabel(record = currentPreviewRecord()) {

  const view = previewHistoryView();
  const location = previewRecordIndex(record) >= 0 ?
  { page: view.page, items: view.items, index: previewRecordIndex(record) } :
  selectedHistoryLocation();
  if (location.index < 0) return record?.filename || "当前图片";
  const total = Number(view.elements.historyCount.textContent.match(/\d+/)?.[0] || 0);
  let position = (location.page - 1) * 24 + location.index + 1;
  let priorPagesAreCached = true;
  let cachedBefore = 0;
  for (let page = 1; page < location.page; page += 1) {
    const data = view.cache.get(page);
    if (!data || !Array.isArray(data.items)) {
      priorPagesAreCached = false;
      break;
    }
    cachedBefore += data.items.length;
  }
  if (priorPagesAreCached) position = cachedBefore + location.index + 1;
  return `${record?.filename || "当前图片"}，第 ${position} 张${total ? `，共 ${total} 张` : ""}`;
}

function clearPreviewCache() {
  imagePreview.cacheEpoch = (imagePreview.cacheEpoch || 0) + 1;
  for (const promise of imagePreview.preloaded.values()) promise.release?.();
  imagePreview.preloaded.clear();
}
function trimPreviewCache(protectedRecord = null) {
  const current = String(currentPreviewRecord()?.id);
  const protectedKey = protectedRecord ? String(protectedRecord.id) : null;
  const keep = new Set([current]);
  const location = selectedHistoryLocation();
  if (location.index >= 0) {
    for (const offset of [-1, 0, 1]) {
      const record = location.items[location.index + offset];
      if (record) keep.add(String(record.id));
    }
  }
  // A cross-page navigation target is decoded before it becomes current.
  if (protectedKey !== null) keep.add(protectedKey);
  for (const [key, promise] of imagePreview.preloaded) {
    if (keep.has(key)) continue;
    imagePreview.preloaded.delete(key);
    promise.release?.();
  }
  for (const [key, promise] of imagePreview.preloaded) {
    if (imagePreview.preloaded.size <= 3) break;
    if (key === current || key === protectedKey) continue;
    imagePreview.preloaded.delete(key);
    promise.release?.();
  }
}
function preloadRecord(record) {
  if (!record) return Promise.resolve(null);
  const key = String(record.id);
  const cached = imagePreview.preloaded.get(key);
  if (cached) {
    imagePreview.preloaded.delete(key);
    imagePreview.preloaded.set(key, cached);
    trimPreviewCache(record);
    return cached;
  }
  const image = new Image();
  const context = imagePreview.context,epoch = imagePreview.cacheEpoch || 0;
  let released = false;
  image.decoding = "async";
  image.src = `/api/images/${record.id}`;
  const promise = image.decode().
  then(() => !released && context === imagePreview.context && epoch === (imagePreview.cacheEpoch || 0) && imagePreview.preloaded.get(key) === promise ? image : null).
  catch(() => {
    if (imagePreview.preloaded.get(key) === promise) imagePreview.preloaded.delete(key);
    return null;
  });
  promise.release = () => {released = true;image.src = "";};
  imagePreview.preloaded.set(key, promise);
  trimPreviewCache(record);
  return promise;
}

function preloadPreviewNeighbors() {
  if (imagePreview.phase !== "open") return;
  trimPreviewCache();
  const location = selectedHistoryLocation();
  if (location.index < 0) return;
  for (const direction of [-1, 1]) {
    const neighbor = location.items[location.index + direction];
    if (neighbor) preloadRecord(neighbor);
  }
}

async function loadHistoryPageForPreview(page, historyView = null) {
  const view = historyView || previewHistoryView();
  const queryKey = typeof historyQueryKey === "function" ? historyQueryKey(view) : "";
  const cached = view.cache.get(page);
  if (cached && cached.historyQueryKey === queryKey) return cached;
  const params = typeof historyQueryParams === "function" ? historyQueryParams(view) : "";
  const data = await request(`/api/history?page=${page}&limit=24${params}`);
  if (view.closed || typeof historyQueryKey === "function" && queryKey !== historyQueryKey(view)) return null;
  cacheHistoryPage(data, queryKey, view);
  return data;
}

function previewNeighbor(direction, location = selectedHistoryLocation()) {
  if (location.index < 0) return null;
  const index = location.index + direction;
  if (location.index >= 0 && index >= 0 && index < location.items.length) {
    return { record: location.items[index], page: location.page, data: null };
  }
  const targetPage = location.page + (direction > 0 ? 1 : -1);
  if (targetPage < 1 || targetPage > previewHistoryView().pages) return null;
  return { record: null, page: targetPage, data: null };
}

function playPreviewAnimation(from, to, duration) {
  if (typeof StudioMotion !== "undefined" && StudioMotion.spring) {
    const animation = springPreviewFrames(from, to, duration);
    return animation.finished.then((result) => {
      if (imagePreview.animation === animation) imagePreview.animation = null;
      return !result.cancelled;
    });
  }
  cancelImagePreviewAnimation();
  const reduce = imagePreviewReducedMotion();
  const animation = ui.imagePreviewImage.animate(
    reduce ? [from, to] : [from, to],
    { duration: reduce ? 120 : duration, easing: getComputedStyle(document.documentElement).getPropertyValue("--spring").trim(), fill: "both" }
  );
  imagePreview.animation = animation;
  return animation.finished.then(() => {
    if (imagePreview.animation === animation) {
      animation.commitStyles();
      animation.cancel();
      imagePreview.animation = null;
    }
    return true;
  }).catch(() => false);
}

async function switchPreviewImage(record, requestId, { src = null, label = null } = {}) {
  const context = imagePreview.context;
  const current = () => requestId === imagePreview.navigationRequest && imagePreview.context === context;
  const previousRecord = currentPreviewRecord();
  const previousSrc = imagePreview.committedSrc || ui.imagePreviewImage.currentSrc || ui.imagePreviewImage.src;
  const image = src ? await new Promise((resolve) => {const value = new Image();value.src = src;value.decode().then(() => resolve(value)).catch(() => resolve(null));}) : await preloadRecord(record);
  if (!image || !current() || imagePreview.phase !== "switching") return false;
  const fadeOut = await playPreviewAnimation(liveImagePreviewFrame(), { ...liveImagePreviewFrame(), opacity: 0 }, previewMotion("--motion-fast", 150));
  if (!fadeOut || !current() || imagePreview.phase !== "switching") return false;

  ui.imagePreviewImage.src = src || `/api/images/${record.id}`;
  ui.imagePreviewImage.alt = `生成结果全尺寸预览：${label || record?.filename || "当前图片"}`;
  ui.imagePreviewImage.hidden = false;
  ui.imagePreviewImage.style.opacity = "0";
  imagePreview.zoom = 1;
  imagePreview.x = imagePreview.y = 0;
  let decoded = true;
  try {
    await ui.imagePreviewImage.decode();
  } catch {
    decoded = false;
  }
  if (!decoded && current() && imagePreview.phase === "switching") {
    setCurrentPreviewRecord(previousRecord);
    ui.imagePreviewImage.src = previousSrc;
    ui.imagePreviewImage.alt = `生成结果全尺寸预览：${previousRecord?.filename || "当前图片"}`;
    ui.imagePreviewImage.hidden = false;
    ui.imagePreviewImage.style.opacity = "1";
    imagePreview.zoom = 1;
    imagePreview.x = imagePreview.y = 0;
    await ui.imagePreviewImage.decode().catch(() => null);
    layoutImagePreview();
    ui.imagePreviewTitle.textContent = previousRecord?.filename || "全尺寸图片预览";
  }
  if (!current() || imagePreview.phase !== "switching") return false;
  // Commit the detail at the same time the decoded new bitmap is displayed.
  // Esc during its entry animation must return to this image's details.
  if (decoded) {
    setCurrentPreviewRecord(record);
    imagePreview.committedSrc = src || `/api/images/${record.id}`;
    imagePreview.context?.onRecordCommit?.(record);
  }
  layoutImagePreview();
  const target = imagePreviewFrame();
  const enter = imagePreviewReducedMotion() ? { ...target, opacity: 0 } : { ...target, opacity: 0, transform: `${target.transform} scale(.985)` };
  imagePreview.phase = "opening";
  const entered = await playPreviewAnimation(enter, target, previewMotion("--motion-medium", 240));
  if (!entered || !current() || imagePreview.phase !== "opening") return false;
  imagePreview.phase = "open";
  if (imagePreview.sourcePresence) completeImagePreviewSourcePresence();
  ui.imagePreviewTitle.textContent = decoded ? label || record?.filename || "全尺寸图片预览" : previousRecord?.filename || "全尺寸图片预览";
  ui.imagePreviewStatus.hidden = true;
  announcePreview(previewPositionLabel(record));
  updatePreviewNavigation();
  ui.imagePreviewDialog.classList.remove("is-navigating");
  preloadPreviewNeighbors();
  return decoded;
}

async function navigatePreview(direction, focusTarget = null) {

  if (!["open", "opening", "switching"].includes(imagePreview.phase)) return;
  const context = imagePreview.context,view = previewHistoryView();
  const restoreFocus = () => {
    if (!focusTarget?.isConnected) return;
    if (!focusTarget.disabled) focusTarget.focus({ preventScroll: true });else
    ui.imagePreviewImage.focus({ preventScroll: true });
  };
  const requestId = ++imagePreview.navigationRequest;
  const current = () => requestId === imagePreview.navigationRequest && imagePreview.context === context && imagePreview.phase !== "closing" && imagePreview.phase !== "closed";
  // Resolve navigation against the preceding intent, not the last decoded image.
  // Page reads serialize cursor discovery; only the newest intent paints a bitmap.
  const previous = imagePreview.navigationCursor?.context === context ?
  imagePreview.navigationCursor.promise : Promise.resolve(selectedHistoryLocation());
  const cursor = { context, promise: previous.then(async (location) => {
      const target = previewNeighbor(direction, location);
      if (!target) return { ...location, boundary: true };
      if (target.record) return { page: location.page, items: location.items, index: location.index + direction, data: location.data };
      const data = await loadHistoryPageForPreview(target.page, view);
      if (!data) return { ...location, boundary: true };
      const items = Array.isArray(data.items) ? data.items : [];
      return items.length ? { page: target.page, items, index: direction > 0 ? 0 : items.length - 1, data } : { ...location, boundary: true };
    }) };
  imagePreview.navigationCursor = cursor;
  ui.imagePreviewDialog.classList.add("is-navigating");
  imagePreview.phase = "switching";
  updatePreviewNavigation();
  try {
    const target = await cursor.promise;
    if (!current()) return;
    const record = target.items[target.index];
    if (!record) throw new Error(direction < 0 ? "已经是第一张图片" : "已经是最后一张图片");
    if (target.boundary && String(record.id) === String(currentPreviewRecord().id)) {
      imagePreview.phase = "open";
      ui.imagePreviewDialog.classList.remove("is-navigating");
      announcePreview(direction < 0 ? "已经是第一张图片" : "已经是最后一张图片");
      updatePreviewNavigation();restoreFocus();return;
    }
    const switched = await switchPreviewImage(record, requestId);
    if (!current()) return;
    if (!switched) throw new Error("图片加载失败，请重试");
    if (target.data) renderHistoryPage(target.data, view);
    if (target.boundary) announcePreview(direction < 0 ? "已经是第一张图片" : "已经是最后一张图片");
    restoreFocus();
  } catch (error) {
    if (!current()) return;
    imagePreview.navigationCursor = null;
    if (typeof restorePreviewCommittedImage === "function") await restorePreviewCommittedImage(requestId, context);
    if (!current()) return;
    imagePreview.phase = "open";
    ui.imagePreviewDialog.classList.remove("is-navigating");
    announcePreview(error.message || "相邻图片加载失败");
    updatePreviewNavigation();
    restoreFocus();
  }
}

async function restorePreviewCommittedImage(requestId, context) {
  const record = currentPreviewRecord();
  if (!record) return;
  const source = imagePreview.committedSrc || `/api/images/${record.id}`;
  cancelImagePreviewAnimation();
  if (ui.imagePreviewImage.getAttribute("src") !== source) {
    ui.imagePreviewImage.src = source;
    await ui.imagePreviewImage.decode().catch(() => null);
  }
  if (requestId !== imagePreview.navigationRequest || context !== imagePreview.context || imagePreview.phase === "closing" || imagePreview.phase === "closed") return;
  ui.imagePreviewImage.hidden = false;
  ui.imagePreviewImage.style.opacity = "1";
  ui.imagePreviewImage.style.clipPath = "inset(0px round 18px)";
  ui.imagePreviewImage.alt = `生成结果全尺寸预览：${record.filename || "当前图片"}`;
  ui.imagePreviewTitle.textContent = record.filename || "全尺寸图片预览";
  layoutImagePreview();
}

function imagePreviewFrame() {
  const style = ui.imagePreviewImage.style;
  return { width: style.width, height: style.height, transform: style.transform || "none", clipPath: "inset(0px round 18px)", opacity: 1 };
}

function cancelImagePreviewAnimation({ keepReveal = false, keepFlight = false } = {}) {
  if (!keepFlight) {imagePreview.animation?.cancel();imagePreview.animation = null;}
  imagePreview.panSpring?.cancel();
  imagePreview.panSpring = null;
  if (!keepReveal) {
    imagePreview.revealAnimation?.cancel();
    imagePreview.revealAnimation = null;
  }
  if (imagePreview.zoomFrame != null) cancelAnimationFrame(imagePreview.zoomFrame);
  imagePreview.zoomFrame = null;
  if (imagePreview.renderFrame != null) {
    cancelAnimationFrame(imagePreview.renderFrame);
    imagePreview.renderFrame = null;
    applyImagePreviewTransform();
  }
}

function animateImagePreview(from, to, duration, onFinish = () => {}) {
  if (typeof StudioMotion !== "undefined" && StudioMotion.spring) {
    const animation = springPreviewFrames(from, to, duration);
    animation.finished.then((result) => {
      if (result.cancelled || imagePreview.animation !== animation) return;
      imagePreview.animation = null;onFinish();
    });
    return;
  }
  cancelImagePreviewAnimation();
  const reduce = imagePreviewReducedMotion();
  const animation = ui.imagePreviewImage.animate(
    reduce ? imagePreview.phase === "closing" ? [from, { ...from, opacity: 0 }] : [{ ...to, opacity: 0 }, to] : [from, to],
    { duration: reduce ? 120 : duration, easing: getComputedStyle(document.documentElement).getPropertyValue("--spring").trim(), fill: "both" }
  );
  imagePreview.animation = animation;
  animation.finished.then(() => {
    if (imagePreview.animation !== animation) return;
    cancelImagePreviewAnimation();
    onFinish();
  }).catch(() => {}); // Reversing/closing during an animation is expected.
}

// Fade the veil from its live opacity so interrupting a fade never jumps.
function animateImagePreviewVeil(toOpacity, duration, onFinish = () => {}) {
  const veil = ui.imagePreviewVeil;
  if (toOpacity === 0) imagePreview.veilCovered = false;
  const from = getComputedStyle(veil).opacity;
  veil.getAnimations().forEach((animation) => animation.cancel());
  const animation = veil.animate([{ opacity: from }, { opacity: toOpacity }], {
    duration: imagePreviewReducedMotion() ? 120 : duration, fill: "both"
  });
  imagePreview.veilAnimation = animation;
  animation.finished.then(() => {
    if (imagePreview.veilAnimation !== animation) return;
    imagePreview.veilCovered = toOpacity === 1;
    completeImagePreviewSourcePresence();
    onFinish();
  }).catch(() => {});
  return animation;
}

function freezeImagePreviewSourcePresence() {
  imagePreview.sourcePresence = null;
  if (typeof StudioMotion === "undefined" || !StudioMotion.snapshot?.(ui.detailDialog, "presence")?.active) return;
  StudioMotion.freeze(ui.detailDialog, "presence");
  imagePreview.sourcePresence = { element: ui.detailDialog, context: imagePreview.context };
}

function completeImagePreviewSourcePresence(force = false) {
  const source = imagePreview.sourcePresence;
  if (!source || source.context !== imagePreview.context) return;
  const covered = imagePreview.veilCovered && ["open", "error"].includes(imagePreview.phase);
  if (!force && !covered) return;
  imagePreview.sourcePresence = null;
  if (!source.element.open || StudioMotion.target?.(source.element) === false) return;
  // Under the full veil the parent can settle immediately. An early dismissal
  // returns to the same frozen source, then lets its presence resume naturally.
  StudioMotion.dialog(source.element, true, { immediate: covered, restoreFocus: false });
}

function liveImagePreviewFrame() {
  const style = getComputedStyle(ui.imagePreviewImage);
  return { width: style.width, height: style.height, transform: style.transform, clipPath: style.clipPath, opacity: style.opacity };
}

function numericPreviewFrame(frame) {
  let sx = 1,sy = 1,x = 0,y = 0;
  const transform = frame.transform || "none";
  if (typeof DOMMatrixReadOnly === "function") {
    const matrix = new DOMMatrixReadOnly(transform === "none" ? undefined : transform);
    sx = Math.hypot(matrix.a, matrix.b) || 1;sy = Math.hypot(matrix.c, matrix.d) || 1;
    x = matrix.e;y = matrix.f;
  } else {
    const matrix = transform.match(/^matrix\(([^)]+)\)/)?.[1].split(",").map(Number);
    if (matrix) {sx = Math.hypot(matrix[0], matrix[1]) || 1;sy = Math.hypot(matrix[2], matrix[3]) || 1;x = matrix[4];y = matrix[5];} else
    {
      const translate = transform.match(/translate(?:3d)?\(([-\d.]+)px,\s*([-\d.]+)px/);
      if (translate) {x = Number(translate[1]);y = Number(translate[2]);}
      sx = sy = Number(transform.match(/scale\(([-\d.]+)\)/)?.[1] || 1);
    }
  }
  const clip = (frame.clipPath || "").match(/inset\(([^)]*)\)/)?.[1] || "0px round 18px";
  const [insets, rounded] = clip.split(/\s+round\s+/),values = insets.trim().split(/\s+/).map((value) => parseFloat(value) || 0);
  const [top = 0, right = top, bottom = top, left = right] = values;
  return { width: (parseFloat(frame.width) || imagePreview.width) * sx, height: (parseFloat(frame.height) || imagePreview.height) * sy,
    x, y, top: top * sy, right: right * sx, bottom: bottom * sy, left: left * sx,
    radius: (parseFloat(rounded) || 0) * sx, opacity: Number(frame.opacity ?? 1) };
}

function springPreviewFrames(from, to, duration) {
  const reduced = imagePreviewReducedMotion(),target = numericPreviewFrame(to);
  const initial = reduced ? { ...target, opacity: Number(from.opacity ?? 0) } : numericPreviewFrame(from);
  cancelImagePreviewAnimation({ keepFlight: Boolean(imagePreview.animation?.studioFlight) });
  const animation = StudioMotion.spring(ui.imagePreviewImage, "flight", target, {
    initial, response: Math.max(.16, Math.min(.36, duration / 1000)), damping: .92, immediate: reduced,
    // Pixel geometry can settle once visually at rest; alpha retains its finer
    // tolerance so returning to detail never waits on an invisible pixel tail.
    precision: { width: .5, height: .5, x: .5, y: .5, top: .5, right: .5, bottom: .5, left: .5, radius: .5, opacity: .001 },
    onUpdate: (value) => {
      const style = ui.imagePreviewImage.style;
      // Actual dimensions are retained throughout the flight; no magnified
      // low-resolution transform layer survives into zoom/pan interaction.
      style.width = `${Math.max(1, value.width)}px`;style.height = `${Math.max(1, value.height)}px`;
      style.transform = `translate(${value.x}px, ${value.y}px)`;
      style.opacity = String(clampPreview(value.opacity, 0, 1));
      style.clipPath = `inset(${Math.max(0, value.top)}px ${Math.max(0, value.right)}px ${Math.max(0, value.bottom)}px ${Math.max(0, value.left)}px round ${Math.max(0, value.radius)}px)`;
    }
  });
  animation.studioFlight = true;
  imagePreview.animation = animation;
  return animation;
}

// Adopt the rendered geometry before cancelling a shared-image flight. The
// bitmap keeps its visible size while pointer input takes ownership immediately.
function beginImagePreviewInteraction() {
  if (!["opening", "closing"].includes(imagePreview.phase) || ui.imagePreviewImage.hidden) return imagePreview.phase === "open";
  const frame = liveImagePreviewFrame();
  let scale = 1,x = imagePreview.x,y = imagePreview.y;
  if (typeof DOMMatrixReadOnly === "function") {
    const matrix = new DOMMatrixReadOnly(frame.transform === "none" ? undefined : frame.transform);
    scale = Math.hypot(matrix.a, matrix.b) || 1;x = matrix.e;y = matrix.f;
  } else {
    const matrix = frame.transform.match(/^matrix\(([^)]+)\)/)?.[1].split(",").map(Number);
    if (matrix) {scale = Math.hypot(matrix[0], matrix[1]) || 1;x = matrix[4];y = matrix[5];} else
    {
      const translate = frame.transform.match(/translate(?:3d)?\(([-\d.]+)px,\s*([-\d.]+)px/);
      if (translate) {x = Number(translate[1]);y = Number(translate[2]);}
      scale = Number(frame.transform.match(/scale\(([-\d.]+)\)/)?.[1] || 1);
    }
  }
  ++imagePreview.token;
  cancelImagePreviewAnimation();
  imagePreview.phase = "open";
  imagePreview.zoom = Math.max(.01, (parseFloat(frame.width) || imagePreview.width) * scale / imagePreview.width);
  imagePreview.x = x;imagePreview.y = y;imagePreview.unclamped = true;
  applyImagePreviewTransform();
  const clipPath = frame.clipPath.replace(/([-\d.]+)px/g, (_, number) => `${Number(number) * scale}px`);
  ui.imagePreviewImage.style.clipPath = clipPath;
  ui.imagePreviewImage.style.opacity = frame.opacity;
  // Only clipping/opacity finish their reveal; the hand owns size and position.
  const reveal = ui.imagePreviewImage.animate([
  { clipPath, opacity: frame.opacity }, { clipPath: "inset(0px round 18px)", opacity: 1 }],
  { duration: imagePreviewReducedMotion() ? 0 : 140, easing: "ease-out", fill: "both" });
  imagePreview.revealAnimation = reveal;
  reveal.finished.then(() => {
    if (imagePreview.revealAnimation !== reveal) return;
    ui.imagePreviewImage.style.opacity = "1";
    ui.imagePreviewImage.style.clipPath = "inset(0px round 18px)";
    imagePreview.revealAnimation = null;reveal.cancel();
  }).catch(() => {});
  animateImagePreviewVeil(1, 140);
  updatePreviewNavigation();
  imagePreview.context?.onReady?.();
  completeImagePreviewSourcePresence();
  return true;
}

function settleImagePreviewBounds() {
  const from = { zoom: imagePreview.zoom, x: imagePreview.x, y: imagePreview.y };
  imagePreview.zoom = clampPreview(imagePreview.zoom, 1, imagePreviewMaxZoom());
  clampImagePreviewOffset();
  const target = { zoom: imagePreview.zoom, x: imagePreview.x, y: imagePreview.y };
  imagePreview.unclamped = false;
  if (imagePreviewReducedMotion() || typeof StudioMotion === "undefined" || !StudioMotion.spring) {
    applyImagePreviewTransform();return;
  }
  if (Object.keys(target).every((key) => Math.abs(target[key] - from[key]) < .001)) {applyImagePreviewTransform();return;}
  Object.assign(imagePreview, from);imagePreview.unclamped = true;
  const spring = StudioMotion.spring(imagePreview, "view", target, {
    initial: from, response: .32, damping: .85,
    onUpdate: (value) => {
      if (imagePreview.phase !== "open") return;
      Object.assign(imagePreview, value);applyImagePreviewTransform();
    },
    onComplete: () => {imagePreview.panSpring = null;imagePreview.unclamped = false;applyImagePreviewTransform();}
  });
  imagePreview.panSpring = spring.active ? spring : null;
}

function clampImagePreviewOffset() {
  const maxX = Math.max(0, (imagePreview.width * imagePreview.zoom - ui.imagePreviewStage.clientWidth) / 2);
  const maxY = Math.max(0, (imagePreview.height * imagePreview.zoom - ui.imagePreviewStage.clientHeight) / 2);
  imagePreview.x = clampPreview(imagePreview.x, -maxX, maxX);
  imagePreview.y = clampPreview(imagePreview.y, -maxY, maxY);
}

function applyImagePreviewTransform() {
  if (!imagePreview.unclamped) clampImagePreviewOffset();
  // Resize the original bitmap's layout box at every zoom level. A permanent
  // scale() layer can keep magnifying the raster created at the initial fit size.
  ui.imagePreviewImage.style.width = imagePreview.width * imagePreview.zoom + "px";
  ui.imagePreviewImage.style.height = imagePreview.height * imagePreview.zoom + "px";
  ui.imagePreviewImage.style.transform = "translate(" + imagePreview.x + "px, " + imagePreview.y + "px)";
  ui.imagePreviewImage.classList.toggle("is-zoomed", imagePreview.zoom > 1);
}

function scheduleImagePreviewRender() {
  if (imagePreview.renderFrame != null) return;
  imagePreview.renderFrame = requestAnimationFrame(() => {
    imagePreview.renderFrame = null;
    if (imagePreview.phase === "open") applyImagePreviewTransform();
  });
}

function imagePreviewMaxZoom() {
  return IMAGE_PREVIEW_MAX_ZOOM / (imagePreview.fit || 1);
}

function layoutImagePreview() {
  const image = ui.imagePreviewImage;
  const stage = ui.imagePreviewStage;
  if (!image.naturalWidth || !image.naturalHeight || !stage.clientWidth || !stage.clientHeight) return;
  const fitted = imagePreview.zoom <= 1.0001;
  const actualScale = (imagePreview.fit || 1) * imagePreview.zoom;
  const style = getComputedStyle(stage);
  const availableWidth = Math.max(1, Math.min(stage.clientWidth * .92, stage.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0)));
  const availableHeight = Math.max(1, Math.min(stage.clientHeight * .92, stage.clientHeight - (parseFloat(style.paddingTop) || 0) - (parseFloat(style.paddingBottom) || 0)));
  const fit = Math.min(1, availableWidth / image.naturalWidth, availableHeight / image.naturalHeight);
  imagePreview.fit = fit;
  imagePreview.width = image.naturalWidth * fit;
  imagePreview.height = image.naturalHeight * fit;
  imagePreview.zoom = fitted ? 1 : clampPreview(actualScale / fit, 1, imagePreviewMaxZoom());
  if (imagePreview.zoom === 1) imagePreview.x = imagePreview.y = 0;
  applyImagePreviewTransform();
}

// Match the visible bitmap for both cropped thumbnails and contained detail images.
function imagePreviewSourceFrame() {
  const sourceElement = imagePreview.context?.sourceElement || ui.detailImage;
  const sourceContainer = imagePreview.context?.sourceContainer || ui.detailDialog;
  if (!sourceElement?.isConnected) return { ...imagePreviewFrame(), opacity: 0 };
  const source = sourceElement.getBoundingClientRect();
  const dialog = sourceContainer.getBoundingClientRect();
  const stage = ui.imagePreviewStage.getBoundingClientRect();
  const fit = getComputedStyle(sourceElement).objectFit === "contain" ? Math.min : Math.max;
  const width = imagePreview.width * (imagePreview.zoom || 1);
  const height = imagePreview.height * (imagePreview.zoom || 1);
  const scale = fit(source.width / width, source.height / height);
  const left = Math.max(source.left, dialog.left + 1, stage.left);
  const top = Math.max(source.top, dialog.top + 1, stage.top);
  const right = Math.min(source.right, dialog.right - 1, stage.right);
  const bottom = Math.min(source.bottom, dialog.bottom - 1, stage.bottom);
  if (!scale || right <= left || bottom <= top) return { ...imagePreviewFrame(), opacity: 0 };
  const cx = source.left + source.width / 2;
  const cy = source.top + source.height / 2;
  const imageLeft = cx - width * scale / 2;
  const imageTop = cy - height * scale / 2;
  const insets = [
  (top - imageTop) / scale,
  (imageLeft + width * scale - right) / scale,
  (imageTop + height * scale - bottom) / scale,
  (left - imageLeft) / scale].
  map((value) => Math.max(0, value) + "px").join(" ");
  let opacity = 1;
  // A source opened during its dialog's entrance has inherited transparency.
  // Preserve that alpha when lifting the bitmap into the independent top layer.
  for (let element = sourceElement; element; element = element.parentElement) {
    const alpha = Number(getComputedStyle(element).opacity);
    if (Number.isFinite(alpha)) opacity *= clampPreview(alpha, 0, 1);
    if (element === sourceContainer) break;
  }
  return {
    width: width + "px", height: height + "px",
    transform: "translate3d(" + (cx - stage.left - stage.width / 2) + "px, " + (cy - stage.top - stage.height / 2) + "px, 0) scale(" + scale + ")",
    clipPath: "inset(" + insets + " round " + 18 / scale + "px)",
    opacity
  };
}

function setImagePreviewSourceHidden(hidden) {
  const source = imagePreview.context?.sourceElement || ui.detailImage;
  source?.classList?.toggle("image-preview-source-hidden", hidden);
}

async function openImagePreview(event, context = null) {
  event?.stopPropagation(); // Preserve any expanded replay/variant controls underneath.
  if (imagePreview.phase === "closed") clearPreviewCache();
  if (context && imagePreview.phase === "closed") imagePreview.context = context;
  if (!imagePreview.context && imagePreview.phase === "closed") imagePreview.context =

  { kind: "random", historyView: detailContext?.historyView, sourceElement: ui.detailImage, sourceContainer: ui.detailDialog, returnFocus: ui.detailImage, isValid: () => ui.detailDialog.open && (typeof historyDetailVisible !== "function" || historyDetailVisible()) };
  const record = currentPreviewRecord();
  const source = imagePreview.context?.src || (record ? `/api/images/${record.id}` : "");
  if (!record || !source || !ui.detailDialog.open || typeof historyDetailVisible === "function" && !historyDetailVisible()) return;
  // Reverse-reopen: if the exit animation is still in flight, bounce back from
  // the live frame instead of ignoring the click (previously felt like a hang).
  if (imagePreview.phase === "closing") {
    ++imagePreview.token; // Cancel the pending finishImagePreviewClose callback.
    if (!ui.imagePreviewDialog.open) {
      // The dialog got closed before we could intercept; restart cleanly.
      imagePreview.phase = "closed";
      return openImagePreview(event);
    }
    imagePreview.phase = "opening";
    const from = liveImagePreviewFrame();
    imagePreview.zoom = 1;
    imagePreview.x = imagePreview.y = 0;
    applyImagePreviewTransform();
    animateImagePreviewVeil(1, previewMotion("--motion-medium", 240));
    animateImagePreview(from, imagePreviewFrame(), previewMotion("--motion-medium", 240), () => {
      if (imagePreview.phase === "opening") {
        imagePreview.phase = "open";
        updatePreviewNavigation();
        imagePreview.context?.onReady?.();
        completeImagePreviewSourcePresence();
      }
    });
    return;
  }
  if (imagePreview.phase !== "closed") return;

  const token = ++imagePreview.token;
  imagePreview.navigationCursor = null;
  imagePreview.committedSrc = "";
  imagePreview.unclamped = false;
  imagePreview.veilCovered = false;
  imagePreview.phase = "loading";
  imagePreview.zoom = 1;
  imagePreview.x = imagePreview.y = 0;
  imagePreview.tap = null;
  // The browser's HTTP cache can reuse the source; decoded preview resources
  // belong to the open viewer and are released when it closes.
  ui.imagePreviewImage.hidden = true;
  setImagePreviewSourceHidden(false);
  ui.imagePreviewVeil.getAnimations().forEach((animation) => animation.cancel());
  ui.imagePreviewVeil.style.opacity = "0";
  ui.imagePreviewStatus.hidden = false;
  ui.imagePreviewStatus.textContent = "正在加载原图…";
  if (source) ui.imagePreviewImage.src = source;
  ui.imagePreviewImage.alt = `生成结果全尺寸预览：${record?.filename || "当前图片"}`;
  ui.imagePreviewTitle.textContent = record?.filename || "全尺寸图片预览";
  ui.imagePreviewDialog.dataset.context = imagePreview.context.kind;
  document.documentElement.classList.add("image-preview-open");
  ui.imagePreviewDialog.showModal();
  imagePreview.context?.sourceElement?.setAttribute("aria-expanded", "true");
  ui.closeImagePreview.focus({ preventScroll: true });
  updatePreviewNavigation();
  announcePreview(`正在打开${previewPositionLabel(record)}`);
  imagePreview.context?.onOpen?.();
  try {
    await ui.imagePreviewImage.decode();
    if (token !== imagePreview.token) return;
    if (imagePreview.context?.isValid?.() === false) return finishImagePreviewClose(false);
    // If a close was requested while decoding, keep the image hidden and let
    // the in-flight close animation finish instead of starting a new entry.
    if (imagePreview.phase === "closing") return;
    // The detail remains visible and free to finish entering while decoding.
    // Freeze only when the decoded bitmap is ready to take over its geometry.
    freezeImagePreviewSourcePresence();
    ui.imagePreviewStatus.hidden = true;
    ui.imagePreviewImage.hidden = false;
    imagePreview.committedSrc = source;
    ui.imagePreviewImage.style.opacity = "1";
    ui.imagePreviewImage.style.clipPath = "inset(0px round 18px)";
    layoutImagePreview();
    imagePreview.phase = "opening";
    animateImagePreview(imagePreviewSourceFrame(), imagePreviewFrame(), previewMotion("--motion-slow", 360), () => {
      if (imagePreview.phase === "opening") {
        imagePreview.phase = "open";
        updatePreviewNavigation();
        announcePreview(previewPositionLabel(record));
        preloadPreviewNeighbors();
        imagePreview.context?.onReady?.();
        completeImagePreviewSourcePresence();
      }
    });
    // The animation commits its initial frame synchronously. Transfer visibility
    // in the same task so there is never a frame with both bitmaps hidden.
    setImagePreviewSourceHidden(true);
    animateImagePreviewVeil(1, previewMotion("--motion-slow", 360));
  } catch {
    if (token !== imagePreview.token) return;
    imagePreview.phase = "error";
    setImagePreviewSourceHidden(false);
    ui.imagePreviewImage.hidden = true;
    ui.imagePreviewStatus.hidden = false;
    completeImagePreviewSourcePresence(true);
    ui.imagePreviewStatus.textContent = "图片加载失败，请关闭后重试。";
    imagePreview.context?.onError?.(record);
  }
}

function finishImagePreviewClose(restoreFocus = true) {
  const context = imagePreview.context;
  // Return ownership before releasing the overlay bitmap or its decoded source.
  setImagePreviewSourceHidden(false);
  if (imagePreview.sourcePresence) completeImagePreviewSourcePresence(true);
  clearPreviewCache();
  imagePreview.pendingReturnFocus = restoreFocus ? context : null;
  ++imagePreview.token;
  imagePreview.phase = "closed";
  cancelImagePreviewAnimation();
  imagePreview.pointers.clear();
  imagePreview.gesture = imagePreview.tap = imagePreview.backdropPointer = imagePreview.swipe = null;
  imagePreview.navigationRequest++;
  imagePreview.navigationCursor = null;
  imagePreview.unclamped = false;
  imagePreview.committedSrc = "";
  imagePreview.veilAnimation = null;
  imagePreview.veilCovered = false;
  ui.imagePreviewVeil.getAnimations().forEach((animation) => animation.cancel());
  ui.imagePreviewImage.classList.remove("is-interacting", "is-zoomed");
  ui.imagePreviewImage.hidden = true;
  ui.imagePreviewImage.removeAttribute("src");
  ui.imagePreviewStatus.hidden = true;
  ui.imagePreviewAnnouncement.textContent = "";
  ui.imagePreviewDialog.classList.remove("has-navigation", "is-navigating");
  document.documentElement.classList.remove("image-preview-open");
  context?.sourceElement?.setAttribute("aria-expanded", "false");
  if (ui.imagePreviewDialog.open) ui.imagePreviewDialog.close();
  imagePreview.context = null;
  delete ui.imagePreviewDialog.dataset.context;
  context?.onClose?.();
}

function restoreImagePreviewCloseFocus() {
  const context = imagePreview.pendingReturnFocus;
  if (!context?.returnFocus) return;
  // Run after the native close event and a rendered frame: the closed modal
  // can retain focus until it has actually left the browser's top layer.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (imagePreview.pendingReturnFocus !== context) return;
    imagePreview.pendingReturnFocus = null;
    if (imagePreview.phase === "closed" && context.isValid?.() !== false && context.returnFocus.isConnected)
    context.returnFocus.focus({ preventScroll: true });
  }));
}

function closeImagePreview({ immediate = false, restoreFocus = true } = {}) {
  if (imagePreview.phase === "closed") return;
  if (immediate) return finishImagePreviewClose(restoreFocus);
  if (imagePreview.phase === "closing") return;
  const live = typeof liveImagePreviewFrame === "function" ? liveImagePreviewFrame() : null;
  if (imagePreview.phase === "open") cancelImagePreviewAnimation();
  ++imagePreview.token; // Invalidate an in-flight decode before starting the exit.
  ++imagePreview.navigationRequest;
  imagePreview.navigationCursor = null;
  imagePreview.pointers?.clear();
  imagePreview.gesture = imagePreview.tap = imagePreview.swipe = null;
  ui.imagePreviewImage.classList?.remove("is-interacting");
  const hasImage = !ui.imagePreviewImage.hidden;
  imagePreview.phase = "closing";
  if (hasImage) {
    animateImagePreviewVeil(0, previewMotion("--motion-medium", 240));
    animateImagePreview(live || liveImagePreviewFrame(), imagePreviewSourceFrame(), previewMotion("--motion-medium", 240), () => finishImagePreviewClose(restoreFocus));
  } else {
    // Loading and failed opens never obscured the source or raised the veil.
    finishImagePreviewClose(restoreFocus);
  }
}

// Zoom uses real layout dimensions, including every frame of a double-click.
// State is the currently displayed tween position so an interruption never jumps.
function setImagePreviewZoom(nextZoom, focalPoint = null, smooth = false) {
  if (["opening", "closing"].includes(imagePreview.phase)) beginImagePreviewInteraction();
  if (imagePreview.phase !== "open" || !Number.isFinite(nextZoom)) return;
  if (imagePreview.zoomFrame != null || imagePreview.animation || !smooth && imagePreview.panSpring) cancelImagePreviewAnimation({ keepReveal: true });
  const from = { zoom: imagePreview.zoom, x: imagePreview.x, y: imagePreview.y };
  imagePreview.unclamped = false;
  const previousZoom = imagePreview.zoom;
  imagePreview.zoom = clampPreview(nextZoom, 1, imagePreviewMaxZoom());
  if (imagePreview.zoom === 1) {
    imagePreview.x = imagePreview.y = 0;
  } else if (focalPoint) {
    const stage = ui.imagePreviewStage.getBoundingClientRect();
    const ratio = imagePreview.zoom / previousZoom;
    imagePreview.x = ((focalPoint.clientX ?? focalPoint.x) - stage.left - stage.width / 2) * (1 - ratio) + imagePreview.x * ratio;
    imagePreview.y = ((focalPoint.clientY ?? focalPoint.y) - stage.top - stage.height / 2) * (1 - ratio) + imagePreview.y * ratio;
  }
  clampImagePreviewOffset();
  if (!smooth || imagePreviewReducedMotion()) {
    scheduleImagePreviewRender();
    return;
  }
  const to = { zoom: imagePreview.zoom, x: imagePreview.x, y: imagePreview.y };
  Object.assign(imagePreview, from);
  if (typeof StudioMotion !== "undefined" && StudioMotion.spring) {
    const spring = StudioMotion.spring(imagePreview, "view", to, {
      initial: from, response: .3, damping: 1,
      onUpdate: (value) => {
        if (imagePreview.phase !== "open") return;
        Object.assign(imagePreview, value);applyImagePreviewTransform();
      },
      onComplete: () => {imagePreview.panSpring = null;}
    });
    imagePreview.panSpring = spring.active ? spring : null;
    return;
  }
  const start = performance.now();
  const step = (now) => {
    imagePreview.zoomFrame = null;
    if (imagePreview.phase !== "open") return;
    const progress = clampPreview((now - start) / 220, 0, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    for (const key of ["zoom", "x", "y"]) imagePreview[key] = from[key] + (to[key] - from[key]) * eased;
    applyImagePreviewTransform();
    if (progress < 1) imagePreview.zoomFrame = requestAnimationFrame(step);
  };
  imagePreview.zoomFrame = requestAnimationFrame(step);
}

function toggleImagePreviewZoom(focalPoint) {
  setImagePreviewZoom(imagePreview.zoom < 1.01 ? 1 / (imagePreview.fit || 1) : 1, focalPoint, true);
}

function resetImagePreviewTransform() {
  setImagePreviewZoom(1, null, true);
}

function beginImagePreviewGesture() {
  const points = [...imagePreview.pointers.values()];
  imagePreview.gesture = {
    points: points.map((point) => ({ ...point })),
    zoom: imagePreview.zoom, x: imagePreview.x, y: imagePreview.y
  };
}

function imagePreviewPointerDown(event) {
  if (event.pointerType === "mouse" && event.button !== 0) return;
  const adopted = ["opening", "closing"].includes(imagePreview.phase) && beginImagePreviewInteraction();
  if (imagePreview.phase !== "open" || event.pointerType === "mouse" && event.button !== 0) return;
  // The tween already stores its displayed dimensions and pan position.
  if (!adopted) cancelImagePreviewAnimation({ keepReveal: true });
  applyImagePreviewTransform();
  ui.imagePreviewImage.setPointerCapture(event.pointerId);
  imagePreview.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY, time: performance.now(), moved: false });
  imagePreview.swipe = event.pointerType === "mouse" ? null : { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY };
  if (imagePreview.pointers.size > 1) {
    imagePreview.tap = null;
    imagePreview.pointers.forEach((point) => {point.moved = true;});
  }
  ui.imagePreviewImage.classList.add("is-interacting");
  beginImagePreviewGesture();
  event.preventDefault();
}

function imagePreviewPointerMove(event) {
  const point = imagePreview.pointers.get(event.pointerId);
  if (!point || imagePreview.phase !== "open") return;
  Object.assign(point, { x: event.clientX, y: event.clientY });
  point.moved ||= Math.hypot(point.x - point.startX, point.y - point.startY) > 6;
  const points = [...imagePreview.pointers.values()];
  const gesture = imagePreview.gesture;
  if (points.length > 1) {
    const [a, b] = points;
    const [startA, startB] = gesture.points;
    const startDistance = Math.max(1, Math.hypot(startA.x - startB.x, startA.y - startB.y));
    imagePreview.zoom = clampPreview(gesture.zoom * Math.hypot(a.x - b.x, a.y - b.y) / startDistance, Math.min(1, gesture.zoom), imagePreviewMaxZoom());
    const stage = ui.imagePreviewStage.getBoundingClientRect();
    const ratio = imagePreview.zoom / gesture.zoom;
    imagePreview.x = (a.x + b.x) / 2 - stage.left - stage.width / 2 - ((startA.x + startB.x) / 2 - stage.left - stage.width / 2 - gesture.x) * ratio;
    imagePreview.y = (a.y + b.y) / 2 - stage.top - stage.height / 2 - ((startA.y + startB.y) / 2 - stage.top - stage.height / 2 - gesture.y) * ratio;
  } else if (point.moved) {
    imagePreview.x = gesture.x + point.x - gesture.points[0].x;
    imagePreview.y = gesture.y + point.y - gesture.points[0].y;
  }
  const maxX = Math.max(0, (imagePreview.width * imagePreview.zoom - ui.imagePreviewStage.clientWidth) / 2);
  const maxY = Math.max(0, (imagePreview.height * imagePreview.zoom - ui.imagePreviewStage.clientHeight) / 2);
  const resist = (value, bound) => {
    const constrained = clampPreview(value, -bound, bound),excess = value - constrained;
    return constrained + Math.sign(excess) * Math.min(36, Math.abs(excess) * .18);
  };
  imagePreview.x = resist(imagePreview.x, Math.max(maxX, Math.abs(gesture.x)));
  imagePreview.y = resist(imagePreview.y, Math.max(maxY, Math.abs(gesture.y)));
  imagePreview.unclamped = true;
  scheduleImagePreviewRender();
  event.preventDefault();
}

function imagePreviewPointerUp(event) {
  const point = imagePreview.pointers.get(event.pointerId);
  if (!point) return;
  const deltaX = point.x - point.startX;
  const deltaY = point.y - point.startY;
  const isSwipe = event.type === "pointerup" &&
  event.pointerType !== "mouse" &&
  imagePreview.zoom <= 1.01 &&
  Math.abs(deltaX) >= 56 &&
  Math.abs(deltaX) > Math.abs(deltaY) * 1.25;
  imagePreview.pointers.delete(event.pointerId);
  if (isSwipe && imagePreview.pointers.size === 0) {
    imagePreview.tap = null;
    imagePreview.gesture = null;
    imagePreview.swipe = null;
    ui.imagePreviewImage.classList.remove("is-interacting");
    settleImagePreviewBounds();
    navigatePreview(deltaX < 0 ? 1 : -1);
    return;
  }
  if (event.type === "pointerup" && event.pointerType === "touch" && !point.moved && performance.now() - point.time < 300) {
    const previous = imagePreview.tap;
    if (previous && performance.now() - previous.time < 300 && Math.hypot(point.x - previous.x, point.y - previous.y) < 30) {
      toggleImagePreviewZoom(point);
      imagePreview.tap = null;
    } else imagePreview.tap = { x: point.x, y: point.y, time: performance.now() };
  }
  if (imagePreview.pointers.size) beginImagePreviewGesture();else
  {
    imagePreview.gesture = null;
    imagePreview.swipe = null;
    ui.imagePreviewImage.classList.remove("is-interacting");
    settleImagePreviewBounds();
  }
}

function imagePreviewWheel(event) {
  event.preventDefault();
  if (["opening", "closing"].includes(imagePreview.phase)) beginImagePreviewInteraction();
  const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? ui.imagePreviewStage.clientHeight : 1);
  setImagePreviewZoom(imagePreview.zoom * Math.exp(-clampPreview(delta, -500, 500) * .0015), { x: event.clientX, y: event.clientY }, imagePreview.zoom < 1);
}

function collapseRegenerationDrawer(drawer, immediately = false) {
  drawer.regenerationOpenToken = null;
  setDetailActionPanelVisible(drawer, false, immediately);
}

function closeRegenerationDrawers(restoreFocus = true, immediately = false) {
  const hadOpen = !ui.replayDrawer.hidden || !ui.variantDrawer.hidden;
  collapseRegenerationDrawer(ui.replayDrawer, immediately);
  collapseRegenerationDrawer(ui.variantDrawer, immediately);
  ui.reproduceImage.setAttribute("aria-expanded", "false");
  ui.variantButton.setAttribute("aria-expanded", "false");
  ui.detailDialog.classList.remove("is-regenerating");
  if (restoreFocus && hadOpen && regenerationTrigger && document.contains(regenerationTrigger))
  regenerationTrigger.focus({ preventScroll: true });
  regenerationTrigger = null;
}

function regenerationReasons(mode) {
  return regenerationOptions?.modes?.[mode]?.reasons || [];
}

function renderRegenerationOptions() {
  if (!regenerationOptions || !selectedRecord) return;
  const issues = regenerationOptions.modes?.replay?.resourceIssues || regenerationOptions.resourceIssues || [];
  const issueText = issues.length ?
  `<strong>无法生成</strong><span>${issues.map((item) => `${escapeHtml(item.label)}：${escapeHtml(item.name)}`).join("<br>")}</span>` :
  `<strong>资源检查通过</strong><span>模型、已启用 LoRA、高清模型与采样资源均可用。</span>`;
  ui.replayStatus.dataset.state = issues.length ? "error" : "ok";
  ui.replayStatus.innerHTML = issueText;
  ui.variantStatus.dataset.state = issues.length ? "error" : "ok";
  ui.variantStatus.innerHTML = issueText;
  ui.replaySeedSummary.innerHTML = `<span><b>图像种子</b>${escapeHtml(selectedRecord.sample_seed)}</span><span><b>提示词种子</b>${escapeHtml(selectedRecord.prompt_seed)}</span>`;
  ui.confirmReplay.disabled = !regenerationOptions.modes?.replay?.available;
  for (const input of ui.redrawSections.querySelectorAll("input")) {
    const section = regenerationOptions.sections?.[input.value];
    input.disabled = !section?.canRedraw;
    input.checked = false;
    input.closest("label").hidden = !section?.canRedraw;
  }
  updateVariantDrawer();
}

async function loadRegenerationOptions() {
  if (!selectedRecord) return null;
  if (regenerationOptions) return regenerationOptions;
  const requestId = ++regenerationRequest;
  ui.replayStatus.dataset.state = "pending";
  ui.replayStatus.textContent = "正在检查历史记录与本地资源…";
  ui.variantStatus.dataset.state = "pending";
  ui.variantStatus.textContent = "正在检查历史记录与本地资源…";
  try {
    const value = await request(`/api/history/${selectedRecord.id}/regeneration-options`);
    if (requestId !== regenerationRequest || !selectedRecord) return null;
    regenerationOptions = value;
    renderRegenerationOptions();
    return value;
  } catch (error) {
    if (requestId !== regenerationRequest) return null;
    for (const status of [ui.replayStatus, ui.variantStatus]) {
      status.dataset.state = "error";
      status.textContent = error.message;
    }
    return null;
  }
}

async function openRegenerationDrawer(kind, trigger) {
  const drawer = kind === "replay" ? ui.replayDrawer : ui.variantDrawer;
  const wasOpen = !drawer.hidden && !drawer.classList.contains("closing");
  closeRegenerationDrawers(false);
  if (wasOpen) {
    trigger.focus({ preventScroll: true });
    return;
  }
  if (typeof closeDetailActionPanels === "function") closeDetailActionPanels();
  regenerationTrigger = trigger;
  const token = drawer.regenerationOpenToken = {};
  drawer.regenerationCloseToken = null;
  drawer.classList.remove("closing");
  setDetailActionPanelVisible(drawer, true);
  trigger.setAttribute("aria-expanded", "true");
  const record = selectedRecord;
  await loadRegenerationOptions();
  if (selectedRecord !== record || drawer.regenerationOpenToken !== token || drawer.hidden || drawer.classList.contains("closing")) return;
  if (typeof DetailActionFold !== "undefined") DetailActionFold.refresh();
}

function selectedRegenerationMode() {
  return ui.variantModes.querySelector('input[name="regenerationMode"]:checked')?.value || "prompt_variant";
}

function selectedRedrawSections() {
  return [...ui.redrawSections.querySelectorAll("input:checked")].map((input) => input.value);
}

function setRedrawSectionsVisible(visible) {
  const el = ui.redrawSections;
  el.hidden = !visible;
  if (typeof DetailActionFold !== "undefined") DetailActionFold.refresh();
}

function updateVariantDrawer() {
  const mode = selectedRegenerationMode();
  const sections = selectedRedrawSections();
  setRedrawSectionsVisible(mode === "content_redraw");
  const labels = {
    prompt_variant: "生成同提示词变体",
    content_redraw: "重新抽取所选内容",
    settings_reroll: "按此配置再抽"
  };
  ui.confirmVariant.textContent = labels[mode];
  const available = Boolean(regenerationOptions?.modes?.[mode]?.available);
  const reasons = [...regenerationReasons(mode)];
  if (mode === "content_redraw") {
    for (const [section, value] of Object.entries(regenerationOptions?.sections || {})) {
      if (value.random && !sections.includes(section) && value.canPreserve === false)
      reasons.push(value.preserveReason || `请同时重抽 ${section}：缺少可靠的历史快照`);
    }
  }
  ui.confirmVariant.disabled = !available || reasons.length > 0 || mode === "content_redraw" && !sections.length;
  if (reasons.length && !(regenerationOptions?.resourceIssues || []).length) {
    ui.variantStatus.dataset.state = "error";
    ui.variantStatus.innerHTML = `<strong>当前方式不可用</strong><span>${reasons.map(escapeHtml).join("<br>")}</span>`;
  } else if (!(regenerationOptions?.resourceIssues || []).length) {
    ui.variantStatus.dataset.state = "ok";
    ui.variantStatus.textContent = "资源检查通过，所选方式可用。";
  }
}

async function submitRegeneration(mode, count = 1, sections = []) {
  if (!selectedRecord || submitInFlight) return false;
  submitInFlight = true;
  ui.confirmReplay.disabled = true;
  ui.confirmVariant.disabled = true;
  try {
    const body = mode === "replay" ? { mode } : { mode, count };
    if (mode === "content_redraw") body.sections = sections;
    const state = await request(`/api/history/${selectedRecord.id}/regenerate`, {
      method: "POST",
      body: JSON.stringify(body)
    });
    toast(state.status === "queued" ? `已加入队列,第 ${state.position} 位` : "批次已开始");
    closeRegenerationDrawers(false);
    suppressDetailReturnFocus();
    motionDialog(ui.detailDialog, false);
    return true;
  } catch (error) {
    toast(error.message);
    regenerationOptions = null;
    await loadRegenerationOptions();
    return false;
  } finally {
    submitInFlight = false;
    try {
      renderBatchPayload(await request("/api/batches/current"));
    } catch {
      updateGenerationAvailability();
    }
    if (regenerationOptions) renderRegenerationOptions();
  }
}

function selectionHas(section, id) {
  const picker = characterPickerSession();
  if (picker) return picker.alreadyBound.has(id) || picker.selection.ids.includes(id);
  const selection = draft.pools[section];
  return selection.mode === "all" ? !selection.excluded_ids.includes(id) : selection.ids.includes(id);
}
function toggleSelection(section, id, checked) {
  if (section !== activeSection || poolResultContext !== poolSelectionContext()) return;
  const picker = characterPickerSession();
  if (picker) {
    try {picker.toggle(id, checked);} catch (error) {toast(error.message);renderPoolItems();return;}
    return poolSelectionChanged();
  }
  const selection = draft.pools[section];
  if (selection.mode === "all")
  selection.excluded_ids = checked ?
  selection.excluded_ids.filter((value) => value !== id) :
  [...new Set([...selection.excluded_ids, id])];else
  if (checked) selection.ids = [...new Set([...selection.ids, id])];else
  selection.ids = selection.ids.filter((value) => value !== id);
  return poolSelectionChanged();
}

// A selection operation belongs to one view and draft, including same-model
// history imports and workbench switches. Older requests must never write back.
function invalidatePoolContext() {
  poolContextRevision++;
  poolRequest++;
  poolSelectionOperation = null;
  if (ui.selectFilteredPool) {
    ui.selectFilteredPool.disabled = false;
    ui.selectFilteredPool.textContent = "选择全部筛选结果";
  }

}
function poolSelectionContext() {
  return JSON.stringify({ session: characterPickerSession()?.id || "generation", section: activeSection, query: poolQueryBody(), selectedOnly: currentView().selectedOnly, selection: poolBrowserSelection() });
}
function poolSelectionQuery() {
  const selection = clone(poolBrowserSelection());
  return { ...poolQueryBody(), selection_state: selection, ...(currentView().selectedOnly ? { selection } : {}) };
}
function updatePoolSelectionControls() {
  const busy = Boolean(poolSelectionOperation);
  const current = poolResultContext === poolSelectionContext();
  ui.poolGrid.inert = !current;
  ui.selectPage.disabled = !current || !poolItems.length || busy;
  ui.selectFilteredPool.disabled = busy || current && poolQueryCount === 0;
  ui.selectFilteredPool.textContent = busy ? "正在选择…" : "选择全部筛选结果";
  const picker = characterPickerSession();
  const all = poolBrowserSelection()?.mode === "all";
  ui.poolModeNote.hidden = !all;
  ui.poolModeNote.textContent = all ? "整个词库参与随机抽取，新增词条也会自动参与；手动取消的词条除外。" : "";
  ui.poolSelectionLabel.textContent = picker ? `已绑定 ${picker.alreadyBound.size} 位 · 待新增 ${poolBrowserCount()} 位` : `${all ? "整个词库" : "已选择"} · ${selectedCount(activeSection)} 项`;
  ui.poolMeta.textContent = `${poolQueryCount ?? "—"} 项匹配 · ${poolBrowserCount()} 项已选${favoritesAvailable ? "" : " · 收藏离线"}`;
  if (picker) {
    ui.poolModeNote.hidden = false;
    ui.poolModeNote.textContent = "仅追加到所选人物，保留原有绑定；这里不会更改生成随机池。";
    ui.confirmPool.textContent = picker.selectionMode === "single" ? "确认选择这位人物" : `确认选择 ${poolBrowserCount()} 位人物`;
    if (picker.selectionMode === "single") {
      ui.poolSelectionLabel.textContent = `选择 1 位人物 · 已选 ${poolBrowserCount()} 位`;
      ui.selectPage.disabled = true;ui.selectFilteredPool.disabled = true;
    }
    ui.confirmPool.disabled = busy || !current || !picker.selection.ids.length;
  }
}
function poolSelectionChanged() {
  invalidatePoolContext();
  renderPoolItems();
  if (characterPickerSession()) return loadPool();
  renderDimensions();
  updatePoolOverview();
  updatePoseConflict();
  schedulePersist();
  return loadPool();
}
async function selectFilteredPool() {
  if (characterPickerSession()?.selectionMode === "single") return;
  if (poolSelectionOperation || poolBrowserProfile() !== "anima") return;
  clearTimeout(ui.poolSearch._timer);
  invalidatePoolContext();
  const operation = { revision: poolContextRevision, context: poolSelectionContext(), section: activeSection };
  const body = poolSelectionQuery();
  poolSelectionOperation = operation;
  updatePoolSelectionControls();
  const current = () => poolSelectionOperation === operation && operation.revision === poolContextRevision && operation.context === poolSelectionContext();
  try {
    const data = await request(`/api/pools/${operation.section}/ids`, { method: "POST", body: JSON.stringify(body) });
    if (!current()) return;
    const ids = new Set(data.ids || []);
    if (!ids.size) {toast("没有匹配的词条，选择未改变");return;}
    const picker = characterPickerSession();
    if (picker) {picker.append([...ids]);await poolSelectionChanged();return;}
    const selection = draft.pools[operation.section];
    if (selection.mode === "all") selection.excluded_ids = selection.excluded_ids.filter((id) => !ids.has(id));else
    selection.ids = [...new Set([...selection.ids, ...ids])];
    await poolSelectionChanged();
  } catch (error) {
    if (current()) toast(`选择筛选结果失败：${error.message}`);
  } finally {
    if (poolSelectionOperation === operation) {
      poolSelectionOperation = null;
      updatePoolSelectionControls();
    }
  }
}
function setPoolMoreSelection(open, { immediate = false, restoreFocus = false } = {}) {
  const menu = ui.poolMoreSelection,summary = menu.querySelector?.("summary"),panel = menu.querySelector?.(".pool-more-content");
  summary?.setAttribute("aria-expanded", String(open));
  if (!panel || typeof StudioMotion === "undefined") {menu.open = open;if (panel) {panel.hidden = !open;panel.inert = !open;}} else
  {
    if (!menu.open) panel.hidden = true;
    menu.open = true;
    StudioMotion.presence(panel, open, {
      from: { opacity: 0, y: -6, scale: .985 }, response: .3, damping: 1, immediate,
      onComplete: () => {menu.open = open;}
    });
  }
  if (restoreFocus) summary?.focus({ preventScroll: true });
}

function useEntirePool() {
  if (characterPickerSession()) return;
  setPoolMoreSelection(false, { immediate: true, restoreFocus: true });
  draft.pools[activeSection] = { mode: "all", ids: [], excluded_ids: [] };
  return poolSelectionChanged();
}
function clearPoolSelection() {
  const picker = characterPickerSession();
  if (picker) {picker.clear();return poolSelectionChanged();}
  draft.pools[activeSection] = { mode: "include", ids: [], excluded_ids: [] };
  return poolSelectionChanged();
}

function favoriteKey(item) {
  return String(item.favorite_key || "");
}
function favoriteMap() {
  const map = new Map();
  for (const item of favoritesData.items || []) {
    const key = activeSection === "character" ? String(item.name || item.id || "") : String(item.id || item.name || "");
    if (key) map.set(key, item);
  }
  return map;
}
function isFavorite(item) {
  return Boolean(favoriteMap().get(favoriteKey(item))?.groupIds?.length);
}

let favoritesRequest = 0;
async function loadFavorites(section = activeSection, { throwOnError = false } = {}) {
  const requestId = ++favoritesRequest;
  const profile = poolBrowserProfile();
  if (profile !== "anima") {favoritesData = { groups: [], items: [], favorite_keys: [] };favoritesAvailable = false;return;}
  try {
    const payload = await request(`/api/favorites/${section}?model_profile=${profile}`);
    if (requestId !== favoritesRequest || section !== activeSection || profile !== poolBrowserProfile()) return;
    favoritesData = payload;
    favoritesAvailable = true;
  } catch (error) {
    if (requestId !== favoritesRequest || section !== activeSection || profile !== poolBrowserProfile()) return;
    favoritesData = { groups: [], items: [], favorite_keys: [] };
    favoritesAvailable = false;
    if (currentView().filters.collection) currentView().filters.collection = "";
    if (currentView().sort === "favorite-first") currentView().sort = "";
    toast(`收藏不可用：${error.message}`);
    if (throwOnError) throw error;
  }
  if (!characterPickerSession()) updateFavoriteSelectionButton();
}
async function loadCustomGroups(section = activeSection, { throwOnError = false } = {}) {
  const requestedProfile = poolBrowserProfile();
  const requestId = ++customGroupsRequest;
  try {
    const payload = await request(`/api/custom-groups/${section}?model_profile=${encodeURIComponent(requestedProfile)}`);
    if (requestId !== customGroupsRequest || requestedProfile !== poolBrowserProfile() || section !== activeSection) return;
    customGroups = payload.groups || [];
    const picker = characterPickerSession();
    if (picker ? !picker.customInitialized : !customTreeInitialized.has(section)) {
      const collapsed = new Set(customGroups.filter((group) => group.kind !== "group").map((group) => group.id));
      if (picker) {picker.collapsedCustom = collapsed;picker.customInitialized = true;} else
      {collapsedCustomGroups[section] = collapsed;customTreeInitialized.add(section);}
      persistCustomTree();
    }
    if (currentView().filters.customGroup && !customGroup(currentView().filters.customGroup)) {
      currentView().filters.customGroup = "";
    }
    const activeGroup = customGroup(currentView().filters.customGroup);
    let ancestor = activeGroup?.parentId ? customGroup(activeGroup.parentId) : null;
    while (ancestor) {
      poolCollapsedGroups("custom").delete(ancestor.id);
      ancestor = ancestor.parentId ? customGroup(ancestor.parentId) : null;
    }
  } catch (error) {
    if (requestId !== customGroupsRequest || requestedProfile !== poolBrowserProfile() || section !== activeSection) return;
    customGroups = [];
    toast(`自定义分组不可用：${error.message}`);
    if (throwOnError) throw error;
  }
}

let drawerTrigger = null;
let characterPickerView = null;
function openCharacterPicker(options = {}) {
  if (typeof PoolBrowser === "undefined") return Promise.reject(new Error("人物浏览器尚未就绪"));
  if (characterPickerSession()) return characterPickerSession().promise;
  // Lease the real browser nodes. Only the visual session is suspended; the
  // generation draft is never swapped or used as the picker's selection store.
  const saved = { activeSection, poolPage, poolPages, poolItems, poolFacets, poolQueryCount,
    favoritesData, favoritesAvailable, customGroups, drawerTrigger,
    scrollTop: ui.poolGrid.scrollTop, sidebarScroll: ui.poolSidebar.scrollTop,
    sidebarOpen: ui.poolSidebar.classList.contains("mobile-open"), drawerOpen: ui.poolDrawer.classList.contains("open") };
  const session = PoolBrowser.createSession({ ...options,
    view: characterPickerView || { ...defaultView(), language: poolViews.character?.language || "zh" } });
  session.generationSection = saved.activeSection;
  const opener = document.activeElement;
  const dialog = document.createElement("dialog");
  dialog.id = "characterPickerDialog";
  dialog.className = "pool-character-picker glass";
  dialog.dataset.selectionMode = session.selectionMode;
  dialog.setAttribute("aria-labelledby", "poolTitle");
  dialog.setAttribute("aria-describedby", "poolModeNote");
  const nodes = [...ui.poolDrawer.childNodes];
  const footer = ui.confirmPool.parentElement;
  const cancel = document.createElement("button");
  cancel.type = "button";cancel.className = "button ghost";cancel.textContent = "取消";
  const actions = document.createElement("div");actions.className = "pool-picker-footer-actions";
  actions.append(cancel, ui.confirmPool);footer.append(actions);
  const selectedLabel = ui.selectedOnly.parentElement.querySelector("span");
  const selectedLabelText = selectedLabel?.textContent;
  const hostProfile = currentProfile();
  clearTimeout(ui.poolSearch._timer);
  invalidatePoolContext();++favoritesRequest;++customGroupsRequest;
  PoolBrowser.activate(session);
  activeSection = "character";poolPage = 1;poolPages = 1;poolItems = [];poolFacets = {};
  poolQueryCount = null;poolResultContext = "";
  favoritesData = { groups: [], items: [], favorite_keys: [] };favoritesAvailable = false;customGroups = [];
  ui.poolDrawer.classList.remove("open");
  ui.poolDrawer.inert = true;
  ui.poolDrawer.setAttribute("aria-hidden", "true");
  if (saved.drawerOpen) animatePoolSurface(ui.poolDrawer, ui.drawerBackdrop, false);
  nodes.forEach((node) => dialog.append(node));
  document.body.append(dialog);
  ui.poolTitle.textContent = "选择绑定人物";ui.poolKicker.textContent = "CHARACTER CONNECTION";
  ui.poolSearch.value = session.view.query;ui.poolSort.value = session.view.sort;
  ui.poolLanguage.value = session.view.language;ui.selectedOnly.checked = session.view.selectedOnly;
  if (selectedLabel) selectedLabel.textContent = "仅看待新增";
  ui.poolGrid.replaceChildren();ui.poolSidebar.replaceChildren();ui.poseConflict.hidden = true;
  ui.poolPageLabel.textContent = "1 / 1";
  setPoolSidebarOpen(false, { immediate: true });updatePoolSelectionControls();
  let resolveResult;
  session.promise = new Promise((resolve) => {resolveResult = resolve;});
  let finishing = false;
  session.finish = async (result) => {
    if (finishing) return;finishing = true;
    session.closed = true;
    clearTimeout(ui.poolSearch._timer);invalidatePoolContext();++favoritesRequest;++customGroupsRequest;
    ui.confirmPool.disabled = true;
    characterPickerView = clone(session.view);
    ui.model_profile?.removeEventListener("change", changedProfile);
    if (typeof StudioMotion !== "undefined" && dialog.open) await new Promise((resolve) => {
      dialog.addEventListener("close", resolve, { once: true });
      StudioMotion.dialog(dialog, false, { restoreFocus: false });
    });else
    if (dialog.open) dialog.close();
    PoolBrowser.release(session);
    nodes.forEach((node) => ui.poolDrawer.append(node));
    footer.append(ui.confirmPool);actions.remove();dialog.remove();
    ({ activeSection, poolPage, poolPages, poolItems, poolFacets, poolQueryCount,
      favoritesData, favoritesAvailable, customGroups, drawerTrigger } = saved);
    poolResultContext = "";
    const view = currentView();
    ui.poolTitle.textContent = `${SECTION_META[activeSection].label}池`;ui.poolKicker.textContent = "PROMPT POOL";
    ui.poolSearch.value = view.query;ui.poolSort.value = view.sort;ui.poolLanguage.value = view.language;
    ui.selectedOnly.checked = view.selectedOnly;
    if (selectedLabel) selectedLabel.textContent = selectedLabelText;
    ui.confirmPool.textContent = "完成";ui.confirmPool.disabled = false;
    ui.poolPageLabel.textContent = `${poolPage} / ${poolPages}`;
    ui.poolPrev.disabled = poolPage <= 1;ui.poolNext.disabled = poolPage >= poolPages;
    poolResultContext = poolSelectionContext();renderPoolSidebar();renderPoolItems();updatePoseConflict();
    ui.poolGrid.scrollTop = saved.scrollTop;ui.poolSidebar.scrollTop = saved.sidebarScroll;
    setPoolSidebarOpen(saved.sidebarOpen, { immediate: true });
    if (saved.drawerOpen) {
      ui.poolDrawer.inert = false;ui.poolDrawer.classList.add("open");ui.poolDrawer.setAttribute("aria-hidden", "false");
      animatePoolSurface(ui.poolDrawer, ui.drawerBackdrop, true);
    }
    if (opener?.isConnected) opener.focus({ preventScroll: true });
    resolveResult(result);
    try {if (result) await options.onConfirm?.(result);else await options.onCancel?.();}
    catch (error) {toast(error.message);}
  };
  const changedProfile = () => {if (currentProfile() !== hostProfile) void session.finish(null);};
  ui.model_profile?.addEventListener("change", changedProfile);
  cancel.addEventListener("click", () => session.finish(null));
  dialog.addEventListener("cancel", (event) => {event.preventDefault();event.stopImmediatePropagation();session.finish(null);});
  dialog.addEventListener("close", () => {if (!finishing) session.finish(null);});
  dialog.addEventListener("keydown", (event) => {if (event.key === "Escape") event.stopPropagation();});
  motionDialog(dialog, true);ui.poolSearch.focus({ preventScroll: true });
  void (async () => {
    await Promise.all([loadFavorites("character"), loadCustomGroups("character")]);
    if (characterPickerSession() !== session || session.closed) return;
    await loadPool();
  })();
  return session.promise;
}
function confirmPoolSelection() {
  const session = characterPickerSession();
  if (!session) {closePool();return;}
  if (!session.closed && !ui.confirmPool.disabled) session.finish(session.result());
}
async function openPool(section) {
  if (characterPickerSession()) return;
  invalidatePoolContext();
  const revision = poolContextRevision;
  activeSection = section;
  poolResultContext = "";
  poolQueryCount = null;
  setPoolMoreSelection(false, { immediate: true });
  poolPage = 1;
  const view = currentView();
  drawerTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  // 关闭态的抽屉带 inert(不进 Tab 序列);打开时移除并把焦点移入抽屉。
  ui.poolDrawer.removeAttribute("inert");
  ui.poolDrawer.classList.add("open");
  ui.poolDrawer.setAttribute("aria-hidden", "false");
  ui.drawerBackdrop.classList.remove("closing");
  animatePoolSurface(ui.poolDrawer, ui.drawerBackdrop, true);
  ui.poolTitle.textContent = `${SECTION_META[section].label}池`;
  ui.poolKicker.textContent = "PROMPT POOL";
  ui.poolSearch.value = view.query;
  ui.poolSort.value = view.sort;
  ui.poolLanguage.value = view.language;
  ui.selectedOnly.checked = view.selectedOnly;
  ui.poolSearch.focus({ preventScroll: true });
  updatePoolSelectionControls();
  if (typeof ensurePoolReconciled === "function") {
    try {await ensurePoolReconciled(currentProfile(), section);}
    catch (error) {toast(poolMutationError(error, { model_profile: currentProfile(), section }));return;}
  }
  await Promise.all([loadFavorites(section), loadCustomGroups(section)]);
  if (revision !== poolContextRevision || section !== activeSection) return;
  await loadPool();
  schedulePersist();
}
function animatePoolSurface(drawer, backdrop, open) {
  if (typeof StudioMotion === "undefined") {backdrop.hidden = !open;return;}
  // Drawer CSS starts offscreen without [hidden]; seed its first presence once.
  if (!drawer.dataset.motionState) drawer.hidden = true;
  StudioMotion.presence(drawer, open, {
    from: { opacity: 1, x: 1 },
    render: (value) => {
      drawer.style.transform = `translateX(${value.x * 104}%)`;
    }
  });
  StudioMotion.presence(backdrop, open, { from: { opacity: 0 }, manageInert: false });
}
function hideDrawerBackdrop() {
  animatePoolSurface(ui.poolDrawer, ui.drawerBackdrop, false);
}

function closePool() {
  const picker = characterPickerSession();
  if (picker) {picker.finish(null);return;}
  invalidatePoolContext();
  setPoolMoreSelection(false, { immediate: true });
  currentView().scrollTop = ui.poolGrid.scrollTop;
  setPoolSidebarOpen(false);
  ui.poolDrawer.classList.remove("open");
  ui.poolDrawer.setAttribute("inert", "");
  const focusTarget = drawerTrigger && document.contains(drawerTrigger) ?
  drawerTrigger : ui.dimensionList.querySelector(`[data-manage="${activeSection}"]`) || ui.model_profile;
  focusTarget?.focus({ preventScroll: true });
  ui.poolDrawer.setAttribute("aria-hidden", "true");
  hideDrawerBackdrop();
  drawerTrigger = null;
  schedulePersist();
}
function setPoolSidebarOpen(open, { immediate = false } = {}) {
  const wasOpen = ui.poolSidebar.classList.contains("mobile-open");
  ui.poolSidebar.classList.toggle("mobile-open", open);
  ui.togglePoolSidebar.setAttribute("aria-expanded", String(open));
  ui.togglePoolSidebar.title = open ? "关闭分类导航" : "打开分类导航";
  ui.togglePoolSidebar.setAttribute("aria-label", ui.togglePoolSidebar.title);
  const mobile = matchMedia("(max-width: 620px)").matches;
  ui.poolSidebar.inert = mobile && !open;
  if (typeof StudioMotion === "undefined") return;
  if (!mobile) {
    StudioMotion.freeze(ui.poolSidebar, "sidebar");
    ui.poolSidebar.style.removeProperty("transform");
    ui.poolSidebar.style.removeProperty("will-change");
    delete ui.poolSidebar.dataset.motionSidebar;
    return;
  }
  const previous = StudioMotion.snapshot(ui.poolSidebar, "sidebar");
  ui.poolSidebar.dataset.motionSidebar = "true";
  ui.poolSidebar.style.willChange = "transform";
  StudioMotion.spring(ui.poolSidebar, "sidebar", { x: open ? 0 : -102 }, {
    initial: previous ? undefined : { x: wasOpen ? 0 : -102 },
    immediate,
    onUpdate: (value) => {ui.poolSidebar.style.transform = `translateX(${value.x}%)`;},
    onComplete: () => {
      ui.poolSidebar.style.removeProperty("transform");
      ui.poolSidebar.style.removeProperty("will-change");
    }
  });
}

function poolQueryBody() {
  const view = currentView();
  return {
    model_profile: poolBrowserProfile(),
    page: poolPage,
    limit: 48,
    q: view.query,
    sort: view.sort,
    collection: view.filters.collection,
    custom_group: view.filters.customGroup,
    categories: view.filters.categories,
    traits: view.filters.traits,
    gender: view.filters.gender,
    hair: view.filters.hair,
    eye: view.filters.eye,
    series: view.filters.series
  };
}
async function loadPool({ throwOnError = false } = {}) {
  const requestedProfile = poolBrowserProfile();

  invalidatePoolContext();
  const requestedSection = activeSection;
  const requestId = poolRequest;
  const view = currentView();
  const context = poolSelectionContext();
  const current = () => requestId === poolRequest && requestedProfile === poolBrowserProfile() && context === poolSelectionContext();
  updatePoolSelectionControls();
  try {
    const data = await request(`/api/pools/${requestedSection}/query`, {
      method: "POST", body: JSON.stringify(poolSelectionQuery())
    });
    if (!current()) return;
    poolItems = data.items || [];
    poolFacets = data.facets || {};
    poolPages = data.pages;
    poolPage = data.page;
    poolQueryCount = data.total;
    poolResultContext = poolSelectionContext();
    const picker = characterPickerSession();
    if (picker) {
      picker.remember(poolItems);
      picker.poolTotal = Number(data.pool_total || 0);
      picker.selectedTotal = Number.isFinite(data.selected_total) ? data.selected_total : null;
    } else {
      if (Number.isFinite(data.pool_total)) config.catalog.counts[requestedSection] = data.pool_total;
      if (Number.isFinite(data.selected_total)) poolSelectionStats.set(requestedSection, {
        selectionKey: JSON.stringify(draft.pools[requestedSection]), selectedTotal: data.selected_total
      });
    }
    if (activeSection === "pose") poolItems.forEach((item) => poseItemCache.set(item.id, item));
    ui.poolPageLabel.textContent = `${data.page} / ${data.pages}`;
    ui.poolPrev.disabled = data.page <= 1;
    ui.poolNext.disabled = data.page >= data.pages;
    renderPoolSidebar();
    renderPoolItems();
    if (!picker) {renderDimensions();updatePoolOverview();updatePoseConflict();}
    requestAnimationFrame(() => {
      if (requestId === poolRequest && view.scrollTop && poolPage === 1) {
        ui.poolGrid.scrollTop = view.scrollTop;
        view.scrollTop = 0;
      }
    });
  } catch (error) {
    if (!current()) return;
    toast(error.message);
    if (throwOnError) throw error;
  }
}

function sidebarSection(title, items, kind, activeValues, multi = false) {
  if (!items?.length) return null;
  const section = document.createElement("section");
  section.className = "facet-section";
  section.innerHTML = `<h3>${escapeHtml(title)}</h3><div></div>`;
  const body = section.querySelector("div");
  const active = new Set(Array.isArray(activeValues) ? activeValues : [activeValues].filter(Boolean));
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = active.has(item.value) ? "active" : "";
    // 英文优先显示规范值;中文优先/双语显示服务端给出的中文标签(无中文时回退英文)
    const label = currentView().language === "en" ? item.label : item.label_zh || item.label;
    button.innerHTML = `<span>${escapeHtml(label)}</span><small>${item.count ?? ""}</small>`;
    button.addEventListener("click", () => {
      const filters = currentView().filters;
      if (multi) {
        const values = new Set(filters[kind] || []);
        values.has(item.value) ? values.delete(item.value) : values.add(item.value);
        filters[kind] = [...values];
      } else filters[kind] = filters[kind] === item.value ? "" : item.value;
      poolPage = 1;
      persistPoolView();
      loadPool();
    });
    body.append(button);
  }
  return section;
}

function favoriteChildren(parentId) {
  return (favoritesData.groups || []).filter((group) => (group.parentId || null) === (parentId || null));
}
function favoriteGroup(id) {
  return (favoritesData.groups || []).find((group) => group.id === id) || null;
}
function favoriteDescendantIds(groupId) {
  const ids = new Set();
  const pending = [groupId];
  while (pending.length) {
    const id = pending.pop();
    if (ids.has(id)) continue;
    ids.add(id);
    favoriteChildren(id).forEach((group) => pending.push(group.id));
  }
  return ids;
}
function favoriteGroupPath(group) {
  const names = [];
  const seen = new Set();
  let current = group;
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    names.unshift(current.id === "default" ? "我的收藏" : current.name);
    current = current.parentId ? favoriteGroup(current.parentId) : null;
  }
  return names.join(" / ");
}
function customChildren(parentId) {
  return customGroups.filter((group) => (group.parentId || null) === (parentId || null));
}
function customGroup(id) {
  return customGroups.find((group) => group.id === id) || null;
}
function customDescendantIds(groupId) {
  const ids = new Set();
  const pending = [groupId];
  while (pending.length) {
    const id = pending.pop();
    if (ids.has(id)) continue;
    ids.add(id);
    customChildren(id).forEach((group) => pending.push(group.id));
  }
  return ids;
}
function customGroupPath(group) {
  const names = [];
  const seen = new Set();
  let current = group;
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    names.unshift(current.name);
    current = current.parentId ? customGroup(current.parentId) : null;
  }
  return names.join(" / ");
}
function persistFavoriteTree() {
  if (characterPickerSession()) return;
  const value = Object.fromEntries(SECTIONS.map((section) => [section, [...collapsedFavoriteGroups[section]]]));
  localStorage.setItem(GROUP_TREE_KEY, JSON.stringify(value));
}
function persistCustomTree() {
  if (characterPickerSession()) return;
  const value = Object.fromEntries(SECTIONS.map((section) => [section, [...collapsedCustomGroups[section]]]));
  localStorage.setItem(CUSTOM_GROUP_TREE_KEY, JSON.stringify(value));
}
function focusFavoriteTreeRow(groupId) {
  const view = currentView();
  queueMicrotask(() => {
    if (currentView() !== view) return;
    const row = ui.poolSidebar.querySelector(`.favorite-tree-row[data-group-id="${CSS.escape(groupId)}"]`);
    if (!row) return;
    ui.poolSidebar.querySelectorAll('.favorite-tree [role="treeitem"]').forEach((item) => {
      item.tabIndex = item === row ? 0 : -1;
    });
    row.focus();
  });
}
// Rows belonging to a group's descendant set, looked up pre-render.
function treeRowsForGroups(ids, kind = null) {
  const tree = kind === "custom" ? ui.poolSidebar.querySelector(".custom-group-tree") :
  kind === "favorite" ? ui.poolSidebar.querySelector(".collections .favorite-tree") : ui.poolSidebar;
  return [...(tree?.querySelectorAll(".favorite-tree-row[data-group-id]") || [])].
  filter((row) => ids.has(row.dataset.groupId));
}
const poolTreeRowMotion = new WeakMap();
function animateTreeRow(row, visible) {
  let state = poolTreeRowMotion.get(row);
  if (!state) {
    const names = ["height", "minHeight", "overflow", "marginBottom", "opacity", "transform", "transitionProperty", "willChange"];
    state = { target: null, height: row.getBoundingClientRect().height || row.offsetHeight || 32,
      saved: Object.fromEntries(names.map((name) => [name, row.style[name] || ""])),
      inert: row.inert, role: row.getAttribute("role"), ariaHidden: row.getAttribute("aria-hidden") };
    poolTreeRowMotion.set(row, state);
  }
  state.target = visible;
  const token = state.token = {};
  row.inert = visible ? state.inert : true;
  if (visible) {
    if (state.role !== null) row.setAttribute("role", state.role);
    if (state.ariaHidden === null) row.removeAttribute("aria-hidden");else row.setAttribute("aria-hidden", state.ariaHidden);
  } else {row.removeAttribute("role");row.setAttribute("aria-hidden", "true");}
  row.style.minHeight = "0";row.style.overflow = "hidden";
  row.style.transitionProperty = "background-color";row.style.willChange = "transform, opacity";
  const finish = () => {
    if (state.token !== token) return;
    for (const [name, value] of Object.entries(state.saved)) row.style[name] = value;
    row.inert = state.inert;
    if (!visible) row.remove();
    poolTreeRowMotion.delete(row);
  };
  if (typeof StudioMotion === "undefined" || !StudioMotion.spring) {
    finish();return { finished: Promise.resolve({ cancelled: false }) };
  }
  const active = StudioMotion.snapshot(row, "tree-row");
  const initial = active?.active ? active.value : { progress: visible ? 0 : 1 };
  return StudioMotion.spring(row, "tree-row", { progress: visible ? 1 : 0 }, {
    initial, response: visible ? .28 : .24, damping: 1,
    onUpdate: ({ progress }) => {
      if (state.token !== token) return;
      const value = Math.max(0, Math.min(1, progress));
      row.style.height = `${state.height * value}px`;
      row.style.marginBottom = `${-2 * (1 - value)}px`;
      row.style.opacity = String(value);
      row.style.transform = `translateX(${-6 * (1 - value)}px)`;
    },
    onComplete: finish
  });
}
function animateTreeRowsIn(rows) {
  rows.forEach((row) => animateTreeRow(row, true));
}
function animateTreeRowsOut(rows, done = () => {}) {
  if (!rows.length) {done();return Promise.resolve();}
  return Promise.all(rows.map((row) => animateTreeRow(row, false).finished)).
  then((results) => {if (results.every((result) => !result.cancelled)) done();});
}
// Reconcile only the two tree bodies. Keeping their rows alive preserves focus
// and lets a new click retarget the same spring, including during an exit.
function renderPoolSidebarAnimated(enteredIds = new Set(), kind = null) {
  const view = currentView(),focused = document.activeElement;
  for (const type of kind ? [kind] : ["favorite", "custom"]) {
    const tree = ui.poolSidebar.querySelector(type === "custom" ? ".custom-group-tree" : ".collections .favorite-tree");
    if (!tree) continue;
    const template = document.createElement("div");
    (type === "custom" ? renderCustomTree : renderFavoriteTree)(template, view);
    const oldRows = [...tree.children],key = (row) => row.dataset.groupId ?? "__all";
    const existing = new Map(oldRows.map((row) => [key(row), row]));
    const desired = [],entering = [];
    for (const fresh of [...template.children]) {
      const row = existing.get(key(fresh)) || fresh;
      if (row !== fresh) {
        row.className = fresh.className;row.title = fresh.title;
        for (const name of ["aria-expanded", "aria-selected", "aria-level"]) {
          const value = fresh.getAttribute(name);
          if (value === null) row.removeAttribute(name);else row.setAttribute(name, value);
        }
        const disclosure = row.querySelector(".tree-disclosure"),nextDisclosure = fresh.querySelector(".tree-disclosure");
        if (disclosure && nextDisclosure) {
          disclosure.title = nextDisclosure.title;
          disclosure.setAttribute("aria-label", nextDisclosure.getAttribute("aria-label"));
        }
        if (poolTreeRowMotion.get(row)?.target === false) entering.push(row);
      } else entering.push(row);
      desired.push(row);
    }
    const retained = new Set(desired),leaving = oldRows.filter((row) => !retained.has(row));
    const order = [...desired];
    // Exiting descendants keep their original slot until their height reaches
    // zero, so a sibling expansion cannot move them to the top of the tree.
    oldRows.forEach((row, index) => {
      if (retained.has(row)) return;
      const previous = oldRows.slice(0, index).reverse().find((item) => order.includes(item));
      order.splice(previous ? order.indexOf(previous) + 1 : 0, 0, row);
    });
    let cursor = tree.firstElementChild;
    for (const row of order) {
      if (row === cursor) cursor = cursor.nextElementSibling;else
      tree.insertBefore(row, cursor);
    }
    animateTreeRowsIn(entering);
    animateTreeRowsOut(leaving.filter((row) => poolTreeRowMotion.get(row)?.target !== false));
  }
  if (focused?.isConnected && document.activeElement !== focused) focused.focus({ preventScroll: true });
}

function toggleFavoriteTreeGroup(groupId, restoreFocus = false) {
  const collapsed = poolCollapsedGroups("favorite");
  const collapsing = !collapsed.has(groupId);
  const descendants = favoriteDescendantIds(groupId);
  if (collapsing) collapsed.add(groupId);else collapsed.delete(groupId);
  persistFavoriteTree();
  renderPoolSidebarAnimated(descendants, "favorite");
  if (restoreFocus) focusFavoriteTreeRow(groupId);
}
function focusCustomTreeRow(groupId) {
  const view = currentView();
  queueMicrotask(() => {
    if (currentView() !== view) return;
    const tree = ui.poolSidebar.querySelector(".custom-group-tree");
    const row = tree?.querySelector(`.favorite-tree-row[data-group-id="${CSS.escape(groupId)}"]`);
    if (!row) return;
    tree.querySelectorAll('[role="treeitem"]').forEach((item) => {
      item.tabIndex = item === row ? 0 : -1;
    });
    row.focus();
  });
}
function toggleCustomTreeGroup(groupId, restoreFocus = false) {
  const collapsed = poolCollapsedGroups("custom");
  const collapsing = !collapsed.has(groupId);
  const descendants = customDescendantIds(groupId);
  if (collapsing) collapsed.add(groupId);else collapsed.delete(groupId);
  persistCustomTree();
  renderPoolSidebarAnimated(descendants, "custom");
  if (restoreFocus) focusCustomTreeRow(groupId);
}
function renderFavoriteTree(container, view) {
  container.className = "favorite-tree";
  container.setAttribute("role", "tree");
  container.setAttribute("aria-label", "收藏分组树");
  const allRow = document.createElement("div");
  allRow.className = `favorite-tree-row favorite-all-row${view.filters.collection ? "" : " active"}`;
  allRow.setAttribute("role", "treeitem");
  allRow.setAttribute("aria-level", "1");
  allRow.tabIndex = view.filters.collection ? -1 : 0;
  allRow.innerHTML = `<span class="tree-spacer" aria-hidden="true"></span><button type="button" class="group-select"><span>全部${escapeHtml(SECTION_META[activeSection].label)}</span><small>${config.catalog?.counts?.[activeSection] || ""}</small></button><span></span>`;
  allRow.querySelector(".group-select").addEventListener("click", () => {
    view.filters.collection = "";
    poolPage = 1;
    loadPool();
    persistPoolView();
    renderPoolSidebar();
  });
  container.append(allRow);

  const collapsed = poolCollapsedGroups("favorite");
  const appendGroup = (group, depth) => {
    const children = favoriteChildren(group.id);
    const isCollapsed = collapsed.has(group.id);
    const active = view.filters.collection === group.id;
    const row = document.createElement("div");
    row.className = `favorite-tree-row${active ? " active" : ""}`;
    row.dataset.groupId = group.id;
    row.dataset.parentId = group.parentId || "";
    row.style.setProperty("--tree-depth", String(Math.min(depth, 4)));
    row.title = favoriteGroupPath(group);
    row.setAttribute("role", "treeitem");
    row.setAttribute("aria-level", String(depth + 1));
    row.setAttribute("aria-selected", String(active));
    if (children.length) row.setAttribute("aria-expanded", String(!isCollapsed));
    row.tabIndex = active ? 0 : -1;
    const disclosure = document.createElement(children.length ? "button" : "span");
    disclosure.className = children.length ? "tree-disclosure" : "tree-spacer";
    if (children.length) {
      disclosure.type = "button";
      disclosure.tabIndex = -1;
      disclosure.title = isCollapsed ? "展开子分组" : "折叠子分组";
      disclosure.setAttribute("aria-label", disclosure.title);
      disclosure.textContent = "›";
      disclosure.addEventListener("click", (event) => {
        event.stopPropagation();
        toggleFavoriteTreeGroup(group.id, true);
      });
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "group-select";
    button.innerHTML = `<span>${escapeHtml(group.id === "default" ? "我的收藏" : group.name)}</span><small>${group.totalCount ?? 0}</small>`;
    button.addEventListener("click", () => {
      view.filters.collection = group.id;
      poolPage = 1;
      loadPool();
      persistPoolView();
      renderPoolSidebar();
    });
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "collection-edit";
    edit.title = group.isSystem ? "管理子分组" : "管理收藏分组";
    edit.setAttribute("aria-label", `${edit.title} ${group.id === "default" ? "我的收藏" : group.name}`);
    edit.textContent = "···";
    edit.addEventListener("click", (event) => {
      event.stopPropagation();
      openGroupDialog(group);
    });
    row.append(disclosure, button, edit);
    container.append(row);
    if (!isCollapsed) children.forEach((child) => appendGroup(child, depth + 1));
  };
  favoriteChildren(null).forEach((group) => appendGroup(group, 0));
}
function renderCustomTree(container, view) {
  container.className = "favorite-tree custom-group-tree";
  container.setAttribute("role", "tree");
  container.setAttribute("aria-label", "自定义分组树");
  const allRow = document.createElement("div");
  allRow.className = `favorite-tree-row favorite-all-row${view.filters.customGroup ? "" : " active"}`;
  allRow.dataset.groupId = "";
  allRow.setAttribute("role", "treeitem");
  allRow.setAttribute("aria-level", "1");
  allRow.tabIndex = view.filters.customGroup ? -1 : 0;
  allRow.innerHTML = '<span class="tree-spacer" aria-hidden="true"></span><button type="button" class="group-select"><span>不限分组</span><small></small></button><span></span>';
  allRow.querySelector(".group-select").addEventListener("click", async () => {
    view.filters.customGroup = "";
    poolPage = 1;
    persistPoolView();
    renderPoolSidebar();
    focusCustomTreeRow("");
    await loadPool();
    if (!view.filters.customGroup) focusCustomTreeRow("");
  });
  container.append(allRow);

  const collapsed = poolCollapsedGroups("custom");
  const appendGroup = (group, depth) => {
    const children = customChildren(group.id);
    const isCollapsed = collapsed.has(group.id);
    const active = view.filters.customGroup === group.id;
    const row = document.createElement("div");
    row.className = `favorite-tree-row${active ? " active" : ""}`;
    row.dataset.groupId = group.id;
    row.dataset.parentId = group.parentId || "";
    row.style.setProperty("--tree-depth", String(Math.min(depth, 4)));
    row.title = customGroupPath(group);
    row.setAttribute("role", "treeitem");
    row.setAttribute("aria-level", String(depth + 1));
    row.setAttribute("aria-selected", String(active));
    if (children.length) row.setAttribute("aria-expanded", String(!isCollapsed));
    row.tabIndex = active ? 0 : -1;
    const disclosure = document.createElement(children.length ? "button" : "span");
    disclosure.className = children.length ? "tree-disclosure" : "tree-spacer";
    if (children.length) {
      disclosure.type = "button";
      disclosure.tabIndex = -1;
      disclosure.title = isCollapsed ? "展开子分组" : "折叠子分组";
      disclosure.setAttribute("aria-label", disclosure.title);
      disclosure.textContent = "›";
      disclosure.addEventListener("click", (event) => {
        event.stopPropagation();
        toggleCustomTreeGroup(group.id, true);
      });
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "group-select";
    button.innerHTML = `<span>${escapeHtml(group.name)}</span><small>${group.count || 0}</small>`;
    button.addEventListener("click", async () => {
      view.filters.customGroup = group.id;
      poolPage = 1;
      persistPoolView();
      renderPoolSidebar();
      focusCustomTreeRow(group.id);
      await loadPool();
      if (currentView() === view && view.filters.customGroup === group.id) focusCustomTreeRow(group.id);
    });
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "collection-edit";
    edit.title = group.kind === "group" ? "编辑自定义分组" : "编辑导入大项";
    edit.setAttribute("aria-label", `${edit.title} ${group.name}`);
    edit.textContent = "···";
    edit.addEventListener("click", (event) => {
      event.stopPropagation();
      openCustomGroupDialog(group);
    });
    row.append(disclosure, button, edit);
    container.append(row);
    if (!isCollapsed) children.forEach((child) => appendGroup(child, depth + 1));
  };
  customChildren(null).forEach((group) => appendGroup(group, 0));
}
function handleGroupTreeKeydown(event) {
  const row = event.target.closest('[role="treeitem"]');
  const tree = row?.closest(".favorite-tree");
  if (!row || !tree) return;
  const rows = [...tree.querySelectorAll('[role="treeitem"]')];
  const index = rows.indexOf(row);
  const groupId = row.dataset.groupId || "";
  const isCustom = tree.classList.contains("custom-group-tree");
  const group = groupId ? isCustom ? customGroup(groupId) : favoriteGroup(groupId) : null;
  const toggle = isCustom ? toggleCustomTreeGroup : toggleFavoriteTreeGroup;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const next = rows[index + (event.key === "ArrowDown" ? 1 : -1)];
    if (next) {
      row.tabIndex = -1;
      next.tabIndex = 0;
      next.focus();
    }
  } else if (event.key === "ArrowRight" && group) {
    event.preventDefault();
    if (row.getAttribute("aria-expanded") === "false") toggle(groupId, true);else
    {
      const next = rows[index + 1];
      if (next) {
        row.tabIndex = -1;
        next.tabIndex = 0;
        next.focus();
      }
    }
  } else if (event.key === "ArrowLeft" && group) {
    event.preventDefault();
    if (row.getAttribute("aria-expanded") === "true") toggle(groupId, true);else
    if (group.parentId) {
      const parent = rows.find((item) => item.dataset.groupId === group.parentId);
      if (parent) {
        row.tabIndex = -1;
        parent.tabIndex = 0;
        parent.focus();
      }
    }
  } else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    row.querySelector(".group-select")?.click();
  }
}

function renderPoolSidebar() {
  const view = currentView();
  const nav = document.createDocumentFragment();
  const collections = document.createElement("section");
  collections.className = "facet-section collections";
  collections.innerHTML = `<div class="facet-heading"><h3>收藏分组</h3><button type="button" title="新建收藏分组" aria-label="新建收藏分组">+</button></div><div></div>`;
  collections.querySelector(".facet-heading button").disabled = !favoritesAvailable;
  collections.querySelector(".facet-heading button").addEventListener("click", () => openGroupDialog());
  const collectionBody = collections.lastElementChild;
  renderFavoriteTree(collectionBody, view);
  nav.append(collections);
  const customSection = document.createElement("section");
  customSection.className = "facet-section custom-group-section";
  customSection.innerHTML = `<div class="facet-heading"><h3>自定义分组</h3><button type="button" title="新建自定义分组" aria-label="新建自定义分组">+</button></div><div></div>`;
  customSection.querySelector(".facet-heading button").addEventListener("click", () => openCustomGroupDialog());
  const customBody = customSection.lastElementChild;
  renderCustomTree(customBody, view);
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
  if (characterPickerSession()) ui.poolSidebar.querySelectorAll(".facet-heading > button, .collection-edit").forEach((button) => {button.hidden = true;});
}

function displayTitle(item) {
  const lang = currentView().language;
  // 角色条目:title 是罗马字原名,title_zh 来自内置译名表(未收录时为空)。
  if (item.section === "character") {
    const zh = item.title_zh || "";
    if (lang === "en" || !zh) return item.title;
    if (lang === "bilingual") return `${zh} / ${item.title}`;
    return zh;
  }
  if (lang === "en") return item.subtitle || item.title;
  if (lang === "bilingual" && item.subtitle && item.subtitle !== item.title) return `${item.title} / ${item.subtitle}`;
  return item.title;
}
function seriesDisplay(item) {
  if (!item.copyright) return "";
  if (currentView().language === "en") return item.copyright;
  return item.subtitle_zh || item.copyright;
}
function categoryDisplay(value) {
  // 双语分类「中文 (English)」按语言偏好取半边;纯中文/纯英文原样。
  const match = String(value ?? "").match(/^(.*?)\s*\(([^()]*)\)\s*$/);
  if (!match || !match[1].trim()) return String(value ?? "");
  return currentView().language === "en" ? match[2].trim() : match[1].trim();
}
function itemBadges(item) {
  const values = [];
  if (activeSection === "character") {
    values.push(item.gender === "1girl" ? "女性" : item.gender === "1boy" ? "男性" : "未标注");
    if (item.hair) values.push(`${item.hair} hair`);
    if (item.eye) values.push(`${item.eye} eyes`);
  } else {
    values.push(...(item.categories || []).slice(0, 1).map(categoryDisplay));
    values.push(...(item.traits || []).slice(0, 2));
  }
  return values.filter(Boolean);
}
function renderPoolItems() {
  const picker = characterPickerSession();
  const favMap = favoriteMap();
  ui.poolEmpty.hidden = poolItems.length > 0;
  ui.poolGrid.replaceChildren(
    ...poolItems.map((item) => {
      const article = document.createElement("article");
      const selected = selectionHas(activeSection, item.id);
      const savedFavorite = favMap.get(favoriteKey(item));
      const favorite = Boolean(savedFavorite?.groupIds?.length);
      article.className = `pool-item ${selected ? "selected" : ""}${favorite ? " favorite" : ""}`;
      article.innerHTML = `<label><input type="checkbox" ${selected ? "checked" : ""}><span class="pool-thumb">${item.preview ? `<img loading="lazy" src="${escapeHtml(item.preview)}" alt="">` : "<span>+</span>"}</span><span class="pool-item-copy"><strong title="${escapeHtml(displayTitle(item))}">${escapeHtml(displayTitle(item))}</strong><span class="item-badges">${itemBadges(
        item
      ).
      map((value) => `<em>${escapeHtml(value)}</em>`).
      join(
        ""
      )}</span><small>${escapeHtml(savedFavorite?.nickname || seriesDisplay(item) || item.prompt || "自定义项")}</small></span></label><div class="item-tools"><button class="favorite-toggle ${favorite ? "active" : ""}" type="button" title="${favorite ? "取消收藏" : "加入收藏"}" aria-label="${favorite ? "取消收藏" : "加入收藏"}">${favorite ? "♥" : "♡"}</button><button class="favorite-settings" type="button" title="收藏设置" aria-label="收藏设置" ${favorite ? "" : "hidden"}>⋮</button><button class="item-edit" type="button" title="编辑自定义项" aria-label="编辑自定义项" ${item.builtin ? "hidden" : ""}>✎</button></div>`;
      article.
      querySelector("label input").
      addEventListener("change", (event) => toggleSelection(activeSection, item.id, event.target.checked));
      if (picker) {
        article.querySelector(".item-tools").hidden = true;
        if (picker.selectionMode === "single") {
          const input = article.querySelector("label input");input.type = "radio";input.name = "bound-character-choice";
        }
        if (picker.alreadyBound.has(item.id)) {
          article.classList.add("already-bound");
          article.querySelector("label input").disabled = true;
          const status = document.createElement("span");status.className = "pool-bound-label";status.textContent = "已绑定 · 保留原设置";
          article.querySelector(".pool-item-copy").append(status);
        }
      }
      article.querySelector("img")?.addEventListener("error", (event) => event.target.style.display = "none");
      article.querySelector(".favorite-toggle").disabled = !favoritesAvailable;
      article.querySelector(".favorite-toggle").addEventListener("click", (event) => {
        event.stopPropagation();
        toggleFavorite(item, !favorite);
      });
      article.querySelector(".favorite-settings").addEventListener("click", (event) => {
        event.stopPropagation();
        openFavoriteDialog(item);
      });
      article.querySelector(".item-edit").addEventListener("click", (event) => {
        event.stopPropagation();
        openCustom(item);
      });
      return article;
    })
  );
  updatePoolSelectionControls();
  if (!picker) updateFavoriteSelectionButton();
}

function updatePoseConflict() {
  if (activeSection !== "pose") {
    ui.poseConflict.hidden = true;
    return;
  }
  const selection = draft.pools.pose;
  if (selection.mode === "all" || Number(draft.counts.pose || 1) <= 1) {
    ui.poseConflict.hidden = true;
    return;
  }
  const selected = selection.ids.map((id) => poseItemCache.get(id)).filter(Boolean);
  const groups = new Map();
  for (const item of selected)
  for (const slot of item.conflict_slots || []) {
    if (!groups.has(slot)) groups.set(slot, []);
    groups.get(slot).push(item.title);
  }
  const conflicts = [...groups.entries()].filter(([, names]) => names.length > 1);
  if (!conflicts.length) {
    ui.poseConflict.hidden = true;
    return;
  }
  const labels = { hand_action: "手部动作", body_pose: "整体姿态", interaction: "双人互动" };
  ui.poseConflict.hidden = false;
  ui.poseConflict.innerHTML = `<strong>存在潜在姿势冲突</strong><span>${conflicts.map(([slot, names]) => `${escapeHtml(labels[slot] || slot)}：${names.map(escapeHtml).join("、")}`).join("；")}</span>`;
}

function reconcileFavoriteCollection() {
  const view = currentView();
  if (view.filters.collection && !favoriteGroup(view.filters.collection)) {
    view.filters.collection = "";
    schedulePersist();
    return true;
  }
  return false;
}
async function toggleFavorite(item, favorite) {
  try {
    favoritesData = await requestFavorites(`/api/favorites/${activeSection}/item`, {
      method: "PUT",
      body: JSON.stringify({ ...(favorite ? { id: item.id } : { source_item_id: item.id }), favorite })
    });
    favoritesAvailable = true;
    const resetCollection = reconcileFavoriteCollection();
    if (resetCollection || !favorite && currentView().filters.collection) await loadPool();else
    {
      renderPoolItems();
      renderPoolSidebar();
    }
    toast(favorite ? "已加入 Anima 收藏" : "已取消收藏");
  } catch (error) {
    toast(error.message);
  }
}
function orderedFavoriteGroups() {
  const result = [];
  const append = (group, depth) => {
    result.push({ group, depth });
    favoriteChildren(group.id).forEach((child) => append(child, depth + 1));
  };
  favoriteChildren(null).forEach((group) => append(group, 0));
  return result;
}
function openFavoriteDialog(item) {
  editingFavoriteItem = item;
  const saved = favoriteMap().get(favoriteKey(item)) || {};
  ui.favoriteTitle.textContent = displayTitle(item);
  ui.favoriteNickname.value = saved.nickname || "";
  ui.favoriteGroups.replaceChildren(
    ...orderedFavoriteGroups().map(({ group, depth }) => {
      const label = document.createElement("label");
      label.className = "favorite-group-check tree-check";
      label.style.setProperty("--tree-depth", String(Math.min(depth, 4)));
      label.title = favoriteGroupPath(group);
      label.innerHTML = `<input type="checkbox" value="${escapeHtml(group.id)}" ${(saved.groupIds || []).includes(group.id) ? "checked" : ""}><span>${escapeHtml(group.id === "default" ? "我的收藏" : group.name)}</span><small>${group.totalCount ?? 0}</small>`;
      return label;
    })
  );
  motionDialog(ui.favoriteDialog, true);
}
function favoriteSelectionDefaultGroups() {
  const currentCollection = currentView().filters.collection;
  if (currentCollection && favoriteGroup(currentCollection)) return [currentCollection];
  return favoriteGroup("default") ? ["default"] : [];
}
function renderFavoriteSelectionGroups(selectedIds = favoriteSelectionDefaultGroups()) {
  const selected = new Set(selectedIds);
  ui.favoriteSelectionGroups.replaceChildren(
    ...orderedFavoriteGroups().map(({ group, depth }) => {
      const label = document.createElement("label");
      label.className = "favorite-group-check tree-check";
      label.style.setProperty("--tree-depth", String(Math.min(depth, 4)));
      label.title = favoriteGroupPath(group);
      label.innerHTML = `<input type="checkbox" value="${escapeHtml(group.id)}" ${selected.has(group.id) ? "checked" : ""}><span>${escapeHtml(group.id === "default" ? "我的收藏" : group.name)}</span><small>${group.totalCount ?? 0}</small>`;
      return label;
    })
  );
}
function openFavoriteSelectionDialog() {
  if (!favoritesAvailable || !selectedCount(activeSection)) return;
  ui.favoriteSelectionTitle.textContent = `收藏${SECTION_META[activeSection].label}池本次选择`;
  ui.favoriteSelectionSummary.textContent = `将当前${SECTION_META[activeSection].label}池已选择的 ${selectedCount(activeSection)} 项加入收藏分组；已有收藏只追加分组，不覆盖备注。`;
  ui.favoriteSelectionGroupName.value = "";
  ui.favoriteSelectionError.hidden = true;
  ui.favoriteSelectionError.textContent = "";
  ui.confirmFavoriteSelection.disabled = false;
  ui.confirmFavoriteSelection.textContent = "收藏";
  renderFavoriteSelectionGroups();
  motionDialog(ui.favoriteSelectionDialog, true);
}
async function createFavoriteSelectionGroup() {
  const name = ui.favoriteSelectionGroupName.value.trim();
  if (!name) {
    ui.favoriteSelectionError.textContent = "请输入收藏分组名称";
    ui.favoriteSelectionError.hidden = false;
    return;
  }
  ui.createFavoriteSelectionGroup.disabled = true;
  try {
    favoritesData = await requestFavorites(`/api/favorites/${activeSection}/groups`, {
      method: "POST",
      body: JSON.stringify({ name })
    });
    const selectedIds = [...ui.favoriteSelectionGroups.querySelectorAll("input:checked")].map((input) => input.value);
    const newGroup = (favoritesData.groups || []).find((group) => group.name === name && !group.isSystem);
    if (newGroup) selectedIds.push(newGroup.id);
    renderFavoriteSelectionGroups([...new Set(selectedIds)]);
    ui.favoriteSelectionGroupName.value = "";
    ui.favoriteSelectionError.hidden = true;
    renderPoolSidebar();
  } catch (error) {
    ui.favoriteSelectionError.textContent = error.message;
    ui.favoriteSelectionError.hidden = false;
  } finally {
    ui.createFavoriteSelectionGroup.disabled = false;
  }
}
async function saveFavoriteSelection(event) {
  event.preventDefault();
  const section = activeSection,profile = currentProfile();
  const groupIds = [...ui.favoriteSelectionGroups.querySelectorAll("input:checked")].map((input) => input.value);
  if (!groupIds.length) {
    ui.favoriteSelectionError.textContent = "至少选择一个收藏分组";
    ui.favoriteSelectionError.hidden = false;
    return;
  }
  ui.confirmFavoriteSelection.disabled = true;
  ui.confirmFavoriteSelection.textContent = "收藏中…";
  try {
    const payload = await request(`/api/favorites/${section}/selection?model_profile=${profile}`, {
      method: "POST",
      body: JSON.stringify({ selection: draft.pools[section], groupIds })
    });
    if (section !== activeSection || profile !== currentProfile()) return;
    favoritesData = payload;
    motionDialog(ui.favoriteSelectionDialog, false);
    renderPoolItems();
    renderPoolSidebar();
    toast(`已收藏 ${payload.selectedCount || 0} 项：新增 ${payload.createdCount || 0} 项，更新 ${payload.updatedCount || 0} 项`);
  } catch (error) {
    ui.favoriteSelectionError.textContent = error.message;
    ui.favoriteSelectionError.hidden = false;
  } finally {
    ui.confirmFavoriteSelection.disabled = false;
    ui.confirmFavoriteSelection.textContent = "收藏";
  }
}
async function saveFavorite(event) {
  event.preventDefault();
  if (!editingFavoriteItem) return;
  const groupIds = [...ui.favoriteGroups.querySelectorAll("input:checked")].map((input) => input.value);
  try {
    favoritesData = await requestFavorites(`/api/favorites/${activeSection}/item`, {
      method: "PUT",
      body: JSON.stringify({
        id: editingFavoriteItem.id,
        favorite: true,
        groupIds,
        nickname: ui.favoriteNickname.value
      })
    });
    motionDialog(ui.favoriteDialog, false);
    if (reconcileFavoriteCollection()) await loadPool();else
    {
      renderPoolItems();
      renderPoolSidebar();
    }
    toast("收藏设置已保存");
  } catch (error) {
    toast(error.message);
  }
}
async function removeFavorite() {
  if (!editingFavoriteItem) return;
  try {
    favoritesData = await requestFavorites(`/api/favorites/${activeSection}/item`, {
      method: "PUT",
      body: JSON.stringify({ source_item_id: editingFavoriteItem.id, favorite: false })
    });
    motionDialog(ui.favoriteDialog, false);
    reconcileFavoriteCollection();
    await loadPool();
    toast("已取消收藏");
  } catch (error) {
    toast(error.message);
  }
}

function openGroupDialog(group = null) {
  editingGroup = group;
  ui.groupId.value = group?.id || "";
  ui.groupName.value = group?.name || "";
  ui.groupDialogTitle.textContent = !group ? "新建收藏分组" : group.isSystem ? "管理我的收藏" : "编辑收藏分组";
  ui.groupNameField.hidden = Boolean(group?.isSystem);
  ui.groupName.required = !group?.isSystem;
  ui.deleteGroup.hidden = !group || group.isSystem;
  ui.importChildGroup.hidden = !group;
  ui.saveGroupButton.hidden = Boolean(group?.isSystem);
  motionDialog(ui.groupDialog, true);
}
async function saveGroup(event) {
  event.preventDefault();
  const id = ui.groupId.value;
  try {
    favoritesData = await requestFavorites(
      id ?
      `/api/favorites/${activeSection}/groups/${encodeURIComponent(id)}` :
      `/api/favorites/${activeSection}/groups`,
      { method: id ? "PUT" : "POST", body: JSON.stringify({ name: ui.groupName.value }) }
    );
    motionDialog(ui.groupDialog, false);
    renderPoolSidebar();
    toast(id ? "收藏分组已更新" : "收藏分组已创建");
  } catch (error) {
    toast(error.message);
  }
}
function setChildGroupError(message = "") {
  ui.childGroupError.textContent = message;
  ui.childGroupError.hidden = !message;
}
function renderChildGroupSources() {
  const query = ui.childGroupSearch.value.trim().toLocaleLowerCase();
  const siblings = favoriteChildren(childGroupParent?.id);
  const available = customGroups.filter((group) => !query || group.name.toLocaleLowerCase().includes(query));
  ui.childGroupList.replaceChildren(
    ...available.map((group) => {
      const duplicate = siblings.some(
        (item) =>
        item.sourceCustomGroupId === group.id ||
        item.name.trim().toLocaleLowerCase() === group.name.trim().toLocaleLowerCase()
      );
      const disabled = !group.count || duplicate;
      const label = document.createElement("label");
      label.className = `child-group-option${disabled ? " disabled" : ""}`;
      label.title = duplicate ? "当前父组下已经导入" : !group.count ? "空分组不能导入" : `导入 ${group.count} 项`;
      label.innerHTML = `<input type="radio" name="childGroupSource" value="${escapeHtml(group.id)}" ${selectedChildSource === group.id ? "checked" : ""} ${disabled ? "disabled" : ""}><span><strong>${escapeHtml(customGroupPath(group))}</strong><small>${duplicate ? "已导入" : !group.count ? "空分组" : `${group.count} 项 · 一次性快照`}</small></span>`;
      label.querySelector("input").addEventListener("change", () => {
        selectedChildSource = group.id;
        ui.confirmChildGroup.disabled = false;
        setChildGroupError();
      });
      return label;
    })
  );
  ui.childGroupEmpty.textContent = customGroups.length ? "没有匹配的自定义分组。" : "当前分类还没有自定义分组。";
  ui.childGroupEmpty.hidden = available.length > 0;
}
function openChildGroupDialog(group = editingGroup) {
  if (!group) return;
  childGroupParent = group;
  selectedChildSource = "";
  motionDialog(ui.groupDialog, false);
  ui.childGroupTitle.textContent = `导入到“${group.id === "default" ? "我的收藏" : group.name}”`;
  ui.childGroupHint.textContent = "从当前提示词池的自定义分组创建一次性收藏快照，后续两边独立修改。";
  ui.childGroupSearch.value = "";
  ui.confirmChildGroup.disabled = true;
  setChildGroupError();
  renderChildGroupSources();
  motionDialog(ui.childGroupDialog, true);
}
async function importFavoriteChild(event) {
  event.preventDefault();
  if (!childGroupParent || !selectedChildSource || ui.confirmChildGroup.disabled) return;
  const label = ui.confirmChildGroup.textContent;
  ui.confirmChildGroup.disabled = true;
  ui.confirmChildGroup.textContent = "导入中…";
  try {
    favoritesData = await requestFavorites(
      `/api/favorites/${activeSection}/groups/${encodeURIComponent(childGroupParent.id)}/children/import`,
      { method: "POST", body: JSON.stringify({ customGroupId: selectedChildSource }) }
    );
    collapsedFavoriteGroups[activeSection].delete(childGroupParent.id);
    persistFavoriteTree();
    motionDialog(ui.childGroupDialog, false);
    renderPoolSidebar();
    if (currentView().filters.collection === childGroupParent.id) await loadPool();
    toast("收藏子分组已导入");
  } catch (error) {
    setChildGroupError(error.message);
  } finally {
    ui.confirmChildGroup.disabled = !selectedChildSource;
    ui.confirmChildGroup.textContent = label;
  }
}

function deleteStat(label, value) {
  return `<div><strong>${Number(value) || 0}</strong><span>${escapeHtml(label)}</span></div>`;
}
function openDeleteGroupDialog(kind, group) {
  pendingGroupDelete = { kind, group };
  const favorite = kind === "favorite";
  const subtree = favorite ? favoriteDescendantIds(group.id) : customDescendantIds(group.id);
  const directCount = Number(group.directCount || 0);
  const affectedFavorites = favorite ?
  (favoritesData.items || []).filter((item) => (item.groupIds || []).some((id) => subtree.has(id))) :
  [];
  const exclusiveCount = favorite ?
  affectedFavorites.filter((item) => !(item.groupIds || []).some((id) => !subtree.has(id))).length :
  Number(group.exclusiveCount || 0);
  const totalCount = favorite ? affectedFavorites.length : Number(group.count || 0);
  const sharedCount = Math.max(0, totalCount - exclusiveCount);
  const childCount = Number(group.childCount || 0);
  const canDeleteItems = favorite ? Boolean(group.parentId && !childCount && directCount) : totalCount > 0;
  motionDialog(ui.groupDialog, false);
  motionDialog(ui.customGroupDialog, false);
  ui.deleteGroupTitle.textContent = `删除“${group.id === "default" ? "我的收藏" : group.name}”`;
  ui.deleteGroupSummary.textContent =
  subtree.size > 1 ?
  `将删除当前父组及 ${subtree.size - 1} 个后代分组，${favorite ? "收藏" : "自定义提示词"}默认保留。` :
  "默认只删除分组结构，条目保持不变。";
  ui.deleteGroupStats.innerHTML = favorite ?
  `${deleteStat("分组", subtree.size)}${deleteStat("聚合收藏", totalCount)}${deleteStat("独占收藏", exclusiveCount)}${deleteStat("共享收藏", sharedCount)}` :
  `${deleteStat("分组", subtree.size)}${deleteStat("聚合条目", totalCount)}${deleteStat("可删除独占项", exclusiveCount)}${deleteStat("保留共享项", sharedCount)}`;
  ui.deleteGroupKeep.checked = true;
  ui.deleteGroupKeepHint.textContent = favorite ?
  `${exclusiveCount} 项失去最后分组的收藏会移入“我的收藏”，其余收藏保留现有分组。` :
  "所有自定义提示词都会保留，仅解除当前分组关联。";
  ui.deleteGroupItemsOption.hidden = !canDeleteItems;
  ui.deleteGroupItemsTitle.textContent = favorite ?
  `同时删除 ${exclusiveCount} 项独占收藏` :
  `同时删除 ${exclusiveCount} 项独占提示词`;
  ui.deleteGroupItemsHint.textContent = sharedCount ?
  `${sharedCount} 项共享条目只会解除当前分组关联。` :
  "没有共享条目。";
  ui.deleteGroupAllOption.hidden = favorite;
  ui.deleteGroupAllHint.textContent = sharedCount ?
  `不可撤销：将永久删除全部 ${totalCount} 项内容，其中 ${sharedCount} 项也属于其他分组。` :
  `不可撤销：将永久删除该分组子树中的全部 ${totalCount} 项内容。`;
  ui.deleteGroupError.hidden = true;
  ui.deleteGroupError.textContent = "";
  ui.confirmDeleteGroup.disabled = false;
  ui.confirmDeleteGroup.dataset.storageWrite = favorite ? "" : "custom_prompts";
  ui.confirmDeleteGroup.textContent = "删除";
  motionDialog(ui.deleteGroupDialog, true);
}
async function confirmGroupDelete(event) {
  event.preventDefault();
  if (!pendingGroupDelete || ui.confirmDeleteGroup.disabled) return;
  const { kind, group } = pendingGroupDelete;
  const context = { model_profile: currentProfile(), section: activeSection };
  const session = pendingGroupDelete;
  const deleteItems = !ui.deleteGroupItemsOption.hidden && ui.deleteGroupItems.checked;
  const deleteMode = ui.deleteGroupAll.checked ? "all" : deleteItems ? "exclusive" : "keep";
  ui.confirmDeleteGroup.disabled = true;
  ui.confirmDeleteGroup.textContent = "删除中…";
  ui.deleteGroupError.hidden = true;
  try {
    if (kind === "favorite") {
      const payload = await request(
        `/api/favorites/${activeSection}/groups/${encodeURIComponent(group.id)}?deleteItems=${deleteItems}`,
        { method: "DELETE" }
      );
      favoritesData = payload;
      if ((payload.deletedGroupIds || []).includes(currentView().filters.collection))
      currentView().filters.collection = "";
      collapsedFavoriteGroups[activeSection] = new Set(
        [...collapsedFavoriteGroups[activeSection]].filter((id) => !(payload.deletedGroupIds || []).includes(id))
      );
      persistFavoriteTree();
      toast(
        deleteItems ?
        `已删除分组和 ${payload.deletedFavoriteCount || 0} 项独占收藏` :
        `已删除 ${payload.deletedGroupCount || 1} 个分组，条目已保留`
      );
    } else {
      const payload = await request(
        `/api/custom-groups/${context.section}/${encodeURIComponent(group.id)}?deleteMode=${deleteMode}&model_profile=${encodeURIComponent(context.model_profile)}`,
        { method: "DELETE" }
      );
      if (typeof poolSourceChanged === "function") poolSourceChanged({ ...context, ...payload });
      if (context.model_profile !== currentProfile() || context.section !== activeSection || session !== pendingGroupDelete) return;
      customGroups = payload.groups || [];
      const deletedIds = new Set(payload.deletedItemIds || []);
      const selection = draft.pools[activeSection];
      selection.ids = selection.ids.filter((id) => !deletedIds.has(id));
      selection.excluded_ids = selection.excluded_ids.filter((id) => !deletedIds.has(id));
      deletedIds.forEach((id) => poseItemCache.delete(id));
      const removedGroups = new Set(payload.deletedGroupIds || [group.id]);
      if (removedGroups.has(currentView().filters.customGroup)) currentView().filters.customGroup = "";
      collapsedCustomGroups[activeSection] = new Set(
        [...collapsedCustomGroups[activeSection]].filter((id) => !removedGroups.has(id))
      );
      persistCustomTree();
      schedulePersist();
      toast(
        payload.deleteMode === "all" ?
        `已彻底删除分组和 ${payload.deletedItemCount || 0} 项关联内容` :
        payload.deleteMode === "exclusive" ?
        `已删除分组和 ${payload.deletedItemCount || 0} 项独占提示词` :
        "自定义分组已删除，条目已保留"
      );
    }
    motionDialog(ui.deleteGroupDialog, false);
    pendingGroupDelete = null;
    if (kind === "favorite" || typeof poolSourceChanged !== "function") {
      await loadPool();
      renderPoolSidebar();
      renderDimensions();
      updatePoolOverview();
    }
  } catch (error) {
    ui.deleteGroupError.textContent = typeof poolMutationError === "function" ? poolMutationError(error, context) : error.message;
    ui.deleteGroupError.hidden = false;
  } finally {
    ui.confirmDeleteGroup.disabled = false;
    ui.confirmDeleteGroup.textContent = "删除";
  }
}
function deleteFavoriteGroup() {
  if (editingGroup) openDeleteGroupDialog("favorite", editingGroup);
}

function selectCurrentPage(checked) {
  if (poolResultContext !== poolSelectionContext() || poolSelectionOperation) return;
  const picker = characterPickerSession();
  if (picker) {
    try {
      if (checked) picker.append(poolItems.map((item) => item.id));else
      for (const item of poolItems) picker.toggle(item.id, false);
    } catch (error) {toast(error.message);return;}
    return poolSelectionChanged();
  }
  for (const item of poolItems) {
    const selection = draft.pools[activeSection];
    if (selection.mode === "all")
    selection.excluded_ids = checked ?
    selection.excluded_ids.filter((value) => value !== item.id) :
    [...new Set([...selection.excluded_ids, item.id])];else
    if (checked) selection.ids = [...new Set([...selection.ids, item.id])];else
    selection.ids = selection.ids.filter((value) => value !== item.id);
  }
  return poolSelectionChanged();
}

function openCustom(item = null) {
  ++customEditorSession;
  const error = document.getElementById("customMutationError");
  if (error) {error.hidden = true;error.textContent = "";}
  invalidatePromptNormalization();
  editingCustom = item;
  ui.customDialogTitle.textContent = item ? "编辑自定义项" : "新增自定义项";
  ui.customId.value = item?.id || "";
  ui.customTitle.value = item?.title || "";
  ui.customSubtitle.value = item?.subtitle || "";
  ui.customGender.value = item?.gender === "1girl" ? "female" : item?.gender === "1boy" ? "male" : "unknown";
  ui.customHair.value = item?.hair || "";
  ui.customEye.value = item?.eye || "";
  ui.customSeries.value = item?.copyright || "";
  ui.customCategory.value = item?.categories?.[0] || "";
  ui.customTraits.value = (item?.traits || []).join(", ");
  ui.customPrompt.value = item?.prompt || "";
  ui.customGenderField.hidden = activeSection !== "character";
  ui.customCharacterMeta.hidden = activeSection !== "character";
  ui.customPoseMeta.hidden = activeSection !== "pose";
  const selectedGroups = new Set(item?.group_ids || item?.groupIds || []);
  const leafGroups = customGroups.filter((group) => group.kind === "group");
  ui.customGroupChecks.replaceChildren(
    ...leafGroups.map((group) => {
      const label = document.createElement("label");
      label.className = "favorite-group-check";
      label.title = customGroupPath(group);
      label.innerHTML = `<input type="checkbox" value="${escapeHtml(group.id)}" ${selectedGroups.has(group.id) ? "checked" : ""}><span>${escapeHtml(customGroupPath(group))}</span>`;
      return label;
    })
  );
  if (!leafGroups.length)
  ui.customGroupChecks.innerHTML = `<div class="inline-empty">尚未创建自定义分组，可在随机池侧栏新建。</div>`;
  ui.deleteCustom.hidden = !item;
  motionDialog(ui.customDialog, true);
}
async function saveCustom(event) {
  event.preventDefault();
  if (customWriteBusy) return;
  const context = { model_profile: currentProfile(), section: activeSection },session = customEditorSession,previousId = editingCustom?.id;
  customWriteBusy = true;
  try {
    await normalizePromptFields({ custom_prompt: ui.customPrompt.value });
    if (session !== customEditorSession || context.model_profile !== currentProfile() || context.section !== activeSection) return;
    const payload = {
      section: context.section,
      model_profile: context.model_profile,
      title: ui.customTitle.value,
      subtitle: ui.customSubtitle.value,
      prompt: ui.customPrompt.value,
      gender: ui.customGender.value,
      hair: ui.customHair.value,
      eye: ui.customEye.value,
      copyright: ui.customSeries.value,
      categories: ui.customCategory.value ? [ui.customCategory.value] : [],
      traits: ui.customTraits.value.
      split(",").
      map((value) => value.trim()).
      filter(Boolean),
      groupIds: [...ui.customGroupChecks.querySelectorAll("input:checked")].map((input) => input.value)
    };
    const item = previousId ?
    await request(`/api/custom-prompts/${previousId}?model_profile=${encodeURIComponent(context.model_profile)}`, { method: "PUT", body: JSON.stringify(payload) }) :
    await request("/api/custom-prompts", { method: "POST", body: JSON.stringify(payload) });
    if (session === customEditorSession && context.model_profile === currentProfile() && context.section === activeSection && !selectionHas(activeSection, item.id)) toggleSelection(activeSection, item.id, true);
    if (typeof poolSourceChanged === "function") poolSourceChanged({ ...context, ...item, updatedItemIds: item.updatedItemIds || [item.id] });
    if (session !== customEditorSession || context.model_profile !== currentProfile() || context.section !== activeSection) return;
    motionDialog(ui.customDialog, false);
    toast(previousId ? "自定义项及关联收藏已更新" : "自定义项已加入随机池");
  } catch (error) {
    const message = typeof poolMutationError === "function" ? poolMutationError(error, context) : error.message;
    if (session === customEditorSession) {
      const target = document.getElementById("customMutationError");
      if (target) {target.textContent = message;target.hidden = false;}
    }
    toast(message);
  } finally {
    customWriteBusy = false;
  }
}
async function deleteCustomItem() {
  if (customWriteBusy || !editingCustom || !confirm("删除这个自定义项，并同步移除关联收藏？收藏分组会保留。")) return;
  const context = { model_profile: currentProfile(), section: activeSection },id = editingCustom.id,session = customEditorSession;
  customWriteBusy = true;
  try {
    const result = await request(`/api/custom-prompts/${id}?model_profile=${encodeURIComponent(context.model_profile)}`, { method: "DELETE" });
    if (typeof poolSourceChanged === "function") poolSourceChanged({ ...context, ...result, deletedItemIds: result.deletedItemIds || [id] });
    if (session !== customEditorSession || context.model_profile !== currentProfile() || context.section !== activeSection) return;
    motionDialog(ui.customDialog, false);
    toast("自定义项及关联收藏已删除，收藏分组已保留");
  } catch (error) {
    const message = typeof poolMutationError === "function" ? poolMutationError(error, context) : error.message;
    if (session === customEditorSession) {
      const target = document.getElementById("customMutationError");
      if (target) {target.textContent = message;target.hidden = false;}
    }
    toast(message);
  } finally {
    customWriteBusy = false;
  }
}

function openCustomGroupDialog(group = null) {
  editingCustomGroup = group;
  ui.customGroupId.value = group?.id || "";
  ui.customGroupName.value = group?.name || "";
  ui.customGroupDialogTitle.textContent = group ?
  group.kind === "group" ?
  "编辑自定义分组" :
  "编辑导入大项" :
  "新建自定义分组";
  ui.deleteCustomGroup.hidden = !group;
  motionDialog(ui.customGroupDialog, true);
}
async function saveCustomGroup(event) {
  event.preventDefault();
  const id = ui.customGroupId.value;
  try {
    const payload = await request(
      id ? `/api/custom-groups/${activeSection}/${encodeURIComponent(id)}?model_profile=${encodeURIComponent(currentProfile())}` : `/api/custom-groups/${activeSection}?model_profile=${encodeURIComponent(currentProfile())}`,
      { method: id ? "PUT" : "POST", body: JSON.stringify({ name: ui.customGroupName.value, model_profile: currentProfile() }) }
    );
    customGroups = payload.groups || [];
    motionDialog(ui.customGroupDialog, false);
    renderPoolSidebar();
    toast(id ? "自定义分组已更新" : "自定义分组已创建");
  } catch (error) {
    toast(error.message);
  }
}
function removeCustomGroup() {
  if (editingCustomGroup) openDeleteGroupDialog("custom", editingCustomGroup);
}

function selectedImportGroupIds() {
  return [...ui.importTargetGroups.querySelectorAll("input:checked")].map((input) => input.value);
}
function selectedImportGroupNames() {
  const selected = new Set(selectedImportGroupIds());
  return customGroups.filter((group) => selected.has(group.id)).map((group) => customGroupPath(group));
}
function renderImportTargetGroups() {
  const leafGroups = customGroups.filter((group) => group.kind === "group");
  if (!leafGroups.length) {
    ui.importTargetGroups.innerHTML = '<div class="inline-empty">当前池暂无自定义分组，请先在池侧栏新建。</div>';
    return;
  }
  ui.importTargetGroups.replaceChildren(
    ...leafGroups.map((group) => {
      const label = document.createElement("label");
      label.className = "favorite-group-check";
      label.title = customGroupPath(group);
      label.innerHTML = `<input type="checkbox" value="${escapeHtml(group.id)}"><span>${escapeHtml(customGroupPath(group))}</span><small>${group.count || 0} 项</small>`;
      label.querySelector("input").addEventListener("change", () => {
        if (importPreview) renderImportPreview();
      });
      return label;
    })
  );
}
function openImportDialog() {
  importPreview = null;
  const label = SECTION_META[activeSection].label;
  ui.importDialogTitle.textContent = `${label}池批量导入`;
  ui.importJsonTemplate.href = `/api/custom-prompts/templates/${activeSection}/json?model_profile=${encodeURIComponent(currentProfile())}`;
  ui.importJsonTemplate.textContent = `${label} JSON 模板`;
  ui.importCsvTemplate.href = `/api/custom-prompts/templates/${activeSection}/csv?model_profile=${encodeURIComponent(currentProfile())}`;
  ui.importCsvTemplate.textContent = `${label} CSV 模板`;
  ui.importFile.value = "";
  ui.importBundleName.value = "";
  ui.importHint.textContent = `仅导入${label}池条目；选择文件后会先校验，不会立即写入。`;
  ui.importSummary.hidden = true;
  ui.importRows.replaceChildren();
  ui.commitImport.disabled = true;
  renderImportTargetGroups();
  motionDialog(ui.importDialog, true);
}
async function previewImportFile() {
  const file = ui.importFile.files?.[0];
  if (!file) return;
  const format = file.name.toLowerCase().endsWith(".csv") ?
  "csv" :
  file.name.toLowerCase().endsWith(".json") ?
  "json" :
  "";
  if (!format) {
    toast("只支持 JSON 或 CSV 文件");
    return;
  }
  ui.importBundleName.value = file.name.replace(/\.[^.]+$/, "").trim();
  ui.importHint.textContent = `正在校验 ${file.name}`;
  try {
    importPreview = await request("/api/custom-prompts/import/preview", {
      method: "POST",
      body: JSON.stringify({
        format,
        content: await file.text(),
        section: activeSection,
        model_profile: currentProfile(),
        bundleName: ui.importBundleName.value
      })
    });
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
  ui.importRows.replaceChildren(
    ...importPreview.rows.map((row, index) => {
      const element = document.createElement("div");
      element.className = "import-row";
      element.dataset.status = row.status;
      const title = row.item?.title || "无法读取";
      const embeddedGroups = row.groups?.length ? row.groups : ["未分组"];
      const bundleName = ui.importBundleName.value.trim() || "未命名大项";
      const importPaths = embeddedGroups.map((name) => `${bundleName} / ${name}`);
      const targetGroups = selectedImportGroupNames();
      const detail = row.error || (row.action === "skip" ? "不导入" : [...importPaths, ...targetGroups].join("、"));
      element.innerHTML = `<span>#${row.row}</span><span class="import-status">${labels[row.status]}</span><strong title="${escapeHtml(title)}">${escapeHtml(title)}</strong><small title="${escapeHtml(detail)}">${escapeHtml(detail)}</small><select aria-label="导入操作" ${row.status === "error" ? "disabled" : ""}>${row.status === "new" ? '<option value="create">导入</option><option value="skip">跳过</option>' : row.status === "conflict" ? '<option value="skip">跳过</option><option value="overwrite">覆盖</option>' : '<option value="skip">跳过</option>'}</select>`;
      const select = element.querySelector("select");
      select.value = row.action;
      select.addEventListener("change", () => {
        importPreview.rows[index].action = select.value;
        renderImportPreview();
      });
      return element;
    })
  );
  ui.commitImport.disabled =
  !ui.importBundleName.value.trim() ||
  !importPreview.rows.some((row) => row.status !== "error" && row.action !== "skip");
}
async function commitCustomImport() {
  if (!importPreview) return;
  const preview = importPreview,context = { model_profile: currentProfile(), section: activeSection };
  ui.commitImport.disabled = true;
  try {
    const result = await request("/api/custom-prompts/import", {
      method: "POST",
      body: JSON.stringify({
        rows: preview.rows,
        section: context.section,
        model_profile: context.model_profile,
        targetGroupIds: selectedImportGroupIds(),
        bundleName: ui.importBundleName.value
      })
    });
    if (typeof poolSourceChanged === "function") poolSourceChanged({ ...context, ...result });
    if (preview !== importPreview || context.model_profile !== currentProfile() || context.section !== activeSection) return;
    motionDialog(ui.importDialog, false);
    toast(`${result.bundleName}：新增 ${result.imported}，更新 ${result.updated}，跳过 ${result.skipped}`);
  } catch (error) {
    ui.commitImport.disabled = false;
    const message = typeof poolMutationError === "function" ? poolMutationError(error, context) : error.message;
    if (preview === importPreview) ui.importHint.textContent = message;
    toast(message);
  }
}

function ensureSelectValue(select, value) {
  if (!select || !value) return;
  if (![...select.options].some((option) => option.value === value)) {
    const missing = select.dataset?.nodeChoices && !JSON.parse(select.dataset.nodeChoices).includes(value);
    const option = new Option(missing ? `当前不可用 · ${value}` : value, value);
    if (missing) option.dataset.missing = "true";
    select.add(option);
  }
  select.value = value;
}
function populateSelect(select, values, selected) {
  select.replaceChildren(...values.map((value) => new Option(value, value)));
  ensureSelectValue(select, selected);
}
function populateResourceSelect(select, values, selected, emptyText) {
  const options = values.map((value) => new Option(value, value));
  if (selected && !values.includes(selected)) {
    const missing = new Option(`当前缺失 · ${selected}`, selected);
    missing.dataset.missing = "true";
    options.unshift(missing);
  }
  if (!options.length) options.push(new Option(emptyText, ""));
  select.replaceChildren(...options);
  select.value = selected && [...select.options].some((option) => option.value === selected) ? selected : "";
}
function applyNodeInputControl(element, spec) {
  if (!element || !Array.isArray(spec)) return;
  const options = spec[1] && typeof spec[1] === "object" ? spec[1] : {};
  const choices = Array.isArray(spec[0]) ? spec[0] : Array.isArray(options.options) && options.options.every((value) => typeof value === "string") ? options.options : null;
  if (choices && element.tagName === "SELECT") {
    element.dataset.nodeChoices = JSON.stringify(choices);
    populateResourceSelect(element, choices, element.value, "节点没有可用选项");
    for (const option of element.options) if (option.dataset.missing) option.textContent = `当前不可用 · ${option.value}`;
  }
  if (element.type === "number") for (const key of ["min", "max", "step"]) if (typeof options[key] === "number") element[key] = String(options[key]);
}
function applyNodeResourceControls() {
  const nodeInputs = resources.node_inputs || {};
  const face = nodeInputs.ForbiddenVisionFaceProcessorIntegrated || {};
  for (const [key, id] of Object.entries(UPSCALER_CONTROL_IDS.face_refine)) applyNodeInputControl(ui[id], face[key]);
  const nvidia = nodeInputs.RTXVideoSuperResolution || {};
  applyNodeInputControl(ui.nvidia_quality, nvidia.quality);
  const scale = nvidia.resize_type?.[1]?.options?.find((option) => option.key === "scale by multiplier")?.inputs?.required?.scale;
  applyNodeInputControl(ui.nvidia_scale, scale);
  applyNodeInputControl(ui.pixel_hires_upscale_method, nodeInputs.ImageScaleBy?.upscale_method);
}
function resolveInventoryPath(selected, values) {
  const requested = String(selected || "").trim().replaceAll("\\", "/");
  if (!requested) return "";
  const lower = requested.toLocaleLowerCase();
  const exact = values.find((value) => String(value).replaceAll("\\", "/").toLocaleLowerCase() === lower);
  if (exact) return exact;
  const basename = requested.split("/").pop().toLocaleLowerCase();
  const matches = values.filter((value) => String(value).replaceAll("\\", "/").split("/").pop().toLocaleLowerCase() === basename);
  return matches.length === 1 ? matches[0] : requested;
}
function populateModelSelect(select, values, selected, missingReason = "当前分类不可用") {
  const groups = new Map();
  for (const raw of values) {
    const value = String(raw || "").replaceAll("\\", "/");
    if (!value) continue;
    const slash = value.lastIndexOf("/");
    const folder = slash >= 0 ? value.slice(0, slash) : "根目录";
    if (!groups.has(folder)) groups.set(folder, []);
    groups.get(folder).push({ value, label: slash >= 0 ? value.slice(slash + 1) : value });
  }
  const options = [];
  for (const [folder, items] of groups) {
    const optgroup = document.createElement("optgroup");
    optgroup.label = folder;
    for (const item of items) optgroup.append(new Option(item.label, item.value));
    options.push(optgroup);
  }
  if (selected && !values.includes(selected)) {
    const missing = new Option(`不可用 · ${selected} · ${missingReason}`, selected);
    missing.dataset.missing = "true";
    missing.disabled = true;
    missing.title = missingReason;
    options.unshift(missing);
  }
  if (!options.length) options.push(new Option("没有识别为当前档案的主模型", ""));
  select.replaceChildren(...options);
  select.value = selected && [...select.options].some((option) => option.value === selected) ? selected : "";
}
function refreshCurrentModelSelection() {
  const selected = ui.model_name.value || profileDrafts[currentProfile()]?.model_name || "";
  const values = resourcesLoaded && resourceInventoryProfile === currentProfile() ? resources.models : [];
  // Resolve legacy basenames against the complete inventory before filtering by profile.
  // An incompatible exact path must never be replaced by a similarly named compatible file.
  const allNames = resourcesLoaded && resourceInventoryProfile === currentProfile() ?
  (resources.model_items || []).map((item) => item.name || item.filename).filter(Boolean) : [];
  const exact = allNames.find((value) => normalizedPath(value) === normalizedPath(selected));
  const matches = allNames.filter((value) => normalizedPath(value).split("/").pop() === normalizedPath(selected));
  const resolved = exact || (!normalizedPath(selected).includes("/") && matches.length === 1 ? matches[0] : selected);
  populateModelSelect(ui.model_name, values, resolved, modelSelectionIssue(resolved));
  ui.model_name.disabled = values.length === 0;
}
function modelLabel(value) {
  const filename = String(value || "未选择模型").
  replaceAll("\\", "/").
  split("/").
  pop();
  return filename.replace(/\.(safetensors|ckpt|pth)$/i, "");
}
function profileEntries() {
  const source = resources.model_profiles || resources.profiles || {};
  return Object.entries(source).filter(([id, value]) => value && typeof value === "object").map(([id, value]) => ({ id: normalizeProfileId(id), ...value }));
}
function updateProfileResources() {
  const profile = profileConfig(currentProfile());
  const available = profile.available || {};
  const missing = [
  ...(available.clip === false ? [profile.clip_name || "CLIP"] : []),
  ...(available.vae === false ? [profile.vae_name || "VAE"] : [])];

  ui.modelProfileHint.textContent = missing.length ?
  `${profileLabel(currentProfile())}：缺少 ${missing.join("、")}` :
  `${profileLabel(currentProfile())} 会自动切换绑定模型、编码方式与采样参数。`;
  const boundSeed = resolveInventoryPath(profile.seedvr2_model || "", resources.seedvr2_models || []);
  if (boundSeed && resources.seedvr2_models?.includes(boundSeed) && !ui.seedvr2_model_name.value) ensureSelectValue(ui.seedvr2_model_name, boundSeed);
  ui.seedvr2_model_name.title = profile.seedvr2_model ? `档案默认：${profile.seedvr2_model}` : "请选择 SeedVR2 模型";
  ui.modelProfileHint.dataset.profileReady = String(!missing.length && available.model !== false);
  ui.model_name.title = modelSelectionIssue() || `${profileLabel(currentProfile())} 主模型`;
}
function profileOptionsFromResources() {
  return [{ id: "anima", label: "Anima" }];
}
async function loadResources({ refresh = false } = {}) {
  const cache = loadResources.cache ||= { generation: 0, values: new Map(), pending: new Map() };
  const selectedProfile = currentProfile();
  if (refresh && !cache.pending.get(selectedProfile)?.refresh) {cache.generation++;cache.values.clear();cache.pending.clear();}
  const generation = cache.generation;
  const requestId = ++resourceRequest;
  const cached = cache.values.get(selectedProfile);
  const selectedSeed = ui.seedvr2_model_name.value || profileConfig(selectedProfile).seedvr2_model || "";
  const selectedSeedVae = ui.seedvr2_vae_name.value || profileConfig(selectedProfile).seedvr2_vae || "";
  const selectedSampler = ui.sampler_name.value || profileDefaults(selectedProfile).sampler_name || defaults?.sampler_name || "";
  const selectedScheduler = ui.scheduler.value || profileDefaults(selectedProfile).scheduler || defaults?.scheduler || "";
  if (!cached) resourcesLoaded = false;
  resourceInventoryProfile = selectedProfile;
  resourceError = "";
  if (!cached) {
    refreshCurrentModelSelection();
    updateGenerationAvailability();
    ui.model_profile.disabled = true;
    ui.sampler_name.disabled = true;
    ui.scheduler.disabled = true;
    ui.seedvr2_model_name.disabled = true;
    ui.seedvr2_vae_name.disabled = true;
    for (const id of ["latent_sampler_name", "latent_sampler_scheduler", "latent_second_sampler_name", "latent_second_scheduler", "seedvr2_sampler_name", "seedvr2_scheduler", "face_sampler", "face_scheduler"].filter((id) => ui[id])) ui[id].disabled = true;
  }
  // Retain selected values while disabled: autosave and overlapping reads must
  // not capture placeholder values and silently replace them with defaults.
  try {
    let pending = cache.pending.get(selectedProfile);
    if (!cached && !pending) {
      pending = request(`/api/resources?model_profile=${encodeURIComponent(selectedProfile)}${refresh ? "&refresh=1" : ""}`).then((payload) => {
        if (generation === cache.generation) cache.values.set(selectedProfile, payload);
        return payload;
      }).finally(() => {if (cache.pending.get(selectedProfile) === pending) cache.pending.delete(selectedProfile);});
      pending.refresh = refresh;
      cache.pending.set(selectedProfile, pending);
    }
    const payload = clone(cached || (await pending));
    if (generation !== cache.generation || requestId !== resourceRequest || selectedProfile !== currentProfile()) return;
    const modelProfiles = payload.model_profiles || payload.profiles || {};
    resources = {
      models: Array.isArray(payload.models) ? payload.models : [],
      model_items: Array.isArray(payload.model_items) ? payload.model_items : [],
      profile_models: payload.profile_models && typeof payload.profile_models === "object" ? payload.profile_models : {},
      model_assignments: payload.model_assignments || {},
      seedvr2_models: Array.isArray(payload.seedvr2_models) ? payload.seedvr2_models : [],
      seedvr2_vaes: Array.isArray(payload.seedvr2_vaes) ? payload.seedvr2_vaes : [],
      upscale_models: Array.isArray(payload.upscale_models) ? payload.upscale_models : [],
      clips: Array.isArray(payload.clips) ? payload.clips : [],
      vaes: Array.isArray(payload.vaes) ? payload.vaes : [],
      clip_types: Array.isArray(payload.clip_types) ? payload.clip_types : [],
      samplers: Array.isArray(payload.samplers) ? payload.samplers : [],
      schedulers: Array.isArray(payload.schedulers) ? payload.schedulers : [],
      model_profiles: modelProfiles,
      profiles: modelProfiles,
      nodes: payload.nodes && typeof payload.nodes === "object" ? payload.nodes : {},
      plugins: payload.plugins && typeof payload.plugins === "object" ? payload.plugins : {},
      node_inputs: payload.node_inputs || {},
      node_types: Array.isArray(payload.node_types) ? payload.node_types : []
    };
    profileResources = modelProfiles;
    const profileOptions = profileOptionsFromResources();
    ui.model_profile.replaceChildren(...profileOptions.map((item) => new Option(item.label || item.name || item.id, item.id)));
    ensureSelectValue(ui.model_profile, selectedProfile);
    activeProfileId = currentProfile();
    const modelValues = resources.models.length ? resources.models : resources.profile_models[selectedProfile] || [];
    populateResourceSelect(ui.sampler_name, resources.samplers, ui.sampler_name.value || selectedSampler, "没有可用 Sampler");
    populateResourceSelect(ui.scheduler, resources.schedulers, ui.scheduler.value || selectedScheduler, "没有可用 Scheduler");
    const seedValues = resources.seedvr2_models;
    populateResourceSelect(ui.seedvr2_model_name, seedValues, resolveInventoryPath(ui.seedvr2_model_name.value || selectedSeed, resources.seedvr2_models), "没有可用 SeedVR2 模型");
    const seedVaeValues = resources.seedvr2_vaes;
    populateResourceSelect(ui.seedvr2_vae_name, seedVaeValues, resolveInventoryPath(ui.seedvr2_vae_name.value || selectedSeedVae, resources.seedvr2_vaes), "没有可用 SeedVR2 VAE");
    if (ui.pixel_hires_model_name) populateResourceSelect(ui.pixel_hires_model_name, resources.upscale_models, resolveInventoryPath(ui.pixel_hires_model_name.value, resources.upscale_models), "没有可用像素放大模型");
    for (const id of ["latent_sampler_name", "latent_second_sampler_name", "seedvr2_sampler_name", "face_sampler"].filter((id) => ui[id])) populateResourceSelect(ui[id], resources.samplers, controlValue(id), "没有可用 Sampler");
    for (const id of ["latent_sampler_scheduler", "latent_second_scheduler", "seedvr2_scheduler", "face_scheduler"].filter((id) => ui[id])) populateResourceSelect(ui[id], resources.schedulers, controlValue(id), "没有可用 Scheduler");
    resourcesLoaded = true;
    ui.model_profile.disabled = profileOptions.length < 2;
    ui.model_name.disabled = modelValues.length === 0;
    ui.sampler_name.disabled = resources.samplers.length === 0;
    ui.scheduler.disabled = resources.schedulers.length === 0;
    ui.seedvr2_vae_name.disabled = resources.seedvr2_vaes.length === 0;
    for (const id of ["latent_sampler_name", "latent_second_sampler_name", "seedvr2_sampler_name", "face_sampler"].filter((id) => ui[id])) ui[id].disabled = resources.samplers.length === 0;
    for (const id of ["latent_sampler_scheduler", "latent_second_scheduler", "seedvr2_scheduler", "face_scheduler"].filter((id) => ui[id])) ui[id].disabled = resources.schedulers.length === 0;
    applyNodeResourceControls();
    if (typeof sharedParameterResources === "function") sharedParameterResources();
  } catch (error) {
    if (generation !== cache.generation || requestId !== resourceRequest || selectedProfile !== currentProfile()) return;
    resources = { models: [], model_items: [], profile_models: {}, model_assignments: {}, seedvr2_models: [], seedvr2_vaes: [], upscale_models: [], samplers: [], schedulers: [], model_profiles: {}, profiles: {}, nodes: {}, plugins: {} };
    profileResources = {};
    resourceError = `模型资源读取失败：${error.message}`;
    ui.model_profile.disabled = false;
    populateResourceSelect(ui.sampler_name, [], ui.sampler_name.value || selectedSampler, "Sampler 资源离线");
    populateResourceSelect(ui.scheduler, [], ui.scheduler.value || selectedScheduler, "Scheduler 资源离线");
    populateResourceSelect(ui.seedvr2_model_name, [], ui.seedvr2_model_name.value || selectedSeed, "SeedVR2 资源离线");
    populateResourceSelect(ui.seedvr2_vae_name, [], ui.seedvr2_vae_name.value || selectedSeedVae, "SeedVR2 VAE 资源离线");
    populateResourceSelect(ui.face_upscaler_model, [], ui.face_upscaler_model.value, "脸部放大模型资源离线");
    if (ui.pixel_hires_model_name) populateResourceSelect(ui.pixel_hires_model_name, [], ui.pixel_hires_model_name.value, "像素放大模型资源离线");
  }
  updateProfileResources();
  updateRepairControls();

}
function pipelineFlowText(enabled) {
  const stages = [
  ["pixel_hires", "像素高清"], ["latent", "潜空间放大"], ["post_sampler", "整图后采样"],
  ["face", "脸部细化"], ["seedvr2", "SeedVR2"], ["nvidia", "N 卡 VSR"], ["dlss", "DLSS"]];

  return ["底图", ...stages.filter(([key]) => enabled[key]).map(([, label]) => label), "保存"].join(" → ");
}
function updateRepairControls() {
  refreshCurrentModelSelection();
  const upscalers = {
    pixel_hires: Boolean(ui.pixel_hires_enabled?.checked),
    post_sampler: ui.latent_second_sampler_enabled.checked,
    dlss: Boolean(ui.dlss_enabled?.checked),
    latent: ui.latent_upscale_enabled.checked,
    seedvr2: ui.seedvr2_enabled.checked,
    nvidia: ui.nvidia_upscale_enabled.checked,
    face: ui.face_refine_enabled.checked
  };
  const activeCount = Object.values(upscalers).filter(Boolean).length;
  if (ui.pipelineFlow) ui.pipelineFlow.textContent = pipelineFlowText(upscalers);
  const baseLabel = ui.base_mode.value === "dual_sampler" ? "底图－双采样器" : "普通底图";
  ui.repairSummary.textContent = `${profileLabel(currentProfile())} · ${baseLabel} · ${activeCount} 项后处理`;
  ui.latentUpscaleFields.classList.toggle("disabled", !upscalers.latent);
  ui.latentUpscaleFields.hidden = !upscalers.latent;
  if (ui.latentDoubleSamplingHint) ui.latentDoubleSamplingHint.hidden = !upscalers.latent || !upscalers.post_sampler;
  ui.seedvr2Fields.classList.toggle("disabled", !upscalers.seedvr2);
  ui.nvidiaUpscaleFields.classList.toggle("disabled", !upscalers.nvidia);
  ui.faceRefineFields.classList.toggle("disabled", !upscalers.face);
  ui.latentSecondSamplerFields.classList.toggle("disabled", !upscalers.post_sampler);
  ui.latent_upscale_scale.disabled = !upscalers.latent;
  ui.latent_upscale_method.disabled = !upscalers.latent;
  for (const id of Object.values(LATENT_SAMPLER_IDS)) if (ui[id]) ui[id].disabled = !upscalers.latent;
  ui.latent_second_sampler_enabled.disabled = false;
  for (const id of Object.values(POST_SAMPLER_IDS)) ui[id].disabled = !upscalers.post_sampler;
  ui.pixelHiresFields?.classList.toggle("disabled", !upscalers.pixel_hires);
  ui.dlssFields?.classList.toggle("disabled", !upscalers.dlss);
  if (ui.pixel_hires_model_name) ui.pixel_hires_model_name.disabled = !upscalers.pixel_hires || !resourcesLoaded;
  for (const id of ["pixel_hires_percent", "pixel_hires_upscale_method"]) if (ui[id]) ui[id].disabled = !upscalers.pixel_hires || upscalers.post_sampler;
  if (ui.pixelHiresResizeHint) ui.pixelHiresResizeHint.textContent = upscalers.post_sampler ? "后采样已开启，跳过缩图" : "按原生放大结果缩图";
  ui.seedvr2_model_name.disabled = !upscalers.seedvr2 || !resourcesLoaded;
  ui.seedvr2_vae_name.disabled = !upscalers.seedvr2 || !resourcesLoaded;
  for (const id of ["seedvr2_weight_dtype", "seedvr2_scale_by", "seedvr2_color_correction_method", ...Object.values(SEED_SAMPLER_IDS), ...Object.values(SEED_TILE_IDS.encode), ...Object.values(SEED_TILE_IDS.decode)]) ui[id].disabled = !upscalers.seedvr2;
  for (const id of ["nvidia_resize_type", "nvidia_scale", "nvidia_quality"]) ui[id].disabled = !upscalers.nvidia;
  for (const id of ["nvidia_dlss_style", "nvidia_dlss_intensity", "nvidia_dlss_local_tone", "nvidia_dlss_local_struct", "nvidia_dlss_output_mix"]) ui[id].disabled = !upscalers.dlss;
  for (const id of Object.values(UPSCALER_CONTROL_IDS.face_refine).filter((id) => id !== "face_refine_enabled")) ui[id].disabled = !upscalers.face;
  if (typeof sharedParameterVisibility === "function") sharedParameterVisibility();

  const settings = readSettings();
  const profile = profileConfig(currentProfile());
  const warnings = [];
  if (resourceError) warnings.push(resourceError);
  if (resourcesLoaded && profile.missing?.length) warnings.push(`${profileLabel(currentProfile())} 缺少资源：${profile.missing.join("、")}`);
  warnings.push(...generationClassificationIssues(settings));
  if (resourcesLoaded && upscalers.seedvr2 && settings.upscalers.seedvr2.model_name && resources.seedvr2_models.length && !resources.seedvr2_models.includes(settings.upscalers.seedvr2.model_name)) warnings.push(`SeedVR2 模型不可用：${settings.upscalers.seedvr2.model_name}`);
  if (resourcesLoaded && upscalers.face && settings.upscalers.face_refine.upscaler_model && !resources.upscale_models.includes(settings.upscalers.face_refine.upscaler_model)) warnings.push(`脸部放大模型不可用：${settings.upscalers.face_refine.upscaler_model}`);
  const advertisedAvailability = (inventory, names) => {
    if (inventory == null) return null;
    if (Array.isArray(inventory)) {
      const available = new Set(inventory.map((item) => String(item).toLowerCase()));
      return names.some((name) => available.has(name.toLowerCase()));
    }
    if (typeof inventory !== "object") return false;
    const advertised = names.filter((name) => Object.prototype.hasOwnProperty.call(inventory, name));
    if (!advertised.length) return null;
    return advertised.some((name) => inventory[name] === true);
  };
  if (resourcesLoaded && upscalers.nvidia) {
    const nvidiaNode = advertisedAvailability(resources.nodes, ["NvidiaUpscale", "RTXVideoSuperResolution"]);
    const nvidiaPlugin = advertisedAvailability(resources.plugins, ["nvidia_upscale"]);
    if ((nvidiaNode !== null || nvidiaPlugin !== null) && nvidiaNode !== true && nvidiaPlugin !== true) {
      warnings.push("N 卡放大节点不可用：RTXVideoSuperResolution");
    }
  }
  if (resourcesLoaded && !resources.samplers.length) warnings.push("ComfyUI 当前没有可用 Sampler");
  if (resourcesLoaded && !resources.schedulers.length) warnings.push("ComfyUI 当前没有可用 Scheduler");
  if (resourcesLoaded && !resources.samplers.includes(settings.sampler_name)) warnings.push(`Sampler 不可用：${settings.sampler_name}`);
  if (resourcesLoaded && !resources.schedulers.includes(settings.scheduler)) warnings.push(`Scheduler 不可用：${settings.scheduler}`);
  ui.resourceWarning.hidden = warnings.length === 0;
  ui.resourceWarning.textContent = warnings.join("；");
  updateProfileResources();
  updateGenerationAvailability();
}

let ownershipItems = [];
let ownershipRequest = 0;

function renderResourceOwnership() {
  const query = ui.ownershipSearch.value.trim().toLocaleLowerCase();
  const filtered = ownershipItems.filter((item) => `${item.name || ""} ${item.folder || ""} ${item.basename || ""} ${item.base_model || ""} ${item.classification_reason || ""}`.toLocaleLowerCase().includes(query));
  ui.ownershipSummary.textContent = `${ownershipItems.length} 项资源 · ${ownershipItems.filter((item) => !item.model_profile).length} 项未识别或不支持 · 分类来自 LoRA Manager`;
  ui.ownershipList.replaceChildren(...filtered.map((item) => {
    const row = document.createElement("article");
    row.className = "ownership-row";
    const copy = document.createElement("div");
    copy.className = "ownership-row-copy";
    const name = document.createElement("strong");
    name.textContent = item.basename || item.name;
    const folder = document.createElement("small");
    folder.textContent = item.name;
    folder.title = item.name;
    const reason = document.createElement("small");
    reason.className = "ownership-reason";
    reason.textContent = item.classification_reason || (item.model_profile ? "已识别" : "请在 LoRA Manager 中补全 base_model");
    copy.append(name, folder, reason);
    const metadata = document.createElement("div");
    metadata.className = "ownership-metadata";
    const baseModel = document.createElement("span");
    baseModel.textContent = `base_model：${item.base_model || "未提供"}`;
    const mode = document.createElement("strong");
    mode.textContent = `模式：${item.model_profile ? profileLabel(item.model_profile) : "不可用"}`;
    metadata.append(baseModel, mode);
    row.append(copy, metadata);
    return row;
  }));
  if (!filtered.length) ui.ownershipList.innerHTML = '<div class="inline-empty">没有匹配的资源。</div>';
}
async function loadResourceOwnership({ refresh = false } = {}) {
  const kind = ui.ownershipKind.value;
  const requestId = ++ownershipRequest;
  ownershipItems = [];
  ui.ownershipError.hidden = true;
  ui.ownershipSummary.textContent = "正在读取资源清单…";
  ui.ownershipList.replaceChildren();
  try {
    const payload = await request(`/api/resource-ownership?kind=${encodeURIComponent(kind)}${refresh ? "&refresh=1" : ""}`);
    if (requestId !== ownershipRequest || kind !== ui.ownershipKind.value) return;
    ownershipItems = Array.isArray(payload.items) ? payload.items : [];
    renderResourceOwnership();
  } catch (error) {
    if (requestId !== ownershipRequest || kind !== ui.ownershipKind.value) return;
    ownershipItems = [];
    ui.ownershipSummary.textContent = "资源清单读取失败";
    ui.ownershipError.textContent = error.message;
    ui.ownershipError.hidden = false;
  }
}
async function refreshResourceClassification() {
  ui.refreshResourceClassification.disabled = true;
  invalidateResourceInventories();
  try {
    await Promise.allSettled([
    loadResourceOwnership({ refresh: true }),
    loadResources({ refresh: true }),
    loadLoraInventory({ refresh: true })]
    );
    regenerationOptions = null;
    if (selectedRecord && (!ui.replayDrawer.hidden || !ui.variantDrawer.hidden)) await loadRegenerationOptions();
  } finally {
    ui.refreshResourceClassification.disabled = false;
  }
}

const PRESET_SETTING_KEYS = [
"pipeline_version",
"workbench", "generation_mode", "clip_name", "vae_name", "clip_type",
"model_profile",
"model_name",

"base_mode",
"base_denoise",

"dual",
"upscalers",
"loras",
"lora_managed_triggers",
"manual_artist",
"quality_prompt",
"extra_prompt",
"negative_prompt",
"width",
"height",
"steps",
"cfg",
"sampler_name",
"scheduler"];

function presetSnapshot() {
  const settings = readSettings();
  return Object.fromEntries(PRESET_SETTING_KEYS.filter((key) => settings[key] !== undefined).map((key) => [key, clone(settings[key])]));
}
async function loadStylePresets() {
  const requestedProfile = currentProfile();
  const requestId = ++stylePresetRequest;
  if (stylePresetProfile && stylePresetProfile !== requestedProfile) {
    stylePresets = [];
    renderStylePresets();
  }
  stylePresetProfile = requestedProfile;
  try {
    const data = await request(`/api/style-presets?model_profile=${encodeURIComponent(requestedProfile)}`);
    if (requestId !== stylePresetRequest || requestedProfile !== currentProfile()) return;
    stylePresets = data.items || [];
    renderStylePresets();
  } catch (error) {
    if (requestId !== stylePresetRequest || requestedProfile !== currentProfile()) return;
    toast(error.message);
  }
}
function setPresetFeedback(message = "", type = "error") {
  ui.presetError.textContent = message;
  ui.presetError.hidden = !message;
  ui.presetError.dataset.type = type;
  ui.presetName.setAttribute("aria-invalid", String(Boolean(message) && type === "error"));
}
function renderStylePresets() {
  ui.presetSummary.textContent = `${stylePresets.length} 个预设`;
  ui.presetEmpty.hidden = stylePresets.length > 0;
  ui.presetList.replaceChildren(
    ...stylePresets.map((item) => {
      const row = document.createElement("div");
      row.className = `preset-row${item.favorite ? " favorite" : ""}`;
      const activeLoras = (item.settings?.loras || []).filter((lora) => lora.enabled).length;
      const settings = item.settings || {};
      const modelShort = profileLabel(settings.model_profile || "anima");
      const metaParts = [
      modelShort,
      `${settings.width || "?"}×${settings.height || "?"}`,
      `${settings.steps || "?"} 步`,
      settings.base_mode === "dual_sampler" ? "双采样底图" : "普通底图",
      settings.upscalers?.latent?.enabled ? "潜空间放大" : "无潜空间放大"];

      if (activeLoras) metaParts.push(`${activeLoras} LoRA`);
      row.innerHTML = `<button class="preset-apply" type="button" title="${escapeHtml(settings.model_profile || "anima")}"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(metaParts.join(" · "))}</small></button><div class="preset-actions"><button type="button" data-preset-favorite title="${item.favorite ? "取消收藏" : "收藏"}" aria-label="${item.favorite ? "取消收藏" : "收藏"}">${item.favorite ? "★" : "☆"}</button><button type="button" data-preset-update title="用当前设置覆盖" aria-label="用当前设置覆盖">↻</button><button type="button" data-preset-rename title="重命名" aria-label="重命名">✎</button><button type="button" data-preset-delete title="删除" aria-label="删除">×</button></div>`;
      row.querySelector(".preset-apply").addEventListener("click", () => {
        if (typeof randomWorkbenchApplyPreset === "function") randomWorkbenchApplyPreset(item);else
        applySettings({ ...readSettings(), ...item.settings });
        schedulePersist();
        toast(`已应用预设：${item.name}`);
      });
      row.
      querySelector("[data-preset-favorite]").
      addEventListener("click", () => updateStylePreset(item, { favorite: !item.favorite }));
      row.querySelector("[data-preset-update]").addEventListener("click", () => {
        if (confirm(`用当前设置覆盖“${item.name}”？`)) updateStylePreset(item, { settings: presetSnapshot() });
      });
      row.querySelector("[data-preset-rename]").addEventListener("click", () => {
        const name = prompt("新的预设名称", item.name);
        if (name?.trim()) updateStylePreset(item, { name: name.trim() });
      });
      row.querySelector("[data-preset-delete]").addEventListener("click", () => deleteStylePreset(item));
      return row;
    })
  );
}
async function createStylePreset() {
  if (ui.savePreset.disabled) return;
  const name = ui.presetName.value.trim();
  if (!name) {
    setPresetFeedback("请输入预设名称");
    ui.presetName.focus();
    return;
  }
  if (stylePresets.some((item) => item.name.trim().toLocaleLowerCase() === name.toLocaleLowerCase())) {
    setPresetFeedback("已有同名预设，请改名或使用覆盖按钮");
    ui.presetName.focus();
    return;
  }
  const originalLabel = ui.savePreset.textContent;
  ui.savePreset.disabled = true;
  ui.savePreset.textContent = "保存中…";
  setPresetFeedback("正在写入本地预设…", "pending");
  try {
    const created = await request("/api/style-presets", {
      method: "POST",
      body: JSON.stringify({ name, favorite: false, model_profile: currentProfile(), settings: presetSnapshot() })
    });
    const favoriteCount = stylePresets.findIndex((item) => !item.favorite);
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
async function updateStylePreset(item, changes) {
  try {
    await request(`/api/style-presets/${encodeURIComponent(item.id)}`, {
      method: "PUT",
      body: JSON.stringify({ ...changes, model_profile: currentProfile() })
    });
    await loadStylePresets();
  } catch (error) {
    toast(error.message);
  }
}
async function deleteStylePreset(item) {
  if (!confirm(`删除预设“${item.name}”？`)) return;
  try {
    await request(`/api/style-presets/${encodeURIComponent(item.id)}?model_profile=${encodeURIComponent(currentProfile())}`, { method: "DELETE" });
    await loadStylePresets();
    toast("风格预设已删除");
  } catch (error) {
    toast(error.message);
  }
}
function setPromptRuleError(message = "") {
  ui.promptRuleError.textContent = message;
  ui.promptRuleError.hidden = !message;
}
function clearPromptRuleEditor() {
  promptRuleEditorRevision++;
  editingPromptRule = null;
  ui.promptRuleId.value = "";
  ui.promptRuleFrom.value = "";
  ui.promptRuleTo.value = "";
  ui.promptRulePositive.checked = true;
  ui.promptRuleNegative.checked = true;
  ui.promptRuleLora.checked = false;
  ui.deletePromptRule.hidden = true;
  setPromptRuleError();
}
function editPromptRule(item) {
  if (item.kind !== "custom") return;
  promptRuleEditorRevision++;
  editingPromptRule = item;
  ui.promptRuleId.value = item.id;
  ui.promptRuleFrom.value = item.from || "";
  ui.promptRuleTo.value = item.to || "";
  ui.promptRulePositive.checked = item.scopes?.includes("positive");
  ui.promptRuleNegative.checked = item.scopes?.includes("negative");
  ui.promptRuleLora.checked = item.scopes?.includes("lora");
  ui.deletePromptRule.hidden = false;
  setPromptRuleError();
  ui.promptRuleFrom.focus();
}
function renderPromptRules() {
  const profile = promptRulesProfile;
  if (profile !== currentProfile()) return;
  ui.promptRuleList.replaceChildren(
    ...promptRules.map((item) => {
      const row = document.createElement("div");
      row.className = `prompt-rule-row${item.enabled ? "" : " disabled"}`;
      const description =
      item.kind === "builtin" ?
      item.description :
      `${item.from} → ${item.to} · ${(item.scopes || []).join(" / ")}`;
      const ruleName = item.kind === "builtin" ? item.name : item.from;
      row.innerHTML = `<div class="prompt-rule-copy"><strong>${escapeHtml(ruleName)}</strong><small title="${escapeHtml(description)}">${escapeHtml(description)}</small></div><div class="prompt-rule-actions"><label class="compact-check"><input type="checkbox" ${item.enabled ? "checked" : ""} aria-label="${escapeHtml(ruleName)}：${item.enabled ? "停用" : "启用"}规则"><span>${item.enabled ? "启用" : "停用"}</span></label>${item.kind === "custom" ? '<button class="button ghost compact" type="button">编辑</button>' : ""}</div>`;
      row.querySelector('input[type="checkbox"]').addEventListener("change", async (event) => {
        if (profile !== currentProfile()) return;
        try {
          await request(`/api/prompt-rules/${encodeURIComponent(item.id)}`, {
            method: "PUT",
            body: JSON.stringify({ enabled: event.target.checked, model_profile: profile })
          });
          if (profile === currentProfile()) await loadPromptRules();
        } catch (error) {
          if (profile !== currentProfile()) return;
          toast(error.message);
          await loadPromptRules();
        }
      });
      row.querySelector("button")?.addEventListener("click", () => editPromptRule(item));
      return row;
    })
  );
}
async function loadPromptRules() {
  const profile = currentProfile(),requestId = ++promptRulesRequest;
  const kicker = document.getElementById("promptRuleKicker"),title = document.getElementById("promptRuleTitle");
  if (kicker) kicker.textContent = "ANIMA · TAG RULES";
  if (title) title.textContent = `${"Anima"} 规范与替换`;
  const hint = document.getElementById("promptRuleHint");
  if (hint) hint.textContent = "Anima 按 Tag 规范整理；自定义规则精确匹配单个 Tag，不区分大小写。";
  ui.promptRuleFrom.placeholder = "例如 bad_tag";
  ui.promptRuleTo.placeholder = "例如 good tag";
  if (promptRulesProfile !== profile) {promptRulesProfile = profile;promptRules = [];renderPromptRules();}
  let data;
  try {data = await request(`/api/prompt-rules?model_profile=${encodeURIComponent(profile)}`);}
  catch (error) {
    if (requestId === promptRulesRequest && profile === currentProfile()) setPromptRuleError(error.message);
    return;
  }
  if (requestId !== promptRulesRequest || profile !== currentProfile()) return;
  promptRulesProfile = profile;
  promptRules = data.items || [];
  renderPromptRules();
}
async function openPromptRuleDialog() {
  const profile = currentProfile();
  clearPromptRuleEditor();
  promptRules = [];promptRulesProfile = currentProfile();renderPromptRules();
  motionDialog(ui.promptRuleDialog, true);
  try {
    await loadPromptRules();
  } catch (error) {
    if (profile !== currentProfile()) return;
    setPromptRuleError(error.message);
  }
}
function promptRuleScopes() {
  return [
  ui.promptRulePositive.checked ? "positive" : "",
  ui.promptRuleNegative.checked ? "negative" : "",
  ui.promptRuleLora.checked ? "lora" : ""].
  filter(Boolean);
}
async function savePromptRule(event) {
  event.preventDefault();
  const profile = currentProfile(),editing = editingPromptRule,revision = promptRuleEditorRevision;
  if (promptRulesProfile !== profile) return;
  const scopes = promptRuleScopes();
  if (!scopes.length) {
    setPromptRuleError("至少选择一个应用范围");
    return;
  }
  const payload = {
    from: ui.promptRuleFrom.value,
    to: ui.promptRuleTo.value,
    scopes,
    enabled: true
  };
  try {
    const path = editing ?
    `/api/prompt-rules/${encodeURIComponent(editing.id)}` :
    "/api/prompt-rules";
    await request(path, {
      method: editing ? "PUT" : "POST",
      body: JSON.stringify({ ...payload, model_profile: profile })
    });
    if (profile !== currentProfile() || editing !== editingPromptRule || revision !== promptRuleEditorRevision) return;
    clearPromptRuleEditor();
    await loadPromptRules();
    toast("提示词替换规则已保存");
  } catch (error) {
    if (profile !== currentProfile()) return;
    setPromptRuleError(error.message);
  }
}
async function deletePromptRule() {
  if (!editingPromptRule) return;
  const profile = currentProfile(),editing = editingPromptRule,revision = promptRuleEditorRevision;
  if (promptRulesProfile !== profile) return;
  try {
    await request(`/api/prompt-rules/${encodeURIComponent(editing.id)}?model_profile=${encodeURIComponent(profile)}`, {
      method: "DELETE"
    });
    if (profile !== currentProfile() || editing !== editingPromptRule || revision !== promptRuleEditorRevision) return;
    clearPromptRuleEditor();
    await loadPromptRules();
    toast("提示词替换规则已删除");
  } catch (error) {
    if (profile !== currentProfile()) return;
    setPromptRuleError(error.message);
  }
}

function artistName(value) {
  let name = String(value || "").trim();
  if (name.startsWith("@")) name = name.slice(1).trim();else
  if (name.toLowerCase().startsWith("by ")) name = name.slice(3).trim();
  return name;
}
function artistTokensFrom(value) {
  const values = String(value || "").
  replaceAll("\r", ",").
  replaceAll("\n", ",").
  split(/,|(?=@)/).
  map(artistName).
  filter(Boolean);
  const seen = new Set();
  return values.filter((name) => {
    const key = name.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
function canonicalArtists(value) {
  return artistTokensFrom(value).
  map((name) => `@${name}`).
  join(", ");
}
function artistTokens() {
  return artistTokensFrom(ui.manual_artist.value);
}
function appendArtist(name) {
  const values = artistTokens();
  const keys = new Set(values.map((value) => value.toLowerCase()));
  const clean = artistName(name);
  if (clean && !keys.has(clean.toLowerCase())) values.push(clean);
  ui.manual_artist.value = values.map((value) => `@${value}`).join(", ");
  schedulePersist();
}
function artistEditingValue(value) {
  return String(value || "").
  replaceAll("\r", ",").
  replaceAll("\n", ",").
  split(",").
  map((part) => {
    const leading = part.match(/^\s*/)?.[0] || "";
    let name = part.slice(leading.length);
    if (/^by\s+/i.test(name)) name = name.replace(/^by\s+/i, "@");else
    if (name && !name.startsWith("@")) name = `@${name}`;
    return name;
  }).
  join(", ");
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
async function loadArtistFavorites() {
  const profile = currentProfile();
  const token = (loadArtistFavorites.sequence || 0) + 1;
  loadArtistFavorites.sequence = token;
  if (profile !== "anima") {artistFavoritesData = { groups: [], items: [] };renderArtistFavorites();return;}
  try {
    const payload = await request("/api/favorites/artist?model_profile=anima");
    if (token !== loadArtistFavorites.sequence || profile !== currentProfile()) return;
    artistFavoritesData = payload;
  } catch {
    if (token !== loadArtistFavorites.sequence || profile !== currentProfile()) return;
    artistFavoritesData = { groups: [], items: [] };
  }
  renderArtistFavorites();
}
function renderArtistFavorites() {
  const items = (artistFavoritesData.items || []).filter((item) => item.groupIds?.length);
  ui.artistFavoriteCount.textContent = `${items.length} 位`;
  ui.artistFavorites.replaceChildren(
    ...items.slice(0, 12).map((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "artist-chip";
      button.title = item.nickname ? `追加 @${item.name} · ${item.nickname}` : `追加 @${item.name}`;
      button.innerHTML = `<strong>@${escapeHtml(item.name)}</strong>${item.nickname ? `<small>${escapeHtml(item.nickname)}</small>` : ""}`;
      button.addEventListener("click", () => appendArtist(item.name));
      return button;
    })
  );
  ui.artistFavoriteList.replaceChildren(
    ...(items.length ?
    items.map((item) => {
      const row = document.createElement("div");
      row.className = "artist-favorite-row";
      row.innerHTML = `<button class="artist-favorite-name" type="button" title="追加到固定画师"><strong>@${escapeHtml(item.name)}</strong>${item.nickname ? `<small>${escapeHtml(item.nickname)}</small>` : ""}</button><button class="button ghost compact" type="button">追加</button><button class="icon-button dark" type="button" title="取消收藏" aria-label="取消收藏">×</button>`;
      row.children[0].addEventListener("click", () => appendArtist(item.name));
      row.children[1].addEventListener("click", () => appendArtist(item.name));
      row.children[2].addEventListener("click", () => removeArtistFavorite(item.name));
      return row;
    }) :
    [
    Object.assign(document.createElement("div"), {
      className: "artist-favorite-empty",
      textContent: "还没有收藏画师"
    })])

  );
}
async function openArtistDialog() {
  const profile = currentProfile();
  const epoch = typeof sharedFavoritesState !== "undefined" ? sharedFavoritesState.epoch : 0;
  if (profile !== "anima") return;
  await loadArtistFavorites();
  if (profile !== currentProfile() || typeof sharedFavoritesState !== "undefined" && epoch !== sharedFavoritesState.epoch) return;
  motionDialog(ui.artistDialog, true);
}
async function saveCurrentArtistFavorites() {
  const values = artistTokens();
  if (!values.length) {
    toast("请先输入画师名称");
    return;
  }
  try {
    for (const name of values)
    artistFavoritesData = await request("/api/favorites/artist/item", {
      method: "PUT",
      body: JSON.stringify({ name, favorite: true })
    });
    renderArtistFavorites();
    toast(`已收藏 ${values.length} 位画师`);
  } catch (error) {
    toast(error.message);
  }
}
async function removeArtistFavorite(name) {
  try {
    artistFavoritesData = await request("/api/favorites/artist/item", {
      method: "PUT",
      body: JSON.stringify({ name, favorite: false })
    });
    renderArtistFavorites();
    toast("已取消收藏画师");
  } catch (error) {
    toast(error.message);
  }
}

async function submitBatch(settings, seeds = null) {
  // 统一的批次提交入口:主表单、复现、变体共用;in-flight 守卫防双击重复入队。
  if (submitInFlight) return false;
  settings = clone(settings);
  seeds = seeds ? clone(seeds) : null;
  const classificationIssues = generationClassificationIssues(settings);
  if (classificationIssues.length) {
    updateGenerationAvailability();
    toast(classificationIssues.join("；"));
    return false;
  }
  submitInFlight = true;
  ui.startButton.disabled = true;
  try {
    const body = seeds ? { ...settings, seeds } : settings;
    const state = await request("/api/batches", { method: "POST", body: JSON.stringify(body) });
    if (state.status === "queued") toast(`已加入队列,第 ${state.position} 位`);else
    toast("批次已开始");
    return true;
  } catch (error) {
    toast(error.message);
    await refreshConnection();
    return false;
  } finally {
    try {
      renderBatchPayload(await request("/api/batches/current"));
    } catch {

      // The submission outcome remains valid if its follow-up status read fails.
    } finally {submitInFlight = false;updateGenerationAvailability();
    }
  }
}
ui.settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submitInFlight) return;
  try {
    // Capture the workbench, profile and seeds before the first await. The
    // The backend owns normalization; the submit lock covers the whole request.
    const settings = readSettings();
    const seeds = typeof randomWorkbenchSeeds === "function" ? randomWorkbenchSeeds() : null;
    persistNow();
    await submitBatch(settings, seeds);
  } catch (error) {
    toast(error.message);
  }
});
ui.settingsForm.addEventListener("input", (event) => {
  if (event.target.matches("input, textarea, select")) schedulePersist();
});
ui.settingsForm.addEventListener("change", schedulePersist);
document.addEventListener("input", (event) => {
  if (promptFieldForElement(event.target)) invalidatePromptNormalization();
});
ui.stopButton.addEventListener("click", async () => {
  if (!currentBatch) return;
  try {
    // 停止 = 中断当前批次并清空队列(队列条目可单独用 × 移除)
    renderBatchPayload(await request(`/api/batches/${currentBatch.id}/stop`, { method: "POST" }));
  } catch (error) {
    toast(error.message);
  }
});
ui.queueBadge?.addEventListener("click", () => {
  const open = !ui.queuePanel.classList.contains("open") && currentQueue.length > 0;
  setQueuePanelOpen(open);
  ui.queueBadge.setAttribute("aria-expanded", String(open));
});
ui.reproduceImage?.addEventListener("click", () => openRegenerationDrawer("replay", ui.reproduceImage));
ui.variantButton?.addEventListener("click", () => openRegenerationDrawer("variant", ui.variantButton));
ui.closeReplayDrawer.addEventListener("click", () => closeRegenerationDrawers());
ui.closeVariantDrawer.addEventListener("click", () => closeRegenerationDrawers());
ui.confirmReplay.addEventListener("click", () => submitRegeneration("replay"));
ui.confirmVariant.addEventListener("click", () => {
  const mode = selectedRegenerationMode();
  const count = Math.max(1, Math.min(100, Number(ui.variantDrawerCount.value) || 4));
  submitRegeneration(mode, count, selectedRedrawSections());
});
ui.variantModes.addEventListener("change", updateVariantDrawer);
ui.redrawSections.addEventListener("change", updateVariantDrawer);
for (const input of [ui.variantCount, ui.variantDrawerCount])
input.addEventListener("input", (event) => {
  const count = Math.max(1, Math.min(100, Number(event.target.value) || 4));
  ui.variantCount.value = count;
  ui.variantDrawerCount.value = count;
});
ui.prevPage.addEventListener("click", () => loadHistory(historyPage - 1));
ui.nextPage.addEventListener("click", () => loadHistory(historyPage + 1));
ui.detailImage.addEventListener("click", openImagePreview);
ui.detailImage.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  openImagePreview();
});
ui.closeImagePreview.addEventListener("click", () => closeImagePreview());
ui.imagePreviewPrevious.addEventListener("click", (event) => navigatePreview(-1, event.currentTarget));
ui.imagePreviewNext.addEventListener("click", (event) => navigatePreview(1, event.currentTarget));
ui.imagePreviewImage.addEventListener("pointerdown", imagePreviewPointerDown);
ui.imagePreviewImage.addEventListener("pointermove", imagePreviewPointerMove);
ui.imagePreviewImage.addEventListener("pointerup", imagePreviewPointerUp);
ui.imagePreviewImage.addEventListener("pointercancel", imagePreviewPointerUp);
ui.imagePreviewImage.addEventListener("lostpointercapture", imagePreviewPointerUp);
ui.imagePreviewStage.addEventListener("wheel", imagePreviewWheel, { passive: false });
ui.imagePreviewImage.addEventListener("dblclick", (event) => {
  event.preventDefault();
  toggleImagePreviewZoom(event);
});
ui.imagePreviewVeil.addEventListener("pointerdown", (event) => {
  if (event.button === 0) imagePreview.backdropPointer = { id: event.pointerId, x: event.clientX, y: event.clientY };
});
ui.imagePreviewVeil.addEventListener("pointerup", (event) => {
  const start = imagePreview.backdropPointer;
  imagePreview.backdropPointer = null;
  if (start?.id === event.pointerId && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 6) closeImagePreview();
});
ui.imagePreviewDialog.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeImagePreview();
    return;
  }
  if (event.key === "+" || event.key === "=") {
    event.preventDefault();
    setImagePreviewZoom(imagePreview.zoom + .25, null, true);
  } else if (event.key === "-") {
    event.preventDefault();
    setImagePreviewZoom(imagePreview.zoom - .25, null, true);
  } else if (event.key === "0") {
    event.preventDefault();
    resetImagePreviewTransform();
  } else if (event.key === "ArrowLeft" || event.key === "ArrowRight" || event.key === "ArrowUp" || event.key === "ArrowDown") {
    event.preventDefault();
    if (imagePreview.zoom <= 1.01 && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
      const navigationButton = event.target instanceof Element ? event.target.closest(".image-preview-nav") : null;
      navigatePreview(event.key === "ArrowLeft" ? -1 : 1, navigationButton);
      return;
    }
    cancelImagePreviewAnimation();
    const amount = event.shiftKey ? 80 : 24;
    imagePreview.x += event.key === "ArrowLeft" ? amount * -1 : event.key === "ArrowRight" ? amount : 0;
    imagePreview.y += event.key === "ArrowUp" ? amount * -1 : event.key === "ArrowDown" ? amount : 0;
    scheduleImagePreviewRender();
  }
});
ui.imagePreviewDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeImagePreview();
});
ui.imagePreviewDialog.addEventListener("close", () => {
  if (ui.imagePreviewDialog.open) return;
  // External close (e.g. detail dialog closing) while opening/open: reset now
  // rather than letting a half-finished entry animation keep the veil alive.
  if (imagePreview.phase !== "closed") finishImagePreviewClose(false);
  restoreImagePreviewCloseFocus();
});
ui.detailDialog.addEventListener("close", finishDetailClose);
ui.detailDialog.addEventListener("keydown", containHistoryDetailFocus);
ui.revealImageFile.addEventListener("click", revealSelectedImageFile);
new ResizeObserver(() => {
  if (imagePreview.phase === "open") {
    cancelImagePreviewAnimation();
    imagePreview.pointers.clear();
    imagePreview.gesture = imagePreview.tap = null;
    ui.imagePreviewImage.classList.remove("is-interacting");
    layoutImagePreview();
  } else if (imagePreview.phase === "closing") finishImagePreviewClose();
}).observe(ui.imagePreviewStage);
ui.closeDialog.addEventListener("click", () => {
  if (typeof historyDetailState !== "undefined" && historyDetailState.busy) return;
  if (typeof closeArtworkPopovers === "function") closeArtworkPopovers({ restoreFocus: false, immediate: true });
  invalidateHistoryTransfer();
  closeImagePreview({ immediate: true, restoreFocus: false });
  closeRegenerationDrawers(false);
  motionDialog(ui.detailDialog, false);
});
ui.detailDialog.addEventListener("click", (event) => {
  if (event.target === ui.detailDialog) {
    event.preventDefault(); // This host owns dismissal, including its busy guard.
    if (typeof historyDetailState !== "undefined" && historyDetailState.busy) return;
    if (typeof closeArtworkPopovers === "function" && closeArtworkPopovers({ restoreFocus: true })) return;
    invalidateHistoryTransfer();
    closeImagePreview({ immediate: true, restoreFocus: false });
    closeRegenerationDrawers(false);
    motionDialog(ui.detailDialog, false);
    return;
  }
  if (
  (typeof historyDetailVisible !== "function" || historyDetailVisible()) &&
  !event.target.closest("#detailActionFold, .detail-primary-actions"))
  {
    closeRegenerationDrawers(false);
    closeDetailActionPanels();
  }
});
ui.detailDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  if (typeof historyDetailState !== "undefined" && historyDetailState.busy) return;
  if (typeof closeArtworkPopovers === "function" && closeArtworkPopovers({ restoreFocus: true })) return;
  const inDetail = typeof historyDetailVisible !== "function" || historyDetailVisible();
  if (inDetail && [ui.replayDrawer, ui.variantDrawer].some((drawer) => !drawer.hidden && drawer.dataset?.motionState !== "closing" && !drawer.classList?.contains("closing"))) {
    closeRegenerationDrawers();
    return;
  }
  if (inDetail && closeDetailActionPanels({ restoreFocus: true })) return;
  if (typeof historyDetailBack === "function" && historyDetailBack()) return;
  invalidateHistoryTransfer();
  motionDialog(ui.detailDialog, false);
});
for (const button of document.querySelectorAll("[data-detail-action]")) {
  button.addEventListener("click", () => toggleDetailActionPanel(button.dataset.detailAction));
}
ui.restoreSettings.addEventListener("click", () => {
  if (!selectedRecord) return;
  try {
    if (typeof randomWorkbenchRestoreHistory === "function") randomWorkbenchRestoreHistory(selectedRecord);else
    applySettings(selectedRecord.settings);
    suppressDetailReturnFocus();
    closeImagePreview({ immediate: true, restoreFocus: false });
    motionDialog(ui.detailDialog, false);
    schedulePersist();
    if (typeof revealHistoryWorkbench === "function") revealHistoryWorkbench();
    toast("已载入历史参数，保留生成数量和卡池配置");
  } catch (error) {toast(`载入失败：${error.message}`);}
});
function deleteSelectedHistoryRecord() {
  if (selectedRecord) openHistoryDelete(selectedRecord);
}
ui.deleteRecord.addEventListener("click", deleteSelectedHistoryRecord);
ui.closePool.addEventListener("click", closePool);
ui.confirmPool.addEventListener("click", confirmPoolSelection);
ui.drawerBackdrop.addEventListener("click", closePool);
ui.togglePoolSidebar.addEventListener("click", () =>
setPoolSidebarOpen(!ui.poolSidebar.classList.contains("mobile-open"))
);
matchMedia("(max-width: 620px)").addEventListener("change", () =>
setPoolSidebarOpen(ui.poolSidebar.classList.contains("mobile-open"), { immediate: true })
);
setPoolSidebarOpen(false, { immediate: true });
ui.poolSidebar.addEventListener("click", (event) => {
  if (
  window.innerWidth <= 620 &&
  event.target.closest("button") &&
  !event.target.closest(".facet-heading, .collection-edit, .tree-disclosure"))

  setPoolSidebarOpen(false);
});
ui.poolSidebar.addEventListener("keydown", handleGroupTreeKeydown);
ui.poolSearch.addEventListener("input", () => {
  currentView().query = ui.poolSearch.value;
  poolPage = 1;
  invalidatePoolContext();
  updatePoolSelectionControls();
  clearTimeout(ui.poolSearch._timer);
  ui.poolSearch._timer = setTimeout(loadPool, 220);
  persistPoolView();
});
ui.poolSort.addEventListener("change", () => {
  currentView().sort = ui.poolSort.value;
  poolPage = 1;
  loadPool();
  persistPoolView();
});
ui.poolLanguage.addEventListener("change", () => {
  currentView().language = ui.poolLanguage.value;
  renderPoolItems();
  renderPoolSidebar(); // 侧栏分类标签同步切换语言
  persistPoolView();
});
ui.selectedOnly.addEventListener("change", () => {
  currentView().selectedOnly = ui.selectedOnly.checked;
  poolPage = 1;
  loadPool();
  persistPoolView();
});
ui.clearFilters.addEventListener("click", () => {
  const language = currentView().language;
  const picker = characterPickerSession();
  if (picker) picker.view = { ...defaultView(), language };else
  poolViews[activeSection] = { ...defaultView(), language };
  ui.poolSearch.value = "";
  ui.poolSort.value = "";
  ui.selectedOnly.checked = false;
  poolPage = 1;
  loadPool();
  persistPoolView();
});
ui.poolPrev.addEventListener("click", () => {
  poolPage -= 1;
  loadPool();
});
ui.poolNext.addEventListener("click", () => {
  poolPage += 1;
  loadPool();
});
ui.selectPage.addEventListener("click", () => selectCurrentPage(true));
ui.selectFilteredPool.addEventListener("click", selectFilteredPool);
ui.selectAllPool.addEventListener("click", useEntirePool);
ui.clearPool.addEventListener("click", clearPoolSelection);
ui.poolMoreSelection.querySelector("summary").setAttribute("aria-expanded", "false");
ui.poolMoreSelection.querySelector("summary").addEventListener("click", (event) => {
  event.preventDefault(); // Own this anchored popover; generic details animate height.
  setPoolMoreSelection(event.currentTarget.getAttribute("aria-expanded") !== "true");
});
document.addEventListener("click", (event) => {
  if (ui.poolMoreSelection.open && !ui.poolMoreSelection.contains(event.target)) setPoolMoreSelection(false);
});
ui.poolMoreSelection.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || ui.poolMoreSelection.querySelector("summary").getAttribute("aria-expanded") !== "true") return;
  event.preventDefault();
  event.stopPropagation();
  setPoolMoreSelection(false, { restoreFocus: true });
});
ui.favoriteSelection.addEventListener("click", openFavoriteSelectionDialog);
ui.favoriteSelectionForm.addEventListener("submit", saveFavoriteSelection);
ui.createFavoriteSelectionGroup.addEventListener("click", createFavoriteSelectionGroup);
ui.closeFavoriteSelection.addEventListener("click", () => motionDialog(ui.favoriteSelectionDialog, false));
ui.cancelFavoriteSelection.addEventListener("click", () => motionDialog(ui.favoriteSelectionDialog, false));
ui.addCustom.addEventListener("click", () => openCustom());
ui.importCustom.addEventListener("click", openImportDialog);
ui.customForm.addEventListener("submit", saveCustom);
ui.cancelCustom.addEventListener("click", () => motionDialog(ui.customDialog, false));
ui.closeCustom.addEventListener("click", () => motionDialog(ui.customDialog, false));
ui.deleteCustom.addEventListener("click", deleteCustomItem);
ui.customGroupForm.addEventListener("submit", saveCustomGroup);
ui.deleteCustomGroup.addEventListener("click", removeCustomGroup);
ui.closeCustomGroup.addEventListener("click", () => motionDialog(ui.customGroupDialog, false));
ui.cancelCustomGroup.addEventListener("click", () => motionDialog(ui.customGroupDialog, false));
ui.importFile.addEventListener("change", previewImportFile);
ui.importBundleName.addEventListener("input", () => {
  if (importPreview) renderImportPreview();
});
ui.commitImport.addEventListener("click", commitCustomImport);
ui.closeImport.addEventListener("click", () => motionDialog(ui.importDialog, false));
ui.cancelImport.addEventListener("click", () => motionDialog(ui.importDialog, false));
ui.manageArtists.addEventListener("click", openArtistDialog);
ui.saveCurrentArtists.addEventListener("click", saveCurrentArtistFavorites);
ui.closeArtists.addEventListener("click", () => motionDialog(ui.artistDialog, false));
ui.cancelArtists.addEventListener("click", () => motionDialog(ui.artistDialog, false));
ui.savePreset.addEventListener("click", createStylePreset);
ui.presetName.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    createStylePreset();
  }
});
ui.presetName.addEventListener("input", () => setPresetFeedback());
ui.manual_artist.addEventListener("input", () => {
  normalizeArtistField(false);
  schedulePersist();
});
ui.manual_artist.addEventListener("paste", () =>
queueMicrotask(() => {
  normalizeArtistField(true);
  schedulePersist();
})
);
ui.manual_artist.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    normalizeArtistField(true);
    schedulePersist();
  }
});
ui.manual_artist.addEventListener("blur", () => {
  normalizeArtistField(true);
  schedulePersist();
  normalizePromptElement(ui.manual_artist);
});
for (const input of [ui.quality_prompt, ui.extra_prompt, ui.negative_prompt, ui.customPrompt])
input.addEventListener("blur", () => normalizePromptElement(input));
ui.undoPromptNormalization.addEventListener("click", undoPromptNormalization);
ui.managePromptRules.addEventListener("click", openPromptRuleDialog);
ui.promptRuleForm.addEventListener("submit", savePromptRule);
ui.promptRuleForm.addEventListener("input", () => {promptRuleEditorRevision++;});
ui.clearPromptRule.addEventListener("click", clearPromptRuleEditor);
ui.deletePromptRule.addEventListener("click", deletePromptRule);
ui.closePromptRules.addEventListener("click", () => motionDialog(ui.promptRuleDialog, false));
ui.cancelPromptRules.addEventListener("click", () => motionDialog(ui.promptRuleDialog, false));
ui.model_profile.addEventListener("change", () => {
  const next = normalizeProfileId(ui.model_profile.value);

  switchProfileDraft(next);
  updateProfileResources();
  updateRepairControls();
  schedulePersist();
  void refreshProfileData();
});
ui.model_name.addEventListener("change", () => {
  saveCurrentProfileDraft();
  updateRepairControls();
  schedulePersist();
});
ui.manageResourceOwnership.addEventListener("click", async () => {
  motionDialog(ui.resourceOwnershipDialog, true);
  await loadResourceOwnership();
});
ui.closeResourceOwnership.addEventListener("click", () => motionDialog(ui.resourceOwnershipDialog, false));
ui.closeResourceOwnershipFooter.addEventListener("click", () => motionDialog(ui.resourceOwnershipDialog, false));
ui.ownershipKind.addEventListener("change", loadResourceOwnership);
ui.ownershipSearch.addEventListener("input", renderResourceOwnership);
ui.refreshResourceClassification.addEventListener("click", refreshResourceClassification);

for (const control of [
ui.pixel_hires_enabled, ui.pixel_hires_model_name, ui.pixel_hires_percent, ui.pixel_hires_upscale_method, ui.dlss_enabled,
ui.latent_second_sampler_enabled,
ui.base_mode,
ui.latent_upscale_enabled,
ui.latent_upscale_scale,
ui.latent_upscale_method,
ui.latent_model_format,
...Object.values(POST_SAMPLER_IDS).map((id) => ui[id]),
...Object.values(LATENT_SAMPLER_IDS).map((id) => ui[id]),
ui.seedvr2_enabled,
ui.seedvr2_model_name,
ui.seedvr2_vae_name,
ui.seedvr2_weight_dtype,
ui.seedvr2_scale_by,
ui.seedvr2_color_correction_method,
...Object.values(SEED_SAMPLER_IDS).map((id) => ui[id]),
...Object.values(SEED_TILE_IDS.encode).map((id) => ui[id]),
...Object.values(SEED_TILE_IDS.decode).map((id) => ui[id]),
ui.nvidia_upscale_enabled,
ui.nvidia_resize_type,
ui.nvidia_scale,
ui.nvidia_quality,
ui.nvidia_dlss_style, ui.nvidia_dlss_intensity, ui.nvidia_dlss_local_tone, ui.nvidia_dlss_local_struct, ui.nvidia_dlss_output_mix,
ui.face_refine_enabled,
...Object.values(UPSCALER_CONTROL_IDS.face_refine).map((id) => ui[id])])
{
  control.addEventListener("change", () => {
    updateRepairControls();
    schedulePersist();
  });
}
ui.themeToggle.addEventListener("click", () =>
applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true)
);
ui.favoriteForm.addEventListener("submit", saveFavorite);
ui.removeFavorite.addEventListener("click", removeFavorite);
ui.closeFavorite.addEventListener("click", () => motionDialog(ui.favoriteDialog, false));
ui.cancelFavorite.addEventListener("click", () => motionDialog(ui.favoriteDialog, false));
ui.groupForm.addEventListener("submit", saveGroup);
ui.deleteGroup.addEventListener("click", deleteFavoriteGroup);
ui.closeGroup.addEventListener("click", () => motionDialog(ui.groupDialog, false));
ui.cancelGroup.addEventListener("click", () => motionDialog(ui.groupDialog, false));
ui.importChildGroup.addEventListener("click", () => openChildGroupDialog());
ui.childGroupForm.addEventListener("submit", importFavoriteChild);
ui.childGroupSearch.addEventListener("input", () => {
  // 与池搜索一致的去抖,避免每次击键全量重建列表。
  clearTimeout(ui.childGroupSearch._timer);
  ui.childGroupSearch._timer = setTimeout(renderChildGroupSources, 220);
});
ui.closeChildGroup.addEventListener("click", () => motionDialog(ui.childGroupDialog, false));
ui.cancelChildGroup.addEventListener("click", () => motionDialog(ui.childGroupDialog, false));
ui.deleteGroupForm.addEventListener("submit", confirmGroupDelete);
ui.closeDeleteGroup.addEventListener("click", () => motionDialog(ui.deleteGroupDialog, false));
ui.cancelDeleteGroup.addEventListener("click", () => motionDialog(ui.deleteGroupDialog, false));
ui.addLora.addEventListener("click", openLoraDialog);
ui.resetLoras.addEventListener("click", () => {
  draft.loras = defaultLorasForProfile(currentProfile());
  reconcileLoraTriggers(draft.loras.filter((item) => item.enabled));
  renderLoras();
  schedulePersist();
  toast("已恢复模板默认 LoRA");
});
ui.closeLora.addEventListener("click", () => motionDialog(ui.loraDialog, false));
ui.cancelLora.addEventListener("click", () => motionDialog(ui.loraDialog, false));
ui.loraTriggerForm.addEventListener("submit", saveLoraTriggers);
ui.resetLoraTriggers.addEventListener("click", resetLoraTriggers);
ui.closeLoraTriggers.addEventListener("click", () => motionDialog(ui.loraTriggerDialog, false));
ui.cancelLoraTriggers.addEventListener("click", () => motionDialog(ui.loraTriggerDialog, false));
ui.loraSearch.addEventListener("input", () => {
  clearTimeout(ui.loraSearch._timer);
  ui.loraSearch._timer = setTimeout(renderLoraCatalog, 220);
});
ui.extra_prompt.addEventListener("input", forgetRemovedManagedTriggers);
ui.female_count.addEventListener("input", updatePeopleTotal);
ui.male_count.addEventListener("input", updatePeopleTotal);
ui.resetSettings.addEventListener("click", () => {
  if (!confirm("恢复全部模板默认设置？")) return;
  const profile = currentProfile();

  applySettings(typeof randomWorkbenchDefaultSettings === "function" ? randomWorkbenchDefaultSettings(profile) : { ...profileSettingsDefaults(profile), model_profile: profile });
  schedulePersist();
  toast("已恢复当前模型档案默认设置");
});
ui.clearDraft.addEventListener("click", () => {
  if (!confirm("清除本地草稿并恢复默认设置？收藏不会被删除。")) return;
  localStorage.removeItem(DRAFT_KEY);

  localStorage.removeItem(VIEW_KEY);
  poolViews = Object.fromEntries(SECTIONS.map((section) => [section, defaultView()]));
  applySettings(typeof randomWorkbenchDefaultSettings === "function" ? randomWorkbenchDefaultSettings(defaults.model_profile) : defaults);
  ui.draftStatus.textContent = "本地草稿已清除";
  toast("已清除本地草稿");
});
document.querySelectorAll(".mobile-tabs button[data-mobile-view]").forEach((button) =>
button.addEventListener("click", () => {
  const view = button.dataset.mobileView;
  document.
  querySelectorAll(".mobile-tabs button").
  forEach((item) => item.classList.toggle("active", item === button));
  document.querySelector(".workspace").dataset.mobileView = view;
})
);
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (ui.poolDrawer.classList.contains("open")) closePool();
});

async function initialize() {
  applyTheme(initialTheme());
  try {
    const payload = await request("/api/config");
    config = payload;
    defaults = payload.defaults;
    renderStorageStatus();
    // 启动期数据文件恢复等服务端警告(如坏文件已备份重建)逐条提示。
    for (const warning of payload.warnings || []) toast(warning);
    loadStoredView();
    const restored = loadStoredDraft();
    if (!restored) {
      applySettings(typeof randomWorkbenchDefaultSettings === "function" ? randomWorkbenchDefaultSettings(defaults.model_profile) : defaults);
      ui.draftStatus.textContent = "使用模板默认设置";
    }

    if (typeof initializeHistoryDetail === "function") initializeHistoryDetail();
    if (typeof initializeArtworkFavorites === "function") initializeArtworkFavorites();
    initializeRandomWorkbench();
    initializePoolLifecycle();
    initializeLoraManager();
    if (typeof StudioMotion !== "undefined") {
      StudioMotion.bindDisclosures(document);
      StudioMotion.bindDialogs(document);
    }
    initialized = true;
  } catch (error) {
    const message = `界面初始化失败：${error.message}`;
    ui.appError.textContent = message;
    ui.appError.hidden = false;
    toast(message);
    return;
  }
  // Resource inventories can be slow; progress monitoring must already work.
  setInterval(pollBatch, 400);
  setInterval(refreshConnection, 5000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      pollIdleTicks = 0;
      pollBatch();
      refreshConnection();
    }
  });
  await Promise.allSettled([
  loadResources(),

  refreshConnection(),
  pollBatch(),
  loadHistory(1),
  loadLoraInventory(),
  loadArtistFavorites(),
  loadStylePresets()]
  );
}
initialize();
