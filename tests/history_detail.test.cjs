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
  const elements = new Map(), calls = [], notices = [], closes = [], dialogCalls = [];
  const document = { activeElement: null, querySelector: () => null, querySelectorAll: () => [] };
  const node = (id, props = {}) => {
    const classes = new Set(), events = {};
    const result = { id, hidden: false, inert: false, disabled: false, scrollTop: 0, textContent: '', value: '', dataset: {}, events,
      classList: { add: (...values) => values.forEach(value => classes.add(value)), remove: (...values) => values.forEach(value => classes.delete(value)), toggle: (value, on) => on ? classes.add(value) : classes.delete(value), contains: value => classes.has(value) },
      style: { removeProperty(key) { delete this[key]; } }, isConnected: true,
      attributes: {}, setAttribute(name, value) { this.attributes[name] = String(value); }, getAttribute(name) { return this.attributes[name]; }, removeAttribute(name) { delete this.attributes[name]; }, addEventListener(name, handler) { events[name] = handler; },
      querySelector: () => null, querySelectorAll: () => [], closest: () => null, getClientRects: () => [{}], getBoundingClientRect: () => ({ left: 0, top: 0, right: 300, bottom: 300 }), focus() { document.activeElement = this; },
      ...props };
    elements.set(id, result); return result;
  };
  const get = id => elements.get(id) || node(id); document.getElementById = get;
  for (const id of ['detailDialog', 'historyDetailView', 'historyDetailTitle', 'historyDeleteDialog', 'historyDeleteRecord', 'historyDeleteFile', 'closeHistoryDelete', 'cancelHistoryDelete', 'historyDeleteResults', 'historyDeleteSummary']) node(id);
  get('detailDialog').open = true;
  get('historyDeleteDialog').open = false;
  get('historyDeleteRecord').dataset.historyDeleteMode = 'record';
  get('historyDeleteFile').dataset.historyDeleteMode = 'file';
  get('historyDeleteDialog').querySelectorAll = selector => selector.includes('data-history-delete-mode') ? [get('historyDeleteRecord'), get('historyDeleteFile')]
    : selector === 'button' ? [get('closeHistoryDelete'), get('historyDeleteRecord'), get('historyDeleteFile'), get('cancelHistoryDelete')] : [];
  document.querySelectorAll = selector => selector.includes('data-history-delete-mode') ? [get('historyDeleteRecord'), get('historyDeleteFile')] : [];
  const more = node('more'); document.querySelector = selector => selector.includes('data-detail-action="more"') ? more : null;
  const ctx = vm.createContext({ document, config: {}, historyItems: [], historyCache: new Map(), historyPage: 1, historyPages: 1, historyRequest: 0, historyDirty: false,
    ui: { gallery: { children: [], querySelector: () => null }, detailDialog: get('detailDialog'), historyCount: { textContent: '0 张图片' }, pageLabel: {}, prevPage: {}, nextPage: {}, emptyState: {} },
    toast: value => notices.push(value), selectedRecord: null, detailContext: null, closeImagePreview() {}, renderDetail() {},
    request: async (url, options) => { calls.push({ url, options }); return { deleted: true, results: [{ id: 1, record_deleted: true }] }; },
    motionDialog: (host, open, options = {}) => { closes.push(open); dialogCalls.push({ host, open, options }); host.open = open; options.onComplete?.(); }, loadHistory: async () => {}, singleGalleryDeleted: async () => null,
    ...extra });
  install(ctx, app, 'historyViewState'); vm.runInContext(source, ctx);
  const state = vm.runInContext('historyDetailState', ctx);
  return { ctx, elements, state, get, node, calls, notices, closes, dialogCalls, document, more };
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

test('deletion opens a separate dialog with two actions and keeps its exact detail owner', () => {
  const item = record(7), owner = { origin: 'random' }, h = setup({ selectedRecord: item, detailContext: owner });
  h.ctx.openHistoryDelete(item);
  assert.equal(h.state.record, item); assert.equal(h.state.owner, owner);
  assert.equal(h.get('historyDeleteDialog').open, true); assert.equal(h.get('detailDialog').open, true);
  assert.equal(h.get('historyDetailView').hidden, false); assert.equal(h.document.activeElement, h.get('cancelHistoryDelete'));
  assert.equal(h.get('historyDeleteRecord').disabled, false); assert.equal(h.get('historyDeleteFile').disabled, false);
  assert.equal(h.calls.length, 0, 'opening must never submit a default action');
  h.ctx.closeHistoryDelete();
  assert.equal(h.get('historyDeleteDialog').open, false); assert.equal(h.get('detailDialog').open, true);
  assert.equal(h.state.record, null); assert.equal(h.state.owner, null); assert.equal(h.document.activeElement, h.more);
});

