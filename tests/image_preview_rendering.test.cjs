const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function classList() {
  const values = new Set();
  return {
    add: (...names) => names.forEach(name => values.add(name)),
    remove: (...names) => names.forEach(name => values.delete(name)),
    contains: name => values.has(name),
    toggle(name, enabled = !values.has(name)) { enabled ? values.add(name) : values.delete(name); return enabled; },
  };
}

function element() {
  const attrs = new Map(), listeners = new Map(), writes = [], animations = [];
  const properties = { removeProperty(name) { delete this[name]; }, setProperty(name, value) { this[name] = String(value); } };
  return {
    attrs, listeners, writes, animations, classList: classList(), dataset: {}, hidden: false,
    style: new Proxy(properties, { set(target, name, value) { writes.push([name, value]); target[name] = value; return true; } }),
    addEventListener: (name, callback) => listeners.set(name, callback),
    setAttribute: (name, value) => attrs.set(name, String(value)),
    getAttribute: name => attrs.get(name) ?? null,
    removeAttribute: name => attrs.delete(name),
    set src(value) { attrs.set('src', value); }, get src() { return attrs.get('src') || ''; },
    get currentSrc() { return this.src; },
    setPointerCapture() {}, releasePointerCapture() {}, focus() {},
    getAnimations: () => animations.filter(animation => !animation.cancelled),
    animate(frames, options) {
      let finish;
      const finished = new Promise(resolve => { finish = resolve; });
      const animation = { frames, options, finished, finish, cancelled: false,
        cancel() { this.cancelled = true; }, commitStyles() {} };
      animations.push(animation);
      return animation;
    },
    open: true, close() { this.open = false; }, showModal() { this.open = true; },
  };
}

function setup({ width = 4000, height = 3000, reducedMotion = false, devicePixelRatio = 1, motion = false } = {}) {
  const ui = Object.fromEntries(['imagePreviewImage', 'imagePreviewStage', 'imagePreviewDialog',
    'imagePreviewVeil', 'imagePreviewStatus', 'imagePreviewAnnouncement', 'imagePreviewPrevious',
    'imagePreviewNext', 'imagePreviewTitle', 'closeImagePreview', 'detailImage', 'detailDialog', 'historyCount'].map(name => [name, element()]));
  const stage = ui.imagePreviewStage, image = ui.imagePreviewImage;
  Object.assign(stage, { clientWidth: 1000, clientHeight: 800 });
  stage.getBoundingClientRect = () => ({ left: 20, top: 30, width: stage.clientWidth, height: stage.clientHeight,
    right: 20 + stage.clientWidth, bottom: 30 + stage.clientHeight });
  Object.assign(image, { naturalWidth: width, naturalHeight: height, decode: async () => {} });
  image.src = '/api/images/1';
  const frames = new Map();
  let now = 0, serial = 0, resize;
  const root = element();
  const ctx = vm.createContext({
    console, ui, devicePixelRatio, document: { documentElement: root },
    performance: { now: () => now },
    requestAnimationFrame(callback) { frames.set(++serial, callback); return serial; },
    cancelAnimationFrame(id) { frames.delete(id); },
    ResizeObserver: class { constructor(callback) { resize = callback; } observe() {} },
    matchMedia: () => ({ matches: reducedMotion }),
    getComputedStyle: target => ({ paddingLeft: '0', paddingRight: '0', paddingTop: '0', paddingBottom: '0',
      transform: target.style?.transform || 'none', clipPath: target.style?.clipPath || 'none',
      opacity: target.style?.opacity || '1', width: target.style?.width, height: target.style?.height,
      getPropertyValue: name => name === '--spring' ? 'ease-out' : '' }),
    selectedRecord: { id: 1, filename: 'original.png' }, historyItems: [], historyCache: new Map(), historyPage: 1, historyPages: 1,
    Image: class { decode() { return Promise.resolve(); } }, Element: class {},
  });
  if (motion) vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/motion.js'), 'utf8'), ctx);
  vm.runInContext(source.slice(source.indexOf('function historyViewState('), source.indexOf('function cacheHistoryPage(')), ctx);
  vm.runInContext(source.slice(source.indexOf('const imagePreview ='), source.indexOf('let regenerationOptions =')), ctx);
  vm.runInContext(source.slice(source.indexOf('const IMAGE_PREVIEW_MAX_ZOOM'), source.indexOf('function collapseRegenerationDrawer')), ctx);
  const doubleClickStart = source.indexOf('ui.imagePreviewImage.addEventListener("dblclick"');
  const keyboard = source.indexOf('ui.imagePreviewDialog.addEventListener("keydown"');
  vm.runInContext(source.slice(doubleClickStart, source.indexOf('ui.imagePreviewVeil.addEventListener', doubleClickStart)), ctx);
  vm.runInContext(source.slice(keyboard, source.indexOf('ui.imagePreviewDialog.addEventListener("cancel"', keyboard)), ctx);
  const resizeStart = source.indexOf('new ResizeObserver(() => {');
  const resizeEnd = source.indexOf('}).observe(ui.imagePreviewStage);', resizeStart) + '}).observe(ui.imagePreviewStage);'.length;
  vm.runInContext(source.slice(resizeStart, resizeEnd), ctx);
  const state = vm.runInContext('imagePreview', ctx);
  Object.assign(state, { phase: 'open', context: { kind: 'random' } });
  function frame(ms = 16) {
    now += ms;
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach(callback => callback(now));
  }
  function settle() {
    for (let index = 0; frames.size && index < 120; index++) frame();
    assert.equal(frames.size, 0, 'rendering or zoom animation must settle');
  }
  function wheel(deltaY, extra = {}) {
    ctx.imagePreviewWheel({ deltaY, deltaMode: 0, clientX: 520, clientY: 430, preventDefault() {}, ...extra });
  }
  function doubleClick(extra = {}) {
    image.listeners.get('dblclick')({ clientX: 520, clientY: 430, x: 520, y: 430, preventDefault() {}, ...extra });
  }
  function pointer(type, id, x, y, extra = {}) {
    ctx[`imagePreviewPointer${type}`]({ type: `pointer${type.toLowerCase()}`, pointerId: id, pointerType: 'touch',
      button: 0, clientX: x, clientY: y, preventDefault() {}, ...extra });
  }
  function key(key, extra = {}) {
    ui.imagePreviewDialog.listeners.get('keydown')({ key, preventDefault() {}, stopPropagation() {}, ...extra });
  }
  ctx.layoutImagePreview();
  settle();
  image.writes.length = 0;
  return { ctx, state, ui, image, stage, frames, frame, settle, wheel, doubleClick, pointer, key, resize };
}

