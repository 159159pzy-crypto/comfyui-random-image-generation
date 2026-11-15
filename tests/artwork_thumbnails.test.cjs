const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/artwork-thumbnails.js'), 'utf8');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function ticks() { for (let i = 0; i < 16; i++) await Promise.resolve(); }
function response(status = 200, { body = { type: 'image/webp', size: 123 }, headers = {} } = {}) {
  return { ok: status >= 200 && status < 300, status,
    headers: { get: name => Object.entries(headers).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1] ?? null },
    blob: () => Promise.resolve(body), json: async () => ({ error: `HTTP ${status}` }), text: async () => `HTTP ${status}` };
}

function harness({ observer = true, random = 0 } = {}) {
  const frames = new Map(), timers = new Map(), observers = [], requests = [], fetches = [], revoked = [], created = [];
  const windowEvents = new Map(), documentEvents = new Map();
  const document = { hidden: false, activeElement: null, addEventListener: (name, callback) => documentEvents.set(name, callback) };
  const navigator = { onLine: true };
  let order = 0, nextId = 0, now = 1_700_000_000_000;
  const initialTime = now;
  class FakeDate extends Date { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } }
  const fakeMath = Object.create(Math); fakeMath.random = () => random;
  function node() {
    const classes = new Set(), events = new Map(), attributes = new Map();
    return {
      dataset: {}, hidden: false, disabled: false, textContent: '',
      classList: { toggle(name, on) { if (on) classes.add(name); else classes.delete(name); },
        contains: name => classes.has(name), add: (...names) => names.forEach(name => classes.add(name)), remove: (...names) => names.forEach(name => classes.delete(name)) },
      setAttribute(name, value) { attributes.set(name, value); }, getAttribute: name => attributes.get(name) ?? null,
      removeAttribute: name => attributes.delete(name),
      addEventListener(name, callback) { if (!events.has(name)) events.set(name, new Set()); events.get(name).add(callback); },
      removeEventListener(name, callback) { events.get(name)?.delete(callback); },
      emit(name) { for (const callback of [...(events.get(name) || [])]) callback({ target: this, stopPropagation() {}, preventDefault() {} }); },
      focus() { document.activeElement = this; },
    };
  }
  const ctx = vm.createContext({ document, navigator, Date: FakeDate, Math: fakeMath, AbortController,
    performance: { now: () => now - initialTime },
    window: { innerHeight: 800, innerWidth: 1200, addEventListener: (name, callback) => windowEvents.set(name, callback) },
    requestAnimationFrame: callback => { const id = ++nextId; frames.set(id, callback); return id; },
    cancelAnimationFrame: id => frames.delete(id),
    setTimeout: (callback, delay = 0) => { const id = ++nextId; timers.set(id, { at: now + Math.max(0, delay), callback }); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: (url, options) => { const gate = deferred(); fetches.push({ url, options, at: now, id: Number(url.match(/images\/(\d+)/)[1]), ...gate }); return gate.promise; },
    URL: { createObjectURL(body) { const value = `blob:test-${created.length + 1}`; created.push({ body, value }); return value; }, revokeObjectURL: url => revoked.push(url) },
    ...(observer ? { IntersectionObserver: class {
      constructor(callback, options) { this.callback = callback; this.options = options; this.targets = new Set(); observers.push(this); }
      observe(article) { this.targets.add(article); }
      unobserve(article) { this.targets.delete(article); }
    } } : {}),
  });
  vm.runInContext(source, ctx);
  const loader = vm.runInContext('ArtworkThumbnails', ctx);
  function add(id, top = 100) {
    const article = node(), image = node(), retry = node(), failure = node(), main = node();
    article.order = ++order;
    article.isConnected = true;
    article.rect = { top, bottom: top + 200, left: 0, right: 200 };
    article.closest = () => article.ancestorHidden ? {} : null;
    article.getClientRects = () => article.isConnected && !article.ancestorHidden ? [article.rect] : [];
    article.getBoundingClientRect = () => article.rect;
    article.compareDocumentPosition = other => article.order < other.order ? 4 : 2;
    article.querySelector = selector => selector === '.image-failure' ? failure : selector === '.image-card-open' ? main : null;
    image.dataset.thumbnailUrl = `/api/images/${id}/thumbnail`;
    image.complete = false; image.naturalWidth = 0; image.naturalHeight = 0;
    image.decode = () => Promise.resolve();
    image.getAttribute = name => name === 'src' ? image.currentSource || null : null;
    image.removeAttribute = name => { if (name === 'src') { image.currentSource = ''; image.currentSrc = ''; } };
    Object.defineProperty(image, 'src', { get: () => image.currentSource || '', set(value) {
      image.currentSource = image.currentSrc = value;
      image.complete = !!image.transientComplete; image.naturalWidth = image.naturalHeight = 0;
      requests.push({ id, url: value, priority: image.fetchPriority, loading: image.loading });
    } });
    image.succeed = () => { image.complete = true; image.naturalWidth = 200; image.naturalHeight = 300; image.emit('load'); };
    image.fail = () => { image.complete = true; image.naturalWidth = image.naturalHeight = 0; image.emit('error'); };
    loader.attach(article, image, retry);
    return { article, image, retry, failure, main, id };
  }
  function see(item, visible = true, nearby = true) {
    observers[0]?.callback([{ target: item.article, isIntersecting: visible }]);
    observers[1]?.callback([{ target: item.article, isIntersecting: nearby }]);
  }
  function visible(count, firstId = 1) { return Array.from({ length: count }, (_, index) => { const item = add(firstId + index); see(item); return item; }); }
  function remove(item) { loader.remove(item.article); item.article.isConnected = false; }
  function frame() { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(now)); }
  async function flush() { await ticks(); frame(); await ticks(); }
  async function advance(ms) {
    const end = now + ms;
    let turns = 0;
    while (true) {
      const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      assert.ok(++turns < 1000, 'timers must not spin while a retry is waiting or paused');
      now = next[1].at; timers.delete(next[0]); next[1].callback(); await flush();
    }
    now = end; await flush();
  }
  async function body(item, request = fetches.findLast(fetch => fetch.id === item.id)) { request.resolve(response()); await ticks(); }
  async function loaded(item, request) { await body(item, request); item.image.succeed(); await flush(); }
  async function fail(status = 503, request = fetches.at(-1), options) { request.resolve(response(status, options)); await flush(); }
  return { loader, add, see, visible, remove, frame, flush, advance, body, loaded, fail,
    observers, requests, fetches, created, revoked, timers, document, navigator, windowEvents, documentEvents, get now() { return now; } };
}