test('readonly history cannot open a destructive action or submit a pending one', async () => {
  const h = setup({ config: { storage_status: { history: { read_only: true } } }, selectedRecord: record(1) });
  h.ctx.openHistoryDelete(h.ctx.selectedRecord); assert.equal(h.get('historyDeleteDialog').open, false);
  h.state.record = h.ctx.selectedRecord;
  await h.ctx.confirmHistoryDelete('record');
  assert.equal(h.calls.length, 0);
});

test('a failed permanent deletion reports partial file removal and retries the same single record', async () => {
  const item = record(7), owner = { origin: 'random' }, h = setup({ selectedRecord: item, detailContext: owner });
  h.ctx.openHistoryDelete(item);
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
  await h.ctx.confirmHistoryDelete('file');
  assert.equal(h.state.record, item); assert.equal(h.get('historyDeleteDialog').open, true); assert.equal(h.state.busy, false);
  assert.equal(h.get('historyDeleteRecord').disabled, false); assert.equal(h.get('historyDeleteFile').disabled, false);
  assert.match(h.get('historyDeleteResults').textContent, /原(?:图|文件)已删除.*记录(?:未删除|仍保留)/);
  assert.match(h.get('historyDeleteResults').textContent, /数据库提交失败/);
  await h.ctx.confirmHistoryDelete('record');
  assert.equal(h.calls.length, 2);
  assert.deepEqual(h.calls.map(call => call.url), ['/api/history/7?mode=file', '/api/history/7?mode=record']);
  assert.ok(h.calls.every(call => call.options.method === 'DELETE'));
  assert.ok(h.calls.every(call => !call.options.body));
  assert.equal(h.state.record, null); assert.match(h.notices.at(-1), /记录及原图已删除/);
  assert.doesNotMatch(h.notices.at(-1), /原图保留/);
});

test('record retry remembers an original already missing during a failed file deletion', async () => {
  const item = record(8), h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
  h.ctx.openHistoryDelete(item); h.ctx.applyHistoryDeleted = async () => [];
  h.ctx.request = async () => {
    const error = new Error('请求失败 (409)'); error.status = 409;
    error.payload = { deleted: false, results: [{ id: 8, file_deleted: false, file_missing: true, record_deleted: false, error: '历史记录写入失败，记录未删除' }] };
    throw error;
  };
  await h.ctx.confirmHistoryDelete('file');
  assert.equal(h.state.record, item); assert.equal(h.get('historyDeleteDialog').open, true);
  h.ctx.request = async () => ({ deleted: true, results: [{ id: 8, file_deleted: false, file_missing: false, record_deleted: true }] });
  await h.ctx.confirmHistoryDelete('record');
  assert.match(h.notices.at(-1), /记录已删除，原图原已不存在/); assert.doesNotMatch(h.notices.at(-1), /原图保留/);
  assert.equal(h.get('historyDeleteDialog').open, false);
  const newer = record(9); h.ctx.selectedRecord = newer; h.ctx.openHistoryDelete(newer);
  await h.ctx.confirmHistoryDelete('record');
  assert.match(h.notices.at(-1), /记录已删除，原图保留/);
});

test('cancelling a partial deletion does not carry its original-file result into another work', async () => {
  const item = record(7), h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
  h.ctx.openHistoryDelete(item); h.ctx.applyHistoryDeleted = async () => [];
  h.ctx.request = async () => {
    const error = new Error('请求失败 (409)'); error.status = 409;
    error.payload = { deleted: false, results: [{ id: 7, file_deleted: true, record_deleted: false, error: '数据库提交失败' }] };
    throw error;
  };
  await h.ctx.confirmHistoryDelete('file'); h.ctx.closeHistoryDelete();
  const newer = record(9); h.ctx.selectedRecord = newer; h.ctx.openHistoryDelete(newer);
  h.ctx.request = async () => ({ deleted: true, results: [{ id: 9, file_deleted: false, record_deleted: true }] });
  await h.ctx.confirmHistoryDelete('record');
  assert.match(h.notices.at(-1), /记录已删除，原图保留/); assert.doesNotMatch(h.notices.at(-1), /记录及原图已删除/);
});

