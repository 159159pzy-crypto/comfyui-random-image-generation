const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Real spring integration with a deterministic clock; the geometry models only
// the toolbar's two flex rows, so these tests catch presentation discontinuity.
function harness({ scrollbarHeight = 0 } = {}) {
  let time = 0, frameId = 0, width = 1000;
  const frames = new Map(), mediaListeners = [], elements = {};
  const media = { matches: false, addEventListener(name, listener) { if (name === 'change') mediaListeners.push(listener); } };
  const document = { hidden: false, addEventListener() {}, getElementById: id => elements[id] || null,
    documentElement: { setAttribute() {}, removeAttribute() {} } };
  function element(id) {
    const classes = new Set();
    const node = { id, dataset: {}, hidden: false, inert: false,
      style: { removeProperty(name) { delete this[name]; } },
      classList: { contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name), toggle(name, active) { if (active) classes.add(name); else classes.delete(name); } },
      querySelector: () => null,
    };
    elements[id] = node;
    return node;
  }
  const root = element('artworkToolbar'), layout = element('artworkToolbarLayout'), reveal = element('artworkFolderReveal');
  const strip = element('artworkFolderStrip'), toggle = element('artworkFoldersToggle'), manage = element('artworkManage');
  const browser = element('browser'), actions = element('actions');
  strip.hidden = true;
  const selecting = () => root.classList.contains('is-selecting');
  const rect = (left, top, width, height) => ({ left, top, right: left + width, bottom: top + height, width, height });
  const browserWidth = () => width - (selecting() ? 0 : 64);
  const available = () => browserWidth() - 104 - 44 - 4;
  const scrollbar = () => strip.hidden ? 0 : scrollbarHeight;
  root.getBoundingClientRect = () => rect(0, 0, width, root.style.height ? parseFloat(root.style.height) : (selecting() ? 114 : 58) + scrollbar());
  root.querySelector = selector => selector === '.artwork-selection-actions' ? actions : null;
  layout.querySelector = selector => selector === '.artwork-browser' ? browser : selector === '.artwork-selection-actions' ? actions : null;
  layout.getBoundingClientRect = () => rect(0, 12, width, (selecting() ? 100 : 44) + scrollbar());
  browser.getBoundingClientRect = () => rect(0, 12, browserWidth(), 44 + scrollbar());
  toggle.getBoundingClientRect = () => rect(0, 12, 104, 44);
  manage.getBoundingClientRect = () => rect(108 + reveal.getBoundingClientRect().width, 12, 44, 44);
  // Once its inline animation width is released, flex: 1 responds immediately
  // to the selected-mode class. A capture taken after that class changes jumps.
  reveal.getBoundingClientRect = () => rect(104, 12, reveal.style.width ? parseFloat(reveal.style.width) : reveal.classList.contains('is-expanded') ? available() : 0, 44 + scrollbar());
  actions.getBoundingClientRect = () => {
    const translation = actions.style.transform?.match(/translate3d\(([-\d.]+)px, ([-\d.]+)px/);
    return rect(width - 60 + Number(translation?.[1] || 0), (selecting() ? 68 + scrollbar() : 12 + scrollbar() / 2) + Number(translation?.[2] || 0), 60, 44);
  };
  const ctx = vm.createContext({ document, performance: { now: () => time }, matchMedia: () => media,
    requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); },
  });
  for (const filename of ['motion.js', 'artwork-favorites.js']) vm.runInContext(fs.readFileSync(path.join(__dirname, '../static', filename), 'utf8'), ctx);
  ctx.positionArtworkPopover = () => {};
  const state = vm.runInContext('artworkFavoritesState', ctx);
  function update(changes = {}, options = {}) {
    const before = ctx.artworkCaptureToolbar();
    Object.assign(state, changes); root.classList.toggle('is-selecting', state.selecting);
    ctx.artworkLayoutToolbar(before, options);
  }
  function step(elapsed = 1000 / 60) {
    time += elapsed;
    const callbacks = [...frames.values()]; frames.clear();
    callbacks.forEach(callback => callback(time));
  }
  function drain() { let limit = 300; while (frames.size && limit--) step(); assert.ok(limit > 0, 'all motion must settle'); }
  return { ctx, state, root, reveal, strip, manage, actions, frames, update, step, drain,
    snapshot: (owner, channel) => ctx.StudioMotion.snapshot(owner, channel),
    resize(next) { width = next; update({}, { immediate: true }); },
    reduce() { media.matches = true; mediaListeners.forEach(listener => listener()); },
  };
}

