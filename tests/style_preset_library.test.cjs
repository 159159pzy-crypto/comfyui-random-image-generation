const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/style-preset-manager.js'), 'utf8');
const { filterItems, normalizedInclude } = require('../static/style-preset-manager.js');
function production(name) {
  const match = new RegExp(`  (?:async )?function ${name}\\(`).exec(source);
  assert.ok(match, name); return source.slice(match.index, source.indexOf('\n  }', match.index) + 4);
}
function harness(withMotion = false) {
  let time = 0, frameId = 0; const frames = new Map();
  const document = { activeElement: null, hidden: false, addEventListener() {} };
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.children = []; this.parentElement = null; this.dataset = {}; this.attributes = new Map(); this.handlers = new Map();
      this.hidden = false; this.disabled = false; this.className = ''; this.style = { removeProperty(name) { delete this[name]; } }; this.scrollTop = 0; this._text = '';
      this.classList = { contains: name => this.className.split(' ').includes(name), toggle: (name, on) => { const names = new Set(this.className.split(' ').filter(Boolean)); on ? names.add(name) : names.delete(name); this.className = [...names].join(' '); } };
    }
    get isConnected() { return this === document.body || !!this.parentElement?.isConnected; }
    get firstElementChild() { return this.children[0] || null; }
    get nextElementSibling() { const siblings = this.parentElement?.children || []; return siblings[siblings.indexOf(this) + 1] || null; }
    get textContent() { return this.children.length ? this.children.map(child => child.textContent).join('') : this._text; }
    set textContent(value) { for (const child of [...this.children]) child.remove(); this._text = value; }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    closest(selector) { for (let node = this; node; node = node.parentElement) if (selector === '[hidden]' ? node.hidden : selector.startsWith('.') && node.classList.contains(selector.slice(1))) return node; return null; }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); }
    append(...nodes) { nodes.forEach(node => this.insertBefore(node, null)); }
    insertBefore(node, anchor) {
      if (node === anchor) return;
      node.remove(); const index = anchor === null ? this.children.length : this.children.indexOf(anchor); assert.ok(index >= 0);
      this.children.splice(index, 0, node); node.parentElement = this;
    }
    remove() {
      if (!this.parentElement) return;
      if (this.contains(document.activeElement)) document.activeElement = document.body;
      this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); this.parentElement = null;
    }
    focus() { if (!this.disabled && this.isConnected && !this.closest('[hidden]')) document.activeElement = this; }
    addEventListener(name, listener) { this.handlers.set(name, [...this.handlers.get(name) || [], listener]); }
    click() { for (const listener of this.handlers.get('click') || []) listener({ detail: 0, target: this }); }
    getBoundingClientRect() {
      if (!this.classList.contains('spm-card')) return { left: 0, top: 0, right: 600, bottom: 600, width: 600, height: 600 };
      const index = this.parentElement.children.indexOf(this), values = /translate3d\(([-\d.]+)px, ([-\d.]+)px/.exec(this.style.transform || '');
      const left = (index % 2) * 300 + Number(values?.[1] || 0), top = Math.floor(index / 2) * 100 + Number(values?.[2] || 0);
      return { left, top, right: left + 280, bottom: top + 80, width: 280, height: 80 };
    }
  }
  const h = (tag, className, value) => { const node = new Element(tag); node.className = className || ''; if (value !== undefined) node.textContent = value; return node; };
  document.body = h('body'); document.documentElement = h('html'); document.activeElement = document.body;
  const button = (text, classes, action, label) => { const node = h('button', classes, text); if (action) node.addEventListener('click', action); if (label) node.setAttribute('aria-label', label); return node; };
  const ui = { cards: new Map(), viewButtons: new Map([['cards', button('')], ['list', button('')]]) };
  for (const key of ['grid', 'scroll', 'subtitle', 'results', 'search', 'selectToggle', 'notice', 'readNotice', 'readStatus', 'retryRead', 'batchbar']) ui[key] = h('div');
  ui.batchbar.hidden = true; ui.scroll.append(ui.grid); document.body.append(ui.scroll, ui.batchbar, ui.search, ui.readNotice); ui.readNotice.append(ui.readStatus, ui.retryRead);
  const state = { items: [], groups: [], query: '', tags: [], collection: 'all', sort: 'updated', profile: 'anima', view: 'cards', selected: new Set(), selecting: false,
    applying: new Map(), applyErrors: new Map(), pendingFavorites: new Map(), batchWrites: new Map(), busy: new Set(), loading: false, readError: '', managerVisible: true, epoch: 1, read: 0 };
  const edited = [];
  const ctx = vm.createContext({ document, state, ui, h, button, icon: name => h('span', '', name), filterItems, normalizedInclude,
    includeLabels: { size: '尺寸', repair: '放大修复', img2img: '图生图', affixes: '前后缀' }, writable: () => true, profile: () => state.profile, profileText: () => 'Anima',
    edit: item => edited.push(item), toggleFavorite() {}, create() {}, openBatch() {}, closeManager: () => { state.managerVisible = false; },
    error(node, message = '') { node.textContent = message; node.hidden = !message; }, performance: { now: () => time },
    matchMedia: () => ({ matches: false, addEventListener() {} }), requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; }, cancelAnimationFrame(id) { frames.delete(id); },
  });
  if (withMotion) vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../static/motion.js'), 'utf8'), ctx);
  vm.runInContext('function setText(node, value) { if (node.textContent !== value) node.textContent = value; }\n' +
    ['reconcileChildren', 'summary', 'imageNode', 'makeCard', 'updateCard', 'applyCard', 'renderGrid', 'renderBatchbar'].map(production).join('\n'), ctx);
  const step = () => { time += 1000 / 60; const pending = [...frames.values()]; frames.clear(); for (const callback of pending) callback(time); };
  const drain = () => { let remaining = 300; while (frames.size && remaining--) step(); assert.ok(remaining > 0); };
  return { ctx, state, ui, document, edited, step, drain };
}
const item = (id, extra = {}) => ({ id, name: id, favorite: false, tags: [], updated_at: id, settings: { steps: 30, cfg: 4 }, ...extra });

