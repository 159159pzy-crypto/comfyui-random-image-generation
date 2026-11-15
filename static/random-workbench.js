/* Random-only state: sampling seeds, local drafts and history/preset imports. */
function randomWorkbenchSeeds() {
  if (document.getElementById("randomSeedMode").value !== "fixed") return null;
  const sample_seed = Number(document.getElementById("randomSampleSeed").value);
  if (!Number.isSafeInteger(sample_seed) || sample_seed < 0 || sample_seed > 2 ** 50) throw new Error("请输入有效的非负整数图像种子");
  return { sample_seed };
}
let randomSettingsImportRequest = 0;
function randomWorkbenchBeginSettingsImport(record = null) {
  return { token: ++randomSettingsImportRequest, settings: JSON.stringify(readSettings()),
    seed: document.getElementById("randomSampleSeed").value, seedMode: document.getElementById("randomSeedMode").value,
    detail: typeof detailContext !== "undefined" ? detailContext : null,
    record: typeof selectedRecord !== "undefined" && selectedRecord === record ? record : null };
}
function randomWorkbenchSettingsImportCurrent(owner) {
  return owner.token === randomSettingsImportRequest && owner.settings === JSON.stringify(readSettings())
    && owner.seed === document.getElementById("randomSampleSeed").value && owner.seedMode === document.getElementById("randomSeedMode").value
    && (!owner.record || (selectedRecord === owner.record && (typeof detailContext === "undefined" || detailContext === owner.detail)));
}
async function randomWorkbenchRestoreHistory(record) {
  const owner = randomWorkbenchBeginSettingsImport(record);
  record = { ...record, settings: await canonicalizeWorkflowSettings(record.settings || {}) };
  if (!randomWorkbenchSettingsImportCurrent(owner)) return false;
  const current = readSettings();
  const pools = clone(profileDraftFromDraft());
  const settings = { ...clone(record.settings || {}), count: current.count, pools: clone(pools.pools), model_profile: "anima", workbench: "random", generation_mode: "txt2img" };
  for (const section of SECTIONS) {
    settings[`random_${section}`] = pools.modes[section] === "pool";
    settings[`random_${section}_count`] = Number(pools.counts[section] || 1);
    settings[`fixed_${section}`] = pools.modes[section] === "fixed" ? pools.fixed[section] || "" : "";
  }
  applySettings(settings);
  for (const key of ["modes", "counts", "fixed", "pools"]) draft[key] = clone(pools[key]);
  renderDimensions();
  updatePoolOverview();
  document.getElementById("randomSeedMode").value = "fixed";
  document.getElementById("randomSampleSeed").value = record.sample_seed ?? 0;
  sharedSamplingRestoreDrafts(null);
  sharedParameterVisibility();
  persistNow();
  return true;
}
async function randomWorkbenchApplyPreset(item, { isCurrent = () => true } = {}) {
  const owner = randomWorkbenchBeginSettingsImport();
  const captured = JSON.parse(owner.settings), replacements = captured.resource_replacements;
  delete captured.resource_replacements;
  const stillCurrent = () => isCurrent() && randomWorkbenchSettingsImportCurrent(owner);
  const profile = item.model_profile || item.settings?.model_profile || "anima";
  if (profile !== "anima") throw new Error("仅支持 Anima 风格预设");
  const resolved = await request(`/api/style-presets/${encodeURIComponent(item.id)}/resolve`, {
    method: "POST", body: JSON.stringify({ settings: captured, model_profile: profile }),
  });
  if (!stillCurrent()) return false;
  if (resolved.version_change) {
    const change = resolved.version_change;
    const accepted = await StylePresetManager.confirm({
      title: "应用预设的放大修复流程",
      message: `此预设携带放大修复，将生成流程从 v${change.from} 切换到 v${change.to}，以保留其修复效果。当前种子、批次数量和词池选择保持不变。`,
      confirmLabel: "应用预设",
    });
    if (!accepted || !stillCurrent()) return false;
  }
  if (!resolved.settings || resolved.settings.model_profile !== "anima" || ![4, 5].includes(resolved.settings.pipeline_version)) throw new Error("预设解析结果无效");
  if (Array.isArray(replacements)) resolved.settings.resource_replacements = clone(replacements);
  const modes = sharedSamplingDrafts();
  applySettings(resolved.settings, { preservePoolDraft: true });
  sharedSamplingRestoreDrafts(modes);
  if (resolved.notice) toast(resolved.notice);
  return true;
}
function initializeRandomWorkbench() {
  document.body.dataset.workbench = "random";
  initializeSharedParameters();
  for (const id of ["randomSeedMode", "randomSampleSeed"]) document.getElementById(id).addEventListener("change", () => {
    sharedParameterVisibility();
    schedulePersist();
  });
  document.getElementById("randomRollSeed").addEventListener("click", () => {
    document.getElementById("randomSampleSeed").value = crypto.getRandomValues(new Uint32Array(1))[0];
    document.getElementById("randomSeedMode").value = "fixed";
    sharedParameterVisibility();
    schedulePersist();
  });
  window.addEventListener("pagehide", persistNow);
}
