const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const browserSource = fs.readFileSync(path.join(__dirname, '../static/pool-browser.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve; const promise = new Promise(done => resolve = done); return {promise, resolve}; };

function harness({ items = ['a', 'b', 'c'], profile = 'anima', request } = {}) {
  const calls = [], messages = []; let saves = 0, summaries = 0;
  const view = {query: '', sort: '', language: 'zh', selectedOnly: false, filters: {collection: '', customGroup: '', categories: [], traits: [], gender: '', hair: '', eye: '', series: ''}};
  const original = {character: {mode: 'all', ids: [], excluded_ids: ['c']}, pose: {mode: 'include', ids: ['unrelated'], excluded_ids: []}};
  const ui = new Proxy({}, {get: (object, name) => object[name] ||= {disabled: false, hidden: false, textContent: '', value: ''}});
  const ctx = vm.createContext({ui, profile, currentProfile: () => ctx.profile, clone: plain,
    currentView: () => ctx.PoolBrowser.active?.view || view,
    draft: {pools: plain(original)}, activeSection: 'character', poolPage: 1, poolPages: 1, poolItems: [], poolFacets: {},
    poolRequest: 0, poolContextRevision: 0, poolSelectionOperation: null, poolResultContext: '', poolQueryCount: null,
    poolSelectionStats: new Map([['character', {selectionKey: JSON.stringify(original.character), selectedTotal: 2}]]),
    config: {catalog: {counts: {character: 3}}}, poseItemCache: new Map(), favoritesAvailable: true,
    clearTimeout() {}, requestAnimationFrame: fn => fn(),
    renderPoolItems: () => ctx.updatePoolSelectionControls(), renderPoolSidebar() {},
    renderDimensions: () => summaries++, updatePoolOverview: () => summaries++, updatePoseConflict() {},
    schedulePersist: () => saves++, toast: text => messages.push(text),
    request: async (url, init) => {
      const body = JSON.parse(init.body); calls.push({url, body});
      if (request) { const value = request(url, body); if (value !== undefined) return value; }
      let matches = items.filter(id => !body.q || id.includes(body.q));
      if (body.selection) matches = matches.filter(id => body.selection.ids.includes(id));
      const stats = {total: matches.length, pool_total: items.length, selected_total: items.filter(id => body.selection_state.ids.includes(id)).length};
      if (url.endsWith('/ids')) return {...stats, ids: matches};
      const pages = Math.max(1, Math.ceil(matches.length / body.limit)), page = Math.min(body.page, pages);
      return {...stats, page, pages, items: matches.slice((page - 1) * body.limit, page * body.limit).map(id => ({id, title: `Person ${id}`})), facets: {series: []}};
    },
  });
  vm.runInContext(browserSource, ctx);
  for (const name of ['characterPickerSession', 'poolBrowserProfile', 'poolBrowserSelection', 'poolBrowserCount', 'persistPoolView',
    'selectedCount', 'selectionHas', 'poolQueryBody', 'invalidatePoolContext', 'poolSelectionContext', 'poolSelectionQuery',
    'updatePoolSelectionControls', 'poolSelectionChanged', 'selectFilteredPool', 'setPoolMoreSelection', 'useEntirePool',
    'clearPoolSelection', 'loadPool', 'toggleSelection', 'selectCurrentPage']) {
    const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
    assert.ok(start >= 0, name); vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), ctx);
  }
  const begin = options => { const session = ctx.PoolBrowser.createSession({...options, view}); ctx.PoolBrowser.activate(session); return session; };
  return {ctx, ui, calls, messages, begin, original, saves: () => saves, summaries: () => summaries};
}

test('shared query stays Anima in Krea host and all binding choices leave generation selection and counters intact', async () => {
  const h = harness({profile: 'krea2'}), session = h.begin({alreadyBoundIds: ['a']});
  await h.ctx.loadPool();
  assert.equal(h.calls[0].body.model_profile, 'anima');
  assert.equal(h.ctx.selectionHas('character', 'a'), true);
  await h.ctx.toggleSelection('character', 'a', false);
  await h.ctx.selectCurrentPage(true);
  assert.deepEqual(plain(session.selection.ids), ['b', 'c']);
  assert.match(h.ui.poolSelectionLabel.textContent, /已绑定 1 位.*待新增 2 位/);
  assert.equal(h.ctx.selectedCount('character'), 2, 'generation statistic is untouched');
  assert.deepEqual(plain(h.ctx.draft.pools), h.original);
  assert.equal(h.saves(), 0); assert.equal(h.summaries(), 0);
  assert.deepEqual(plain(session.result()).map(item => item.title), ['Person b', 'Person c']);
});

