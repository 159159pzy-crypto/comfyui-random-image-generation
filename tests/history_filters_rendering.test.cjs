const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, '../static', name), 'utf8');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const settle = () => new Promise(resolve => setImmediate(resolve));

// A small layout-aware DOM, with the production motion clock and production
// window planner. No renderer or lifecycle method under test is replaced.
function harness() {
  let time = 0, frameId = 0, created = 0, writes = 0, focusedMoves = 0;
  const frames = new Map(), ids = new Map(), listeners = new Map();
  const document = { hidden: false, activeElement: null, upperModal: false,
    addEventListener(name, fn) { const list = listeners.get(name) || []; list.push(fn); listeners.set(name, list); },
    getElementById: id => ids.get(id), querySelector: () => document.upperModal ? { open: true } : null,
    createElement: tag => new Element(tag),
  };
  class Element {
    constructor(tag) { created++; this.tagName = tag.toUpperCase(); this.nodeType = 1; this.children = []; this.parentElement = null; this.attributes = new Map(); this.dataset = {}; this.style = {}; this.hidden = false; this.inert = false; this.open = false; this.value = ''; this.className = ''; this.handlers = new Map(); this.scrollTop = 0; this._text = ''; this.disabled = false; this.clientTop = 0; }
    get classList() { return { contains: name => this.className.split(' ').includes(name), toggle: (name, on) => { const set = new Set(this.className.split(' ').filter(Boolean)); if (on ?? !set.has(name)) set.add(name); else set.delete(name); this.className = [...set].join(' '); }, add: name => this.classList.toggle(name, true), remove: name => this.classList.toggle(name, false) }; }
    set textContent(value) { writes++; this._text = String(value); for (const child of this.children) child.parentElement = null; this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    get firstElementChild() { return this.children[0] || null; }
    get lastElementChild() { return this.children.at(-1) || null; }
    get childNodes() { return this.children; }
    get nextElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) + 1] || null; }
    get previousElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) - 1] || null; }
    get isConnected() { return this === document.documentElement || Boolean(this.parentElement?.isConnected); }
    get clientHeight() { return this.id === 'historyFilterBody' ? 300 : this.height(); }
    get clientWidth() { return 400; }
    setAttribute(name, value) { writes++; this.attributes.set(name, String(value)); if (name === 'id') { this.id = value; ids.set(value, this); } }
    getAttribute(name) { return this.attributes.get(name); }
    removeAttribute(name) { writes++; this.attributes.delete(name); }
    hasAttribute(name) { return this.attributes.has(name); }
    append(...nodes) { for (const node of nodes) this.insertBefore(node, null); }
    insertBefore(node, next) { writes++; if (node.isConnected && node.contains(document.activeElement)) { document.activeElement = document.body; focusedMoves++; } node.remove(); const at = next ? this.children.indexOf(next) : this.children.length; this.children.splice(at, 0, node); node.parentElement = this; }
    remove() { if (this.parentElement) { writes++; const siblings = this.parentElement.children; siblings.splice(siblings.indexOf(this), 1); this.parentElement = null; } }
    contains(node) { for (let current = node; current; current = current.parentElement) if (current === this) return true; return false; }
    matches(selector) {
      if (selector === '[hidden]') return this.hidden;
      if (selector === '[inert]') return this.inert;
      if (selector === "[tabindex='0']") return this.getAttribute('tabindex') === '0';
      if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
      return this.tagName === selector.toUpperCase();
    }
    querySelectorAll(selector) {
      const direct = selector.startsWith(':scope > '), selectors = selector.replace(':scope > ', '').split(',').map(item => item.trim()), result = [];
      const visit = node => { for (const child of node.children) { if (selectors.some(item => child.matches(item))) result.push(child); if (!direct) visit(child); } }; visit(this); return result;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { for (let node = this; node; node = node.parentElement) if (selector.split(',').some(item => node.matches(item.trim()))) return node; return null; }
    addEventListener(name, fn) { this.handlers.set(name, fn); }
    focus() { document.activeElement = this; }
    getRootNode() { return document; }
    height() {
      if (this.hidden) return 0;
      if (this.style.height && this.style.height !== 'auto') return Number.parseFloat(this.style.height) || 0;
      if (this.tagName === 'SUMMARY') return this.tall ? 72 : 44;
      if (this.tagName === 'DETAILS') return 44 + (this.open ? this.children.slice(1).reduce((sum, child) => sum + child.height(), 0) : 0);
      if (this.tagName === 'INPUT' || this.tagName === 'BUTTON') return 36;
      return this.children.reduce((sum, child) => sum + child.height(), 0);
    }
    top() { if (!this.parentElement) return 0; const siblings = this.parentElement.children; return this.parentElement.top() + siblings.slice(0, siblings.indexOf(this)).reduce((sum, child) => sum + child.height(), 0) - this.parentElement.scrollTop; }
    getBoundingClientRect() { const top = this.top(), height = this.height(); return { top, bottom: top + height, left: 0, right: 400, width: 400, height }; }
    getClientRects() { for (let node = this; node; node = node.parentElement) if (node.hidden || (node.tagName === 'DETAILS' && !node.open && node !== this && !node.firstElementChild.contains(this))) return []; return this.isConnected ? [this.getBoundingClientRect()] : []; }
  }
  document.documentElement = new Element('html'); document.body = new Element('body'); document.documentElement.append(document.body); document.body.style.overflow = 'auto';
  const mount = (id, tag = 'div', parent = document.body) => { const element = new Element(tag); element.setAttribute('id', id); parent.append(element); return element; };
  const trigger = mount('openHistoryFilters', 'button'), drawer = mount('historyFilterDrawer', 'aside'); drawer.hidden = true; drawer.inert = true;
  for (const id of ['closeHistoryFilters', 'historyFilterClear', 'historyFilterShowResults', 'historyFilterRetry', 'historyFilterCatalogRetry']) mount(id, 'button', drawer);
  const input = mount('historyFilterSearch', 'input', drawer), conditions = mount('historyFilterConditions', 'div', drawer);
  mount('historyFilterFeedback', 'p', drawer); const body = mount('historyFilterBody', 'div', drawer);
  mount('historyFilterCatalogStatus', 'p', body); const pools = mount('historyFilterPools', 'div', body);
  mount('historyFilterResult', 'p', drawer);
  document.activeElement = trigger;
  const media = { matches: false, addEventListener() {} };
  const window = { matchMedia: () => media, addEventListener() {} };
  const requests = [];
  let catalog = { group_revision: 'r1', sections: [] }, responder = async url => url.includes('filter-catalog') ? catalog : { items: [], page: 1, has_more: false, group_revision: catalog.group_revision };
  const ctx = vm.createContext({ document, window, URLSearchParams, setTimeout, clearTimeout, console,
    localStorage: { getItem: () => null }, historyDetailState: { homeQuery: { source: '', q: '', contentFilters: { include: [], exclude: [] }, groupRevision: '' } },
    invalidateHistoryView() {}, loadHistory() {}, request: url => { requests.push(url); return responder(url); },
    performance: { now: () => time }, matchMedia: () => ({ matches: false, addEventListener() {} }),
    getComputedStyle: () => ({}), requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; }, cancelAnimationFrame(id) { frames.delete(id); },
  });
  for (const file of ['motion.js', 'history-filter-window.js', 'history-filters.js']) vm.runInContext(read(file), ctx);
  ctx.historyFiltersInit();
  function step(ms = 1000 / 60) { time += ms; const callbacks = [...frames.values()]; frames.clear(); for (const callback of callbacks) callback(time); }
  function drain() { let guard = 200; while (frames.size && guard--) step(); assert.ok(guard > 0, 'painting must settle'); }
  const state = () => vm.runInContext('historyFiltersState', ctx);
  const accept = async data => { if (state().catalogPromise) await state().catalogPromise; catalog = data; await ctx.historyFiltersLoadCatalog({ force: true }); };
  const open = () => { ctx.historyFiltersSetOpen(true); drain(); };
  const expand = id => { ctx.historyFiltersDisclosure(state().groupNodes.get(id), true); drain(); };
  const find = (kind, value) => [...state().index.values()].find(group => group.kind === kind && group.value === value)?.id;
  const emitClose = () => { document.upperModal = false; for (const listener of listeners.get('close') || []) listener({ target: { tagName: 'DIALOG' } }); drain(); };
  return { ctx, document, drawer, trigger, input, body, pools, conditions, requests, state, step, drain, accept, open, expand, find, emitClose,
    counts: () => ({ created, writes, focusedMoves, connected: document.documentElement.querySelectorAll('div,details,summary,span,input,button,p,aside').length }),
    respond: fn => { responder = fn; }, catalog: () => catalog };
}
function largeCatalog(revision = 'r1', count = 1963) {
  return { group_revision: revision, sections: [
    { id: 'character', groups: [], attributes: [{ kind: 'hair', label: '发色' }] },
    { id: 'pose', groups: [{ kind: 'heading', value: 'traits', label: '特征', children: Array.from({ length: count }, (_, index) => ({ kind: 'trait', value: `trait-${index}`, label: `特征 ${index}`, children: [] })) }] },
  ] };
}

