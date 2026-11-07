const createFrontendContext = require('./helpers/frontend-context.cjs');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function functionSource(name) {
  let start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  if (source.slice(start - 6, start) === 'async ') start -= 6;
  return source.slice(start, source.indexOf('\n}', start) + 2);
}
function context() {
  const pending = [], reconciled = [], rendered = [];
  const element = () => ({ value: '', disabled: false, checked: false, hidden: false,
    replaceChildren() {}, querySelector() { return this; }, close() { this.closed = true; } });
  const ui = new Proxy({}, { get(target, key) { return target[key] ||= element(); } });
  const ctx = createFrontendContext({
    console, Map, Promise, JSON, ui, document: { hidden: false },
    Option: function(label, value) { return { label, value }; },
    activeProfileId: 'anima', resourceRequest: 0, resourceInventoryProfile: '', resourceError: '', resourcesLoaded: false,
    resources: {}, profileResources: {}, loraInventoryRequest: 0, loraInventoryProfile: '',
    loraInventory: [], loraClassificationItems: [], loraInventoryLoaded: false, loraInventoryError: '',
    defaults: {}, draft: { loras: [] }, editingLoraTrigger: null,
    clone: value => JSON.parse(JSON.stringify(value)), normalizedPath: value => String(value).replaceAll('\\', '/').toLowerCase(),
    request: (url, options) => new Promise((resolve, reject) => pending.push({ url, options, resolve, reject })),
    currentProfile: () => ctx.activeProfileId,
    profileConfig: () => ({}), profileDefaults: () => ({}),
    profileOptionsFromResources: () => [{ id: 'anima' }, { id: 'krea2' }],
    ensureSelectValue: (control, value) => { control.value = value; },
    populateResourceSelect: (control, choices, value) => { control.value = value; },
    resolveInventoryPath: value => value,
    controlValue: id => ui[id].value,
    refreshCurrentModelSelection() {}, updateGenerationAvailability() {}, applyNodeResourceControls() {},
    updateProfileResources() {}, updateRepairControls() {}, singleWorkbenchResources() {},
    renderLoras: () => rendered.push(ctx.loraInventory.map(item => item.filename)), renderLoraCatalog() {},
    reconcileLoraTriggers: loras => reconciled.push({ profile: ctx.activeProfileId, loras }),
    parseLoraTriggerInput: value => value.split(','), schedulePersist() {}, toast() {},
  });
  for (const name of ['loadResources', 'loadLoraInventory', 'invalidateResourceInventories',
    'replaceLoraInventoryItem', 'queueLoraTriggerWrite', 'saveLoraTriggers', 'resetLoraTriggers', 'refreshConnection', 'refreshResourceClassification']) {
    vm.runInContext(functionSource(name), ctx);
  }
  return { ctx, pending, reconciled, rendered };
}
function resourcePayload(profile, sampler = 'er_sde') {
  return { models: [`${profile}.safetensors`], model_profiles: { anima: { id: 'anima' }, krea2: { id: 'krea2' } },
    samplers: [sampler], schedulers: ['simple'], seedvr2_models: ['seed'], seedvr2_vaes: ['vae'] };
}
function lora(profile, trigger = 'old') {
  return { filename: `${profile}.safetensors`, model_profile: profile, classification_status: 'recognized', trigger_words: [trigger] };
}
function loraPayload(profile, trigger) { return { items: [lora(profile, trigger)], classification_items: [lora(profile, trigger)] }; }

