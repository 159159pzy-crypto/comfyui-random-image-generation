const test = require('node:test');
const assert = require('node:assert/strict');
const { createController, groupRows, groupPath, descendants, bindingPayload, mergeDetailDraft, detailDirtyFields, metadataPatch, submitLibraryLoras, cardNavigationIndex, queryString } = require('../static/lora-manager.js');
const { configuredRemoval } = require('../static/lora-manager.js');

function harness() {
  let profile = 'anima', loras = [];
  const pending = [], changes = [];
  const controller = createController({
    request(url, options = {}) { return new Promise((resolve, reject) => pending.push({ url, options, resolve, reject })); },
    getProfile: () => profile, getLoras: () => loras,
    onChange: state => changes.push({ loading: state.loading, ids: state.items.map(item => item.id), selected: [...state.selected] }),
  });
  return { controller, pending, changes, profile(value) { profile = value; }, loras(value) { loras = value; } };
}
const page = (ids, extra = {}) => ({ items: ids.map(id => ({ id, filename: `${id}.safetensors` })), total: ids.length, page: 1, total_pages: 1, groups: [], facets: {}, ...extra });

test('a late search response cannot replace a more recent result, even if transport ignores abort', async () => {
  const h = harness();
  const first = h.controller.update({ q: 'alice' });
  const second = h.controller.update({ q: 'bob' });
  assert.equal(h.pending[0].options.signal.aborted, true);
  h.pending[1].resolve(page(['bob'])); await second;
  h.pending[0].resolve(page(['alice'])); await first;
  assert.deepEqual(h.controller.state.items.map(item => item.id), ['bob']);
  assert.equal(h.controller.state.loading, false);
});

test('typed search is committed before debounce so repeated expand redraws cannot restore the previous query', async () => {
  const h = harness();
  const first = h.controller.load(); h.pending[0].resolve(page(['old'])); await first;
  h.controller.stage({ q: 'denia' });
  // Expand/collapse synchronizes the input from controller state immediately,
  // while the deferred network read is still pending.
  let input = 'denia';
  for (let i = 0; i < 4; i++) input = h.controller.state.q;
  assert.equal(input, 'denia');
  assert.notEqual(h.controller.state.resultKey, JSON.stringify(h.controller.query()), 'previous results must stay noninteractive');
  const searched = h.controller.load();
  assert.equal(new URL(h.pending[1].url, 'http://localhost').searchParams.get('q'), 'denia');
  h.pending[1].resolve(page(['denia'])); await searched;
  assert.equal(h.controller.state.resultKey, JSON.stringify(h.controller.query()));
  assert.deepEqual(h.controller.state.items.map(item => item.id), ['denia']);
});

test('changing the host model discards in-flight inventory and selections from the old model', async () => {
  const h = harness();
  h.controller.toggle('anima-only');
  const old = h.controller.load();
  h.profile('krea2');
  const fresh = h.controller.load();
  h.pending[1].resolve(page(['krea'])); await fresh;
  h.pending[0].resolve(page(['anima'])); await old;
  assert.deepEqual([...h.controller.state.selected], []);
  assert.deepEqual(h.controller.state.items.map(item => item.id), ['krea']);
  assert.equal(h.controller.state.known.has('anima'), false);
});

test('select all captures the full query and includes off-page ids without altering previous selections', async () => {
  const h = harness();
  h.controller.state.page = 3;
  h.controller.state.q = 'style';
  h.controller.state.tag = ['painting', 'warm'];
  h.controller.state.tag_exclude = ['watermark'];
  h.controller.state.collection = 'group-a';
  h.controller.toggle('previous');
  const selection = h.controller.selectAll();
  const body = JSON.parse(h.pending[0].options.body);
  assert.deepEqual(body.query.tag, ['painting', 'warm']);
  assert.deepEqual(body.query.tag_exclude, ['watermark']);
  assert.equal(body.query.collection, 'group-a');
  assert.equal(body.query.page, undefined);
  assert.equal(body.query.limit, undefined);
  h.pending[0].resolve({ ids: ['visible', 'off-page', 'previous'], revision: 9 }); await selection;
  assert.deepEqual([...h.controller.state.selected], ['previous', 'visible', 'off-page']);
  assert.equal(h.controller.state.selectionRevision, 9);
});

test('changing filters during select all rejects that operation instead of selecting unrelated resources', async () => {
  const h = harness();
  h.controller.toggle('kept');
  const selection = h.controller.selectAll();
  const load = h.controller.update({ folder: 'characters' });
  h.pending[0].resolve({ ids: ['old-query'] }); await selection;
  h.pending[1].resolve(page(['new-query'])); await load;
  assert.deepEqual([...h.controller.state.selected], ['kept']);
  assert.equal(h.controller.state.selectionLoading, false);
});

