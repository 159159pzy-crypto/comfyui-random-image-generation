const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/resource-resolution.js'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));

function setup() {
  const elements = new Map(), parents = new Map(), pending = [], saved = [], toasts = [], timers = new Map();
  let timerId = 0;
  const node = (id = '') => ({ id, hidden: true, textContent: '', value: '', children: [],
    append(...children) { this.children.push(...children); for (const child of children) if (child.id) elements.set(child.id, child); },
    replaceChildren(...children) { this.children = children; }, setAttribute() {}, addEventListener() {},
    querySelectorAll: () => [], });
  const stages = ['pixel_hires', 'latent', 'post_sampler', 'face_refine', 'seedvr2', 'nvidia', 'dlss'];
  const settings = { workbench: 'random', model_profile: 'anima', pipeline_version: 5, base_mode: 'normal',
    model_name: 'base', clip_name: 'clip', vae_name: 'vae', prompt: '',
    upscalers: Object.fromEntries(stages.map(stage => [stage, { enabled: false }])) };
  settings.upscalers.seedvr2.model_name = 'missing'; settings.upscalers.seedvr2.vae_name = 'old-vae';
  const ui = {};
  for (const id of ['model_name', 'clip_name', 'vae_name', 'seedvr2_model_name', 'seedvr2_vae_name', 'pixel_hires_model_name', 'face_upscaler_model', 'resourceWarning']) {
    ui[id] = node(id); elements.set(id, ui[id]);
  }
  ui.loraList = { parentElement: node() };
  Object.assign(ui.model_name, { value: 'base' }); Object.assign(ui.clip_name, { value: 'clip' }); Object.assign(ui.vae_name, { value: 'vae' });
  ui.seedvr2_model_name.value = 'missing'; ui.seedvr2_vae_name.value = 'old-vae';
  elements.set('randomDualSection', node());
  for (const stage of stages) parents.set(`.sampling-stage-${stage === 'face_refine' ? 'face' : stage}`, node());
  const ctx = vm.createContext({ ui, settings, JSON, Map, Set, Object, Array, String, clone: plain,
    escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
    resourcesLoaded: true, resourceInventoryProfile: 'anima', resourceError: '', resources: { model_profiles: { anima: { missing: ['old-default-seed'] } } },
    currentProfile: () => settings.model_profile,
    pipelineContractState: { source: {} },
    document: { getElementById: id => elements.get(id), createElement: () => node(), querySelector: selector => parents.get(selector) },
    Option: function(label, value) { return { textContent: label, value }; },
    ensureSelectValue: (element, value) => { element.value = value; },
    populateResourceSelect: (element, choices, selected) => { element.choices = choices; element.value = selected; },
    resolveInventoryPath: value => value,
    readSettings: () => ({ ...plain(settings), ...plain(ctx.pipelineContractState.source),
      model_name: ui.model_name.value, clip_name: ui.clip_name.value, vae_name: ui.vae_name.value,
      upscalers: { ...plain(settings.upscalers), seedvr2: { ...plain(settings.upscalers.seedvr2), model_name: ui.seedvr2_model_name.value, vae_name: ui.seedvr2_vae_name.value } } }),
    request: (url, options) => new Promise((resolve, reject) => pending.push({ url, body: JSON.parse(options.body), resolve, reject })),
    saveCurrentProfileDraft() {}, schedulePersist: () => saved.push(plain(ctx.readSettings())),
    updateGenerationAvailability() {}, toast: value => toasts.push(value),
    setTimeout: callback => { const id = ++timerId; timers.set(id, callback); return id; }, clearTimeout: id => timers.delete(id),
  });
  vm.runInContext(source, ctx);
  const resolve = async (index, { replacements = [], issues = [], effective = null } = {}) => {
    pending[index].resolve({ version: 1, ready: !issues.length, issues, replacements, effective_settings: effective || pending[index].body.settings });
    await tick();
  };
  return { ctx, ui, settings, pending, saved, toasts, elements, resolve,
    start: () => ctx.scheduleResourceResolution({ immediate: true }),
    run: code => vm.runInContext(code, ctx), flush: () => { const tasks = [...timers.values()]; timers.clear(); tasks.forEach(fn => fn()); } };
}
const seedReplacement = { module: 'seedvr2', field: 'upscalers.seedvr2.model_name', from: 'missing', to: 'installed/seed-7b.safetensors' };

