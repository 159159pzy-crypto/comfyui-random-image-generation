/* Shared parameter behavior; stored workbench and sampling-mode drafts remain independent. */
const RANDOM_DUAL_KEYS = ["switch_step", "scale", "second_cfg", "second_sampler_name", "second_scheduler", "upscale_method", "model_format", "half_precision"];
const NVIDIA_DLSS_KEYS = ["style", "intensity", "local_tone", "local_struct", "output_mix"];
const sharedParameterState = { latent: { model_format: "Wan 2.1", half_precision: true }, dual: {}, dualLegacy: false, dualInitial: null, dlss: {}, modes: {}, activeMode: null, applying: false };
function randomDualDefaults() {
  // Historical partial snapshots keep their original second-pass settings.
  return { switch_step: 15, scale: 1, second_cfg: 8, second_sampler_name: "euler", second_scheduler: "simple", upscale_method: "sesqui", model_format: "Wan 2.1", half_precision: true };
}
function sharedSamplingDefaults(mode = "normal", profile = typeof currentProfile === "function" ? currentProfile() : "anima") {
  const bound = typeof profileConfig === "function" ? profileConfig(profile) : {};
  const settings = typeof profileSettingsDefaults === "function" ? profileSettingsDefaults(profile) : {};
  const sampling = { ...settings, ...bound.defaults };
  return { steps: sampling.steps, cfg: sampling.cfg, sampler_name: sampling.sampler_name, scheduler: sampling.scheduler,
    base_denoise: settings.base_denoise ?? 1,
    dual: { ...randomDualDefaults(), ...settings.dual, ...bound.dual_defaults }, seedMode: "fixed", seed: "0" };
}
function sharedUpscalerDefaults(profile, settings) {
  const bound = typeof profileConfig === "function" ? profileConfig(profile) : {};
  const upscalers = normalizeUpscalers(settings, null, profile, 5);
  Object.assign(upscalers.post_sampler, bound.post_sampler_defaults);
  Object.assign(upscalers.latent.sampler, bound.latent_sampler_defaults);
  Object.assign(upscalers.face_refine, bound.face_refine_defaults);
  return upscalers;
}
function randomDualFromSettings(settings = {}) {
  const explicit = settings.dual;
  if (explicit && typeof explicit === "object") return Object.fromEntries(RANDOM_DUAL_KEYS.map((key) => [key, explicit[key] ?? randomDualDefaults()[key]]));
  return { ...randomDualDefaults(), switch_step: 15, scale: settings.upscalers?.latent?.scale_by ?? 1.5,
    second_cfg: settings.cfg ?? 4, second_sampler_name: settings.sampler_name || "er_sde",
    second_scheduler: settings.scheduler || "simple", upscale_method: settings.upscalers?.latent?.upscale_method || "bilinear",
    model_format: settings.upscalers?.latent?.model_format || "Wan 2.1", half_precision: settings.upscalers?.latent?.half_precision ?? true };
}
function randomWorkbenchDefaultSettings(profile = "anima") {
  const settings = JSON.parse(JSON.stringify(profileSettingsDefaults(profile)));
  const upscalers = sharedUpscalerDefaults(profile, settings.upscalers);
  Object.assign(upscalers.latent, { upscale_method: "sesqui", model_format: "Wan 2.1", half_precision: true, scale_by: 1.5 });
  const mode = settings.base_mode === "dual_sampler" ? "dual_sampler" : "normal";
  const sampling = sharedSamplingDefaults(mode, profile);
  delete sampling.seedMode; delete sampling.seed;
  return { ...settings, ...sampling, pipeline_version: 5, base_mode: mode, model_profile: profile, workbench: "random", upscalers };
}
function sharedLatentFormat(value, fallback = "Wan 2.1") {
  return String(value || "").startsWith("legacy:") ? fallback : value || fallback;
}
function sharedLatentMethodChoice(element, method, format) {
  if (!element) return;
  for (const option of [...(element.options || [])]) if (option.value.startsWith("legacy:")) option.remove();
  const value = method && method !== "sesqui" ? `legacy:${method}` : format || "Wan 2.1";
  ensureSelectValue(element, value);
  if (value.startsWith("legacy:")) {
    const option = [...(element.options || [])].find((item) => item.value === value);
    if (option) {option.textContent = `旧算法：${method}`;if (option.dataset) delete option.dataset.missing;}
  }
}
function setRandomDualControls(settings) {
  const values = randomDualFromSettings(settings);
  sharedParameterState.dualLegacy = settings.dual == null;
  sharedParameterState.dual = { ...values };
  for (const key of RANDOM_DUAL_KEYS) {
    const element = document.getElementById(`random_dual_${key}`);
    if (!element) continue;
    if (element.type === "checkbox") element.checked = Boolean(values[key]);else
    if (element.tagName === "SELECT") ensureSelectValue(element, String(values[key]));else
    element.value = values[key];
  }
  sharedLatentMethodChoice(document.getElementById("random_dual_model_format"), values.upscale_method, values.model_format);
  sharedParameterState.dualInitial = JSON.stringify(readRandomDualControls());
}
function readRandomDualControls() {
  return Object.fromEntries(RANDOM_DUAL_KEYS.map((key) => {
    const element = document.getElementById(`random_dual_${key}`);
    if (key === "model_format") return [key, sharedLatentFormat(element?.value, sharedParameterState.dual.model_format || "Wan 2.1")];
    return [key, element ? element.type === "checkbox" ? element.checked : element.type === "number" ? Number(element.value) : element.value : sharedParameterState.dual[key] ?? randomDualDefaults()[key]];
  }));
}
function readRandomDualSettings() {
  const values = readRandomDualControls();
  if (sharedParameterState.dualLegacy && JSON.stringify(values) === sharedParameterState.dualInitial) return null;
  return values;
}
function refreshInheritedDualControls() {
  if (!sharedParameterState.dualLegacy || readRandomDualSettings() !== null) return;
  setRandomDualControls({ dual: null, cfg: Number(ui.cfg.value), sampler_name: ui.sampler_name.value, scheduler: ui.scheduler.value,
    upscalers: { latent: { scale_by: Number(ui.latent_upscale_scale.value), upscale_method: ui.latent_upscale_method.value,
      ...sharedParameterState.latent } } });
}
function sharedParametersApply(settings, raw = settings) {
  sharedParameterState.applying = true;

  if (ui.base_denoise) ui.base_denoise.value = settings.base_denoise ?? 1;
  const latent = settings.upscalers?.latent;
  if (latent) {
    sharedParameterState.latent = { model_format: latent.model_format || "Wan 2.1", half_precision: latent.half_precision ?? true };
    ensureSelectValue(ui.latent_upscale_method, latent.upscale_method || "sesqui");
    ensureSelectValue(ui.latent_model_format, sharedParameterState.latent.model_format);
  }
  sharedParameterState.dlss = { ...defaultUpscalers().dlss, ...(settings.upscalers?.dlss || {}) };
  delete sharedParameterState.dlss.enabled;
  for (const key of NVIDIA_DLSS_KEYS) setControl(`nvidia_dlss_${key}`, sharedParameterState.dlss[key]);
  sharedParameterState.activeMode = settings.base_mode || "normal";
  sharedParameterState.modes = {};
  sharedParameterState.applying = false;
}
function sharedSamplingCapture() {
  return { steps: Number(ui.steps.value), cfg: Number(ui.cfg.value), sampler_name: ui.sampler_name.value, scheduler: ui.scheduler.value,
    base_denoise: Number(ui.base_denoise?.value ?? 1),
    dual: readRandomDualSettings(), dualDisplay: readRandomDualControls(), seedMode: document.getElementById("randomSeedMode")?.value || "fixed", seed: document.getElementById("randomSampleSeed")?.value || "0" };
}
function sharedSamplingDrafts() {
  if (sharedParameterState.activeMode) sharedParameterState.modes[sharedParameterState.activeMode] = sharedSamplingCapture();
  return JSON.parse(JSON.stringify(sharedParameterState.modes));
}
function sharedSamplingRestoreDrafts(modes) {
  sharedParameterState.modes = JSON.parse(JSON.stringify(modes || {}));
  sharedParameterState.activeMode = ui.base_mode.value;
  sharedParameterState.modes[ui.base_mode.value] = sharedSamplingCapture();
}
function sharedSamplingModeChanged() {
  const mode = ui.base_mode.value;
  if (sharedParameterState.applying || mode === sharedParameterState.activeMode) return;
  if (sharedParameterState.activeMode) sharedParameterState.modes[sharedParameterState.activeMode] = sharedSamplingCapture();
  const next = sharedParameterState.modes[mode] || sharedSamplingDefaults(mode);
  for (const key of ["steps", "cfg", "sampler_name", "scheduler", "base_denoise"]) setControl(key, next[key]);
  setRandomDualControls({ dual: next.dual ?? next.dualDisplay });
  if (next.dual == null && next.dualDisplay) sharedParameterState.dualLegacy = true;
  const seedMode = document.getElementById("randomSeedMode"), seed = document.getElementById("randomSampleSeed");
  if (seedMode) seedMode.value = next.seedMode || "fixed";
  if (seed) seed.value = next.seed ?? "0";
  sharedParameterState.activeMode = mode;
  sharedParameterVisibility();updateRepairControls();schedulePersist();
}
function sharedParameterVisibility() {
  const dual = ui.base_mode.value === "dual_sampler";
  const panel = document.getElementById("randomDualSection");
  if (panel) {
    panel.hidden = !dual;
    for (const field of panel.querySelectorAll("input, select")) field.disabled = !dual;
    const scale = document.getElementById("random_dual_scale");
    const sesqui = document.getElementById("random_dual_upscale_method").value === "sesqui";
    scale.min = "1";scale.max = sesqui ? "2" : "8";
    document.getElementById("random_dual_switch_step").max = String(Math.max(0, Number(ui.steps.value) - 1));
  }
  const modeTitle = document.getElementById("baseSamplingTitle");
  if (modeTitle) modeTitle.textContent = dual ? "双采样" : "底图";
  const order = dual ? ["steps", "random_dual_switch_step", "random_dual_scale", "random_dual_model_format", "randomSeedControls", "cfg", "sampler_name", "scheduler", "baseZeroNegativeControls", "random_dual_second_cfg", "random_dual_second_sampler_name", "random_dual_second_scheduler"]
    : ["steps", "cfg", "sampler_name", "scheduler", "base_denoise", "randomSeedControls", "baseZeroNegativeControls"];
  for (const [index, id] of order.entries()) {
    const node = document.getElementById(id);
    const field = ["randomSeedControls", "baseZeroNegativeControls"].includes(id) ? node : node?.closest?.("label");
    if (field?.style) field.style.order = String(index);
  }
  for (const [id, title] of [["steps", dual ? "第一轮步数" : "步数"], ["cfg", dual ? "CFG 1" : "CFG"], ["sampler_name", dual ? "采样器 1" : "采样器"], ["scheduler", dual ? "调度器 1" : "调度器"]]) {
    const label = ui[id]?.closest?.("label")?.querySelector("span"); if (label) label.textContent = title;
  }
  const dualFormat = document.getElementById("random_dual_model_format");
  const dualFormatLabel = dualFormat?.closest?.("label")?.querySelector("span");
  if (dualFormatLabel) dualFormatLabel.textContent = String(dualFormat.value).startsWith("legacy:") ? "旧放大算法" : "潜空间格式";
  const dualHint = document.getElementById("dualSamplingHint");
  if (dualHint) {
    dualHint.hidden = !dual;
    const stepsText = String(ui.steps.value).trim();
    const startText = String(document.getElementById("random_dual_switch_step")?.value ?? "").trim();
    const steps = Number(stepsText), start = Number(startText);
    const valid = stepsText !== "" && startText !== "" && Number.isInteger(steps) && steps >= 1 && steps <= 150
      && Number.isInteger(start) && start >= 0 && start < steps;
    dualHint.textContent = valid
      ? `第一轮 0→${steps}；第二轮 ${start}→${steps}；计划 ${steps}＋${steps - start}＝${steps * 2 - start} 步。`
      : "请填写有效步数；第二轮起始步须小于第一轮步数。";
  }

  const denoise = ui.base_denoise?.closest?.("label");if (denoise) denoise.hidden = dual;
  if (ui.base_denoise) ui.base_denoise.disabled = dual;
  const method = ui.latent_upscale_method.value;
  const sesqui = method === "sesqui";
  const schema = typeof resources !== "undefined" ? resources.node_inputs?.[sesqui ? "SesquiLatentUpscale" : "LatentUpscaleBy"] : null;
  const bounds = schema?.[sesqui ? "scale" : "scale_by"]?.[1] || {};
  ui.latent_upscale_scale.min = String(bounds.min ?? (sesqui ? 1 : .01));
  ui.latent_upscale_scale.max = String(bounds.max ?? (sesqui ? 2 : 8));
  ui.latent_upscale_scale.title = `倍率范围 ${ui.latent_upscale_scale.min}–${ui.latent_upscale_scale.max}`;
  if (ui.latentModelFormatField) ui.latentModelFormatField.hidden = !sesqui;
  if (ui.latent_model_format) ui.latent_model_format.disabled = !ui.latent_upscale_enabled?.checked || !sesqui;
  const seedMode = document.getElementById("randomSeedMode"),seed = document.getElementById("randomSampleSeed");
  if (seedMode) seedMode.disabled = false;
  if (seed) seed.disabled = seedMode?.value !== "fixed";
  const roll = document.getElementById("randomRollSeed");if (roll) roll.disabled = false;
}
function sharedParameterResources() {
  for (const [key, choices] of [["second_sampler_name", resources.samplers], ["second_scheduler", resources.schedulers]]) {
    const element = document.getElementById(`random_dual_${key}`);
    if (element) populateResourceSelect(element, choices || [], element.value, "资源暂不可用");
  }
  const schema = resources.node_inputs?.SesquiLatentUpscale || {};
  const formatSpec = schema.model_format;
  for (const id of ["latent_model_format", "random_dual_model_format"]) {
    const element = document.getElementById(id);
    if (!element) continue;
    if (formatSpec) applyNodeInputControl(element, formatSpec);else
    {
      const selected = element.value || "Wan 2.1";
      populateSelect(element, ["SDXL", "Flux", "Flux2", "Ideogram 4", "Wan 2.1"], selected);
    }
    if (id === "random_dual_model_format") sharedLatentMethodChoice(element, document.getElementById("random_dual_upscale_method")?.value, sharedParameterState.dual.model_format);
  }
  const dlss = resources.node_inputs?.DLSS5NeuralRender || {};
  for (const key of NVIDIA_DLSS_KEYS) applyNodeInputControl(ui[`nvidia_dlss_${key}`], dlss[key]);
}
function sharedParameterResourceIssues(settings) {
  const issues = [];
  if (typeof resourcesLoaded === "undefined" || !resourcesLoaded) return issues;
  const version = Number(settings.pipeline_version ?? 4);
  const post = normalizeUpscalers(settings.upscalers, null, settings.model_profile || "anima", version);
  const available = Array.isArray(resources.node_types) && resources.node_types.length ? new Set(resources.node_types) : null;
  const requireNodes = (enabled, label, names) => { if (enabled && available) for (const name of names) if (!available.has(name)) issues.push(`${label}节点不可用：${name}`); };
  requireNodes(post.pixel_hires?.enabled, "像素高清", ["UpscaleModelLoader", "ImageUpscaleWithModel", ...(version >= 5 || !post.post_sampler?.enabled ? ["ImageScaleBy"] : [])]);
  requireNodes(post.nvidia?.enabled, "N 卡 VSR", ["RTXVideoSuperResolution"]);
  requireNodes(post.dlss?.enabled, "DLSS", ["DLSS5NeuralRender"]);
  requireNodes(post.post_sampler?.enabled, "整图后采样", ["KSampler"]);
  if (version >= 4 && post.latent?.enabled) {
    requireNodes(true, "潜空间放大", [post.latent.upscale_method === "sesqui" ? "SesquiLatentUpscale" : "LatentUpscaleBy", "KSampler"]);
    if (!(resources.samplers || []).includes(post.latent.sampler?.sampler_name)) issues.push(`潜空间采样器不可用：${post.latent.sampler?.sampler_name || "未选择"}`);
    if (!(resources.schedulers || []).includes(post.latent.sampler?.scheduler)) issues.push(`潜空间调度器不可用：${post.latent.sampler?.scheduler || "未选择"}`);
  }
  if (post.pixel_hires?.enabled && !(resources.upscale_models || []).includes(post.pixel_hires.model_name)) issues.push(`像素高清模型不可用：${post.pixel_hires.model_name || "未选择"}`);
  if (post.post_sampler?.enabled) {
    if (!(resources.samplers || []).includes(post.post_sampler.sampler_name)) issues.push(`整图后采样器不可用：${post.post_sampler.sampler_name}`);
    if (!(resources.schedulers || []).includes(post.post_sampler.scheduler)) issues.push(`整图后采样调度器不可用：${post.post_sampler.scheduler}`);
  }
  const face = settings.upscalers?.face_refine;
  if (face?.enabled) {
    const schema = resources.node_inputs?.ForbiddenVisionFaceProcessorIntegrated || {};
    const choices = (spec, fallback) => Array.isArray(spec?.[0]) ? spec[0] : fallback;
    if (!choices(schema.sampler, resources.samplers || []).includes(face.sampler)) issues.push(`脸部采样器不可用：${face.sampler}`);
    if (!choices(schema.scheduler, resources.schedulers || []).includes(face.scheduler)) issues.push(`脸部调度器不可用：${face.scheduler}`);
  }
  return issues;
}
function adaptPostprocessSettings(settings, source) {
  settings.base_denoise = source?.base_denoise ?? settings.base_denoise ?? 1;
  settings.dual = source?.dual == null ? null : clone(source.dual);
  settings.pipeline_version = Number(source?.pipeline_version ?? 4);
  settings.upscalers = normalizeUpscalers(settings.upscalers || source?.upscalers, null, "anima", settings.pipeline_version);
  return settings;
}
function initializeSharedParameters() {
  const seed = document.getElementById("randomSeedControls"),base = document.getElementById("baseSamplingFields");
  if (seed && base) base.append(seed);
  ui.base_mode.addEventListener("change", sharedSamplingModeChanged);
  for (const key of RANDOM_DUAL_KEYS) document.getElementById(`random_dual_${key}`)?.addEventListener("change", () => { sharedParameterState.dualLegacy = false; sharedParameterVisibility(); schedulePersist(); });
  document.getElementById("random_dual_switch_step")?.addEventListener("input", sharedParameterVisibility);
  for (const [id, target] of [["random_dual_model_format", "random_dual_upscale_method"]]) document.getElementById(id)?.addEventListener("change", () => {
    const selected = document.getElementById(id).value;
    ensureSelectValue(document.getElementById(target), selected.startsWith("legacy:") ? selected.slice(7) : "sesqui");
    if (!selected.startsWith("legacy:")) {
      if (target === "latent_upscale_method") sharedParameterState.latent.model_format = selected;else
      sharedParameterState.dual.model_format = selected;
    }
    sharedParameterVisibility();schedulePersist();
  });
  ui.latent_model_format?.addEventListener("change", () => {
    sharedParameterState.latent.model_format = ui.latent_model_format.value;
    schedulePersist();
  });
  ui.steps.addEventListener("input", () => {sharedParameterVisibility();});
  sharedParameterResources();sharedParameterVisibility();
}