test('selection failures do not erase chosen items and leave retry available', async () => {
  const h = harness(); h.controller.toggle('kept');
  const selection = h.controller.selectAll();
  h.pending[0].reject(new Error('offline'));
  await assert.rejects(selection, /offline/);
  assert.deepEqual([...h.controller.state.selected], ['kept']);
  assert.equal(h.controller.state.selectionLoading, false);
});

test('pagination stays bounded at sixty items and preserves selection across pages', async () => {
  const h = harness();
  let loading = h.controller.load();
  assert.equal(new URL(h.pending[0].url, 'http://localhost').searchParams.get('limit'), '60');
  h.pending[0].resolve(page(['one', 'two'], { total: 122, total_pages: 3 })); await loading;
  h.controller.selectPage();
  loading = h.controller.update({ page: 2 });
  h.pending[1].resolve(page(['three'], { total: 122, total_pages: 3, page: 2 })); await loading;
  h.controller.toggle('three');
  assert.deepEqual([...h.controller.state.selected], ['one', 'two', 'three']);
  assert.equal(h.controller.state.page, 2);
  assert.equal(h.controller.state.pages, 3);
});

test('current configuration filtering uses host filenames in both page and all-result queries', async () => {
  const h = harness();
  h.loras([{ filename: 'people/alice.safetensors' }, { filename: 'styles/ink.safetensors' }]);
  const loading = h.controller.update({ in_configured: 'false' });
  const url = new URL(h.pending[0].url, 'http://localhost');
  assert.deepEqual(url.searchParams.getAll('configured_filenames'), ['people/alice.safetensors', 'styles/ink.safetensors']);
  assert.equal(url.searchParams.get('in_configured'), 'false');
  assert.equal(url.searchParams.has('configured'), false);
  h.pending[0].resolve(page(['new'])); await loading;
  const selecting = h.controller.selectAll();
  const query = JSON.parse(h.pending[1].options.body).query;
  assert.deepEqual(query.configured_filenames, ['people/alice.safetensors', 'styles/ink.safetensors']);
  h.pending[1].resolve({ ids: ['new'] }); await selecting;
});

test('a configuration change while selecting all invalidates the captured query', async () => {
  const h = harness();
  h.controller.state.in_configured = 'true'; h.loras([{ filename: 'before' }]);
  const selecting = h.controller.selectAll();
  h.loras([{ filename: 'after' }]);
  h.pending[0].resolve({ ids: ['before'] }); await selecting;
  assert.equal(h.controller.state.selected.size, 0);
});

test('all-results removal uses the frozen filenames for unvisited pages and never matches a basename', async () => {
  const h = harness();
  h.loras([{filename:'first/shared.safetensors'}, {filename:'second/shared.safetensors'}, {filename:'other/shared.safetensors'}]);
  h.controller.state.known.set('first',{id:'first',filename:'first/shared.safetensors'});
  const selecting=h.controller.selectAll();
  h.pending[0].resolve({ids:['first','second'],resources:[{id:'first',filename:'first/shared.safetensors'},{id:'second',filename:'second/shared.safetensors'}],revision:4});
  await selecting;
  h.controller.markOffline('fixture offline');
  assert.deepEqual(configuredRemoval([...h.controller.state.selected],h.controller.state.known,h.controller.query().configured_filenames || [{filename:'first/shared.safetensors'},{filename:'second/shared.safetensors'},{filename:'other/shared.safetensors'}],h.controller.state.selectionResources),['first/shared.safetensors','second/shared.safetensors']);
  assert.equal(h.controller.state.known.has('second'),false,'selection references must not masquerade as full model metadata');
});

test('stale all-results responses cannot leak filename mappings across profiles', async () => {
  const h=harness();const selecting=h.controller.selectAll();h.profile('krea2');h.controller.profileChanged();
  h.pending[0].resolve({ids:['old'],resources:[{id:'old',filename:'old.safetensors'}]});await selecting;
  assert.equal(h.controller.state.selectionResources.size,0);
});

test('failed refresh retains a successful same-query cache explicitly read-only', async () => {
  const h = harness();
  let loading = h.controller.load(); h.pending[0].resolve(page(['old'])); await loading;
  loading = h.controller.load(); h.pending[1].reject(new Error('plugin offline')); await loading;
  assert.deepEqual(h.controller.state.items.map(item=>item.id), ['old']);
  assert.equal(h.controller.state.error, 'plugin offline');
  assert.equal(h.controller.state.loading, false);
  assert.equal(h.controller.state.offline, true);
  assert.equal(h.controller.state.cached, true);
  assert.equal(h.controller.state.writable, false);
  assert.equal(h.controller.state.resultKey, JSON.stringify(h.controller.query()));
});