test('full catalog indexes unloaded groups without constructing their controls', async () => {
  const h = harness(), initial = h.counts().connected;
  await h.accept(largeCatalog());
  assert.equal(h.state().knownGroups.size, 1963); assert.equal(h.counts().connected, initial);
  assert.equal(h.state().panels.size, 0); h.open();
  assert.ok(h.counts().connected < 100); assert.equal(h.state().panelNodes.size, 0);
  assert.equal(h.state().labels.get(JSON.stringify(['pose', 'trait', 'trait-1900'])), '特征 1900');
});

test('1963 sibling groups mount only a measured window and retain a focused row while scrolling', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.open(); h.expand('pool:pose'); h.expand(h.find('heading', 'traits'));
  let list = h.state().lists.get(h.find('heading', 'traits'));
  assert.ok(list.mounted.size <= 24); assert.equal(h.state().panelNodes.size, 0);
  const first = h.find('trait', 'trait-0'), focused = h.state().groupNodes.get(first).firstElementChild;
  focused.focus(); h.body.scrollTop = 20000; h.ctx.historyFiltersSchedulePaint(); h.drain();
  assert.equal(h.document.activeElement, focused); assert.ok(focused.isConnected);
  assert.ok(list.mounted.size <= 26); assert.ok([...list.mounted].some(id => id.includes('trait-4')));
  h.input.focus(); h.ctx.historyFiltersSchedulePaint(); h.drain();
  assert.equal(focused.isConnected, false); assert.equal(h.state().groupNodes.has(first), false);
  assert.ok(h.counts().connected < 300);
});

