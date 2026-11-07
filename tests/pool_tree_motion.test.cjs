const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const runtime = fs.readFileSync(path.join(__dirname, '../static/motion.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup({ reduced = false } = {}) {
  let ctx, now = 0, serial = 0, renders = 0, persisted = 0;
  const frames = new Map();
  class Row {
    constructor(id, parent = '', expanded = null) {
      this.dataset = { groupId: id, parentId: parent }; this.style = {}; this.inert = false;
      this.className = 'favorite-tree-row'; this.title = id; this.tabIndex = -1; this.parentElement = null;
      this.attrs = new Map([['role', 'treeitem'], ['aria-level', parent ? '2' : '1']]);
      if (expanded !== null) this.attrs.set('aria-expanded', String(expanded));
      this.disclosure = expanded === null ? null : { title: expanded ? '折叠' : '展开', attrs: new Map([['aria-label', expanded ? '折叠' : '展开']]),
        getAttribute(name) { return this.attrs.get(name) ?? null; }, setAttribute(name, value) { this.attrs.set(name, value); } };
    }
    getAttribute(name) { return this.attrs.get(name) ?? null; }
    setAttribute(name, value) { this.attrs.set(name, String(value)); }
    removeAttribute(name) { this.attrs.delete(name); }
    getBoundingClientRect() { return { height: this.style.height ? parseFloat(this.style.height) : 32 }; }
    get isConnected() { return !!this.parentElement?.attached; }
    get nextElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) + 1] || null; }
    querySelector(selector) { return selector === '.tree-disclosure' ? this.disclosure : null; }
    focus() { ctx.document.activeElement = this; }
    remove() { if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); this.parentElement = null; }
  }
  class Tree {
    constructor(attached = false) { this.attached = attached; this.children = []; }
    get firstElementChild() { return this.children[0] || null; }
    append(row) { this.insertBefore(row, null); }
    insertBefore(row, before) { row.remove(); const index = before ? this.children.indexOf(before) : this.children.length; this.children.splice(index, 0, row); row.parentElement = this; }
  }
  const favorite = new Tree(true), custom = new Tree(true);
  const collapsedFavoriteGroups = { pose: new Set() }, collapsedCustomGroups = { pose: new Set() };
  const describe = (tree, collapsed, withGrand = false) => {
    tree.append(new Row('parent', '', !collapsed.has('parent')));
    if (!collapsed.has('parent')) {
      tree.append(new Row('child', 'parent', withGrand ? !collapsed.has('child') : null));
      if (withGrand && !collapsed.has('child')) tree.append(new Row('grand', 'child'));
    }
    tree.append(new Row('other', '', !collapsed.has('other')));
    if (!collapsed.has('other')) tree.append(new Row('other-child', 'other'));
  };
  ctx = vm.createContext({
    console, performance: { now: () => now }, matchMedia: () => ({ matches: reduced }),
    requestAnimationFrame: callback => { frames.set(++serial, callback); return serial; }, cancelAnimationFrame: id => frames.delete(id),
    document: { activeElement: null, createElement: () => new Tree(), hidden: false },
    activeSection: 'pose', collapsedFavoriteGroups, collapsedCustomGroups,
    currentView: () => ({}),
    ui: { poolSidebar: { querySelector: selector => selector === '.custom-group-tree' ? custom : favorite } },
    renderPoolSidebar: () => { renders++; },
    renderFavoriteTree: tree => describe(tree, collapsedFavoriteGroups.pose, true),
    renderCustomTree: tree => describe(tree, collapsedCustomGroups.pose),
    favoriteDescendantIds: id => new Set(id === 'parent' ? ['child', 'grand'] : id === 'child' ? ['grand'] : ['other-child']),
    customDescendantIds: id => new Set(id === 'parent' ? ['child'] : ['other-child']),
    persistFavoriteTree: () => persisted++, persistCustomTree: () => persisted++,
    focusFavoriteTreeRow: id => favorite.children.find(row => row.dataset.groupId === id)?.focus(),
    focusCustomTreeRow: id => custom.children.find(row => row.dataset.groupId === id)?.focus(),
  });
  vm.runInContext(runtime, ctx);
  vm.runInContext('const poolTreeRowMotion = new WeakMap();', ctx);
  for (const name of ['characterPickerSession', 'poolCollapsedGroups', 'animateTreeRow', 'animateTreeRowsIn', 'animateTreeRowsOut', 'renderPoolSidebarAnimated', 'toggleFavoriteTreeGroup', 'toggleCustomTreeGroup']) {
    const start = source.indexOf(`function ${name}(`);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), ctx);
  }
  describe(favorite, collapsedFavoriteGroups.pose, true); describe(custom, collapsedCustomGroups.pose);
  const frame = (ms = 16) => { now += ms; const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(now)); };
  const settle = () => { for (let count = 0; frames.size && count < 150; count++) frame(); assert.equal(frames.size, 0); };
  return { ctx, favorite, custom, frames, frame, settle, get renders() { return renders; }, get persisted() { return persisted; } };
}
const ids = tree => tree.children.map(row => row.dataset.groupId);