test('visible thumbnails lead 400px prefetch, twelve slots saturate and the thirteenth waits', async () => {
  const h = harness(), near = h.add(1, 900), clipped = h.add(2), far = h.add(3, 1400);
  h.see(near, false, true); h.see(clipped, false, false); h.see(far, false, false);
  const visible = h.visible(13, 4);
  assert.deepEqual(h.observers.map(item => item.options.rootMargin), ['0px', '400px 0px']);
  h.frame();
  assert.deepEqual(h.fetches.map(item => item.id), Array.from({ length: 12 }, (_, index) => index + 4));
  assert.ok(h.fetches.every(item => item.options.cache === 'default' && item.options.priority === 'high'));
  await h.loaded(visible[0]); assert.equal(h.fetches.at(-1).id, 16);
  await h.loaded(visible[1]); assert.equal(h.fetches.at(-1).id, 1);
  assert.equal(h.fetches.at(-1).options.priority, 'low');
  assert.equal(clipped.image.src, ''); assert.equal(far.image.src, '');
});

test('rapid pages retain old active slots, drop queued pages and do not abort detached requests', async () => {
  const h = harness(), old = h.visible(15); h.frame(); old.forEach(h.remove);
  const middle = h.visible(15, 20); h.frame(); middle.forEach(h.remove);
  const latest = h.visible(13, 40); h.frame();
  assert.equal(h.fetches.length, 12);
  assert.ok(h.fetches.every(fetch => !fetch.options.signal.aborted));
  h.fetches[0].resolve(response()); h.fetches[1].reject(new Error('network')); await h.flush();
  assert.deepEqual(h.fetches.slice(12).map(item => item.id), [40, 41]);
  assert.equal(h.created.length, 0, 'detached successful responses do not create unused blob URLs');
  assert.ok(h.observers.every(observer => observer.targets.size === 13));
  assert.ok(latest.every(item => item.article.isConnected));
});