test('pending deletion blocks duplicate submits and navigation until its result returns', async () => {
  const pending = deferred(), item = record(1), h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
  h.ctx.openHistoryDelete(item); h.ctx.request = () => { h.calls.push(true); return pending.promise; };
  h.ctx.applyHistoryDeleted = async () => [];
  const first = h.ctx.confirmHistoryDelete('record');
  await h.ctx.confirmHistoryDelete('file'); h.ctx.closeHistoryDelete();
  assert.equal(h.calls.length, 1); assert.equal(h.state.busy, true); assert.equal(h.get('historyDeleteDialog').open, true);
  for (const id of ['historyDeleteRecord', 'historyDeleteFile', 'closeHistoryDelete', 'cancelHistoryDelete']) assert.equal(h.get(id).disabled, true, id);
  pending.resolve({ deleted: true, results: [{ id: 1, record_deleted: true }] }); await first;
  assert.equal(h.state.busy, false); assert.equal(h.get('historyDeleteDialog').open, false);
});

test('each direct action submits its explicit mode and invalid modes never delete', async () => {
  for (const mode of ['record', 'file']) {
    const item = record(4), h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
    h.ctx.openHistoryDelete(item); h.ctx.applyHistoryDeleted = async () => [];
    await h.ctx.confirmHistoryDelete(); await h.ctx.confirmHistoryDelete('invalid');
    assert.equal(h.calls.length, 0);
    await h.ctx.confirmHistoryDelete(mode);
    assert.equal(h.calls.length, 1); assert.equal(h.calls[0].url, `/api/history/4?mode=${mode}`);
    const close = h.dialogCalls.find(call => call.host === h.get('historyDeleteDialog') && !call.open);
    assert.equal(close.options.restoreFocus, false); assert.equal(close.options.immediate, true);
  }
});

test('both visible action buttons execute immediately through their click handlers', async () => {
  for (const [id, mode] of [['historyDeleteRecord', 'record'], ['historyDeleteFile', 'file']]) {
    const item = record(4), h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
    h.ctx.initializeHistoryDetail(); h.ctx.openHistoryDelete(item); h.ctx.applyHistoryDeleted = async () => [];
    h.get(id).events.click();
    assert.equal(h.calls.length, 1); assert.equal(h.calls[0].url, `/api/history/4?mode=${mode}`);
    await new Promise(setImmediate);
    assert.equal(h.get('historyDeleteDialog').open, false); assert.equal(h.state.busy, false);
  }
});

test('cancel, close, Escape and outside clicks dismiss only the idle delete dialog', () => {
  const item = record(4), h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
  h.ctx.initializeHistoryDetail(); const dialog = h.get('historyDeleteDialog');
  const event = (x = -1) => ({ target: dialog, clientX: x, clientY: x, preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.propagationStopped = true; } });
  for (const dismiss of [() => h.get('cancelHistoryDelete').events.click(), () => h.get('closeHistoryDelete').events.click(),
    () => { const e = event(); dialog.events.cancel(e); assert.equal(e.defaultPrevented, true); },
    () => { const e = { ...event(), key: 'Escape' }; dialog.events.keydown(e); assert.equal(e.defaultPrevented, true); assert.equal(e.propagationStopped, true); },
    () => { dialog.events.pointerdown(event()); const e = event(); dialog.events.click(e); assert.equal(e.defaultPrevented, true); }]) {
    h.ctx.openHistoryDelete(item); dismiss();
    assert.equal(dialog.open, false); assert.equal(h.get('detailDialog').open, true); assert.equal(h.document.activeElement, h.more);
  }
  h.ctx.openHistoryDelete(item);
  dialog.events.pointerdown(event(10)); dialog.events.click(event());
  assert.equal(dialog.open, true, 'dragging from inside to the backdrop must not dismiss');
  h.state.busy = true; h.ctx.renderHistoryDelete();
  dialog.events.cancel(event()); h.get('cancelHistoryDelete').events.click(); h.get('closeHistoryDelete').events.click();
  for (let press = 0; press < 2; press++) {
    const escape = { ...event(), key: 'Escape' }; dialog.events.keydown(escape);
    assert.equal(escape.defaultPrevented, true); assert.equal(escape.propagationStopped, true); assert.equal(dialog.open, true);
  }
  dialog.events.pointerdown(event()); dialog.events.click(event());
  assert.equal(dialog.open, true); assert.equal(h.calls.length, 0);
});