test('refresh, search, metadata changes and view switching preserve card and image identities', () => {
  const h = harness(); h.state.items = [item('a'), item('b')]; h.ctx.renderGrid();
  const entry = h.ui.cards.get('a'), image = entry.cover, primary = entry.primary;
  primary.focus(); h.ui.scroll.scrollTop = 120;
  h.state.items = [item('a', { name: 'Updated', tags: ['Soft'] }), item('b')]; h.ctx.renderGrid();
  assert.equal(h.ui.cards.get('a'), entry); assert.equal(entry.cover, image); assert.equal(h.document.activeElement, primary); assert.equal(h.ui.scroll.scrollTop, 120);
  primary.click(); assert.equal(h.edited[0].name, 'Updated', 'handlers use the refreshed record');
  h.state.query = 'b'; h.ctx.renderGrid(); h.state.query = ''; h.ctx.renderGrid();
  assert.equal(h.ui.cards.get('a').primary, primary); assert.equal(entry.cover, image);
  h.state.view = 'list'; h.ctx.renderGrid(); assert.equal(h.ui.grid.dataset.view, 'list'); assert.equal(h.ui.viewButtons.get('list').getAttribute('aria-pressed'), 'true');
  assert.equal(h.ui.cards.get('a').cover, image);
});

test('reordering retains focused controls; deleting the focused record moves to its neighbor', () => {
  const h = harness(); h.state.items = [item('a'), item('b'), item('c')]; h.ctx.renderGrid();
  const entry = h.ui.cards.get('a'); entry.primary.focus(); h.state.items[0].updated_at = 'z'; h.ctx.renderGrid();
  assert.equal(h.ui.grid.firstElementChild, entry.card); assert.equal(h.document.activeElement, entry.primary);
  h.state.items = h.state.items.filter(value => value.id !== 'a'); h.ctx.renderGrid();
  assert.equal(h.document.activeElement, h.ui.cards.get('c').primary);
  h.state.items = []; h.ctx.renderGrid(); assert.equal(h.document.activeElement, h.ui.search);
});

test('selection exposes pressed state while batch controls retain keyboard focus', () => {
  const h = harness(); h.state.items = [item('a'), item('b')]; h.state.selecting = true; h.ctx.renderGrid();
  h.ui.selectAll.focus(); h.ui.selectAll.click();
  assert.equal(h.document.activeElement, h.ui.selectAll); assert.equal(h.ui.cards.get('a').primary.getAttribute('aria-pressed'), 'true');
  assert.equal(h.ui.batchCount.textContent, '已选择 2 项');
  h.ui.clearSelection.focus(); h.ui.clearSelection.click();
  assert.equal(h.document.activeElement, h.ui.clearSelection); assert.equal(h.ui.cards.get('a').primary.getAttribute('aria-pressed'), 'false');
});

