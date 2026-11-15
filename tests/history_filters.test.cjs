const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const model = require('../static/history-filters.js');
const source = fs.readFileSync(path.join(__dirname, '../static/history-filters.js'), 'utf8');
const token = (value, kind = 'item', pool = 'character') => ({ pool, kind, value });
const plain = value => JSON.parse(JSON.stringify(value));
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function runtime(overrides = {}) {
  const query = { source: '', folderId: '', q: '', contentFilters: { include: [], exclude: [] }, groupRevision: 'r1' };
  const nodes = new Map();
  const context = vm.createContext({ URLSearchParams, setTimeout, clearTimeout, console,
    historyDetailState: { homeQuery: query }, document: { activeElement: null, getElementById: id => nodes.get(id) },
    invalidateHistoryView() {}, loadHistory() {}, request: async () => ({}), ...overrides });
  vm.runInContext(source, context);
  vm.runInContext('historyFiltersState.ready = true;', context);
  context.historyFiltersRenderConditions = () => {};
  context.historyFiltersRenderChoices = () => {};
  context.historyFiltersRenderOptions = () => {};
  context.historyFiltersRenderCatalog = () => {};
  context.historyFiltersCatalogStatus = () => {};
  context.historyFiltersRenderResult = () => {};
  context.historyFiltersPersist = () => {};
  return { context, query, nodes };
}

test('inclusion/exclusion are exclusive for the same rule without erasing parent/child rules', () => {
  const parent = token('parent', 'favorite_group'), child = token('child');
  let filters = model.toggle({}, parent, 'include');
  filters = model.toggle(filters, child, 'exclude');
  assert.deepEqual(filters, { include: [parent], exclude: [child] });
  filters = model.toggle(filters, child, 'include');
  assert.deepEqual(filters, { include: [parent, child], exclude: [] });
  filters = model.toggle(filters, child, 'include');
  assert.deepEqual(filters, { include: [parent], exclude: [] });
});

test('normalization validates namespaces and keeps IDs instead of merging same-name records', () => {
  const a = token('character:A'), b = token('custom:B');
  assert.deepEqual(model.normalize({ include: [a, a, b, token('blond', 'hair', 'pose'), token('x', 'unknown')], exclude: [a] }), { include: [b], exclude: [a] });
  assert.equal(model.token({ pool: 'character', kind: 'item', value: 42 }), null);
});

test('serialized filters are stable and never send display labels or expand dynamic groups into members', () => {
  const a = token('A'), group = token('group-1', 'custom_group');
  const left = model.params({ q: ' 白发_% ', groupRevision: 'r2', contentFilters: { include: [{ ...group, label: 'favorites' }, a] } });
  const right = model.params({ q: ' 白发_% ', groupRevision: 'r2', contentFilters: { include: [a, group] } });
  assert.equal(left, right);
  const params = new URLSearchParams(left.slice(1));
  assert.equal(params.get('q'), '白发_%'); assert.equal(params.get('group_revision'), 'r2');
  assert.deepEqual(JSON.parse(params.get('content_filters')), { include: [group, a], exclude: [] });
  assert.doesNotMatch(params.get('content_filters'), /label|members/);
  assert.equal(model.params({ q: 'hello', groupRevision: 'r2' }), '&q=hello');
});

test('stored conditions recover safely without restoring folders or revision', () => {
  assert.equal(model.restore('invalid'), null);
  assert.equal(model.restore('{"version":2}'), null);
  const restored = model.restore(JSON.stringify({ version: 1, source: 'single', folderId: 'not-persisted', groupRevision: 'old', q: '微笑', contentFilters: { exclude: [token('g', 'favorite_group')] }, labels: { a: '人物' } }));
  assert.equal(restored.source, ''); assert.equal(restored.q, '微笑');
  assert.equal(restored.folderId, undefined); assert.equal(restored.groupRevision, undefined);
  assert.equal(restored.contentFilters.exclude[0].value, 'g');
});

test('selection limits reject additions without truncating existing conditions', () => {
  const filters = { include: Array.from({ length: 128 }, (_, index) => token(`item-${index}`)), exclude: [] };
  assert.throws(() => model.toggle(filters, token('one-more'), 'include'), /128/);
  assert.equal(filters.include.length, 128);
  const moved = model.toggle(filters, token('item-1'), 'exclude');
  assert.equal(moved.include.length, 127); assert.equal(moved.exclude.length, 1);
  assert.equal(model.token(token('x'.repeat(1001))), null);
  assert.equal(model.token(token('bad\u0000id')), null);
  assert.notEqual(model.token(token('人物😀')), null);
});

