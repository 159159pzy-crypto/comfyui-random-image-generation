const createFrontendContext = require('./helpers/frontend-context.cjs');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
function functionSource(name) {
  let start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  if (source.slice(start - 6, start) === 'async ') start -= 6;
  const end = source.indexOf('\n}', start) + 2;
  return source.slice(start, end);
}
function context() {
  const ctx = createFrontendContext({
    SECTIONS: ['character', 'clothing', 'pose', 'background', 'expression'],
    defaults: { profile_settings: { anima: {}, krea2: {} } },
    draft: {}, profileDrafts: {}, ui: {}, activeProfileId: 'anima',
    clone: x => JSON.parse(JSON.stringify(x)),
    normalizeProfileId: x => x === 'krea2' ? 'krea2' : 'anima',
    defaultPools: () => ({ pose: { ids: [] } }), defaultUpscalers: () => ({}),
    profileSettingsDefaults: () => ({ random_character: true, fixed_pose: '', pools: {}, loras: [] }),
    profileConfig: () => ({}),
  });
  for (const name of ['defaultUpscalers', 'normalizeUpscalers', 'profileStateFrom', 'profileDraftFromDraft', 'restoreProfileDraft']) vm.runInContext(functionSource(name), ctx);
  return ctx;
}
function plain(value) { return JSON.parse(JSON.stringify(value)); }
test('history and legacy settings override defaults, including empty arrays', () => {
  const ctx = context();
  ctx.profileDrafts.anima = { modes: { pose: 'off' }, fixed: { pose: 'unrelated' }, pools: {}, loras: [] };
  ctx.restoreProfileDraft('anima', { fixed_pose: 'standing', pools: { pose: { ids: ['pose:one'] } }, loras: [{ filename: 'saved.safetensors' }] }, { preferStored: false });
  assert.equal(ctx.draft.modes.pose, 'fixed');
  assert.equal(ctx.draft.fixed.pose, 'standing');
  assert.deepEqual(plain(ctx.draft.pools.pose.ids), ['pose:one']);
  assert.equal(ctx.draft.loras[0].filename, 'saved.safetensors');
  ctx.restoreProfileDraft('anima', { loras: [], fixed_pose: '' }, { preferStored: false });
  assert.deepEqual(plain(ctx.draft.loras), []);
  assert.equal(ctx.draft.fixed.pose, '');
});
test('saved profile draft preserves inactive fixed text and model isolation', () => {
  const ctx = context();
  ctx.profileDrafts.anima = { modes: { pose: 'pool' }, fixed: { pose: 'draft standing' }, pools: {}, loras: [] };
  ctx.restoreProfileDraft('anima', { fixed_pose: 'history' });
  assert.equal(ctx.draft.fixed.pose, 'draft standing');
  assert.equal(ctx.draft.modes.pose, 'pool');
  ctx.restoreProfileDraft('krea2', { model_profile: 'krea2', loras: [{ filename: 'krea.safetensors' }] }, { preferStored: false });
  assert.equal(ctx.draft.loras[0].filename, 'krea.safetensors');
  ctx.restoreProfileDraft('anima');
  assert.equal(ctx.draft.fixed.pose, 'draft standing');
});
test('generation omits inactive fixed drafts while retaining pools and count', () => {
  const ctx = context();
  vm.runInContext(functionSource('readSettings'), ctx);
  ctx.saveCurrentProfileDraft = () => {};
  ctx.canonicalArtists = x => x;
  ctx.currentProfile = () => 'anima';
  ctx.readUpscalerControls = () => ({});
  ctx.readKreaPoolSelection = () => ({ ids: [] });
  const fields = ['count','female_count','male_count','character_detail','manual_artist','quality_prompt','extra_prompt','negative_prompt','width','height','steps','cfg','sampler_name','scheduler','model_name','base_mode','krea_prompt_pool_enabled'];
  ctx.ui = Object.fromEntries(fields.map(x => [x, { value: '1', checked: false }]));
  ctx.ui.count.value = '7';
  ctx.draft = { modes: { character: 'pool', clothing: 'off', pose: 'fixed' }, fixed: { character: 'hidden identity', clothing: 'hidden coat', pose: 'standing' }, counts: {}, pools: { pose: { ids: ['pose:one'] } }, loras: [] };
  const settings = ctx.readSettings();
  assert.equal(settings.pipeline_version, 4);
  assert.equal(settings.fixed_character, '');
  assert.equal(settings.fixed_clothing, '');
  assert.equal(settings.fixed_pose, 'standing');
  assert.equal(settings.count, 7);
  assert.deepEqual(plain(settings.pools.pose.ids), ['pose:one']);
  assert.equal(ctx.draft.fixed_character, undefined);
  assert.equal(ctx.draft.fixed.character, 'hidden identity');
});