test('disabled optional modules ignore old profile missing defaults and preserve their saved selection', async () => {
  const h = setup(); h.start(); await h.resolve(0);
  assert.equal(h.ui.resourceWarning.hidden, true);
  assert.equal(h.elements.get('resourceIssue_seedvr2').hidden, true);
  assert.equal(h.ui.seedvr2_model_name.value, 'missing');
  assert.deepEqual(Array.from(h.ctx.resourceResolutionIssues(h.ctx.readSettings())), []);
  assert.deepEqual(h.toasts, []);
});
test('enabling a module applies server substitutions once, saves their origin, and uses one readiness result', async () => {
  const h = setup(); h.settings.upscalers.seedvr2.enabled = true; h.start();
  assert.match(h.ctx.resourceResolutionIssues(h.ctx.readSettings())[0], /检查/);
  await h.resolve(0, { replacements: [seedReplacement] });
  assert.equal(h.ui.seedvr2_model_name.value, seedReplacement.to);
  assert.equal(h.saved.length, 1); assert.equal(h.toasts.length, 1);
  assert.deepEqual(h.saved[0].resource_replacements, [seedReplacement]);
  assert.deepEqual(Array.from(h.ctx.resourceResolutionIssues(h.ctx.readSettings())), []);
  h.start(); assert.equal(h.pending.length, 1); assert.equal(h.toasts.length, 1);
});
test('all enabled module problems render locally and disappear immediately when that module is disabled', async () => {
  const h = setup();
  for (const stage of Object.keys(h.settings.upscalers)) h.settings.upscalers[stage].enabled = true;
  h.start(); await h.resolve(0, { issues: Object.keys(h.settings.upscalers).map(module => ({ module, message: `${module} missing` })) });
  assert.equal(h.ui.resourceWarning.hidden, true);
  for (const stage of Object.keys(h.settings.upscalers)) assert.equal(h.elements.get(`resourceIssue_${stage}`).hidden, false);
  h.settings.upscalers.seedvr2.enabled = false; h.start();
  assert.equal(h.elements.get('resourceIssue_seedvr2').hidden, true);
  assert.equal(h.ui.seedvr2_model_name.value, 'missing');
});
test('a resource response cannot overwrite a newer choice, workbench, or profile', async () => {
  for (const change of [h => { h.ui.seedvr2_model_name.value = 'user-choice'; }, h => { h.settings.workbench = 'single'; }, h => { h.settings.model_profile = 'krea2'; }]) {
    const h = setup(); h.settings.upscalers.seedvr2.enabled = true; h.start(); change(h);
    await h.resolve(0, { replacements: [seedReplacement] });
    assert.notEqual(h.ui.seedvr2_model_name.value, seedReplacement.to);
    assert.equal(h.saved.length, 0); assert.equal(h.toasts.length, 0);
  }
});
test('newer resolution and inventory refresh invalidate old completions', async () => {
  const h = setup(); h.settings.upscalers.seedvr2.enabled = true; h.start();
  h.ctx.invalidateResourceResolution(); h.start(); await h.resolve(1);
  await h.resolve(0, { replacements: [seedReplacement] });
  assert.equal(h.ui.seedvr2_model_name.value, 'missing'); assert.equal(h.saved.length, 0);
  assert.deepEqual(Array.from(h.ctx.resourceResolutionIssues(h.ctx.readSettings())), []);
});
test('loading and offline inventory never trigger fallback; resolver failure remains a connection problem', async () => {
  const h = setup(); h.ctx.resourcesLoaded = false; h.start(); assert.equal(h.pending.length, 0);
  h.ctx.resourcesLoaded = true; h.start(); h.pending[0].reject(new Error('offline')); await tick();
  assert.match(h.ui.resourceWarning.textContent, /offline/);
  assert.equal(h.ui.seedvr2_model_name.value, 'missing'); assert.equal(h.saved.length, 0);
  assert.match(h.ctx.resourceResolutionIssues(h.ctx.readSettings())[0], /offline/);
});
test('compatible dropdowns include assigned models and keep unknown or incompatible candidates out', () => {
  const h = setup(); h.ctx.resources.resource_catalog = [
    { kind: 'clips', name: 'known', status: 'recognized', roles: ['anima.clip'] },
    { kind: 'clips', name: 'assigned', status: 'recognized', source: 'manual', roles: ['anima.clip'] },
    { kind: 'clips', name: 'unknown', status: 'unknown', roles: [] },
    { kind: 'clips', name: 'krea', status: 'recognized', roles: ['krea2.clip'] },
    { kind: 'upscale_models', name: 'upscale', status: 'recognized', roles: ['upscale'] },
  ];
  h.ctx.resources.node_inputs = { ForbiddenVisionFaceProcessorIntegrated: { upscaler_model: [['Fast 4x (Lanczos)', 'unsupported.pth']] } };
  h.ctx.populateCompatibleResources();
  assert.deepEqual(Array.from(h.ui.clip_name.choices), ['known', 'assigned']);
  assert.deepEqual(Array.from(h.ui.face_upscaler_model.choices), ['Fast 4x (Lanczos)', 'upscale']);
});
test('submission uses its frozen effective settings and carries auto-replacement provenance even after the draft changed', async () => {
  const h = setup(); h.settings.upscalers.seedvr2.enabled = true; h.start(); await h.resolve(0, { replacements: [seedReplacement] });
  const captured = h.ctx.readSettings(); const submission = h.ctx.resolveSubmissionResources(captured);
  assert.equal(h.pending[1].body.settings.resource_replacements, undefined);
  h.settings.workbench = 'single'; h.ui.seedvr2_model_name.value = 'new-draft';
  await h.resolve(1); const payload = plain(await submission);
  assert.equal(payload.workbench, 'random'); assert.equal(payload.upscalers.seedvr2.model_name, seedReplacement.to);
  assert.deepEqual(payload.resource_replacements, [seedReplacement]);
  assert.equal(h.ui.seedvr2_model_name.value, 'new-draft');
});
test('successive replacements retain the original source and a manual choice clears only its own provenance', () => {
  const h = setup();
  const merged = plain(h.ctx.mergeResourceReplacements([seedReplacement], [{ ...seedReplacement, from: seedReplacement.to, to: 'third' }]));
  assert.equal(merged[0].from, 'missing'); assert.equal(merged[0].to, 'third');
  h.ctx.pipelineContractState.source.resource_replacements = [...merged, { module: 'base', field: 'clip_name', from: 'old', to: 'new' }];
  h.ctx.clearDraftResourceReplacement('seedvr2_model_name');
  assert.deepEqual(plain(h.ctx.pipelineContractState.source.resource_replacements).map(item => item.field), ['clip_name']);
});