test('delete focus stays within its controls and remains on status while actions are disabled', () => {
  const item = record(4), h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
  h.ctx.initializeHistoryDetail(); h.ctx.openHistoryDelete(item);
  const event = shiftKey => ({ key: 'Tab', shiftKey, preventDefault() { this.defaultPrevented = true; } });
  const forward = event(false); h.get('historyDeleteDialog').events.keydown(forward);
  assert.equal(forward.defaultPrevented, true); assert.equal(h.document.activeElement, h.get('closeHistoryDelete'));
  h.get('historyDeleteDialog').events.keydown(event(true)); assert.equal(h.document.activeElement, h.get('cancelHistoryDelete'));
  h.state.busy = true; h.ctx.renderHistoryDelete();
  const busy = event(false); h.get('historyDeleteDialog').events.keydown(busy);
  assert.equal(busy.defaultPrevented, true); assert.equal(h.document.activeElement, h.get('historyDeleteResults'));
});

test('an already removed record updates the UI without claiming that its original was deleted', async () => {
  const item = record(5), h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
  h.ctx.openHistoryDelete(item);
  h.ctx.request = async () => { const error = new Error('不存在'); error.status = 404; throw error; };
  const removed = []; h.ctx.applyHistoryDeleted = async value => { removed.push(value.id); return []; };
  await h.ctx.confirmHistoryDelete('file');
  assert.deepEqual(removed, [5]); assert.equal(h.get('historyDeleteDialog').open, false);
  assert.match(h.notices.at(-1), /记录.*不存在|记录.*已.*删除/);
  assert.doesNotMatch(h.notices.at(-1), /已删除作品及对应原文件|原图已删除|原文件已删除/);
});

test('success notices distinguish a missing original and preserve cleanup warnings', async () => {
  const item = record(6), h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
  h.ctx.openHistoryDelete(item); h.ctx.applyHistoryDeleted = async () => ['列表刷新失败'];
  h.ctx.request = async () => ({ deleted: true, results: [{ id: 6, record_deleted: true, file_deleted: false, file_missing: true, warning: '缩略图缓存清理失败' }] });
  await h.ctx.confirmHistoryDelete('file');
  assert.match(h.notices.at(-1), /原图.*不存在|原文件.*不存在/);
  assert.match(h.notices.at(-1), /缩略图缓存清理失败/); assert.match(h.notices.at(-1), /列表刷新失败/);
  assert.doesNotMatch(h.notices.at(-1), /已删除作品及对应原文件/);
});

test('partial failure retains the backend explanation once and keeps both retry actions available', async () => {
  const item = record(7), h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
  h.ctx.openHistoryDelete(item);
  h.ctx.request = async () => {
    const error = new Error('请求失败 (409)'); error.status = 409;
    error.payload = { deleted: false, results: [{ id: 7, file_deleted: true, record_deleted: false, error: '原文件已删除，但记录写入失败；记录仍保留，请重试删除记录' }] };
    throw error;
  };
  await h.ctx.confirmHistoryDelete('file');
  const message = h.get('historyDeleteResults').textContent;
  assert.equal(message.match(/原文件已删除/g)?.length, 1); assert.match(message, /记录仍保留/);
  assert.equal(h.get('historyDeleteDialog').open, true); assert.equal(h.state.busy, false);
  assert.equal(h.get('historyDeleteRecord').disabled, false); assert.equal(h.get('historyDeleteFile').disabled, false);
});