test('partial redraw requires selecting every non-preservable dimension', () => {
  const ctx = context();
  ctx.ui.confirmVariant = {};
  ctx.ui.variantStatus = { dataset: {} };
  ctx.selectedRegenerationMode = () => 'content_redraw';
  ctx.selectedRedrawSections = () => ['pose'];
  ctx.setRedrawSectionsVisible = () => {};
  ctx.escapeHtml = x => x;
  ctx.regenerationReasons = () => [];
  ctx.regenerationOptions = { modes: { content_redraw: { available: true } }, resourceIssues: [], sections: { character: { random: true, canPreserve: false, preserveReason: '角色身份与最终提示词冲突，请同时重抽角色' }, pose: { random: true, canPreserve: true } } };
  vm.runInContext(functionSource('updateVariantDrawer'), ctx);
  ctx.updateVariantDrawer();
  assert.equal(ctx.ui.confirmVariant.disabled, true);
  assert.match(ctx.ui.variantStatus.innerHTML, /同时重抽角色/);
  ctx.selectedRedrawSections = () => ['character', 'pose'];
  ctx.updateVariantDrawer();
  assert.equal(ctx.ui.confirmVariant.disabled, false);
  assert.equal(ctx.ui.variantStatus.dataset.state, 'ok');
});

function classificationContext() {
  const ctx = context();
  ctx.URL = URL;
  ctx.config = {};
  ctx.submitInFlight = false;
  ctx.currentProfile = () => ctx.activeProfileId;
  ctx.profileLabel = value => value === 'krea2' ? 'Krea 2' : 'Anima';
  ctx.resources = { models: ['anima.safetensors'], model_items: [] };
  ctx.resourcesLoaded = true;
  ctx.resourceInventoryProfile = 'anima';
  ctx.resourceError = '';
  ctx.loraInventory = [];
  ctx.loraClassificationItems = [];
  ctx.loraInventoryLoaded = true;
  ctx.loraInventoryProfile = 'anima';
  ctx.loraInventoryError = '';
  ctx.loraInventoryRequest = 0;
  ctx.draft = { loras: [] };
  const connection = { className: 'status-badge online', querySelector: () => ({}) };
  connection.classList = { contains: name => connection.className.split(' ').includes(name) };
  ctx.ui = { model_name: { value: 'anima.safetensors' }, startButton: {}, connection };
  for (const name of ['normalizedPath', 'loraItem', 'classificationItem', 'classificationReason', 'loraSelectionIssue', 'modelSelectionIssue', 'generationClassificationIssues', 'updateGenerationAvailability', 'replaceLoraInventoryItem']) {
    vm.runInContext(functionSource(name), ctx);
  }
  return ctx;
}

test('enabled incompatible LoRAs remain saved and block generation with the Manager reason', () => {
  const ctx = classificationContext();
  ctx.draft.loras = [{ filename: 'saved.safetensors', enabled: true, strength: 0.8 }];
  ctx.loraClassificationItems = [{ filename: 'saved.safetensors', model_profile: 'krea2', classification_status: 'recognized', base_model: 'Krea 2' }];
  ctx.updateGenerationAvailability();
  assert.equal(ctx.ui.startButton.disabled, true);
  assert.match(ctx.ui.startButton.title, /Krea 2/);
  assert.equal(ctx.draft.loras[0].filename, 'saved.safetensors');
  assert.equal(ctx.draft.loras[0].strength, 0.8);
  ctx.draft.loras[0].enabled = false;
  ctx.updateGenerationAvailability();
  assert.equal(ctx.ui.startButton.disabled, false);
});

test('unknown and unsupported classifications display the backend reason', () => {
  const ctx = classificationContext();
  ctx.resources.models = [];
  ctx.resources.model_items = [{ name: 'anima.safetensors', model_profile: null, classification_status: 'unknown', classification_reason: 'base_model 未填写' }];
  assert.match(ctx.modelSelectionIssue(), /base_model 未填写/);
  ctx.loraClassificationItems = [{ filename: 'sdxl.safetensors', model_profile: null, classification_status: 'unsupported', classification_reason: '不支持 SDXL' }];
  assert.match(ctx.loraSelectionIssue('sdxl.safetensors'), /不支持 SDXL/);
});

test('connection polling cannot enable generation while Manager classification is unavailable', async () => {
  const ctx = classificationContext();
  ctx.document = { hidden: false };
  ctx.request = async () => ({ online: true, version: 'test' });
  ctx.loraInventoryLoaded = false;
  ctx.loraInventoryError = 'LoRA Manager 不可用';
  vm.runInContext(functionSource('refreshConnection'), ctx);
  await ctx.refreshConnection();
  assert.equal(ctx.ui.startButton.disabled, true);
  assert.match(ctx.ui.startButton.title, /LoRA Manager 不可用/);
  ctx.loraInventoryLoaded = true;
  ctx.loraInventoryError = '';
  await ctx.refreshConnection();
  assert.equal(ctx.ui.startButton.disabled, false);
  ctx.config.storage_status = { history: { read_only: true, reason: '备份失败' } };
  await ctx.refreshConnection();
  assert.equal(ctx.ui.startButton.disabled, true);
});

