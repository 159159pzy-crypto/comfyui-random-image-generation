const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const stageControls = [
  ['pixel_hires', 'pixel_hires_enabled'], ['latent', 'latent_upscale_enabled'], ['post_sampler', 'latent_second_sampler_enabled'],
  ['face_refine', 'face_refine_enabled'], ['seedvr2', 'seedvr2_enabled'], ['nvidia', 'nvidia_upscale_enabled'], ['dlss', 'dlss_enabled'],
];
function load(ctx, name) {
  let start = app.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  if (app.slice(start - 6, start) === 'async ') start -= 6;
  vm.runInContext(app.slice(start, app.indexOf('\n}', start) + 2), ctx);
}
function setup(profile, workbench) {
  const ui = {}, requests = [], saved = [], gates = [];
  for (const match of html.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const [, tag, attrs, id] = match, listeners = new Map();
    ui[id] = { tagName: tag.toUpperCase(), type: attrs.match(/\btype="([^"]+)"/)?.[1] || '',
      value: '', checked: false, disabled: false, textContent: '', classList: { toggle() {} },
      querySelectorAll: () => [], closest: () => null,
      addEventListener(name, listener) { if (!listeners.has(name)) listeners.set(name, []); listeners.get(name).push(listener); },
      dispatch(name) { return Promise.all((listeners.get(name) || []).map(listener => listener({ target: this, preventDefault() {} }))); },
    };
  }
  const sourceImage = { input_image_id: 'chosen-original', positive_prompt: 'original prompt' };
  const ctx = vm.createContext({
    ui, document: { getElementById: id => ui[id] || null }, clone: plain, submitInFlight: false, resourcesLoaded: false, resourceError: '', resources: {},
    currentProfile: () => profile, profileLabel: value => value, profileConfig: () => ({}),
    refreshCurrentModelSelection() {}, updateProfileResources() {}, updateGenerationAvailability() {},
    applyNodeResourceControls() {}, ensureSelectValue: (node, value) => { node.value = value; },
    generationClassificationIssues: () => [], toast() {}, refreshConnection: async () => {},
    singleWorkbenchActive: () => workbench === 'single', singleWorkbenchInputBlocked: () => false,
    randomWorkbenchSeeds: () => ({ sample_seed: 777 }),
    renderBatchPayload() {},
    request: (url, options) => {
      requests.push({ url, body: options?.body ? JSON.parse(options.body) : null });
      if (url !== '/api/batches') return Promise.resolve({});
      return new Promise(resolve => gates.push(() => resolve({ status: 'running' })));
    },
  });
  load(ctx, 'defaultUpscalers');
  vm.runInContext(app.slice(app.indexOf('const UPSCALER_CONTROL_FIELDS'), app.indexOf('\nfunction refreshProfileData')), ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/workbench-parameters.js'), 'utf8'), ctx);
  ctx.readSettings = () => ({ pipeline_version: 4, model_profile: profile, workbench, upscalers: ctx.readUpscalerControls() });
  ctx.schedulePersist = ctx.persistNow = () => saved.push(plain(ctx.readSettings()));
  for (const name of ['pipelineFlowText', 'updateRepairControls', 'submitBatch']) load(ctx, name);
  const formStart = app.indexOf('ui.settingsForm.addEventListener("submit"');
  vm.runInContext(app.slice(formStart, app.indexOf('ui.settingsForm.addEventListener("input"', formStart)), ctx);
  const controlsStart = app.search(/for \(const control of \[\s*ui\.pixel_hires_enabled/);
  assert.ok(controlsStart >= 0);
  vm.runInContext(app.slice(controlsStart, app.indexOf('ui.themeToggle.addEventListener', controlsStart)), ctx);
  ctx.setUpscalerControls(ctx.defaultUpscalers(profile));
  ctx.updateRepairControls();
  return { ctx, ui, requests, saved, gates, sourceImage, submit: () => ui.settingsForm.dispatch('submit'),
    change: async (id, checked) => { ui[id].checked = checked; await ui[id].dispatch('change'); },
    posts: () => requests.filter(item => item.url === '/api/batches'),
  };
}

for (const profile of ['anima']) for (const workbench of ['random']) {
  test(`${profile}/${workbench}: stage changes save configuration; Start captures one complete pipeline from the selected base`, async () => {
    const h = setup(profile, workbench);
    assert.equal(h.ui.pipelineFlow.textContent, '底图 → 保存');
    assert.equal(h.ui.latentUpscaleFields.hidden, true);
    assert.equal(h.ui.latent_sampler_steps.disabled, true);
    assert.equal(h.ui.latent_sampler_steps.value, 16);
    h.ui.latent_sampler_steps.value = 29;
    h.ui.latent_sampler_cfg.value = 3.4;
    h.ui.latent_sampler_denoise.value = .41;
    h.ui.latent_model_format.value = 'SDXL';
    h.ui.latent_upscale_method.value = 'bilinear';
    h.ui.latent_upscale_scale.value = 3;
    await h.ui.latent_upscale_method.dispatch('change');
    assert.equal(h.ui.latentModelFormatField.hidden, true);
    assert.deepEqual([h.ui.latent_upscale_scale.min, h.ui.latent_upscale_scale.max, h.ui.latent_upscale_scale.value], ['0.01', '8', 3]);
    h.ui.latent_upscale_method.value = 'sesqui'; await h.ui.latent_upscale_method.dispatch('change');
    assert.equal(h.ui.latentModelFormatField.hidden, false);
    assert.equal(h.ui.latent_model_format.value, 'SDXL');
    assert.deepEqual([h.ui.latent_upscale_scale.min, h.ui.latent_upscale_scale.max, h.ui.latent_upscale_scale.value], ['1', '2', 3], 'algorithm changes do not clamp a saved value');
    h.ui.latent_upscale_scale.value = 1.7;
    // Toggle out of execution order so the preview cannot accidentally follow click order.
    for (const [, id] of [...stageControls].reverse()) await h.change(id, true);
    assert.equal(h.ui.pipelineFlow.textContent, '底图 → 像素高清 → 潜空间放大 → 整图后采样 → 脸部细化 → SeedVR2 → N 卡 VSR → DLSS → 保存');
    assert.equal(h.requests.length, 0, 'switch changes must never start or extend a generation');
    assert.ok(h.saved.length >= stageControls.length);
    assert.equal(h.ui.pixel_hires_percent.disabled, true);
    assert.equal(h.ui.pixelHiresResizeHint.textContent, '后采样已开启，跳过缩图');
    assert.equal(h.ui.latentUpscaleFields.hidden, false);
    assert.equal(h.ui.latent_sampler_steps.disabled, false);
    assert.equal(h.ui.latentDoubleSamplingHint.hidden, false);

    const first = h.submit();
    assert.equal(h.posts().length, 1);
    const submitted = plain(h.posts()[0].body);
    for (const [stage] of stageControls) assert.equal(submitted.upscalers[stage].enabled, true, stage);
    assert.equal(submitted.seeds.sample_seed, 777);
    assert.deepEqual(submitted.upscalers.latent.sampler, { steps: 29, cfg: 3.4, sampler_name: 'er_sde', scheduler: 'simple', denoise: .41 });
    assert.equal(submitted.upscalers.post_sampler.steps, 16);

    await h.change('latent_second_sampler_enabled', false);
    await h.change('nvidia_upscale_enabled', false);
    assert.equal(h.ui.pixel_hires_percent.disabled, false);
    assert.equal(h.ui.pixelHiresResizeHint.textContent, '按原生放大结果缩图');
    assert.equal(h.ui.latentDoubleSamplingHint.hidden, true);
    assert.equal(h.ui.pipelineFlow.textContent, '底图 → 像素高清 → 潜空间放大 → 脸部细化 → SeedVR2 → DLSS → 保存');
    await h.submit();
    assert.equal(h.posts().length, 1, 'a pending submit owns the double-click lock');
    assert.deepEqual(h.posts()[0].body, submitted, 'editing next-task switches cannot mutate the submitted snapshot');
    h.gates.shift()(); await first;

    const second = h.submit();
    assert.equal(h.posts().length, 2);
    assert.equal(h.posts()[1].body.upscalers.post_sampler.enabled, false);
    assert.equal(h.posts()[1].body.upscalers.nvidia.enabled, false);
    assert.equal(h.posts()[1].body.upscalers.pixel_hires.enabled, true);
    assert.equal(h.posts()[1].body.upscalers.dlss.enabled, true);
    h.gates.shift()(); await second;
    for (const [, id] of stageControls) await h.change(id, false);
    assert.equal(h.ui.pipelineFlow.textContent, '底图 → 保存');
    assert.equal(h.ui.latentUpscaleFields.hidden, true);
    assert.equal(h.ui.latent_sampler_steps.disabled, true);
    assert.deepEqual(plain(h.ctx.readUpscalerControls().latent.sampler), submitted.upscalers.latent.sampler);
    await h.change('latent_upscale_enabled', true);
    assert.equal(h.ui.latentUpscaleFields.hidden, false);
    assert.equal(h.ui.latent_sampler_steps.value, 29);
    assert.equal(h.posts().length, 2);
  });
}

test('processing instructions are shared once and advanced explanations start collapsed', () => {
  assert.equal(html.includes('点击「开始生成」，从底图执行本次所选流程。'), false);
  assert.equal(html.split('<strong>流程：</strong>').length - 1, 1);
  assert.equal(html.split('id="pipelineFlow"').length - 1, 1);
  const help = html.match(/<details class="pipeline-help">([\s\S]*?)<\/details>/)?.[1];
  assert.ok(help); assert.match(help, /继承底图最后一段的模型、LoRA 和提示词/);
  assert.match(help, /同时开启会重绘两次/);
  const algorithms = html.match(/<select id="latent_upscale_method">([\s\S]*?)<\/select>/)?.[1];
  assert.deepEqual([...algorithms.matchAll(/value="([^"]+)"/g)].map(match => match[1]), ['sesqui', 'bilinear', 'bislerp', 'nearest-exact', 'area', 'bicubic']);
  assert.doesNotMatch(algorithms, /legacy:|旧算法/);
  assert.ok(html.indexOf('sampling-stage-plan') > html.indexOf('sampling-stage-dlss'));
});
