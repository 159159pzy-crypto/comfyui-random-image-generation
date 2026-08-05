import { api } from "./api.js";
import { element, replace } from "./dom.js";

const ROOT = "/api/v7/studio";
const byId = (id) => document.getElementById(id);
const ui = Object.fromEntries([
  "studioAdmin", "studioAdminStatus", "studioAdminRefresh", "studioDiagnosticsRefresh", "studioDiagnostics", "studioOperations",
  "studioPromptAssetQuery", "studioPromptAssetSearch", "studioPromptAssetList", "studioPromptAssetImportJson", "studioPromptAssetImport", "studioPromptAssetUpdateUrl", "studioPromptAssetUpdate",
  "studioPromptLabPrompt", "studioPromptLabCreate", "studioPromptLabCount", "studioPromptLabGenerate", "studioPromptLabBatch", "studioPromptLabList",
  "studioPromptPlanName", "studioPromptPlanJson", "studioPromptPlanSave", "studioPromptPlanRefresh", "studioPromptPlanList",
  "studioLoraFilter", "studioLoraRefresh", "studioLoraVisuals", "studioLoraList", "studioLoraDetail", "studioLoraAnalyze", "studioLoraArchive", "studioLoraDownloadUrl", "studioLoraDownload", "studioLoraOutput",
  "studioDanbooruQuery", "studioDanbooruSearch", "studioDanbooruMode", "studioDanbooruBuild", "studioDanbooruScheduleEnabled", "studioDanbooruScheduleInterval", "studioDanbooruScheduleSave", "studioDanbooruScheduleRun", "studioDanbooruOutput",
  "studioModelsRefresh", "studioModelsInventory", "studioModelKind", "studioModelExactName", "studioModelConfirmName", "studioModelQuarantine", "studioQuarantineList",
  "studioWorkflowsRefresh", "studioWorkflowList", "studioProfileName", "studioProfileJson", "studioProfileSave", "studioProfileImport", "studioProfileList",
  "studioLogFilter", "studioLogLevel", "studioLogApplyLevel", "studioLogsRefresh", "studioLogsClear", "studioLogList",
].map((id) => [id, byId(id)]));

const state = { loras: [], loraProfiles: [], identityBindings: [], selectedLoras: new Set(), logs: [], initialized: false };
const itemsOf = (payload) => Array.isArray(payload) ? payload : payload?.items || payload?.records || payload?.entries || [];
const textOf = (value) => typeof value === "string" ? value : JSON.stringify(value ?? {}, null, 2);
const labelOf = (item, fallback = "未命名") => String(item?.display_name || item?.name || item?.filename || item?.title || item?.id || fallback);

function setStatus(message, kind = "") {
  ui.studioAdminStatus.textContent = message;
  ui.studioAdminStatus.dataset.kind = kind;
}

function showJson(target, value) {
  target.textContent = textOf(value);
}

function markUnavailable(button, error) {
  if (!button || ![404, 501, 503].includes(error?.status)) return;
  button.disabled = true;
  button.title = `当前服务不可用：${error.message}`;
}

async function perform(button, task, { pending = "处理中…", success = "操作已提交" } = {}) {
  const original = button?.textContent;
  if (button) { button.disabled = true; button.textContent = pending; }
  setStatus(pending);
  try {
    const result = await task();
    setStatus(success, "success");
    return result;
  } catch (error) {
    setStatus(error.message || String(error), "error");
    markUnavailable(button, error);
    throw error;
  } finally {
    if (button && !button.title.startsWith("当前服务不可用")) button.disabled = false;
    if (button) button.textContent = original;
  }
}

async function manual(button, message, path, body = {}, options = {}) {
  if (!window.confirm(message)) return null;
  return perform(button, () => api(`${ROOT}${path}`, {
    method: options.method || "POST",
    body: JSON.stringify({ ...body, confirm_manual: true }),
  }), options);
}