test('trigger edits do not promote a reclassified item into the selectable inventory', () => {
  const ctx = classificationContext();
  ctx.loraInventory = [{ filename: 'saved.safetensors', model_profile: 'anima', classification_status: 'recognized' }];
  ctx.replaceLoraInventoryItem({ filename: 'saved.safetensors', model_profile: null, classification_status: 'unindexed', classification_reason: '插件未索引', trigger_words: ['saved'] });
  assert.equal(ctx.loraItem('saved.safetensors'), null);
  assert.equal(ctx.loraInventory.length, 0);
  assert.match(ctx.loraSelectionIssue('saved.safetensors'), /插件未索引/);
});



class TestOption {
  constructor(text, value) { this.text = text; this.value = value; this.dataset = {}; this.disabled = false; }
}
function modelSelect() {
  return {
    value: '', children: [],
    replaceChildren(...children) { this.children = children; },
    get options() { return this.children.flatMap(item => item.children || [item]); },
  };
}

test('restored and stale explicit model paths stay visible as disabled options', () => {
  const ctx = classificationContext();
  ctx.Option = TestOption;
  ctx.document = { createElement: () => ({ children: [], append(item) { this.children.push(item); } }) };
  ctx.ui.model_name = modelSelect();
  ctx.ui.model_name.value = 'old/anima.safetensors';
  ctx.resources.models = ['new/anima.safetensors'];
  ctx.resources.model_items = [{ name: 'new/anima.safetensors', model_profile: 'anima', classification_status: 'recognized' }];
  for (const name of ['resolveInventoryPath', 'populateModelSelect', 'refreshCurrentModelSelection']) vm.runInContext(functionSource(name), ctx);
  ctx.refreshCurrentModelSelection();
  assert.equal(ctx.ui.model_name.value, 'old/anima.safetensors');
  assert.equal(ctx.ui.model_name.options[0].disabled, true);
  assert.match(ctx.ui.model_name.options[0].text, /不可用/);
  assert.match(ctx.ui.model_name.options[0].text, /不在当前/);
  ctx.ui.model_name.value = 'anima.safetensors';
  ctx.refreshCurrentModelSelection();
  assert.equal(ctx.ui.model_name.value, 'new/anima.safetensors');
  assert.equal(ctx.ui.model_name.options[0].disabled, false);
});

test('a matching basename cannot replace an incompatible exact model or LoRA', () => {
  const ctx = classificationContext();
  ctx.Option = TestOption;
  ctx.document = { createElement: () => ({ children: [], append(item) { this.children.push(item); } }) };
  ctx.ui.model_name = modelSelect();
  ctx.ui.model_name.value = 'model.safetensors';
  ctx.resources.models = ['anima/model.safetensors'];
  ctx.resources.model_items = [
    { name: 'model.safetensors', model_profile: null, classification_status: 'unknown', classification_reason: 'base_model 未填写' },
    { name: 'anima/model.safetensors', model_profile: 'anima', classification_status: 'recognized' },
  ];
  for (const name of ['populateModelSelect', 'refreshCurrentModelSelection']) vm.runInContext(functionSource(name), ctx);
  ctx.refreshCurrentModelSelection();
  assert.equal(ctx.ui.model_name.value, 'model.safetensors');
  assert.equal(ctx.ui.model_name.options[0].disabled, true);
  assert.match(ctx.ui.model_name.options[0].text, /base_model 未填写/);
  ctx.loraInventory = [{ filename: 'anima/model.safetensors', model_profile: 'anima', classification_status: 'recognized' }];
  assert.equal(ctx.loraItem('model.safetensors'), null);
  assert.equal(ctx.loraItem('anima/model.safetensors').filename, 'anima/model.safetensors');
});