test('a forced reset keeps deletion busy and sends a stale failure to a notice instead of the new detail', async () => {
  const pending = deferred(), item = record(1), newer = record(2);
  const h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
  h.ctx.openHistoryDelete(item); const firstSession = h.state.session;
  h.ctx.request = () => pending.promise; const operation = h.ctx.confirmHistoryDelete('file');
  h.ctx.closeHistoryDelete({ force: true, restoreFocus: false, immediate: true });
  h.ctx.selectedRecord = newer; h.ctx.detailContext = { origin: 'single' }; h.ctx.openHistoryDelete(newer);
  assert.ok(h.state.session > firstSession);
  assert.equal(h.state.record, null); assert.equal(h.state.busy, true); assert.equal(h.get('historyDeleteDialog').open, false);
  pending.reject(new Error('旧删除错误')); await operation;
  assert.equal(h.ctx.selectedRecord, newer); assert.equal(h.get('detailDialog').open, true); assert.equal(h.state.busy, false);
  assert.doesNotMatch(h.get('historyDeleteResults').textContent, /旧删除错误/);
  assert.match(h.notices.at(-1), /旧删除错误/);
  h.ctx.openHistoryDelete(newer); assert.equal(h.state.record, newer); assert.equal(h.get('historyDeleteDialog').open, true);
  assert.equal(h.get('historyDeleteResults').textContent, '');
});

test('a stale success cleans up its record while keeping a newer detail intact', async () => {
  const pending = deferred(), item = record(1), newer = record(2), removed = [];
  const h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
  h.ctx.openHistoryDelete(item); h.ctx.request = () => pending.promise; h.ctx.applyHistoryDeleted = async value => { removed.push(value.id); return []; };
  const operation = h.ctx.confirmHistoryDelete('record');
  h.ctx.closeHistoryDelete({ force: true, restoreFocus: false, immediate: true });
  h.ctx.selectedRecord = newer; h.ctx.detailContext = { origin: 'single' }; h.ctx.openHistoryDelete(newer);
  assert.equal(h.get('historyDeleteDialog').open, false); assert.equal(h.state.busy, true);
  pending.resolve({ deleted: true, results: [{ id: 1, record_deleted: true }] }); await operation;
  assert.deepEqual(removed, [1]); assert.equal(h.ctx.selectedRecord, newer); assert.equal(h.get('detailDialog').open, true);
  assert.equal(h.state.busy, false); h.ctx.openHistoryDelete(newer);
  assert.equal(h.state.record, newer); assert.equal(h.get('historyDeleteRecord').disabled, false);
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
  await h.ctx.confirmHistoryDelete('record');
  assert.equal(h.state.record, null); assert.equal(h.ctx.selectedRecord, null); assert.equal(h.get('detailDialog').open, false);
  assert.equal(h.get('historyDeleteRecord').disabled, true); assert.equal(h.get('historyDeleteFile').disabled, true);
  assert.match(h.notices.at(-1), /记录已删除，原图保留/);
  assert.match(h.notices.at(-1), /history offline/);
  assert.equal(h.calls[0].url, '/api/history/1?mode=record');
  await h.ctx.confirmHistoryDelete('record'); assert.equal(h.calls.length, 1);
});


test('an unexpected post-delete refresh exception still closes the committed modal and reports success with a warning', async () => {
  const item = record(1), h = setup({ selectedRecord: item, detailContext: { origin: 'random' } });
  h.ctx.openHistoryDelete(item);
  h.ctx.applyHistoryDeleted = async () => { throw new Error('unexpected refresh error'); };
  await h.ctx.confirmHistoryDelete('record');
  assert.equal(h.state.busy, false); assert.equal(h.state.record, null); assert.equal(h.get('historyDeleteDialog').open, false);
  assert.match(h.notices.at(-1), /记录已删除，原图保留/); assert.match(h.notices.at(-1), /删除已完成，但界面刷新失败：unexpected refresh error/);
  assert.equal(h.get('historyDeleteRecord').disabled, true); assert.equal(h.get('historyDeleteFile').disabled, true);
  await h.ctx.confirmHistoryDelete('record'); assert.equal(h.calls.length, 1);
});


