const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
function setup({ items = ['a', 'b', 'c'], request } = {}) {
  const calls = [], messages = [];
  let saves = 0;
  const view = { query: '', sort: '', selectedOnly: false, filters: { collection: '', customGroup: '', categories: [], traits: [], gender: '', hair: '', eye: '', series: '' } };
  const ui = new Proxy({}, { get: (target, name) => target[name] ||= { disabled: false, hidden: false, textContent: '', value: '' } });
  const ctx = vm.createContext({
    console, ui, profile: 'anima', currentProfile: () => ctx.profile, currentView: () => view, clone: plain,
    draft: { pools: { pose: { mode: 'include', ids: [], excluded_ids: [] } } },
    activeSection: 'pose', poolPage: 1, poolPages: 1, poolItems: [], poolFacets: {}, poolRequest: 0,
    poolContextRevision: 0, poolSelectionOperation: null, poolResultContext: '', poolQueryCount: null,
    poolSelectionStats: new Map(), config: { catalog: { counts: { pose: items.length } } },
    poseItemCache: new Map(), favoritesAvailable: true, clearTimeout() {}, requestAnimationFrame: fn => fn(),
    renderPoolItems: () => ctx.updatePoolSelectionControls(), renderDimensions() {}, renderPoolSidebar() {}, updatePoolOverview() {}, updatePoseConflict() {},
    schedulePersist: () => saves++, toast: text => messages.push(text),
    request: async (url, init) => {
      const body = JSON.parse(init.body); calls.push({ url, body });
      if (request) { const result = request(url, body); if (result !== undefined) return result; }
      const selected = s => items.filter(id => s.mode === 'all' ? !s.excluded_ids.includes(id) : s.ids.includes(id));
      let matches = items.filter(id => !body.q || id.includes(body.q));
      if (body.selection) matches = matches.filter(id => selected(body.selection).includes(id));
      const stats = { total: matches.length, pool_total: items.length, selected_total: selected(body.selection_state).length };
      if (url.endsWith('/ids')) return { ...stats, ids: matches };
      const pages = Math.max(1, Math.ceil(matches.length / body.limit)), page = Math.min(body.page, pages);
      return { ...stats, page, pages, items: matches.slice((page - 1) * body.limit, page * body.limit).map(id => ({ id })), facets: {} };
    },
  });
  for (const name of ['characterPickerSession', 'poolBrowserProfile', 'poolBrowserSelection', 'poolBrowserCount', 'selectedCount', 'poolQueryBody', 'invalidatePoolContext', 'poolSelectionContext', 'poolSelectionQuery', 'updatePoolSelectionControls', 'poolSelectionChanged', 'selectFilteredPool', 'setPoolMoreSelection', 'useEntirePool', 'clearPoolSelection', 'loadPool', 'toggleSelection', 'selectCurrentPage']) {
    const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
    assert.ok(start >= 0, name);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), ctx);
  }
  return { ctx, ui, view, calls, messages, saves: () => saves };
}

for (const size of [49, 101, 8000]) test(`filtered selection appends all ${size} matches in one IDs request`, async () => {
  const h = setup({ items: ['outside', ...Array.from({ length: size }, (_, i) => `match-${i}`)] });
  h.ctx.draft.pools.pose.ids = ['outside', 'match-0']; h.view.query = 'match-';
  await h.ctx.loadPool();
  assert.equal(h.ctx.poolItems.length, 48);
  await h.ctx.selectFilteredPool();
  assert.equal(h.ctx.draft.pools.pose.ids.length, size + 1);
  assert.equal(h.ctx.draft.pools.pose.ids[0], 'outside');
  assert.equal(h.calls.filter(call => call.url.endsWith('/ids')).length, 1);
  assert.equal(h.ctx.selectedCount('pose'), size + 1);
  assert.equal(h.saves(), 1);
});

test('all-mode re-includes filtered exclusions without changing unrelated exclusions', async () => {
  const h = setup({ items: ['a', 'b', 'c'] });
  h.ctx.draft.pools.pose = { mode: 'all', ids: [], excluded_ids: ['a', 'c', 'deleted'] };
  h.view.query = 'a'; await h.ctx.selectFilteredPool();
  assert.deepEqual(plain(h.ctx.draft.pools.pose), { mode: 'all', ids: [], excluded_ids: ['c', 'deleted'] });
  assert.equal(h.ctx.selectedCount('pose'), 2);
  assert.equal(h.ui.poolModeNote.hidden, false);
});