test('only the latest ownership request can update the selected resource kind', async () => {
  const ctx = classificationContext();
  const pending = [];
  ctx.config.comfy_url = 'http://127.0.0.1:8188';
  ctx.ownershipRequest = 0;
  ctx.ownershipItems = [];
  ctx.ui.ownershipKind = { value: 'models' };
  ctx.ui.ownershipError = {};
  ctx.ui.ownershipSummary = {};
  ctx.ui.ownershipList = { replaceChildren() {} };
  ctx.renderResourceOwnership = () => {};
  ctx.request = url => new Promise(resolve => pending.push({ url, resolve }));
  for (const name of ['loadResourceOwnership']) vm.runInContext(functionSource(name), ctx);
  const models = ctx.loadResourceOwnership({ refresh: true });
  ctx.ui.ownershipKind.value = 'loras';
  const loras = ctx.loadResourceOwnership();
  pending[1].resolve({ items: [{ name: 'lora.safetensors' }], manager_url: 'http://127.0.0.1:8188/loras' });
  await loras;
  pending[0].resolve({ items: [{ name: 'model.safetensors' }], manager_url: 'http://127.0.0.1:8188/checkpoints' });
  await models;
  assert.equal(ctx.ownershipItems[0].name, 'lora.safetensors');
});

test('LoRA refresh preserves profile drafts and rejects a stale response after switching profiles', async () => {
  const ctx = classificationContext();
  const pending = [];
  ctx.request = url => new Promise(resolve => pending.push({ url, resolve }));
  ctx.renderLoras = ctx.renderLoraCatalog = ctx.updateRepairControls = ctx.reconcileLoraTriggers = () => {};
  ctx.draft.loras = [{ filename: 'anima-saved.safetensors', enabled: true }];
  vm.runInContext(functionSource('loadLoraInventory'), ctx);
  const oldLoad = ctx.loadLoraInventory({ refresh: true });
  assert.match(pending[0].url, /refresh=1/);
  ctx.activeProfileId = 'krea2';
  ctx.draft.loras = [{ filename: 'krea-saved.safetensors', enabled: true }];
  const currentLoad = ctx.loadLoraInventory({ refresh: true });
  pending[1].resolve({ items: [{ filename: 'krea.safetensors', model_profile: 'krea2', classification_status: 'recognized' }], classification_items: [] });
  await currentLoad;
  pending[0].resolve({ items: [{ filename: 'anima.safetensors', model_profile: 'anima', classification_status: 'recognized' }] });
  await oldLoad;
  assert.equal(ctx.loraInventory[0].filename, 'krea.safetensors');
  assert.equal(ctx.loraInventoryProfile, 'krea2');
  assert.equal(ctx.draft.loras[0].filename, 'krea-saved.safetensors');
  assert.equal(ctx.loraInventoryLoaded, true);
});

test('favorite reads cannot replace the active section with a stale response', async () => {
  const pending = [];
  const ctx = createFrontendContext({ favoritesRequest: 0, activeSection: 'pose', favoritesData: {}, favoritesAvailable: false, currentProfile: () => 'anima',
    request: path => new Promise(resolve => pending.push({ path, resolve })), updateFavoriteSelectionButton() {}, toast() {}, currentView: () => ({ filters: {} }) });
  for (const name of ['characterPickerSession', 'poolBrowserProfile', 'loadFavorites']) vm.runInContext(functionSource(name), ctx);
  const first = ctx.loadFavorites('pose'); ctx.activeSection = 'character'; const second = ctx.loadFavorites('character');
  pending[1].resolve({ items: ['current'] }); await second;
  pending[0].resolve({ items: ['stale'] }); await first;
  assert.deepEqual(plain(ctx.favoritesData.items), ['current']);
});

test('node resource controls retain unavailable selections and apply live numeric ranges', () => {
  const ctx = context();
  ctx.Option = class { constructor(text, value) { this.textContent = text; this.value = value; this.dataset = {}; } };
  for (const name of ['populateResourceSelect', 'ensureSelectValue', 'applyNodeInputControl']) vm.runInContext(functionSource(name), ctx);
  const select = { tagName: 'SELECT', dataset: {}, value: '90', options: [], replaceChildren(...options) { this.options = options; }, add(option) { this.options.push(option); } };
  ctx.applyNodeInputControl(select, [['None', '90° CW', '90° CCW', '180°'], { default: 'None' }]);
  assert.equal(select.value, '90');
  assert.match(select.options[0].textContent, /当前不可用/);
  assert.equal(select.options[0].dataset.missing, 'true');
  ctx.ensureSelectValue(select, '270');
  assert.match(select.options.at(-1).textContent, /当前不可用/);
  assert.equal(select.value, '270');
  const numeric = { type: 'number', value: '64' };
  ctx.applyNodeInputControl(numeric, ['FLOAT', { min: 1, max: 4, step: 0.01 }]);
  assert.deepEqual(numeric, { type: 'number', value: '64', min: '1', max: '4', step: '0.01' });
  const quality = { ...select, dataset: {}, value: 'ULTRA', options: [] };
  ctx.applyNodeInputControl(quality, ['COMBO', { options: ['LOW', 'MEDIUM', 'HIGH', 'ULTRA'] }]);
  assert.equal(quality.options.length, 4);
  assert.equal(quality.value, 'ULTRA');
});
