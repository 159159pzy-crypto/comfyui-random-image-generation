const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function load(context, names) {
  const ctx = vm.createContext(context);
  for (const name of names) {
    const start = source.indexOf(`function ${name}(`);
    assert.ok(start >= 0, name);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), ctx);
  }
  return ctx;
}
const item = (filename, options = {}) => ({filename, model_profile: 'anima', classification_status: 'recognized', ...options});

test('library additions preserve manual disables and weights, and reject incompatible items', () => {
  const existing = {filename: 'Characters/A.safetensors', enabled: false, strength: .25};
  let addedTriggers;
  const ctx = load({draft: {loras: [existing]}, currentProfile: () => 'anima', loraItem: () => null, replaceLoraInventoryItem() {},
    normalizedPath: value => value.toLowerCase().replaceAll('\\', '/'), renderLoras() {}, schedulePersist() {},
    reconcileLoraTriggers: items => addedTriggers = items,
  }, ['addManagedLoras']);
  const result = ctx.addManagedLoras([item('characters/a.safetensors'), item('B.safetensors', {default_strength: -.6}),
    item('other.safetensors', {model_profile: 'krea2'}), item('unknown.safetensors', {classification_status: 'unknown'})]);
  assert.equal(ctx.draft.loras[0], existing);
  assert.equal(existing.enabled, false);
  assert.equal(existing.strength, .25);
  assert.equal(result.added.length, 1);
  assert.equal(result.skipped.length, 3);
  assert.equal(result.added[0].strength, -.6);
  assert.equal(addedTriggers.length, 1);
});

test('bulk additions report capacity without silently dropping existing configuration', () => {
  const loras = Array.from({length: 63}, (_, i) => ({filename: `${i}.safetensors`, enabled: true, strength: 1}));
  const ctx = load({draft: {loras}, currentProfile: () => 'anima', loraItem: () => null, replaceLoraInventoryItem() {},
    normalizedPath: value => value.toLowerCase(), renderLoras() {}, schedulePersist() {}, reconcileLoraTriggers() {},
  }, ['addManagedLoras']);
  const result = ctx.addManagedLoras([item('next.safetensors'), item('overflow.safetensors')]);
  assert.equal(loras.length, 64);
  assert.equal(result.added.length, 1);
  assert.match(result.skipped[0].reason, /64/);
});

test('fresh library metadata replaces stale classification before trigger reconciliation', () => {
  const fresh = item('new.safetensors', {default_strength: .65, trigger_words: ['fresh trigger']});
  let cached = item('new.safetensors', {model_profile: 'krea2', trigger_words: []});
  let reconciled;
  const ctx = load({draft: {loras: []}, currentProfile: () => 'anima', loraItem: () => cached,
    replaceLoraInventoryItem: value => {cached = value;}, normalizedPath: value => value.toLowerCase(),
    renderLoras() {}, schedulePersist() {}, reconcileLoraTriggers: () => {reconciled = cached.trigger_words;},
  }, ['addManagedLoras']);
  const result = ctx.addManagedLoras([fresh]);
  assert.equal(result.added[0].strength, .65);
  assert.deepEqual(reconciled, ['fresh trigger']);
  assert.equal(cached, fresh);
});

test('successful automatic LoRAs stay in image details without a generation notice', () => {
  const ctx = load({escapeHtml: text => String(text)}, ['loraRuntimeSummary', 'loraRuntimeMarkup']);
  for (const count of [1, 3]) {
    const runtime = {version: 1, applied: Array.from({length: count}, (_, i) =>
      ({character_name: `Character ${i}`, filename: `bound-${i}.safetensors`, strength: .8})), skipped: []};
    assert.equal(ctx.loraRuntimeSummary(runtime), '');
    const markup = ctx.loraRuntimeMarkup(runtime);
    assert.match(markup, /人物自动启用/);
    for (const applied of runtime.applied) assert.ok(markup.includes(applied.filename));
    assert.match(ctx.loraRuntimeSummary(runtime, 2), /累计跳过 2/);
  }
});