test('request errors retain structured deletion outcomes for the confirmation UI', async () => {
  const payload = { deleted: false, results: [{ id: 1, file_deleted: true, record_deleted: false }] };
  const ctx = vm.createContext({ fetch: async () => ({ ok: false, status: 409, json: async () => payload }) });
  install(ctx, app, 'request');
  await assert.rejects(ctx.request('/api/history/1?mode=file', { method: 'DELETE' }), error => error.status === 409 && error.payload === payload);
});

function batchSetup(records = [record(1), record(2)], extra = {}) {
  const h = setup(extra);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/artwork-favorites.js'), 'utf8'), h.ctx);
  h.artwork = vm.runInContext('artworkFavoritesState', h.ctx);
  h.get('detailDialog').open = false;
  h.ctx.artworkSetSelecting(true);
  for (const item of records) h.ctx.artworkActivateCard(item);
  h.removed = [];
  h.ctx.applyHistoryBatchDeleted = async items => {
    h.removed.push(...items.map(item => item.id));
    for (const item of items) h.artwork.selected.delete(String(item.id));
    return [];
  };
  return h;
}

test('batch confirmation snapshots cross-page and cross-source choices without submitting on open or cancel', () => {
  const first = record(1), second = { ...record(2), workbench: 'single' };
  const h = batchSetup([first, second], { historyItems: [second] });
  h.ctx.openHistoryBatchDelete();
  assert.deepEqual(plain(h.state.batch.records.map(item => item.id)), [1, 2]);
  assert.match(h.get('historyDeleteSummary').textContent, /2.*1/);
  assert.equal(h.calls.length, 0);
  assert.equal(h.get('historyDeleteDialog').open, true);
  assert.equal(h.document.activeElement, h.get('cancelHistoryDelete'));
  h.ctx.historyItems = [record(3)];
  assert.deepEqual(plain(h.state.batch.records.map(item => item.id)), [1, 2]);
  h.ctx.closeHistoryDelete();
  assert.equal(h.get('historyDeleteDialog').open, false);
  assert.deepEqual([...h.artwork.selected.keys()], ['1', '2']);
  assert.equal(h.artwork.selecting, true);
  assert.equal(h.document.activeElement, h.get('artworkBatchDelete'));
});

test('empty and protected batches cannot open or submit a deletion', async () => {
  for (const h of [batchSetup([]), batchSetup([record(1)], { config: { storage_status: { history: { read_only: true } } } })]) {
    h.ctx.openHistoryBatchDelete();
    await h.ctx.confirmHistoryDelete('record');
    assert.equal(h.get('historyDeleteDialog').open, false);
    assert.equal(h.calls.length, 0);
  }
  const h = batchSetup(); h.ctx.openHistoryBatchDelete();
  h.ctx.config.storage_status = { history: { read_only: true } };
  await h.ctx.confirmHistoryDelete('file');
  assert.equal(h.calls.length, 0);
  assert.equal(h.artwork.selected.size, 2);
});

test('batch actions submit one explicit mode and block duplicate requests and dismissal while pending', async () => {
  for (const mode of ['record', 'file']) {
    const h = batchSetup(), pending = deferred();
    h.ctx.request = (url, options) => { h.calls.push({ url, options }); return pending.promise; };
    h.ctx.openHistoryBatchDelete();
    await h.ctx.confirmHistoryDelete(); await h.ctx.confirmHistoryDelete('invalid');
    assert.equal(h.calls.length, 0);
    const operation = h.ctx.confirmHistoryDelete(mode);
    await h.ctx.confirmHistoryDelete(mode); h.ctx.closeHistoryDelete();
    assert.equal(h.calls.length, 1);
    assert.equal(h.calls[0].url, '/api/history/delete');
    assert.equal(h.calls[0].options.method, 'POST');
    assert.deepEqual(JSON.parse(h.calls[0].options.body), { image_ids: [1, 2], mode });
    assert.equal(h.state.busy, true); assert.equal(h.artwork.busy, true);
    assert.equal(h.get('historyDeleteDialog').open, true);
    for (const id of ['historyDeleteRecord', 'historyDeleteFile', 'closeHistoryDelete', 'cancelHistoryDelete']) assert.equal(h.get(id).disabled, true, id);
    pending.resolve({ results: [{ id: 1, record_deleted: true }, { id: 2, record_deleted: true }], deleted_count: 2, success_count: 2, failed_count: 0 });
    await operation;
    assert.deepEqual(h.removed, [1, 2]);
    assert.equal(h.get('historyDeleteDialog').open, false);
    assert.equal(h.state.busy, false); assert.equal(h.artwork.busy, false);
    assert.equal(h.artwork.selecting, false); assert.equal(h.artwork.selected.size, 0);
  }
});

