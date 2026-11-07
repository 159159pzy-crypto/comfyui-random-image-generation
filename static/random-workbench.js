/* Random-only state: sampling seeds, local drafts and history/preset imports. */
function randomWorkbenchSeeds() {
  if (document.getElementById("randomSeedMode").value !== "fixed") return null;
  const sample_seed = Number(document.getElementById("randomSampleSeed").value);
  if (!Number.isSafeInteger(sample_seed) || sample_seed < 0 || sample_seed > 2 ** 50) throw new Error("请输入有效的非负整数图像种子");
  return { sample_seed };
}
function randomWorkbenchRestoreHistory(record) {
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
}
function randomWorkbenchApplyPreset(item) {
  const current = readSettings();
  const snapshot = clone(item.settings || {});
  const modes = sharedSamplingDrafts();
  delete snapshot.clip_name;
  delete snapshot.vae_name;
  delete snapshot.clip_type;
  applySettings({ ...current, ...snapshot, workbench: "random", model_profile: "anima", generation_mode: "txt2img" });
  sharedSamplingRestoreDrafts(modes);
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