test('path normalization updates and persists canonical names without model-change notifications', async () => {
  const h = setup();
  const normalized = { module: 'base', field: 'model_name', from: 'base', to: 'folder/base', reason: 'path_normalized' };
  h.start(); await h.resolve(0, { replacements: [normalized] });
  assert.equal(h.ui.model_name.value, 'folder/base');
  assert.deepEqual(h.saved.at(-1).resource_replacements, [normalized]);
  assert.deepEqual(h.toasts, []);
  assert.equal(h.ctx.resourceReplacementText([normalized]), '');
  assert.equal(h.ctx.resourceSelectionChanged({ replacements: [normalized] }), false);
  assert.equal(h.ctx.resourceResolutionRecordMarkup({ bound_resources: { resource_resolution: { replacements: [normalized], resourceChanged: false } } }), '');
});

test('a later path normalization retains real replacement provenance including legacy drafts', () => {
  const h = setup();
  for (const original of [seedReplacement, { ...seedReplacement, reason: 'missing_model' }]) {
    const normalized = { ...seedReplacement, from: original.to, to: 'canonical/seed-7b.safetensors', reason: 'path_normalized' };
    const merged = plain(h.ctx.mergeResourceReplacements([original], [normalized]));
    assert.deepEqual(merged, [{ ...original, to: normalized.to }]);
    assert.equal(h.ctx.resourceSelectionChanged({ replacements: merged }), true);
  }
  const normalized = { ...seedReplacement, reason: 'path_normalized' };
  const real = { ...seedReplacement, from: normalized.to, to: 'other-model', reason: 'missing_model' };
  assert.deepEqual(plain(h.ctx.mergeResourceReplacements([normalized], [real])), [{ ...real, from: normalized.from }]);
});

