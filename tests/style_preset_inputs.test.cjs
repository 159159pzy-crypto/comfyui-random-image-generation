const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const helpers = require('../static/style-preset-manager.js');

function tagHarness(initial = '') {
  const source = fs.readFileSync(require.resolve('../static/style-preset-manager.js'), 'utf8');
  const document = { activeElement: null };
  class Element {
    constructor(tag, className = '', text = '') { this.tagName = tag; this.className = className; this.textContent = text; this.children = []; this.handlers = {}; this.attributes = new Map(); this.value = ''; this.disabled = false; this.classList = { toggle() {} }; }
    append(...nodes) { for (const node of nodes) { node.parentElement = this; this.children.push(node); } }
    get lastElementChild() { return this.children.at(-1); }
    remove() { const parent = this.parentElement; if (parent) parent.children.splice(parent.children.indexOf(this), 1); this.parentElement = null; }
    querySelector(tag) { return this.children.find(child => child.tagName === tag) || null; }
    addEventListener(type, action) { (this.handlers[type] ||= []).push(action); }
    setAttribute(name, value) { this.attributes.set(name, value); }
    removeAttribute(name) { this.attributes.delete(name); }
    focus() { document.activeElement = this; }
    setRangeText(value, start, end) { this.value = this.value.slice(0, start) + value + this.value.slice(end); }
    fire(type, overrides = {}) { const event = { key: '', preventDefault() { this.prevented = true; }, stopPropagation() {}, ...overrides }; for (const action of this.handlers[type] || []) action(event); return event; }
  }
  let draft = initial;
  const h = (tag, name, text) => new Element(tag, name, text);
  const context = vm.createContext({ document, h, uniqueWords: helpers.uniqueWords, icon: () => h('svg'), error: (node, message) => { node.textContent = message; node.hidden = !message; },
    button(text, name, action, label) { const node = h('button', name, text); node.setAttribute('aria-label', label); node.addEventListener('click', action); return node; }, update: value => { draft = value; } });
  vm.runInContext(source.slice(source.indexOf('  function field('), source.indexOf('  function editorDirty(')) + '\nthis.root = tagInput(' + JSON.stringify(initial) + ', update, "tags"); this.fieldNode = field("标签", root, "提示");', context);
  const root = context.root, input = root.spmInput;
  return { root, input, document, values: () => root.children[0].children.map(token => token.children[0].textContent), draft: () => draft };
}

test('tag entry preserves pending text in the draft and commits without duplicating existing tokens', () => {
  const h = tagHarness('Soft');
  h.input.value = '人像'; h.input.fire('input');
  assert.equal(h.draft(), 'Soft, 人像'); assert.deepEqual(h.values(), ['Soft']);
  assert.equal(h.root.spmTags.commit(), true); assert.deepEqual(h.values(), ['Soft', '人像']);
  h.input.value = 'soft'; h.input.fire('keydown', { key: 'Enter' });
  assert.deepEqual(h.values(), ['Soft', '人像']); assert.equal(h.input.value, '');
});

test('IME Enter is not consumed and comma/newline paste commits atomically with case deduplication', () => {
  const h = tagHarness(); h.input.fire('compositionstart'); h.input.value = '柔光';
  assert.equal(h.input.fire('keydown', { key: 'Enter', isComposing: true }).prevented, undefined);
  assert.deepEqual(h.values(), []); h.input.fire('compositionend'); assert.deepEqual(h.values(), []);
  h.input.fire('blur'); assert.deepEqual(h.values(), ['柔光']);
  h.input.fire('paste', { clipboardData: { getData: () => 'Film，film\n人像' } });
  assert.deepEqual(h.values(), ['柔光', 'Film', '人像']); assert.equal(h.draft(), '柔光, Film, 人像');
});

test('committing on blur preserves a removal target and invalid tags remain editable with field ARIA', () => {
  const h = tagHarness('Film'); const remove = h.root.children[0].children[0].lastElementChild;
  h.input.value = '人像'; h.input.fire('input'); h.input.fire('blur');
  assert.equal(h.root.children[0].children[0].lastElementChild, remove);
  remove.fire('click'); assert.deepEqual(h.values(), ['人像']);
  h.input.value = 'x'.repeat(101); h.input.fire('input');
  assert.equal(h.root.spmTags.commit(true), false); assert.equal(h.input.attributes.get('aria-invalid'), 'true');
  assert.equal(h.input.value.length, 101); assert.equal(h.document.activeElement, h.input);
  h.input.value = '自然光'; h.input.fire('input'); h.root.spmTags.commit();
  assert.equal(h.input.attributes.has('aria-invalid'), false); assert.deepEqual(h.values(), ['人像', '自然光']);
});

test('tag validation matches the 64 tag and 100 Unicode character contract without losing pending input', () => {
  const tags = Array.from({ length: 64 }, (_, index) => `tag${index}`);
  assert.equal(helpers.tagInputState(tags, 'TAG0').message, '');
  assert.ok(helpers.tagInputState(tags, 'pending').message); assert.equal(helpers.tagInputState(tags, 'pending').values.at(-1), 'pending');
  assert.equal(helpers.tagInputState([], '😀'.repeat(100)).message, '');
  assert.ok(helpers.tagInputState([], '😀'.repeat(101)).message);
});

test('compositor crop geometry maps the submitted normalized rectangle exactly onto the stage', () => {
  for (const [width, height] of [[4000, 1000], [600, 1800], [800, 600]]) {
    for (const zoom of [1, 1.8, 4]) for (const [cx, cy] of [[.5, .5], [-20, 80], [80, -20]]) {
      const crop = helpers.cropRectangle(width, height, zoom, cx, cy), stage = { width: 260, height: 195 };
      const view = helpers.cropPresentation(width, height, stage, crop);
      assert.ok(Math.abs(view.x + crop.x * view.width * view.scale) < 1e-8);
      assert.ok(Math.abs(view.y + crop.y * view.height * view.scale) < 1e-8);
      assert.ok(Math.abs(crop.width * view.width * view.scale - stage.width) < 1e-8);
      assert.ok(Math.abs(crop.height * view.height * view.scale - stage.height) < 1e-8);
    }
  }
});