test('offline fallback cannot present another query result as if it matched the requested filters', async () => {
  const h=harness();let loading=h.controller.load();h.pending[0].resolve(page(['anima']));await loading;
  loading=h.controller.update({q:'unseen'});h.pending[1].reject(new Error('offline'));await loading;
  assert.deepEqual(h.controller.state.items,[]);assert.equal(h.controller.state.cached,false);
  loading=h.controller.update({q:''});h.pending[2].reject(new Error('offline'));await loading;
  assert.deepEqual(h.controller.state.items.map(item=>item.id),['anima']);assert.equal(h.controller.state.cached,true);
});

test('server-side offline snapshots remain browseable but never imply writable or fresh state, then recover on refresh', async () => {
  const h=harness();let loading=h.controller.load();
  h.pending[0].resolve(page(['cached'],{offline:true,cached:true,writable:false,error:'connection refused',last_synced_at:'2026-11-07T00:00:00Z'}));
  assert.equal(await loading,false);assert.equal(h.controller.state.writable,false);assert.equal(h.controller.state.cached,true);
  loading=h.controller.load({refresh:true});h.pending[1].resolve(page(['fresh'],{offline:false,cached:false,writable:true,error:null}));
  assert.equal(await loading,true);assert.equal(h.controller.state.offline,false);assert.equal(h.controller.state.cached,false);assert.equal(h.controller.state.writable,true);assert.equal(h.controller.state.error,'');
  assert.equal(h.controller.state.known.has('cached'),false);
});

test('a successful item edit updates the local offline cache rather than restoring superseded metadata', async () => {
  const h=harness();let loading=h.controller.load();h.pending[0].resolve(page(['one']));await loading;
  h.controller.replace({id:'one',filename:'one.safetensors',tags:['saved']});
  loading=h.controller.load();h.pending[1].reject(new Error('offline'));await loading;
  assert.deepEqual(h.controller.state.items[0].tags,['saved']);
});

test('gallery arrow navigation follows actual columns and clamps at page boundaries', () => {
  assert.equal(cardNavigationIndex(0,60,5,'ArrowRight'),1);
  assert.equal(cardNavigationIndex(6,60,5,'ArrowDown'),11);
  assert.equal(cardNavigationIndex(6,60,5,'ArrowUp'),1);
  assert.equal(cardNavigationIndex(0,60,5,'ArrowLeft'),0);
  assert.equal(cardNavigationIndex(59,60,5,'ArrowDown'),59);
  assert.equal(cardNavigationIndex(13,17,2,'ArrowDown'),15);
  assert.equal(cardNavigationIndex(3,17,1,'ArrowDown'),4);
  assert.equal(cardNavigationIndex(6,60,5,'Home'),0);
  assert.equal(cardNavigationIndex(6,60,5,'End'),59);
  assert.equal(cardNavigationIndex(6,60,5,'Enter'),6);
});

test('closing cancels all pending reads and their results cannot change the reopened library', async () => {
  const h = harness(); const old = h.controller.load(); const selected = h.controller.selectAll();
  h.controller.cancel();
  const reopened = h.controller.load(); h.pending[2].resolve(page(['fresh'])); await reopened;
  h.pending[0].resolve(page(['stale'])); h.pending[1].resolve({ ids: ['stale'] }); await Promise.all([old, selected]);
  assert.deepEqual(h.controller.state.items.map(item => item.id), ['fresh']);
  assert.equal(h.controller.state.selected.size, 0);
});

test('group navigation handles nested groups, collapse and malformed cycles without recursion loops', () => {
  const groups = [{ id: 'root', name: '收藏', parentId: null }, { id: 'child', name: '人物', parentId: 'root' },
    { id: 'leaf', name: 'Alice', parentId: 'child' }, { id: 'other', name: '画风', parentId: null }];
  assert.deepEqual(groupRows(groups).map(row => [row.id, row.depth]), [['root', 0], ['child', 1], ['leaf', 2], ['other', 0]]);
  assert.deepEqual(groupRows(groups, new Set(['child'])).map(row => row.id), ['root', 'child', 'other']);
  assert.equal(groupPath(groups[2], groups), '收藏 / 人物 / Alice');
  assert.deepEqual([...descendants(groups, 'child')], ['child', 'leaf']);
  const cycle = [{ id: 'a', name: 'A', parentId: 'b' }, { id: 'b', name: 'B', parentId: 'a' }];
  assert.equal(groupPath(cycle[0], cycle), 'B / A');
  assert.deepEqual([...descendants(cycle, 'a')], ['a', 'b']);
});