test('runtime feedback keeps skipped reasons visible and escapes model metadata', () => {
  const ctx = load({escapeHtml: text => String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')},
    ['loraRuntimeSummary', 'loraRuntimeMarkup']);
  const runtime = {version: 1, applied: [{character_name: 'A', filename: 'a.safetensors', strength: .8}],
    skipped: [{character_name: '<script>', filename: 'missing.safetensors', message: '文件不存在'}]};
  assert.match(ctx.loraRuntimeSummary(runtime, 3), /累计跳过 3/);
  assert.match(ctx.loraRuntimeSummary(runtime), /文件不存在/);
  assert.doesNotMatch(ctx.loraRuntimeSummary(runtime, 3), /自动启用/);
  const markup = ctx.loraRuntimeMarkup(runtime);
  assert.ok(markup.includes('&lt;script&gt;'));
  assert.ok(!markup.includes('<script>'));
  assert.equal(ctx.loraRuntimeMarkup({}), '');
});

test('library removals use normalized filenames and preserve untouched manual rows', () => {
  const kept = {filename: 'manual.safetensors', enabled: false, strength: .25};
  let reconciled = 0, saved = 0;
  const ctx = load({ draft: {loras: [kept, {filename: 'People\\Alice.safetensors', enabled: true, strength: .8}]},
    normalizedPath: value => value.toLowerCase().replaceAll('\\', '/'), renderLoras() {}, renderLoraCatalog() {},
    schedulePersist: () => saved++, reconcileLoraTriggers: () => reconciled++,
  }, ['removeManagedLoras']);
  const result = ctx.removeManagedLoras([{filename: 'people/alice.safetensors'}, 'missing.safetensors']);
  assert.equal(ctx.draft.loras.length, 1);
  assert.equal(ctx.draft.loras[0], kept);
  assert.equal(kept.enabled, false); assert.equal(kept.strength, .25);
  assert.equal(result.removed.length, 1); assert.equal(result.skipped.length, 1);
  assert.equal(reconciled, 1); assert.equal(saved, 1);
});

function changeContext({ expanded = true, profile = 'anima' } = {}) {
  const changes = { replaced: [], reconciled: [], rows: 0, catalog: 0, persisted: 0 };
  const enabled = { filename: 'a.safetensors', enabled: true, strength: .4 };
  const disabled = { filename: 'disabled.safetensors', enabled: false, strength: .2 };
  const ctx = load({
    ui: { loraDialog: { open: true, classList: { contains: () => expanded } } },
    currentProfile: () => profile, draft: { loras: [enabled, disabled] },
    replaceLoraInventoryItem: value => changes.replaced.push(value),
    reconcileLoraTriggers: value => changes.reconciled.push(value),
    renderLoras: () => changes.rows++, renderLoraCatalog: () => changes.catalog++,
    schedulePersist: () => changes.persisted++,
    loadLoraInventory: () => assert.fail('single metadata edits must not reload inventory'),
  }, ['managedLoraItemChanged']);
  return { ctx, changes, enabled, disabled };
}

test('library-only metadata never invalidates or redraws generation inventories', () => {
  const { ctx, changes } = changeContext();
  for (const field of ['favorite', 'groupIds', 'tags', 'notes']) {
    ctx.managedLoraItemChanged({ item: item('a.safetensors'), changedFields: [field] });
  }
  assert.deepEqual(changes, { replaced: [], reconciled: [], rows: 0, catalog: 0, persisted: 0 });
});

test('default weight edits patch the cache without changing configured weights or hidden previews', () => {
  const { ctx, changes, enabled, disabled } = changeContext();
  const saved = item('a.safetensors', { default_strength: .8 });
  ctx.managedLoraItemChanged({ item: saved, changedFields: ['default_strength'] });
  assert.deepEqual(changes.replaced, [saved]);
  assert.equal(enabled.strength, .4); assert.equal(disabled.strength, .2);
  assert.equal(changes.rows + changes.catalog + changes.persisted, 0);
});

test('trigger edits reconcile enabled rows and redraw the compact picker only while visible', () => {
  const { ctx, changes, enabled } = changeContext({ expanded: false });
  const saved = item('a.safetensors', { trigger_words: ['saved'] });
  ctx.managedLoraItemChanged({ item: saved, changedFields: ['trigger_words', 'trigger_override'] });
  assert.deepEqual(changes.replaced, [saved]);
  assert.equal(changes.reconciled[0].length, 1); assert.equal(changes.reconciled[0][0], enabled);
  assert.equal(changes.rows, 1); assert.equal(changes.catalog, 1); assert.equal(changes.persisted, 1);
});

test('late changes for another profile update cache without changing the active workbench', () => {
  const { ctx, changes } = changeContext({ profile: 'krea2', expanded: false });
  ctx.managedLoraItemChanged({ item: item('a.safetensors'), changedFields: ['trigger_words'] });
  assert.equal(changes.replaced.length, 1);
  assert.equal(changes.reconciled.length + changes.rows + changes.catalog + changes.persisted, 0);
});