test('fold and drawer exits release DOM references only at the real motion completion', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.open(); h.expand('pool:pose'); h.expand(h.find('heading', 'traits'));
  const heading = h.state().groupNodes.get(h.find('heading', 'traits'));
  h.ctx.historyFiltersDisclosure(heading, false); h.step(100);
  assert.ok(h.state().lists.has(heading.filterId)); h.drain();
  assert.equal(h.state().lists.has(heading.filterId), false);
  h.ctx.historyFiltersSetOpen(false); h.step(100); assert.ok(h.state().groupNodes.size > 0); h.drain();
  assert.equal(h.state().groupNodes.size, 0); assert.equal(h.state().poolNodes.size, 0); assert.equal(h.state().panelNodes.size, 0);
  assert.equal(h.pools.children.length, 0); assert.equal(h.state().knownGroups.size, 1963);
});

test('reversing a fold or drawer close keeps the current tree and prevents stale disposal', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.open(); h.expand('pool:pose'); h.expand(h.find('heading', 'traits'));
  const heading = h.state().groupNodes.get(h.find('heading', 'traits'));
  h.ctx.historyFiltersDisclosure(heading, false); h.step(50); h.ctx.historyFiltersDisclosure(heading, true); h.drain();
  assert.equal(h.state().groupNodes.get(heading.filterId), heading); assert.ok(h.state().lists.has(heading.filterId));
  h.ctx.historyFiltersSetOpen(false); h.step(50); h.ctx.historyFiltersSetOpen(true); h.drain();
  assert.equal(h.state().groupNodes.get(heading.filterId), heading); assert.ok(heading.isConnected);
});

test('unchanged catalog revision keeps mounted nodes and performs no tree writes', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.open(); h.drain();
  const original = h.state().groupNodes.get('pool:character'), created = h.counts().created;
  let renders = 0; const real = h.ctx.historyFiltersRenderCatalog; h.ctx.historyFiltersRenderCatalog = (...args) => { renders++; return real(...args); };
  await h.accept(largeCatalog()); h.drain();
  assert.equal(renders, 0); assert.equal(h.counts().created, created); assert.equal(h.state().groupNodes.get('pool:character'), original);
});