test('tag queries encode repeated values rather than joining tags ambiguously', () => {
  const params = new URLSearchParams(queryString({ tag: ['a,b', '风格'], tag_exclude: ['x y'], recursive: false, q: '' }));
  assert.deepEqual(params.getAll('tag'), ['a,b', '风格']);
  assert.deepEqual(params.getAll('tag_exclude'), ['x y']);
  assert.equal(params.get('recursive'), 'false');
  assert.equal(params.has('q'), false);
});

test('root-folder exact browsing preserves the empty folder parameter', () => {
  const params = new URLSearchParams(queryString({ root: 'F:/models/loras', folder: '', recursive: false }));
  assert.equal(params.has('folder'), true);
  assert.equal(params.get('folder'), '');
  assert.equal(new URLSearchParams(queryString({ root: '', folder: '', recursive: false })).has('folder'), false);
});

test('saving bindings only sends trusted editable fields and never invents a zero weight from an empty input', () => {
  assert.deepEqual(bindingPayload([{ id: 'one', filename: 'server-path', sha256: 'server-hash', trigger_words: ['server-word'], status: 'recognized', strength: '0.75', enabled: false }]), [{ id: 'one', strength: .75, enabled: false }]);
  for (const strength of ['', null, 'NaN', Infinity, -101, 101]) assert.throws(() => bindingPayload([{ id: 'one', strength }]), /绑定权重/);
  assert.deepEqual(bindingPayload([{ id: 'one', strength: 0 }]), [{ id: 'one', strength: 0, enabled: true }]);
  assert.deepEqual(bindingPayload([{ id: 'one', strength: 1, status: 'changed', sha256: 'old', rebind: true }]), [{ id: 'one', strength: 1, enabled: true, rebind: true }]);
  assert.equal('rebind' in bindingPayload([{ id: 'one', strength: 1, status: 'changed' }])[0], false);
});

test('saved detail values never cover later batch edits when the detail is reopened', () => {
  const before = { tags: ['角色绑定验收', 'ui-verified'], notes: 'note', default_strength: .8, groupIds: ['group'], trigger_words: ['alice'] };
  const clean = mergeDetailDraft(before);
  assert.equal(Object.values(detailDirtyFields(before, clean)).some(Boolean), false);
  const after = { ...before, tags: [...before.tags, 'batch-ui-verified'] };
  assert.equal(mergeDetailDraft(after).tags, '角色绑定验收, ui-verified, batch-ui-verified');
  // A genuine unsaved note is preserved, while untouched tags still follow the server.
  const edited = { ...clean, notes: 'unfinished note' };
  edited.dirty = detailDirtyFields(before, edited);
  const restored = mergeDetailDraft(after, edited);
  assert.equal(restored.notes, 'unfinished note');
  assert.equal(restored.tags, '角色绑定验收, ui-verified, batch-ui-verified');
  assert.deepEqual(metadataPatch(after, restored), { notes: 'unfinished note' });
});

test('saving one metadata field does not overwrite stale untouched fields or resurrect dormant favorite groups', () => {
  const item = { tags: ['old'], notes: '', default_strength: 1, groupIds: ['remembered'], favorite: false, trigger_words: [] };
  const edit = { ...mergeDetailDraft(item), notes: 'new note' };
  assert.deepEqual(metadataPatch(item, edit), { notes: 'new note' });
  assert.deepEqual(metadataPatch(item, mergeDetailDraft(item)), {});
});

test('manager additions reach the real host adapter with default weight, classification and trigger metadata intact', async () => {
  const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
  const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
  const start = source.indexOf('function addManagedLoras(');
  const inventory = new Map(), received = [];
  const context = vm.createContext({ draft: { loras: [] }, currentProfile: () => 'anima',
    loraItem: filename => inventory.get(filename), replaceLoraInventoryItem: item => inventory.set(item.filename,item),
    normalizedPath: value => value.toLowerCase().replaceAll('\\','/'), renderLoras() {}, schedulePersist() {}, reconcileLoraTriggers() {},
  });
  vm.runInContext(source.slice(start, source.indexOf('\n}',start)+2),context);
  const item = {id:'new',filename:'newly-scanned.safetensors',model_profile:'anima',classification_status:'recognized',default_strength:.35,trigger_words:['alice'],available:true};
  const result = await submitLibraryLoras([item], values => { received.push(...values); return context.addManagedLoras(values); });
  assert.equal(result.added.length,1);
  assert.equal(result.added[0].strength,.35);
  assert.equal(received[0].model_profile,'anima');
  assert.equal(received[0].classification_status,'recognized');
  assert.deepEqual(received[0].trigger_words,['alice']);
  assert.notEqual(received[0],item,'host mutations must not modify library cache objects');
});