test('selection summary excludes stale IDs and is independent of filtering', async () => {
  const h = setup(); h.ctx.draft.pools.pose.ids = ['a', 'b', 'deleted']; h.view.query = 'a';
  await h.ctx.loadPool();
  assert.equal(h.ctx.poolItems.length, 1); assert.equal(h.ctx.selectedCount('pose'), 2);
  assert.equal(h.ui.poolMeta.textContent, '1 项匹配 · 2 项已选');
  assert.equal(h.calls[0].body.selection, undefined);
  assert.deepEqual(h.calls[0].body.selection_state.ids, ['a', 'b', 'deleted']);
});

test('selected-only removal clamps the last page and clear removes every displayed card', async () => {
  const h = setup({ items: Array.from({ length: 49 }, (_, i) => `id-${i}`) });
  h.ctx.draft.pools.pose.ids = Array.from({ length: 49 }, (_, i) => `id-${i}`);
  h.view.selectedOnly = true; h.ctx.poolPage = 2;
  await h.ctx.loadPool(); assert.equal(h.ctx.poolItems.length, 1);
  await h.ctx.toggleSelection('pose', 'id-48', false);
  assert.equal(h.ctx.poolPage, 1); assert.equal(h.ctx.poolPages, 1); assert.equal(h.ctx.poolItems.length, 48);
  await h.ctx.clearPoolSelection();
  assert.equal(h.ctx.poolItems.length, 0); assert.equal(h.ctx.selectedCount('pose'), 0);
  assert.equal(h.ui.selectFilteredPool.disabled, true);
});

test('page selection respects the loaded context and does not silently select other pages', async () => {
  const h = setup({ items: Array.from({ length: 60 }, (_, i) => `id-${i}`) });
  await h.ctx.loadPool(); await h.ctx.selectCurrentPage(true);
  assert.equal(h.ctx.draft.pools.pose.ids.length, 48);
  h.view.query = 'id-59'; await h.ctx.selectCurrentPage(true);
  assert.equal(h.ctx.draft.pools.pose.ids.length, 48);
});

test('stale single cards cannot select into a new filter or section', async () => {
  const h = setup(); await h.ctx.loadPool();
  h.view.query = 'b'; h.ctx.updatePoolSelectionControls();
  assert.equal(h.ui.poolGrid.inert, true);
  await h.ctx.toggleSelection('pose', 'a', true);
  assert.deepEqual(plain(h.ctx.draft.pools.pose.ids), []);
  await h.ctx.loadPool(); assert.equal(h.ui.poolGrid.inert, false);
  h.ctx.draft.pools.character = { mode: 'include', ids: [], excluded_ids: [] };
  h.ctx.activeSection = 'character';
  await h.ctx.toggleSelection('character', 'b', true);
  assert.deepEqual(plain(h.ctx.draft.pools.character.ids), []);
});

test('whole library and clearing remain explicit modes', async () => {
  const h = setup(); h.view.query = 'a'; await h.ctx.useEntirePool();
  assert.equal(h.ctx.selectedCount('pose'), 3); assert.equal(h.ctx.draft.pools.pose.mode, 'all');
  assert.match(h.ui.poolModeNote.textContent, /新增词条/);
  await h.ctx.clearPoolSelection();
  assert.equal(h.ctx.draft.pools.pose.mode, 'include'); assert.equal(h.ui.poolModeNote.hidden, true);
});

test('empty results and failures do not change or persist selections', async () => {
  const h = setup(); h.ctx.draft.pools.pose.ids = ['b']; h.view.query = 'missing';
  await h.ctx.selectFilteredPool(); assert.deepEqual(plain(h.ctx.draft.pools.pose.ids), ['b']); assert.equal(h.saves(), 0);
  const failed = setup({ request: url => { if (url.endsWith('/ids')) throw new Error('offline'); } });
  failed.ctx.draft.pools.pose.ids = ['b']; await failed.ctx.selectFilteredPool();
  assert.deepEqual(plain(failed.ctx.draft.pools.pose.ids), ['b']); assert.equal(failed.saves(), 0);
  assert.match(failed.messages[0], /offline/); assert.equal(failed.ui.selectFilteredPool.disabled, false);
});