test('hidden workbench, hidden document and offline browser pause new work without cancelling active work', async () => {
  const h = harness(), items = h.visible(13);
  h.loader.setActive(false); h.frame(); assert.equal(h.fetches.length, 0);
  h.loader.setActive(true); h.frame(); assert.equal(h.fetches.length, 12);
  h.loader.setActive(false); await h.loaded(items[0]); assert.equal(h.fetches.length, 12);
  h.document.hidden = true; h.loader.setActive(true); h.frame(); assert.equal(h.fetches.length, 12);
  h.document.hidden = false; h.navigator.onLine = false; h.documentEvents.get('visibilitychange')(); h.frame();
  assert.equal(h.fetches.length, 12);
  h.navigator.onLine = true; h.windowEvents.get('online')(); h.frame(); assert.equal(h.fetches.length, 13);
});

test('one slot covers body loading and decode, and loaded blobs survive refresh until removal', async () => {
  const h = harness(), items = h.visible(13); h.frame();
  const body = deferred(), decoding = deferred(), item = items[0]; item.image.decode = () => decoding.promise;
  h.fetches[0].resolve(response(200, { body: body.promise })); await h.flush();
  assert.equal(h.fetches.length, 12); assert.equal(item.image.src, '');
  body.resolve({ type: 'image/webp', size: 100 }); await h.flush(); item.image.succeed(); await h.flush();
  assert.equal(h.fetches.length, 12, 'decode still holds one of twelve slots');
  decoding.resolve(); await h.flush();
  assert.equal(item.article.dataset.thumbnailState, 'loaded'); assert.equal(h.fetches.length, 13);
  const source = item.image.src;
  h.loader.refresh(); h.frame(); assert.equal(item.image.src, source); assert.equal(h.fetches.length, 13);
  h.remove(item); assert.deepEqual(h.revoked, [source]);
});

test('a temporary complete=true with zero dimensions is not mistaken for a load error', async () => {
  const h = harness(), [item] = h.visible(1); item.image.transientComplete = true; h.frame();
  await h.body(item); await h.flush();
  assert.equal(item.article.dataset.thumbnailState, 'loading'); assert.ok(item.image.src);
  assert.equal(item.failure.hidden, true); assert.equal(h.revoked.length, 0);
  item.image.succeed(); await h.flush(); assert.equal(item.article.dataset.thumbnailState, 'loaded');
});

test('a transient failure retries after 300ms using the same URL and a refreshed cache', async () => {
  const h = harness(), [item] = h.visible(1); h.frame(); await h.fail();
  assert.equal(item.article.dataset.thumbnailState, 'retry-wait');
  assert.equal(item.retry.hidden, true); assert.match(item.failure.textContent, /1\s*\/\s*3/);
  await h.advance(299); assert.equal(h.fetches.length, 1);
  await h.advance(1); assert.equal(h.fetches.length, 2);
  assert.equal(h.fetches[1].url, h.fetches[0].url); assert.equal(h.fetches[1].options.cache, 'reload');
  await h.loaded(item); assert.equal(item.article.dataset.thumbnailState, 'loaded');
  assert.equal(item.failure.hidden, true); assert.equal(item.retry.hidden, true);
});