test('catalog updates behind a dialog change rule validity immediately and merge a single deferred paint', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.open(); h.document.upperModal = true;
  const before = h.counts(); await h.accept(largeCatalog('r2', 10)); h.drain();
  assert.equal(h.state().knownGroups.size, 10); assert.equal(h.state().catalog.group_revision, 'r2');
  assert.equal(h.counts().created, before.created); assert.equal(h.counts().writes, before.writes);
  h.emitClose(); assert.equal(h.state().mountedRevision, 'r2'); assert.equal(h.state().dirty.size, 0);
});

test('candidate responses after closing update pure data and restore cached search and pages on reopening', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.open();
  const id = h.find('all', ''), pending = deferred();
  h.respond(url => url.includes('filter-catalog') ? Promise.resolve(h.catalog()) : pending.promise);
  h.expand(id); const panel = h.state().panels.get(id);
  panel.q = 'cached'; panel.page = 2;
  h.ctx.historyFiltersSetOpen(false); h.drain(); const before = h.counts();
  pending.resolve({ items: [{ id: 'one', title: '候选一', count: 2 }], page: 2, has_more: true }); await settle();
  assert.equal(panel.items[0].id, 'one'); assert.equal(h.state().panelNodes.size, 0); assert.equal(h.counts().writes, before.writes);
  assert.equal(Object.values(panel).some(value => value && value.nodeType), false);
  const pages = [];
  h.respond(async url => { if (url.includes('filter-catalog')) return h.catalog(); const page = Number(new URL(url, 'http://test').searchParams.get('page')); pages.push(page); return { items: [{ id: `page-${page}`, title: '保留分页' }], page, has_more: true }; });
  h.open(); await settle(); h.drain();
  assert.equal(h.state().panelNodes.get(id).input.value, 'cached'); assert.deepEqual(pages, [1, 2]);
  assert.equal(panel.page, 2); assert.equal(panel.items.length, 2);
});

test('candidate failure paints once and never becomes an automatic retry loop', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.open(); let reads = 0;
  h.respond(async url => { if (url.includes('filter-catalog')) return h.catalog(); reads++; throw new Error('offline'); });
  h.expand(h.find('all', '')); await settle(); h.drain();
  assert.equal(reads, 1); assert.equal(h.state().panels.values().next().value.error, 'offline');
});

test('a history response revision cannot suppress indexing the first catalog', async () => {
  const h = harness(); h.ctx.historyDetailState.homeQuery.groupRevision = 'r1';
  await h.accept(largeCatalog());
  assert.equal(h.state().knownGroups.size, 1963); h.open(); assert.ok(h.state().groupNodes.size > 0);
});

test('Tab at a virtual boundary mounts and focuses the next row instead of skipping the remainder', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.open(); h.expand('pool:pose'); h.expand(h.find('heading', 'traits'));
  const list = h.state().lists.get(h.find('heading', 'traits'));
  const lastId = [...list.mounted].at(-1), index = list.ids.indexOf(lastId), row = h.state().groupNodes.get(lastId), button = row.querySelectorAll('button').at(-1);
  button.focus(); let prevented = false;
  h.ctx.historyFiltersListKeydown(list, { key: 'Tab', target: button, shiftKey: false, preventDefault() { prevented = true; } });
  assert.equal(prevented, true); assert.equal(h.document.activeElement, h.state().groupNodes.get(list.ids[index + 1]).firstElementChild);
  h.drain(); assert.ok(h.document.activeElement.isConnected);
});

test('measured expanded heights survive virtual release and keep later row offsets stable', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.open(); h.expand('pool:pose'); h.expand(h.find('heading', 'traits'));
  const id = h.find('trait', 'trait-0'); h.expand(id);
  const node = h.state().groupNodes.get(id); h.ctx.historyFiltersMeasureNode(node);
  assert.ok(h.state().heights.get(id) > 44); const height = h.state().heights.get(id);
  h.body.scrollTop = 20000; h.ctx.historyFiltersSchedulePaint(); h.drain();
  assert.equal(h.state().groupNodes.has(id), false); assert.equal(h.state().heights.get(id), height);
  h.body.scrollTop = 0; h.ctx.historyFiltersSchedulePaint(); h.drain();
  assert.equal(h.state().groupNodes.get(id).open, true); assert.equal(h.state().panelNodes.has(id), true);
});

