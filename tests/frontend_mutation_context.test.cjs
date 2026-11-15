const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const shared = require('../static/request-state.js');
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
function install(ctx, name) {
  let start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `Missing function ${name}`);
  if (source.slice(start - 6, start) === 'async ') start -= 6;
  vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), ctx);
}
function element() { return { value: '', disabled: false, textContent: '', hidden: false, open: true, files: [], dataset: {}, focus() {}, setAttribute() {}, replaceChildren() {} }; }
function harness() {
  let profile = 'anima';
  const calls = [], messages = [], changes = [], renders = [];
  const ui = Object.fromEntries(['importFile', 'importBundleName', 'importHint', 'commitImport', 'importDialog', 'importSummary', 'importRows', 'savePreset', 'presetName', 'presetError'].map(id => [id, element()]));
  ui.savePreset.textContent = '保存当前';
  const ctx = vm.createContext({ ui, activeSection: 'pose', importPreview: null, stylePresets: [], stylePresetProfile: 'anima', stylePresetRequest: 0,
    importReadGate: shared?.createReadGate(), importViewEpoch: shared?.createViewEpoch(),
    stylePresetReadGate: shared?.createReadGate(), stylePresetViewEpoch: shared?.createViewEpoch(), stylePresetCreates: new Map(), stylePresetWrites: new Set(),
    currentProfile: () => profile, clone: plain, normalizeProfileId: x => x === 'krea2' ? 'krea2' : 'anima', profileLabel: x => x === 'krea2' ? 'Krea 2' : 'Anima',
    presetSnapshot: () => ({ model_profile: profile }), selectedImportGroupIds: () => ['group'],
    toast: text => messages.push(text), confirm: () => true, poolSourceChanged: change => changes.push(plain(change)),
    motionDialog: (dialog, open) => { dialog.open = open; },
    renderImportPreview: () => { ui.commitImport.disabled = Boolean(ctx.importPreview?.saving); },
    renderStylePresets: () => renders.push(plain(ctx.stylePresets)),
    request: (url, options = {}) => { const d = deferred(); calls.push({ url, options, body: options.body && JSON.parse(options.body), ...d }); return d.promise; },
  });
  for (const name of ['invalidateCustomImport', 'customImportContext', 'customImportCurrent', 'previewImportFile', 'commitCustomImport', 'stylePresetItemProfile', 'syncStylePresetControls', 'stylePresetContextChanged', 'stylePresetWriteCompleted', 'loadStylePresets', 'setPresetFeedback', 'createStylePreset', 'updateStylePreset', 'deleteStylePreset']) install(ctx, name);
  return { ctx, ui, calls, messages, changes, renders, profile(next) { profile = next; ctx.stylePresetContextChanged?.(); ctx.invalidateCustomImport?.(); } };
}
const file = (name, text = async () => name) => ({ name: `${name}.json`, text });
const preview = name => ({ rows: [{ row: 1, status: 'new', action: 'create', item: { section: 'pose', title: name } }], summary: { new: 1 } });
async function flush() { for (let i = 0; i < 6; i++) await Promise.resolve(); }
async function readyPreview(h, name = 'A') { h.ui.importFile.files = [file(name)]; const task = h.ctx.previewImportFile(); await flush(); h.calls.at(-1).resolve(preview(name)); await task; }