test('a rejected addition gives inline feedback and leaves the current query intact', () => {
  let loads = 0;
  const { context, query, nodes } = runtime({ loadHistory: () => loads++ });
  query.contentFilters.include = Array.from({ length: 128 }, (_, index) => token(`i-${index}`));
  const feedback = { hidden: true, textContent: '' }; nodes.set('historyFilterFeedback', feedback);
  context.historyFiltersToggle(token('overflow'), 'include', 'More');
  assert.equal(query.contentFilters.include.length, 128); assert.equal(loads, 0);
  assert.equal(feedback.hidden, false); assert.match(feedback.textContent, /128/);
});

test('encoded request budget counts long namespaced IDs and reserves future group revision', () => {
  const longName = token(`character:${'人'.repeat(700)}`);
  const query = { contentFilters: { include: [longName], exclude: [] } };
  assert.doesNotThrow(() => model.ensureRequestBudget(query));
  assert.throws(() => model.ensureRequestBudget({ ...query, q: '海'.repeat(100) }), /整组筛选/);
  const dynamic = { contentFilters: { include: [token('g', 'favorite_group')], exclude: [] } };
  assert.equal(model.requestLength(dynamic), model.params({ ...dynamic, groupRevision: 'a'.repeat(64) }).length);
});

test('oversized new toggle leaves applied filters and result requests unchanged', () => {
  let loads = 0;
  const { context, query, nodes } = runtime({ loadHistory: () => loads++ });
  const original = token(`character:${'人'.repeat(700)}`); query.contentFilters.include = [original];
  const feedback = { hidden: true, textContent: '' }; nodes.set('historyFilterFeedback', feedback);
  context.historyFiltersToggle(token(`character:${'姿'.repeat(100)}`), 'include', 'Additional');
  assert.deepEqual(query.contentFilters.include, [original]); assert.equal(loads, 0);
  assert.match(feedback.textContent, /减少条件.*整组筛选/); assert.equal(feedback.hidden, false);
});

test('oversized combined search reverts the input while preserving the last applied query', () => {
  const timers = []; let invalidations = 0;
  const { context, query, nodes } = runtime({ setTimeout: fn => { timers.push(fn); }, invalidateHistoryView: () => invalidations++ });
  query.q = 'old'; query.contentFilters.include = [token(`character:${'人'.repeat(700)}`)];
  const input = { value: '海'.repeat(100) }, feedback = { hidden: true, textContent: '' };
  nodes.set('historyFilterSearch', input); nodes.set('historyFilterFeedback', feedback);
  context.historyFiltersMainInput();
  assert.equal(query.q, 'old'); assert.equal(input.value, 'old'); assert.equal(timers.length, 0); assert.equal(invalidations, 0);
  assert.match(feedback.textContent, /缩短搜索词/);
});

test('composition does not query partial Chinese input; debounce invalidates stale history immediately', () => {
  const timers = [], pages = []; let invalidations = 0;
  const { context, query, nodes } = runtime({ setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout() {}, invalidateHistoryView() { invalidations++; }, loadHistory: page => pages.push(page) });
  nodes.set('historyFilterSearch', { value: '人物' });
  vm.runInContext('historyFiltersState.composing = true;', context);
  context.historyFiltersMainInput(); assert.equal(query.q, ''); assert.equal(timers.length, 0);
  vm.runInContext('historyFiltersState.composing = false;', context);
  context.historyFiltersMainInput(); assert.equal(query.q, '人物'); assert.equal(invalidations, 1); assert.deepEqual(pages, []);
  timers[0](); assert.deepEqual(pages, [1]);
});

test('clear removes only content conditions and search, preserving source and folder', () => {
  const { context, query, nodes } = runtime(); query.source = 'single'; query.folderId = 'folder-1'; query.q = 'old'; query.contentFilters.include = [token('A')];
  nodes.set('historyFilterSearch', { value: 'old' }); context.historyFiltersClear();
  assert.equal(query.source, 'single'); assert.equal(query.folderId, 'folder-1'); assert.equal(query.q, '');
  assert.deepEqual(plain(query.contentFilters), { include: [], exclude: [] });
});

