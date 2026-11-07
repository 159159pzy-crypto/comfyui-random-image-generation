const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function load(ctx, ...names) {
  for (const name of names) {
    let start = source.indexOf(`function ${name}(`);
    assert.ok(start >= 0, name);
    if (source.slice(start - 6, start) === 'async ') start -= 6;
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), ctx);
  }
}
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
const flush = () => new Promise(setImmediate);
const response = payload => ({ ok: true, json: async () => payload });
const batch = (id = 'krea-batch', value = 1) => ({
  id, status: 'running', settings: { workbench: 'random', model_profile: 'krea2' },
  progress: { value, max: 4 },
});

function context() {
  let now = 100000, timerId = 0;
  const timers = new Map(), intervals = [], listeners = {}, requests = [], rendered = [], messages = [];
  const ctx = vm.createContext({
    AbortController, Date: { now: () => now },
    currentBatch: batch(), pollBatchPending: false, pollBatchLast: 0, pollIdleTicks: 0, pollErrorShown: false,
    document: { hidden: false, addEventListener: (name, handler) => { listeners[name] = handler; } },
    setTimeout: (callback, delay) => { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
    clearTimeout: id => timers.delete(id),
    setInterval: (callback, delay) => { intervals.push({ callback, delay }); return intervals.length; },
    singleWorkbenchActive: () => false,
    fetch: async (url, options) => { requests.push({ url, options }); return ctx.reply(url, options); },
    reply: () => response({ batch: batch() }),
    renderBatchPayload: payload => { rendered.push(payload.batch); ctx.currentBatch = payload.batch; },
    toast: message => messages.push(message),
  });
  load(ctx, 'request', 'pollBatch');
  return {
    ctx, timers, intervals, listeners, requests, rendered, messages,
    advance: milliseconds => { now += milliseconds; },
    expire: () => { assert.equal(timers.size, 1); [...timers.values()][0].callback(); },
  };
}

for (const phase of ['fetch', 'body']) {
  test(`a hung progress ${phase} is aborted and the next poll can recover`, async () => {
    const { ctx, timers, requests, rendered, messages, advance, expire } = context();
    ctx.reply = (_url, { signal }) => {
      const pending = new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })), { once: true });
      });
      return phase === 'fetch' ? pending : { ok: true, json: () => pending };
    };
    const pending = ctx.pollBatch();
    await flush();
    assert.equal(ctx.pollBatchPending, true);
    advance(10000);
    await ctx.pollBatch();
    assert.equal(requests.length, 1, 'a pending read must not overlap another read');
    assert.equal([...timers.values()][0].delay, 10000);
    expire();
    await pending;
    assert.equal(requests[0].options.signal.aborted, true);
    assert.equal(requests[0].options.cache, 'no-store');
    assert.equal(ctx.pollBatchPending, false);
    assert.equal(timers.size, 0);
    assert.equal(rendered.length, 0);
    assert.deepEqual(messages, ['读取生成进度超时，正在重试']);

    ctx.reply = () => response({ batch: batch('krea-batch', 3) });
    await ctx.pollBatch();
    assert.equal(requests.length, 2);
    assert.equal(ctx.currentBatch.progress.value, 3);
    assert.equal(ctx.pollErrorShown, false);
    assert.equal(timers.size, 0, 'a successful read must cancel its timeout');
  });
}

test('a delayed status read cannot overwrite a newer submit or stop response', async () => {
  const { ctx, timers, rendered, advance } = context();
  const oldRead = deferred();
  ctx.reply = () => oldRead.promise;
  const pending = ctx.pollBatch();
  const submitted = batch('newly-submitted', 0);
  ctx.currentBatch = submitted;
  oldRead.resolve(response({ batch: batch('previous-batch', 4) }));
  await pending;
  assert.equal(ctx.currentBatch, submitted);
  assert.equal(rendered.length, 0);
  assert.equal(ctx.pollBatchPending, false);
  assert.equal(timers.size, 0);

  ctx.reply = () => response({ batch: batch('newly-submitted', 2) });
  advance(1200);
  await ctx.pollBatch();
  assert.equal(ctx.currentBatch.id, 'newly-submitted');
  assert.equal(ctx.currentBatch.progress.value, 2);
});

test('slow startup resources do not delay polling or visibility recovery', async () => {
  const { ctx, intervals, listeners, requests, advance } = context();
  const resources = deferred();
  let finished = false, connectionChecks = 0, progress = 0;
  Object.assign(ctx, {
    initialTheme: () => 'dark', applyTheme() {}, renderStorageStatus() {}, initializeLoraManager() {}, initializeRandomWorkbench() {}, initializePoolLifecycle() {},
    loadStoredView() {}, loadStoredDraft: () => true,
    ui: { appError: {}, draftStatus: {} }, initialized: false,
    loadResources: () => resources.promise,
    loadKreaPromptPool: async () => {}, loadHistory: async () => {},
    loadLoraInventory: async () => {}, loadArtistFavorites: async () => {}, loadStylePresets: async () => {},
    refreshConnection: async () => { connectionChecks++; },
    reply: url => response(url === '/api/config' ? { defaults: {}, warnings: [] } : { batch: batch('krea-batch', ++progress) }),
  });
  load(ctx, 'initialize');
  const initialization = ctx.initialize().then(() => { finished = true; });
  await flush();
  assert.equal(finished, false, 'resource loading is still pending');
  assert.equal(ctx.initialized, true);
  assert.deepEqual(intervals.map(timer => timer.delay), [400, 5000]);
  assert.equal(typeof listeners.visibilitychange, 'function');
  assert.equal(ctx.currentBatch.progress.value, 1);

  const polling = intervals.find(timer => timer.delay === 400);
  advance(1200);
  await polling.callback();
  assert.equal(ctx.currentBatch.progress.value, 2);
  ctx.document.hidden = true;
  advance(1200);
  const before = requests.length;
  await polling.callback();
  listeners.visibilitychange();
  await flush();
  assert.equal(requests.length, before, 'hidden pages do not start progress reads');
  ctx.document.hidden = false;
  listeners.visibilitychange();
  await flush();
  assert.equal(ctx.currentBatch.progress.value, 3);
  assert.equal(connectionChecks, 2);
  assert.equal(finished, false, 'monitoring does not wait for the resource inventory');
  resources.resolve();
  await initialization;
});
