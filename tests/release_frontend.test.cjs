const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function functionSource(name) {
  let start = source.indexOf(`function ${name}(`);
  if (start < 0) return '';
  if (source.slice(start - 6, start) === 'async ') start -= 6;
  return source.slice(start, source.indexOf('\n}', start) + 2);
}
function load(ctx, ...names) { for (const name of names) vm.runInContext(functionSource(name), ctx); }
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
const plain = value => JSON.parse(JSON.stringify(value));

function normalizationContext() {
  const requests = [], changes = [];
  const ctx = vm.createContext({
    promptNormalizationEpoch: 0, promptNormalizationRequests: new Map(),
    draft: { fixed: {}, managedTriggers: [], loras: [] }, clone: plain, loraItem: () => null,
    ui: { extra_prompt: { value: 'old' }, dimensionList: { querySelector: () => null } },
    document: { getElementById: () => null },
    currentProfile: () => 'anima', singleWorkbenchState: { ready: true, mode: 'random' },
    request: (url, options) => { const gate = deferred(); gate.url = url; gate.body = JSON.parse(options.body); requests.push(gate); return gate.promise; },
    setNormalizationUndo: value => changes.push(value), schedulePersist() {},
  });
  load(ctx, 'normalizeTriggerWords', 'promptNormalizationContext', 'promptElementForField', 'invalidatePromptNormalization', 'applyPromptField', 'normalizePromptFields');
  return { ctx, requests, changes };
}
test('normalization cannot replace a newer edit or a different workbench draft', async () => {
  for (const change of ['edit', 'profile']) {
    const { ctx, requests } = normalizationContext();
    const pending = ctx.normalizePromptFields({ extra_prompt: 'old' });
    if (change === 'edit') ctx.ui.extra_prompt.value = 'new edit';
    if (change === 'workbench') ctx.singleWorkbenchState.mode = 'single';
    if (change === 'profile') ctx.currentProfile = () => 'krea2';
    requests[0].resolve({ fields: { extra_prompt: 'normalized old' }, changed: true });
    await pending;
    assert.equal(ctx.ui.extra_prompt.value, change === 'edit' ? 'new edit' : 'old', change);
  }
});
test('the latest normalization request wins and unchanged input still normalizes', async () => {
  const { ctx, requests } = normalizationContext();
  const old = ctx.normalizePromptFields({ extra_prompt: 'old' });
  const latest = ctx.normalizePromptFields({ extra_prompt: 'old' });
  requests[1].resolve({ fields: { extra_prompt: 'latest normalization' }, changed: true });
  await latest;
  requests[0].resolve({ fields: { extra_prompt: 'stale normalization' }, changed: true });
  await old;
  assert.equal(ctx.ui.extra_prompt.value, 'latest normalization');
});
test('input revisions reject an old normalization even when text changes back to the same value', async () => {
  const { ctx, requests } = normalizationContext();
  const pending = ctx.normalizePromptFields({ extra_prompt: 'old' });
  ctx.invalidatePromptNormalization();
  requests[0].resolve({ fields: { extra_prompt: 'obsolete result' }, changed: true });
  await pending;
  assert.equal(ctx.ui.extra_prompt.value, 'old');
});