test('initial toolbar layout settles directly without an entrance or leftover size overrides', () => {
  const h = harness(); h.update();
  assert.equal(h.frames.size, 0);
  assert.equal(h.strip.hidden, true); assert.equal(h.reveal.inert, true);
  assert.equal(h.root.getBoundingClientRect().height, 58);
  for (const [node, property] of [[h.root, 'height'], [h.reveal, 'width'], [h.actions, 'transform']]) assert.equal(node.style[property], undefined);
});

test('folder width reverses at its current value and velocity and stale closing cannot hide a reopened strip', () => {
  const h = harness(); h.update(); h.update({ expanded: true });
  assert.equal(h.reveal.getBoundingClientRect().width, 0, 'opening must not jump to its final width');
  assert.equal(h.strip.hidden, false); assert.equal(h.reveal.inert, false);
  h.step(80);
  const opening = h.snapshot(h.reveal, 'artwork-width');
  assert.ok(opening.value.width > 0 && opening.value.width < opening.target.width);
  h.update({ expanded: false });
  const closing = h.snapshot(h.reveal, 'artwork-width');
  assert.equal(closing.value.width, opening.value.width); assert.equal(closing.velocity.width, opening.velocity.width);
  assert.equal(h.reveal.inert, true); assert.equal(h.strip.hidden, false, 'contents remain until the exit finishes');
  h.step(70); h.update({ expanded: true }); h.drain();
  assert.equal(h.strip.hidden, false); assert.equal(h.reveal.inert, false);
  assert.equal(h.reveal.dataset.motionState, 'open'); assert.equal(h.reveal.style.width, undefined);
});

test('selection height and action position remain continuous when a user reverses mid-flight', () => {
  const h = harness(); h.update();
  const originalActions = h.actions.getBoundingClientRect();
  h.update({ selecting: true, expanded: true });
  assert.equal(h.root.getBoundingClientRect().height, 58, 'the gallery must not jump down when selection begins');
  assert.equal(h.actions.getBoundingClientRect().bottom, originalActions.bottom, 'the selection button starts where it was');
  h.step(70);
  const height = h.snapshot(h.root, 'artwork-height'), visibleActions = h.actions.getBoundingClientRect();
  assert.ok(height.value.height > 58 && height.value.height < 114);
  h.update({ selecting: false });
  const reverse = h.snapshot(h.root, 'artwork-height');
  assert.equal(reverse.value.height, height.value.height); assert.equal(reverse.velocity.height, height.velocity.height);
  assert.equal(h.actions.getBoundingClientRect().bottom, visibleActions.bottom);
  h.drain();
  assert.equal(h.root.getBoundingClientRect().height, 58); assert.equal(h.root.style.height, undefined);
  assert.equal(h.actions.style.transform, undefined); assert.equal(h.root.classList.contains('is-layout-moving'), false);
});

test('entering and leaving selection from settled expanded layouts never snaps the management button', () => {
  const h = harness(); h.update(); h.update({ expanded: true }); h.drain();
  const browseWidth = h.reveal.getBoundingClientRect().width, browseLeft = h.manage.getBoundingClientRect().left;
  assert.equal(h.reveal.style.width, undefined, 'the expanded row begins with intrinsic flex width');
  h.update({ selecting: true });
  assert.equal(h.reveal.getBoundingClientRect().width, browseWidth, 'capture must precede the selected-mode flex change');
  assert.equal(h.manage.getBoundingClientRect().left, browseLeft);
  h.drain();
  const selectedWidth = h.reveal.getBoundingClientRect().width, selectedLeft = h.manage.getBoundingClientRect().left;
  assert.ok(selectedWidth > browseWidth);
  assert.equal(h.reveal.style.width, undefined);
  h.update({ selecting: false });
  assert.equal(h.reveal.getBoundingClientRect().width, selectedWidth, 'cancel must start from the former full-row width');
  assert.equal(h.manage.getBoundingClientRect().left, selectedLeft);
  h.drain(); assert.equal(h.reveal.getBoundingClientRect().width, browseWidth);
});

