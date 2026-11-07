const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function install(ctx, name) {
  let start = app.indexOf(`function ${name}(`);
  if (app.slice(start - 6, start) === 'async ') start -= 6;
  vm.runInContext(app.slice(start, app.indexOf('\n}', start) + 2), ctx);
}
function deferred() { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { resolve, promise }; }

test('an old source-filter response cannot replace the newest history query', async () => {
  let key = 'home:1:single', params = '&workbench=single';
  const pending = [], shown = [];
  const ctx = vm.createContext({ historyPage: 1, historyRequest: 0,
    historyQueryKey: () => key, historyQueryParams: () => params,
    request: url => { const item = deferred(); pending.push({ ...item, url }); return item.promise; },
    renderHistoryPage: data => shown.push(data), toast() {} });
  install(ctx, 'historyViewState'); install(ctx, 'loadHistory');
  const old = ctx.loadHistory(2);
  key = 'home:2:random'; params = '&workbench=random';
  const current = ctx.loadHistory(1);
  pending[1].resolve({ page: 1, items: [{ id: 10 }] }); await current;
  pending[0].resolve({ page: 2, items: [{ id: 99 }] }); await old;
  assert.equal(shown.length, 1); assert.equal(shown[0].items[0].id, 10);
  assert.match(pending[0].url, /workbench=single/); assert.match(pending[1].url, /workbench=random/);
});

test('a pending full-size neighbor read cannot poison another filter cache', async () => {
  let key = 'source-single'; const pending = deferred();
  const ctx = vm.createContext({ historyCache: new Map(), imagePreview: {}, historyQueryKey: () => key,
    historyQueryParams: () => '&workbench=single', request: () => pending.promise });
  install(ctx, 'historyViewState'); install(ctx, 'previewHistoryView'); install(ctx, 'cacheHistoryPage'); install(ctx, 'loadHistoryPageForPreview');
  const old = ctx.loadHistoryPageForPreview(2); key = 'source-random';
  pending.resolve({ page: 2, items: [{ id: 99 }] });
  assert.equal(await old, null); assert.equal(ctx.historyCache.size, 0);
});

test('removing the last item on a collection page loads the last remaining page', async () => {
  const requests = [], shown = [];
  const ctx = vm.createContext({ historyPage: 3, historyRequest: 0,
    historyQueryKey: () => 'home:collection', historyQueryParams: () => '&folder_id=portraits',
    request: async url => { requests.push(url); return requests.length === 1
      ? { page: 3, pages: 2, total: 48, items: [] }
      : { page: 2, pages: 2, total: 48, items: [{ id: 9 }] }; },
    renderHistoryPage: data => shown.push(data), toast() {} });
  install(ctx, 'historyViewState'); install(ctx, 'loadHistory');
  await ctx.loadHistory(3);
  assert.equal(requests.length, 2); assert.match(requests[1], /page=2.*folder_id=portraits/);
  assert.equal(shown.length, 1); assert.equal(shown[0].page, 2);
});

test('an image removed from the active collection cannot navigate into an unrelated page', () => {
  const view = { page: 2, pages: 5, items: [{ id: 1, folder_ids: ['portrait'] }],
    query: { folderId: 'portrait' }, cache: new Map([[1, { items: [{ id: 9, folder_ids: ['portrait'] }] }]]) };
  const ctx = vm.createContext({ imagePreview: {}, selectedRecord: { id: 9, folder_ids: [] },
    previewHistoryView: () => view });
  for (const name of ['previewRecordIndex', 'selectedHistoryLocation', 'previewNeighborAvailable', 'previewNeighbor']) install(ctx, name);
  assert.equal(ctx.selectedHistoryLocation().index, -1);
  assert.equal(ctx.previewNeighborAvailable(1), false); assert.equal(ctx.previewNeighborAvailable(-1), false);
  assert.equal(ctx.previewNeighbor(1), null);
});