test('all historical labels hide pure path normalization and honor the server resourceChanged flag', () => {
  const h = setup(), descriptions = {};
  h.ui.confirmReplay = {}; h.ui.replayDrawer = { querySelector: () => descriptions.replay ||= {} };
  h.elements.set('replayDrawerTitle', {});
  h.ui.variantModes = { querySelector: selector => ({ closest: () => ({ querySelector: () => descriptions[selector] ||= {} }) }) };
  const normalized = { module: 'base', field: 'model_name', from: 'base', to: 'folder/base', reason: 'path_normalized' };
  h.ctx.regenerationOptions = { modes: Object.fromEntries(['replay', 'prompt_variant', 'content_redraw', 'settings_reroll'].map(mode => [mode, { replacements: [normalized], resourceChanged: false }])) };
  h.ctx.updateHistoricalResourceLabels();
  assert.equal(h.ui.confirmReplay.textContent, '按原种子重跑');
  assert.doesNotMatch(h.elements.get('replayDrawerTitle').textContent, /非严格/);
  for (const mode of ['prompt_variant', 'content_redraw', 'settings_reroll']) assert.doesNotMatch(descriptions[`input[value="${mode}"]`].textContent, /替换模型/);
  h.ui.confirmVariant = {}; h.ui.variantStatus = { dataset: {} };
  h.ctx.selectedRegenerationMode = () => 'prompt_variant'; h.ctx.selectedRedrawSections = () => [];
  h.ctx.setRedrawSectionsVisible = () => {}; h.ctx.regenerationReasons = () => [];
  const drawerStart = app.indexOf('function updateVariantDrawer(');
  vm.runInContext(app.slice(drawerStart, app.indexOf('\n}', drawerStart) + 2), h.ctx);
  h.ctx.updateVariantDrawer(); assert.equal(h.ui.confirmVariant.textContent, '生成同提示词变体');
  const target = { children: [], append(node) { this.children.push(node); } };
  h.ctx.renderHistoricalResourceChanges(target, 'replay'); assert.equal(target.children.length, 0);
  h.ctx.regenerationOptions.modes.content_redraw = { resourceChanged: true, replacements: [normalized, seedReplacement] };
  h.ctx.renderHistoricalResourceChanges(target, 'content_redraw');
  assert.match(target.children[0].textContent, /missing.*seed-7b/);
  assert.doesNotMatch(target.children[0].textContent, /folder\/base/);
  assert.equal(h.ctx.resourceSelectionChanged({ resourceChanged: false, replacements: [seedReplacement] }), false);
});

test('queue admission updates the matching draft to its final model while retaining prompt edits and replacement origin', async () => {
  const h = setup(); h.settings.upscalers.seedvr2.enabled = true; h.start();
  await h.resolve(0, { replacements: [seedReplacement] });
  const submitted = h.ctx.readSettings(), key = h.ctx.resourceResolutionKey(submitted);
  h.settings.prompt = 'edited while submitting';
  const finalReplacement = { ...seedReplacement, to: 'final-model' };
  assert.equal(h.ctx.acceptSubmittedResourceResolution({ status: 'queued', resource_resolution: { replacements: [finalReplacement] } }, submitted, key), true);
  assert.equal(h.ui.seedvr2_model_name.value, 'final-model');
  assert.equal(h.ctx.readSettings().prompt, 'edited while submitting');
  assert.deepEqual(h.saved.at(-1).resource_replacements, [finalReplacement]);
  assert.deepEqual(Array.from(h.ctx.resourceResolutionIssues(h.ctx.readSettings())), []);
});

