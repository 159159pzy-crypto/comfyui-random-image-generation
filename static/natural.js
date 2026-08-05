import { api } from "./js/api.js";
import { collectIds, element, replace } from "./js/dom.js";
import { createNaturalPlanController } from "./js/natural-plan.js";
import { createTaskCenter } from "./js/task-center.js";
import { ArtworkViewer, recordPromptSeed, recordSeed } from "./js/artwork-viewer.js";
import { MultiLoraControl, normalizeLoras, populateModels, populatePresets } from "./js/shared-controls.js";
import { v7State } from "./js/v7-state.js";

const naturalUi = collectIds(
  [
      "naturalWorkspace", "naturalMobileTabs", "naturalForm", "naturalModes",
      "naturalPrompt", "naturalSourceSelectorField", "naturalSourceSelector", "naturalSourceInput", "naturalSourcePreview", "sourceAssetSlot",
      "naturalMaskInput", "naturalMaskPreview", "maskAssetSlot", "maskEditor",
      "maskCanvas", "maskBrushSize", "maskClear", "controlModeFields",
      "naturalPipeline", "naturalCount", "naturalWidth", "naturalHeight", "naturalSeed",
      "naturalSteps", "naturalCfg", "naturalDenoise", "naturalLockPools",
      "naturalUseLlm",
      "naturalLockedTags", "naturalNegative", "naturalPreset", "naturalModel", "naturalLoraList", "naturalLoraEmpty",
      "naturalLoraSummary", "naturalAddLora", "naturalDraftStatus",
      "naturalError", "naturalPreviewPlan", "naturalGenerate", "naturalCancel",
      "naturalPlanState", "naturalPlanEmpty", "naturalPlanContent", "naturalPlanMeta",
      "naturalPlanLayers", "naturalSwapSection", "naturalSwapDetails", "naturalPlanLocks", "naturalPlanPositive", "naturalPlanNegative",
      "naturalDiagnostics", "naturalTimeline", "naturalGallery", "naturalGalleryEmpty",
      "naturalRefreshGallery", "naturalSettingsButton", "naturalSettingsDialog",
      "naturalGalleryPrev", "naturalGalleryNext", "naturalGalleryPage",
      "naturalSettingsClose", "naturalProviderForm", "naturalProviderId", "naturalProviderName",
      "naturalProviderUrl", "naturalDirectorModel", "naturalVisionModel", "naturalEmbeddingModel", "naturalRerankModel", "naturalProviderKey",
      "naturalProviderTimeout", "naturalProviderTest", "naturalProviderList",
      "naturalCapabilitySummary", "naturalWorkflowList", "naturalLoraStatus",
      "naturalDanbooruQuery", "naturalDanbooruSearch", "naturalDanbooruResult",
      "naturalLoraActivationTerms", "naturalLoraIdentityTags", "naturalSaveLoraProfile", "naturalLoraProfiles",
      "naturalIdentityProfile", "naturalIdentityName", "naturalIdentityCanonical", "naturalIdentityCopyright", "naturalIdentityActivationTerms", "naturalSaveIdentity", "naturalIdentityList",
      "naturalPromptLabText", "naturalSavePromptLab", "naturalPromptLabList",
      "naturalRefreshLogs", "naturalLogList",
  ],
);

const MODE_REQUIREMENTS = {
    text_to_image: { source: false, mask: false, prompt: true },
    reverse: { source: true, mask: false, prompt: false },
    img2img: { source: true, mask: false, prompt: true },
    control: { source: true, mask: false, prompt: true },
    inpaint: { source: true, mask: true, prompt: true },
    upscale: { source: true, mask: false, prompt: false },
    character_swap: { source: true, mask: false, prompt: true },
  };

