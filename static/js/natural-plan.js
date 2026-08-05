import { api } from "./api.js";
import { detailRow, element, formatTime, replace, replaceDetails } from "./dom.js";
import { v7State } from "./v7-state.js";

const TERMINAL_STAGES = new Set(["completed", "succeeded", "failed", "cancelled", "interrupted"]);

const RESOLUTION_FIELDS = {
  artist: "artist_tags",
  lora: "loras",
  preset: "style_preset_id",
  style_preset: "style_preset_id",
  prompt_asset: "prompt_asset_ids",
  prompt_plan: "prompt_plan_id",
  character_alias: "locked_tags",
};

function copyJson(value, fallback = {}) {
  try { return JSON.parse(JSON.stringify(value ?? fallback)); }
  catch { return fallback; }
}

export function normalizePlanResolution(result = {}, plan = {}) {
  const resolution = result.resolution && typeof result.resolution === "object" ? result.resolution : {};
  const matches = result.matches || resolution.matches || plan.matches || [];
  const required = result.requires_confirmation
    || resolution.requires_confirmation
    || plan.requires_confirmation
    || [];
  const sources = result.sources || resolution.sources || plan.sources || {};
  return {
    matches: Array.isArray(matches) ? matches : [],
    requiresConfirmation: Array.isArray(required) ? required : [],
    sources: sources && typeof sources === "object" ? sources : {},
  };
}

function confirmationKey(item, index) {
  return `${item.kind || "asset"}:${item.query || ""}:${index}`;
}

function explicitOverrideAvailable(intent, kind) {
  const field = RESOLUTION_FIELDS[kind];
  const value = field ? intent?.[field] : null;
  return Array.isArray(value) ? value.length > 0 : Boolean(value);
}

export function hasUnresolvedConfirmations(required = [], confirmations = {}) {
  return required.some((item, index) => !confirmations[confirmationKey(item, index)]);
}

function appendUnique(values, value, identity = (item) => String(item).toLocaleLowerCase()) {
  const result = Array.isArray(values) ? [...values] : [];
  const key = identity(value);
  const index = result.findIndex((item) => identity(item) === key);
  if (index >= 0) result[index] = value;
  else result.push(value);
  return result;
}

export function applyConfirmedResolution(intentValue, required = [], confirmations = {}) {
  const intent = copyJson(intentValue, {});
  const receipts = [];
  required.forEach((item, index) => {
    const choice = confirmations[confirmationKey(item, index)];
    if (!choice) throw new Error("unresolved asset confirmation");
    if (choice.action === "keep_explicit") {
      receipts.push({ kind: item.kind, query: item.query, action: choice.action });
      return;
    }
    const candidate = choice.candidate || {};
    const kind = String(item.kind || "");
    const name = String(candidate.name || candidate.filename || candidate.id || "").trim();
    if (kind === "artist") {
      intent.artist_tags = appendUnique(intent.artist_tags, name.startsWith("@") ? name : `@${name}`);
    } else if (kind === "lora") {
      const filename = String(candidate.filename || candidate.name || candidate.id || "").trim();
      intent.loras = appendUnique(intent.loras, {
        filename, enabled: true, strength: 1, role: "style", order: (intent.loras || []).length,
      }, (entry) => String(entry?.filename || "").toLocaleLowerCase());
    } else if (kind === "preset" || kind === "style_preset") {
      intent.style_preset_id = String(candidate.id || name);
    } else if (kind === "prompt_asset") {
      intent.prompt_asset_ids = appendUnique(intent.prompt_asset_ids, String(candidate.id || name));
    } else if (kind === "prompt_plan") {
      intent.prompt_plan_id = String(candidate.id || name);
    } else if (kind === "character_alias") {
      intent.locked_tags = appendUnique(intent.locked_tags, name);
    }
    receipts.push({
      kind,
      query: item.query,
      action: "select_candidate",
      candidate_id: String(candidate.id || ""),
      candidate_name: name,
    });
  });
  return { intent, receipts };
}