function submitContext() {
  const gates = [], posts = []; let submit;
  const ctx = vm.createContext({
    ui: { settingsForm: { addEventListener: (_name, callback) => { submit = callback; } }, startButton: {} },
    submitInFlight: false, settings: { workbench: 'random', model_profile: 'anima', extra_prompt: 'original', loras: [{ filename: 'old' }] },
    clone: plain, singleWorkbenchActive: () => ctx.settings.workbench === 'single', singleWorkbenchInputBlocked: () => false,
    randomWorkbenchSeeds: () => ({ sample_seed: 777 }),
    currentPromptFields: () => ({}), readSettings: () => ctx.settings, persistNow() {},
    normalizePromptFields: () => { const gate = deferred(); gates.push(gate); return gate.promise; },
    normalizeSettingsPayload: settings => { const gate = deferred(); gates.push({ resolve: () => gate.resolve(settings) }); return gate.promise; },
    generationClassificationIssues: () => [], toast() {}, refreshConnection: async () => {}, renderBatchPayload() {}, updateGenerationAvailability() {},
    request: async (url, options) => { if (url === '/api/batches') { posts.push(JSON.parse(options.body)); return { status: 'running' }; } return {}; },
  });
  load(ctx, 'submitBatch');
  const start = source.indexOf('ui.settingsForm.addEventListener("submit"');
  vm.runInContext(source.slice(start, source.indexOf('ui.settingsForm.addEventListener("input"', start)), ctx);
  return { ctx, gates, posts, submit: () => submit({ preventDefault() {} }) };
}
async function drainSubmit(promise, gates) {
  let done = false; promise.finally(() => { done = true; });
  while (!done) { while (gates.length) gates.shift().resolve({ fields: {} }); await new Promise(setImmediate); }
  await promise;
}
test('submit locks once and posts one immutable snapshot without frontend generation normalization', async () => {
  const { ctx, gates, posts, submit } = submitContext();
  const first = submit();
  assert.equal(ctx.submitInFlight, true, 'the first asynchronous stage must already own the submit lock');
  const second = submit();
  ctx.settings.loras[0].filename = 'edited';
  ctx.settings = { workbench: 'single', model_profile: 'krea2', extra_prompt: 'different workbench' };
  await drainSubmit(first, gates); await drainSubmit(second, gates);
  assert.equal(posts.length, 1);
  assert.equal(gates.length, 0, 'backend performs generation normalization once');
  assert.deepEqual(posts[0], { workbench: 'random', model_profile: 'anima', extra_prompt: 'original', loras: [{ filename: 'old' }], seeds: { sample_seed: 777 } });
  assert.equal(ctx.submitInFlight, false);
});
test('fixed single seeds are captured with settings and a failed submit unlocks retry', async () => {
  const { ctx, gates, posts, submit } = submitContext();
  ctx.settings = { workbench: 'single', model_profile: 'anima' };
  await submit();
  assert.equal(posts[0].seeds.sample_seed, 777);
  ctx.settings = { workbench: 'random', model_profile: 'anima' };
  ctx.request = async () => { throw new Error('offline'); };
  await drainSubmit(submit(), gates);
  assert.equal(ctx.submitInFlight, false);
});
test('submission remains locked until its final status read settles', async () => {
  const { ctx, submit } = submitContext();
  const status = deferred(); let posts = 0;
  ctx.settings = { workbench: 'single', model_profile: 'anima' };
  ctx.request = async url => { if (url === '/api/batches') { posts++; return { status: 'running' }; } return status.promise; };
  const pending = submit();
  await new Promise(setImmediate);
  assert.equal(ctx.submitInFlight, true);
  await submit();
  assert.equal(posts, 1);
  status.reject(new Error('status temporarily unavailable'));
  await pending;
  assert.equal(ctx.submitInFlight, false);
});

function resourceContext() {
  const field = (value = '') => ({ value, disabled: false, replaceChildren(...options) { this.value = options[0]?.value || ''; } });
  const ui = Object.fromEntries(['model_profile', 'base_mode', 'sampler_name', 'scheduler', 'seedvr2_model_name', 'seedvr2_vae_name', 'model_name', 'latent_second_sampler_name', 'latent_second_scheduler', 'seedvr2_sampler_name', 'seedvr2_scheduler', 'face_sampler', 'face_scheduler', 'face_upscaler_model'].map(key => [key, field()]));
  const saved = { sampler_name: 'user_sampler', scheduler: 'user_scheduler', seedvr2_model_name: 'user_model', seedvr2_vae_name: 'user_vae' };
  for (const [key, value] of Object.entries(saved)) ui[key].value = value;
  const gates = [];
  const ctx = vm.createContext({
    ui, resourceRequest: 0, currentProfile: () => 'anima', defaults: {}, clone: value => JSON.parse(JSON.stringify(value)),
    profileConfig: () => ({ seedvr2_model: 'default_model', seedvr2_vae: 'default_vae' }),
    profileDefaults: () => ({ sampler_name: 'default_sampler', scheduler: 'default_scheduler' }),
    refreshCurrentModelSelection() {}, updateGenerationAvailability() {}, Option: function(_label, value) { this.value = value; },
    request: () => { const gate = deferred(); gates.push(gate); return gate.promise; },
    profileOptionsFromResources: () => [{ id: 'anima' }], ensureSelectValue: (element, value) => element.value = value,
    populateResourceSelect: (element, _choices, selected) => element.value = selected,
    resolveInventoryPath: value => value, controlValue: id => ui[id].value,
    applyNodeResourceControls() {}, updateProfileResources() {}, updateRepairControls() {},
  });
  load(ctx, 'loadResources');
  return { ctx, ui, gates, saved };
}
test('overlapping resource refreshes preserve saved selections throughout loading', async () => {
  const { ctx, ui, gates, saved } = resourceContext();
  const first = ctx.loadResources(), second = ctx.loadResources();
  assert.equal(gates.length, 1, 'overlapping inventory reads share a request');
  const duringLoad = Object.fromEntries(Object.keys(saved).map(key => [key, ui[key].value]));
  const payload = { models: ['model'], samplers: ['user_sampler'], schedulers: ['user_scheduler'], seedvr2_models: ['user_model'], seedvr2_vaes: ['user_vae'] };
  gates.forEach(gate => gate.resolve(payload)); await Promise.all([first, second]);
  assert.deepEqual(duringLoad, saved, 'autosave during loading must retain settings');
  assert.deepEqual(Object.fromEntries(Object.keys(saved).map(key => [key, ui[key].value])), saved);
});
test('an in-flight resource refresh preserves settings loaded into another workbench on the same profile', async () => {
  const { ctx, ui, gates } = resourceContext();
  const pending = ctx.loadResources();
  ui.sampler_name.value = 'new_workbench_sampler';
  ui.scheduler.value = 'new_workbench_scheduler';
  gates[0].resolve({ models: ['model'], samplers: ['new_workbench_sampler'], schedulers: ['new_workbench_scheduler'] });
  await pending;
  assert.equal(ui.sampler_name.value, 'new_workbench_sampler');
  assert.equal(ui.scheduler.value, 'new_workbench_scheduler');
});