test('a scrollbar keeps the shell pinned after height settles until the slower width exit completes', () => {
  const h = harness({ scrollbarHeight: 6 }); h.update(); h.update({ expanded: true }); h.drain();
  assert.equal(h.root.getBoundingClientRect().height, 64);
  h.update({ expanded: false });
  let limit = 300;
  while (h.snapshot(h.root, 'artwork-height').active && limit--) h.step();
  assert.ok(limit > 0); assert.equal(h.snapshot(h.reveal, 'artwork-width').active, true);
  assert.equal(h.strip.hidden, false, 'the old horizontal scrollbar still contributes to intrinsic row height');
  assert.equal(h.root.getBoundingClientRect().height, 58, 'height completion must not expose that scrollbar height');
  assert.equal(h.root.style.height, '58px');
  h.update();
  assert.equal(h.root.getBoundingClientRect().height, 58, 'a background refresh during the remaining exit must also retain the shell');
  h.drain();
  assert.equal(h.strip.hidden, true); assert.equal(h.root.getBoundingClientRect().height, 58);
  assert.equal(h.root.style.height, undefined); assert.equal(h.root.classList.contains('is-layout-moving'), false);
});

test('unchanged background rendering does not restart the active geometry or action motion', () => {
  const h = harness(); h.update(); h.update({ selecting: true, expanded: true }); h.step(80);
  const owners = [[h.root, 'artwork-height'], [h.reveal, 'artwork-width'], [h.actions, 'artwork-actions']];
  const before = owners.map(([owner, channel]) => h.snapshot(owner, channel));
  const rect = h.actions.getBoundingClientRect(), height = h.root.getBoundingClientRect().height;
  h.update();
  owners.forEach(([owner, channel], index) => assert.deepEqual(h.snapshot(owner, channel), before[index]));
  assert.equal(h.root.getBoundingClientRect().height, height); assert.deepEqual(h.actions.getBoundingClientRect(), rect);
  h.drain(); h.update(); assert.equal(h.frames.size, 0, 'a settled refresh should not replay any entrance');
});

test('reduced motion settles the latest intent immediately and removes temporary presentation styles', () => {
  const h = harness(); h.update(); h.update({ selecting: true, expanded: true }); h.step(60); h.reduce();
  assert.equal(h.frames.size, 0); assert.equal(h.root.getBoundingClientRect().height, 114);
  assert.equal(h.root.style.height, undefined); assert.equal(h.actions.style.transform, undefined); assert.equal(h.reveal.style.width, undefined);
  h.update({ selecting: false, expanded: false });
  assert.equal(h.frames.size, 0); assert.equal(h.root.getBoundingClientRect().height, 58);
  assert.equal(h.strip.hidden, true); assert.equal(h.reveal.inert, true);
});

test('a resize during motion retargets to current available width and leaves no inline geometry', () => {
  const h = harness(); h.update(); h.update({ selecting: true, expanded: true }); h.step(70); h.resize(390);
  assert.equal(h.frames.size, 0);
  assert.equal(h.reveal.getBoundingClientRect().width, 390 - 104 - 44 - 4);
  assert.equal(h.reveal.style.width, undefined); assert.equal(h.strip.style.width, undefined);
  assert.equal(h.root.style.height, undefined); assert.equal(h.actions.style.transform, undefined);
  h.update({ selecting: false, expanded: false }); h.drain();
  assert.equal(h.reveal.getBoundingClientRect().width, 0); assert.equal(h.strip.hidden, true);
});