test('read errors do not impersonate an empty library and retain cached cards', () => {
  const h = harness(); h.state.readError = 'offline'; h.ctx.renderGrid();
  assert.equal(h.ui.grid.children.length, 0); assert.equal(h.ui.readNotice.hidden, false); assert.equal(h.ui.retryRead.hidden, false);
  h.state.items = [item('a')]; h.ctx.renderGrid(); const card = h.ui.cards.get('a').card;
  h.state.loading = true; h.state.readError = ''; h.ctx.renderGrid();
  assert.equal(h.ui.grid.firstElementChild, card); assert.equal(h.ui.grid.getAttribute('aria-busy'), 'true');
  assert.equal(h.ui.readNotice.hidden, true, 'background loading must not insert a banner above the grid');
  assert.equal(h.ui.results.textContent, '1 个预设 · 正在刷新…');
});

test('apply is single flight per card and exposes retryable local errors', async () => {
  const h = harness(); h.state.items = [item('a')]; h.ctx.renderGrid(); const entry = h.ui.cards.get('a');
  let resolve, calls = 0, hooks;
  h.ctx.applyStylePreset = (_item, options) => { calls++; hooks = options; return new Promise(done => { resolve = done; }); };
  const pending = h.ctx.applyCard(entry); await h.ctx.applyCard(entry);
  assert.equal(calls, 1); assert.equal(entry.apply.disabled, true); assert.equal(entry.apply.getAttribute('aria-busy'), 'true');
  hooks.onError(new Error('missing model')); resolve(false); await pending;
  assert.equal(entry.apply.disabled, false); assert.match(entry.failure.textContent, /missing model/); assert.equal(entry.failure.hidden, false);
});

test('late apply completion settles current busy state without reporting errors or unlocking a newer request', async () => {
  const h = harness(); h.state.items = [item('a')]; h.ctx.renderGrid(); const entry = h.ui.cards.get('a'), requests = [];
  h.ctx.applyStylePreset = (_item, hooks) => new Promise(resolve => requests.push({ hooks, resolve }));
  const first = h.ctx.applyCard(entry); h.state.epoch++; h.state.applying.clear();
  const next = h.ctx.applyCard(entry);
  requests[0].hooks.onError(new Error('old failure')); requests[0].resolve(false); await first;
  assert.equal(entry.apply.disabled, true); assert.equal(entry.failure.hidden, true);
  h.state.epoch++; requests[1].resolve(false); await next;
  assert.equal(entry.apply.disabled, false); assert.equal(entry.apply.getAttribute('aria-busy'), 'false');
  assert.equal(h.state.managerVisible, true);
});

test('rapid and repeated reorder motion starts at the presented position and preserves velocity', () => {
  const h = harness(true); h.state.items = [item('a'), item('b')]; h.ctx.renderGrid();
  const entry = h.ui.cards.get('a'); h.state.items[0].updated_at = 'z'; h.ctx.renderGrid({ animate: true }); h.step(); h.step();
  const before = entry.card.getBoundingClientRect(), motion = h.ctx.StudioMotion.snapshot(entry.card, 'spm-reorder');
  assert.equal(motion.active, true);
  h.state.items[0].updated_at = 'a'; h.ctx.renderGrid({ animate: true });
  const after = entry.card.getBoundingClientRect(), reversed = h.ctx.StudioMotion.snapshot(entry.card, 'spm-reorder');
  assert.ok(Math.abs(before.left - after.left) < .001); assert.equal(reversed.velocity.x, motion.velocity.x); h.drain();
  h.state.items[0].updated_at = 'z'; h.ctx.renderGrid({ animate: true });
  assert.equal(h.ctx.StudioMotion.snapshot(entry.card, 'spm-reorder').active, true, 'settled owners must animate a second reorder'); h.drain();
  assert.equal(entry.card.style.transform, undefined);
});

