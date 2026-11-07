const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/history-detail.js'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const record = id => ({ id, filename: `${id}.png`, workbench: 'random' });
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function install(ctx, text, name) {
  let start = text.indexOf(`function ${name}(`); assert.ok(start >= 0, name);
  if (text.slice(start - 6, start) === 'async ') start -= 6;
  vm.runInContext(text.slice(start, text.indexOf('\n}', start) + 2), ctx);
}
function setup(extra = {}) {
  const elements = new Map(), calls = [], notices = [], closes = [];
  const document = { activeElement: null, querySelector: () => null, querySelectorAll: () => [] };
  const node = (id, props = {}) => {
    const classes = new Set(), events = {};
    const result = { id, hidden: false, inert: false, disabled: false, scrollTop: 0, textContent: '', value: '', dataset: {}, events,
      classList: { add: (...values) => values.forEach(value => classes.add(value)), remove: (...values) => values.forEach(value => classes.delete(value)), toggle: (value, on) => on ? classes.add(value) : classes.delete(value), contains: value => classes.has(value) },
      style: { removeProperty(key) { delete this[key]; } }, isConnected: true,
      setAttribute() {}, removeAttribute() {}, addEventListener(name, handler) { events[name] = handler; },
      querySelector: () => null, querySelectorAll: () => [], closest: () => null, getClientRects: () => [{}], focus() { document.activeElement = this; },
      ...props };
    elements.set(id, result); return result;
  };
  const get = id => elements.get(id) || node(id); document.getElementById = get;
  for (const id of ['detailDialog', 'historyDetailView', 'historyDeletePanel', 'historyDetailBack', 'historyDetailTitle', 'historyDeleteMode', 'historyDeleteResults', 'historyDeleteSummary', 'historyDeleteWarning', 'historyDeleteSubmit']) node(id);
  get('detailDialog').open = true;
  const ctx = vm.createContext({ document, config: {}, historyItems: [], historyCache: new Map(), historyPage: 1, historyPages: 1, historyRequest: 0, historyDirty: false,
    ui: { gallery: { children: [], querySelector: () => null }, detailDialog: get('detailDialog'), historyCount: { textContent: '0 张图片' }, pageLabel: {}, prevPage: {}, nextPage: {}, emptyState: {} },
    toast: value => notices.push(value), selectedRecord: null, detailContext: null, closeImagePreview() {}, renderDetail() {},
    request: async (url, options) => { calls.push({ url, options }); return { deleted: true, results: [{ id: 1, record_deleted: true }] }; },
    motionDialog: (host, open) => { closes.push(open); host.open = open; }, loadHistory: async () => {}, singleGalleryDeleted: async () => null,
    ...extra });
  install(ctx, app, 'historyViewState'); vm.runInContext(source, ctx);
  const state = vm.runInContext('historyDetailState', ctx);
  return { ctx, elements, state, get, node, calls, notices, closes, document };
}

test('source query is stored independently of controls and invalidation cancels stale preview navigation', () => {
  let cleared = 0;
  const h = setup({ imagePreview: { navigationRequest: 10 }, clearPreviewCache: () => cleared++ });
  h.get('historyWorkbenchFilter').value = 'ignored';
  assert.equal(h.ctx.historyQueryParams(), '');
  h.state.homeQuery.source = 'single';
  assert.equal(h.ctx.historyQueryParams(), '&workbench=single');
  const key = h.ctx.historyQueryKey(); h.ctx.historyCache.set(2, { items: [record(2)] });
  h.ctx.invalidateHistoryView();
  assert.notEqual(h.ctx.historyQueryKey(), key); assert.equal(h.ctx.historyCache.size, 0);
  assert.equal(h.ctx.imagePreview.navigationRequest, 11); assert.equal(cleared, 1);
});

test('single deletion opens with record-only default and keeps its exact detail owner', () => {
  const item = record(7), owner = { origin: 'random' }, h = setup({ selectedRecord: item, detailContext: owner });
  h.get('historyDeleteMode').value = 'file';
  h.ctx.openHistoryDelete(item);
  assert.equal(h.state.record, item); assert.equal(h.state.owner, owner); assert.equal(h.state.view, 'delete');
  assert.equal(h.get('historyDeleteMode').value, 'record');
  assert.equal(h.get('historyDeletePanel').hidden, false); assert.equal(h.get('historyDetailView').hidden, true);
  h.state.busy = true; assert.equal(h.ctx.historyDetailBack(), true); assert.equal(h.state.view, 'delete');
  h.state.busy = false; assert.equal(h.ctx.historyDetailBack(), true); assert.equal(h.state.view, 'detail');
  assert.equal(h.ctx.historyDetailBack(), false);
});

test('readonly history cannot open a destructive action or submit a pending one', async () => {
  const h = setup({ config: { storage_status: { history: { read_only: true } } }, selectedRecord: record(1) });
  h.ctx.openHistoryDelete(h.ctx.selectedRecord); assert.equal(h.state.view, 'detail');
  h.state.record = h.ctx.selectedRecord;
  await h.ctx.confirmHistoryDelete({ preventDefault() {} });
  assert.equal(h.calls.length, 0);
});