for (const [load, payload, value] of [
  ['loadResources', resourcePayload, ctx => ctx.resources.models[0]],
  ['loadLoraInventory', loraPayload, ctx => ctx.loraInventory[0]?.filename],
]) {
  test(`${load} shares concurrent requests and immediately reuses each visited profile`, async () => {
    const { ctx, pending } = context();
    const first = ctx[load](), duplicate = ctx[load]();
    assert.equal(pending.length, 1);
    ctx.activeProfileId = 'krea2';
    const second = ctx[load]();
    assert.equal(pending.length, 2);
    pending[1].resolve(payload('krea2'));
    await second;
    pending[0].resolve(payload('anima'));
    await Promise.all([first, duplicate]);
    assert.equal(value(ctx), 'krea2.safetensors');
    ctx.activeProfileId = 'anima';
    const back = ctx[load]();
    assert.equal(value(ctx), 'anima.safetensors', 'cached values apply without a loading tick');
    await back;
    ctx.activeProfileId = 'krea2';
    await ctx[load]();
    assert.equal(pending.length, 2);
    assert.equal(value(ctx), 'krea2.safetensors');
  });

  test(`${load} returns to an in-flight profile without duplicate reads`, async () => {
    const { ctx, pending } = context();
    const first = ctx[load]();
    ctx.activeProfileId = 'krea2';
    const second = ctx[load]();
    ctx.activeProfileId = 'anima';
    const back = ctx[load]();
    assert.equal(pending.length, 2);
    pending[0].resolve(payload('anima'));
    await Promise.all([first, back]);
    pending[1].resolve(payload('krea2'));
    await second;
    assert.equal(value(ctx), 'anima.safetensors');
  });

  test(`${load} refresh rejects old completions and clears every profile cache`, async () => {
    const { ctx, pending } = context();
    const stale = ctx[load]();
    ctx.activeProfileId = 'krea2';
    const second = ctx[load]();
    pending[1].resolve(payload('krea2'));
    await second;
    ctx.activeProfileId = 'anima';
    const fresh = ctx[load]({ refresh: true });
    assert.match(pending[2].url, /refresh=1/);
    pending[2].resolve(payload('fresh'));
    await fresh;
    pending[0].resolve(payload('stale'));
    await stale;
    assert.equal(value(ctx), 'fresh.safetensors');
    await ctx[load]();
    assert.equal(value(ctx), 'fresh.safetensors');
    ctx.activeProfileId = 'krea2';
    const again = ctx[load]();
    assert.equal(pending.length, 4);
    pending[3].resolve(payload('krea2'));
    await again;
  });

  test(`${load} failed refresh cannot restore stale data and retries normally`, async () => {
    const { ctx, pending } = context();
    const initial = ctx[load]();
    pending[0].resolve(payload('anima'));
    await initial;
    const failed = ctx[load]({ refresh: true });
    pending[1].reject(new Error('offline'));
    await failed;
    const retry = ctx[load]();
    assert.equal(pending.length, 3);
    pending[2].resolve(payload('fresh'));
    await retry;
    assert.equal(value(ctx), 'fresh.safetensors');
  });

  test(`${load} shares simultaneous forced refreshes`, async () => {
    const { ctx, pending } = context();
    const first = ctx[load]({ refresh: true }), duplicate = ctx[load]({ refresh: true });
    assert.equal(pending.length, 1);
    pending[0].resolve(payload('fresh'));
    await Promise.all([first, duplicate]);
    assert.equal(value(ctx), 'fresh.safetensors');
  });
}

test('resource completion and cache restoration retain the current form values', async () => {
  const { ctx, pending } = context();
  ctx.ui.sampler_name.value = 'before';
  ctx.ui.seedvr2_model_name.value = 'seed-before';
  const loading = ctx.loadResources();
  ctx.ui.sampler_name.value = 'current';
  ctx.ui.seedvr2_model_name.value = 'seed-current';
  pending[0].resolve(resourcePayload('anima'));
  await loading;
  assert.equal(ctx.ui.sampler_name.value, 'current');
  assert.equal(ctx.ui.seedvr2_model_name.value, 'seed-current');
  ctx.resources.models.push('must-not-enter-cache');
  ctx.ui.sampler_name.value = 'new-draft';
  await ctx.loadResources();
  assert.equal(ctx.ui.sampler_name.value, 'new-draft');
  assert.deepEqual(Array.from(ctx.resources.models), ['anima.safetensors']);
});

