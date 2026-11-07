const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/artwork-favorites.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const record = (id, folder_ids = [], workbench = 'random') => ({ id, filename: `${id}.png`, folder_ids, workbench });
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function harness(extra = {}) {
  const requests = [], notices = [], loads = [], writes = [], events = new Map(); let invalidations = 0;
  const ctx = vm.createContext({ console, Map, Set, Date, Number, String,
    document: { getElementById: () => null, querySelectorAll: () => [], querySelector: () => null, activeElement: null,
      addEventListener: (name, handler) => { if (!events.has(name)) events.set(name, []); events.get(name).push(handler); } },
    window: { addEventListener() {} }, localStorage: { setItem: (...args) => writes.push(args) },
    historyDetailState: { homeQuery: { source: '', folderId: '' } }, historyItems: [], historyCache: new Map(), historyPage: 3,
    selectedRecord: null, singleGalleryState: { items: [], lastCompleted: null, viewerRecord: null },
    historyDetailWritable: () => true, singleWorkbenchActive: () => false,
    invalidateHistoryView: () => invalidations++, loadHistory: page => loads.push(page), toast: message => notices.push(message),
    request: async (url, options) => { requests.push({ url, ...(options || {}), json: options?.body ? JSON.parse(options.body) : undefined }); return { folders: [] }; },
    ...extra,
  });
  vm.runInContext(source, ctx);
  const state = vm.runInContext('artworkFavoritesState', ctx);
  return { ctx, state, requests, notices, loads, writes, events, invalidations: () => invalidations };
}
test('selection survives pagination and source changes and only explicit clicks add images', () => {
  const h = harness(), first = record(1), second = record(2, [], 'single');
  assert.equal(h.ctx.artworkActivateCard(first), false);
  h.ctx.artworkSetSelecting(true); h.ctx.artworkActivateCard(first);
  h.ctx.historyItems = [second]; h.ctx.historyDetailState.homeQuery.source = 'single'; h.ctx.artworkActivateCard(second);
  assert.deepEqual([...h.state.selected.keys()], ['1', '2']);
  h.ctx.artworkActivateCard(first); assert.deepEqual([...h.state.selected.keys()], ['2']);
  h.ctx.artworkSetSelecting(false); assert.equal(h.state.selected.size, 0); assert.equal(h.ctx.artworkSelectionActive(), false);
});
test('target chips toggle independently of the browsed folder in selection mode', () => {
  const h = harness(); h.ctx.historyDetailState.homeQuery.folderId = 'browsed';
  h.ctx.artworkSetSelecting(true); h.ctx.artworkSetFolder('one'); h.ctx.artworkSetFolder('two'); h.ctx.artworkSetFolder('one');
  assert.deepEqual([...h.state.targets], ['two']); assert.equal(h.ctx.artworkFolderQuery(), 'browsed'); assert.equal(h.loads.length, 0);
});
test('browsing a folder retains the source filter and resets the page exactly once', () => {
  const h = harness(); h.ctx.historyDetailState.homeQuery.source = 'single';
  h.ctx.artworkSetFolder('favorites'); h.ctx.artworkSetFolder('favorites');
  assert.deepEqual(plain(h.ctx.historyDetailState.homeQuery), { source: 'single', folderId: 'favorites' });
  assert.deepEqual(h.loads, [1]); assert.equal(h.invalidations(), 1);
});
test('one atomic batch request contains all selected images and all target folders', async () => {
  const h = harness(); h.ctx.artworkSetSelecting(true);
  h.ctx.artworkActivateCard(record(1)); h.ctx.artworkActivateCard(record(2));
  h.ctx.artworkSetFolder('one'); h.ctx.artworkSetFolder('two');
  h.ctx.request = async (url, options) => { h.requests.push({ url, json: JSON.parse(options.body) }); return { folders: [], items: [{ id: 1, folder_ids: ['one', 'two'] }, { id: 2, folder_ids: ['one', 'two'] }], changed_count: 4, updated_count: 2 }; };
  await h.ctx.artworkBatchMembership('add');
  assert.equal(h.requests.length, 1); assert.deepEqual(h.requests[0].json, { image_ids: [1, 2], folder_ids: ['one', 'two'], action: 'add' });
  assert.equal(h.state.selecting, false); assert.equal(h.state.selected.size, 0); assert.equal(h.ctx.historyPage, 3);
  assert.match(h.notices.at(-1), /2 张作品/); assert.equal(h.loads.length, 0);
});
test('a failed atomic batch retains image and target selections for retry', async () => {
  const h = harness({ request: async () => { throw new Error('图片已被删除'); } });
  h.ctx.artworkSetSelecting(true); h.ctx.artworkActivateCard(record(9)); h.ctx.artworkSetFolder('one');
  await h.ctx.artworkBatchMembership('add');
  assert.equal(h.state.selecting, true); assert.deepEqual([...h.state.selected.keys()], ['9']); assert.deepEqual([...h.state.targets], ['one']);
  assert.equal(h.state.busy, false); assert.match(h.notices.at(-1), /已保留本次选择/);
});
test('batch remove targets only the currently browsed folder, never other target chips', async () => {
  const h = harness(); h.ctx.historyDetailState.homeQuery.folderId = 'current';
  h.ctx.artworkSetSelecting(true); h.ctx.artworkActivateCard(record(1, ['current', 'keep'])); h.ctx.artworkSetFolder('elsewhere');
  h.ctx.request = async (url, options) => { h.requests.push(JSON.parse(options.body)); return { folders: [], items: [{ id: 1, folder_ids: ['keep'] }], changed_count: 1, updated_count: 1 }; };
  await h.ctx.artworkBatchMembership('remove');
  assert.deepEqual(h.requests[0], { image_ids: [1], folder_ids: ['current'], action: 'remove' }); assert.deepEqual(h.loads, [3]);
  assert.deepEqual(plain(h.state.memberships.get('1')), ['keep']);
});
test('readonly storage prevents mutations while browsing and existing memberships remain usable', async () => {
  const h = harness({ historyDetailWritable: () => false }); h.state.selecting = true;
  h.state.selected.set('1', record(1)); h.state.targets.add('one'); await h.ctx.artworkBatchMembership('add');
  assert.equal(h.requests.length, 0); h.ctx.artworkSetFolder('two'); assert.deepEqual([...h.state.targets], ['one', 'two']);
});
test('membership commits synchronize records, page caches, selected details and single viewer by ID', () => {
  const h = harness(), a = record(1), b = record(1), c = record(1), d = record(1), e = record(1);
  h.ctx.artworkRememberRecord(a); h.ctx.historyItems = [a]; h.ctx.historyCache.set(3, { items: [b] });
  h.ctx.selectedRecord = c; h.ctx.singleGalleryState = { items: [d], lastCompleted: d, viewerRecord: e };
  h.ctx.artworkApplyPayload({ folders: [{ id: 'one' }], items: [{ id: 1, folder_ids: ['one'] }] });
  for (const item of [a, b, c]) assert.deepEqual(plain(item.folder_ids), ['one']);
  assert.equal(h.invalidations(), 1); assert.equal(h.loads.length, 0); assert.equal(h.state.refreshNeeded, true);
});
test('stale history records cannot erase a membership just committed by the same session', () => {
  const h = harness(); h.ctx.artworkRememberRecord(record(5));
  h.ctx.artworkApplyPayload({ folders: [], items: [{ id: 5, folder_ids: ['one'] }] });
  const stale = record(5); h.ctx.artworkRememberRecord(stale); assert.deepEqual(plain(stale.folder_ids), ['one']);
});
test('single-star toggles remove only the chosen folder and remain tied to the original image', async () => {
  const pending = deferred(), h = harness(); h.ctx.artworkRememberRecord(record(1, ['one', 'two']));
  h.state.popover = { mode: 'members', recordId: '1', token: 10 };
  h.ctx.request = (url, options) => { h.requests.push(JSON.parse(options.body)); return pending.promise; };
  const save = h.ctx.artworkToggleMembership('one');
  h.state.popover = { mode: 'members', recordId: '2', token: 11 }; h.ctx.artworkRememberRecord(record(2));
  pending.resolve({ folders: [], items: [{ id: 1, folder_ids: ['two'] }] }); await save;
  assert.deepEqual(h.requests[0], { image_ids: [1], folder_ids: ['one'], action: 'remove' });
  assert.deepEqual(plain(h.state.memberships.get('1')), ['two']); assert.deepEqual(plain(h.state.memberships.get('2')), []);
});
test('filtered memberships refresh only after the anchored popover closes', async () => {
  const h = harness(); h.ctx.historyDetailState.homeQuery.folderId = 'one'; h.ctx.artworkRememberRecord(record(1, ['one']));
  h.state.popover = { mode: 'members', recordId: '1', token: 1, anchor: { setAttribute() {} } };
  h.ctx.request = async () => ({ folders: [], items: [{ id: 1, folder_ids: [] }] });
  await h.ctx.artworkToggleMembership('one'); assert.equal(h.loads.length, 0);
  assert.equal(h.ctx.closeArtworkPopovers({ restoreFocus: false }), true); assert.deepEqual(h.loads, [3]);
  assert.equal(h.ctx.closeArtworkPopovers(), false); assert.deepEqual(h.loads, [3]);
});
test('a delayed failed save cannot put its error into a different image menu', async () => {
  const pending = deferred(), h = harness({ request: () => pending.promise });
  h.ctx.artworkRememberRecord(record(1)); h.state.popover = { mode: 'members', recordId: '1', token: 1 };
  const save = h.ctx.artworkToggleMembership('one'); h.state.popover = { mode: 'members', recordId: '2', token: 2 };
  pending.reject(new Error('disk failed')); await save;
  assert.equal(h.state.menuError, ''); assert.match(h.notices[0], /disk failed/); assert.equal(h.state.busy, false);
});
test('a stale folder-list response cannot overwrite a subsequent mutation', async () => {
  const pending = deferred(), h = harness({ request: () => pending.promise });
  const old = h.ctx.artworkLoadFolders();
  h.ctx.artworkApplyPayload({ folders: [{ id: 'new', name: 'new' }] });
  pending.resolve({ folders: [{ id: 'old', name: 'old' }] }); await old;
  assert.equal(h.state.folders[0].id, 'new'); assert.equal(h.state.loading, false);
});
test('folder load failures are handled and can be retried without rejecting initialization', async () => {
  const h = harness({ request: async () => { throw new Error('offline'); } });
  await h.ctx.artworkLoadFolders(); assert.equal(h.state.loadError, 'offline'); assert.equal(h.state.loading, false);
  h.ctx.request = async () => ({ folders: [{ id: 'ok' }] }); await h.ctx.artworkLoadFolders();
  assert.equal(h.state.loadError, ''); assert.equal(h.state.folders[0].id, 'ok');
});
test('creating from a star uses the new folder identity, then adds the original image', async () => {
  const h = harness(); h.state.popover = { mode: 'members', recordId: '4', token: 1 }; h.state.newName = '  花园  ';
  h.ctx.request = async (url, options) => {
    h.requests.push({ url, json: JSON.parse(options.body) });
    return h.requests.length === 1 ? { folder: { id: 'garden' }, folders: [{ id: 'garden', name: '花园' }] } : { folders: [{ id: 'garden', name: '花园', count: 1 }], items: [{ id: 4, folder_ids: ['garden'] }] };
  };
  await h.ctx.artworkCreateFolder();
  assert.deepEqual(h.requests.map(item => item.url), ['/api/artwork-folders', '/api/artwork-folders/members']);
  assert.deepEqual(h.requests[0].json, { name: '花园' }); assert.deepEqual(h.requests[1].json, { image_ids: [4], folder_ids: ['garden'], action: 'add' });
});
test('the created folder remains available if its subsequent image membership request fails', async () => {
  const h = harness(); h.state.popover = { mode: 'members', recordId: '4', token: 1 }; h.state.newName = 'New';
  let calls = 0; h.ctx.request = async () => { if (++calls === 2) throw new Error('missing image'); return { folder: { id: 'new' }, folders: [{ id: 'new' }] }; };
  await h.ctx.artworkCreateFolder(); assert.equal(h.state.folders[0].id, 'new'); assert.equal(h.state.menuError, 'missing image'); assert.equal(h.state.busy, false);
});
test('deleting even the last folder removes memberships while keeping every image record', async () => {
  const h = harness(), item = record(1, ['last']); h.ctx.artworkRememberRecord(item); h.ctx.historyItems = [item];
  h.ctx.historyDetailState.homeQuery.folderId = 'last';
  h.ctx.request = async () => ({ folders: [], deleted: true, affected_image_ids: [1], items: [{ id: 1, folder_ids: [] }] });
  await h.ctx.artworkDeleteFolder('last');
  assert.equal(h.ctx.historyItems[0], item); assert.deepEqual(plain(item.folder_ids), []); assert.equal(h.ctx.artworkFolderQuery(), ''); assert.deepEqual(h.loads, [1]);
});
test('keyboard folder ordering sends the full order and keeps the unrelated folders', async () => {
  const h = harness(); h.state.folders = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
  h.ctx.request = async (url, options) => { const json = JSON.parse(options.body); h.requests.push({ url, json }); return { folders: json.folder_ids.map(id => ({ id })) }; };
  await h.ctx.artworkMoveFolder('b', -1);
  assert.deepEqual(h.requests[0], { url: '/api/artwork-folders/reorder', json: { folder_ids: ['b', 'a', 'c'] } });
  await h.ctx.artworkMoveFolder('b', -1); assert.equal(h.requests.length, 1);
});