export function createNaturalPlanController({ ui, state, buildPayload, setNaturalView, showError, loadGallery, notifyJobsChanged }) {
  async function postV7(path, payload) {
    await v7State.init();
    try {
      return await api(`/api/v7${path}`, { method: "POST", body: JSON.stringify(payload) });
    } catch (error) {
      if (error.status !== 400 && error.status !== 422) throw error;
      return api(`/api/v7${path}`, { method: "POST", body: JSON.stringify({ intent: payload }) });
    }
  }
  function setDirty(dirty) {
    state.planDirty = dirty;
    if (dirty && state.plan) ui.naturalPlanState.textContent = "计划有未提交修订";
  }

  function invalidate() {
    state.plan = null;
    state.planIntent = null;
    state.planResolution = null;
    state.planConfirmations = {};
    state.planDirty = false;
    ui.naturalGenerate.disabled = true;
    ui.naturalPlanState.textContent = "计划待更新";
    ui.naturalPlanContent.querySelector("[data-resolution-panel]")?.remove();
  }

  function updateGenerateState() {
    const required = state.planResolution?.requiresConfirmation || [];
    const unresolved = hasUnresolvedConfirmations(required, state.planConfirmations || {});
    ui.naturalGenerate.disabled = !state.plan || state.plan.job_type === "reverse" || unresolved;
    if (unresolved) ui.naturalPlanState.textContent = `待确认 ${required.length} 项素材匹配`;
  }

  function renderResolution(resolution) {
    ui.naturalPlanContent.querySelector("[data-resolution-panel]")?.remove();
    const panel = element("section", { dataset: { resolutionPanel: "true" } });
    panel.append(element("h3", { text: "素材匹配与来源" }));
    const body = element("div", { className: "plan-layer-list" });
    const matchRows = resolution.matches.map((match) => detailRow(
      `${match.kind || "asset"} / ${match.query || ""}`,
      `${match.status || "unknown"}: ${(match.candidates || []).map((item) => item.name || item.id).join(", ") || "--"}`,
    ));
    body.append(...(matchRows.length ? matchRows : [element("span", {
      className: "natural-muted", text: "未返回素材匹配",
    })]));
    for (const [field, source] of Object.entries(resolution.sources)) body.append(detailRow(field, source));

    resolution.requiresConfirmation.forEach((item, index) => {
      const key = confirmationKey(item, index);
      const row = element("label", {}, [
        element("strong", { text: `${item.kind || "asset"}: ${item.query || ""}` }),
      ]);
      const select = element("select", { attrs: { "aria-label": `Confirm ${item.kind || "asset"}` } });
      select.append(element("option", { text: "请选择唯一候选", attrs: { value: "" } }));
      (item.candidates || []).forEach((candidate, candidateIndex) => {
        select.append(element("option", {
          text: `${candidate.name || candidate.id} (${candidate.matched_by || "candidate"})`,
          attrs: { value: String(candidateIndex) },
        }));
      });
      if (explicitOverrideAvailable(state.planIntent, item.kind)) {
        select.append(element("option", {
          text: "使用当前工作台的明确选择",
          attrs: { value: "explicit" },
        }));
      }
      select.addEventListener("change", () => {
        if (select.value === "") delete state.planConfirmations[key];
        else if (select.value === "explicit") state.planConfirmations[key] = { action: "keep_explicit" };
        else state.planConfirmations[key] = {
          action: "select_candidate",
          candidate: item.candidates[Number(select.value)],
        };
        updateGenerateState();
      });
      row.append(select);
      body.append(row);
    });
    panel.append(body);
    ui.naturalPlanContent.insertBefore(panel, ui.naturalPlanContent.firstChild);
  }

  function renderPlan(plan, resolution = normalizePlanResolution({}, plan), intent = plan) {
    state.plan = plan;
    state.planIntent = copyJson(intent, {});
    state.planResolution = resolution;
    state.planConfirmations = {};
    state.planDirty = false;
    ui.naturalPlanEmpty.hidden = true;
    ui.naturalPlanContent.hidden = false;
    ui.naturalPlanState.textContent = plan.provider_id ? "Provider 计划就绪" : "本地计划就绪";

    replace(ui.naturalPlanMeta, [
      ["任务", plan.job_type],
      ["管线", plan.pipeline],
      ["Provider", plan.provider_id || "本地确定性"],
      ["Matches", resolution.matches.length],
      ["Sources", Object.keys(resolution.sources).join(", ") || "--"],
      ["Requires confirmation", resolution.requiresConfirmation.length],
    ].map(([key, value]) => element("div", {}, [element("dt", { text: key }), element("dd", { text: value })])));
    renderResolution(resolution);
    replaceDetails(ui.naturalPlanLayers, Object.entries(plan.layers || {}), "无结构层数据");

    const locks = [];
    for (const [section, items] of Object.entries(plan.locked_pool_selection || {})) {
      for (const item of items || []) locks.push(`${section} · ${item.title_zh || item.title || item.id}`);
    }
    locks.push(...(plan.locked_tags || []));
    replace(ui.naturalPlanLocks, locks.length
      ? locks.map((value) => element("span", { text: value }))
      : [element("span", { className: "natural-muted", text: "未锁定字段" })]);

    ui.naturalPlanPositive.value = plan.positive_prompt || "";
    ui.naturalPlanNegative.value = plan.negative_prompt || "";
    const swap = plan.character_swap;
    ui.naturalSwapSection.hidden = !swap;
    replace(ui.naturalSwapDetails, swap ? [
      ["目标身份", swap.target_identity_trigger || "未验证"],
      ["验证证据", swap.identity_evidence || {}],
      ["分类器", swap.classifier || "未设置"],
      ["移除源身份", swap.removed_terms || []],
      ["加入目标身份", swap.added_terms || []],
      ["保留内容", swap.kept_terms || []],
      ["目标 LoRA", swap.target_lora || "纯语义模式"],
      ["保留角色 LoRA", swap.preserved_character_loras || []],
      ["角色 LoRA 约束", swap.forbid_character_loras ? "禁止未授权角色 LoRA" : "仅允许计划中的角色 LoRA"],
    ].map(([key, value]) => detailRow(key, value)) : []);
    replaceDetails(ui.naturalDiagnostics, Object.entries(plan.diagnostics || {}), "无诊断警告");
    updateGenerateState();
    setNaturalView("plan");
  }

  function planReference() {
    if (!state.plan) return {};
    const positive = ui.naturalPlanPositive.value.trim();
    const negative = ui.naturalPlanNegative.value.trim();
    const reference = {
      plan_id: state.plan.id,
      plan_revision: {
        id: state.plan.id,
        revision: Number(state.plan.revision || 1),
        digest: state.plan.digest || "",
      },
      plan_digest: state.plan.digest || "",
    };
    if (positive !== String(state.plan.positive_prompt || "").trim()
      || negative !== String(state.plan.negative_prompt || "").trim()) {
      const overrides = {
        positive_prompt: positive,
        negative_prompt: negative,
      };
      reference.plan_overrides = overrides;
      reference.overrides = overrides;
    }
    return reference;
  }

  async function preview() {
    showError();
    ui.naturalPreviewPlan.disabled = true;
    ui.naturalPlanState.textContent = "正在规划";
    try {
      const requestPayload = await buildPayload();
      const result = await postV7("/intents/preview", requestPayload);
      const raw = result.plan || result.intent || result;
      const resolution = normalizePlanResolution(result, raw);
      const plan = result.intent ? {
        ...raw,
        id: raw.id || raw.intent_id,
        job_type: requestPayload.job_type || raw.job_type || raw.mode,
        positive_prompt: raw.positive_prompt || "",
        negative_prompt: raw.negative_prompt || "",
        layers: raw.layers || { intent: "V7 GenerationIntent 已规范化" },
        diagnostics: raw.diagnostics || {
          resolution: result.resolution?.status || "resolved",
          confirmation: result.requires_confirmation?.length ? result.requires_confirmation : "无需确认",
        },
      } : raw;
      renderPlan(plan, resolution, result.intent || raw);
    } catch (error) {
      showError(error.message);
      ui.naturalPlanState.textContent = "计划失败";
    } finally {
      ui.naturalPreviewPlan.disabled = false;
    }
  }

  function addTimeline(event) {
    if (event.stage === "heartbeat") return;
    const row = element("div", { dataset: { stage: event.stage || event.state || "update" } }, [
      element("i"),
      element("span", {}, [
        element("strong", { text: event.message || event.stage || event.state || "任务更新" }),
        element("small", { text: formatTime(event.timestamp || Date.now()) }),
      ]),
    ]);
    ui.naturalTimeline.prepend(row);
  }

  function watchJob(jobId) {
    state.events?.close?.();
    const listener = async ({ detail: event }) => {
        if (!String(event.type || "").startsWith("job.")) return;
        const data = event.data || {};
        const job = data.job || data;
        if (String(job.id || job.job_id || event.entity_id || "") !== String(jobId)) return;
        const normalized = { ...job, stage: job.stage || job.state || job.status || event.type.split(".").pop() };
        addTimeline(normalized);
        ui.naturalPlanState.textContent = normalized.message || normalized.stage;
        if (TERMINAL_STAGES.has(normalized.stage) || TERMINAL_STAGES.has(normalized.state)) {
          window.removeEventListener("studio:v7-event", listener);
          state.events = null;
          state.activeJob = null;
          ui.naturalCancel.hidden = true;
          ui.naturalCancel.disabled = false;
          ui.naturalPreviewPlan.disabled = false;
          updateGenerateState();
          if (normalized.stage === "failed" || normalized.state === "failed") showError(normalized.error || "任务失败");
          await loadGallery();
          notifyJobsChanged();
        }
    };
    window.addEventListener("studio:v7-event", listener);
    state.events = { close: () => window.removeEventListener("studio:v7-event", listener) };
  }

  async function submit(event) {
    event.preventDefault();
    if (!state.plan) return preview();
    showError();
    const required = state.planResolution?.requiresConfirmation || [];
    if (hasUnresolvedConfirmations(required, state.planConfirmations || {})) {
      showError("请先确认所有歧义素材，生成不会自动猜测。");
      updateGenerateState();
      return;
    }
    ui.naturalGenerate.disabled = true;
    ui.naturalPreviewPlan.disabled = true;
    try {
      const frozen = state.planIntent || state.plan;
      const resolved = applyConfirmedResolution(frozen, required, state.planConfirmations || {});
      resolved.intent.positive_prompt = ui.naturalPlanPositive.value.trim();
      resolved.intent.negative_prompt = ui.naturalPlanNegative.value.trim();
      const result = await api("/api/v7/jobs", {
        method: "POST",
        body: JSON.stringify({
          intent: resolved.intent,
          resolution_confirmations: resolved.receipts,
          ...planReference(),
        }),
      });
      const job = result.job || result;
      state.activeJob = job;
      state.planDirty = false;
      ui.naturalCancel.hidden = TERMINAL_STAGES.has(job.state);
      addTimeline({ stage: job.stage, state: job.state, message: job.message, timestamp: job.updated_at });
      notifyJobsChanged();
      if (!ui.naturalCancel.hidden) watchJob(job.id);
      else await loadGallery();
    } catch (error) {
      showError(error.message);
      updateGenerateState();
      ui.naturalPreviewPlan.disabled = false;
    }
  }

  async function cancel() {
    if (!state.activeJob) return;
    try {
      await api(`/api/v7/jobs/${encodeURIComponent(state.activeJob.id)}/cancel`, { method: "POST", body: "{}" });
      ui.naturalCancel.disabled = true;
      notifyJobsChanged();
    } catch (error) {
      showError(error.message);
    }
  }

  ui.naturalPlanPositive.addEventListener("input", () => setDirty(true));
  ui.naturalPlanNegative.addEventListener("input", () => setDirty(true));

  return { addTimeline, cancel, invalidate, planReference, preview, renderPlan, submit, watchJob };
}