test('mixed batch results remove successes and missing records while retrying only failed IDs', async () => {
  const h = batchSetup([record(1), record(2), record(3)]);
  let attempts = 0;
  h.ctx.request = async (url, options) => {
    h.calls.push({ url, options }); attempts++;
    return attempts === 1
      ? { results: [{ id: 1, record_deleted: true }, { id: 2, code: 'already_missing' }, { id: 3, code: 'image_delete_failed', error: '原图被占用' }], deleted_count: 1, success_count: 2, failed_count: 1 }
      : { results: [{ id: 3, record_deleted: true }], deleted_count: 1, success_count: 1, failed_count: 0 };
  };
  h.ctx.openHistoryBatchDelete(); await h.ctx.confirmHistoryDelete('file');
  assert.deepEqual(h.removed, [1, 2]);
  assert.deepEqual([...h.artwork.selected.keys()], ['3']);
  assert.deepEqual(plain(h.state.batch.records.map(item => item.id)), [3]);
  assert.equal(h.get('historyDeleteDialog').open, true);
  assert.match(h.get('historyDeleteResults').textContent, /3\.png/);
  assert.match(h.get('historyDeleteResults').textContent, /原图被占用/);
  await h.ctx.confirmHistoryDelete('record');
  assert.deepEqual(JSON.parse(h.calls[1].options.body), { image_ids: [3], mode: 'record' });
  assert.equal(h.artwork.selected.size, 0); assert.equal(h.get('historyDeleteDialog').open, false);
});

test('unreported batch results remain retryable instead of being counted as successes', async () => {
  const h = batchSetup(); h.ctx.openHistoryBatchDelete();
  h.ctx.request = async () => ({ results: [{ id: 1, record_deleted: true }], deleted_count: 1, success_count: 1, failed_count: 0 });
  await h.ctx.confirmHistoryDelete('record');
  assert.deepEqual(h.removed, [1]);
  assert.deepEqual([...h.artwork.selected.keys()], ['2']);
  assert.deepEqual(plain(h.state.batch.records.map(item => item.id)), [2]);
  assert.equal(h.get('historyDeleteDialog').open, true);
  assert.match(h.get('historyDeleteResults').textContent, /2\.png/);
});

test('network failures keep the exact selection and allow an already-missing retry to reconcile it', async () => {
  const h = batchSetup(); h.ctx.openHistoryBatchDelete();
  h.ctx.request = async () => { throw new Error('网络中断'); };
  await h.ctx.confirmHistoryDelete('record');
  assert.deepEqual([...h.artwork.selected.keys()], ['1', '2']);
  assert.equal(h.artwork.selecting, true); assert.equal(h.get('historyDeleteDialog').open, true);
  assert.equal(h.state.busy, false); assert.equal(h.artwork.busy, false);
  assert.match(h.get('historyDeleteResults').textContent, /网络中断/);
  h.ctx.request = async () => ({ results: [{ id: 1, code: 'already_missing' }, { id: 2, code: 'already_missing' }], deleted_count: 0, success_count: 2, failed_count: 0 });
  await h.ctx.confirmHistoryDelete('record');
  assert.deepEqual(h.removed, [1, 2]);
  assert.equal(h.artwork.selecting, false); assert.equal(h.get('historyDeleteDialog').open, false);
});

test('a record-only batch retry remembers originals removed before a database failure', async () => {
  const h = batchSetup([record(7)]); h.ctx.openHistoryBatchDelete();
  h.ctx.request = async () => ({ results: [{ id: 7, record_deleted: false, file_deleted: true, code: 'database_error', error: '提交失败' }], deleted_count: 0, success_count: 0, failed_count: 1, file_deleted_count: 1 });
  await h.ctx.confirmHistoryDelete('file');
  assert.match(h.get('historyDeleteResults').textContent, /原(?:图|文件)已删除/);
  assert.deepEqual([...h.artwork.selected.keys()], ['7']);
  h.ctx.request = async () => ({ results: [{ id: 7, record_deleted: true, file_deleted: false }], deleted_count: 1, success_count: 1, failed_count: 0, file_deleted_count: 0 });
  await h.ctx.confirmHistoryDelete('record');
  assert.match(h.notices.at(-1), /原图.*删除|删除.*原图/);
  assert.doesNotMatch(h.notices.at(-1), /原图保留/);
});