test('mobile teardown releases the tree and restores background locks only after the exit', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.state().media.matches = true; h.open();
  assert.equal(h.document.body.style.overflow, 'hidden'); assert.equal(h.trigger.inert, true);
  h.ctx.historyFiltersSetOpen(false); h.step(100);
  assert.ok(h.state().groupNodes.size > 0); assert.equal(h.trigger.inert, true);
  h.drain(); assert.equal(h.state().groupNodes.size, 0); assert.equal(h.trigger.inert, false); assert.equal(h.document.body.style.overflow, 'auto');
});

test('candidate revision recovery is bounded across automatic catalog remounts', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.open(); await settle();
  let catalogs = 0, candidates = 0;
  h.respond(async url => {
    if (url.includes('filter-catalog')) return largeCatalog(`r${++catalogs + 1}`);
    candidates++; throw Object.assign(new Error('revision keeps changing'), { code: 'history_filter_catalog_changed' });
  });
  h.expand(h.find('all', '')); await settle(); h.drain(); await settle(); h.drain();
  assert.equal(catalogs, 1); assert.equal(candidates, 2);
  assert.equal(h.state().panels.values().next().value.error, 'revision keeps changing');
});

test('nonadvancing candidate revision does not retry the same request', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.open(); await settle(); let reads = 0;
  h.respond(async url => { if (url.includes('filter-catalog')) return h.catalog(); reads++; throw Object.assign(new Error('no new revision'), { code: 'history_filter_catalog_changed' }); });
  h.expand(h.find('all', '')); await settle(); h.drain();
  assert.equal(reads, 1); assert.equal(h.state().panels.values().next().value.loading, false);
});

test('a late failed candidate catalog recovery cannot overwrite a newer search status', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.open(); await settle(); const pending = deferred();
  h.respond(async url => { if (url.includes('filter-catalog')) return pending.promise; throw Object.assign(new Error('obsolete candidate'), { code: 'history_filter_catalog_changed' }); });
  h.expand(h.find('all', '')); await settle();
  const panel = h.state().panels.values().next().value; panel.q = 'new search'; ++panel.serial; panel.error = 'current search status';
  pending.reject(new Error('obsolete recovery failed')); await settle(); h.drain();
  assert.equal(panel.error, 'current search status');
});

test('a newer cached catalog repairs an older query revision and retries the requested candidate page', async () => {
  const h = harness(); await h.accept(largeCatalog('r2')); h.open(); await settle();
  h.expand(h.find('all', '')); await settle(); h.drain();
  const panel = h.state().panels.values().next().value;
  panel.items = [{ id: 'page-one', title: '第一页' }]; panel.page = 1; panel.more = true; panel.loaded = true;
  h.ctx.historyDetailState.homeQuery.groupRevision = 'r1'; let reads = 0;
  h.respond(async url => {
    if (url.includes('filter-catalog')) return h.catalog();
    reads++; if (reads === 1) throw Object.assign(new Error('old request revision'), { code: 'history_filter_catalog_changed' });
    assert.equal(new URL(url, 'http://test').searchParams.get('page'), '2');
    return { items: [{ id: 'page-two', title: '第二页' }], page: 2, has_more: false };
  });
  await h.ctx.historyFiltersLoadOptions(panel, { more: true }); h.drain();
  assert.equal(reads, 2); assert.equal(panel.loading, false); assert.equal(panel.error, ''); assert.equal(panel.page, 2);
  assert.deepEqual(Array.from(panel.items, item => item.id), ['page-one', 'page-two']);
});

test('changing spacer boundaries never moves an unchanged focused expanded row', async () => {
  const h = harness(); await h.accept(largeCatalog()); h.open(); h.expand('pool:pose'); h.expand(h.find('heading', 'traits'));
  const list = h.state().lists.get(h.find('heading', 'traits'));
  h.body.scrollTop = 5800; h.ctx.historyFiltersSchedulePaint(); h.drain();
  const id = [...list.mounted][8], row = h.state().groupNodes.get(id);
  row.firstElementChild.focus(); h.expand(id); await settle(); h.drain();
  const input = h.state().panelNodes.get(id).input; input.focus();
  const before = h.counts().focusedMoves;
  for (const offset of [5844, 5888, 5932, 5800]) { h.body.scrollTop = offset; h.ctx.historyFiltersSchedulePaint(); h.drain(); }
  assert.equal(h.counts().focusedMoves, before, 'unchanged retained rows must not be reinserted around spacers');
  assert.equal(h.document.activeElement, input); assert.equal(input.isConnected, true);
  assert.equal(h.state().groupNodes.get(id), row); assert.equal(h.state().panelNodes.get(id).input, input);
});