function previewContext() {
  const images = [], pending = [];
  const ctx = vm.createContext({
    imagePreview: { phase: 'open', context: {}, preloaded: new Map(), pointers: new Set(), token: 0, navigationRequest: 0 },
    currentRecord: { id: 1 }, records: Array.from({ length: 12 }, (_, index) => ({ id: index + 1 })),
    currentPreviewRecord: () => ctx.currentRecord,
    selectedHistoryLocation: () => ({ items: ctx.records, index: ctx.records.findIndex(record => record.id === ctx.currentRecord.id) }),
    Image: function() { this.decode = () => { const gate = deferred(); pending.push(gate); return gate.promise; }; images.push(this); },
    cancelImagePreviewAnimation() {},
    document: { documentElement: { classList: { remove() {} } } },
    ui: { imagePreviewVeil: { getAnimations: () => [] }, imagePreviewImage: { classList: { remove() {} }, removeAttribute() {} }, imagePreviewStatus: {}, imagePreviewAnnouncement: {}, imagePreviewDialog: { classList: { remove() {} }, dataset: {} } },
  });
  load(ctx, 'clearPreviewCache', 'trimPreviewCache', 'preloadRecord', 'preloadPreviewNeighbors', 'setImagePreviewSourceHidden', 'finishImagePreviewClose');
  return { ctx, images, pending };
}
test('decoded preview cache retains only the current image and its two neighbors', async () => {
  const { ctx, pending, images } = previewContext();
  ctx.currentRecord = { id: 6 };
  for (const id of [5, 6, 7]) { const value = ctx.preloadRecord({ id }); pending.shift().resolve(); await value; }
  assert.equal(ctx.imagePreview.preloaded.size, 3);
  ctx.currentRecord = { id: 7 };
  ctx.preloadPreviewNeighbors();
  pending.shift().resolve(); await ctx.imagePreview.preloaded.get('8');
  assert.deepEqual([...ctx.imagePreview.preloaded.keys()].sort(), ['6', '7', '8']);
  assert.equal(images[0].src, '', 'leaving the neighbor window releases its decoded image');
  ctx.finishImagePreviewClose();
  assert.equal(ctx.imagePreview.preloaded.size, 0);
  assert.ok(images.every(image => image.src === ''));
});
test('cache eviction protects a navigation target until its decode becomes current', async () => {
  const { ctx, pending, images } = previewContext();
  ctx.currentRecord = { id: 2 };
  for (const id of [1, 2, 3]) { const value = ctx.preloadRecord({ id }); pending.shift().resolve(); await value; }
  const target = ctx.preloadRecord({ id: 10 }), decode = pending.shift();
  assert.equal(ctx.imagePreview.preloaded.size, 3);
  assert.ok(ctx.imagePreview.preloaded.has('2'));
  assert.ok(ctx.imagePreview.preloaded.has('10'));
  assert.equal(images.at(-1).src, '/api/images/10');
  ctx.imagePreview.phase = 'switching';
  ctx.preloadPreviewNeighbors();
  assert.ok(ctx.imagePreview.preloaded.has('10'), 'background prefetch must not evict the uncommitted navigation target');
  decode.resolve(); assert.ok(await target);
  ctx.currentRecord = { id: 10 };
  ctx.imagePreview.phase = 'open';
  ctx.preloadPreviewNeighbors();
  for (const item of pending.splice(0)) item.resolve();
  await Promise.all(ctx.imagePreview.preloaded.values());
  assert.deepEqual([...ctx.imagePreview.preloaded.keys()].sort(), ['10', '11', '9']);
  assert.equal(images[1].src, '', 'the former current image is released after navigation commits');
});
test('closing a viewer invalidates a pending decode without deleting a newer same-id cache entry', async () => {
  const { ctx, pending } = previewContext();
  const old = ctx.preloadRecord({ id: 5 }), oldDecode = pending.shift();
  ctx.finishImagePreviewClose();
  ctx.imagePreview.context = {}; ctx.imagePreview.phase = 'open';
  const latest = ctx.preloadRecord({ id: 5 }), latestDecode = pending.shift();
  assert.ok(latestDecode, 'a new viewer must get its own decode');
  oldDecode.reject(new Error('old decode cancelled')); assert.equal(await old, null);
  assert.equal(ctx.imagePreview.preloaded.get('5'), latest);
  latestDecode.resolve(); assert.ok(await latest);
});
test('a successfully decoded image from a closed viewer cannot commit into the new context', async () => {
  const { ctx, pending } = previewContext();
  const stale = ctx.preloadRecord({ id: 7 }), decode = pending.shift();
  ctx.finishImagePreviewClose(); ctx.imagePreview.context = {};
  decode.resolve();
  assert.equal(await stale, null);
  assert.equal(ctx.imagePreview.preloaded.size, 0);
});
