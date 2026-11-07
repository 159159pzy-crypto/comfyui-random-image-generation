const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
function fn(name) { let start = source.indexOf(`function ${name}(`); if (source.slice(start - 6, start) === 'async ') start -= 6; return source.slice(start, source.indexOf('\n}', start) + 2); }
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function setup() {
  const elements = {}, calls = [];
  const field = id => elements[id] ||= { value: '', textContent: '', hidden: false, checked: false, focus() {} };
  const ui = Object.fromEntries(['promptRuleFrom', 'promptRuleTo', 'promptRuleId', 'promptRulePositive', 'promptRuleNegative', 'promptRuleLora', 'deletePromptRule', 'promptRuleError', 'undoPromptNormalization', 'promptNormalizationStatus'].map(id => [id, field(id)]));
  ui.promptRulePositive.checked = true;
  const ctx = vm.createContext({ ui, document: { getElementById: field }, currentProfile: () => ctx.profile, profile: 'anima', promptRulesProfile: 'anima', promptRulesRequest: 0, promptRules: [], editingPromptRule: null, promptRuleEditorRevision: 0,
    renderPromptRules() {}, request: async (url, options) => { calls.push({ url, options }); return { items: [] }; }, toast() {},
    normalizationUndo: null, singleWorkbenchState: { ready: true, mode: 'single' }, invalidatePromptNormalization() {}, singleWorkbenchInvalidatePrompt() {}, schedulePersist() {},
    promptElementForField: id => field(id), applyPromptField: (id, value) => { field(id).value = value; },
    PROMPT_FIELD_LABELS: {},
  });
  for (const name of ['loadPromptRules', 'clearPromptRuleEditor', 'editPromptRule', 'setPromptRuleError', 'promptRuleScopes', 'savePromptRule', 'deletePromptRule', 'setNormalizationUndo', 'clearNormalizationUndo', 'undoPromptNormalization']) vm.runInContext(fn(name), ctx);
  return { ctx, elements, field, calls };
}
test('rule reads use Anima and discard an older concurrent response', async () => {
  const { ctx, elements } = setup(), requests = [];
  ctx.request = (url, options) => { const gate = deferred(); requests.push({ url, gate }); return gate.promise; };
  const old = ctx.loadPromptRules();
  const fresh = ctx.loadPromptRules();
  assert.match(requests[1].url, /model_profile=anima/);
  requests[1].gate.resolve({ items: [{ id: 'format-separators' }] }); await fresh;
  requests[0].gate.resolve({ items: [{ id: 'lowercase-tags' }] }); await old;
  assert.equal(ctx.promptRules[0].id, 'format-separators');
  assert.equal(ctx.promptRulesProfile, 'anima');
  assert.match(elements.promptRuleTitle.textContent, /Anima/);
  assert.match(elements.promptRuleHint.textContent, /区分大小写/);
});
test('rule create, update and delete all carry the captured model', async () => {
  const { ctx, calls } = setup();
  ctx.profile = ctx.promptRulesProfile = 'anima';
  ctx.ui.promptRuleFrom.value = 'Line one,\nLine two';
  ctx.ui.promptRuleTo.value = 'Replacement';
  await ctx.savePromptRule({ preventDefault() {} });
  assert.equal(JSON.parse(calls[0].options.body).model_profile, 'anima');
  assert.equal(JSON.parse(calls[0].options.body).from, 'Line one,\nLine two');
  ctx.editPromptRule({ id: 'rule', kind: 'custom', from: 'old', to: 'new', scopes: ['positive'] });
  await ctx.savePromptRule({ preventDefault() {} });
  assert.equal(calls.find(item => item.options?.method === 'PUT').url, '/api/prompt-rules/rule');
  assert.equal(JSON.parse(calls.find(item => item.options?.method === 'PUT').options.body).model_profile, 'anima');
  ctx.editPromptRule({ id: 'rule', kind: 'custom', from: 'old', to: 'new', scopes: ['positive'] });
  await ctx.deletePromptRule();
  assert.equal(calls.find(item => item.options?.method === 'DELETE').url, '/api/prompt-rules/rule?model_profile=anima');
});
test('a completed save cannot clear edits made after its request or another model editor', async () => {
  for (const edit of ['new input', 'profile']) {
    const { ctx } = setup(), gate = deferred();
    ctx.ui.promptRuleFrom.value = 'original'; ctx.ui.promptRuleTo.value = 'replacement';
    ctx.request = () => gate.promise;
    const pending = ctx.savePromptRule({ preventDefault() {} });
    ctx.ui.promptRuleFrom.value = 'new work'; ctx.promptRuleEditorRevision++;
    if (edit === 'profile') ctx.profile = 'anima';
    gate.resolve({});
    await pending;
    assert.equal(ctx.ui.promptRuleFrom.value, 'new work');
  }
});

test('random normalization undo restores captured input but preserves a subsequent edit', () => {
  const { ctx, field } = setup();
  const target = field('extra_prompt'); target.value = 'normalized';
  ctx.setNormalizationUndo([{ field: 'extra_prompt', before: 'original', after: 'normalized' }], { extra_prompt: target });
  ctx.undoPromptNormalization(); assert.equal(target.value, 'original');
  target.value = 'normalized';
  ctx.setNormalizationUndo([{ field: 'extra_prompt', before: 'original', after: 'normalized' }], { extra_prompt: target });
  target.value = 'new edit'; ctx.undoPromptNormalization(); assert.equal(target.value, 'new edit');
});
