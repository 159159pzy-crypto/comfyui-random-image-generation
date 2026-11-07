const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = name => fs.readFileSync(path.join(__dirname, '../static', name), 'utf8');
const app = source('app.js');
const plain = value => JSON.parse(JSON.stringify(value));
function fn(name) {
  const start = app.indexOf(`function ${name}(`);
  return app.slice(start, app.indexOf('\n}', start) + 2);
}
function setup() {
  const elements = {};
  const ctx = vm.createContext({
    clone: plain, document: { getElementById: id => elements[id] || null },
    profileSettingsDefaults: () => ({ model_profile: 'anima', cfg: 4, sampler_name: 'er_sde', scheduler: 'simple', upscalers: { latent: { enabled: false, upscale_method: 'bilinear', scale_by: 1.5 } } }),
    ensureSelectValue: (element, value) => { element.value = value; },
    singleWorkbenchActive: () => false, ui: { base_mode: { value: 'normal' }, steps: { value: '30' }, latent_upscale_method: { value: 'sesqui' }, latent_upscale_scale: {} },
  });
  for (const name of ['defaultUpscalers', 'normalizeUpscalers']) vm.runInContext(fn(name), ctx);
  vm.runInContext(source('workbench-parameters.js'), ctx);
  for (const [key, value] of Object.entries(ctx.randomDualDefaults())) elements[`random_dual_${key}`] = { value: String(value), type: typeof value === 'boolean' ? 'checkbox' : typeof value === 'number' ? 'number' : 'select-one', checked: Boolean(value), tagName: typeof value === 'number' ? 'INPUT' : 'SELECT', disabled: false };
  elements.randomDualSection = { hidden: false, querySelectorAll: () => Object.values(elements).filter(e => e.type) };
  return { ctx, elements };
}
test('new random defaults match single dual and latent controls without changing server defaults', () => {
  const { ctx } = setup();
  const before = plain(ctx.profileSettingsDefaults());
  const settings = ctx.randomWorkbenchDefaultSettings('anima');
  assert.deepEqual(plain(settings.dual), { switch_step: 15, scale: 1, second_cfg: 8, second_sampler_name: 'euler', second_scheduler: 'simple', upscale_method: 'sesqui', model_format: 'Wan 2.1', half_precision: true });
  assert.equal(settings.upscalers.latent.upscale_method, 'sesqui');
  assert.deepEqual(plain(ctx.profileSettingsDefaults()), before);
});
test('editing an old random record retains its actual dual sampler and latent scale', () => {
  const { ctx } = setup();
  const old = { workbench: 'random', dual: null, cfg: 2.5, sampler_name: 'euler', scheduler: 'karras', upscalers: { latent: { enabled: false, scale_by: 1.75, upscale_method: 'bicubic' } } };
  const before = plain(old);
  ctx.setRandomDualControls(old);
  assert.deepEqual(plain(ctx.readRandomDualControls()), { switch_step: 15, scale: 1.75, second_cfg: 2.5, second_sampler_name: 'euler', second_scheduler: 'karras', upscale_method: 'bicubic', model_format: 'Wan 2.1', half_precision: true });
  assert.deepEqual(old, before);
});
test('dual fields hide and disable on normal mode without losing values, then restore', () => {
  const { ctx, elements } = setup();
  elements.random_dual_switch_step.value = '7';
  elements.random_dual_second_cfg.value = '5.2';
  const values = plain(ctx.readRandomDualControls());
  ctx.sharedParameterVisibility();
  assert.equal(elements.randomDualSection.hidden, true);
  assert.equal(elements.random_dual_second_cfg.disabled, true);
  ctx.ui.base_mode.value = 'dual_sampler';
  ctx.sharedParameterVisibility();
  assert.equal(elements.randomDualSection.hidden, false);
  assert.equal(elements.random_dual_second_cfg.disabled, false);
  assert.deepEqual(plain(ctx.readRandomDualControls()), values);
  assert.equal(elements.random_dual_switch_step.max, '29');
  ctx.singleWorkbenchActive = () => true;
  ctx.sharedParameterVisibility();
  assert.equal(elements.randomDualSection.hidden, false);
  assert.equal(elements.random_dual_second_cfg.disabled, false);
});
test('Sesqui constraints follow each workbench algorithm and never clamp a stored value', () => {
  const { ctx, elements } = setup();
  ctx.ui.latent_upscale_scale.value = '3';
  ctx.sharedParameterVisibility();
  assert.deepEqual([ctx.ui.latent_upscale_scale.min, ctx.ui.latent_upscale_scale.max, ctx.ui.latent_upscale_scale.value], ['1', '2', '3']);
  ctx.ui.latent_upscale_method.value = 'bicubic';
  elements.random_dual_upscale_method.value = 'bilinear';
  ctx.sharedParameterVisibility();
  assert.deepEqual([ctx.ui.latent_upscale_scale.min, ctx.ui.latent_upscale_scale.max, elements.random_dual_scale.max], ['0.01', '8', '8']);
  ctx.singleWorkbenchActive = () => true;
  ctx.ui.latent_upscale_method.value = 'sesqui';
  ctx.sharedParameterVisibility();
  assert.equal(ctx.ui.latent_upscale_scale.max, '2');
  ctx.ui.latent_upscale_method.value = 'bilinear';
  ctx.resources = { node_inputs: { LatentUpscaleBy: { scale_by: ['FLOAT', { min: .05, max: 6 }] } } };
  ctx.sharedParameterVisibility();
  assert.deepEqual([ctx.ui.latent_upscale_scale.min, ctx.ui.latent_upscale_scale.max, ctx.ui.latent_upscale_scale.value], ['0.05', '6', '3']);
});

