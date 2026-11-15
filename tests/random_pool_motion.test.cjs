const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const motion = fs.readFileSync(path.join(__dirname, '../static/motion.js'), 'utf8');
const layout = fs.readFileSync(path.join(__dirname, '../static/motion.js'), 'utf8');
const sections = ['character', 'clothing', 'pose', 'background', 'expression'];
const plain = value => JSON.parse(JSON.stringify(value));
const decode = value => value.replace(/&(?:amp|lt|gt|quot|#39);/g, entity => ({ '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" })[entity]);
function sourceFunction(source, name) {
  const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
  assert.ok(start >= 0, `${name} must be loaded from production`);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}

// The DOM model supplies fixed-width text geometry, not animation behavior.
// Real word wrapping/font metrics are covered in the browser acceptance fixture.
// Every spring below is the production runtime under a deterministic frame clock.
function harness({ width = 320, reduced = false, fixed = {}, modes = {} } = {}) {
  let now = 0, serial = 0, saves = 0;
  const frames = new Map(), observers = [], requests = [], springs = [];
  class Events {
    constructor() { this.listeners = new Map(); }
    addEventListener(name, handler) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(handler); }
    removeEventListener(name, handler) { this.listeners.set(name, (this.listeners.get(name) || []).filter(item => item !== handler)); }
    dispatch(name, values = {}) {
      const event = { type: name, target: this, currentTarget: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...values };
      for (const handler of this.listeners.get(name) || []) handler(event);
      if (event.bubbles && !event.stopped) this.parentElement?.dispatch(name, event);
      return event;
    }
  }
  const document = new Events(), media = new Events();
  document.hidden = false; document.activeElement = null; media.matches = reduced;
  const camel = name => name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
  const style = () => ({ setProperty(name, value) { this[camel(name)] = String(value); }, removeProperty(name) { delete this[camel(name)]; }, getPropertyValue(name) { return this[camel(name)] || ''; } });
  class Element extends Events {
    constructor(tag) {
      super(); this.tagName = tag.toUpperCase(); this.children = []; this.parentElement = null;
      this.dataset = {}; this.style = style(); this.attributes = new Map(); this.classes = new Set();
      this.hidden = false; this.inert = false; this.disabled = false; this.scrollTop = 0; this.scrollLeft = 0;
      this.selectionStart = 0; this.selectionEnd = 0; this.selectionDirection = 'none'; this._value = ''; this._text = '';
      this.classList = {
        contains: name => this.classes.has(name), add: (...names) => names.forEach(name => this.classes.add(name)),
        remove: (...names) => names.forEach(name => this.classes.delete(name)),
        toggle: (name, active = !this.classes.has(name)) => { if (active) this.classes.add(name); else this.classes.delete(name); return active; },
      };
    }
    get className() { return [...this.classes].join(' '); }
    set className(value) { this.classes = new Set(String(value).split(/\s+/).filter(Boolean)); }
    get value() { return this._value; }
    set value(value) { value = String(value); if (this._value !== value) { this._value = value; this.selectionStart = this.selectionEnd = value.length; } }
    get textContent() { return this._text + this.children.map(node => node.textContent).join(''); }
    set textContent(value) { this.replaceChildren(); this._text = String(value); if (this.tagName === 'TEXTAREA') this.value = String(value); }
    set innerHTML(html) {
      this.replaceChildren(); this._text = '';
      const stack = [this], tokens = html.match(/<[^>]+>|[^<]+/g) || [];
      for (const token of tokens) {
        if (token.startsWith('</')) { stack.pop(); continue; }
        if (token.startsWith('<')) {
          const tag = token.match(/^<([\w-]+)/)?.[1]; if (!tag) continue;
          const child = new Element(tag), attrs = token.slice(tag.length + 1, -1);
          for (const match of attrs.matchAll(/([^\s=]+)(?:="([^"]*)"|='([^']*)'|=([^\s]+))?/g)) child.setAttribute(match[1], decode(match[2] ?? match[3] ?? match[4] ?? ''));
          stack.at(-1).append(child);
          if (!['input', 'br', 'hr', 'img'].includes(tag.toLowerCase()) && !token.endsWith('/>')) stack.push(child);
        } else {
          stack.at(-1)._text += decode(token);
          if (stack.at(-1).tagName === 'TEXTAREA') stack.at(-1).value = stack.at(-1)._text;
        }
      }
    }
    get firstElementChild() { return this.children[0] || null; }
    get childElementCount() { return this.children.length; }
    get isConnected() { return this === document.documentElement || !!this.parentElement?.isConnected; }
    get id() { return this.attributes.get('id') || ''; }
    set id(value) { this.attributes.set('id', String(value)); }
    setAttribute(name, value) {
      value = String(value); this.attributes.set(name, value);
      if (name === 'class') this.className = value;
      else if (name.startsWith('data-')) this.dataset[camel(name.slice(5))] = value;
      else if (['disabled', 'hidden', 'inert'].includes(name)) this[name] = true;
      else if (name === 'value') this.value = value;
      else if (['rows', 'tabindex'].includes(name)) this[name === 'tabindex' ? 'tabIndex' : name] = Number(value);
    }
    getAttribute(name) { if (name === 'class') return this.className; if (name.startsWith('data-')) return this.dataset[camel(name.slice(5))] ?? null; return this.attributes.get(name) ?? null; }
    hasAttribute(name) { return this.getAttribute(name) !== null; }
    removeAttribute(name) { this.attributes.delete(name); if (name.startsWith('data-')) delete this.dataset[camel(name.slice(5))]; if (['disabled', 'hidden', 'inert'].includes(name)) this[name] = false; }
    append(...nodes) { nodes.forEach(node => this.insertBefore(node, null)); }
    appendChild(node) { this.append(node); return node; }
    insertBefore(node, anchor) { node.remove(); const index = anchor ? this.children.indexOf(anchor) : this.children.length; assert.ok(index >= 0); this.children.splice(index, 0, node); node.parentElement = this; return node; }
    replaceChildren(...nodes) { [...this.children].forEach(node => node.remove()); this.append(...nodes); }
    remove() { if (!this.parentElement) return; if (this.contains(document.activeElement)) document.activeElement = null; this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); this.parentElement = null; }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    matches(selector) {
      if (selector.includes(',')) return selector.split(',').some(part => this.matches(part.trim()));
      const tag = selector.match(/^[a-z]+/i)?.[0]; if (tag && this.tagName !== tag.toUpperCase()) return false;
      for (const match of selector.matchAll(/\.([\w-]+)/g)) if (!this.classes.has(match[1])) return false;
      for (const match of selector.matchAll(/\[([\w-]+)(?:=["']?([^"'\]]+)["']?)?\]/g)) {
        const value = this.getAttribute(match[1]); if (value === null || (match[2] !== undefined && value !== match[2])) return false;
      }
      if (selector.startsWith('#') && this.id !== selector.slice(1)) return false;
      return true;
    }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
    querySelectorAll(selector) {
      const parts = selector.trim().replace(/^:scope\s*>\s*/, '').split(/\s+>\s+|\s+/), last = parts.pop();
      const result = [], visit = node => { for (const child of node.children) { if (child.matches(last) && (!parts.length || child.parentElement?.closest(parts.at(-1)))) result.push(child); visit(child); } };
      visit(this); return result;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    focus(options) { document.activeElement = this; this.focusOptions = options; }
    setSelectionRange(start, end, direction = 'none') { this.selectionStart = start; this.selectionEnd = end; this.selectionDirection = direction; }
    getClientRects() { return this.closest('[hidden]') || this.hidden || this.ancestorsHidden() ? [] : [this.getBoundingClientRect()]; }
    ancestorsHidden() { for (let parent = this.parentElement; parent; parent = parent.parentElement) if (parent.hidden) return true; return false; }
    get offsetWidth() {
      if (this.hidden || this.ancestorsHidden()) return 0;
      if (this.style.width && this.style.width !== 'auto') return parseFloat(this.style.width);
      if (this.tagName === 'BUTTON' && this.parentElement?.classList.contains('dimension-mode')) return (this.parentElement.offsetWidth - 10) / 3;
      if (this.tagName === 'TEXTAREA') return this.parentElement?.offsetWidth || width;
      return this.parentElement?.offsetWidth || width;
    }
    get clientWidth() { return this.offsetWidth - (this.tagName === 'TEXTAREA' ? 2 : 0); }
    get offsetLeft() {
      if (this.parentElement?.classList.contains('dimension-mode') && this.tagName === 'BUTTON') return 3 + this.parentElement.children.filter(node => node.tagName === 'BUTTON').indexOf(this) * (this.offsetWidth + 2);
      return 0;
    }
    get offsetTop() {
      if (this.tagName !== 'SPAN' || !this.parentElement) return 0;
      const before = this.parentElement.children.slice(0, this.parentElement.children.indexOf(this)).map(node => node.textContent).join('').replaceAll('\u200b', '');
      const columns = Math.max(1, Math.floor((this.parentElement.clientWidth - 26) / 8));
      const lines = before.split('\n'), last = lines.pop();
      return 6 + 20 * (lines.reduce((count, line) => count + Math.max(1, Math.ceil(line.length / columns)), 0) + Math.floor(last.length / columns));
    }
    get naturalLines() { const columns = Math.max(1, Math.floor((this.clientWidth - 24) / 8)); return this.value.split('\n').reduce((count, line) => count + Math.max(1, Math.ceil(line.length / columns)), 0); }
    get scrollHeight() { return this.tagName === 'TEXTAREA' ? Math.max(this.clientHeight, this.naturalLines * 20 + 13) : this.offsetHeight; }
    get offsetHeight() { if (this.hidden || this.ancestorsHidden()) return 0; if (this.style.height && this.style.height !== 'auto') return parseFloat(this.style.height); return this.tagName === 'TEXTAREA' ? 35 : 29; }
    get clientHeight() { return Math.max(0, this.offsetHeight - (this.tagName === 'TEXTAREA' ? 2 : 0)); }
    getBoundingClientRect() { return { x: this.offsetLeft, y: 0, left: this.offsetLeft, top: 0, width: this.offsetWidth, height: this.offsetHeight, right: this.offsetLeft + this.offsetWidth, bottom: this.offsetHeight }; }
    cloneNode() { const clone = new Element(this.tagName); clone.className = this.className; clone.value = this.value; Object.assign(clone.style, this.style); return clone; }
  }
  document.createElement = tag => new Element(tag);
  document.documentElement = new Element('html'); document.body = new Element('body'); document.documentElement.append(document.body);
  document.querySelectorAll = selector => document.documentElement.querySelectorAll(selector);
  document.querySelector = selector => document.documentElement.querySelector(selector);
  document.getElementById = id => document.querySelector(`#${id}`);
  document.contains = node => document.documentElement.contains(node);
  const list = new Element('div'); list.id = 'dimensionList'; document.body.append(list);
  const counts = Object.fromEntries(sections.map(section => [section, 2]));
  const ctx = vm.createContext({ document, console, performance: { now: () => now }, matchMedia: () => media,
    requestAnimationFrame(callback) { frames.set(++serial, callback); return serial; }, cancelAnimationFrame(id) { frames.delete(id); },
    getComputedStyle(node) { return { boxSizing: 'border-box', width: `${node.offsetWidth}px`, height: `${node.offsetHeight}px`, minHeight: '35px', maxHeight: '135px', lineHeight: '20px', fontSize: '12px', fontFamily: 'system-ui', fontWeight: '400', letterSpacing: '0px', paddingTop: '6px', paddingBottom: '7px', paddingLeft: '12px', paddingRight: '12px', borderTopWidth: '1px', borderBottomWidth: '1px', borderLeftWidth: '1px', borderRightWidth: '1px', getPropertyValue: key => node.style.getPropertyValue(key) }; },
    ResizeObserver: class { constructor(callback) { this.callback = callback; this.nodes = []; observers.push(this); } observe(node) { this.nodes.push(node); } unobserve(node) { this.nodes = this.nodes.filter(item => item !== node); } disconnect() { this.nodes = []; } },
    SECTIONS: sections, SECTION_META: Object.fromEntries(sections.map((section, index) => [section, { label: ['角色', '服装', '姿势', '背景', '表情'][index] }])),
    draft: { modes: { ...modes }, fixed: { ...fixed }, counts: {}, pools: {}, loras: [], managedTriggers: [] },
    ui: { dimensionList: list, undoPromptNormalization: new Element('button'), promptNormalizationStatus: new Element('div') },
    selectedCount: section => counts[section], schedulePersist: () => saves++, updatePoseConflict() {}, openPool() {},
    escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'),
    currentProfile: () => 'anima', singleWorkbenchState: { ready: true, mode: 'random' },
    promptNormalizationEpoch: 0, promptNormalizationRequests: new Map(), normalizationUndo: null,
    PROMPT_FIELD_NAMES: sections.map(section => `fixed_${section}`), PROMPT_FIELD_LABELS: {}, clone: plain,
    request(url, options) { let resolve; const promise = new Promise(done => { resolve = done; }); requests.push({ url, body: JSON.parse(options.body), resolve }); return promise; },
    toast() {},
  });
  ctx.window = ctx; ctx.addEventListener = (...args) => document.addEventListener(...args);
  vm.runInContext(motion, ctx);
  const runtime = ctx.StudioMotion;
  ctx.StudioMotion = { ...runtime, spring(...args) { springs.push({ owner: args[0], channel: args[1], target: args[2] }); return runtime.spring(...args); } };
  vm.runInContext(sourceFunction(layout, 'animateStudioIndicator'), ctx);
  const names = [...app.matchAll(/^function (\w*Dimension\w*|dimension\w*|syncDimension\w*|positionDimension\w*|setDimension\w*)\(/gm)].map(match => match[1]);
  for (const name of names) vm.runInContext(sourceFunction(app, name), ctx);
  for (const name of ['normalizeTriggerWords', 'promptFieldForElement', 'applyPromptField', 'promptElementForField', 'promptNormalizationContext', 'invalidatePromptNormalization', 'normalizePromptFields', 'normalizePromptElement', 'setNormalizationUndo', 'clearNormalizationUndo', 'undoPromptNormalization']) vm.runInContext(sourceFunction(app, name), ctx);
  const step = (elapsed = 16) => { now += elapsed; const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback(now)); };
  const drain = () => { let limit = 300; while (frames.size && limit--) step(); assert.ok(limit > 0, 'motion releases its frame after settling'); };
  const row = section => list.querySelector(`[data-section="${section}"]`);
  const field = section => row(section).querySelector('[data-fixed]');
  const input = (section, value) => { const node = field(section); node.value = value; node.dispatch('input', { bubbles: true }); ctx.invalidatePromptNormalization(); return node; };
  return { ctx, document, list, counts, frames, springs, observers, requests, media, row, field, input, step, drain,
    saves: () => saves, snapshot: (node, channel) => runtime.snapshot(node, channel),
    resize(nextWidth) { width = nextWidth; for (const observer of observers) if (observer.nodes.length) observer.callback(observer.nodes.map(target => ({ target, contentRect: target.getBoundingClientRect() }))); },
    reduce() { media.matches = true; media.dispatch('change'); },
    hide(hidden) { document.hidden = hidden; document.dispatch('visibilitychange'); },
  };
}

test('the initial five pool cards use single-line textareas and restore without entrance motion', () => {
  const h = harness(); h.ctx.renderDimensions();
  assert.equal(h.list.children.length, 5); assert.equal(h.frames.size, 0);
  for (const section of sections) {
    assert.equal(h.field(section).tagName, 'TEXTAREA');
    assert.equal(h.field(section).disabled, true);
    assert.equal(h.field(section).getBoundingClientRect().height, 35);
  }
});

test('background candidate refresh keeps all nodes, text focus, selection and listeners', () => {
  const h = harness({ modes: { character: 'fixed' }, fixed: { character: 'red hair, blue eyes\n'.repeat(8) } }); h.ctx.renderDimensions();
  const cards = [...h.list.children], field = h.field('character');
  field.focus(); field.setSelectionRange(4, 8, 'backward'); field.scrollTop = 7;
  const listeners = (field.listeners.get('input') || []).length;
  for (let index = 0; index < 4; index++) { h.counts.pose++; h.ctx.renderDimensions(); }
  cards.forEach((card, index) => assert.equal(h.list.children[index], card));
  assert.equal(h.field('character'), field); assert.equal(h.document.activeElement, field);
  assert.equal(field.selectionStart, 4); assert.equal(field.selectionEnd, 8); assert.equal(field.selectionDirection, 'backward');
  assert.equal(field.scrollTop, 7); assert.equal((field.listeners.get('input') || []).length, listeners);
  assert.match(h.row('pose').querySelector('.dimension-heading small').textContent, /6/);
  assert.equal(h.frames.size, 0, 'unchanged cards do not replay motion');
});

test('mode intent and accessibility update immediately while selecting the same mode is a no-op', () => {
  const h = harness(); h.ctx.renderDimensions();
  const field = h.field('pose'), button = h.row('pose').querySelector('[data-mode="fixed"]');
  button.focus(); button.dispatch('click');
  assert.equal(h.ctx.draft.modes.pose, 'fixed'); assert.equal(field.disabled, false);
  assert.equal(button.getAttribute('aria-pressed'), 'true'); assert.equal(h.document.activeElement, button);
  const writes = h.saves(), count = h.springs.length;
  button.dispatch('click');
  assert.equal(h.saves(), writes, 'a repeated selection does not persist a new change');
  assert.equal(h.springs.length, count, 'a repeated selection does not restart any spring');
  assert.equal(h.row('pose').querySelector('[data-mode="off"]').getAttribute('aria-pressed'), 'false');
  h.drain();
});

test('a pool switch leaves other cards and their in-flight motion untouched', () => {
  const h = harness({ fixed: { character: 'first\nsecond\nthird', pose: 'standing\nlooking back' } }); h.ctx.renderDimensions();
  h.ctx.setDimensionMode('character', 'fixed'); h.step(60);
  const node = h.field('character'), before = h.snapshot(node, 'dimension-height'), cards = [...h.list.children];
  h.ctx.setDimensionMode('pose', 'fixed');
  assert.deepEqual(h.snapshot(node, 'dimension-height'), before);
  cards.forEach((card, index) => assert.equal(h.list.children[index], card));
  assert.equal(h.ctx.draft.modes.character, 'fixed'); assert.equal(h.ctx.draft.modes.pose, 'fixed');
  h.drain();
});

test('mode indicator reverses from the visible position and velocity without blocking clicks', () => {
  const h = harness(); h.ctx.renderDimensions();
  const indicator = h.row('pose').querySelector('.dimension-mode-thumb');
  assert.ok(indicator, 'the segmented group has one moving selected surface');
  h.ctx.setDimensionMode('pose', 'fixed'); h.step(80);
  const before = h.snapshot(indicator, 'indicator');
  assert.ok(before.value.x > 0 && before.value.x < before.target.x);
  h.ctx.setDimensionMode('pose', 'off');
  const reverse = h.snapshot(indicator, 'indicator');
  assert.equal(reverse.value.x, before.value.x); assert.equal(reverse.velocity.x, before.velocity.x);
  h.step(40); h.ctx.setDimensionMode('pose', 'pool'); h.drain();
  assert.equal(h.ctx.draft.modes.pose, 'pool');
  assert.equal(h.snapshot(indicator, 'indicator').value.x, h.snapshot(indicator, 'indicator').target.x);
  assert.equal(h.row('pose').querySelector('[data-mode="pool"]').getAttribute('aria-pressed'), 'true');
});

test('fixed prompt height follows explicit lines and soft wrapping up to six lines', () => {
  const h = harness({ modes: { pose: 'fixed' } }); h.ctx.renderDimensions();
  const field = h.input('pose', 'one\ntwo\nthree'); h.drain();
  assert.equal(field.getBoundingClientRect().height, 75);
  h.input('pose', 'word '.repeat(80)); h.drain();
  assert.equal(field.getBoundingClientRect().height, 135);
  assert.equal(field.style.overflowY, 'auto', 'long input remains available by internal scrolling');
  h.input('pose', 'short'); h.drain();
  assert.equal(field.getBoundingClientRect().height, 35);
  assert.notEqual(field.style.overflowY, 'auto');
});

test('closing and reopening a growing editor carries current height and velocity and preserves text', () => {
  const text = 'one\ntwo\nthree\nfour\nfive';
  const h = harness({ fixed: { pose: text } }); h.ctx.renderDimensions();
  const field = h.field('pose'); h.ctx.setDimensionMode('pose', 'fixed'); h.step(70);
  const opening = h.snapshot(field, 'dimension-height');
  assert.ok(opening.value.height > 35 && opening.value.height < 115);
  h.ctx.setDimensionMode('pose', 'off');
  const closing = h.snapshot(field, 'dimension-height');
  assert.equal(closing.value.height, opening.value.height); assert.equal(closing.velocity.height, opening.velocity.height);
  assert.equal(field.disabled, true); assert.equal(field.value, text); assert.equal(h.ctx.draft.fixed.pose, text);
  h.step(40); const interrupted = h.snapshot(field, 'dimension-height'); h.ctx.setDimensionMode('pose', 'fixed');
  assert.equal(h.snapshot(field, 'dimension-height').value.height, interrupted.value.height);
  assert.equal(h.snapshot(field, 'dimension-height').velocity.height, interrupted.velocity.height);
  h.drain(); assert.equal(field.disabled, false); assert.equal(field.getBoundingClientRect().height, 115);
  h.ctx.setDimensionMode('pose', 'pool'); h.drain();
  assert.equal(field.getBoundingClientRect().height, 35); assert.equal(field.value, text);
  assert.equal(field.scrollTop, 0); assert.equal(field.style.willChange || '', '');
});

test('candidate refresh while height is moving does not restart or settle the editor motion', () => {
  const h = harness({ modes: { pose: 'fixed' } }); h.ctx.renderDimensions();
  const field = h.input('pose', 'one\ntwo\nthree\nfour'); h.step(70);
  field.focus(); field.setSelectionRange(2, 6);
  const before = h.snapshot(field, 'dimension-height'), count = h.springs.length;
  h.counts.pose = 39; h.ctx.renderDimensions();
  assert.deepEqual(h.snapshot(field, 'dimension-height'), before);
  assert.equal(h.springs.length, count); assert.equal(h.document.activeElement, field);
  assert.equal(field.selectionStart, 2); assert.equal(field.selectionEnd, 6);
  h.drain(); assert.equal(field.getBoundingClientRect().height, 95);
});

test('programmatic normalization and undo synchronize the editor height without replacing it', async () => {
  const original = 'One\nTwo\nThree';
  const h = harness({ modes: { pose: 'fixed' }, fixed: { pose: original } }); h.ctx.renderDimensions();
  const field = h.field('pose'), pending = h.ctx.normalizePromptFields({ fixed_pose: original });
  h.requests[0].resolve({ fields: { fixed_pose: 'one, two, three' }, changed: true, changes: [{ field: 'fixed_pose', before: original, after: 'one, two, three' }] });
  await pending; h.drain();
  assert.equal(h.field('pose'), field); assert.equal(field.value, 'one, two, three'); assert.equal(field.getBoundingClientRect().height, 35);
  h.ctx.undoPromptNormalization(); h.drain();
  assert.equal(field.value, original); assert.equal(h.ctx.draft.fixed.pose, original); assert.equal(field.getBoundingClientRect().height, 75);
});

test('a late normalization cannot overwrite a later edit or reopen a non-fixed editor', async () => {
  for (const next of ['edit', 'pool', 'off']) {
    const h = harness({ modes: { pose: 'fixed' }, fixed: { pose: 'One\nTwo' } }); h.ctx.renderDimensions();
    const field = h.field('pose'), pending = h.ctx.normalizePromptFields({ fixed_pose: field.value });
    if (next === 'edit') h.input('pose', 'new\nuser\ntext');
    else h.ctx.setDimensionMode('pose', next);
    h.requests[0].resolve({ fields: { fixed_pose: 'one, two' }, changed: true, changes: [] });
    await pending; h.drain();
    assert.equal(field.value, next === 'edit' ? 'new\nuser\ntext' : 'one, two');
    assert.equal(h.ctx.draft.modes.pose, next === 'edit' ? 'fixed' : next);
    assert.equal(field.getBoundingClientRect().height, next === 'edit' ? 75 : 35);
    assert.equal(field.disabled, next !== 'edit');
  }
});

test('restoring another draft updates existing values and lands directly at the correct height', () => {
  const h = harness({ modes: { pose: 'fixed' }, fixed: { pose: 'old' } }); h.ctx.renderDimensions();
  const field = h.field('pose');
  h.input('pose', 'one\ntwo\nthree\nfour\nfive\nsix'); h.step(60);
  assert.equal(h.snapshot(field, 'dimension-height').active, true);
  h.ctx.draft = { ...h.ctx.draft, modes: { pose: 'fixed' }, fixed: { pose: 'restored\nsecond\nthird\nfourth' } };
  h.ctx.renderDimensions({ immediate: true });
  assert.equal(h.field('pose'), field); assert.equal(field.getBoundingClientRect().height, 95);
  assert.equal(field.value, h.ctx.draft.fixed.pose); assert.equal(h.frames.size, 0);
  h.ctx.draft.modes.pose = 'off'; h.ctx.renderDimensions({ immediate: true });
  assert.equal(field.getBoundingClientRect().height, 35); assert.equal(field.disabled, true);
  assert.match(field.value, /restored/); assert.equal(h.frames.size, 0);
});

test('a hidden pool panel defers measurement and restores its latest text at the visible width', () => {
  const h = harness({ modes: { pose: 'fixed' }, fixed: { pose: 'one\ntwo' } }); h.ctx.renderDimensions();
  const field = h.field('pose');
  h.list.hidden = true; h.resize(320);
  h.ctx.draft.fixed.pose = 'latest\nsecond\nthird\nfourth'; h.ctx.renderDimensions();
  assert.equal(field.dimensionPromptMotion.pending, true); assert.equal(h.frames.size, 1);
  h.step();
  assert.equal(h.frames.size, 0, 'a panel that stays hidden does not poll on animation frames');
  h.list.hidden = false; h.resize(320);
  assert.equal(h.field('pose'), field); assert.equal(field.value, h.ctx.draft.fixed.pose);
  assert.equal(field.getBoundingClientRect().height, 95); assert.equal(field.dimensionPromptMotion.pending, false);
  assert.equal(h.frames.size, 0, 'revealing a retained panel lands directly at its latest layout');
});

test('same-frame hidden restore uses one shared frame when no zero-width resize is delivered', () => {
  const h = harness({ modes: { pose: 'fixed', character: 'fixed' } }); h.ctx.renderDimensions();
  const field = h.field('pose'), character = h.field('character');
  h.resize(320); // Observer knows the last visible width before the panel is hidden.
  h.list.hidden = true;
  h.ctx.draft.fixed.pose = 'new\nsecond\nthird\nfourth';
  h.ctx.draft.fixed.character = 'changed\nsecond\nthird';
  h.ctx.renderDimensions({ immediate: true });
  assert.equal(h.frames.size, 1, 'all pending editors share one remeasurement frame');
  h.list.hidden = false;
  h.resize(320); // Same width gives the observer no new layout work.
  assert.equal(field.dimensionPromptMotion.pending, true);
  h.step();
  assert.equal(h.field('pose'), field); assert.equal(h.field('character'), character);
  assert.equal(field.getBoundingClientRect().height, 95); assert.equal(character.getBoundingClientRect().height, 75);
  assert.equal(field.dimensionPromptMotion.pending, false); assert.equal(character.dimensionPromptMotion.pending, false);
  assert.equal(h.frames.size, 0, 'restoration settles immediately instead of starting a polling loop');
});

test('a focused editor keeps the new caret visible throughout height growth and caps long input', () => {
  const h = harness({ modes: { pose: 'fixed' } }); h.ctx.renderDimensions();
  const field = h.field('pose'); field.focus(); h.input('pose', 'one\ntwo\nthree\nfour\nfive');
  for (let index = 0; index < 6; index++) {
    const state = field.dimensionPromptMotion;
    assert.ok(state.caretBottom <= field.scrollTop + field.clientHeight - state.paddingBottom + .1, 'the live caret fits inside each presentation frame');
    h.step(30);
  }
  h.drain(); assert.equal(field.scrollTop, 0); assert.equal(field.selectionEnd, field.value.length);
  h.input('pose', 'line\n'.repeat(10)); h.drain();
  assert.equal(field.getBoundingClientRect().height, 135); assert.ok(field.scrollTop > 0);
  assert.equal(field.selectionEnd, field.value.length); assert.equal(h.document.activeElement, field);
});

test('width changes remeasure wrapping without replacing the focused input', () => {
  const h = harness({ modes: { pose: 'fixed' }, fixed: { pose: 'a'.repeat(64) } }); h.ctx.renderDimensions();
  const field = h.field('pose'); field.focus(); field.setSelectionRange(8, 17);
  assert.equal(field.getBoundingClientRect().height, 55);
  h.resize(180); h.drain();
  assert.equal(field.getBoundingClientRect().height, 95);
  assert.equal(h.field('pose'), field); assert.equal(h.document.activeElement, field);
  assert.equal(field.selectionStart, 8); assert.equal(field.selectionEnd, 17);
  h.resize(320); h.drain(); assert.equal(field.getBoundingClientRect().height, 55);
});

test('enabling reduced motion or hiding the page settles the latest height and indicator intent', () => {
  for (const action of ['reduce', 'hide']) {
    const h = harness({ fixed: { pose: 'one\ntwo\nthree\nfour' } }); h.ctx.renderDimensions();
    const field = h.field('pose'); h.ctx.setDimensionMode('pose', 'fixed'); h.step(55);
    if (action === 'reduce') h.reduce(); else h.hide(true);
    assert.equal(field.getBoundingClientRect().height, 95); assert.equal(field.style.willChange || '', '');
    assert.equal(h.frames.size, 0);
    h.ctx.setDimensionMode('pose', 'off');
    assert.equal(field.getBoundingClientRect().height, 35); assert.equal(field.disabled, true); assert.equal(h.frames.size, 0);
    if (action === 'hide') { h.hide(false); h.drain(); assert.equal(field.getBoundingClientRect().height, 35); }
  }
});
