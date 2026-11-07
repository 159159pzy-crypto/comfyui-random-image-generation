const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
function load(ctx, name) {
  const start = app.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  vm.runInContext(app.slice(start, app.indexOf('\n}', start) + 2), ctx);
}
function context() {
  const ctx = vm.createContext({ clone: plain, ui: {}, applyNodeResourceControls() {}, ensureSelectValue: (node, value) => { node.value = value; } });
  for (const name of ['defaultUpscalers', 'normalizeUpscalers']) load(ctx, name);
  return ctx;
}

test('v3 post sampling and DLSS remain independent of latent and VSR flags', () => {
  const ctx = context();
  const value = ctx.normalizeUpscalers({ latent: { enabled: false }, nvidia: { enabled: false }, post_sampler: { enabled: true, steps: 7 }, dlss: { enabled: true } }, null, 'anima', 3);
  assert.equal(value.post_sampler.enabled, true); assert.equal(value.post_sampler.steps, 7);
  assert.equal(value.dlss.enabled, true); assert.equal(value.nvidia.enabled, false);
  const off = ctx.normalizeUpscalers({ latent: { enabled: true, second_sampler: { enabled: true } }, nvidia: { enabled: true }, post_sampler: { enabled: false }, dlss: { enabled: false } }, null, 'anima', 3);
  assert.equal(off.post_sampler.enabled, false); assert.equal(off.dlss.enabled, false);
});


test('controls round trip canonical settings including disabled shrink and independent post sampler', () => {
  const ctx = context();
  vm.runInContext(app.slice(app.indexOf('const UPSCALER_CONTROL_FIELDS'), app.indexOf('\nfunction refreshProfileData')), ctx);
  vm.runInContext('const NVIDIA_DLSS_KEYS = ["style", "intensity", "local_tone", "local_struct", "output_mix"]; const sharedParameterState = {latent:{}, dlss:{}};', ctx);
  const ids = vm.runInContext('[...Object.values(UPSCALER_CONTROL_IDS).flatMap(Object.values), ...Object.values(POST_SAMPLER_IDS), ...Object.values(LATENT_SAMPLER_IDS), "latent_second_sampler_enabled", ...Object.values(SEED_SAMPLER_IDS), ...Object.values(SEED_TILE_IDS.encode), ...Object.values(SEED_TILE_IDS.decode), ...NVIDIA_DLSS_KEYS.map(key => `nvidia_dlss_${key}`)]', ctx);
  for (const id of ids) ctx.ui[id] = { type: id.endsWith('_enabled') ? 'checkbox' : /steps|cfg|denoise|percent|scale|size|overlap|tone|struct|mix|intensity/.test(id) ? 'number' : 'text', tagName: 'INPUT', value: '', checked: false };
  Object.assign(ctx.ui.latent_upscale_method, { type: 'select-one', tagName: 'SELECT' });
  const settings = ctx.normalizeUpscalers({ pixel_hires: { enabled: true, percent: 37 }, latent: { enabled: false, upscale_method: 'bilinear', sampler: { steps: 29, denoise: .41 } }, post_sampler: { enabled: true, denoise: .33 }, dlss: { enabled: true, output_mix: .6 } }, null, 'anima');
  ctx.setUpscalerControls(settings);
  const result = ctx.readUpscalerControls();
  assert.equal(result.pixel_hires.percent, 37); assert.equal(result.post_sampler.enabled, true);
  assert.equal(result.post_sampler.denoise, .33); assert.equal(result.dlss.enabled, true); assert.equal(result.dlss.output_mix, .6);
  assert.equal(result.latent.enabled, false); assert.equal(result.latent.upscale_method, 'bilinear');
  assert.deepEqual(plain(result.latent.sampler), { steps: 29, cfg: 4, denoise: .41, sampler_name: 'er_sde', scheduler: 'simple' });
  assert.equal(result.latent.second_sampler, undefined); assert.equal(result.nvidia.dlss, undefined);
});



test('capability checks distinguish VSR and DLSS and require pixel models only when enabled', () => {
  const ctx = context();
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/workbench-parameters.js'), 'utf8'), ctx);
  ctx.resourcesLoaded = true;
  ctx.resources = { node_types: ['DLSS5NeuralRender', 'KSampler'], samplers: ['er_sde'], schedulers: ['simple'], upscale_models: [] };
  assert.deepEqual(plain(ctx.sharedParameterResourceIssues({ upscalers: { dlss: { enabled: true } } })), []);
  assert.match(ctx.sharedParameterResourceIssues({ upscalers: { nvidia: { enabled: true } } }).join(), /RTXVideoSuperResolution/);
  assert.match(ctx.sharedParameterResourceIssues({ upscalers: { pixel_hires: { enabled: true, model_name: 'missing.pth' } } }).join(), /missing.pth/);
});

test('shared markup has visible independent toggles in the fixed pipeline order', () => {
  const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
  const order = [...html.matchAll(/class="sampling-stage sampling-stage-(pixel_hires|nvidia|seedvr2|latent|post_sampler|face|dlss)"/g)].map(match => match[1]);
  assert.deepEqual(order, ['pixel_hires', 'latent', 'post_sampler', 'face', 'seedvr2', 'nvidia', 'dlss']);
  assert.match(html, /class="switch-row"><span><strong>整图后采样/);
  assert.match(html, /id="pixelHiresResizeHint"/);
});