const state = {
    workspace: "random",
    jobType: "text_to_image",
    sourceAsset: null,
    maskAsset: null,
    plan: null,
    planIntent: null,
    planResolution: null,
    planConfirmations: {},
    planDirty: false,
    activeJob: null,
    events: null,
    maskDirty: false,
    providers: [],
    providerDefaultsApplied: false,
    presetId: "",
    modelName: "",
    loras: [],
    draftRestoring: false,
    galleryRecords: [],
    galleryPage: 1,
    galleryPages: 1,
    editingLoraProfileId: "",
    editingLoraFilename: "",
    editingIdentityId: "",
  };

  const naturalLoras = new MultiLoraControl({
    list: naturalUi.naturalLoraList,
    addButton: naturalUi.naturalAddLora,
    empty: naturalUi.naturalLoraEmpty,
    summary: naturalUi.naturalLoraSummary,
  });

  async function nativeApi(v7Path, options = {}) {
    await v7State.init();
    return api(`/api/v7${v7Path}`, options);
  }

  function notify(message) {
    if (typeof window.toast === "function") window.toast(message);
  }

  function showError(message = "") {
    naturalUi.naturalError.textContent = message;
    naturalUi.naturalError.hidden = !message;
  }

  function setWorkspace(workspace, { updateUrl = true, load = true } = {}) {
    state.workspace = workspace === "natural" ? "natural" : "random";
    document.body.dataset.workspace = state.workspace;
    document.querySelectorAll("[data-random-ui]").forEach((node) => {
      node.hidden = state.workspace !== "random";
    });
    document.querySelectorAll("[data-natural-ui]").forEach((node) => {
      node.hidden = state.workspace !== "natural";
    });
    document.querySelectorAll("[data-workspace]").forEach((button) => {
      const active = button.dataset.workspace === state.workspace;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    if (updateUrl) {
      const url = new URL(location.href);
      url.searchParams.set("workspace", state.workspace);
      history.replaceState({ workspace: state.workspace }, "", url);
    }
    if (state.workspace === "natural" && load) {
      loadNaturalGallery();
      loadNaturalSettings();
    }
  }

  function setNaturalView(view) {
    const next = ["compose", "plan", "gallery"].includes(view) ? view : "compose";
    naturalUi.naturalWorkspace.dataset.naturalView = next;
    naturalUi.naturalMobileTabs.querySelectorAll("button").forEach((button) => {
      const active = button.dataset.naturalView === next;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
  }

  function invalidatePlan() {
    state.plan = null;
    naturalUi.naturalGenerate.disabled = true;
    naturalUi.naturalPlanState.textContent = "计划待更新";
  }

  function setJobType(jobType) {
    state.jobType = jobType;
    naturalUi.naturalModes.querySelectorAll("button").forEach((button) => {
      const active = button.dataset.jobType === jobType;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    const requirements = MODE_REQUIREMENTS[jobType];
    naturalUi.sourceAssetSlot.hidden = !requirements.source;
    naturalUi.maskAssetSlot.hidden = !requirements.mask;
    naturalUi.maskEditor.hidden = !requirements.mask || !state.sourceAsset;
    naturalUi.controlModeFields.hidden = jobType !== "control";
    naturalUi.naturalSourceSelectorField.hidden = jobType !== "character_swap";
    naturalUi.naturalPrompt.required = requirements.prompt;
    naturalUi.naturalPrompt.placeholder = jobType === "reverse"
      ? "可选：补充希望视觉模型关注的内容"
      : jobType === "upscale"
        ? "放大模式不需要描述"
        : jobType === "character_swap"
          ? "描述目标角色；来源不唯一时请明确角色位置或身份"
          : "写下人物、动作、关系、场景和画面气氛…";
    naturalUi.naturalPipeline.disabled = jobType === "upscale";
    invalidatePlan();
  }

  async function uploadFile(file) {
    if (!file) return null;
    const form = new FormData();
    form.append("file", file, file.name || "image.png");
    return nativeApi("/studio/uploads", { method: "POST", body: form });
  }

  async function selectAsset(kind, file) {
    showError();
    const preview = kind === "source" ? naturalUi.naturalSourcePreview : naturalUi.naturalMaskPreview;
    const slot = kind === "source" ? naturalUi.sourceAssetSlot : naturalUi.maskAssetSlot;
    slot.classList.add("loading");
    try {
      const asset = await uploadFile(file);
      state[`${kind}Asset`] = asset;
      preview.src = URL.createObjectURL(file);
      preview.hidden = false;
      slot.classList.add("has-image");
      if (kind === "source") await initializeMaskCanvas(file);
      invalidatePlan();
    } catch (error) {
      showError(error.message);
    } finally {
      slot.classList.remove("loading");
    }
  }

  function clearMaskCanvas() {
    const canvas = naturalUi.maskCanvas;
    const context = canvas.getContext("2d");
    context.save();
    context.globalCompositeOperation = "source-over";
    context.fillStyle = "black";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.restore();
    state.maskDirty = false;
    state.maskAsset = null;
    naturalUi.naturalMaskPreview.hidden = true;
    naturalUi.maskAssetSlot.classList.remove("has-image");
    invalidatePlan();
  }

  async function initializeMaskCanvas(file) {
    const image = new Image();
    const objectUrl = URL.createObjectURL(file);
    image.src = objectUrl;
    try {
      await image.decode();
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
    const canvas = naturalUi.maskCanvas;
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    canvas.style.aspectRatio = `${image.naturalWidth} / ${image.naturalHeight}`;
    const fitDimension = (value) => Math.min(4096, Math.max(256, Math.round(value / 8) * 8));
    naturalUi.naturalWidth.value = String(fitDimension(image.naturalWidth));
    naturalUi.naturalHeight.value = String(fitDimension(image.naturalHeight));
    clearMaskCanvas();
    naturalUi.maskEditor.hidden = state.jobType !== "inpaint";
  }

  function setupMaskDrawing() {
    const canvas = naturalUi.maskCanvas;
    const context = canvas.getContext("2d");
    let drawing = false;
    const point = (event) => {
      const rect = canvas.getBoundingClientRect();
      return {
        x: (event.clientX - rect.left) * canvas.width / rect.width,
        y: (event.clientY - rect.top) * canvas.height / rect.height,
      };
    };
    const draw = (event) => {
      if (!drawing) return;
      const current = point(event);
      context.lineTo(current.x, current.y);
      context.stroke();
      state.maskDirty = true;
      state.maskAsset = null;
      invalidatePlan();
    };
    canvas.addEventListener("pointerdown", (event) => {
      drawing = true;
      canvas.setPointerCapture(event.pointerId);
      const current = point(event);
      context.beginPath();
      context.moveTo(current.x, current.y);
      context.strokeStyle = "white";
      context.lineCap = "round";
      context.lineJoin = "round";
      context.lineWidth = Number(naturalUi.maskBrushSize.value) * canvas.width / Math.max(canvas.clientWidth, 1);
      draw(event);
    });
    canvas.addEventListener("pointermove", draw);
    for (const name of ["pointerup", "pointercancel", "pointerleave"])
      canvas.addEventListener(name, () => { drawing = false; });
  }

  async function uploadPaintedMask() {
    if (!state.maskDirty) return state.maskAsset;
    const blob = await new Promise((resolve) => naturalUi.maskCanvas.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("无法导出蒙版");
    const file = new File([blob], "mask.png", { type: "image/png" });
    state.maskAsset = await uploadFile(file);
    return state.maskAsset;
  }

  function lockedTags() {
    return naturalUi.naturalLockedTags.value
      .split(/[\n,，]+/)
      .map((value) => value.trim())
      .filter(Boolean);
  }

  function canonicalMode(jobType = state.jobType) {
    if (jobType === "img2img") return "image_to_image";
    return jobType;
  }

  async function buildPayload() {
    const requirements = MODE_REQUIREMENTS[state.jobType];
    if (requirements.source && !state.sourceAsset) throw new Error("当前模式需要原图");
    if (requirements.prompt && !naturalUi.naturalPrompt.value.trim()) throw new Error("请输入自然语言描述");
    if (requirements.mask && !state.maskAsset && !state.maskDirty) throw new Error("局部重绘需要上传或绘制蒙版");
    if (requirements.mask && state.maskDirty) await uploadPaintedMask();
    const sourceImage = state.sourceAsset ? { asset_id: state.sourceAsset.id } : null;
    const maskImage = state.maskAsset ? { asset_id: state.maskAsset.id } : null;
    const payload = {
      workspace: "natural",
      mode: canonicalMode(),
      job_type: state.jobType,
      text: naturalUi.naturalPrompt.value.trim(),
      positive_prompt: naturalUi.naturalPrompt.value.trim(),
      negative_prompt: naturalUi.naturalNegative.value.trim(),
      pipeline: naturalUi.naturalPipeline.value,
      model_name: naturalUi.naturalModel.value,
      model: naturalUi.naturalModel.value,
      style_preset_id: naturalUi.naturalPreset.value,
      count: Number(naturalUi.naturalCount.value),
      width: Number(naturalUi.naturalWidth.value),
      height: Number(naturalUi.naturalHeight.value),
      seed: Number(naturalUi.naturalSeed.value),
      steps: Number(naturalUi.naturalSteps.value),
      cfg: Number(naturalUi.naturalCfg.value),
      denoise: Number(naturalUi.naturalDenoise.value),
      sampling: {
        count: Number(naturalUi.naturalCount.value), width: Number(naturalUi.naturalWidth.value), height: Number(naturalUi.naturalHeight.value),
        seed: Number(naturalUi.naturalSeed.value), steps: Number(naturalUi.naturalSteps.value), cfg: Number(naturalUi.naturalCfg.value), denoise: Number(naturalUi.naturalDenoise.value),
      },
      asset_id: state.sourceAsset?.id || "",
      input_image: sourceImage,
      source_selector: naturalUi.naturalSourceSelector.value.trim(),
      mask_asset_id: state.maskAsset?.id || "",
      mask_image: maskImage,
      inpaint_mode: "quick",
      locked_tags: lockedTags(),
      control_modes: [...document.querySelectorAll('input[name="controlMode"]:checked')].map((item) => item.value),
      controls: sourceImage && state.jobType === "control"
        ? [...document.querySelectorAll('input[name="controlMode"]:checked')].map((item) => ({ kind: item.value, image: sourceImage }))
        : [],
      loras: naturalLoras.getValue(),
      use_llm: naturalUi.naturalUseLlm.checked,
    };
    if (naturalUi.naturalLockPools.checked && typeof window.readSettings === "function") {
      payload.pool_settings = window.readSettings();
      payload.pool_seed = 0;
    }
    return payload;
  }

  function naturalDraftPayload() {
    const sourceImage = state.sourceAsset ? { asset_id: state.sourceAsset.id } : null;
    const maskImage = state.maskAsset ? { asset_id: state.maskAsset.id } : null;
    return {
      workspace: "natural",
      mode: canonicalMode(),
      job_type: state.jobType,
      text: naturalUi.naturalPrompt.value,
      positive_prompt: naturalUi.naturalPrompt.value,
      negative_prompt: naturalUi.naturalNegative.value,
      pipeline: naturalUi.naturalPipeline.value,
      count: Number(naturalUi.naturalCount.value),
      width: Number(naturalUi.naturalWidth.value),
      height: Number(naturalUi.naturalHeight.value),
      seed: Number(naturalUi.naturalSeed.value),
      steps: Number(naturalUi.naturalSteps.value),
      cfg: Number(naturalUi.naturalCfg.value),
      denoise: Number(naturalUi.naturalDenoise.value),
      sampling: {
        count: Number(naturalUi.naturalCount.value), width: Number(naturalUi.naturalWidth.value), height: Number(naturalUi.naturalHeight.value),
        seed: Number(naturalUi.naturalSeed.value), steps: Number(naturalUi.naturalSteps.value), cfg: Number(naturalUi.naturalCfg.value), denoise: Number(naturalUi.naturalDenoise.value),
      },
      source_selector: naturalUi.naturalSourceSelector.value,
      locked_tags: lockedTags(),
      control_modes: [...document.querySelectorAll('input[name="controlMode"]:checked')].map((item) => item.value),
      lock_pools: naturalUi.naturalLockPools.checked,
      use_llm: naturalUi.naturalUseLlm.checked,
      style_preset_id: naturalUi.naturalPreset.value,
      model_name: naturalUi.naturalModel.value,
      model: naturalUi.naturalModel.value,
      loras: naturalLoras.getValue(),
      source_asset: state.sourceAsset,
      asset_id: state.sourceAsset?.id || "",
      input_image: sourceImage,
      controls: sourceImage && state.jobType === "control"
        ? [...document.querySelectorAll('input[name="controlMode"]:checked')].map((item) => ({ kind: item.value, image: sourceImage }))
        : [],
      mask_asset: state.maskAsset,
      mask_asset_id: state.maskAsset?.id || "",
      mask_image: maskImage,
      inpaint_mode: "quick",
    };
  }

  function restoreNaturalDraft(payload = {}) {
    state.draftRestoring = true;
    const sampling = payload.sampling || {};
    const merged = { ...payload, ...sampling };
    const value = (id, key, fallback = "") => {
      if (merged[key] !== undefined && naturalUi[id]) naturalUi[id].value = String(merged[key] ?? fallback);
    };
    if (payload.text !== undefined || payload.positive_prompt !== undefined)
      naturalUi.naturalPrompt.value = String(payload.text ?? payload.positive_prompt ?? "");
    value("naturalNegative", "negative_prompt");
    value("naturalPipeline", "pipeline");
    value("naturalCount", "count");
    value("naturalWidth", "width");
    value("naturalHeight", "height");
    value("naturalSeed", "seed");
    value("naturalSteps", "steps");
    value("naturalCfg", "cfg");
    value("naturalDenoise", "denoise");
    value("naturalSourceSelector", "source_selector");
    naturalUi.naturalLockedTags.value = Array.isArray(payload.locked_tags) ? payload.locked_tags.join("\n") : payload.locked_tags || "";
    if (payload.lock_pools !== undefined) naturalUi.naturalLockPools.checked = Boolean(payload.lock_pools);
    if (payload.use_llm !== undefined) naturalUi.naturalUseLlm.checked = Boolean(payload.use_llm);
    state.presetId = payload.style_preset_id || "";
    state.modelName = payload.model || payload.model_name || "";
    populatePresets(naturalUi.naturalPreset, v7State.presets, state.presetId);
    populateModels(naturalUi.naturalModel, v7State.models, state.modelName);
    naturalLoras.setValue(payload.loras || [], { silent: true });
    const restoreAsset = (value, fallbackId) => {
      const candidate = value && typeof value === "object" ? value : {};
      const id = String(candidate.id || candidate.asset_id || fallbackId || "").trim();
      return id ? { ...candidate, id } : null;
    };
    state.sourceAsset = restoreAsset(payload.source_asset || payload.input_image, payload.asset_id);
    state.maskAsset = restoreAsset(payload.mask_asset || payload.mask_image, payload.mask_asset_id);
    state.maskDirty = false;
    for (const [asset, slot, preview] of [
      [state.sourceAsset, naturalUi.sourceAssetSlot, naturalUi.naturalSourcePreview],
      [state.maskAsset, naturalUi.maskAssetSlot, naturalUi.naturalMaskPreview],
    ]) {
      slot.classList.toggle("has-image", Boolean(asset));
      const previewUrl = asset?.preview_url || asset?.url || "";
      if (previewUrl) preview.src = previewUrl;
      else preview.removeAttribute("src");
      preview.hidden = !previewUrl;
    }
    const controlModes = payload.control_modes || (payload.controls || []).map((item) => item.kind);
    document.querySelectorAll('input[name="controlMode"]').forEach((input) => {
      input.checked = controlModes.includes(input.value);
    });
    setJobType(payload.job_type || payload.mode || "text_to_image");
    state.draftRestoring = false;
  }

  function saveNaturalDraft() {
    if (state.draftRestoring) return;
    naturalUi.naturalDraftStatus.textContent = "正在保存服务器草稿…";
    v7State.scheduleDraft("natural", naturalDraftPayload(), {
      onSaved: (record) => { naturalUi.naturalDraftStatus.textContent = `服务器草稿已保存 · r${record.revision}`; },
      onError: (error) => { naturalUi.naturalDraftStatus.textContent = error.message; },
    });
  }

  async function loadSharedAssets() {
    await v7State.init();
    await Promise.all([v7State.loadAssets(), v7State.loadPresets()]);
    naturalLoras.setInventory(v7State.loras);
    populateModels(naturalUi.naturalModel, v7State.models, state.modelName || naturalUi.naturalModel.value);
    populatePresets(naturalUi.naturalPreset, v7State.presets, state.presetId || naturalUi.naturalPreset.value);
  }

  function applyNaturalPreset() {
    const preset = v7State.presets.find((item) => String(item.id) === naturalUi.naturalPreset.value);
    state.presetId = naturalUi.naturalPreset.value;
    if (!preset) return saveNaturalDraft();
    const settings = preset.intent || preset.settings || preset.payload || {};
    const sampling = settings.sampling || settings;
    if (settings.model || settings.model_name) {
      state.modelName = settings.model || settings.model_name;
      populateModels(naturalUi.naturalModel, v7State.models, state.modelName);
    }
    if (Array.isArray(settings.loras)) naturalLoras.setValue(settings.loras, { silent: true });
    if (settings.negative_prompt !== undefined) naturalUi.naturalNegative.value = settings.negative_prompt;
    if (sampling.width) naturalUi.naturalWidth.value = sampling.width;
    if (sampling.height) naturalUi.naturalHeight.value = sampling.height;
    if (sampling.steps) naturalUi.naturalSteps.value = sampling.steps;
    if (sampling.cfg) naturalUi.naturalCfg.value = sampling.cfg;
    invalidatePlan();
    saveNaturalDraft();
    notify(`已应用预设：${preset.name}`);
  }

  function showGalleryRecord(record) {
    const plan = record.resolved_selection?.natural_plan;
    if (plan) planController.renderPlan(plan);
    naturalUi.naturalPlanPositive.value = record.positive_prompt || record.resolved_prompt || "";
    naturalUi.naturalPlanNegative.value = record.negative_prompt || "";
    setNaturalView("plan");
  }

  async function loadNaturalGallery(page = state.galleryPage) {
    try {
      const payload = await nativeApi(`/history?page=${Math.max(1, page)}&limit=24`);
      state.galleryRecords = payload.items || [];
      state.galleryPage = Number(payload.page || 1);
      state.galleryPages = Number(payload.pages || 1);
      naturalUi.naturalGalleryPage.textContent = `${state.galleryPage} / ${state.galleryPages}`;
      naturalUi.naturalGalleryPrev.disabled = state.galleryPage <= 1;
      naturalUi.naturalGalleryNext.disabled = state.galleryPage >= state.galleryPages;
      naturalUi.naturalGallery.replaceChildren(...state.galleryRecords.map((record) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "natural-gallery-item";
        const image = element("img", { attrs: { src: `/api/v7/images/${encodeURIComponent(record.id)}`, alt: record.filename || "生成结果", loading: "lazy" } });
        const copy = element("span", {}, [
          element("strong", { text: record.source_workspace === "natural" ? "自然语言" : "随机工作台" }),
          element("small", { text: `${record.job_type || "text_to_image"} · Seed ${recordSeed(record) || "--"}` }),
        ]);
        button.append(image, copy);
        button.addEventListener("click", () => artworkViewer.open(record, state.galleryRecords));
        return button;
      }));
      naturalUi.naturalGalleryEmpty.hidden = state.galleryRecords.length > 0;
    } catch (error) {
      naturalUi.naturalGalleryEmpty.hidden = false;
      naturalUi.naturalGalleryEmpty.querySelector("small").textContent = error.message;
    }
  }

  const notifyJobsChanged = () => window.dispatchEvent(new CustomEvent("studio:jobs-changed"));
  const planController = createNaturalPlanController({
    ui: naturalUi,
    state,
    buildPayload,
    setNaturalView,
    showError,
    loadGallery: loadNaturalGallery,
    notifyJobsChanged,
  });

  function recordIntent(record) {
    let intent = record.intent_json || record.intent || record.settings || {};
    if (typeof intent === "string") {
      try { intent = JSON.parse(intent); } catch { intent = {}; }
    }
    return intent && typeof intent === "object" ? intent : {};
  }

  async function submitArtwork(record, { variant = false } = {}) {
    const intent = { ...recordIntent(record), workspace: record.source_workspace || "random" };
    intent.count = variant ? 4 : 1;
    if (!variant) {
      const sampleSeed = recordSeed(record);
      const promptSeed = recordPromptSeed(record);
      intent.seed = sampleSeed || intent.seed;
      intent.prompt_seed = promptSeed || intent.prompt_seed;
      intent.sampling = {
        ...(intent.sampling || {}),
        seed: sampleSeed || intent.sampling?.seed,
        prompt_seed: promptSeed || intent.sampling?.prompt_seed,
      };
      intent.seeds = { sample_seed: sampleSeed, prompt_seed: promptSeed };
    } else {
      intent.seed = -1;
      delete intent.seeds;
    }
    await v7State.init();
    try {
      await api("/api/v7/jobs", { method: "POST", body: JSON.stringify(intent) });
    } catch (error) {
      if (![400, 422].includes(error.status)) throw error;
      await api("/api/v7/jobs", { method: "POST", body: JSON.stringify({ intent }) });
    }
    notifyJobsChanged();
    notify(variant ? "变体任务已入队" : "复现任务已入队");
  }

  const artworkViewer = new ArtworkViewer({
    notify,
    onRestore: (record) => {
      const intent = recordIntent(record);
      if ((record.source_workspace || intent.workspace) === "natural") {
        restoreNaturalDraft(intent);
        saveNaturalDraft();
        setWorkspace("natural");
        setNaturalView("compose");
      } else if (typeof window.applySettings === "function") {
        window.applySettings(intent);
        window.schedulePersist?.();
        setWorkspace("random");
      }
      artworkViewer.dialog.close();
    },
    onReproduce: (record) => submitArtwork(record),
    onVariant: (record) => submitArtwork(record, { variant: true }),
    onWorkspace: (workspace) => { setWorkspace(workspace); artworkViewer.dialog.close(); },
    onDeleted: () => {
      loadNaturalGallery();
      window.loadHistory?.();
    },
  });
  window.animaArtworkViewer = artworkViewer;

  function renderProviders(snapshot) {
    state.providers = snapshot.profiles || [];
    replace(naturalUi.naturalProviderList, state.providers.length ? state.providers.map((provider) => {
      const button = element("button", { attrs: { type: "button" } }, [
        element("span", {}, [element("strong", { text: provider.name }), element("small", { text: provider.base_url })]),
        element("i", { text: provider.has_api_key ? "已加密" : "无密钥" }),
      ]);
      button.addEventListener("click", () => fillProvider(provider));
      return button;
    }) : [element("div", { className: "natural-muted", text: "尚未配置 Provider" })]);
  }

  function fillProvider(provider = null) {
    naturalUi.naturalProviderId.value = provider?.id || "";
    naturalUi.naturalProviderName.value = provider?.name || "";
    naturalUi.naturalProviderUrl.value = provider?.base_url || "";
    naturalUi.naturalDirectorModel.value = provider?.director_model || "";
    naturalUi.naturalVisionModel.value = provider?.vision_model || "";
    naturalUi.naturalEmbeddingModel.value = provider?.embedding_model || "";
    naturalUi.naturalRerankModel.value = provider?.rerank_model || "";
    naturalUi.naturalProviderTimeout.value = provider?.timeout || 120;
    naturalUi.naturalProviderKey.value = "";
  }

  async function loadNaturalSettings() {
    try {
      await loadSharedAssets();
      naturalUi.naturalProviderForm.querySelectorAll("input, textarea, button:not(#naturalSettingsClose)").forEach((control) => { control.disabled = false; });
      const [providers, diagnostics, loraProfiles, identities, promptLab] = await Promise.all([
        nativeApi("/studio/providers"),
        nativeApi("/studio/diagnostics"),
        nativeApi("/studio/lora-profiles"),
        nativeApi("/studio/identities"),
        nativeApi("/studio/prompt-lab"),
      ]);
      renderProviders(providers);
      if (!state.providerDefaultsApplied) {
        naturalUi.naturalUseLlm.checked = Boolean(providers.bindings?.director);
        state.providerDefaultsApplied = true;
      }
      const runtime = diagnostics.runtime || {};
      naturalUi.naturalCapabilitySummary.textContent = runtime.comfy_online
        ? "ComfyUI 在线，能力以实时节点清单为准"
        : `ComfyUI 离线：${runtime.comfy_error || "无法读取节点清单"}`;
      replace(naturalUi.naturalWorkflowList, (runtime.workflows || []).map((workflow) => element("div", {
        className: workflow.ready ? "ready" : "missing",
      }, [
        element("span", {}, [
          element("strong", { text: workflow.name || workflow.id }),
          element("small", { text: `${workflow.file || ""}${workflow.optional_missing_nodes?.length ? " · 无 LoRA 可用" : ""}` }),
        ]),
        element("i", { text: workflow.ready ? "可用" : workflow.missing_nodes?.length ? `缺 ${workflow.missing_nodes.length}` : "待核验" }),
      ])));
      const items = v7State.loras;
      naturalLoras.setInventory(items);
      naturalUi.naturalLoraStatus.textContent = `已读取 ${items.length} 个本地 LoRA；选择后作为受约束计划输入。`;
      const profileItems = loraProfiles.items || [];
      replace(naturalUi.naturalLoraProfiles, profileItems.length ? profileItems.map((item) => {
        const loadButton = element("button", { className: "button ghost compact", text: "载入", attrs: { type: "button" } });
        loadButton.addEventListener("click", () => {
          state.editingLoraProfileId = item.id;
          state.editingLoraFilename = item.filename;
          naturalUi.naturalIdentityProfile.value = item.id;
          naturalUi.naturalLoraActivationTerms.value = (item.activation_terms || []).join(", ");
          naturalUi.naturalLoraIdentityTags.value = (item.identity_tags || []).join(", ");
        });
        const deleteButton = element("button", { className: "button danger compact", text: "删除", attrs: { type: "button" } });
        deleteButton.addEventListener("click", async () => {
          if (!window.confirm(`确认删除 LoRA 档案 ${item.display_name || item.filename}？相关身份绑定将失效。`)) return;
          await nativeApi(`/studio/lora-profiles/${encodeURIComponent(item.id)}`, { method: "DELETE" });
          await loadNaturalSettings();
        });
        return element("div", { className: "identity-admin-row" }, [
          element("span", {}, [
            element("strong", { text: item.display_name }),
            element("small", { text: `${item.file_status || "unverified"} · ${(item.activation_terms || []).join("、") || "无共享激活词"}` }),
          ]),
          element("span", { className: "identity-admin-actions" }, [loadButton, deleteButton]),
        ]);
      }) : [document.createTextNode("尚无语义档案")]);
      const selectedProfile = naturalUi.naturalIdentityProfile.value;
      if (!profileItems.some((item) => item.id === state.editingLoraProfileId)) {
        state.editingLoraProfileId = "";
        state.editingLoraFilename = "";
      }
      replace(naturalUi.naturalIdentityProfile, [
        element("option", { attrs: { value: "" }, text: "选择 LoRA 档案" }),
        ...profileItems.map((item) => element("option", {
          attrs: { value: item.id },
          text: `${item.display_name || item.filename} · ${item.file_status || "unverified"}`,
        })),
      ]);
      naturalUi.naturalIdentityProfile.value = profileItems.some((item) => item.id === selectedProfile)
        ? selectedProfile
        : (profileItems[0]?.id || "");
      const identityItems = identities.items || [];
      replace(naturalUi.naturalIdentityList, identityItems.length ? identityItems.map((item) => {
        const editButton = element("button", { className: "button ghost compact", text: "编辑", attrs: { type: "button" } });
        editButton.addEventListener("click", () => {
          state.editingIdentityId = item.id;
          naturalUi.naturalIdentityProfile.value = item.lora_profile_id || item.lora_profile_ids?.[0] || "";
          naturalUi.naturalIdentityName.value = item.name || "";
          naturalUi.naturalIdentityCanonical.value = item.character_canonical || item.canonical_tag || "";
          naturalUi.naturalIdentityCopyright.value = item.copyright_canonical || "";
          naturalUi.naturalIdentityActivationTerms.value = (item.activation_terms || []).join(", ");
        });
        const deleteButton = element("button", { className: "button danger compact", text: "删除", attrs: { type: "button" } });
        deleteButton.addEventListener("click", async () => {
          if (!window.confirm(`确认删除身份绑定 ${item.name}？`)) return;
          await nativeApi(`/studio/identities/${encodeURIComponent(item.id)}`, { method: "DELETE" });
          if (state.editingIdentityId === item.id) state.editingIdentityId = "";
          await loadNaturalSettings();
        });
        return element("div", { className: "identity-admin-row" }, [
          element("span", {}, [
            element("strong", { text: item.name }),
            element("small", { text: `${item.verification_status || "review_needed"} · ${item.character_canonical || item.canonical_tag || "未绑定 Character"}${item.copyright_canonical ? ` · ${item.copyright_canonical}` : ""}${item.activation_terms?.length ? ` · ${item.activation_terms.join("、")}` : ""}` }),
          ]),
          element("span", { className: "identity-admin-actions" }, [editButton, deleteButton]),
        ]);
      }) : [document.createTextNode("尚无身份绑定")]);
      const promptItems = promptLab.items || [];
      replace(naturalUi.naturalPromptLabList, promptItems.length ? promptItems.map((item) => {
        const button = element("button", { attrs: { type: "button" } }, [
          element("span", { text: item.prompt }),
          element("i", { text: item.status }),
        ]);
        button.addEventListener("click", async () => {
          await nativeApi(`/studio/prompt-lab/${encodeURIComponent(item.id)}/confirm`, { method: "POST", body: "{}" });
          await loadNaturalSettings();
        });
        return button;
      }) : [document.createTextNode("尚无候选")]);
    } catch (error) {
      naturalUi.naturalCapabilitySummary.textContent = error.message;
    }
  }

  async function saveProvider(event) {
    event.preventDefault();
    const providerId = naturalUi.naturalProviderId.value;
    const body = {
      name: naturalUi.naturalProviderName.value,
      base_url: naturalUi.naturalProviderUrl.value,
      director_model: naturalUi.naturalDirectorModel.value,
      vision_model: naturalUi.naturalVisionModel.value,
      embedding_model: naturalUi.naturalEmbeddingModel.value,
      rerank_model: naturalUi.naturalRerankModel.value,
      timeout: Number(naturalUi.naturalProviderTimeout.value),
      enabled: true,
    };
    if (naturalUi.naturalProviderKey.value) body.api_key = naturalUi.naturalProviderKey.value;
    try {
      const provider = await nativeApi(providerId ? `/studio/providers/${providerId}` : "/studio/providers", {
        method: providerId ? "PUT" : "POST", body: JSON.stringify(body),
      });
      const bindings = { director: provider.director_model ? provider.id : "", vision: provider.vision_model ? provider.id : "", embedding: provider.embedding_model ? provider.id : "", rerank: provider.rerank_model ? provider.id : "" };
      await nativeApi("/studio/providers/bindings", { method: "PUT", body: JSON.stringify(bindings) });
      fillProvider(provider);
      await loadNaturalSettings();
      notify("Provider 已保存并绑定");
    } catch (error) { notify(error.message); }
  }

  async function testProvider() {
    const providerId = naturalUi.naturalProviderId.value;
    if (!providerId) return notify("请先保存 Provider");
    if (!window.confirm("确认连接并读取该 Provider 的模型信息？")) return;
    naturalUi.naturalProviderTest.disabled = true;
    try {
      const result = await nativeApi(`/studio/providers/${providerId}/test`, { method: "POST", body: JSON.stringify({ confirm_manual: true }) });
      notify(`连接成功，发现 ${result.model_count} 个模型`);
    } catch (error) { notify(error.message); }
    finally { naturalUi.naturalProviderTest.disabled = false; }
  }

  async function searchDanbooru() {
    const query = naturalUi.naturalDanbooruQuery.value.trim();
    if (!query) return;
    try {
      const payload = await nativeApi(`/studio/danbooru/search?q=${encodeURIComponent(query)}`);
      naturalUi.naturalDanbooruResult.textContent = payload.items?.length
        ? payload.items.map((item) => item.canonical || item.name || item.tag).filter(Boolean).join("、")
        : "没有 exact 命中";
    } catch (error) { naturalUi.naturalDanbooruResult.textContent = error.message; }
  }

  async function saveLoraProfile() {
    const filename = state.editingLoraFilename
      || naturalLoras.getValue().find((item) => item.enabled)?.filename
      || "";
    if (!filename) return notify("请先选择 LoRA");
    const path = state.editingLoraProfileId
      ? `/studio/lora-profiles/${encodeURIComponent(state.editingLoraProfileId)}`
      : "/studio/lora-profiles";
    await nativeApi(path, { method: state.editingLoraProfileId ? "PUT" : "POST", body: JSON.stringify({
      filename,
      display_name: filename,
      activation_terms: naturalUi.naturalLoraActivationTerms.value.split(/[，,]/).map((item) => item.trim()).filter(Boolean),
      identity_tags: naturalUi.naturalLoraIdentityTags.value.split(/[，,]/).map((item) => item.trim()).filter(Boolean),
      preview_asset_ids: state.sourceAsset ? [state.sourceAsset.id] : [],
    }) });
    state.editingLoraProfileId = "";
    state.editingLoraFilename = "";
    await loadNaturalSettings();
    notify("LoRA Schema v3 档案已保存");
  }

  async function saveIdentity() {
    const profileId = naturalUi.naturalIdentityProfile.value;
    if (!profileId) return notify("请先选择 LoRA 档案");
    const identityPath = state.editingIdentityId
      ? `/studio/identities/${encodeURIComponent(state.editingIdentityId)}`
      : "/studio/identities";
    await nativeApi(identityPath, { method: state.editingIdentityId ? "PUT" : "POST", body: JSON.stringify({
      name: naturalUi.naturalIdentityName.value,
      character_canonical: naturalUi.naturalIdentityCanonical.value,
      copyright_canonical: naturalUi.naturalIdentityCopyright.value,
      activation_terms: naturalUi.naturalIdentityActivationTerms.value.split(/[，,]/).map((item) => item.trim()).filter(Boolean),
      aliases: [],
      lora_profile_id: profileId,
      lora_profile_ids: [profileId],
    }) });
    state.editingIdentityId = "";
    await loadNaturalSettings();
    notify("身份绑定已保存");
  }

  async function savePromptLab() {
    const prompt = naturalUi.naturalPromptLabText.value.trim() || state.plan?.positive_prompt || "";
    if (!prompt) return notify("请先填写候选提示词或预览计划");
    await nativeApi("/studio/prompt-lab", { method: "POST", body: JSON.stringify({
      prompt,
      negative_prompt: state.plan?.negative_prompt || "",
      source_plan_id: state.plan?.id || "",
    }) });
    await loadNaturalSettings();
    notify("Prompt Lab 候选已保存");
  }

  async function refreshLogs() {
    const payload = await nativeApi("/studio/logs?limit=30");
    const items = (payload.items || []).reverse();
    replace(naturalUi.naturalLogList, items.length ? items.map((item) => element("div", {}, [
      element("strong", { text: item.stage }),
      element("small", { text: `${item.message} · ${new Date(item.timestamp * 1000).toLocaleString()}` }),
    ])) : [document.createTextNode("暂无任务日志")]);
  }

  document.querySelectorAll("[data-workspace]").forEach((button) =>
    button.addEventListener("click", () => setWorkspace(button.dataset.workspace)),
  );
  naturalUi.naturalMobileTabs.querySelectorAll("button").forEach((button) =>
    button.addEventListener("click", () => setNaturalView(button.dataset.naturalView)),
  );
  naturalUi.naturalModes.querySelectorAll("button").forEach((button) =>
    button.addEventListener("click", () => setJobType(button.dataset.jobType)),
  );
  naturalUi.naturalSourceInput.addEventListener("change", () => selectAsset("source", naturalUi.naturalSourceInput.files[0]));
  naturalUi.naturalMaskInput.addEventListener("change", () => selectAsset("mask", naturalUi.naturalMaskInput.files[0]));
  naturalUi.maskClear.addEventListener("click", clearMaskCanvas);
  naturalUi.naturalPreviewPlan.addEventListener("click", planController.preview);
  naturalUi.naturalForm.addEventListener("submit", planController.submit);
  naturalUi.naturalCancel.addEventListener("click", planController.cancel);
  naturalUi.naturalRefreshGallery.addEventListener("click", () => loadNaturalGallery());
  naturalUi.naturalGalleryPrev.addEventListener("click", () => loadNaturalGallery(state.galleryPage - 1));
  naturalUi.naturalGalleryNext.addEventListener("click", () => loadNaturalGallery(state.galleryPage + 1));
  naturalUi.naturalSettingsButton.addEventListener("click", () => {
    loadNaturalSettings();
    naturalUi.naturalSettingsDialog.showModal();
  });
  naturalUi.naturalSettingsClose.addEventListener("click", () => naturalUi.naturalSettingsDialog.close());
  naturalUi.naturalProviderForm.addEventListener("submit", saveProvider);
  naturalUi.naturalProviderTest.addEventListener("click", testProvider);
  naturalUi.naturalDanbooruSearch.addEventListener("click", searchDanbooru);
  naturalUi.naturalSaveLoraProfile.addEventListener("click", () => saveLoraProfile().catch((error) => notify(error.message)));
  naturalUi.naturalSaveIdentity.addEventListener("click", () => saveIdentity().catch((error) => notify(error.message)));
  naturalUi.naturalSavePromptLab.addEventListener("click", () => savePromptLab().catch((error) => notify(error.message)));
  naturalUi.naturalRefreshLogs.addEventListener("click", () => refreshLogs().catch((error) => notify(error.message)));
  naturalUi.naturalForm.addEventListener("input", (event) => {
    if (!["naturalPreviewPlan", "naturalGenerate", "naturalCancel"].includes(event.target.id)) {
      invalidatePlan();
      saveNaturalDraft();
    }
  });
  naturalUi.naturalForm.addEventListener("change", saveNaturalDraft);
  naturalUi.naturalPreset.addEventListener("change", applyNaturalPreset);
  naturalUi.naturalModel.addEventListener("change", () => {
    state.modelName = naturalUi.naturalModel.value;
    invalidatePlan();
    saveNaturalDraft();
  });
  naturalLoras.addEventListener("change", () => {
    state.loras = normalizeLoras(naturalLoras.getValue());
    invalidatePlan();
    saveNaturalDraft();
  });
  window.addEventListener("popstate", () => setWorkspace(new URL(location.href).searchParams.get("workspace"), { updateUrl: false }));
  window.addEventListener("studio:open-workspace", ({ detail }) => {
    document.getElementById("taskCenterDialog")?.close();
    if (detail?.workspace === "studio") {
      setWorkspace("natural");
      loadNaturalSettings();
      naturalUi.naturalSettingsDialog.showModal();
    } else setWorkspace(detail?.workspace);
  });
  window.addEventListener("studio:history-changed", () => {
    loadNaturalGallery();
    window.loadHistory?.();
  });
  window.addEventListener("studio:assets-changed", () => loadSharedAssets().catch(() => {}));
  window.addEventListener("studio:presets-changed", () => {
    loadSharedAssets().catch(() => {});
    window.loadStylePresets?.();
  });

  async function initializeRandomDraftBridge() {
    await v7State.init();
    window.clearRandomServerDraft = (payload) => v7State.saveDraft("random", payload);
    if (typeof window.readSettings !== "function") return;
    const record = await v7State.loadDraft("random");
    if (record?.payload && Object.keys(record.payload).length) {
      const intent = record.payload;
      const sampling = intent.sampling || {};
      const repair = intent.repair || {};
      const pools = intent.random_pools || intent.pools || {};
      const converted = { ...window.readSettings(), ...intent, pools };
      const derived = {
        model_name: intent.model || intent.model_name,
        manual_artist: Array.isArray(intent.artist_tags) ? intent.artist_tags.join(", ") : intent.manual_artist,
        count: sampling.count, width: sampling.width, height: sampling.height, steps: sampling.steps, cfg: sampling.cfg,
      };
      for (const [name, value] of Object.entries(derived)) if (value !== undefined && value !== "") converted[name] = value;
      if (intent.hires || Object.keys(repair).length)
        converted.hires = intent.hires || { enabled: repair.hires_enabled, model_name: repair.upscale_model, percent: repair.upscale_percent };
      if (intent.detailers || repair.detailers)
        converted.detailers = intent.detailers || Object.fromEntries((repair.detailers || []).map((name) => [name, true]));
      for (const [name, selection] of Object.entries(pools)) {
        converted[`random_${name}`] = selection.mode !== "off" && !selection.fixed_tags;
        converted[`random_${name}_count`] = selection.count || 1;
        converted[`fixed_${name}`] = selection.fixed_tags || "";
      }
      window.applySettings?.(converted);
      const status = document.getElementById("draftStatus");
      if (status) status.textContent = `已恢复服务器草稿 · r${record.revision}`;
    }
    window.addEventListener("studio:random-draft-dirty", () => {
      v7State.scheduleDraft("random", window.readSettings(), {
        onSaved: (saved) => {
          const status = document.getElementById("draftStatus");
          if (status) status.textContent = `服务器草稿已保存 · r${saved.revision}`;
        },
        onError: (error) => {
          const status = document.getElementById("draftStatus");
          if (status) status.textContent = error.message;
        },
      });
    });
  }

  async function initializeV7() {
    const initialWorkspace = new URL(location.href).searchParams.get("workspace");
    setWorkspace(initialWorkspace, { updateUrl: false, load: false });
    await v7State.init();
    await loadSharedAssets();
    const draft = await v7State.loadDraft("natural");
    if (draft?.payload && Object.keys(draft.payload).length) restoreNaturalDraft(draft.payload);
    naturalUi.naturalDraftStatus.textContent = draft ? `已恢复服务器草稿 · r${draft.revision}` : "服务器草稿已就绪";
    if (state.workspace === "natural") loadNaturalSettings();
    loadNaturalGallery();
  }

  createTaskCenter();
  setupMaskDrawing();
  if (window.__animaRandomReady) initializeRandomDraftBridge().catch((error) => notify(error.message));
  else window.addEventListener("studio:random-ready", () => initializeRandomDraftBridge().catch((error) => notify(error.message)), { once: true });
  initializeV7().catch((error) => {
    naturalUi.naturalDraftStatus.textContent = error.message;
    showError(error.message);
  });