test('a successful batch still exits selection when its UI refresh throws', async () => {
  const h = batchSetup(); h.ctx.openHistoryBatchDelete();
  h.ctx.request = async () => ({ results: [{ id: 1, record_deleted: true }, { id: 2, record_deleted: true }], deleted_count: 2, success_count: 2, failed_count: 0 });
  h.ctx.applyHistoryBatchDeleted = async () => { throw new Error('刷新离线'); };
  await h.ctx.confirmHistoryDelete('record');
  assert.equal(h.get('historyDeleteDialog').open, false); assert.equal(h.artwork.selecting, false);
  assert.equal(h.artwork.selected.size, 0); assert.equal(h.artwork.busy, false);
  assert.match(h.notices.at(-1), /刷新离线/);
  assert.doesNotMatch(h.notices.at(-1), /删除失败/);
});

test('a stale batch response cleans up successes without closing a newer detail', async () => {
  const h = batchSetup(), pending = deferred(); h.ctx.openHistoryBatchDelete();
  h.ctx.request = () => pending.promise;
  const operation = h.ctx.confirmHistoryDelete('record');
  h.ctx.closeHistoryDelete({ force: true, restoreFocus: false, immediate: true });
  const newer = record(99); h.ctx.selectedRecord = newer; h.ctx.detailContext = { origin: 'single' };
  h.get('detailDialog').open = true;
  pending.resolve({ results: [{ id: 1, record_deleted: true }, { id: 2, record_deleted: true }], deleted_count: 2, success_count: 2, failed_count: 0 });
  await operation;
  assert.deepEqual(h.removed, [1, 2]); assert.equal(h.ctx.selectedRecord, newer);
  assert.equal(h.get('detailDialog').open, true); assert.equal(h.get('historyDeleteDialog').open, false);
  assert.equal(h.state.busy, false); assert.equal(h.artwork.busy, false);
});

test('batch view removal invalidates once and moves an emptied last page to the preceding page', () => {
  const first = record(25), second = record(26), h = setup({ historyItems: [first, second], historyPage: 2, historyPages: 2 });
  const removed = [];
  h.ctx.ui.gallery.children = [first, second].map(item => ({ dataset: { recordId: String(item.id) }, remove: () => removed.push(item.id) }));
  h.ctx.historyCache.set(2, { page: 2, total: 26, items: [first, second] });
  const view = h.ctx.historyViewState(), previousRequest = view.requestId;
  h.ctx.removeHistoryViewRecords(view, [first, second]);
  assert.deepEqual(removed, [25, 26]); assert.equal(view.items.length, 0);
  assert.equal(view.total, 24); assert.equal(view.page, 1); assert.equal(view.pages, 1);
  assert.equal(view.cache.size, 0); assert.equal(view.requestId, previousRequest + 1);
  assert.equal(h.ctx.ui.historyCount.textContent, '24 张图片');
});

test('committed batch updates folder state and reloads its archive only once even when refresh fails', async () => {
  const items = [record(1), record(2), record(3)], folderCalls = [], loads = [];
  const h = setup({ historyItems: items, artworkRecordsRemoved: async ids => { folderCalls.push([...ids]); return ['收藏计数离线']; },
    loadHistory: async page => { loads.push(page); throw new Error('列表离线'); } });
  h.ctx.historyViewState().total = 3;
  const warnings = await h.ctx.applyHistoryBatchDeleted(items);
  assert.deepEqual(folderCalls, [[1, 2, 3]]); assert.deepEqual(loads, [1]);
  assert.equal(h.ctx.historyItems.length, 0); assert.equal(h.ctx.historyViewState().total, 0);
  assert.equal(h.ctx.historyRequest, 1); assert.equal(h.ctx.historyDirty, true);
  assert.match(warnings.join('；'), /收藏计数离线/); assert.match(warnings.join('；'), /列表离线/);
});
