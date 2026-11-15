const test = require('node:test');
const assert = require('node:assert/strict');
const { editorDraft, editorPayload, normalizedInclude, filterItems, cropRectangle, saveSession } = require('../static/style-preset-manager.js');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const session = (mode = 'create', item) => ({ mode, profile: 'anima', draft: editorDraft(mode, item), snapshot: { model_profile: 'anima', steps: 28, single: { positive_prompt: 'current body' } }, crop: { x: 0, y: 0, width: 1, height: 1 }, busy: false });

test('new editor always starts with every optional group off; overwrite uses saved flags', () => {
  const item = { include: { size: true, repair: true } };
  assert.deepEqual(editorDraft('create', item).include, { size: false, repair: false });
  assert.deepEqual(editorDraft('overwrite', item).include, item.include);
  assert.deepEqual(normalizedInclude({ size: 'true', repair: true }), { size: false, repair: true });
});

test('metadata edit never includes settings or optional groups and normalizes tags without changing case', () => {
  const edit = session('edit', { name: '  Film  ', tags: ['Soft', 'soft', '人像'], groups: ['g1'], include: { size: true }, cover_id: 'cover' });
  assert.deepEqual(editorPayload(edit), { model_profile: 'anima', name: 'Film', favorite: false, groups: ['g1'], tags: ['Soft', '人像'], cover_id: 'cover' });
  const overwrite = session('overwrite', { name: 'Style', include: { size: true } });
  overwrite.snapshot.resource_replacements = [{ from: 'old', to: 'new' }];
  const payload = editorPayload(overwrite); overwrite.snapshot.steps = 99;
  assert.equal(payload.settings.steps, 28); assert.equal(payload.include.size, true);
  assert.equal(payload.settings.resource_replacements, undefined); assert.equal(overwrite.snapshot.resource_replacements.length, 1);
});

test('search combines words, multi-tag AND, favorite group, resources and LoRA', () => {
  const items = [
    { id: 'a', name: '柔光', favorite: true, groups: ['g1', 'g2'], tags: ['Soft', 'Portrait'], updated_at: '2026-11-11', settings: { model_name: 'Anima', loras: [{ filename: 'cinema.safetensors' }] } },
    { id: 'b', name: '胶片', favorite: false, groups: ['g1'], tags: ['Soft'], updated_at: '2026-11-12', settings: { loras: [{ filename: 'cinema.safetensors' }], vae_name: 'clear.vae' } },
    { id: 'c', name: '收藏', favorite: true, groups: [], tags: ['Portrait'], updated_at: '2026-11-10', settings: {} },
  ];
  const groups = [{ id: 'g1', name: '人像分组' }, { id: 'g2', name: '电影' }];
  assert.deepEqual(filterItems(items, { query: 'cinema 人像分组', collection: 'group:g1', tags: ['soft', 'portrait'] }, groups).map(x => x.id), ['a']);
  assert.deepEqual(filterItems(items, { query: 'clear.vae', collection: 'all', tags: [] }, groups).map(x => x.id), ['b']);
  assert.deepEqual(filterItems(items, { collection: 'ungrouped' }, groups).map(x => x.id), ['c']);
  assert.deepEqual(filterItems(items, { sort: 'updated' }, groups).map(x => x.id), ['b', 'a', 'c']);
});

test('every library sort ignores favorite changes and homepage response order', () => {
  const items = [
    { id: 'a', name: 'Style 2', favorite: false, created_at: '2026-11-10', updated_at: '2026-11-12' },
    { id: 'b', name: 'Style 3', favorite: true, created_at: '2026-11-12', updated_at: '2026-11-11' },
    { id: 'c', name: 'Style 1', favorite: false, created_at: '2026-11-11', updated_at: '2026-11-10' },
  ];
  for (const [sort, expected] of [[undefined, ['a', 'b', 'c']], ['updated', ['a', 'b', 'c']], ['created', ['b', 'c', 'a']], ['name', ['c', 'a', 'b']]]) {
    assert.deepEqual(filterItems(items, { sort }).map(item => item.id), expected);
    const saved = [...items].reverse().map(item => ({ ...item, favorite: !item.favorite, favorite_at: item.favorite ? null : '2026-11-15' }));
    assert.deepEqual(filterItems(saved, { sort }).map(item => item.id), expected);
  }
  assert.deepEqual(items.map(item => item.id), ['a', 'b', 'c'], 'sorting must not mutate the supplied records');
});

