/* Resource choices and generation readiness share the server's effective settings. */
const resourceResolutionState = { serial: 0, inventory: 0, timer: null, key: "", pending: null, result: null, notices: new Set() };
const RESOURCE_MODULE_LABELS = { base: "底图", base_dual: "双采样器", pixel_hires: "像素高清", latent: "潜空间放大", post_sampler: "整图后采样", face_refine: "脸部细化", seedvr2: "SeedVR2", nvidia: "N 卡 VSR", dlss: "DLSS", loras: "LoRA", connection: "连接" };
const RESOURCE_CONTROL_PATHS = { model_name: "model_name", clip_name: "clip_name", vae_name: "vae_name", "upscalers.pixel_hires.model_name": "pixel_hires_model_name", "upscalers.seedvr2.model_name": "seedvr2_model_name", "upscalers.seedvr2.vae_name": "seedvr2_vae_name", "upscalers.face_refine.upscaler_model": "face_upscaler_model" };
function resourceResolutionKey(settings) {
  const post = JSON.parse(JSON.stringify(settings.upscalers || {}));
  for (const key of ["face_positive_prompt", "face_negative_prompt"]) if (post.face_refine) delete post.face_refine[key];
  const relevant = Object.fromEntries(["workbench", "model_profile", "pipeline_version", "base_mode", "generation_mode",
    "model_name", "clip_name", "vae_name", "clip_type", "sampler_name", "scheduler", "steps", "cfg", "base_denoise",
    "zero_negative", "dual", "loras"].map(key => [key, settings[key]]));
  relevant.upscalers = post;
  return `${resourceResolutionState.inventory}:${JSON.stringify(relevant)}`;
}
function invalidateResourceResolution() {
  clearTimeout(resourceResolutionState.timer);
  resourceResolutionState.serial++;
  resourceResolutionState.inventory++;
  resourceResolutionState.key = "";
  resourceResolutionState.pending = null;
  resourceResolutionState.result = null;
}
function resourceCatalogChoices(role, fallback = []) {
  if (!Array.isArray(resources.resource_catalog)) return fallback;
  return resources.resource_catalog.filter(item => item.status === "recognized" && item.roles?.includes(role)).map(item => item.name);
}
function populateCompatibleResources() {
  const profile = currentProfile();
  for (const [id, role, fallback, label] of [
    ["clip_name", `${profile}.clip`, resources.clips || [], "文本编码器"],
    ["vae_name", `${profile}.vae`, resources.vaes || [], "VAE"],
    ["seedvr2_model_name", "seedvr2.model", resources.seedvr2_models || [], "SeedVR2 模型"],
    ["seedvr2_vae_name", "seedvr2.vae", resources.seedvr2_vaes || [], "SeedVR2 VAE"],
    ["pixel_hires_model_name", "upscale", resources.upscale_models || [], "像素放大模型"],
  ]) {
    if (!ui[id]) continue;
    const choices = resourceCatalogChoices(role, fallback);
    // Older ComfyUI installs may expose codec inventory without the optional
    // inspection extension. Keep an explicit existing codec usable; do not
    // promote other unknown files into compatible automatic choices.
    const selected = ui[id].value;
    const samePath = (a, b) => String(a).replaceAll("\\", "/").toLocaleLowerCase() === String(b).replaceAll("\\", "/").toLocaleLowerCase();
    const exact = fallback.find(name => samePath(name, selected));
    const kind = role.endsWith(".clip") ? "clips" : role.endsWith(".vae") ? "vaes" : null;
    const entry = Array.isArray(resources.resource_catalog) ? resources.resource_catalog.find(item => item.kind === kind && samePath(item.name, selected)) : null;
    if ((role.endsWith(".clip") || role.endsWith(".vae")) && exact && (!entry || entry.status === "unknown") && !choices.includes(exact)) choices.push(exact);
    populateResourceSelect(ui[id], choices, resolveInventoryPath(ui[id].value, choices), `没有可用${label}`);
  }
  if (ui.face_upscaler_model && Array.isArray(resources.resource_catalog)) {
    const spec = resources.node_inputs?.ForbiddenVisionFaceProcessorIntegrated?.upscaler_model;
    const builtins = (Array.isArray(spec?.[0]) ? spec[0] : []).filter(name => String(name).startsWith("Fast "));
    const choices = [...new Set([...builtins, ...resourceCatalogChoices("upscale")])];
    populateResourceSelect(ui.face_upscaler_model, choices, ui.face_upscaler_model.value, "没有可用脸部放大方式");
  }
}
function resourceModuleEnabled(module, settings) {
  if (["base", "connection", "loras"].includes(module)) return true;
  if (module === "base_dual") return settings.base_mode === "dual_sampler";
  const post = settings.upscalers || {};
  return Boolean(post[module]?.enabled);
}
function resourceWarningTarget(module) {
  if (module === "base" || module === "connection") return ui.resourceWarning;
  const id = `resourceIssue_${module}`;
  let target = document.getElementById(id);
  if (target) return target;
  const stage = { face_refine: "face" }[module] || module;
  const parent = module === "base_dual" ? document.getElementById("randomDualSection")
    : module === "loras" ? ui.loraList?.parentElement : document.querySelector(`.sampling-stage-${stage}`);
  if (!parent) return ui.resourceWarning;
  target = document.createElement("div");
  target.id = id; target.className = "resource-warning module-resource-warning";
  target.setAttribute("role", "status"); target.hidden = true;
  parent.append(target);
  return target;
}
function renderResourceResolution(settings = readSettings()) {
  const state = resourceResolutionState;
  const result = state.key === resourceResolutionKey(settings) ? state.result : null;
  const issues = result?.issues || (resourceError ? [{ module: "connection", message: resourceError }] : []);
  const grouped = new Map();
  for (const module of Object.keys(RESOURCE_MODULE_LABELS)) {
    const target = resourceWarningTarget(module);
    if (target) grouped.set(target, []);
  }
  for (const issue of issues) {
    const module = issue.module || "base";
    if (!resourceModuleEnabled(module, settings)) continue;
    const target = resourceWarningTarget(module);
    if (target) { if (!grouped.has(target)) grouped.set(target, []); grouped.get(target).push(issue.message || `${RESOURCE_MODULE_LABELS[module] || module}资源不可用`); }
  }
  for (const [target, messages] of grouped) {
    target.hidden = messages.length === 0;
    target.textContent = [...new Set(messages)].join("；");
  }
}
function resourceResolutionIssues(settings) {
  if (!resourcesLoaded || resourceInventoryProfile !== currentProfile()) return [resourceError || "正在读取模型资源"];
  const state = resourceResolutionState;
  if (state.key !== resourceResolutionKey(settings) || !state.result) return ["正在检查本次生成资源…"];
  const messages = (state.result.issues || []).filter(issue => resourceModuleEnabled(issue.module || "base", settings)).map(issue => issue.message || "生成资源不可用");
  if (!state.result.ready && !messages.length) messages.push("当前生成资源不可用，请刷新资源清单");
  return [...new Set(messages)];
}
function physicalResourceReplacements(replacements = []) {
  return replacements.filter(item => item.reason !== "path_normalized");
}
function resourceSelectionChanged(resolution) {
  return typeof resolution?.resourceChanged === "boolean" ? resolution.resourceChanged : physicalResourceReplacements(resolution?.replacements || []).length > 0;
}
function resourceReplacementText(replacements = []) {
  return physicalResourceReplacements(replacements).map(item => `${RESOURCE_MODULE_LABELS[item.module] || "模型"}：${item.from || "未选择"} → ${item.to}`).join("；");
}
function notifyResourceReplacements(replacements, settings) {
  const fresh = physicalResourceReplacements(replacements).filter(item => {
    const key = JSON.stringify([settings.workbench || "random", settings.model_profile, item.field, item.from, item.to]);
    if (resourceResolutionState.notices.has(key)) return false;
    resourceResolutionState.notices.add(key); return true;
  });
  if (fresh.length) toast(`已选择兼容模型：${resourceReplacementText(fresh)}`);
}
function mergeResourceReplacements(previous = [], changes = []) {
  const records = new Map(previous.map(item => [item.field, { ...item }]));
  for (const item of changes) {
    const prior = records.get(item.field);
    const merged = { ...item, from: prior?.from ?? item.from };
    if (item.reason === "path_normalized" && prior && prior.reason !== "path_normalized") {
      if (prior.reason) merged.reason = prior.reason;
      else delete merged.reason; // Older drafts without a reason still describe real replacements.
    }
    records.set(item.field, merged);
  }
  return [...records.values()].filter(item => item.from !== item.to);
}
function clearDraftResourceReplacement(controlId) {
  const field = Object.keys(RESOURCE_CONTROL_PATHS).find(path => RESOURCE_CONTROL_PATHS[path] === controlId);
  if (!field || typeof pipelineContractState === "undefined") return;
  pipelineContractState.source.resource_replacements = (pipelineContractState.source.resource_replacements || []).filter(item => item.field !== field);
}
function resourceResolutionRequestSettings(settings) {
  const result = { ...settings };
  delete result.resource_replacements;
  return result;
}
function applyResourceReplacements(result, settings) {
  const replacements = (result.replacements || []).filter(item => resourceModuleEnabled(item.module || "base", settings));
  for (const item of replacements) {
    const id = RESOURCE_CONTROL_PATHS[item.field];
    if (id && ui[id]) ensureSelectValue(ui[id], item.to);
    if (item.field === "clip_type" && typeof pipelineContractState !== "undefined") pipelineContractState.source.clip_type = item.to;
  }
  if (replacements.length) {
    if (typeof pipelineContractState !== "undefined") pipelineContractState.source.resource_replacements = mergeResourceReplacements(settings.resource_replacements, replacements);
    saveCurrentProfileDraft();
    schedulePersist();
    notifyResourceReplacements(replacements, settings);
  }
}
function scheduleResourceResolution({ immediate = false } = {}) {
  if (!resourcesLoaded || resourceInventoryProfile !== currentProfile()) { renderResourceResolution(); return; }
  const settings = readSettings(), key = resourceResolutionKey(settings), state = resourceResolutionState;
  if (key === state.key && (state.result || state.pending)) { renderResourceResolution(settings); return; }
  clearTimeout(state.timer);
  state.serial++;
  state.key = key; state.result = null; state.pending = null;
  renderResourceResolution(settings);
  const serial = state.serial;
  const run = async () => {
    if (serial !== state.serial) return;
    const pending = request("/api/resources/resolve", { method: "POST", body: JSON.stringify({ settings: resourceResolutionRequestSettings(settings) }) });
    state.pending = pending;
    try {
      const result = await pending;
      if (serial !== state.serial || key !== resourceResolutionKey(readSettings())) return;
      applyResourceReplacements(result, settings);
      state.key = resourceResolutionKey(readSettings());
      state.result = result;
    } catch (error) {
      if (serial !== state.serial || key !== resourceResolutionKey(readSettings())) return;
      state.result = { ready: false, issues: [{ module: "connection", message: `资源检查失败：${error.message}` }] };
    } finally {
      if (serial === state.serial) {
        state.pending = null;
        renderResourceResolution();
        updateGenerationAvailability();
      }
    }
  };
  if (immediate) void run(); else state.timer = setTimeout(run, 160);
}
async function resolveSubmissionResources(settings) {
  const key = resourceResolutionKey(settings);
  const result = await request("/api/resources/resolve", { method: "POST", body: JSON.stringify({ settings: resourceResolutionRequestSettings(settings) }) });
  if (key === resourceResolutionKey(readSettings())) {
    applyResourceReplacements(result, settings);
    resourceResolutionState.key = resourceResolutionKey(readSettings());
    resourceResolutionState.result = result;
    renderResourceResolution();
  }
  if (!result.ready) throw new Error((result.issues || []).map(item => item.message).join("；") || "当前生成资源不可用");
  return { ...result.effective_settings, resource_replacements: mergeResourceReplacements(settings.resource_replacements, result.replacements)
    .filter(item => resourceModuleEnabled(item.module || "base", settings)) };
}
function acceptSubmittedResourceResolution(state, submittedSettings, submittedKey) {
  const resolution = state.resource_resolution;
  if (!resolution?.replacements?.length) return false;
  const current = readSettings();
  if (submittedKey !== resourceResolutionKey(current)) {
    notifyResourceReplacements(resolution.replacements, submittedSettings);
    return false;
  }
  // Admission may choose a newer fallback than the editor's preflight did.
  // Retire older checks and update only resources in the matching draft.
  clearTimeout(resourceResolutionState.timer);
  resourceResolutionState.serial++;
  resourceResolutionState.pending = null;
  applyResourceReplacements(resolution, current);
  resourceResolutionState.key = resourceResolutionKey(readSettings());
  resourceResolutionState.result = { ...resolution, effective_settings: readSettings(), issues: [], ready: true };
  renderResourceResolution();
  return true;
}
function renderHistoricalResourceChanges(element, mode) {
  if (["replay", "prompt_variant"].includes(mode)) return;
  const resolution = regenerationOptions?.modes?.[mode];
  if (!resourceSelectionChanged(resolution) || !element) return;
  const changes = physicalResourceReplacements(resolution?.replacements || []);
  const note = document.createElement("span");
  note.className = "resource-replacement-note";
  note.textContent = `本次模型已变更，不属于严格复现。${changes.length ? `将使用兼容模型：${resourceReplacementText(changes)}` : ""}`;
  element.append(note);
}
function updateHistoricalResourceLabels() {
  const changed = mode => resourceSelectionChanged(regenerationOptions?.modes?.[mode]);
  const replayChanged = false;
  ui.confirmReplay.textContent = replayChanged ? "使用兼容模型按原种子重跑" : "按原种子重跑";
  const title = document.getElementById("replayDrawerTitle");
  if (title) title.textContent = replayChanged ? "按原种子重跑 · 非严格复现" : "按原种子重跑";
  const description = ui.replayDrawer?.querySelector("p");
  if (description) description.textContent = replayChanged
    ? "冻结历史最终提示词与双种子；缺失模型改用下方列出的兼容模型，本次不是严格复现。"
    : "冻结历史最终提示词，沿用原设置与双种子。资源文件内容被覆盖时无法识别，不承诺像素级一致。";
  for (const [mode, original, replacement] of [
    ["prompt_variant", "提示词不变，只换图像种子", "提示词不变，换图像种子；本次也替换模型"],
    ["content_redraw", "只改变勾选的内容维度", "重抽勾选内容；本次也替换模型"],
    ["settings_reroll", "候选池和设置不变，全部重新随机", "沿用配置重新随机；本次也替换模型"],
  ]) {
    const text = ui.variantModes?.querySelector(`input[value="${mode}"]`)?.closest("label")?.querySelector("small");
    if (text) text.textContent = mode !== "prompt_variant" && changed(mode) ? replacement : original;
  }
}
function resourceResolutionRecordMarkup(record) {
  const resolution = record.bound_resources?.resource_resolution;
  if (!resourceSelectionChanged(resolution)) return "";
  const replacements = physicalResourceReplacements(resolution?.replacements || []);
  if (!replacements.length) return "";
  const items = replacements.map(item => `<li><strong>${escapeHtml(RESOURCE_MODULE_LABELS[item.module] || "模型")}</strong>：${escapeHtml(item.from || "未选择")} → ${escapeHtml(item.to)}</li>`).join("");
  return `<details class="lora-runtime-details resource-runtime-details"><summary>本张实际使用的替代模型</summary><ul>${items}</ul><small>任务入队时已固定模型；本记录保留实际使用的资源。</small></details>`;
}
function resourceUsageRoles(kind) {
  return { models: [["anima.model", "Anima 主模型"], ["seedvr2.model", "SeedVR2 模型"]],
    clips: [["anima.clip", "Anima 文本编码器"]],
    vaes: [["anima.vae", "Anima VAE"], ["seedvr2.vae", "SeedVR2 VAE"]],
    upscale_models: [["upscale", "像素 / 脸部放大模型"]] }[kind] || [];
}
function renderResourceUsageItem(item) {
  const row = document.createElement("article"); row.className = "ownership-row";
  const copy = document.createElement("div"); copy.className = "ownership-row-copy";
  const name = document.createElement("strong"); name.textContent = item.name.replaceAll("\\", "/").split("/").pop();
  const path = document.createElement("small"); path.textContent = item.name; path.title = item.name;
  const reason = document.createElement("small"); reason.className = "ownership-reason";
  const roleLabels = new Map(resourceUsageRoles(item.kind));
  reason.textContent = item.status === "recognized" ? `用途：${(item.roles || []).map(role => roleLabels.get(role) || role).join("、")}`
    : item.status === "unknown" ? (item.fingerprint ? "尚未识别用途；确认模型兼容后可在这里指定。" : "尚未读取文件身份，请刷新资源清单后再指定用途。") : item.status === "invalid" ? "文件已变化，请刷新清单后重新检查用途。" : "不兼容当前支持的模型架构。";
  copy.append(name, path, reason); row.append(copy);
  const actions = document.createElement("div"); actions.className = "ownership-metadata";
  const manual = ["manual", "user", "user_assignment", "usage_override"].includes(item.source);
  if (item.status === "unknown" || manual) {
    const select = document.createElement("select"); select.setAttribute("aria-label", `${name.textContent} 的用途`);
    select.append(new Option("选择模型用途", ""), ...resourceUsageRoles(item.kind).map(([value, label]) => new Option(label, value)));
    select.value = item.roles?.length > 1 ? [...item.roles].sort().join(",") : item.roles?.[0] || "";
    const save = document.createElement("button"); save.type = "button"; save.className = "button ghost compact"; save.textContent = "保存用途";
    save.disabled = !select.value || !item.fingerprint; select.addEventListener("change", () => { save.disabled = !select.value || !item.fingerprint; });
    save.addEventListener("click", () => void saveResourceUsage(item, select.value.split(","), row));
    actions.append(select, save);
  }
  if (manual || item.status === "invalid") {
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "button ghost compact"; remove.textContent = "撤销指定";
    remove.addEventListener("click", () => void saveResourceUsage(item, null, row)); actions.append(remove);
  }
  row.append(actions); return row;
}
async function saveResourceUsage(item, roles, row) {
  const owner = { kind: ui.ownershipKind.value, request: ownershipRequest };
  row.querySelectorAll("button, select").forEach(element => { element.disabled = true; });
  try {
    await request("/api/resource-usages", { method: roles ? "PUT" : "DELETE", body: JSON.stringify({ kind: item.kind, name: item.name, ...(roles ? { roles, fingerprint: item.fingerprint } : {}) }) });
    await refreshResourceClassification();
    toast(roles ? "模型用途已保存" : "已撤销指定用途");
  } catch (error) {
    if (owner.kind === ui.ownershipKind.value && owner.request === ownershipRequest) { ui.ownershipError.hidden = false; ui.ownershipError.textContent = error.message; }
    row.querySelectorAll("button, select").forEach(element => { element.disabled = false; });
  }
}