test('collapse reverses on the same row with continuous velocity and no stale sidebar render', async () => {
  const h = setup(), parent = h.favorite.children[0], child = h.favorite.children[1];
  h.ctx.toggleFavoriteTreeGroup('parent', true); h.frame(); h.frame(70);
  assert.equal(parent.getAttribute('aria-expanded'), 'false');
  assert.equal(child.inert, true); assert.equal(child.getAttribute('role'), null);
  const before = h.ctx.StudioMotion.snapshot(child, 'tree-row');
  h.ctx.toggleFavoriteTreeGroup('parent', true);
  const after = h.ctx.StudioMotion.snapshot(child, 'tree-row');
  assert.equal(h.favorite.children[1], child); assert.equal(after.value.progress, before.value.progress);
  assert.equal(after.velocity.progress, before.velocity.progress);
  assert.equal(parent.getAttribute('aria-expanded'), 'true'); assert.equal(h.ctx.document.activeElement, parent);
  h.settle(); await flush();
  assert.equal(child.isConnected, true); assert.equal(child.style.opacity, ''); assert.equal(child.style.height, '');
  assert.equal(child.getAttribute('role'), 'treeitem'); assert.equal(child.inert, false);
  assert.equal(h.renders, 0); assert.equal(h.persisted, 2);
});

test('close-open-close removes descendants only at the latest target and releases temporary styles', async () => {
  const h = setup(), child = h.favorite.children[1];
  h.ctx.toggleFavoriteTreeGroup('parent'); h.frame(50);
  h.ctx.toggleFavoriteTreeGroup('parent'); h.frame(20);
  h.ctx.toggleFavoriteTreeGroup('parent'); h.settle(); await flush();
  assert.deepEqual(ids(h.favorite), ['parent', 'other', 'other-child']);
  assert.equal(child.isConnected, false); assert.equal(child.style.transform, ''); assert.equal(child.style.willChange, '');
  assert.equal(h.renders, 0); assert.equal(h.ctx.collapsedFavoriteGroups.pose.has('parent'), true);
});

test('cancelling a row exit never calls its obsolete completion', async () => {
  const h = setup(), child = h.favorite.children[1]; let completed = 0;
  const old = h.ctx.animateTreeRowsOut([child], () => completed++); h.frame(40);
  h.ctx.animateTreeRowsIn([child]); h.settle(); await old;
  assert.equal(completed, 0); assert.equal(child.isConnected, true);
});

test('nested targets retain original order and independently closed descendants remain closed after parent reverses', async () => {
  const h = setup(), child = h.favorite.children[1];
  h.ctx.toggleFavoriteTreeGroup('child'); h.frame(30);
  h.ctx.toggleFavoriteTreeGroup('parent'); h.frame(30);
  h.ctx.toggleFavoriteTreeGroup('other');
  assert.deepEqual(ids(h.favorite), ['parent', 'child', 'grand', 'other', 'other-child']);
  h.ctx.toggleFavoriteTreeGroup('parent'); h.settle(); await flush();
  assert.deepEqual(ids(h.favorite), ['parent', 'child', 'other']);
  assert.equal(h.favorite.children[1], child); assert.equal(child.getAttribute('aria-expanded'), 'false');
});

test('favorite and custom groups with identical ids own separate rows and focus', async () => {
  const h = setup(), favoriteChild = h.favorite.children[1], customChild = h.custom.children[1];
  h.ctx.toggleCustomTreeGroup('parent', true); h.frame(40);
  assert.equal(favoriteChild.inert, false); assert.equal(favoriteChild.style.height, undefined);
  assert.equal(customChild.inert, true); assert.equal(h.ctx.document.activeElement, h.custom.children[0]);
  h.ctx.toggleCustomTreeGroup('parent', true); h.settle(); await flush();
  assert.equal(h.custom.children[1], customChild); assert.equal(h.favorite.children[1], favoriteChild);
  assert.equal(h.ctx.collapsedFavoriteGroups.pose.size, 0);
});

test('reduced motion commits collapsed state synchronously without pending animation or style residue', async () => {
  const h = setup({ reduced: true }), parent = h.favorite.children[0];
  h.ctx.toggleFavoriteTreeGroup('parent', true);
  assert.deepEqual(ids(h.favorite), ['parent', 'other', 'other-child']); assert.equal(h.frames.size, 0);
  h.ctx.toggleFavoriteTreeGroup('parent', true); await flush();
  assert.deepEqual(ids(h.favorite), ['parent', 'child', 'grand', 'other', 'other-child']);
  assert.equal(h.favorite.children[1].style.height, ''); assert.equal(h.ctx.document.activeElement, parent);
});