test('a failed permanent deletion reports partial file removal and retries the same single record', async () => {
  const item = record(7), owner = { origin: 'random' }, h = setup({ selectedRecord: item, detailContext: owner });
  h.ctx.openHistoryDelete(item); h.get('historyDeleteMode').value = 'file';
  let attempts = 0;
  h.ctx.request = async (url, options) => {
    h.calls.push({ url, options }); attempts++;
    if (attempts === 1) {
      const error = new Error('请求失败 (409)'); error.status = 409;
      error.payload = { deleted: false, results: [{ id: 7, file_deleted: true, record_deleted: false, error: '数据库提交失败' }] };
      throw error;
    }
    return { deleted: true, results: [{ id: 7, file_deleted: false, record_deleted: true }] };
  };
  h.ctx.applyHistoryDeleted = async () => [];
  await h.ctx.confirmHistoryDelete({ preventDefault() {} });
  assert.equal(h.state.record, item); assert.equal(h.state.view, 'delete'); assert.equal(h.state.busy, false);
  assert.equal(h.get('historyDeleteSubmit').disabled, false);
  assert.match(h.get('historyDeleteResults').textContent, /原文件已删除.*记录未删除/);
  assert.match(h.get('historyDeleteResults').textContent, /数据库提交失败/);
  await h.ctx.confirmHistoryDelete({ preventDefault() {} });
  assert.equal(h.calls.length, 2);
  assert.ok(h.calls.every(call => call.url === '/api/history/7?mode=file' && call.options.method === 'DELETE'));
  assert.ok(h.calls.every(call => !call.options.body));
  assert.equal(h.state.record, null); assert.match(h.notices.at(-1), /已删除作品/);
});

test('pending deletion blocks duplicate submits and navigation until its result returns', async () => {
  const pending = deferred(), item = record(1), h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
  h.ctx.openHistoryDelete(item); h.ctx.request = () => { h.calls.push(true); return pending.promise; };
  h.ctx.applyHistoryDeleted = async () => [];
  const first = h.ctx.confirmHistoryDelete({ preventDefault() {} });
  await h.ctx.confirmHistoryDelete({ preventDefault() {} });
  assert.equal(h.calls.length, 1); assert.equal(h.state.busy, true); assert.equal(h.ctx.historyDetailBack(), true);
  assert.equal(h.state.view, 'delete'); assert.equal(h.get('historyDeleteMode').disabled, true);
  pending.resolve({ deleted: true, results: [{ id: 1, record_deleted: true }] }); await first;
  assert.equal(h.state.busy, false); assert.equal(h.get('historyDeleteMode').disabled, false);
});

test('committed deletion removes local rows and fixes counts even if refreshes fail', async () => {
  const item = record(1), next = record(2), removed = [], h = setup({ historyItems: [item, next], historyPages: 2,
    loadHistory: async () => { throw new Error('history offline'); }, singleGalleryDeleted: async () => { throw new Error('single offline'); } });
  h.ctx.ui.gallery.children = [item, next].map(value => ({ dataset: { recordId: String(value.id) }, remove: () => removed.push(value.id) }));
  h.ctx.ui.historyCount.textContent = '25 张图片'; h.ctx.historyCache.set(1, { page: 1, total: 25, items: [item, next] });
  const warnings = await h.ctx.applyHistoryDeleted(item, null);
  assert.match(warnings.join('；'), /history offline/);
  assert.deepEqual(plain(h.ctx.historyItems.map(value => value.id)), [2]); assert.deepEqual(removed, [1]);
  assert.equal(h.ctx.ui.historyCount.textContent, '24 张图片'); assert.equal(h.ctx.historyCache.size, 0);
  assert.equal(h.ctx.historyPages, 1);
});

test('deleting a work outside the current collection does not decrement its count', () => {
  const item = { ...record(9), folder_ids: ['other'] };
  const h = setup({ historyItems: [{ ...record(1), folder_ids: ['current'] }] });
  h.state.homeQuery.folderId = 'current';
  const view = h.ctx.historyViewState(); view.total = 8;
  h.ctx.ui.historyCount.textContent = '8 张图片';
  h.ctx.removeHistoryViewRecord(view, item);
  assert.equal(view.total, 8); assert.equal(h.ctx.ui.historyCount.textContent, '8 张图片');
});

test('successful deletion still announces its result when both history refreshes fail', async () => {
  const item = record(1), owner = { origin: 'random' }, h = setup({ selectedRecord: item, detailContext: owner,
    loadHistory: async () => { throw new Error('history offline'); }, singleGalleryDeleted: async () => { throw new Error('single offline'); } });
  h.ctx.openHistoryDelete(item);
  await h.ctx.confirmHistoryDelete({ preventDefault() {} });
  assert.equal(h.state.record, null); assert.equal(h.ctx.selectedRecord, null); assert.equal(h.get('detailDialog').open, false);
  assert.equal(h.get('historyDeleteSubmit').disabled, true);
  assert.match(h.notices.at(-1), /已删除作品记录，原文件保留/);
  assert.match(h.notices.at(-1), /history offline/);
  assert.equal(h.calls[0].url, '/api/history/1?mode=record');
  await h.ctx.confirmHistoryDelete({ preventDefault() {} }); assert.equal(h.calls.length, 1);
});



test('request errors retain structured deletion outcomes for the confirmation UI', async () => {
  const payload = { deleted: false, results: [{ id: 1, file_deleted: true, record_deleted: false }] };
  const ctx = vm.createContext({ fetch: async () => ({ ok: false, status: 409, json: async () => payload }) });
  install(ctx, app, 'request');
  await assert.rejects(ctx.request('/api/history/1?mode=file', { method: 'DELETE' }), error => error.status === 409 && error.payload === payload);
});