function row(title, subtitle = "", actions = []) {
  return element("div", { className: "studio-row" }, [
    element("span", {}, [element("strong", { text: title }), element("small", { text: subtitle })]),
    element("div", { className: "studio-row-actions" }, actions),
  ]);
}

function action(label, handler, { danger = false } = {}) {
  const button = element("button", { text: label, className: `button ${danger ? "danger" : "ghost"} compact`, attrs: { type: "button" } });
  button.addEventListener("click", handler);
  return button;
}

function parseJsonField(input, fallback) {
  const raw = input.value.trim();
  if (!raw) return fallback;
  const parsed = JSON.parse(raw);
  if (fallback instanceof Array && !Array.isArray(parsed)) throw new Error("必须填写 JSON 数组");
  if (!(fallback instanceof Array) && (!parsed || Array.isArray(parsed) || typeof parsed !== "object")) throw new Error("必须填写 JSON 对象");
  return parsed;
}

async function loadDiagnostics() {
  const payload = await api(`${ROOT}/diagnostics`);
  const capabilities = payload.capabilities || {};
  replace(ui.studioDiagnostics, Object.entries(capabilities).map(([name, value]) =>
    row(name, value?.ready === false ? value.reason || "不可用" : value?.available === false ? value.reason || "未配置" : "可用"),
  ));
  const operations = payload.operations?.items || [];
  replace(ui.studioOperations, operations.length ? operations.map((operation) => {
    const id = operation.id || operation.run_id;
    const status = operation.status || operation.state || "unknown";
    const actions = ["queued", "running", "planning", "cancelling"].includes(status) && id
      ? [action("取消", async (event) => {
        try {
          await perform(event.currentTarget, () => api(`${ROOT}/operations/${encodeURIComponent(id)}/cancel`, { method: "POST", body: "{}" }), { pending: "取消中…", success: "操作已取消" });
          await loadDiagnostics();
        } catch { /* status already rendered */ }
      }, { danger: true })] : [];
    return row(operation.message || operation.task_type || operation.type || String(id), `${status} · ${operation.source_workspace || "studio"}`, actions);
  }) : [element("p", { className: "studio-empty", text: "没有进行中的 Studio 操作" })]);
  return payload;
}

async function searchPromptAssets() {
  const query = encodeURIComponent(ui.studioPromptAssetQuery.value.trim());
  const payload = await api(`/api/v7/prompt-assets?q=${query}&limit=100`);
  const assets = itemsOf(payload);
  replace(ui.studioPromptAssetList, assets.length ? assets.map((item) => row(
    labelOf(item),
    [item.asset_type || item.type, item.category, item.source].filter(Boolean).join(" · "),
  )) : [element("p", { className: "studio-empty", text: "没有匹配素材" })]);
}

async function importPromptAssets() {
  try {
    const assets = parseJsonField(ui.studioPromptAssetImportJson, []);
    await perform(ui.studioPromptAssetImport, () => api(`${ROOT}/prompt-assets/import`, {
      method: "POST", body: JSON.stringify({ assets, source: "anima-native", mode: "replace_source" }),
    }), { pending: "导入中…", success: `已导入 ${assets.length} 项素材` });
    await searchPromptAssets();
  } catch (error) { setStatus(error.message, "error"); }
}

async function updatePromptAssets() {
  const url = ui.studioPromptAssetUpdateUrl.value.trim();
  if (!url) return setStatus("请输入远程更新 URL", "error");
  try { await manual(ui.studioPromptAssetUpdate, `确认从此地址联网更新 Prompt Asset？\n${url}`, "/prompt-assets/update", { url }, { pending: "提交更新…", success: "Prompt Asset 更新任务已入队" }); } catch { /* rendered */ }
}

