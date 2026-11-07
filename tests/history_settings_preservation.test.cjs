const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const random = fs.readFileSync(path.join(__dirname, '../static/random-workbench.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const sections = ['character', 'clothing', 'pose', 'background', 'expression'];
function load(ctx, name) {
  let start = app.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  if (app.slice(start - 6, start) === 'async ') start -= 6;
  vm.runInContext(app.slice(start, app.indexOf('\n}', start) + 2), ctx);
}
function context() {
  const elements = { randomSeedMode: { value: 'fixed' }, randomSampleSeed: { value: '42' } };
  const writes = [], storage = new Map(), applied = [];
  const pools = Object.fromEntries(sections.map(section => [section, { mode: 'include', ids: [`${section}:live`], excluded_ids: [] }]));
  const ctx = vm.createContext({
    SECTIONS: sections, document: { getElementById: id => elements[id] }, clone: plain,
    draft: { modes: { character: 'pool', clothing: 'off', pose: 'fixed', background: 'pool', expression: 'off' },
      counts: { character: 2, background: 3 }, fixed: { clothing: 'hidden coat', pose: 'standing' }, pools },
    settings: { count: 7, width: 1024, height: 1024, model_profile: 'anima', workbench: 'random', pipeline_version: 4 },
    readSettings: () => ctx.settings,
    profileDraftFromDraft: () => ctx.draft,
    applySettings: (settings, options) => { applied.push({ settings: plain(settings), options }); ctx.settings = settings; ctx.draft = plain(ctx.draft); },
    renderDimensions() {}, updatePoolOverview() {}, sharedParameterVisibility() {}, persistNow() {},
    samplingModes: { normal: { steps: 23 }, dual_sampler: { steps: 39 } },
    sharedSamplingDrafts: () => plain(ctx.samplingModes), sharedSamplingRestoreDrafts: value => { ctx.samplingModes = value; },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => { writes.push(key); storage.set(key, value); }, removeItem: key => { throw new Error(`unexpected deletion: ${key}`); } },
    initialized: true, DRAFT_KEY: 'anima-random-webui:draft:v1', VIEW_KEY: 'anima-random-webui:pool-view:v1',
    ui: { draftStatus: {} }, profileDrafts: { anima: { fixed: { clothing: 'hidden coat' } } }, activeSection: 'pose', poolViews: {}, characterPickerSession: () => null,
    formatSavedTime: () => '12:00',
  });
  vm.runInContext(random, ctx);
  return { ctx, elements, storage, writes, applied };
}

test('history restores image parameters and image seed while retaining count and complete pool editor state', () => {
  const { ctx, elements, applied } = context();
  const before = plain(ctx.draft);
  const history = { sample_seed: 765, prompt_seed: 998, settings: { count: 1, width: 768, height: 1280, model_name: 'chosen.safetensors',
    random_character: false, fixed_character: 'old identity', pools: { character: { ids: ['old'] } }, loras: [{ filename: 'saved.safetensors', enabled: true, strength: .7 }],
    upscalers: { latent: { enabled: true, sampler: { steps: 29 } }, post_sampler: { enabled: false, steps: 18 } }, pipeline_version: 4 } };
  ctx.randomWorkbenchRestoreHistory(history);
  const restored = applied[0].settings;
  assert.equal(restored.count, 7); assert.equal(restored.width, 768); assert.equal(restored.height, 1280);
  assert.equal(restored.model_name, 'chosen.safetensors'); assert.equal(restored.loras[0].strength, .7);
  assert.equal(restored.random_character, true); assert.equal(restored.random_character_count, 2);
  assert.equal(restored.fixed_clothing, ''); assert.equal(restored.fixed_pose, 'standing');
  assert.deepEqual(restored.pools, before.pools); assert.deepEqual(plain(ctx.draft), before);
  assert.equal(elements.randomSeedMode.value, 'fixed'); assert.equal(elements.randomSampleSeed.value, 765);
  assert.equal(restored.upscalers.latent.sampler.steps, 29); assert.equal(restored.upscalers.post_sampler.steps, 18);
  assert.equal(restored.workbench, 'random'); assert.equal(restored.model_profile, 'anima');
  assert.deepEqual(plain(ctx.randomWorkbenchSeeds()), { sample_seed: 765 });
});

test('random seeds omit prompt seeds, allow fresh draws and reject unsafe integer values', () => {
  const { ctx, elements } = context();
  assert.deepEqual(plain(ctx.randomWorkbenchSeeds()), { sample_seed: 42 });
  elements.randomSeedMode.value = 'random'; assert.equal(ctx.randomWorkbenchSeeds(), null);
  elements.randomSeedMode.value = 'fixed';
  for (const invalid of ['-1', '1.5', String(2 ** 50 + 1), 'NaN']) { elements.randomSampleSeed.value = invalid; assert.throws(() => ctx.randomWorkbenchSeeds(), /有效/); }
  elements.randomSampleSeed.value = String(2 ** 50); assert.equal(ctx.randomWorkbenchSeeds().sample_seed, 2 ** 50);
});

test('new project ignores old browser drafts and persists its seeds, inactive fixed text and both sampling modes', () => {
  const { ctx, storage, writes, elements } = context();
  storage.set('anima-random-studio:draft:v4', JSON.stringify({ settings: { model_name: 'old-project' } }));
  storage.set('anima-studio:workbench-drafts:v1', JSON.stringify({ drafts: { random: { anima: { settings: { count: 99 } } } } }));
  load(ctx, 'persistNow'); load(ctx, 'loadStoredDraft');
  assert.equal(ctx.loadStoredDraft(), false);
  ctx.persistNow();
  assert.deepEqual(writes, ['anima-random-webui:draft:v1', 'anima-random-webui:pool-view:v1']);
  const saved = JSON.parse(storage.get(ctx.DRAFT_KEY));
  assert.equal(saved.version, 1); assert.equal(saved.seed, '42');
  assert.deepEqual(saved.samplingModes, { normal: { steps: 23 }, dual_sampler: { steps: 39 } });
  elements.randomSampleSeed.value = '0'; ctx.profileDrafts = {};
  assert.equal(ctx.loadStoredDraft(), true); assert.equal(elements.randomSampleSeed.value, '42');
  assert.equal(ctx.profileDrafts.anima.fixed.clothing, 'hidden coat');
  assert.equal(storage.has('anima-random-studio:draft:v4'), true);
});

test('preset application retains current pool/count and independent sampling mode drafts', () => {
  const { ctx, applied } = context();
  ctx.settings.pools = plain(ctx.draft.pools);
  const before = plain(ctx.samplingModes);
  ctx.randomWorkbenchApplyPreset({ settings: { model_name: 'style.safetensors', steps: 31, upscalers: { latent: { sampler: { steps: 22 } }, post_sampler: { steps: 19 } } } });
  assert.equal(applied[0].settings.count, 7); assert.equal(applied[0].settings.model_name, 'style.safetensors');
  assert.deepEqual(applied[0].settings.pools, plain(ctx.draft.pools));
  assert.deepEqual(plain(ctx.samplingModes), before);
});