test('trigger edits update both profile caches and invalidate older inventories', async () => {
  const { ctx, pending } = context();
  let loading = ctx.loadLoraInventory();
  pending[0].resolve(loraPayload('anima'));
  await loading;
  ctx.activeProfileId = 'krea2';
  loading = ctx.loadLoraInventory();
  pending[1].resolve(loraPayload('krea2'));
  await loading;
  ctx.replaceLoraInventoryItem({ ...lora('anima', 'saved'), trigger_override: true });
  ctx.activeProfileId = 'anima';
  await ctx.loadLoraInventory();
  assert.equal(pending.length, 2);
  assert.equal(ctx.loraInventory[0].trigger_words[0], 'saved');
  ctx.replaceLoraInventoryItem(lora('anima', 'source'));
  await ctx.loadLoraInventory();
  assert.equal(ctx.loraInventory[0].trigger_words[0], 'source');
  const cache = ctx.loadLoraInventory.cache;
  assert.equal(cache.values.get('krea2').classification_items.find(item => item.filename === 'anima.safetensors').trigger_words[0], 'source');
});

test('a trigger response after switching profiles does not reconcile or close the new editor', async () => {
  const { ctx, pending, reconciled } = context();
  ctx.editingLoraTrigger = { filename: 'anima.safetensors', enabled: true };
  ctx.ui.loraTriggerFilename.value = 'anima.safetensors';
  ctx.ui.loraTriggerWords.value = 'saved';
  const save = ctx.saveLoraTriggers({ preventDefault() {} });
  assert.equal(JSON.parse(pending[0].options.body).model_profile, 'anima');
  ctx.activeProfileId = 'krea2';
  ctx.editingLoraTrigger = { filename: 'krea2.safetensors', enabled: true };
  pending[0].resolve(lora('anima', 'saved'));
  await save;
  assert.equal(reconciled.length, 0);
  assert.equal(ctx.ui.loraTriggerDialog.closed, undefined);
});

test('out-of-order edits of different LoRAs both update cache without closing another editor', async () => {
  const { ctx, pending, reconciled } = context();
  const a = { ...lora('anima', 'old-a'), filename: 'styles/a.safetensors' };
  const b = { ...lora('anima', 'old-b'), filename: 'styles/b.safetensors' };
  const load = ctx.loadLoraInventory();
  pending[0].resolve({ items: [a, b], classification_items: [a, b] });
  await load;
  reconciled.length = 0;
  let closes = 0;
  ctx.ui.loraTriggerDialog.close = () => { closes++; };
  ctx.editingLoraTrigger = { filename: a.filename, enabled: true };
  ctx.ui.loraTriggerFilename.value = a.filename;
  ctx.ui.loraTriggerWords.value = 'new-a';
  const saveA = ctx.saveLoraTriggers({ preventDefault() {} });
  const editorB = ctx.editingLoraTrigger = { filename: b.filename, enabled: true };
  ctx.ui.loraTriggerFilename.value = b.filename;
  ctx.ui.loraTriggerWords.value = 'new-b';
  const saveB = ctx.saveLoraTriggers({ preventDefault() {} });
  pending[2].resolve({ ...b, trigger_words: ['new-b'], trigger_override: true });
  await saveB;
  pending[1].resolve({ ...a, trigger_words: ['new-a'], trigger_override: true });
  await saveA;
  assert.equal(closes, 1);
  assert.equal(reconciled.length, 1);
  assert.equal(reconciled[0].loras[0], editorB);
  await ctx.loadLoraInventory();
  assert.equal(pending.length, 3, 'both edits survive a warm-cache restore');
  assert.deepEqual(Object.fromEntries(ctx.loraInventory.map(item => [item.filename, item.trigger_words[0]])), {
    [a.filename]: 'new-a', [b.filename]: 'new-b',
  });
});