async function loadPromptLab() {
  const payload = await api(`${ROOT}/prompt-lab`);
  const records = itemsOf(payload);
  replace(ui.studioPromptLabList, records.length ? records.map((item) => row(
    labelOf(item, item.prompt || "候选"), item.status || "draft", [
      action("确认", async (event) => {
        try { await perform(event.currentTarget, () => api(`${ROOT}/prompt-lab/${encodeURIComponent(item.id)}/confirm`, { method: "POST", body: "{}" }), { success: "候选已确认" }); await loadPromptLab(); } catch { /* rendered */ }
      }),
      action("编辑", async () => {
        const prompt = window.prompt("更新候选 Prompt", item.prompt || "");
        if (prompt == null) return;
        try { await api(`${ROOT}/prompt-lab/${encodeURIComponent(item.id)}`, { method: "PUT", body: JSON.stringify({ ...item, prompt }) }); await loadPromptLab(); } catch (error) { setStatus(error.message, "error"); }
      }),
      action("删除", async (event) => {
        if (!window.confirm("确认删除该 Prompt Lab 候选？")) return;
        try { await perform(event.currentTarget, () => api(`${ROOT}/prompt-lab/${encodeURIComponent(item.id)}`, { method: "DELETE" }), { success: "候选已删除" }); await loadPromptLab(); } catch { /* rendered */ }
      }, { danger: true }),
    ],
  )) : [element("p", { className: "studio-empty", text: "尚无 Prompt Lab 候选" })]);
}

async function createPromptLab() {
  const prompt = ui.studioPromptLabPrompt.value.trim();
  if (!prompt) return setStatus("请输入 Prompt Lab 候选", "error");
  try { await perform(ui.studioPromptLabCreate, () => api(`${ROOT}/prompt-lab`, { method: "POST", body: JSON.stringify({ prompt }) }), { success: "候选已保存" }); await loadPromptLab(); } catch { /* rendered */ }
}

async function generatePromptLab() {
  const body = { count: Number(ui.studioPromptLabCount.value || 3) };
  if (ui.studioPromptLabPrompt.value.trim()) body.prompt = ui.studioPromptLabPrompt.value.trim();
  try {
    const batch = await perform(ui.studioPromptLabGenerate, () => api(`${ROOT}/prompt-lab/candidates`, { method: "POST", body: JSON.stringify(body) }), { pending: "生成中…", success: "候选组已生成" });
    const batchId = batch.id || batch.batch_id;
    const candidates = batch.candidates || batch.items || [];
    replace(ui.studioPromptLabBatch, candidates.map((candidate, index) => row(
      labelOf(candidate, typeof candidate === "string" ? candidate : `候选 ${index + 1}`), "", [
        action("采用", async (event) => {
          try { await perform(event.currentTarget, () => api(`${ROOT}/prompt-lab/batches/${encodeURIComponent(batchId)}/confirm`, { method: "POST", body: JSON.stringify({ selection: index + 1 }) }), { success: "候选已采用" }); await loadPromptLab(); } catch { /* rendered */ }
        }),
      ],
    )));
  } catch { /* rendered */ }
}

async function loadPromptPlans() {
  try {
    const payload = await api(`${ROOT}/prompt-plans`);
    const plans = itemsOf(payload);
    replace(ui.studioPromptPlanList, plans.length ? plans.map((plan) => row(labelOf(plan), `r${plan.revision || 1}`, [
      action("载入", () => { ui.studioPromptPlanName.value = plan.name || plan.title || ""; ui.studioPromptPlanJson.value = JSON.stringify(plan, null, 2); }),
      action("更新", async (event) => {
        try { const body = { ...parseJsonField(ui.studioPromptPlanJson, {}), name: ui.studioPromptPlanName.value.trim() }; await perform(event.currentTarget, () => api(`${ROOT}/prompt-plans/${encodeURIComponent(plan.id)}`, { method: "PUT", body: JSON.stringify(body) }), { success: "Plan 已更新" }); await loadPromptPlans(); } catch (error) { setStatus(error.message, "error"); }
      }),
      action("删除", async (event) => { if (!window.confirm("确认删除该 Prompt Plan？")) return; try { await perform(event.currentTarget, () => api(`${ROOT}/prompt-plans/${encodeURIComponent(plan.id)}`, { method: "DELETE" }), { success: "Plan 已删除" }); await loadPromptPlans(); } catch { /* rendered */ } }, { danger: true }),
    ])) : [element("p", { className: "studio-empty", text: "尚无 Prompt Plan" })]);
  } catch (error) {
    replace(ui.studioPromptPlanList, [element("p", { className: "studio-unavailable", text: `Prompt Plan 管理不可用：${error.message}` })]);
    markUnavailable(ui.studioPromptPlanSave, error);
  }
}