for (const change of ['clear', 'filter', 'profile', 'section', 'history', 'selection']) test(`late batch selection cannot overwrite a ${change} change`, async () => {
  const gate = deferred();
  const h = setup({ request: url => url.endsWith('/ids') ? gate.promise : undefined });
  h.ctx.draft.pools.pose.ids = ['b']; const pending = h.ctx.selectFilteredPool();
  await h.ctx.selectFilteredPool(); assert.equal(h.calls.filter(call => call.url.endsWith('/ids')).length, 1);
  if (change === 'clear') await h.ctx.clearPoolSelection();
  if (change === 'filter') h.view.query = 'c';
  if (change === 'profile') h.ctx.profile = 'krea2';
  if (change === 'section') { h.ctx.draft.pools.character = { mode: 'include', ids: [], excluded_ids: [] }; h.ctx.activeSection = 'character'; }
  if (change === 'history') h.ctx.invalidatePoolContext();
  if (change === 'selection') h.ctx.draft.pools.pose.ids = ['c'];
  const before = plain(h.ctx.draft); gate.resolve({ ids: ['a'], total: 1 }); await pending;
  assert.deepEqual(plain(h.ctx.draft), before);
});

test('late selected-only reads cannot repopulate a cleared list', async () => {
  const gate = deferred(); let reads = 0;
  const h = setup({ request: url => url.endsWith('/query') && ++reads === 1 ? gate.promise : undefined });
  h.ctx.draft.pools.pose.ids = ['a']; h.view.selectedOnly = true;
  const old = h.ctx.loadPool(); await h.ctx.clearPoolSelection();
  gate.resolve({ items: [{ id: 'a' }], total: 1, page: 1, pages: 1, selected_total: 1 }); await old;
  assert.equal(h.ctx.poolItems.length, 0); assert.equal(h.ctx.selectedCount('pose'), 0);
});

test('a late background resource refresh cannot cancel a newer selection operation', async () => {
  const h = setup(), groups = deferred(); let loads = 0;
  Object.assign(h.ctx, { loadResources() {}, loadLoraInventory() {}, loadStylePresets() {}, loadCustomGroups: () => groups.promise, loadPool: () => loads++ });
  const start = source.indexOf('function refreshProfileData(');
  vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), h.ctx);
  const pending = h.ctx.refreshProfileData();
  h.ctx.invalidatePoolContext();
  groups.resolve(); await pending;
  assert.equal(loads, 0);
});

test('restored draft totals validate every pool without opening its drawer and reject replaced drafts', async () => {
  const h = setup(); h.ctx.SECTIONS = ['pose']; h.ctx.poolTotalsRequest = 0;
  h.ui.poolDrawer.classList = { contains: () => false };
  const start = source.indexOf('async function refreshPoolSelectionTotals(');
  vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), h.ctx);
  h.ctx.draft.pools.pose.ids = ['a', 'deleted'];
  await h.ctx.refreshPoolSelectionTotals();
  assert.equal(h.ctx.selectedCount('pose'), 1);
  const gate = deferred(); h.ctx.request = () => gate.promise;
  const old = h.ctx.refreshPoolSelectionTotals(); await Promise.resolve();
  h.ctx.draft.pools = { pose: { mode: 'include', ids: ['b'], excluded_ids: [] } };
  gate.resolve({ selected_total: 99, pool_total: 999 }); await old;
  assert.equal(h.ctx.selectedCount('pose'), 1);
  assert.equal(h.ctx.config.catalog.counts.pose, 3);
});

test('closing a pool restores focus to the rebuilt manage button before hiding the drawer', () => {
  const h = setup(); let focused = false;
  h.ctx.drawerTrigger = {}; h.ctx.document = { contains: () => false };
  h.ctx.setPoolSidebarOpen = () => {}; h.ctx.hideDrawerBackdrop = () => {};
  h.ui.dimensionList.querySelector = () => ({ focus: () => { focused = true; } });
  h.ui.poolDrawer.classList = { remove() {} };
  h.ui.poolDrawer.setAttribute = name => { if (name === 'aria-hidden') assert.equal(focused, true); };
  const start = source.indexOf('function closePool(');
  vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), h.ctx);
  h.ctx.closePool();
  assert.equal(focused, true); assert.equal(h.ctx.drawerTrigger, null);
});