test('network rejection and empty responses recover without ever requesting original images', async t => {
  for (const failure of ['network', 'empty']) await t.test(failure, async () => {
    const h = harness(), [item] = h.visible(1); h.frame();
    if (failure === 'network') h.fetches[0].reject(new TypeError('Failed to fetch'));
    else h.fetches[0].resolve(response(200, { body: { type: 'image/webp', size: 0 } }));
    await h.flush(); assert.equal(item.article.dataset.thumbnailState, 'retry-wait');
    assert.equal(h.created.length, 0); await h.advance(300); await h.loaded(item);
    assert.equal(item.article.dataset.thumbnailState, 'loaded');
    assert.ok(h.fetches.every(fetch => fetch.url.endsWith('/thumbnail')));
  });
});

test('scrolling away during loading keeps the task alive and returning reuses the decoded bitmap', async () => {
  const h = harness(), [item] = h.visible(1); h.frame();
  item.article.rect.top = 1600; item.article.rect.bottom = 1800; h.see(item, false, false); h.frame();
  assert.equal(h.fetches[0].options.signal.aborted, false);
  await h.loaded(item); const source = item.image.src;
  assert.equal(item.article.dataset.thumbnailState, 'loaded');
  item.article.rect.top = 100; item.article.rect.bottom = 300; h.see(item); h.frame();
  assert.equal(h.fetches.length, 1); assert.equal(item.image.src, source);
});

test('three extra attempts use 300/800/1500ms, then manual retry starts one focused fresh round', async () => {
  const h = harness(), [item] = h.visible(1); h.frame();
  for (const delay of [300, 800, 1500]) {
    const count = h.fetches.length; await h.fail(); await h.advance(delay - 1);
    assert.equal(h.fetches.length, count); await h.advance(1); assert.equal(h.fetches.length, count + 1);
  }
  await h.fail(); assert.equal(h.fetches.length, 4);
  assert.equal(item.article.dataset.thumbnailState, 'error'); assert.equal(item.retry.hidden, false);
  await h.advance(10_000); assert.equal(h.fetches.length, 4);
  h.document.activeElement = item.retry; item.retry.emit('click'); item.retry.emit('click'); h.frame();
  assert.equal(h.document.activeElement, item.main); assert.equal(h.fetches.length, 5);
  assert.equal(h.fetches.at(-1).options.cache, 'reload');
  await h.fail(); await h.advance(300); assert.equal(h.fetches.length, 6, 'manual retry restores three automatic attempts');
  await h.loaded(item); assert.equal(item.article.dataset.thumbnailState, 'loaded');
});

test('retry jitter adds up to twenty percent instead of making all failures fire together', async () => {
  const h = harness({ random: 0.5 }); h.visible(1); h.frame(); await h.fail();
  await h.advance(329); assert.equal(h.fetches.length, 1);
  await h.advance(1); assert.equal(h.fetches.length, 2);
});

test('permanent HTTP failures stop immediately, while transient statuses remain automatically retryable', async t => {
  for (const status of [400, 401, 403, 404, 410]) await t.test(String(status), async () => {
    const h = harness(), [item] = h.visible(1); h.frame(); await h.fail(status); await h.advance(5000);
    assert.equal(item.article.dataset.thumbnailState, 'error'); assert.equal(item.retry.hidden, false);
    assert.equal(h.fetches.length, 1); assert.equal(h.created.length, 0);
  });
  for (const status of [408, 429, 500, 503]) await t.test(String(status), async () => {
    const h = harness(), [item] = h.visible(1); h.frame(); await h.fail(status); await h.advance(300);
    assert.equal(h.fetches.length, 2); assert.equal(item.article.dataset.thumbnailState, 'loading');
    assert.ok(h.fetches.every(fetch => fetch.url.endsWith('/thumbnail')));
  });
});