async function savePromptPlan() {
  try {
    const body = { ...parseJsonField(ui.studioPromptPlanJson, {}), name: ui.studioPromptPlanName.value.trim() };
    await perform(ui.studioPromptPlanSave, () => api(`${ROOT}/prompt-plans`, { method: "POST", body: JSON.stringify(body) }), { success: "Prompt Plan 已创建" });
    await loadPromptPlans();
  } catch (error) { setStatus(error.message, "error"); }
}

function loraFilename(item) { return String(item.filename || item.name || ""); }
function renderLoras() {
  const query = ui.studioLoraFilter.value.trim().toLocaleLowerCase();
  const records = state.loras.filter((item) => !query || `${loraFilename(item)} ${item.display_name || ""}`.toLocaleLowerCase().includes(query));
  replace(ui.studioLoraList, records.length ? records.map((item) => {
    const filename = loraFilename(item);
    const profile = state.loraProfiles.find((candidate) => String(candidate.filename || "").toLocaleLowerCase() === filename.toLocaleLowerCase());
    const bindings = profile ? state.identityBindings.filter((binding) => (binding.lora_profile_ids || []).includes(profile.id)) : [];
    const verified = bindings.filter((binding) => binding.verification_status === "verified").length;
    const identityStatus = profile
      ? `${profile.file_status || "unverified"} · ${verified}/${bindings.length} verified`
      : "未建立 Schema v3 档案";
    const checkbox = element("input", { attrs: { type: "checkbox", "aria-label": `选择 ${filename}` } });
    checkbox.checked = state.selectedLoras.has(filename);
    checkbox.addEventListener("change", () => checkbox.checked ? state.selectedLoras.add(filename) : state.selectedLoras.delete(filename));
    return element("label", { className: "studio-choice" }, [checkbox, element("span", {}, [element("strong", { text: labelOf(item) }), element("small", { text: `${filename} · ${identityStatus}` })])]);
  }) : [element("p", { className: "studio-empty", text: "LoRA 目录为空" })]);
}

async function loadLoras() {
  const [payload, profiles, identities] = await Promise.all([
    api(`${ROOT}/loras`),
    api(`${ROOT}/lora-profiles`),
    api(`${ROOT}/identities`),
  ]);
  state.loras = itemsOf(payload);
  state.loraProfiles = itemsOf(profiles);
  state.identityBindings = itemsOf(identities);
  renderLoras();
}
const selectedLoras = () => [...state.selectedLoras];

async function loraOperation(button, path, message, extra = {}) {
  const names = selectedLoras();
  if (!names.length && path !== "/loras/download" && path !== "/loras/refresh") return setStatus("请先选择至少一个 LoRA", "error");
  try {
    const result = await manual(button, message, path, { selected_names: names, ...extra }, { pending: "提交中…", success: "LoRA 操作已提交" });
    if (result) showJson(ui.studioLoraOutput, result);
  } catch { /* rendered */ }
}