test('selecting another file immediately invalidates the old preview before file.text completes', async () => {
  const h = harness(); await readyPreview(h);
  const reading = deferred(); h.ui.importFile.files = [file('B', () => reading.promise)]; const task = h.ctx.previewImportFile();
  assert.equal(h.ui.commitImport.disabled, true); assert.equal(h.ctx.importPreview, null);
  await h.ctx.commitCustomImport(); assert.equal(h.calls.length, 1);
  reading.resolve('B'); await flush(); h.calls.at(-1).resolve(preview('B')); await task;
});
test('older preview success or failure cannot replace the newest file or its button state', async () => {
  for (const fail of [false, true]) {
    const h = harness(); h.ui.importFile.files = [file('A')]; const a = h.ctx.previewImportFile(); await flush();
    h.ui.importFile.files = [file('B')]; const b = h.ctx.previewImportFile(); await flush();
    h.calls[1].resolve(preview('B')); await b;
    fail ? h.calls[0].reject(new Error('old error')) : h.calls[0].resolve(preview('A')); await a;
    assert.equal(h.ctx.importPreview.filename, 'B.json'); assert.equal(h.ui.commitImport.disabled, false);
    assert.ok(!h.messages.includes('old error'));
  }
});
test('unsupported files and failed reads cannot retain a submittable preview', async () => {
  for (const next of [{ name: 'bad.txt' }, file('B', async () => { throw new Error('cannot read'); })]) {
    const h = harness(); await readyPreview(h); h.ui.importFile.files = [next]; await h.ctx.previewImportFile();
    assert.equal(h.ctx.importPreview, null); assert.equal(h.ui.commitImport.disabled, true);
  }
});
test('a preview from before leaving and returning to a profile stays invalid', async () => {
  const h = harness(); h.ui.importFile.files = [file('A')]; const task = h.ctx.previewImportFile(); await flush();
  h.profile('krea2'); h.profile('anima'); h.calls[0].resolve(preview('A')); await task;
  assert.equal(h.ctx.importPreview, null); assert.equal(h.ui.commitImport.disabled, true);
});
test('an invalidated file read cannot start a preview HTTP request', async () => {
  const h = harness(), reading = deferred();
  h.ui.importFile.files = [file('A', () => reading.promise)]; const task = h.ctx.previewImportFile();
  h.ctx.invalidateCustomImport(); reading.resolve('A'); await task;
  assert.equal(h.calls.length, 0); assert.equal(h.ui.commitImport.disabled, true);
});
test('import commits frozen rows with edited destination settings once and does not cancel writes', async () => {
  const h = harness(); await readyPreview(h); h.ui.importBundleName.value = 'Edited bundle';
  const task = h.ctx.commitCustomImport(); void h.ctx.commitCustomImport(); await flush();
  assert.equal(h.calls.length, 2); assert.equal(h.calls[1].options.signal, undefined);
  assert.equal(h.calls[1].body.bundleName, 'Edited bundle'); assert.deepEqual(h.calls[1].body.targetGroupIds, ['group']);
  h.ctx.importPreview.rows[0].item.title = 'later edit'; assert.equal(h.calls[1].body.rows[0].item.title, 'A');
  h.calls[1].resolve({ bundleName: 'Edited bundle', imported: 1, updated: 0, skipped: 0 }); await task;
  assert.equal(h.changes.length, 1);
});
test('old import completion or failure leaves a newly opened preview and its pending write untouched', async () => {
  for (const fail of [false, true]) {
    const h = harness(); await readyPreview(h); const first = h.ctx.commitCustomImport();
    h.ctx.invalidateCustomImport?.(); await readyPreview(h, 'B'); const second = h.ctx.commitCustomImport();
    fail ? h.calls[1].reject(new Error('old failure')) : h.calls[1].resolve({ imported: 1, bundleName: 'A' }); await first;
    assert.equal(h.ctx.importPreview.filename, 'B.json'); assert.equal(h.ui.importDialog.open, true); assert.equal(h.ui.commitImport.disabled, true);
    assert.equal(h.changes.length, fail ? 0 : 1);
    h.calls[3].resolve({ imported: 1, bundleName: 'B' }); await second;
  }
});
test('metadata updates use the item profile and cross-profile overwrites never send a write', async () => {
  const h = harness(), item = { id: 'A', settings: { model_profile: 'anima' } }; h.profile('krea2');
  const update = h.ctx.updateStylePreset(item, { favorite: true }); assert.equal(h.calls[0].body.model_profile, 'anima');
  h.calls[0].resolve(item); await update;
  await h.ctx.updateStylePreset(item, { settings: { model_profile: 'krea2' } }); assert.equal(h.calls.length, 1);
});
test('a write success invalidates older list reads before a fresh list can be replaced', async () => {
  const h = harness(); const old = h.ctx.loadStylePresets(); const saving = h.ctx.updateStylePreset({ id: 'A', name: 'A', model_profile: 'anima' }, { favorite: true });
  h.calls[1].resolve({ id: 'A', name: 'A', settings: { model_profile: 'anima' } }); await flush();
  assert.equal(h.calls[0].options.signal?.aborted, true);
  h.calls.at(-1).resolve({ items: [{ id: 'A', settings: { model_profile: 'anima' } }] }); await saving;
  h.calls[0].resolve({ items: [] }); await old; assert.equal(h.ctx.stylePresets[0].id, 'A');
});

test('homepage save opens the explicit carry-options editor without writing', async () => {
  const h = harness(); let opened = 0;
  h.ctx.StylePresetManager = { create() { opened++; } };
  await h.ctx.createStylePreset(); assert.equal(opened, 1); assert.equal(h.calls.length, 0);
  h.ui.savePreset.disabled = true; await h.ctx.createStylePreset(); assert.equal(opened, 1);
});