test('one detail action group is exposed at a time and closes without hiding historical data', () => {
  let closes = 0, focusInPanel = null;
  const names = ['use', 'generate', 'more'];
  const buttons = names.map(name => ({ name, expanded: 'false', getAttribute() { return this.expanded; }, setAttribute(_, value) { this.expanded = value; }, focus() { this.focused = true; } }));
  const panels = names.map(name => ({ name, hidden: true, querySelector: () => ({ focus() { focusInPanel = name; } }) }));
  const ctx = vm.createContext({ ui: {}, historyDetailVisible: () => true, closeRegenerationDrawers() { closes++; },
    document: { querySelectorAll: selector => selector.includes('data-detail-action') ? buttons : panels,
      querySelector: selector => (selector.includes('data-detail-action') ? buttons : panels).find(item => selector.includes(`"${item.name}"`)) } });
  for (const name of ['setDetailActionPanelVisible', 'closeDetailActionPanels', 'toggleDetailActionPanel']) install(ctx, name);
  ctx.toggleDetailActionPanel('generate');
  assert.deepEqual(panels.map(panel => panel.hidden), [true, false, true]);
  assert.equal(focusInPanel, null, 'opening actions keeps the current reading focus');
  ctx.toggleDetailActionPanel('use');
  assert.deepEqual(panels.map(panel => panel.hidden), [false, true, true]);
  assert.equal(focusInPanel, null);
  assert.equal(ctx.closeDetailActionPanels({ restoreFocus: true }), true);
  assert.equal(buttons[0].focused, true); assert.ok(panels.every(panel => panel.hidden));
  assert.equal(closes, 2);
});

function installDismissal(ctx) {
  install(ctx, 'invalidateHistoryTransfer');
  for (const marker of ['ui.closeDialog.addEventListener("click",', 'ui.detailDialog.addEventListener("click",', 'ui.detailDialog.addEventListener("cancel",']) {
    const start = app.indexOf(marker);
    assert.ok(start >= 0, marker);
    vm.runInContext(app.slice(start, app.indexOf('\n});', start) + 4), ctx);
  }
}
function dismissalContext(extra = {}) {
  const events = {}, calls = [];
  const host = { open: true, addEventListener: (name, callback) => { events[name] = callback; } };
  const ctx = vm.createContext({
    ui: { detailDialog: host, closeDialog: { addEventListener: (_, callback) => { events.x = callback; } }, replayDrawer: { hidden: true }, variantDrawer: { hidden: true } },
    historyDetailState: { busy: false }, singleWorkbenchState: { historyTransferRequest: 0 },
    closeImagePreview: () => calls.push('preview'), closeRegenerationDrawers: () => calls.push('drawers'),
    closeDetailActionPanels: () => false, historyDetailVisible: () => true, historyDetailBack: () => false,
    // Keep the host open: its actual close event arrives after an animation.
    motionDialog: () => calls.push('close-intent'), ...extra,
  });
  installDismissal(ctx);
  return { ctx, events, calls, host };
}



test('a busy deletion consumes backdrop and Escape without dismissing or invalidating its owner', () => {
  for (const close of ['x', 'click', 'cancel']) {
    const h = dismissalContext({ historyDetailState: { busy: true }, historyDetailBack: () => { throw new Error('must not navigate during write'); } });
    const event = { target: h.host, prevented: false, preventDefault() { this.prevented = true; } };
    h.events[close](event);
    assert.deepEqual(h.calls, [], close); assert.equal(h.ctx.singleWorkbenchState.historyTransferRequest, 0);
    if (close !== 'x') assert.equal(event.prevented, true, 'must suppress native or generic backdrop dismissal');
  }
});

test('Escape in an internal panel ignores hidden detail drawers and returns only one level', () => {
  let backs = 0, actions = 0;
  const h = dismissalContext({ historyDetailVisible: () => false, historyDetailBack: () => { backs++; return true; }, closeDetailActionPanels: () => { actions++; return true; } });
  h.ctx.ui.replayDrawer.hidden = false;
  const event = { preventDefault() {} }; h.events.cancel(event);
  assert.equal(backs, 1); assert.equal(actions, 0); assert.deepEqual(h.calls, []);
  assert.equal(h.ctx.singleWorkbenchState.historyTransferRequest, 0);
});