test('queue admission cannot overwrite a newer selection, workbench, profile or refreshed inventory', () => {
  for (const change of [h => { h.ui.seedvr2_model_name.value = 'manual-choice'; }, h => { h.settings.workbench = 'single'; },
    h => { h.settings.model_profile = 'krea2'; }, h => h.ctx.invalidateResourceResolution()]) {
    const h = setup(); h.settings.upscalers.seedvr2.enabled = true;
    const submitted = h.ctx.readSettings(), key = h.ctx.resourceResolutionKey(submitted);
    change(h);
    assert.equal(h.ctx.acceptSubmittedResourceResolution({ resource_resolution: { replacements: [seedReplacement] } }, submitted, key), false);
    assert.notEqual(h.ui.seedvr2_model_name.value, seedReplacement.to);
    assert.equal(h.saved.length, 0);
  }
});

test('queue admission retires an older pending editor resource check', async () => {
  const h = setup(); h.settings.upscalers.seedvr2.enabled = true; h.start();
  const submitted = h.ctx.readSettings(), key = h.ctx.resourceResolutionKey(submitted);
  const finalReplacement = { ...seedReplacement, to: 'admitted-model' };
  h.ctx.acceptSubmittedResourceResolution({ resource_resolution: { replacements: [finalReplacement] } }, submitted, key);
  await h.resolve(0, { replacements: [seedReplacement] });
  assert.equal(h.ui.seedvr2_model_name.value, 'admitted-model');
  assert.deepEqual(h.saved.at(-1).resource_replacements, [finalReplacement]);
});
test('prompt edits, counts, pool selections and replacement notes do not recheck resources while resource changes do', async () => {
  const h = setup(); h.start(); await h.resolve(0);
  h.settings.extra_prompt = 'new text'; h.settings.count = 8; h.settings.pools = { pose: { ids: ['new'] } };
  h.settings.upscalers.face_refine.face_positive_prompt = 'face text';
  h.ctx.pipelineContractState.source.resource_replacements = [seedReplacement];
  h.start(); assert.equal(h.pending.length, 1);
  h.settings.upscalers.face_refine.enabled = true; h.start(); assert.equal(h.pending.length, 2);
  await h.resolve(1); h.settings.sampler_name = 'other'; h.start(); assert.equal(h.pending.length, 3);
  await h.resolve(2); h.settings.zero_negative = true; h.start(); assert.equal(h.pending.length, 4);
  await h.resolve(3); h.settings.base_denoise = .4; h.start(); assert.equal(h.pending.length, 5);
});
test('encoder-type replacements update stored settings so the next check does not repeat them', async () => {
  const h = setup(); h.settings.clip_type = 'old-type'; h.start();
  await h.resolve(0, { replacements: [{ module: 'base', field: 'clip_type', from: 'old-type', to: 'krea2' }] });
  assert.equal(h.ctx.readSettings().clip_type, 'krea2');
  h.start(); assert.equal(h.pending.length, 1);
  assert.equal(h.saved[0].clip_type, 'krea2');
});
test('history replacement explanations are scoped to the selected operation', () => {
  const h = setup(); h.ctx.regenerationOptions = { modes: { replay: {}, content_redraw: { replacements: [seedReplacement] } } };
  const target = { children: [], append(node) { this.children.push(node); } };
  h.ctx.renderHistoricalResourceChanges(target, 'replay'); assert.equal(target.children.length, 0);
  h.ctx.renderHistoricalResourceChanges(target, 'content_redraw'); assert.match(target.children[0].textContent, /missing.*seed-7b/);
  assert.match(target.children[0].textContent, /不属于严格复现/);
});
test('strict replay and prompt variation keep original resources; redraw actions disclose substitutions', () => {
  const h = setup(), descriptions = {};
  h.ui.confirmReplay = {}; h.ui.replayDrawer = { querySelector: () => descriptions.replay ||= {} };
  h.elements.set('replayDrawerTitle', {});
  h.ui.variantModes = { querySelector: selector => ({ closest: () => ({ querySelector: () => descriptions[selector] ||= {} }) }) };
  h.ctx.regenerationOptions = { modes: Object.fromEntries(['replay', 'prompt_variant', 'content_redraw', 'settings_reroll'].map(mode => [mode, { replacements: [seedReplacement] }])) };
  h.ctx.updateHistoricalResourceLabels();
  assert.equal(h.ui.confirmReplay.textContent, '按原种子重跑');
  assert.equal(h.elements.get('replayDrawerTitle').textContent, '按原种子重跑');
  assert.match(descriptions.replay.textContent, /双种子/);
  for (const mode of ['content_redraw', 'settings_reroll']) assert.match(descriptions[`input[value="${mode}"]`].textContent, /替换模型/);
});
test('the real generation gate reads the shared resolver instead of the old profile-level missing list', async () => {
  const h = setup(); const start = app.indexOf('function generationClassificationIssues(');
  vm.runInContext(app.slice(start, app.indexOf('\n}', start) + 2), h.ctx);
  h.start(); await h.resolve(0); assert.deepEqual(Array.from(h.ctx.generationClassificationIssues(h.ctx.readSettings())), []);
  h.settings.upscalers.seedvr2.enabled = true; h.start();
  await h.resolve(1, { issues: [{ module: 'seedvr2', field: 'upscalers.seedvr2.vae_name', message: 'missing Seed VAE' }] });
  assert.deepEqual(Array.from(h.ctx.generationClassificationIssues(h.ctx.readSettings())), ['missing Seed VAE']);
  assert.equal(h.elements.get('resourceIssue_seedvr2').textContent, 'missing Seed VAE');
});
test('draft normalization keeps provenance locally without passing editor-only fields into strict validation', async () => {
  const h = setup(); const start = app.indexOf('async function canonicalizeWorkflowSettings(');
  vm.runInContext(app.slice(start, app.indexOf('\n}', start) + 2), h.ctx);
  const normalized = h.ctx.canonicalizeWorkflowSettings({ model_name: 'chosen', resource_replacements: [seedReplacement] });
  assert.deepEqual(h.pending[0].body, { settings: { model_name: 'chosen' } });
  h.pending[0].resolve({ settings: { model_name: 'chosen', pipeline_version: 5 } });
  assert.deepEqual(plain(await normalized), { model_name: 'chosen', pipeline_version: 5, resource_replacements: [seedReplacement] });
});
test('history shows recorded substitutions safely without recomputing choices from the current inventory', () => {
  const h = setup();
  const markup = h.ctx.resourceResolutionRecordMarkup({ bound_resources: { resource_resolution: { replacements: [{ ...seedReplacement, from: '<old>', to: '<actual>' }] } } });
  assert.match(markup, /&lt;old&gt;.*&lt;actual&gt;/); assert.doesNotMatch(markup, /<actual>/);
  assert.equal(h.ctx.resourceResolutionRecordMarkup({ settings: { resource_replacements: [seedReplacement] } }), '');
});


test('an existing selected codec remains usable without inspection while other unknown files stay out', () => {
  const h = setup();
  h.ctx.resources.clips = ['encoders/selected.safetensors', 'other.safetensors'];
  h.ctx.resources.vaes = ['selected-vae.safetensors'];
  h.ctx.resources.resource_catalog = [
    { kind: 'clips', name: 'encoders/selected.safetensors', status: 'unknown', roles: [] },
    { kind: 'clips', name: 'other.safetensors', status: 'unknown', roles: [] },
    { kind: 'vaes', name: 'selected-vae.safetensors', status: 'incompatible', roles: [] },
  ];
  h.ui.clip_name.value = 'ENCODERS\\selected.safetensors';
  h.ui.vae_name.value = 'selected-vae.safetensors';
  h.ctx.populateCompatibleResources();
  assert.deepEqual(Array.from(h.ui.clip_name.choices), ['encoders/selected.safetensors']);
  assert.deepEqual(Array.from(h.ui.vae_name.choices), []);
  h.ui.clip_name.value = 'selected.safetensors';
  h.ctx.populateCompatibleResources();
  assert.deepEqual(Array.from(h.ui.clip_name.choices), []);
});