test('Retry-After seconds and dates are minimum delays, and invalid headers use normal backoff', async t => {
  for (const format of ['seconds', 'date', 'invalid']) await t.test(format, async () => {
    const h = harness(); h.visible(1); h.frame();
    const value = format === 'seconds' ? '2' : format === 'date' ? new Date(h.now + 2000).toUTCString() : 'invalid';
    const delay = format === 'invalid' ? 300 : 2000;
    await h.fail(429, h.fetches[0], { headers: { 'Retry-After': value } });
    await h.advance(delay - 1); assert.equal(h.fetches.length, 1);
    await h.advance(1); assert.equal(h.fetches.length, 2);
  });
});

test('offscreen, hidden and offline retries resume remaining budget without observer-resetting the round', async t => {
  for (const pause of ['offscreen', 'ancestor', 'workbench', 'document', 'offline']) await t.test(pause, async () => {
    const h = harness(), [item] = h.visible(1); h.frame(); await h.fail();
    if (pause === 'offscreen') { item.article.rect.top = 900; item.article.rect.bottom = 1100; h.see(item, false, true); }
    if (pause === 'ancestor') { item.article.ancestorHidden = true; h.loader.refresh(); }
    if (pause === 'workbench') h.loader.setActive(false);
    if (pause === 'document') { h.document.hidden = true; h.documentEvents.get('visibilitychange')(); }
    if (pause === 'offline') { h.navigator.onLine = false; h.windowEvents.get('offline')(); }
    h.frame(); await h.advance(5000); assert.equal(h.fetches.length, 1);
    item.article.rect.top = 100; item.article.rect.bottom = 300; item.article.ancestorHidden = false;
    h.document.hidden = false; h.navigator.onLine = true; h.loader.setActive(true); h.see(item); h.see(item); h.frame();
    assert.equal(h.fetches.length, 2);
    await h.fail(); await h.advance(799); assert.equal(h.fetches.length, 2);
    await h.advance(1); assert.equal(h.fetches.length, 3, 'paused time did not consume or reset attempts');
  });
});

test('waiting retries release slots and waiting for a slot does not spend their next attempt', async () => {
  const h = harness(), [item] = h.visible(1); h.frame(); await h.fail();
  const blockers = h.visible(12, 20); h.frame(); assert.equal(h.fetches.length, 13);
  await h.advance(300); assert.equal(h.fetches.length, 13);
  h.loader.refresh(); h.see(item); h.frame(); assert.equal(h.fetches.length, 13);
  await h.loaded(blockers[0]); assert.equal(h.fetches.length, 14); assert.equal(h.fetches.at(-1).id, item.id);
  await h.fail(); await h.advance(799); assert.equal(h.fetches.length, 14);
  await h.advance(1); assert.equal(h.fetches.length, 15, 'the retry that finally started was only attempt one');
});

test('removing a waiting card clears its retry; detached body reads hold their slot until completion', async () => {
  const h = harness(), [waiting] = h.visible(1); h.frame(); await h.fail(); h.remove(waiting);
  await h.advance(5000); assert.equal(h.fetches.length, 1);
  const items = h.visible(13, 20); h.frame(); const request = h.fetches.find(fetch => fetch.id === 20), body = deferred();
  request.resolve(response(200, { body: body.promise })); await h.flush(); h.remove(items[0]); h.frame();
  assert.equal(h.fetches.length, 13); assert.equal(request.options.signal.aborted, false);
  body.resolve({ type: 'image/webp', size: 123 }); await h.flush();
  assert.equal(h.fetches.length, 14); assert.equal(h.fetches.at(-1).id, 32); assert.equal(h.created.length, 0);
});