test('a committed change refreshes the library even when the homepage refresh fails', async () => {
  const calls = [], ctx = vm.createContext({ state: { epoch: 1 }, notify() {}, profile: () => 'anima', profileText: () => 'Anima', ui: {},
    loadStylePresets: async options => { calls.push(['home', options.refreshManager]); throw new Error('homepage offline'); },
    refresh: async () => { calls.push(['manager']); } });
  vm.runInContext(production('changed'), ctx);
  await ctx.changed('anima', 'saved'); assert.deepEqual(calls, [['home', false], ['manager']]);
});

test('favorite and batch completions unlock a reopened library without putting old errors in it', async () => {
  for (const action of ['favorite', 'batch']) for (const fail of [false, true]) {
    let resolve, reject; const renders = [], errors = [], notices = [];
    const record = item('a'), state = { profile: 'anima', epoch: 1, read: 1, loading: false, busy: new Set(), pendingFavorites: new Map(), batchWrites: new Map(), selected: new Set(['a']), items: [record], managerVisible: true };
    const render = () => renders.push(action === 'favorite' ? state.busy.has('a') : state.batchWrites.has('anima'));
    const ctx = vm.createContext({ state, ui: { notice: {} }, writable: () => true, profile: () => 'anima', profileText: () => 'Anima',
      render, renderGrid: render, request: () => new Promise((yes, no) => { resolve = yes; reject = no; }), changed: async () => render(),
      notify: message => notices.push(message), error: (_node, message) => errors.push(message) });
    vm.runInContext(production(action === 'favorite' ? 'toggleFavorite' : 'openBatch'), ctx);
    const pending = action === 'favorite' ? ctx.toggleFavorite(record) : ctx.openBatch('favorite');
    assert.equal(renders.at(-1), true); state.epoch++; // The same profile's library was closed and reopened.
    if (fail) reject(new Error('old write failed')); else resolve({});
    await pending;
    assert.equal(renders.at(-1), false, `${action} must refresh its reopened busy controls`);
    assert.deepEqual(errors, [], 'old errors must not be written into the new library');
    assert.equal(notices.length, fail ? 1 : 0);
  }
});

