const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const history = fs.readFileSync(path.join(__dirname, '../static/history-detail.js'), 'utf8');
function install(ctx, name, source = app) {
  let start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  if (source.slice(start - 6, start) === 'async ') start -= 6;
  vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), ctx);
}
function deferred() { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; }

test('content filters accompany source and collection in every history URL and cache identity', () => {
  const view = { kind: 'home', queryEpoch: 2, query: { source: 'single', folderId: 'portraits' } };
  let filters = '&q=blue&content_filters=one';
  const ctx = vm.createContext({ historyViewState: () => view, historyFiltersQueryParams: () => filters });
  install(ctx, 'historyQueryParams', history); install(ctx, 'historyQueryKey', history);
  assert.equal(ctx.historyQueryParams(), '&workbench=single&folder_id=portraits&q=blue&content_filters=one');
  const old = ctx.historyQueryKey(); filters = '&q=blue&content_filters=two';
  assert.notEqual(ctx.historyQueryKey(), old);
});

test('source changes persist without losing content conditions or folder', () => {
  let persisted = 0;
  const ctx = vm.createContext({ historyDetailState: { homeQuery: { source: '', folderId: 'a', q: 'blue' } },
    HISTORY_SOURCES: new Set(['', 'single', 'random']), historyFiltersPersist() { persisted++; },
    invalidateHistoryView() {}, renderHistorySource() {}, loadHistory() {} });
  install(ctx, 'setHistorySource', history); ctx.setHistorySource('single');
  assert.equal(ctx.historyDetailState.homeQuery.q, 'blue');
  assert.equal(ctx.historyDetailState.homeQuery.folderId, 'a'); assert.equal(persisted, 1);
});

test('stale content response cannot update drawer counts or render stale results', async () => {
  const pending = [], rendered = [], counts = [];
  let key = 'old';
  const ctx = vm.createContext({ historyPage: 1, historyRequest: 0,
    historyQueryKey: () => key, historyQueryParams: () => '&q=' + key,
    request() { const p = deferred(); pending.push(p); return p.promise; },
    historyFiltersHistoryLoading() {}, historyFiltersHistoryResponse(data) { counts.push(data.total); },
    renderHistoryPage: data => rendered.push(data.total), toast() {} });
  install(ctx, 'historyViewState'); install(ctx, 'loadHistory');
  const old = ctx.loadHistory(1); key = 'current'; const next = ctx.loadHistory(1);
  pending[1].resolve({ page: 1, pages: 1, total: 3 }); await next;
  pending[0].resolve({ page: 1, pages: 1, total: 99 }); await old;
  assert.deepEqual(counts, [3]); assert.deepEqual(rendered, [3]);
});

test('revision changes are recovered without rendering old pages or showing duplicate errors', async () => {
  let recovered = 0, rendered = 0, toasts = 0;
  const ctx = vm.createContext({ historyPage: 1, historyRequest: 0,
    historyQueryKey: () => 'revision-one', historyQueryParams: () => '&group_revision=one',
    async request() { throw Object.assign(new Error('changed'), { code: 'history_filter_catalog_changed' }); },
    async historyFiltersHistoryError(error) { assert.equal(error.code, 'history_filter_catalog_changed'); recovered++; return true; },
    renderHistoryPage() { rendered++; }, toast() { toasts++; } });
  install(ctx, 'historyViewState'); install(ctx, 'loadHistory');
  await ctx.loadHistory(1);
  assert.equal(recovered, 1); assert.equal(rendered, 0); assert.equal(toasts, 0);
});

test('preview navigation uses revision recovery and never caches a failed neighbor', async () => {
  const view = { cache: new Map(), query: {}, kind: 'home' };
  let recoveries = 0;
  const ctx = vm.createContext({ previewHistoryView: () => view, historyViewState: value => value,
    historyQueryKey: () => 'one', historyQueryParams: () => '&content_filters=one',
    async request() { throw Object.assign(new Error('changed'), { code: 'history_filter_catalog_changed' }); },
    async historyFiltersHistoryError() { recoveries++; return true; } });
  install(ctx, 'cacheHistoryPage'); install(ctx, 'loadHistoryPageForPreview');
  assert.equal(await ctx.loadHistoryPageForPreview(2), null);
  assert.equal(recoveries, 1); assert.equal(view.cache.size, 0);
});

test('a failed catalog recovery cannot toast over a newer successful query', async () => {
  const recovery = deferred();
  let key = 'old', requests = 0, toasts = 0;
  const ctx = vm.createContext({ historyPage: 1, historyRequest: 0,
    historyQueryKey: () => key, historyQueryParams: () => '&q=' + key,
    async request() { if (++requests === 1) throw new Error('old failure'); return { page: 1, pages: 1, total: 2 }; },
    historyFiltersHistoryError: () => recovery.promise, renderHistoryPage() {}, toast() { toasts++; } });
  install(ctx, 'historyViewState'); install(ctx, 'loadHistory');
  const stale = ctx.loadHistory(1); await Promise.resolve();
  key = 'new'; await ctx.loadHistory(1); recovery.resolve(false); await stale;
  assert.equal(toasts, 0);
});

test('deleting off-page selected works never guesses filtered totals from source alone', () => {
  let invalidations = 0, candidateInvalidations = 0;
  const view = { cache: new Map(), items: [{ id: 1 }], query: { source: '', folderId: '' },
    total: 10, pages: 1, page: 1, elements: { gallery: { children: [] }, emptyState: {} } };
  const ctx = vm.createContext({ historyFiltersActive: () => true, invalidateHistoryView() { invalidations++; },
    historyFiltersHistoryChanged() { candidateInvalidations++; } });
  install(ctx, 'removeHistoryViewRecords', history);
  ctx.removeHistoryViewRecords(view, [{ id: 90, workbench: 'random', folder_ids: [] }]);
  assert.equal(view.total, 10, 'only server can decide whether off-page work matched a dynamic group');
  assert.equal(invalidations, 1);
  assert.equal(candidateInvalidations, 1, 'historical candidates change even when the filtered result count does not');
});

test('opening detail suspends queued filter painting before the modal starts', () => {
  const order = [];
  const ctx = vm.createContext({ document: { activeElement: null }, ui: { detailDialog: {} },
    historyFiltersSuspendPainting() { order.push('suspend'); }, closeImagePreview() {},
    historyViewState: value => value, updateDetailRecord() { order.push('record'); },
    updatePreviewNavigation() {}, motionDialog() { order.push('dialog'); } });
  install(ctx, 'openDetail');
  ctx.openDetail(null); assert.deepEqual(order, []);
  ctx.openDetail({ id: 1 }); assert.deepEqual(order, ['suspend', 'record', 'dialog']);
});