test('equal library sort values use IDs rather than the incoming favorite order', () => {
  const items = ['c', 'a', 'b'].map(id => ({ id, name: 'Style', favorite: id === 'c', created_at: '2026-11-10', updated_at: '2026-11-10' }));
  for (const sort of ['updated', 'created', 'name']) {
    for (const records of [items, [...items].reverse()]) {
      assert.deepEqual(filterItems(records, { sort }).map(item => item.id), ['a', 'b', 'c']);
    }
  }
  const numericNames = [{ ...items[0], id: 'b', name: 'Style 01' }, { ...items[0], id: 'a', name: 'Style 1' }];
  assert.deepEqual(filterItems(numericNames, { sort: 'name' }).map(item => item.id), ['a', 'b']);
});

test('crop keeps a 4:3 pixel rectangle inside portrait and landscape images at any pan/zoom', () => {
  for (const [width, height] of [[4000, 1000], [600, 1800], [800, 600]]) {
    for (const zoom of [1, 1.8, 4]) for (const [cx, cy] of [[.5, .5], [-20, 80], [80, -20]]) {
      const crop = cropRectangle(width, height, zoom, cx, cy);
      assert.ok(crop.x >= 0 && crop.y >= 0 && crop.x + crop.width <= 1.000001 && crop.y + crop.height <= 1.000001);
      assert.ok(Math.abs(crop.width * width / (crop.height * height) - 4 / 3) < .000001);
    }
  }
});

test('ungrouped includes every preset without groups regardless of favorite status', () => {
  const items = [
    { id: 'starred', name: '收藏未分组', favorite: true, groups: [] },
    { id: 'plain', name: '未收藏未分组', favorite: false, groups: [] },
    { id: 'legacy', name: '未带分组字段', favorite: false },
    { id: 'grouped', name: '已有分组', favorite: false, groups: ['g1'] },
  ];
  const ungrouped = filterItems(items, { collection: 'ungrouped' });
  assert.deepEqual(ungrouped.map(item => item.id).sort(), ['legacy', 'plain', 'starred']);
  assert.equal(ungrouped.length, 3, 'the navigation count uses the same filter as the result list');
  assert.deepEqual(filterItems(items, { collection: 'favorite' }).map(item => item.id), ['starred']);
});

test('pending cover completion after closing or switching profile cannot start a preset write', async () => {
  for (const replacement of [null, session('create')]) {
    const original = session(); original.file = { name: 'cover.png' }; let active = original; const upload = deferred(); let writes = 0, successes = 0;
    const task = saveSession(original, { current: candidate => active === candidate, upload: () => upload.promise, write: async () => { writes++; }, success: () => successes++, failure: () => assert.fail('stale error'), committed: async () => assert.fail('not committed') });
    active = replacement; upload.resolve({ id: 'staged-cover' });
    assert.deepEqual(await task, { abandoned: true }); assert.equal(writes, 0); assert.equal(successes, 0); assert.equal(original.busy, false);
  }
});

test('a committed save survives close/reopen without clearing or updating the new editor', async () => {
  const original = session(); original.draft.name = 'Original'; const next = session(); next.draft.name = 'New draft';
  let active = original, received; const write = deferred(), commits = [], successes = [];
  const task = saveSession(original, { current: candidate => active === candidate, write: payload => { received = payload; return write.promise; }, success: value => successes.push(value), committed: async value => commits.push(value), failure: () => assert.fail('unexpected') });
  original.snapshot.steps = 99; original.draft.name = 'Later edit'; active = next;
  write.resolve({ id: 'saved', name: 'Original' }); const result = await task;
  assert.equal(result.committed, true); assert.equal(received.settings.steps, 28); assert.equal(received.name, 'Original');
  assert.deepEqual(successes, []); assert.equal(commits.length, 1); assert.equal(next.draft.name, 'New draft'); assert.equal(next.busy, false);
});

test('double submit sends one write and current failure keeps the draft retryable', async () => {
  const editor = session(); editor.draft.name = 'Keep me'; const write = deferred(); let writes = 0; const failures = [];
  const hooks = { current: () => true, write: () => { writes++; return write.promise; }, success: () => {}, committed: async () => {}, failure: error => failures.push(error.message) };
  const first = saveSession(editor, hooks); assert.deepEqual(await saveSession(editor, hooks), { ignored: true });
  write.reject(new Error('disk full')); await first;
  assert.equal(writes, 1); assert.deepEqual(failures, ['disk full']); assert.equal(editor.draft.name, 'Keep me'); assert.equal(editor.busy, false);
});

test('old save failure after profile change only notifies the detached operation', async () => {
  const editor = session(); let active = true; const write = deferred(), notifications = [];
  const task = saveSession(editor, { current: () => active, write: () => write.promise, success: () => assert.fail('not saved'), failure: () => assert.fail('old failure touched UI'), committed: async () => {}, detachedFailure: error => notifications.push(error.message) });
  active = false; write.reject(new Error('old profile save failed')); await task;
  assert.deepEqual(notifications, ['old profile save failed']);
});