function favoriteHarness() {
  const h = harness(true), requests = [], renders = [];
  h.ui.manager = { open: true };
  h.ctx.AbortController = AbortController;
  h.ctx.renderProtection = () => {};
  h.ctx.render = options => { renders.push(options); h.ctx.renderGrid(options); };
  h.ctx.changed = async () => {};
  h.ctx.notify = () => {};
  h.ctx.request = (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
  vm.runInContext(production('toggleFavorite') + '\n' + production('refresh'), h.ctx);
  h.state.items = [item('a', { favorite: true, updated_at: '1' }), item('b', { favorite: true, updated_at: '2' }), item('c', { updated_at: '3' }), item('d', { updated_at: '4' })];
  h.ctx.renderGrid();
  const order = () => h.ui.grid.children.map(node => node.dataset.presetId);
  return { ...h, requests, renders, order };
}

test('favorite gives immediate star feedback and keeps the committed card in place', async () => {
  const h = favoriteHarness(), original = h.state.items[0], entry = h.ui.cards.get('a'), before = h.order();
  entry.star.focus(); h.ui.scroll.scrollTop = 120;
  const pending = h.ctx.toggleFavorite(original, { detail: 1 });
  await h.ctx.toggleFavorite(original, { detail: 1 });
  assert.equal(h.requests.length, 1, 'a pending write is single flight');
  assert.equal(original.favorite, true); assert.equal(original.updated_at, '1');
  assert.equal(entry.star.getAttribute('aria-pressed'), 'false'); assert.equal(entry.star.getAttribute('aria-busy'), 'true');
  assert.equal(entry.star.disabled, false, 'busy stars keep native focus'); assert.equal(entry.star.getAttribute('aria-disabled'), 'true');
  assert.deepEqual(h.order(), before); assert.equal(h.renders.some(options => options?.animate), false);
  const saved = { ...original, favorite: false, favorite_at: null };
  h.requests[0].resolve(saved); await pending;
  assert.equal(h.state.items[0], saved);
  assert.deepEqual(h.order(), before, 'unfavorite must not reorder the library');
  assert.equal(h.ctx.StudioMotion.snapshot(entry.card, 'spm-reorder')?.active ?? false, false);
  assert.equal(h.document.activeElement, entry.star); assert.equal(h.ui.scroll.scrollTop, 120);
  assert.equal(h.ui.cards.get('a'), entry); assert.equal(entry.star.disabled, false); assert.equal(entry.star.getAttribute('aria-busy'), 'false');
  h.drain();
});

test('favorite-only results stay put while saving and on failure', async () => {
  const h = favoriteHarness(); h.state.collection = 'favorite'; h.ctx.renderGrid();
  const before = h.order(), pending = h.ctx.toggleFavorite(h.state.items[0], { detail: 1 });
  assert.deepEqual(h.order(), before);
  h.requests[0].reject(new Error('write unavailable')); await pending;
  assert.deepEqual(h.order(), before); assert.equal(h.ui.cards.get('a').star.getAttribute('aria-pressed'), 'true');
  assert.match(h.ui.notice.textContent, /write unavailable/); assert.equal(h.renders.some(options => options?.animate), false);
});

test('a read cannot reorder a pending favorite before its write response', async () => {
  const h = favoriteHarness(), original = h.state.items[0], before = h.order();
  const pending = h.ctx.toggleFavorite(original, { detail: 1 }), read = h.ctx.refresh();
  const saved = { ...original, favorite: false, favorite_at: null };
  h.requests[1].resolve({ items: [...h.state.items.slice(1).map(value => ({ ...value, name: value.name + ' refreshed' })), saved], groups: [] }); await read;
  assert.equal(h.state.items.find(value => value.id === 'a'), original); assert.deepEqual(h.order(), before);
  assert.equal(h.state.items.find(value => value.id === 'b').name, 'b refreshed', 'unrelated records still refresh');
  assert.equal(h.ui.cards.get('a').star.getAttribute('aria-pressed'), 'false');
  h.requests[0].resolve(saved); await pending; h.drain();
  assert.deepEqual(h.order(), before);
});

test('success and failure invalidate reads started during a favorite write', async () => {
  for (const fail of [false, true]) {
    const h = favoriteHarness(), original = h.state.items[0], before = h.order();
    const pending = h.ctx.toggleFavorite(original, { detail: 1 }), read = h.ctx.refresh();
    const oldRead = h.requests[1], saved = { ...original, favorite: false, favorite_at: null };
    if (fail) h.requests[0].reject(new Error('write failed')); else h.requests[0].resolve(saved);
    await pending;
    assert.equal(oldRead.options.signal.aborted, true);
    oldRead.resolve({ items: [fail ? saved : original, ...h.state.items.slice(1)], groups: [] }); await read;
    assert.equal(h.state.items[0].favorite, fail); assert.equal(h.state.loading, false);
    assert.deepEqual(h.order(), before); h.drain();
  }
});

test('an old favorite response cannot replace or unlock a newer operation', async () => {
  const h = favoriteHarness(), original = h.state.items[0];
  const first = h.ctx.toggleFavorite(original, { detail: 1 });
  h.state.epoch++; h.state.pendingFavorites.clear(); h.state.busy.clear();
  const second = h.ctx.toggleFavorite(original, { detail: 1 }), newer = h.state.pendingFavorites.get(original.id);
  h.requests[0].resolve({ ...original, favorite: false, name: 'old session', favorite_at: null }); await first;
  assert.equal(h.state.items[0], original); assert.equal(h.state.pendingFavorites.get(original.id), newer);
  assert.equal(h.ui.cards.get('a').star.getAttribute('aria-disabled'), 'true');
  h.requests[1].resolve({ ...original, favorite: false, favorite_at: null }); await second; h.drain();
  assert.equal(h.ui.cards.get('a').star.getAttribute('aria-disabled'), 'false'); assert.equal(h.state.pendingFavorites.size, 0);
});

test('a late homepage refresh failure does not write into a new profile or dialog session', async () => {
  for (const change of ['profile', 'epoch']) {
    let reject, activeProfile = 'anima'; const errors = [], state = { epoch: 1 };
    const ctx = vm.createContext({ state, ui: { notice: {} }, notify() {}, profile: () => activeProfile, profileText: value => value,
      error: (_node, message) => errors.push(message), loadStylePresets: () => new Promise((_, no) => { reject = no; }), refresh: async () => {} });
    vm.runInContext(production('changed'), ctx);
    const pending = ctx.changed('anima', 'saved');
    if (change === 'profile') activeProfile = 'krea2'; else state.epoch++;
    reject(new Error('old error')); await pending; assert.deepEqual(errors, []);
  }
});