test('real image errors and rejected decoding recover automatically and revoke failed blobs', async t => {
  for (const failure of ['image', 'decode']) await t.test(failure, async () => {
    const h = harness(), [item] = h.visible(1); h.frame(); await h.body(item);
    const first = item.image.src;
    if (failure === 'image') item.image.fail();
    else { item.image.decode = () => Promise.reject(new Error('corrupt bitmap')); item.image.succeed(); }
    await h.flush(); assert.equal(item.article.dataset.thumbnailState, 'retry-wait');
    assert.deepEqual(h.revoked, [first]); await h.advance(300); item.image.decode = () => Promise.resolve();
    await h.loaded(item); assert.equal(item.article.dataset.thumbnailState, 'loaded');
  });
});

test('late decode completion cannot settle twice or overwrite the current retry', async () => {
  const h = harness(), items = h.visible(12), item = items[0], decoding = deferred(); h.frame();
  item.image.decode = () => decoding.promise; await h.body(item); item.image.succeed(); await h.flush(); item.image.fail(); await h.flush();
  await h.advance(300); assert.equal(h.fetches.length, 13);
  const waiting = h.visible(1, 20)[0]; h.frame(); assert.equal(h.fetches.length, 13);
  decoding.resolve(); await h.flush();
  assert.equal(h.fetches.length, 13); assert.equal(item.article.dataset.thumbnailState, 'loading');
  item.image.decode = () => Promise.resolve(); await h.loaded(item);
  assert.equal(h.fetches.length, 14); assert.equal(h.fetches.at(-1).id, waiting.id);
  assert.equal(new Set(h.revoked).size, h.revoked.length, 'each old blob is revoked once');
});

test('120-second deadline covers fetch, response body and decode, and late completions stay inert', async t => {
  for (const stage of ['fetch', 'body', 'decode']) await t.test(stage, async () => {
    const h = harness(), [item] = h.visible(1), pending = deferred(); h.frame();
    const initial = h.fetches[0];
    if (stage === 'body') { initial.resolve(response(200, { body: pending.promise })); await h.flush(); }
    if (stage === 'decode') { item.image.decode = () => pending.promise; await h.body(item); item.image.succeed(); await h.flush(); }
    await h.advance(119999); assert.equal(item.article.dataset.thumbnailState, 'loading');
    await h.advance(1); assert.equal(initial.options.signal.aborted, true);
    assert.equal(item.article.dataset.thumbnailState, 'retry-wait');
    await h.advance(300); assert.equal(h.fetches.length, 2);
    const createdBeforeLate = h.created.length;
    if (stage === 'fetch') initial.resolve(response()); else pending.resolve({ type: 'image/webp', size: 1 });
    await h.flush(); assert.equal(item.article.dataset.thumbnailState, 'loading');
    assert.equal(h.created.length, createdBeforeLate, 'late fetch or body cannot create a leaked blob');
    item.image.decode = () => Promise.resolve(); await h.loaded(item);
    assert.equal(item.article.dataset.thumbnailState, 'loaded');
    assert.equal(new Set(h.revoked).size, h.revoked.length);
  });
});

test('queued local work gets its full timeout only when an actual slot is acquired', async () => {
  const h = harness(), items = h.visible(13); h.frame(); await h.advance(119_000);
  await h.loaded(items[0]); const thirteenth = h.fetches.find(fetch => fetch.id === 13);
  assert.equal(thirteenth.at, h.now); await h.advance(1000);
  assert.equal(items[12].article.dataset.thumbnailState, 'loading'); assert.equal(thirteenth.options.signal.aborted, false);
});

test('the no-observer fallback responds to scrolling and skips hidden ancestors', () => {
  const h = harness({ observer: false }), item = h.add(1, 1400), hidden = h.add(2);
  hidden.article.ancestorHidden = true; h.frame(); assert.equal(h.fetches.length, 0);
  item.article.rect.top = 100; item.article.rect.bottom = 300; h.windowEvents.get('scroll')(); h.frame();
  assert.deepEqual(h.fetches.map(item => item.id), [1]);
  hidden.article.ancestorHidden = false; h.windowEvents.get('resize')(); h.frame();
  assert.deepEqual(h.fetches.map(item => item.id), [1, 2]);
});