test('cross-page filtered selection, selected-only clearing and whole-pool guard never borrow the generation all-mode', async () => {
  const h = harness({items: Array.from({length: 100}, (_, index) => `person-${index}`)}), session = h.begin({alreadyBoundIds: ['person-0']});
  session.view.filters.collection = 'favorite-group'; session.view.filters.customGroup = 'custom-group';
  session.view.filters.gender = '1girl'; session.view.filters.series = 'series'; session.view.language = 'bilingual';
  await h.ctx.loadPool(); await h.ctx.selectFilteredPool();
  assert.equal(session.selection.ids.length, 99);
  const query = h.calls.find(call => call.url.endsWith('/ids')).body;
  assert.equal(query.collection, 'favorite-group'); assert.equal(query.custom_group, 'custom-group');
  assert.equal(query.gender, '1girl'); assert.equal(query.series, 'series');
  session.view.selectedOnly = true; await h.ctx.clearPoolSelection();
  assert.deepEqual(plain(session.selection), {mode: 'include', ids: [], excluded_ids: []});
  assert.equal(h.ctx.poolItems.length, 0);
  h.ctx.useEntirePool(); h.ctx.persistPoolView();
  assert.deepEqual(plain(h.ctx.draft.pools), h.original); assert.equal(h.saves(), 0);
});

test('late filtered selection cannot write into the draft or a replacement picker after cancel', async () => {
  const gate = deferred(), h = harness({request: url => url.endsWith('/ids') ? gate.promise : undefined});
  const first = h.begin(); const selecting = h.ctx.selectFilteredPool();
  h.ctx.invalidatePoolContext(); h.ctx.PoolBrowser.release(first);
  const next = h.begin({initialSelectedIds: ['b']});
  gate.resolve({ids: ['a', 'c']}); await selecting;
  assert.deepEqual(plain(next.selection.ids), ['b']); assert.deepEqual(plain(first.selection.ids), []);
  assert.deepEqual(plain(h.ctx.draft.pools), h.original); assert.equal(h.saves(), 0);
});

test('late page response is rejected after picker closes even when generation section and filters are identical', async () => {
  const gate = deferred(), h = harness({request: () => gate.promise}), session = h.begin();
  const loading = h.ctx.loadPool();
  h.ctx.PoolBrowser.release(session); h.ctx.invalidatePoolContext();
  gate.resolve({items: [{id: 'stale'}], page: 1, pages: 1, total: 1, pool_total: 999, selected_total: 999});
  await loading;
  assert.equal(h.ctx.poolItems.length, 0); assert.equal(h.ctx.config.catalog.counts.character, 3);
  assert.equal(h.ctx.selectedCount('character'), 2); assert.equal(h.saves(), 0);
});

test('over-capacity filtered selection is rejected atomically instead of partially selecting people', async () => {
  const h = harness({items: Array.from({length: 4097}, (_, index) => `person-${index}`)}), session = h.begin({initialSelectedIds: ['kept']});
  await h.ctx.selectFilteredPool();
  assert.deepEqual(plain(session.selection.ids), ['kept']);
  assert.match(h.messages[0], /4096/); assert.deepEqual(plain(h.ctx.draft.pools), h.original);
});

test('single-character mode replaces selection and cannot use cross-page select-all', async () => {
  const h = harness(), session = h.begin({selectionMode: 'single', initialSelectedIds: ['a', 'b']});
  await h.ctx.loadPool();
  assert.deepEqual(plain(session.selection.ids), ['a']);
  await h.ctx.toggleSelection('character', 'c', true);
  assert.deepEqual(plain(session.result()).map(item => item.id), ['c']);
  const count = h.calls.length; await h.ctx.selectFilteredPool();
  assert.equal(h.calls.length, count); assert.equal(h.ui.selectPage.disabled, true);
  assert.match(h.ui.poolSelectionLabel.textContent, /选择 1 位人物/);
  assert.deepEqual(plain(h.ctx.draft.pools), h.original); assert.equal(h.saves(), 0);
});