async function loadDanbooru() {
  try {
    const payload = await api(`${ROOT}/danbooru`);
    const schedule = payload.schedule || {};
    ui.studioDanbooruScheduleEnabled.checked = Boolean(schedule.enabled);
    ui.studioDanbooruScheduleInterval.value = String(schedule.interval_hours || 168);
    showJson(ui.studioDanbooruOutput, payload);
  }
  catch (error) { showJson(ui.studioDanbooruOutput, { unavailable: error.message }); }
}

async function loadModels() {
  const [inventory, quarantine] = await Promise.all([api("/api/v7/assets/models"), api(`${ROOT}/models/quarantine`)]);
  showJson(ui.studioModelsInventory, inventory);
  const entries = itemsOf(quarantine);
  replace(ui.studioQuarantineList, entries.length ? entries.map((entry) => row(labelOf(entry), `${entry.kind || "model"} · ${entry.quarantined_at || ""}`, [
    action("恢复", async (event) => {
      const name = entry.name || entry.exact_name || entry.relative_path;
      try { const result = await manual(event.currentTarget, `确认恢复隔离项？\n${name}`, `/models/quarantine/${encodeURIComponent(entry.id)}/restore`, { confirm_name: name }, { success: "模型已恢复" }); if (result) await loadModels(); } catch { /* rendered */ }
    }, { danger: true }),
  ])) : [element("p", { className: "studio-empty", text: "隔离区为空" })]);
}

async function quarantineModel() {
  const exactName = ui.studioModelExactName.value.trim();
  const confirmName = ui.studioModelConfirmName.value.trim();
  if (!exactName || exactName !== confirmName) return setStatus("两次输入的精确相对路径必须一致", "error");
  try { const result = await manual(ui.studioModelQuarantine, `确认将此文件移入可恢复隔离区？\n${exactName}`, "/models/quarantine", { kind: ui.studioModelKind.value, exact_name: exactName, confirm_name: confirmName }, { success: "模型已隔离" }); if (result) await loadModels(); } catch { /* rendered */ }
}

async function loadWorkflows() {
  const [workflows, profiles] = await Promise.all([api(`${ROOT}/workflows`), api(`${ROOT}/config-profiles`)]);
  replace(ui.studioWorkflowList, itemsOf(workflows).map((item) => row(labelOf(item), [item.file, item.ready === false ? "不可用" : "可用"].filter(Boolean).join(" · "))));
  replace(ui.studioProfileList, itemsOf(profiles).map((profile) => {
    const name = profile.name || profile.id;
    return row(name, profile.active ? "当前启用" : `r${profile.revision || 1}`, [
      action("启用", async (event) => { try { await perform(event.currentTarget, () => api(`${ROOT}/config-profiles/${encodeURIComponent(name)}/activate`, { method: "POST", body: "{}" }), { success: "档案已启用" }); await loadWorkflows(); } catch { /* rendered */ } }),
      action("导出", async () => { try { const value = await api(`${ROOT}/config-profiles/${encodeURIComponent(name)}/export`); ui.studioProfileName.value = name; ui.studioProfileJson.value = JSON.stringify(value, null, 2); } catch (error) { setStatus(error.message, "error"); } }),
      action("删除", async (event) => { if (!window.confirm(`确认删除环境档案 ${name}？`)) return; try { await perform(event.currentTarget, () => api(`${ROOT}/config-profiles/${encodeURIComponent(name)}`, { method: "DELETE" }), { success: "档案已删除" }); await loadWorkflows(); } catch { /* rendered */ } }, { danger: true }),
    ]);
  }));
}

async function saveProfile(importing = false) {
  try {
    const config = parseJsonField(ui.studioProfileJson, {});
    const name = ui.studioProfileName.value.trim();
    const path = importing ? "/config-profiles/import" : "/config-profiles";
    const body = importing ? { profile: { ...config, name }, overwrite: true } : { name, config, overwrite: true };
    await perform(importing ? ui.studioProfileImport : ui.studioProfileSave, () => api(`${ROOT}${path}`, { method: "POST", body: JSON.stringify(body) }), { success: importing ? "档案已导入" : "档案已保存" });
    await loadWorkflows();
  } catch (error) { setStatus(error.message, "error"); }
}

