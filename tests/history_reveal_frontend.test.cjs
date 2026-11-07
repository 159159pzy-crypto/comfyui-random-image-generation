const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function functionSource(name) {
  let start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  if (source.slice(start - 6, start) === 'async ') start -= 6;
  return source.slice(start, source.indexOf('\n}', start) + 2);
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { resolve, reject, promise };
}
function setup() {
  const calls = [], notices = [], attributes = new Map(), pending = [];
  const button = { disabled: false, setAttribute: (key, value) => attributes.set(key, value), removeAttribute: key => attributes.delete(key) };
  const ctx = vm.createContext({ selectedRecord: { id: 7 }, revealFileRequest: 0, ui: { revealImageFile: button },
    toast: message => notices.push(message), request: (url, options) => { calls.push({ url, options }); const next = deferred(); pending.push(next); return next.promise; } });
  for (const name of ['resetRevealImageFile', 'revealSelectedImageFile']) vm.runInContext(functionSource(name), ctx);
  return { ctx, calls, notices, button, pending, attributes };
}

test('both workbenches submit only the record id, prevent duplicate requests and restore button state', async () => {
  for (const workbench of ['single', 'random']) {
    const h = setup(); h.ctx.selectedRecord.settings = { workbench };
    const opening = h.ctx.revealSelectedImageFile();
    await h.ctx.revealSelectedImageFile();
    assert.equal(h.calls.length, 1);
    assert.equal(h.calls[0].url, '/api/history/7/reveal');
    assert.equal(h.calls[0].options.method, 'POST');
    assert.deepEqual(JSON.parse(h.calls[0].options.body), {});
    assert.equal(h.button.disabled, true);
    assert.equal(h.attributes.get('aria-busy'), 'true');
    h.pending[0].resolve({ opened: true }); await opening;
    assert.deepEqual(h.notices, ['已在文件夹中选中原图']);
    assert.equal(h.button.disabled, false);
    assert.equal(h.button.textContent, '打开所在文件夹');
    assert.equal(h.attributes.has('aria-busy'), false);
  }
});

test('a late response after switching image neither toasts nor unlocks a newer pending request', async () => {
  const h = setup(); const old = h.ctx.revealSelectedImageFile();
  h.ctx.selectedRecord = { id: 8 };
  h.ctx.resetRevealImageFile();
  const current = h.ctx.revealSelectedImageFile();
  h.pending[0].reject(new Error('旧图片不存在')); await old;
  assert.equal(h.button.disabled, true);
  assert.deepEqual(h.notices, []);
  h.pending[1].resolve({ opened: true }); await current;
  assert.equal(h.button.disabled, false);
  assert.deepEqual(h.notices, ['已在文件夹中选中原图']);
});

test('closing the detail invalidates in-flight success and errors remain readable and retryable', async () => {
  const h = setup(); const closing = h.ctx.revealSelectedImageFile();
  h.ctx.resetRevealImageFile(); h.pending[0].resolve({ opened: true }); await closing;
  assert.deepEqual(h.notices, []);
  for (const reason of ['原图文件不存在', '无法确认本机 ComfyUI 目录', '不支持远程访问']) {
    const opening = h.ctx.revealSelectedImageFile();
    h.pending.at(-1).reject(new Error(reason)); await opening;
    assert.equal(h.notices.at(-1), reason);
    assert.equal(h.button.disabled, false);
  }
  const incomplete = h.ctx.revealSelectedImageFile();
  h.pending.at(-1).resolve({ opened: false }); await incomplete;
  assert.match(h.notices.at(-1), /未能打开/);
});

test('preview transition matches contain sizing for single details and cover for random thumbnails', () => {
  const element = { isConnected: true, getBoundingClientRect: () => ({ left: 0, top: 0, right: 600, bottom: 600, width: 600, height: 600 }) };
  const ctx = vm.createContext({ imagePreview: { context: null, width: 600, height: 300 },
    ui: { detailImage: element, detailDialog: element, imagePreviewStage: element }, getComputedStyle: () => ({ objectFit: 'contain' }) });
  vm.runInContext(functionSource('imagePreviewSourceFrame'), ctx);
  assert.match(ctx.imagePreviewSourceFrame().transform, /scale\(1\)/);
  assert.equal(ctx.imagePreviewSourceFrame().clipPath, 'inset(0px 1px 0px 1px round 18px)');
  ctx.getComputedStyle = () => ({ objectFit: 'cover' });
  assert.match(ctx.imagePreviewSourceFrame().transform, /scale\(2\)/);
});

test('single drawers close immediately, restore details and ignore outdated resource-check focus', async () => {
  const classes = new Set(['is-single-detail']);
  const drawer = () => ({ hidden: true, classList: { contains: () => false, remove() {} }, setAttribute() {} });
  const replay = drawer(), variant = drawer(), trigger = { focus() {}, setAttribute() {} }, pending = deferred();
  let focused = false;
  const ctx = vm.createContext({ selectedRecord: { id: 7 }, regenerationTrigger: null,
    ui: { replayDrawer: replay, variantDrawer: variant, detailScroll: { scrollTop: 80 }, reproduceImage: trigger, variantButton: trigger,
      detailDialog: { classList: { contains: name => classes.has(name), remove: name => classes.delete(name), add: name => classes.add(name) } },
      confirmReplay: { focus() { focused = true; } }, variantModes: { querySelector: () => null } }, loadRegenerationOptions: () => pending.promise,
    document: { contains: () => true } });
  for (const name of ['setDetailActionPanelVisible', 'collapseRegenerationDrawer', 'closeRegenerationDrawers', 'openRegenerationDrawer']) vm.runInContext(functionSource(name), ctx);
  const opening = ctx.openRegenerationDrawer('replay', trigger);
  assert.equal(classes.has('is-regenerating'), false, 'inline generation options do not hide historical details');
  assert.equal(ctx.ui.detailScroll.scrollTop, 80, 'opening actions preserves the detail reading position');
  assert.equal(replay.hidden, false);
  ctx.closeRegenerationDrawers();
  assert.equal(replay.hidden, true);
  assert.equal(classes.has('is-regenerating'), false);
  pending.resolve(null); await opening;
  assert.equal(focused, false);
});

test('an older resource response cannot steal focus after closing and reopening the same record', async () => {
  const pending = [deferred(), deferred()]; let request = 0, focuses = 0;
  const drawer = () => ({ hidden: true, classList: { contains: () => false, remove() {} } });
  const replay = drawer(), variant = drawer(), trigger = { focus() {}, setAttribute() {} };
  const ctx = vm.createContext({ selectedRecord: { id: 7 }, regenerationTrigger: null,
    ui: { replayDrawer: replay, variantDrawer: variant, reproduceImage: trigger, variantButton: trigger,
      detailDialog: { classList: { remove() {} } }, confirmReplay: { focus() { focuses++; } } },
    loadRegenerationOptions: () => pending[request++].promise, document: { contains: () => true, activeElement: trigger } });
  for (const name of ['setDetailActionPanelVisible', 'collapseRegenerationDrawer', 'closeRegenerationDrawers', 'openRegenerationDrawer']) vm.runInContext(functionSource(name), ctx);
  const old = ctx.openRegenerationDrawer('replay', trigger);
  ctx.closeRegenerationDrawers(false, true);
  const latest = ctx.openRegenerationDrawer('replay', trigger);
  pending[0].resolve({}); await old; assert.equal(focuses, 0);
  ctx.document.activeElement = { id: 'another-action' };
  pending[1].resolve({}); await latest; assert.equal(focuses, 0, 'resource checks must not override a newer user focus choice');
});