for (const [earlier, latest, expected] of [['save', 'reset', 'source'], ['reset', 'save', 'new']]) {
  test(`same-LoRA ${earlier} then ${latest} writes serially with captured values`, async () => {
    const { ctx, pending, reconciled } = context();
    const original = { ...lora('anima', 'old'), filename: 'styles/ink.safetensors' };
    const load = ctx.loadLoraInventory();
    pending[0].resolve({ items: [original], classification_items: [original] });
    await load;
    reconciled.length = 0;
    let closes = 0;
    ctx.ui.loraTriggerDialog.close = () => { closes++; };
    ctx.editingLoraTrigger = { filename: original.filename, enabled: true };
    ctx.ui.loraTriggerFilename.value = 'STYLES\\ink.safetensors';
    ctx.ui.loraTriggerWords.value = 'new';
    const submit = kind => kind === 'save' ? ctx.saveLoraTriggers({ preventDefault() {} }) : ctx.resetLoraTriggers();
    const first = submit(earlier);
    ctx.ui.loraTriggerFilename.value = original.filename;
    const second = submit(latest);
    assert.equal(pending.length, 2, 'the later operation waits for the earlier write');
    assert.equal(pending[1].options.method, earlier === 'save' ? 'PUT' : 'DELETE');
    ctx.ui.loraTriggerWords.value = 'edited-after-enqueue';
    ctx.ui.loraTriggerFilename.value = 'other.safetensors';
    pending[1].resolve({ ...original, trigger_words: [earlier === 'save' ? 'new' : 'source'], trigger_override: earlier === 'save' });
    await first;
    await new Promise(setImmediate);
    assert.equal(pending.length, 3);
    assert.equal(ctx.loraInventory[0].trigger_words[0], earlier === 'save' ? 'new' : 'source');
    assert.equal(closes, 0, 'an earlier success cannot close the latest editor operation');
    assert.equal(reconciled.length, 0);
    assert.equal(pending[2].options.method, latest === 'save' ? 'PUT' : 'DELETE');
    if (latest === 'save') {
      assert.equal(JSON.parse(pending[2].options.body).filename, original.filename);
      assert.deepEqual(JSON.parse(pending[2].options.body).triggerWords, ['new']);
    } else assert.match(pending[2].url, /filename=styles%2Fink.safetensors/);
    pending[2].resolve({ ...original, trigger_words: [expected], trigger_override: latest === 'save' });
    await second;
    assert.equal(closes, 1);
    assert.equal(reconciled.length, 1);
    await ctx.loadLoraInventory();
    assert.equal(pending.length, 3);
    assert.equal(ctx.loraInventory[0].trigger_words[0], expected);
    assert.equal(ctx.loraInventory[0].trigger_override, latest === 'save');
  });
}

test('a failed queued reset preserves the preceding successful save in cache', async () => {
  const { ctx, pending, reconciled } = context();
  const loading = ctx.loadLoraInventory();
  pending[0].resolve(loraPayload('anima', 'old'));
  await loading;
  reconciled.length = 0;
  ctx.editingLoraTrigger = { filename: 'anima.safetensors', enabled: true };
  ctx.ui.loraTriggerFilename.value = 'anima.safetensors';
  ctx.ui.loraTriggerWords.value = 'saved';
  const save = ctx.saveLoraTriggers({ preventDefault() {} });
  const reset = ctx.resetLoraTriggers();
  assert.equal(pending.length, 2);
  pending[1].resolve({ ...lora('anima', 'saved'), trigger_override: true });
  await save;
  await new Promise(setImmediate);
  assert.equal(pending.length, 3);
  pending[2].reject(new Error('reset failed'));
  await reset;
  assert.equal(ctx.ui.loraTriggerDialog.closed, undefined);
  assert.equal(reconciled.length, 1, 'the latest failed operation synchronizes the last committed trigger values');
  await ctx.loadLoraInventory();
  assert.equal(pending.length, 3, 'failure does not race a refresh against writes');
  assert.equal(ctx.loraInventory[0].trigger_words[0], 'saved');
  assert.equal(ctx.loraInventory[0].trigger_override, true);
});