test('deleting a folder keeps the committed memberships returned by the server', async () => {
  const h = harness();
  h.ctx.artworkRememberRecord(record(1, ['deleted']));
  h.ctx.request = async () => ({ folders: [{ id: 'concurrently-added', name: 'Other' }],
    items: [{ id: 1, folder_ids: ['concurrently-added'] }], deleted: true });
  await h.ctx.artworkDeleteFolder('deleted');
  assert.deepEqual(plain(h.state.memberships.get('1')), ['concurrently-added']);
});
test('failed sorting keeps the last server order and reports an actionable error', async () => {
  const h = harness({ request: async () => { throw new Error('disk full'); } }); h.state.folders = [{ id: 'a' }, { id: 'b' }];
  await h.ctx.artworkMoveFolder('b', -1); assert.deepEqual(plain(h.state.folders.map(item => item.id)), ['a', 'b']); assert.match(h.state.menuError, /排序未保存.*disk full/);
});
test('leaving the workbench clears transient selection without changing the archive filter', () => {
  const h = harness(); h.ctx.historyDetailState.homeQuery.folderId = 'one'; h.ctx.artworkSetSelecting(true); h.ctx.artworkActivateCard(record(1)); h.ctx.artworkSetFolder('two');
  h.ctx.artworkLeaveWorkbench(); assert.equal(h.state.selecting, false); assert.equal(h.state.selected.size, 0); assert.equal(h.state.targets.size, 0); assert.equal(h.ctx.artworkFolderQuery(), 'one');
});
test('menu Escape is consumed and restores its trigger without closing an enclosing detail dialog', () => {
  const h = harness(); let focused = 0, prevented = 0, stopped = 0;
  h.state.popover = { anchor: { isConnected: true, setAttribute() {}, closest() { return null; }, focus() { focused++; } } };
  h.ctx.artworkPopoverKeydown({ key: 'Escape', preventDefault() { prevented++; }, stopPropagation() { stopped++; } });
  assert.equal(prevented, 1); assert.equal(stopped, 1); assert.equal(focused, 1); assert.equal(h.state.popover, null);
});
test('document Escape also dismisses a menu after focus moves outside it', () => {
  const h = harness(); h.ctx.initializeArtworkFavorites();
  h.state.popover = { anchor: { setAttribute() {} } }; let prevented = false;
  for (const handler of h.events.get('keydown')) handler({ key: 'Escape', preventDefault() { prevented = true; }, stopPropagation() {} });
  assert.equal(prevented, true); assert.equal(h.state.popover, null);
});
test('a structured stale-object conflict removes only missing choices and leaves valid selections retryable', async () => {
  const failure = Object.assign(new Error('changed'), { payload: { code: 'artwork_collection_changed', missing_image_ids: [2], missing_folder_ids: ['gone'] } });
  const h = harness({ request: async url => { if (url.endsWith('/members')) throw failure; return { folders: [{ id: 'keep' }] }; } });
  h.ctx.artworkSetSelecting(true); h.ctx.artworkActivateCard(record(1)); h.ctx.artworkActivateCard(record(2));
  h.ctx.artworkSetFolder('keep'); h.ctx.artworkSetFolder('gone'); await h.ctx.artworkBatchMembership('add');
  assert.deepEqual([...h.state.selected.keys()], ['1']); assert.deepEqual([...h.state.targets], ['keep']);
  assert.equal(h.state.selecting, true); assert.match(h.notices[0], /1 张失效作品和 1 个失效收藏夹/);
  assert.equal(h.ctx.artworkRememberRecord(record(2)), null, 'late records must not reintroduce a deleted image');
});
test('external membership sync updates single-workbench details without generating a broadcast loop', async () => {
  const h = harness({ singleWorkbenchActive: () => true });
  const detail = record(7, ['old']), canvas = record(8), selected = record(9);
  h.ctx.selectedRecord = detail; h.ctx.historyItems = [canvas];
  h.state.selected.set('9', selected); h.state.externalPending = true;
  h.ctx.request = async (url, options) => { h.requests.push({ url, json: JSON.parse(options.body) }); return { folders: [{ id: 'new' }], items: [{ id: 7, folder_ids: ['new'] }, { id: 8, folder_ids: ['new'] }, { id: 9, folder_ids: ['new'] }], missing_image_ids: [] }; };
  await h.ctx.artworkSyncExternal();
  assert.equal(h.requests[0].url, '/api/artwork-folders/members/query'); assert.deepEqual(h.requests[0].json.image_ids.sort(), [7, 8, 9]);
  for (const item of [detail, canvas, selected]) assert.deepEqual(plain(item.folder_ids), ['new']);
  assert.equal(h.writes.length, 0); assert.equal(h.loads.length, 0); assert.equal(h.state.externalPending, false);
});
test('external synchronization waits for an active membership write and is consumed afterward', async () => {
  const pending = deferred(), h = harness(); let reads = 0;
  h.ctx.artworkRememberRecord(record(1)); h.state.popover = { mode: 'members', recordId: '1', token: 1 };
  h.ctx.request = async url => { if (url.endsWith('/query')) { reads++; return { folders: [], items: [{ id: 1, folder_ids: ['remote'] }] }; } return pending.promise; };
  const save = h.ctx.artworkToggleMembership('local'); h.state.externalPending = true; await h.ctx.artworkSyncExternal(); assert.equal(reads, 0);
  pending.resolve({ folders: [], items: [{ id: 1, folder_ids: ['local'] }] }); await save; await new Promise(resolve => setImmediate(resolve));
  assert.equal(reads, 1); assert.deepEqual(plain(h.state.memberships.get('1')), ['remote']); assert.equal(h.writes.length, 1);
});
test('failed external sync retains a retry flag and cannot mutate stale membership state', async () => {
  const h = harness({ request: async () => { throw new Error('offline'); } }); h.state.externalPending = true;
  h.ctx.artworkRememberRecord(record(1, ['old'])); h.state.popover = { mode: 'members', recordId: '1', token: 1 };
  await h.ctx.artworkSyncExternal(); assert.equal(h.state.externalPending, true); assert.equal(h.state.externalSyncing, false);
  let writes = 0; h.ctx.request = async url => { if (url.endsWith('/members')) writes++; throw new Error('offline'); };
  await h.ctx.artworkToggleMembership('old'); await new Promise(resolve => setImmediate(resolve)); assert.equal(writes, 0);
  h.ctx.request = async () => ({ folders: [], items: [{ id: 1, folder_ids: [] }] }); await h.ctx.artworkSyncExternal();
  assert.equal(h.state.externalPending, false); assert.deepEqual(plain(h.state.memberships.get('1')), []);
});
test('externally deleted images leave all visible caches and cannot be reintroduced by old records', async () => {
  const h = harness(), missing = record(1, ['one']), kept = record(2); let closed = 0;
  h.ctx.historyItems = [missing, kept]; h.ctx.historyCache.set(3, { items: [missing, kept] });
  h.ctx.singleGalleryState = { items: [missing, kept], total: 2, lastCompleted: missing, viewerRecord: missing }; h.ctx.selectedRecord = missing;
  h.ctx.closeImagePreview = () => closed++; h.state.selected.set('1', missing); h.state.externalPending = true;
  h.ctx.request = async () => ({ folders: [], items: [{ id: 2, folder_ids: [] }], missing_image_ids: [1] }); await h.ctx.artworkSyncExternal();
  assert.deepEqual(h.ctx.historyItems.map(item => item.id), [2]); assert.deepEqual(h.ctx.historyCache.get(3).items.map(item => item.id), [2]);

  assert.equal(h.ctx.selectedRecord, null); assert.equal(closed, 1); assert.equal(h.state.selected.size, 0); assert.equal(h.ctx.artworkRememberRecord(missing), null);
});