function near(actual, expected, message) {
  assert.ok(Math.abs(actual - expected) < 1e-7, `${message || 'values should match'}: ${actual} !== ${expected}`);
}

test('fit uses natural dimensions and never enlarges small originals', () => {
  const large = setup();
  near(large.state.fit, .23);
  near(parseFloat(large.image.style.width), 920);
  near(parseFloat(large.image.style.height), 690);
  const small = setup({ width: 240, height: 160 });
  assert.equal(small.state.fit, 1);
  assert.equal(small.image.style.width, '240px');
  assert.equal(small.image.style.height, '160px');
});

for (const devicePixelRatio of [1, 2]) {
  test(`double-click renders original pixels as CSS dimensions at DPR ${devicePixelRatio}`, () => {
    const h = setup({ devicePixelRatio });
    h.doubleClick(); h.settle();
    near(parseFloat(h.image.style.width), 4000);
    near(parseFloat(h.image.style.height), 3000);
    near(h.state.fit * h.state.zoom, 1);
    assert.doesNotMatch(h.image.style.transform, /scale\(/, 'zoom must alter rendered dimensions rather than scaling a fitted bitmap');
    h.doubleClick(); h.settle();
    assert.equal(h.state.zoom, 1);
    near(parseFloat(h.image.style.width), 920);
  });
}

test('double-click on an original that already fits keeps its original size', () => {
  const h = setup({ width: 240, height: 160 });
  h.doubleClick(); h.settle();
  assert.equal(h.state.zoom, 1);
  assert.equal(h.image.style.width, '240px');
  assert.equal(h.image.style.height, '160px');
});

test('zoom bounds are fit through 400 percent of original dimensions', () => {
  const h = setup();
  h.ctx.setImagePreviewZoom(1000); h.settle();
  near(h.state.zoom * h.state.fit, 4);
  near(parseFloat(h.image.style.width), 16000);
  near(parseFloat(h.image.style.height), 12000);
  h.ctx.setImagePreviewZoom(-100); h.settle();
  assert.equal(h.state.zoom, 1);
  assert.equal(h.state.x, 0); assert.equal(h.state.y, 0);
});

test('wheel zoom retains the image point under the pointer while clamping pan to visible edges', () => {
  const h = setup();
  h.ctx.setImagePreviewZoom(2, { x: 620, y: 490 }); h.settle();
  near(h.state.x, -100); near(h.state.y, -60);
  const before = { x: (100 - h.state.x) / h.state.zoom, y: (60 - h.state.y) / h.state.zoom };
  h.wheel(-120, { clientX: 620, clientY: 490 }); h.settle();
  near((100 - h.state.x) / h.state.zoom, before.x);
  near((60 - h.state.y) / h.state.zoom, before.y);
  h.state.x = 100000; h.state.y = -100000;
  h.ctx.applyImagePreviewTransform(); h.settle();
  near(h.state.x, (parseFloat(h.image.style.width) - h.stage.clientWidth) / 2);
  near(h.state.y, -(parseFloat(h.image.style.height) - h.stage.clientHeight) / 2);
});

test('a burst of wheel events combines its zoom changes into one rendered frame', () => {
  const h = setup();
  h.wheel(-80); h.wheel(-80); h.wheel(-80);
  assert.equal(h.frames.size, 1);
  assert.equal(h.image.writes.length, 0, 'input events should not each force a bitmap resize');
  h.frame();
  near(h.state.zoom, Math.exp(.36));
  near(parseFloat(h.image.style.width), 920 * Math.exp(.36));
  assert.equal(h.image.writes.filter(([name]) => name === 'width').length, 1);
});

test('smooth zoom renders intermediate pixel dimensions and wheel interruption continues from the visible size', () => {
  const h = setup();
  h.doubleClick(); h.frame(16); h.frame(55);
  const displayedWidth = parseFloat(h.image.style.width);
  assert.ok(displayedWidth > 920 && displayedWidth < 4000);
  near(displayedWidth, h.state.width * h.state.zoom);
  h.wheel(-80); h.settle();
  near(parseFloat(h.image.style.width), displayedWidth * Math.exp(.12));
  assert.doesNotMatch(h.image.style.transform, /scale\(/);
});

test('drag interrupts a smooth zoom at its visible size without changing zoom', () => {
  const h = setup();
  h.doubleClick(); h.frame(16); h.frame(55);
  const displayedWidth = parseFloat(h.image.style.width), displayedZoom = h.state.zoom;
  h.pointer('Down', 1, 520, 430, { pointerType: 'mouse' });
  h.pointer('Move', 1, 555, 455, { pointerType: 'mouse' });
  h.pointer('Up', 1, 555, 455, { pointerType: 'mouse' });
  h.settle();
  near(parseFloat(h.image.style.width), displayedWidth);
  near(h.state.zoom, displayedZoom);
  near(h.state.x, 35); near(h.state.y, 25);
});

test('pinch and touch double-tap share the original-size bounds', () => {
  const h = setup();
  h.pointer('Down', 1, 470, 430); h.pointer('Down', 2, 570, 430);
  h.pointer('Move', 2, 1070, 430); h.settle();
  near(h.state.zoom, 6);
  near(parseFloat(h.image.style.width), 5520);
  h.pointer('Move', 2, 100000, 430); h.settle();
  near(h.state.zoom * h.state.fit, 4);
  h.pointer('Up', 1, 470, 430); h.pointer('Up', 2, 100000, 430);
  h.ctx.resetImagePreviewTransform(); h.settle();
  h.pointer('Down', 3, 520, 430); h.pointer('Up', 3, 520, 430);
  h.frame(50);
  h.pointer('Down', 4, 520, 430); h.pointer('Up', 4, 520, 430); h.settle();
  near(parseFloat(h.image.style.width), 4000);
});

test('keyboard zoom and pan use the same dimensions and reset to fit', () => {
  const h = setup();
  h.key('+'); h.settle();
  near(h.state.zoom, 1.25); near(parseFloat(h.image.style.width), 1150);
  h.key('ArrowRight'); h.key('ArrowDown'); h.settle();
  assert.equal(h.state.x, 24); assert.equal(h.state.y, 24);
  h.key('0'); h.settle();
  assert.equal(h.state.zoom, 1); assert.equal(h.state.x, 0); assert.equal(h.state.y, 0);
  near(parseFloat(h.image.style.width), 920);
});

test('reduced motion changes dimensions in one frame without a zoom tween', () => {
  const h = setup({ reducedMotion: true });
  h.doubleClick();
  assert.equal(h.frames.size, 1);
  h.frame();
  near(parseFloat(h.image.style.width), 4000);
  assert.equal(h.frames.size, 0);
  assert.equal(h.image.animations.length, 0);
});

test('resizing preserves fit mode or the actual original scale and interrupts an in-flight zoom', () => {
  const fit = setup();
  fit.stage.clientWidth = 500; fit.stage.clientHeight = 400;
  fit.resize(); fit.settle();
  assert.equal(fit.state.zoom, 1); near(parseFloat(fit.image.style.width), 460);
  const original = setup();
  original.doubleClick(); original.settle();
  original.stage.clientWidth = 500; original.stage.clientHeight = 400;
  original.resize(); original.settle();
  near(parseFloat(original.image.style.width), 4000);
  near(original.state.zoom * original.state.fit, 1);
  const animated = setup();
  animated.doubleClick(); animated.frame(60);
  const width = parseFloat(animated.image.style.width);
  animated.stage.clientWidth = 500; animated.stage.clientHeight = 400;
  animated.resize(); animated.settle();
  near(parseFloat(animated.image.style.width), width);
  assert.equal(animated.state.gesture, null); assert.equal(animated.state.pointers.size, 0);
});

test('entry and exit release temporary scaled animations while retaining an original source', async () => {
  const h = setup();
  h.state.phase = 'closed'; h.state.context = null;
  Object.assign(h.ui.detailImage, { isConnected: true,
    getBoundingClientRect: () => ({ left: 100, top: 100, right: 340, bottom: 280, width: 240, height: 180 }) });
  h.ui.detailDialog.getBoundingClientRect = () => ({ left: 40, top: 50, right: 900, bottom: 750, width: 860, height: 700 });
  await h.ctx.openImagePreview(null, { kind: 'random', sourceElement: h.ui.detailImage, sourceContainer: h.ui.detailDialog });
  assert.equal(h.state.phase, 'opening');
  assert.equal(h.image.src, '/api/images/1');
  assert.match(h.image.animations.at(-1).frames[0].transform, /scale\(/);
  h.image.animations.at(-1).finish(); await flush();
  assert.equal(h.state.phase, 'open');
  assert.equal(h.image.getAnimations().length, 0);
  assert.doesNotMatch(h.image.style.transform, /scale\(/);
  h.ctx.closeImagePreview();
  assert.equal(h.state.phase, 'closing');
  h.image.animations.at(-1).finish(); await flush();
  assert.equal(h.state.phase, 'closed');
  assert.equal(h.image.getAnimations().length, 0);
  assert.equal(h.image.getAttribute('src'), null);
});

for (const [mode, scale, original, deadline] of [
  ['fit', null, false, 336], ['4 times fit', 4, false, 480], ['400 percent original', 4, true, 544],
]) {
  test(`return from ${mode} releases the modal without an invisible spring tail`, async () => {
    const h = setup({ motion: true });
    Object.assign(h.ui.detailImage, { isConnected: true,
      getBoundingClientRect: () => ({ left: 100, top: 100, right: 340, bottom: 280, width: 240, height: 180 }) });
    h.ui.detailDialog.getBoundingClientRect = () => ({ left: 40, top: 50, right: 900, bottom: 750, width: 860, height: 700 });
    h.state.context = { kind: 'random', sourceElement: h.ui.detailImage, sourceContainer: h.ui.detailDialog };
    h.ctx.document.documentElement.classList.add('image-preview-open');
    if (scale) { h.ctx.setImagePreviewZoom(original ? scale / h.state.fit : scale); h.settle(); }
    const target = h.ctx.numericPreviewFrame(h.ctx.imagePreviewSourceFrame());
    h.ctx.closeImagePreview();
    let elapsed = 0;
    while (h.state.phase !== 'closed' && elapsed < deadline) {
      h.frame(16); elapsed += 16; await flush();
    }
    assert.equal(h.state.phase, 'closed', `return must finish by ${deadline} ms`);
    assert.equal(h.ui.imagePreviewDialog.open, false);
    assert.equal(h.ctx.document.documentElement.classList.contains('image-preview-open'), false);
    assert.equal(h.ui.detailImage.getAttribute('aria-expanded'), 'false');
    const final = h.ctx.numericPreviewFrame(h.ctx.liveImagePreviewFrame());
    for (const key of Object.keys(target)) near(final[key], target[key], `final ${key}`);
    assert.equal(h.image.getAttribute('src'), null);
    assert.equal(h.frames.size, 0);
    assert.equal(h.ui.imagePreviewVeil.getAnimations().length, 0);
  });
}

test('a decode completing after close cannot reveal the hidden original or restart entry', async () => {
  const h = setup();
  let resolve;
  h.image.decode = () => new Promise(done => { resolve = done; });
  h.state.phase = 'closed'; h.state.context = null;
  const opening = h.ctx.openImagePreview(null, { kind: 'random' });
  assert.equal(h.state.phase, 'loading');
  h.ctx.closeImagePreview({ immediate: true });
  resolve(); await opening;
  assert.equal(h.state.phase, 'closed');
  assert.equal(h.image.hidden, true);
  assert.equal(h.image.getAttribute('src'), null);
  assert.equal(h.image.animations.length, 0);
});

for (const mode of [{ motion: false }, { motion: true }, { motion: true, reducedMotion: true }]) {
  test(`cold preview keeps its source until a rendered bitmap is ready (${JSON.stringify(mode)})`, async () => {
    const h = setup(mode);
    let decoded;
    h.image.decode = () => new Promise(resolve => { decoded = resolve; });
    h.state.phase = 'closed'; h.state.context = null;
    Object.assign(h.ui.detailImage, { isConnected: true,
      getBoundingClientRect: () => ({ left: 100, top: 100, right: 340, bottom: 280, width: 240, height: 180 }) });
    h.ui.detailDialog.getBoundingClientRect = () => ({ left: 40, top: 50, right: 900, bottom: 750, width: 860, height: 700 });
    const sourceClasses = h.ui.detailImage.classList;
    const toggle = sourceClasses.toggle.bind(sourceClasses);
    let transferred = false;
    sourceClasses.toggle = (name, hidden) => {
      if (name === 'image-preview-source-hidden' && hidden) {
        assert.equal(h.image.hidden, false, 'the decoded bitmap must already be visible');
        assert.ok(parseFloat(h.image.style.width) > 0, 'layout must precede the handoff');
        assert.ok(mode.motion ? h.ctx.StudioMotion.snapshot(h.image, 'flight') : h.image.animations.length,
          'the initial animation frame must precede the handoff');
        assert.equal(h.ui.imagePreviewVeil.animations.length, 0, 'the veil must wait for the handoff');
        transferred = true;
      }
      return toggle(name, hidden);
    };
    const opening = h.ctx.openImagePreview(null, { kind: 'random', sourceElement: h.ui.detailImage, sourceContainer: h.ui.detailDialog });
    assert.equal(h.state.phase, 'loading');
    assert.equal(sourceClasses.contains('image-preview-source-hidden'), false);
    assert.equal(h.image.hidden, true);
    assert.equal(h.ui.imagePreviewStatus.hidden, false);
    assert.equal(h.ui.imagePreviewVeil.style.opacity, '0');
    assert.equal(h.ui.imagePreviewVeil.animations.length, 0);
    decoded(); await opening;
    assert.equal(transferred, true);
    assert.equal(sourceClasses.contains('image-preview-source-hidden'), true);
    assert.equal(h.ui.imagePreviewStatus.hidden, true);
    assert.equal(h.ui.imagePreviewVeil.animations.length, 1);
    let bitmapHidden = h.image.hidden;
    Object.defineProperty(h.image, 'hidden', { get: () => bitmapHidden, set(value) {
      if (value) assert.equal(sourceClasses.contains('image-preview-source-hidden'), false,
        'closing must restore the source before hiding the overlay bitmap');
      bitmapHidden = value;
    } });
    h.ctx.closeImagePreview({ immediate: true });
    assert.equal(h.state.phase, 'closed');
    assert.equal(sourceClasses.contains('image-preview-source-hidden'), false);
    assert.equal(h.image.getAttribute('src'), null);
  });
}

test('canceling a cold preview returns immediately and ignores its late decode', async () => {
  const h = setup({ motion: true });
  let decoded;
  h.image.decode = () => new Promise(resolve => { decoded = resolve; });
  h.state.phase = 'closed'; h.state.context = null;
  const opening = h.ctx.openImagePreview(null, { kind: 'random', sourceElement: h.ui.detailImage });
  h.ctx.closeImagePreview();
  assert.equal(h.state.phase, 'closed');
  assert.equal(h.ui.imagePreviewDialog.open, false);
  assert.equal(h.ui.detailImage.classList.contains('image-preview-source-hidden'), false);
  assert.equal(h.frames.size, 0);
  decoded(); await opening;
  assert.equal(h.state.phase, 'closed');
  assert.equal(h.image.hidden, true);
  assert.equal(h.ui.imagePreviewVeil.animations.length, 0);
});

test('a failed original decode keeps the detail visible and closes without a veil animation', async () => {
  const h = setup();
  h.image.decode = async () => { throw new Error('decode failed'); };
  h.state.phase = 'closed'; h.state.context = null;
  await h.ctx.openImagePreview(null, { kind: 'random', sourceElement: h.ui.detailImage });
  assert.equal(h.state.phase, 'error');
  assert.equal(h.ui.detailImage.classList.contains('image-preview-source-hidden'), false);
  assert.equal(h.ui.imagePreviewStatus.hidden, false);
  assert.match(h.ui.imagePreviewStatus.textContent, /加载失败/);
  assert.equal(h.ui.imagePreviewVeil.style.opacity, '0');
  assert.equal(h.ui.imagePreviewVeil.animations.length, 0);
  h.ctx.closeImagePreview();
  assert.equal(h.state.phase, 'closed');
});

test('a decode whose source view became invalid releases its loading overlay', async () => {
  const h = setup();
  h.state.phase = 'closed'; h.state.context = null;
  await h.ctx.openImagePreview(null, { kind: 'random', sourceElement: h.ui.detailImage, isValid: () => false });
  assert.equal(h.state.phase, 'closed');
  assert.equal(h.ui.imagePreviewDialog.open, false);
  assert.equal(h.ui.detailImage.classList.contains('image-preview-source-hidden'), false);
});

test('the source flight preserves opacity inherited from an entering detail and its content', () => {
  const h = setup(), content = element();
  Object.assign(h.ui.detailImage, { isConnected: true, parentElement: content,
    getBoundingClientRect: () => ({ left: 100, top: 100, right: 340, bottom: 280, width: 240, height: 180 }) });
  content.parentElement = h.ui.detailDialog;
  h.ui.detailDialog.getBoundingClientRect = () => ({ left: 40, top: 50, right: 900, bottom: 750, width: 860, height: 700 });
  h.state.context = { kind: 'random', sourceElement: h.ui.detailImage, sourceContainer: h.ui.detailDialog };
  h.ui.detailImage.style.opacity = '.8'; content.style.opacity = '.75'; h.ui.detailDialog.style.opacity = '.6';
  near(h.ctx.imagePreviewSourceFrame().opacity, .36, 'lifted pixels keep the exact source alpha');
  h.ui.detailImage.style.opacity = content.style.opacity = h.ui.detailDialog.style.opacity = '1';
  assert.equal(h.ctx.imagePreviewSourceFrame().opacity, 1, 'a completed dialog returns to an opaque source');
});

test('an early return resumes the frozen detail from its visible alpha without a brightness jump', async () => {
  const h = setup({ motion: true });
  h.state.phase = 'closed'; h.state.context = null;
  Object.assign(h.ui.detailImage, { isConnected: true, parentElement: h.ui.detailDialog,
    getBoundingClientRect: () => ({ left: 100, top: 100, right: 340, bottom: 280, width: 240, height: 180 }) });
  h.ui.detailDialog.getBoundingClientRect = () => ({ left: 40, top: 50, right: 900, bottom: 750, width: 860, height: 700 });
  h.ctx.StudioMotion.dialog(h.ui.detailDialog, true); h.frame(64);
  const alpha = Number(h.ui.detailDialog.style.opacity);
  assert.ok(alpha > 0 && alpha < 1);
  await h.ctx.openImagePreview(null, { kind: 'random', sourceElement: h.ui.detailImage, sourceContainer: h.ui.detailDialog });
  near(Number(h.image.style.opacity), alpha, 'entry begins at the visible parent alpha');
  h.frame(32); h.ctx.closeImagePreview();
  h.settle(); await flush();
  assert.equal(h.state.phase, 'closed');
  near(Number(h.ui.detailDialog.style.opacity), alpha, 'the parent resumes from the same alpha on return');
  assert.equal(h.ui.detailImage.classList.contains('image-preview-source-hidden'), false);
  h.settle(); await flush();
  assert.equal(h.ui.detailDialog.style.opacity, '', 'the parent finishes at its normal opaque style');
});

test('reopening an exiting preview retains its bitmap until the latest close finishes', async () => {
  const h = setup({ motion: true });
  h.state.phase = 'closed'; h.state.context = null;
  Object.assign(h.ui.detailImage, { isConnected: true,
    getBoundingClientRect: () => ({ left: 100, top: 100, right: 340, bottom: 280, width: 240, height: 180 }) });
  h.ui.detailDialog.getBoundingClientRect = () => ({ left: 40, top: 50, right: 900, bottom: 750, width: 860, height: 700 });
  await h.ctx.openImagePreview(null, { kind: 'random', sourceElement: h.ui.detailImage, sourceContainer: h.ui.detailDialog });
  h.settle(); await flush();
  h.ctx.closeImagePreview(); h.frame(32);
  const before = h.ctx.StudioMotion.snapshot(h.image, 'flight');
  await h.ctx.openImagePreview();
  const after = h.ctx.StudioMotion.snapshot(h.image, 'flight');
  near(before.value.width, after.value.width); near(before.velocity.width, after.velocity.width);
  assert.equal(h.ui.detailImage.classList.contains('image-preview-source-hidden'), true);
  assert.equal(h.image.hidden, false);
  h.settle(); await flush();
  assert.equal(h.state.phase, 'open');
  assert.equal(h.ui.imagePreviewDialog.open, true);
  h.ctx.closeImagePreview(); h.settle(); await flush();
  assert.equal(h.state.phase, 'closed');
  assert.equal(h.ui.detailImage.classList.contains('image-preview-source-hidden'), false);
  assert.equal(h.image.hidden, true);
});

test('closing cancels queued renders and animation work and releases decoded preloads', async () => {
  const h = setup();
  let released = 0;
  h.state.preloaded.set('1', { release: () => { released++; } });
  h.doubleClick(); h.frame(20); h.frame(30);
  h.ctx.finishImagePreviewClose();
  assert.equal(h.state.phase, 'closed');
  assert.equal(h.frames.size, 0);
  assert.equal(released, 1);
  assert.equal(h.state.preloaded.size, 0);
  assert.equal(h.image.getAttribute('src'), null);
  assert.equal(h.image.hidden, true);
  const writes = h.image.writes.length;
  h.frame(500); await flush();
  assert.equal(h.image.writes.length, writes, 'late animation callbacks cannot redraw a closed viewer');
});

for (const phase of ['opening', 'closing']) {
  test(`pointer input takes over a ${phase} image at its live dimensions without waiting`, async () => {
    const h = setup({ motion: true });
    h.state.phase = phase;
    h.image.style.width = '920px'; h.image.style.height = '690px';
    h.image.style.transform = 'matrix(0.5, 0, 0, 0.5, 90, -40)';
    h.image.style.clipPath = 'inset(10px 20px 30px 40px round 18px)';
    h.image.style.opacity = '.65';
    h.pointer('Down', 1, 520, 430, { pointerType: 'mouse' });
    assert.equal(h.state.phase, 'open');
    near(parseFloat(h.image.style.width), 460); near(parseFloat(h.image.style.height), 345);
    near(h.state.x, 90); near(h.state.y, -40);
    assert.doesNotMatch(h.image.style.transform, /scale\(/);
    assert.equal(h.image.style.clipPath, 'inset(5px 10px 15px 20px round 9px)');
    h.pointer('Move', 1, 521, 430, { pointerType: 'mouse' }); h.frame();
    assert.ok(Math.abs(h.state.x - 90) < 1, 'the first pixel of dragging must not clamp the source geometry to center');
    h.pointer('Up', 1, 521, 430, { pointerType: 'mouse' }); h.settle();
    near(h.state.zoom, 1); near(h.state.x, 0); near(h.state.y, 0);
  });
}

test('spring zoom keeps original pixel dimensions and retargets with continuous velocity', () => {
  const h = setup({ motion: true });
  h.doubleClick(); h.frame(16); h.frame(55);
  const before = h.ctx.StudioMotion.snapshot(h.state, 'view');
  const displayed = parseFloat(h.image.style.width);
  h.ctx.setImagePreviewZoom(2, null, true);
  const after = h.ctx.StudioMotion.snapshot(h.state, 'view');
  near(after.value.zoom, before.value.zoom); near(after.velocity.zoom, before.velocity.zoom);
  near(parseFloat(h.image.style.width), displayed);
  h.settle(); near(parseFloat(h.image.style.width), 1840);
  assert.doesNotMatch(h.image.style.transform, /scale\(/);
});

test('drag bounds resist overscroll then settle and a new drag cancels the old spring', () => {
  const h = setup({ motion: true });
  h.ctx.setImagePreviewZoom(2); h.settle();
  h.pointer('Down', 1, 520, 430, { pointerType: 'mouse' });
  h.pointer('Move', 1, 1520, 430, { pointerType: 'mouse' }); h.frame();
  const bound = (h.state.width * h.state.zoom - h.stage.clientWidth) / 2;
  assert.ok(h.state.x > bound && h.state.x <= bound + 36);
  const visible = h.state.x;
  h.pointer('Up', 1, 1520, 430, { pointerType: 'mouse' });
  near(h.state.x, visible);
  h.frame(16); h.frame(30);
  const interrupted = h.state.x;
  h.pointer('Down', 2, 520, 430, { pointerType: 'mouse' });
  near(h.state.x, interrupted);
  assert.equal(h.ctx.StudioMotion.snapshot(h.state, 'view').active, false);
  h.pointer('Up', 2, 520, 430, { pointerType: 'mouse' }); h.settle();
  near(h.state.x, bound);
});

test('shared-image spring reverses in flight and old exit cannot close the reopened viewer', async () => {
  const h = setup({ motion: true });
  let closes = 0;
  h.state.phase = 'closing';
  const from = h.ctx.imagePreviewFrame();
  h.ctx.animateImagePreview(from, { ...from, transform: 'translate(80px, -40px) scale(.4)', opacity: .2 }, 300, () => closes++);
  h.frame(16); h.frame(60);
  const before = h.ctx.StudioMotion.snapshot(h.image, 'flight');
  h.state.phase = 'opening';
  h.ctx.animateImagePreview(h.ctx.liveImagePreviewFrame(), from, 300, () => { h.state.phase = 'open'; });
  const after = h.ctx.StudioMotion.snapshot(h.image, 'flight');
  near(before.value.width, after.value.width); near(before.velocity.width, after.velocity.width);
  h.settle(); await flush();
  assert.equal(closes, 0); assert.equal(h.state.phase, 'open');
  near(parseFloat(h.image.style.width), 920);
  assert.doesNotMatch(h.image.style.transform, /scale\(/);
});

for (const earlyClose of [false, true]) {
  test(`fullscreen freezes its entering source and ${earlyClose ? 'resumes it after an early return' : 'settles it only behind the completed veil'}`, async () => {
    const h = setup(), calls = [];
    h.state.phase = 'closed'; h.state.context = null;
    h.ui.detailDialog.style.transform = 'translate3d(0px, 7px, 0px) scale(.99)';
    h.ui.detailDialog.style.opacity = '.6';
    h.ctx.StudioMotion = {
      snapshot: () => ({ active: true }), target: () => true,
      freeze: (element, channel) => calls.push(['freeze', element, channel]),
      dialog: (element, open, options) => { calls.push(['resume', element, open, options]); element.style.opacity = '1'; element.style.transform = ''; },
    };
    await h.ctx.openImagePreview(null, { kind: 'random' });
    assert.equal(calls.length, 1); assert.equal(calls[0][0], 'freeze');
    assert.equal(h.ui.detailDialog.style.opacity, '.6');
    assert.equal(h.ui.detailDialog.style.transform, 'translate3d(0px, 7px, 0px) scale(.99)');
    if (earlyClose) {
      h.ctx.closeImagePreview();
      assert.equal(calls.length, 1, 'return flight still targets the frozen source');
      h.image.animations.at(-1).finish(); await flush();
      assert.equal(h.state.phase, 'closed');
    } else {
      h.image.animations.at(-1).finish(); await flush();
      assert.equal(h.state.phase, 'open'); assert.equal(calls.length, 1, 'a finished image alone cannot move a partly exposed parent');
      h.ui.imagePreviewVeil.animations.at(-1).finish(); await flush();
    }
    assert.equal(calls.length, 2); assert.equal(calls[1][0], 'resume');
    assert.equal(calls[1][3].immediate, !earlyClose);
    assert.equal(h.state.sourcePresence, null);
    assert.equal(h.ui.detailDialog.style.opacity, '1');
  });
}