test('a failed earlier write does not block the queued reset or mutate its captured identity', async () => {
  const { ctx, pending, reconciled } = context();
  const loading = ctx.loadLoraInventory();
  pending[0].resolve(loraPayload('anima', 'old'));
  await loading;
  reconciled.length = 0;
  ctx.editingLoraTrigger = { filename: 'anima.safetensors', enabled: true };
  ctx.ui.loraTriggerFilename.value = 'anima.safetensors';
  ctx.ui.loraTriggerWords.value = 'saved';
  const save = ctx.saveLoraTriggers({ preventDefault() {} });
  const reset = ctx.resetLoraTriggers();
  assert.equal(pending.length, 2);
  pending[1].reject(new Error('save failed'));
  await save;
  await new Promise(setImmediate);
  assert.equal(pending.length, 3);
  assert.equal(pending[2].options.method, 'DELETE');
  assert.equal(reconciled.length, 0, 'an older failure cannot change the latest operation UI');
  assert.equal(ctx.loraInventory[0].trigger_words[0], 'old');
  pending[2].resolve({ ...lora('anima', 'source'), trigger_override: false });
  await reset;
  assert.equal(ctx.ui.loraTriggerDialog.closed, true);
  assert.equal(reconciled.length, 1);
  await ctx.loadLoraInventory();
  assert.equal(pending.length, 3);
  assert.equal(ctx.loraInventory[0].trigger_words[0], 'source');
  assert.equal(ctx.loraInventory[0].trigger_override, false);
});

test('trigger edits during inventory refresh restart the read and reject the pre-edit response', async () => {
  const { ctx, pending } = context();
  const stale = ctx.loadLoraInventory();
  ctx.replaceLoraInventoryItem(lora('anima', 'saved'));
  assert.equal(pending.length, 2, 'invalidated active read must be replaced');
  pending[0].resolve(loraPayload('anima', 'old'));
  await stale;
  assert.equal(ctx.loraInventory[0].trigger_words[0], 'saved');
  pending[1].resolve(loraPayload('anima', 'saved'));
  await new Promise(setImmediate);
  assert.equal(ctx.loraInventoryLoaded, true);
  await ctx.loadLoraInventory();
  assert.equal(pending.length, 2);
  assert.equal(ctx.loraInventory[0].trigger_words[0], 'saved');
});

test('manual classification refresh invalidates both cached profiles and keeps explicit refresh requests', async () => {
  const { ctx, pending } = context();
  let loading = Promise.all([ctx.loadResources(), ctx.loadLoraInventory()]);
  pending[0].resolve(resourcePayload('anima'));
  pending[1].resolve(loraPayload('anima'));
  await loading;
  ctx.activeProfileId = 'krea2';
  loading = Promise.all([ctx.loadResources(), ctx.loadLoraInventory()]);
  pending[2].resolve(resourcePayload('krea2'));
  pending[3].resolve(loraPayload('krea2'));
  await loading;
  let ownershipRefresh;
  ctx.loadResourceOwnership = options => { ownershipRefresh = options.refresh; };
  ctx.selectedRecord = null;
  const refresh = ctx.refreshResourceClassification();
  assert.equal(ownershipRefresh, true);
  assert.match(pending[4].url, /resources.*refresh=1/);
  assert.match(pending[5].url, /loras.*refresh=1/);
  pending[4].resolve(resourcePayload('krea2'));
  pending[5].resolve(loraPayload('krea2'));
  await refresh;
  ctx.activeProfileId = 'anima';
  loading = Promise.all([ctx.loadResources(), ctx.loadLoraInventory()]);
  assert.equal(pending.length, 8);
  pending[6].resolve(resourcePayload('anima'));
  pending[7].resolve(loraPayload('anima'));
  await loading;
});

test('offline to online transition refreshes inventories once; normal polling reuses them', async () => {
  const { ctx, pending } = context();
  let poll = ctx.refreshConnection();
  const duplicate = ctx.refreshConnection();
  assert.equal(pending.length, 1);
  pending[0].resolve({ online: true });
  await Promise.all([poll, duplicate]);
  poll = ctx.refreshConnection();
  pending[1].resolve({ online: false });
  await poll;
  poll = ctx.refreshConnection();
  pending[2].resolve({ online: true });
  await new Promise(setImmediate);
  assert.equal(pending.length, 5);
  assert.match(pending[3].url, /resources.*refresh=1/);
  assert.match(pending[4].url, /loras.*refresh=1/);
  pending[3].resolve(resourcePayload('anima'));
  pending[4].resolve(loraPayload('anima'));
  await poll;
  poll = ctx.refreshConnection();
  pending[5].resolve({ online: true });
  await poll;
  await Promise.all([ctx.loadResources(), ctx.loadLoraInventory()]);
  assert.equal(pending.length, 6);
});