test('late candidate responses never replace a newer search', async () => {
  const pending = [], { context } = runtime({ request: url => { const item = deferred(); pending.push({ ...item, url }); return item.promise; } });
  const panel = { section: 'pose', kind: 'all', q: '', items: [], serial: 0, loading: false, page: 1, more: false };
  const first = context.historyFiltersLoadOptions(panel);
  panel.q = '坐'; ++panel.serial; panel.loading = false;
  const second = context.historyFiltersLoadOptions(panel);
  pending[1].resolve({ items: [{ id: 'pose:current', title: '坐姿' }], page: 1, has_more: true }); await second;
  pending[0].resolve({ items: [{ id: 'pose:old' }], page: 1, has_more: false }); await first;
  assert.equal(panel.items[0].id, 'pose:current'); assert.equal(panel.more, true);
  assert.equal(new URL(pending[1].url, 'http://local').searchParams.get('q'), '坐');
});

test('candidate responses from an old group revision cannot commit', async () => {
  const pending = deferred(), { context, query } = runtime({ request: () => pending.promise });
  const panel = { section: 'character', kind: 'favorite_group', value: 'g', q: '', items: [{ id: 'kept' }], serial: 0, loading: false, page: 1, more: false };
  const loading = context.historyFiltersLoadOptions(panel); query.groupRevision = 'r2';
  pending.resolve({ items: [{ id: 'obsolete' }], page: 1 }); await loading;
  assert.equal(panel.items[0].id, 'kept'); assert.equal(panel.loading, false);
});

test('a revision response clears only the matching home history cache', () => {
  const { context, query } = runtime(), cache = new Map([[2, { items: [] }]]), view = { kind: 'home', cache, queryEpoch: 5 };
  context.historyFiltersHistoryResponse({ total: 8, group_revision: 'r2' }, view);
  assert.equal(query.groupRevision, 'r2'); assert.equal(cache.size, 0); assert.equal(view.queryEpoch, 6);
  context.historyFiltersHistoryResponse({ total: 2, group_revision: 'single-r' }, { kind: 'single' });
  assert.equal(query.groupRevision, 'r2');
});

test('409 recovery requires revision progress and does not retry an unchanged catalog', async () => {
  let reads = 0, reloads = 0;
  const { context, query } = runtime({ request: async () => { reads++; return { sections: [], group_revision: 'r1' }; }, loadHistory: () => reloads++ });
  const view = { kind: 'home', query, requestId: 1 }, error = { code: 'history_filter_catalog_changed', message: 'changed' };
  assert.equal(await context.historyFiltersHistoryError(error, view), false);
  assert.equal(reads, 1); assert.equal(reloads, 0);
  assert.match(vm.runInContext('historyFiltersState.resultError', context), /版本尚未更新/);
  assert.equal(await context.historyFiltersHistoryError(error, view), false);
  assert.equal(reads, 1); assert.equal(reloads, 0);
});

test('a query-intent chain gets only one automatic recovery even when revisions keep advancing', async () => {
  let reads = 0, reloads = 0;
  const { context, query } = runtime({ request: async () => ({ sections: [], group_revision: `r${++reads + 1}` }), loadHistory: () => reloads++ });
  const view = { kind: 'home', query, requestId: 1, cache: new Map(), queryEpoch: 0 }, error = { code: 'history_filter_catalog_changed', message: 'changed' };
  assert.equal(await context.historyFiltersHistoryError(error, view), true);
  assert.equal(query.groupRevision, 'r2'); assert.equal(reads, 1); assert.equal(reloads, 1);
  view.requestId++; view.queryEpoch++; context.historyFiltersHistoryLoading(view);
  assert.equal(await context.historyFiltersHistoryError(error, view), false);
  assert.equal(reads, 1); assert.equal(reloads, 1);
  context.historyFiltersHistoryResponse({ total: 4, group_revision: 'r2' }, view);
  assert.equal(await context.historyFiltersHistoryError(error, view), true);
  assert.equal(reads, 2); assert.equal(reloads, 2);
});

test('a new query intent or explicit retry renews the bounded recovery budget', async () => {
  let reads = 0;
  const { context, query } = runtime({ request: async () => { reads++; return { sections: [], group_revision: query.groupRevision }; } });
  const view = { kind: 'home', query, requestId: 1 }, error = { code: 'history_filter_catalog_changed' };
  await context.historyFiltersHistoryError(error, view); assert.equal(reads, 1);
  query.source = 'single'; view.requestId++; context.historyFiltersHistoryLoading(view);
  await context.historyFiltersHistoryError(error, view); assert.equal(reads, 2);
  context.historyFiltersRetryHistory();
  await context.historyFiltersHistoryError(error, view); assert.equal(reads, 3);
});