test('Tab stays in the active detail view and excludes controls inside closed disclosures', () => {
  let focused = null;
  const host = { open: true };
  const element = name => ({ name, tabIndex: 0, parentElement: host, matches: () => false, getClientRects: () => [{}], closest: () => null, focus() { focused = name; } });
  const first = element('close'), last = element('advanced'), hiddenSummary = element('advanced-hidden');
  hiddenSummary.parentElement = { tagName: 'DETAILS', open: false, parentElement: host, querySelector: () => ({ contains: () => false }) };
  host.querySelectorAll = () => [first, last, hiddenSummary];
  const ctx = vm.createContext({ ui: { detailDialog: host }, document: { activeElement: last }, getComputedStyle: () => ({ visibility: 'visible' }) });
  install(ctx, 'containHistoryDetailFocus'); let prevented = false;
  ctx.containHistoryDetailFocus({ key: 'Tab', shiftKey: false, preventDefault() { prevented = true; } });
  assert.equal(prevented, true); assert.equal(focused, 'close');
  ctx.document.activeElement = first;
  ctx.containHistoryDetailFocus({ key: 'Tab', shiftKey: true, preventDefault() {} });
  assert.equal(focused, 'advanced');
});

test('the detail focus boundary respects Tab already handled by its collection popover', () => {
  const ctx = vm.createContext({ ui: { detailDialog: { open: true, querySelectorAll() { throw new Error('nested focus was already handled'); } } } });
  install(ctx, 'containHistoryDetailFocus');
  ctx.containHistoryDetailFocus({ key: 'Tab', defaultPrevented: true });
});

test('Escape ignores a drawer already exiting and returns the next active level', () => {
  let backs = 0;
  const h = dismissalContext({ historyDetailBack: () => { backs++; return true; } });
  h.ctx.ui.replayDrawer.hidden = false;
  h.ctx.ui.replayDrawer.dataset = { motionState: 'closing' };
  h.events.cancel({ preventDefault() {} });
  assert.equal(backs, 1); assert.deepEqual(h.calls, []);
});

test('clicking detail metadata folds all actions without restoring the old trigger focus', () => {
  const closes = [];
  const h = dismissalContext({ closeRegenerationDrawers: restoreFocus => closes.push(['generation', restoreFocus]),
    closeDetailActionPanels: options => closes.push(['more', options]) });
  h.events.click({ target: { closest: () => null } });
  assert.deepEqual(closes, [['generation', false], ['more', undefined]]);
  closes.length = 0;
  h.events.click({ target: { closest: () => ({}) } });
  assert.deepEqual(closes, [], 'interacting inside the fold leaves it open');
});

test('closing the last deleted record restores focus to the archive title when its thumbnail is gone', () => {
  let focused = null;
  const source = { focus(options) { focused = { target: this, preventScroll: options.preventScroll }; } };
  const ctx = vm.createContext({
    ui: {
      detailDialog: { open: false }, detailImage: { removeAttribute() {} },
      gallery: { querySelector: () => null },
      prevPage: { disabled: true, focus() { throw new Error('Cannot restore focus to a disabled pager'); } },
    },
    detailContext: { origin: 'random', returnFocus: { isConnected: false, focus() { throw new Error('Deleted thumbnail is detached'); } } },
    selectedRecord: null, regenerationRequest: 0, regenerationOptions: {},
    resetRevealImageFile() {}, closeImagePreview() {}, closeRegenerationDrawers() {}, resetHistoryDetail() {},
    document: { getElementById: id => id === "artworkHistoryTitle" ? source : null },
  });
  install(ctx, 'finishDetailClose');
  ctx.finishDetailClose();
  assert.equal(focused.target, source);
  assert.equal(focused.preventScroll, true);
  assert.equal(ctx.detailContext, null);
});