test('v4 latent sampling checks its own sampler resources only while enabled', () => {
  const { ctx } = setup(); ctx.resourcesLoaded = true;
  ctx.resources = { node_types: ['LatentUpscaleBy'], samplers: ['euler'], schedulers: ['simple'] };
  const settings = { pipeline_version: 4, upscalers: { latent: { enabled: false, upscale_method: 'bilinear', sampler: { sampler_name: 'er_sde', scheduler: 'karras' } } } };
  assert.deepEqual(plain(ctx.sharedParameterResourceIssues(settings)), []);
  settings.upscalers.latent.enabled = true;
  const errors = ctx.sharedParameterResourceIssues(settings).join(' ');
  assert.match(errors, /KSampler/); assert.match(errors, /er_sde/); assert.match(errors, /karras/);
  settings.pipeline_version = 3;
  assert.deepEqual(plain(ctx.sharedParameterResourceIssues(settings)), [], 'v3 resize-only history cannot require a new sampler');
});



test('ordinary and dual modes use their first-entry defaults and restore independent edits including seeds', () => {
  const { ctx, elements } = setup();
  Object.assign(ctx.ui, { cfg: { value: '8' }, sampler_name: { value: 'euler' }, scheduler: { value: 'simple' }, base_denoise: { value: '1' }, krea_variance_enabled: { checked: false } });
  elements.randomSeedMode = { value: 'fixed' }; elements.randomSampleSeed = { value: '0' };
  ctx.setControl = (id, value) => { if (id === 'krea_variance_enabled') ctx.ui[id].checked = value; else ctx.ui[id].value = String(value); };
  ctx.updateRepairControls = () => {}; ctx.schedulePersist = () => {};
  ctx.ui.steps.value = '20'; ctx.sharedSamplingRestoreDrafts();
  ctx.ui.steps.value = '27'; ctx.ui.cfg.value = '5.5'; ctx.ui.base_denoise.value = '.74'; elements.randomSampleSeed.value = '123';
  ctx.ui.base_mode.value = 'dual_sampler'; ctx.sharedSamplingModeChanged();
  assert.deepEqual([ctx.ui.steps.value, ctx.ui.cfg.value, elements.randomSampleSeed.value], ['30', '8', '0']);
  assert.equal(ctx.readRandomDualControls().second_cfg, 8);
  ctx.ui.steps.value = '38'; elements.random_dual_second_cfg.value = '6.5'; elements.randomSeedMode.value = 'random';
  ctx.ui.base_mode.value = 'normal'; ctx.sharedSamplingModeChanged();
  assert.deepEqual([ctx.ui.steps.value, ctx.ui.cfg.value, ctx.ui.base_denoise.value, elements.randomSampleSeed.value], ['27', '5.5', '0.74', '123']);
  const persisted = plain(ctx.sharedSamplingDrafts());
  ctx.sharedSamplingRestoreDrafts(persisted);
  ctx.ui.base_mode.value = 'dual_sampler'; ctx.sharedSamplingModeChanged();
  assert.equal(ctx.ui.steps.value, '38'); assert.equal(ctx.readRandomDualControls().second_cfg, 6.5);
  assert.equal(elements.randomSeedMode.value, 'random');
});



test('unavailable face defaults only block generation when face refinement is enabled', () => {
  const { ctx } = setup();
  ctx.resourcesLoaded = true;
  ctx.resources = { samplers: ['euler'], schedulers: ['simple'], node_inputs: {} };
  const face = { enabled: false, sampler: 'res_2m_ode', scheduler: 'beta57' };
  assert.deepEqual(plain(ctx.sharedParameterResourceIssues({ upscalers: { face_refine: face } })), []);
  face.enabled = true;
  assert.equal(ctx.sharedParameterResourceIssues({ upscalers: { face_refine: face } }).length, 2);
  ctx.resources.node_inputs.ForbiddenVisionFaceProcessorIntegrated = { sampler: [['res_2m_ode']], scheduler: [['beta57']] };
  assert.deepEqual(plain(ctx.sharedParameterResourceIssues({ upscalers: { face_refine: face } })), []);
});