function renderLogs() {
  const query = ui.studioLogFilter.value.trim().toLocaleLowerCase();
  const records = state.logs.filter((item) => !query || textOf(item).toLocaleLowerCase().includes(query));
  replace(ui.studioLogList, records.length ? records.map((item) => row(
    item.message || item.event || item.stage || item.level || "日志",
    [item.level, item.timestamp || item.created_at].filter(Boolean).join(" · "),
  )) : [element("p", { className: "studio-empty", text: "没有匹配日志" })]);
}

async function loadLogs() {
  const payload = await api(`${ROOT}/logs?limit=500`);
  state.logs = [...(payload.items || []), ...(payload.natural_items || [])];
  renderLogs();
}

async function refreshAll() {
  setStatus("正在刷新 Studio 管理数据…");
  const tasks = [loadDiagnostics(), searchPromptAssets(), loadPromptLab(), loadPromptPlans(), loadLoras(), loadDanbooru(), loadModels(), loadWorkflows(), loadLogs()];
  const results = await Promise.allSettled(tasks);
  const failures = results.filter((item) => item.status === "rejected");
  setStatus(failures.length ? `${results.length - failures.length}/${results.length} 项可用，其余能力未配置` : "Studio 管理数据已刷新", failures.length ? "warning" : "success");
}