test('late failed catalog recovery cannot overwrite a newer request footer or toast', async () => {
  const pending = deferred(); let reloads = 0;
  const { context, query } = runtime({ request: () => pending.promise, loadHistory: () => reloads++ });
  const view = { kind: 'home', query, requestId: 1 }, error = { code: 'history_filter_catalog_changed', message: 'old error' };
  const recovery = context.historyFiltersHistoryError(error, view);
  view.requestId++;
  vm.runInContext('historyFiltersState.resultError = "new request status"; historyFiltersState.resultLoading = true;', context);
  pending.reject(new Error('old catalog failure'));
  assert.equal(await recovery, true);
  assert.equal(reloads, 0);
  assert.equal(vm.runInContext('historyFiltersState.resultError', context), 'new request status');
  assert.equal(vm.runInContext('historyFiltersState.resultLoading', context), true);
  assert.equal(vm.runInContext('historyFiltersState.catalogError', context), '');
});

test('a stale recovery cannot change revision and strand a newer query in flight', async () => {
  const pending = deferred(); let reloads = 0;
  const { context, query } = runtime({ request: () => pending.promise, loadHistory: () => reloads++ });
  const view = { kind: 'home', query, requestId: 1 };
  const recovery = context.historyFiltersHistoryError({ code: 'history_filter_catalog_changed' }, view);
  query.q = 'new intent'; // Even before the debounce starts another HTTP request.
  pending.resolve({ sections: [], group_revision: 'r2' });
  assert.equal(await recovery, true);
  assert.equal(query.groupRevision, 'r1'); assert.equal(reloads, 0);
});

test('concurrent history and preview 409 errors share one recovery owner', async () => {
  const pending = deferred(); let reads = 0, reloads = 0;
  const { context, query } = runtime({ request: () => { reads++; return pending.promise; }, loadHistory: () => reloads++ });
  const view = { kind: 'home', query, requestId: 1 }, error = { code: 'history_filter_catalog_changed' };
  const first = context.historyFiltersHistoryError(error, view), second = context.historyFiltersHistoryError(error, view);
  pending.resolve({ sections: [], group_revision: 'r2' });
  assert.deepEqual(await Promise.all([first, second]), [true, true]);
  assert.equal(reads, 1); assert.equal(reloads, 1);
});

test('mobile mode restores original inert and scroll values when crossing the breakpoint', () => {
  const attrs = {}, drawer = { setAttribute: (key, value) => { attrs[key] = value; }, contains: () => true };
  const main = { tagName: 'MAIN', inert: false }, alreadyInert = { tagName: 'ASIDE', inert: true }, script = { tagName: 'SCRIPT', inert: false };
  const body = { children: [main, alreadyInert, drawer, script], style: { overflow: 'auto' } };
  const { context, nodes } = runtime(); nodes.set('historyFilterDrawer', drawer); context.document.body = body;
  vm.runInContext('historyFiltersState.open = true; historyFiltersState.media = { matches: true };', context);
  context.historyFiltersSyncMobile();
  assert.equal(main.inert, true); assert.equal(script.inert, false); assert.equal(body.style.overflow, 'hidden'); assert.equal(attrs['aria-modal'], 'true');
  vm.runInContext('historyFiltersState.media.matches = false;', context); context.historyFiltersSyncMobile();
  assert.equal(main.inert, false); assert.equal(alreadyInert.inert, true); assert.equal(body.style.overflow, 'auto'); assert.equal(attrs['aria-modal'], 'false');
});

test('Escape does not close the filter drawer underneath another modal', () => {
  const { context, nodes } = runtime(); nodes.set('historyFilterDrawer', { contains: () => true });
  vm.runInContext('historyFiltersState.open = true;', context);
  context.document.querySelector = () => ({ open: true });
  let closed = false; context.historyFiltersSetOpen = () => { closed = true; };
  context.historyFiltersKeydown({ key: 'Escape', defaultPrevented: false, preventDefault() { throw new Error('must leave the upper modal in control'); } });
  assert.equal(closed, false);
});

test('closed trigger stays plain and search/conditions live inside the independent accessible drawer', () => {
  const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
  const trigger = html.match(/<button id="openHistoryFilters"[\s\S]*?<\/button>/)[0];
  assert.match(trigger, /aria-controls="historyFilterDrawer"/);
  assert.doesNotMatch(trigger, /badge|count|summary|aria-pressed|is-active/);
  const drawer = html.match(/<aside id="historyFilterDrawer"[\s\S]*?<\/aside>/)[0];
  assert.match(drawer, /role="dialog" aria-modal="false"/);
  assert.match(drawer, /id="historyFilterSearch"/); assert.match(drawer, /id="historyFilterConditions"/);
  assert.doesNotMatch(drawer, /新建分组|删除分组|拖拽|应用筛选/);
  assert.doesNotMatch(source, /ensurePoolReconciled|sharedFavoritesChanged\(/);
});