// A small DOM with browser-like focus loss on node removal catches bugs that
// state-only tests cannot: replacing a pressed row and moving a focused chip.
function fakeDocument() {
  const events = new Map();
  const doc = { activeElement: null, documentElement: { clientWidth: 1200 }, addEventListener(name, handler) { if (!events.has(name)) events.set(name, []); events.get(name).push(handler); },
    removeEventListener(name, handler) { events.set(name, (events.get(name) || []).filter(item => item !== handler)); }, events };
  class Element {
    constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.parentElement = null; this.dataset = {}; this.attrs = {}; this.style = { removeProperty(key) { delete this[key]; } }; this.hidden = false; this.inert = false; this.disabled = false; this.scrollTop = 0; this.handlers = new Map(); }
    get isConnected() { return this === doc.body || Boolean(this.parentElement?.isConnected); }
    get classList() { const node = this; return { contains: value => (node.className || '').split(' ').includes(value), add(...values) { node.className = [...new Set([...(node.className || '').split(' '), ...values])].join(' '); }, remove(...values) { node.className = (node.className || '').split(' ').filter(value => !values.includes(value)).join(' '); }, toggle(value, yes) { if (yes) this.add(value); else this.remove(value); } }; }
    setAttribute(name, value) { this.attrs[name] = String(value); if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value); }
    removeAttribute(name) { delete this.attrs[name]; }
    getAttribute(name) { return this.attrs[name]; }
    matches(selector) {
      if (selector.includes(',')) return selector.split(',').some(value => this.matches(value.trim()));
      if (selector === '[hidden]') return this.hidden; if (selector === '[inert]') return this.inert;
      if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
      const data = selector.match(/^\[data-([a-z-]+)\]$/); if (data) return Object.hasOwn(this.dataset, data[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase()));
      return this.tagName === selector.toUpperCase();
    }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    append(...children) { children.forEach(child => this.insertBefore(child, null)); }
    insertBefore(child, before) { child.remove(); const index = before ? this.children.indexOf(before) : this.children.length; this.children.splice(index, 0, child); child.parentElement = this; }
    remove() { if (this.contains(doc.activeElement)) doc.activeElement = doc.body; if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); this.parentElement = null; }
    replaceChildren(...children) { [...this.children].forEach(child => child.remove()); this.append(...children); }
    addEventListener(name, handler) { this.handlers.set(name, handler); }
    removeEventListener(name) { this.handlers.delete(name); }
    focus() { if (!this.disabled && this.isConnected) doc.activeElement = this; }
    getBoundingClientRect() { const offset = Number((this.style.transform || '').match(/translateY\(([-\d.]+)px\)/)?.[1] || 0); return { top: (this.parentElement?.children.indexOf(this) || 0) * 46 + offset, left: 0, right: 360, bottom: 46, width: 360, height: 46 }; }
    get nextElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) + 1] || null; }
  }
  doc.createElement = tag => new Element(tag); doc.body = doc.createElement('body'); doc.activeElement = doc.body;
  doc.getElementById = id => [doc.body, ...doc.body.querySelectorAll('*')].find(node => node.id === id) || null;
  doc.querySelectorAll = () => []; doc.querySelector = () => null;
  return doc;
}
test('rebuilding a busy membership menu restores logical row focus when its request completes', async () => {
  const document = fakeDocument(), h = harness({ document }), pending = deferred(); h.ctx.positionArtworkPopover = () => {};
  h.state.loaded = true; h.state.folders = [{ id: 'one', name: 'One' }]; h.ctx.artworkRememberRecord(record(1));
  const node = document.createElement('section'); document.body.append(node); h.state.popoverNode = node; h.state.popover = { mode: 'members', recordId: '1', token: 1 };
  h.ctx.artworkRenderPopover(); node.querySelector('[data-member-folder]').focus();
  h.ctx.request = () => pending.promise; const save = h.ctx.artworkToggleMembership('one');
  assert.equal(document.activeElement, document.body, 'disabled replacement rows cannot own focus while pending');
  pending.resolve({ folders: [{ id: 'one', name: 'One' }], items: [{ id: 1, folder_ids: ['one'] }] }); await save;
  assert.equal(document.activeElement.dataset.focusKey, 'member:one'); assert.equal(document.activeElement.disabled, false);
});
test('pointer release commits the reordered DOM even after the dragged row changes its parent slot', async () => {
  const document = fakeDocument(), h = harness({ document }); h.state.folders = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
  const list = document.createElement('div'); document.body.append(list);
  const rows = h.state.folders.map(folder => { const row = document.createElement('div'); row.dataset.folderId = folder.id; const handle = document.createElement('button'); row.append(handle); list.append(row); return row; });
  h.ctx.request = async (url, options) => { h.requests.push(JSON.parse(options.body)); return { folders: JSON.parse(options.body).folder_ids.map(id => ({ id })) }; };
  h.ctx.artworkDragStart({ button: 0, pointerId: 1, clientY: 20 }, 'a', rows[0], rows[0].children[0]);
  assert.ok(document.events.get('pointerup')?.length, 'release is owned by document, not a moved capture element');
  h.ctx.artworkDragMove({ pointerId: 1, clientY: 125, preventDefault() {} });
  assert.deepEqual(list.children.map(row => row.dataset.folderId), ['b', 'c', 'a']);
  for (const handler of [...document.events.get('pointerup')]) handler({ pointerId: 1 }); await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(h.requests[0], { folder_ids: ['b', 'c', 'a'] }); assert.equal(h.state.drag, null); assert.equal(document.events.get('pointerup').length, 0);
});