function bind() {
  if (!ui.studioAdmin || state.initialized) return;
  state.initialized = true;
  ui.studioAdminRefresh.addEventListener("click", refreshAll);
  ui.studioDiagnosticsRefresh.addEventListener("click", () => loadDiagnostics().catch((error) => setStatus(error.message, "error")));
  ui.studioPromptAssetSearch.addEventListener("click", () => searchPromptAssets().catch((error) => setStatus(error.message, "error")));
  ui.studioPromptAssetImport.addEventListener("click", importPromptAssets);
  ui.studioPromptAssetUpdate.addEventListener("click", updatePromptAssets);
  ui.studioPromptLabCreate.addEventListener("click", createPromptLab);
  ui.studioPromptLabGenerate.addEventListener("click", generatePromptLab);
  ui.studioPromptPlanSave.addEventListener("click", savePromptPlan);
  ui.studioPromptPlanRefresh.addEventListener("click", loadPromptPlans);
  ui.studioLoraFilter.addEventListener("input", renderLoras);
  ui.studioLoraRefresh.addEventListener("click", () => loraOperation(ui.studioLoraRefresh, "/loras/refresh", "确认刷新本地 LoRA 目录？"));
  ui.studioLoraVisuals.addEventListener("click", async () => { try { showJson(ui.studioLoraOutput, await api(`${ROOT}/loras/visuals?page=1&page_size=48`)); } catch (error) { setStatus(error.message, "error"); markUnavailable(ui.studioLoraVisuals, error); } });
  ui.studioLoraDetail.addEventListener("click", async () => { const filename = selectedLoras()[0]; if (!filename) return setStatus("请选择一个 LoRA", "error"); try { const value = await manual(ui.studioLoraDetail, `确认读取 LoRA 详情？\n${filename}`, "/loras/detail", { filename }, { success: "详情已读取" }); if (value) showJson(ui.studioLoraOutput, value); } catch { /* rendered */ } });
  ui.studioLoraAnalyze.addEventListener("click", () => loraOperation(ui.studioLoraAnalyze, "/loras/analyze", "确认使用已配置服务分析所选 LoRA？"));
  ui.studioLoraArchive.addEventListener("click", () => loraOperation(ui.studioLoraArchive, "/loras/archive", "确认生成所选 LoRA 的归档与索引？"));
  ui.studioLoraDownload.addEventListener("click", () => { const url = ui.studioLoraDownloadUrl.value.trim(); if (!url) return setStatus("请输入 LoRA 下载 URL", "error"); return loraOperation(ui.studioLoraDownload, "/loras/download", `确认联网下载 LoRA？\n${url}`, { url }); });
  ui.studioDanbooruSearch.addEventListener("click", async () => { try { showJson(ui.studioDanbooruOutput, await api(`${ROOT}/danbooru/search?q=${encodeURIComponent(ui.studioDanbooruQuery.value.trim())}`)); } catch (error) { setStatus(error.message, "error"); } });
  ui.studioDanbooruBuild.addEventListener("click", async () => { try { const value = await manual(ui.studioDanbooruBuild, "确认联网构建 Danbooru 索引？该操作可能耗时较长。", "/danbooru/build", { mode: ui.studioDanbooruMode.value }, { pending: "提交构建…", success: "Danbooru 构建任务已入队" }); if (value) showJson(ui.studioDanbooruOutput, value); } catch { /* rendered */ } });
  ui.studioDanbooruScheduleSave.addEventListener("click", async () => { try { const enabled = ui.studioDanbooruScheduleEnabled.checked; const value = await manual(ui.studioDanbooruScheduleSave, `确认${enabled ? "启用" : "停用"} Danbooru 定期更新？`, "/danbooru/schedule", { enabled, interval_hours: Number(ui.studioDanbooruScheduleInterval.value || 168), options: { mode: ui.studioDanbooruMode.value } }, { method: "PUT", pending: "保存计划…", success: "Danbooru 更新计划已保存" }); if (value) await loadDanbooru(); } catch { /* rendered */ } });
  ui.studioDanbooruScheduleRun.addEventListener("click", async () => { try { const value = await manual(ui.studioDanbooruScheduleRun, "确认联网执行已到期的 Danbooru 更新？", "/danbooru/schedule/run", { force: false }, { pending: "提交更新…", success: "Danbooru 更新任务已入队" }); if (value) showJson(ui.studioDanbooruOutput, value); } catch { /* rendered */ } });
  ui.studioModelsRefresh.addEventListener("click", async () => { try { const value = await manual(ui.studioModelsRefresh, "确认重新扫描模型与 UNET？", "/models/refresh", {}, { success: "模型清单已刷新" }); if (value) { showJson(ui.studioModelsInventory, value); await loadModels(); } } catch { /* rendered */ } });
  ui.studioModelQuarantine.addEventListener("click", quarantineModel);
  ui.studioWorkflowsRefresh.addEventListener("click", () => loadWorkflows().catch((error) => setStatus(error.message, "error")));
  ui.studioProfileSave.addEventListener("click", () => saveProfile(false));
  ui.studioProfileImport.addEventListener("click", () => saveProfile(true));
  ui.studioLogFilter.addEventListener("input", renderLogs);
  ui.studioLogsRefresh.addEventListener("click", () => loadLogs().catch((error) => setStatus(error.message, "error")));
  ui.studioLogApplyLevel.addEventListener("click", async () => { try { await perform(ui.studioLogApplyLevel, () => api(`${ROOT}/logs/level`, { method: "PUT", body: JSON.stringify({ level: ui.studioLogLevel.value }) }), { success: "日志级别已更新" }); } catch { /* rendered */ } });
  ui.studioLogsClear.addEventListener("click", async () => { try { const result = await manual(ui.studioLogsClear, "确认清空可清理的 Studio 日志？", "/logs", {}, { method: "DELETE", success: "日志已清空" }); if (result) await loadLogs(); } catch { /* rendered */ } });

  const dialog = byId("naturalSettingsDialog");
  const observeOpen = () => { if (dialog?.open) refreshAll(); };
  new MutationObserver(observeOpen).observe(dialog, { attributes: true, attributeFilter: ["open"] });
  if (dialog.open) refreshAll();
}

bind();