test('random first entry honors the model profile base mode while using that mode sampling defaults', () => {
  const { ctx } = setup();
  const defaults = ctx.profileSettingsDefaults();
  ctx.profileSettingsDefaults = () => ({ ...defaults, base_mode: 'dual_sampler' });
  const dual = ctx.randomWorkbenchDefaultSettings();
  assert.equal(dual.base_mode, 'dual_sampler'); assert.equal(dual.steps, 30); assert.equal(dual.cfg, 8);
  ctx.profileSettingsDefaults = () => ({ ...defaults, base_mode: 'normal' });
  const normal = ctx.randomWorkbenchDefaultSettings();
  assert.equal(normal.base_mode, 'normal'); assert.equal(normal.steps, 20);
});

test('legacy algorithms are explicit dropdown choices and never leak into model_format serialization', () => {
  const { ctx, elements } = setup();
  ctx.setRandomDualControls({ dual: { ...ctx.randomDualDefaults(), upscale_method: 'bicubic', model_format: 'Flux2', half_precision: false } });
  assert.equal(elements.random_dual_model_format.value, 'legacy:bicubic');
  const saved = ctx.readRandomDualControls();
  assert.equal(saved.upscale_method, 'bicubic'); assert.equal(saved.model_format, 'Flux2'); assert.equal(saved.half_precision, false);
  assert.equal(ctx.sharedLatentFormat('legacy:bilinear', 'SDXL'), 'SDXL');
  assert.equal(ctx.sharedLatentFormat('Wan 2.1', 'SDXL'), 'Wan 2.1');
});




test('live resource refresh preserves chosen formats, old algorithms and DLSS values', () => {
  const { ctx, elements } = setup();
  ctx.Option = class { constructor(text, value) { this.textContent = text; this.value = value; this.dataset = {}; } };
  const select = value => ({ value, tagName: 'SELECT', type: 'select-one', dataset: {}, options: [],
    add(option) { this.options.push(option); option.remove = () => { this.options.splice(this.options.indexOf(option), 1); }; },
    replaceChildren(...options) { this.options = []; options.forEach(option => this.add(option)); },
  });
  for (const name of ['ensureSelectValue', 'populateSelect', 'populateResourceSelect', 'applyNodeInputControl']) vm.runInContext(fn(name), ctx);
  elements.random_dual_model_format = select('Flux2'); elements.random_dual_upscale_method = select('sesqui');
  elements.random_dual_second_sampler_name = select('heun'); elements.random_dual_second_scheduler = select('beta');
  elements.latent_model_format = ctx.ui.latent_model_format = select('SDXL');
  ctx.ui.nvidia_dlss_style = select('自然'); ctx.ui.nvidia_dlss_intensity = { type: 'number', value: '.35' };
  ctx.resources = { samplers: ['euler', 'heun'], schedulers: ['simple', 'beta'], node_inputs: {
    SesquiLatentUpscale: { model_format: [['SDXL', 'Flux', 'Flux2', 'Ideogram 4', 'Wan 2.1']] },
    DLSS5NeuralRender: { style: [['默认', '自然', '电影']], intensity: ['FLOAT', { min: 0, max: 1, step: .05 }] },
  } };
  ctx.setRandomDualControls({ dual: { ...ctx.randomDualDefaults(), second_sampler_name: 'heun', second_scheduler: 'beta', model_format: 'Flux2' } });
  vm.runInContext('sharedParameterState.latent.model_format = "SDXL"', ctx);
  ctx.sharedParameterResources();
  assert.equal(elements.random_dual_model_format.value, 'Flux2'); assert.equal(ctx.ui.latent_model_format.value, 'SDXL');
  assert.equal(ctx.ui.nvidia_dlss_style.value, '自然'); assert.equal(ctx.ui.nvidia_dlss_intensity.value, '.35');
  assert.equal(elements.random_dual_second_sampler_name.value, 'heun');
  ctx.setRandomDualControls({ dual: { ...ctx.randomDualDefaults(), upscale_method: 'bicubic', model_format: 'Flux2' } });
  ctx.sharedParameterResources();
  assert.equal(elements.random_dual_model_format.value, 'legacy:bicubic');
  assert.equal(elements.random_dual_model_format.options.find(option => option.value === 'legacy:bicubic').textContent, '旧算法：bicubic');
  assert.equal(ctx.readRandomDualControls().model_format, 'Flux2');
});
