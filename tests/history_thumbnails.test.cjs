const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function load(ctx, ...names) {
  for (const name of names) {
    const start = source.indexOf(`function ${name}(`);
    assert.ok(start >= 0, name);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), ctx);
  }
}
function element(tag = '') {
  const classes = new Set(), attributes = new Map(), listeners = new Map();
  return {
    tag, children: [], style: {}, dataset: {}, hidden: false, complete: false,
    classList: { add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value) },
    setAttribute: (key, value) => attributes.set(key, value), getAttribute: key => attributes.get(key) ?? null,
    removeAttribute(key) { if (key === 'src') this.src = ''; attributes.delete(key); },
    addEventListener: (name, handler) => listeners.set(name, handler), listeners,
    append(child) { child.remove(); child.parentElement = this; this.children.push(child); },
    insertBefore(child, before) { child.remove(); child.parentElement = this; const index = this.children.indexOf(before); this.children.splice(index < 0 ? this.children.length : index, 0, child); },
    remove() { if (this.parentElement) { const parent = this.parentElement; parent.children.splice(parent.children.indexOf(this), 1); this.parentElement = null; } },
    replaceChildren(...children) { this.children.forEach(child => { child.parentElement = null; }); this.children = []; children.forEach(child => this.append(child)); },
    set innerHTML(value) {
      this.markup = value;
      if (tag === 'button') {
        this.image = element('img');
        this.image.dataset.thumbnailUrl = value.match(/\bdata-thumbnail-url="([^"]+)"/)[1];
        this.failure = { hidden: true };
        this.title = { textContent: '' }; this.description = { textContent: '' };
      }
    },
    get innerHTML() { return this.markup || ''; },
    querySelector(selector) {
      if (tag === 'article') return ['button', '.image-card-open'].includes(selector) ? this.children[0] : this.children[0]?.querySelector(selector);
      return selector === 'img' ? this.image : selector === '.card-copy strong' ? this.title : selector === '.card-copy span' ? this.description : this.failure;
    },
    querySelectorAll(selector) { return selector === 'img' ? this.children.flatMap(article => article.children.map(button => button.image).filter(Boolean)) : []; },
  };
}
function setup() {
  const opened = [], attachments = [], removed = [], gallery = element('gallery');
  const empty = { hidden: false, querySelector: () => ({ textContent: '' }) };
  const ctx = vm.createContext({
    document: { createElement: element }, escapeHtml: value => String(value || ''), openDetail: record => opened.push(record),
    ui: { gallery, emptyState: empty, pageLabel: {}, prevPage: {}, nextPage: {}, historyCount: {} },
    config: {}, historyDirty: true, historyPage: 1, historyPages: 1, historyItems: [], historyCache: new Map(), updatePreviewNavigation() {},
    ArtworkThumbnails: { attach: (...args) => attachments.push(args), remove: article => removed.push(article), refresh() {} },
  });
  load(ctx, 'historyViewState', 'card', 'cacheHistoryPage', 'renderHistoryPage');
  return { ctx, gallery, opened, attachments, removed };
}
const record = (id, workbench = 'random') => ({ id, filename: `${id}.png`, sequence: id, sample_seed: 0, settings: { workbench } });

test('history cards schedule thumbnails for both workbenches and have a separate retry button', () => {
  const { ctx, opened, attachments } = setup();
  for (const workbench of ['random', 'single']) {
    const value = record(7, workbench), card = ctx.card(value, 0), button = card.children[0];
    assert.equal(button.image.dataset.thumbnailUrl, '/api/images/7/thumbnail');
    assert.equal(button.image.src, undefined, 'the scheduler owns assigning src');
    assert.match(button.innerHTML, /decoding="async"/);
    assert.equal(card.children[1].className, 'image-retry');
    assert.equal(card.children[1].hidden, true);
    assert.deepEqual(attachments.at(-1), [card, button.image, card.children[1]]);
    button.listeners.get('click')();
    assert.equal(opened.at(-1), value, 'the thumbnail still opens the original record details');
  }
});

test('history paging detaches old cards and keeps its rendered list bounded', () => {
  const { ctx, gallery, removed } = setup();
  ctx.renderHistoryPage({ page: 1, pages: 3, total: 50, items: Array.from({ length: 24 }, (_, index) => record(index + 1)) });
  const prior = gallery.querySelectorAll('img');
  const priorCards = [...gallery.children];
  assert.equal(prior.length, 24);
  ctx.renderHistoryPage({ page: 2, pages: 3, total: 50, items: Array.from({ length: 24 }, (_, index) => record(index + 25, 'single')) });
  assert.equal(gallery.children.length, 24);
  assert.ok(priorCards.every(card => !card.parentElement));
  assert.ok(gallery.querySelectorAll('img').every(image => image.dataset.thumbnailUrl.endsWith('/thumbnail')));
  assert.deepEqual(removed, priorCards, 'all detached cards release queued work through the loader');
});

test('selection handles the main card without opening details and decoration preserves its image', () => {
  const { ctx, gallery, opened } = setup();
  const picked = [], decorated = [];
  ctx.artworkActivateCard = value => { picked.push(value.id); return true; };
  ctx.decorateArtworkCard = (article, value, main) => decorated.push({ article, value, main });
  const value = record(3);
  ctx.renderHistoryPage({ page: 1, pages: 1, total: 1, items: [value] });
  const node = gallery.children[0], image = node.children[0].image;
  node.children[0].listeners.get('click')();
  assert.deepEqual(picked, [3]); assert.equal(opened.length, 0);
  ctx.renderHistoryPage({ page: 1, pages: 1, total: 1, items: [{ ...value, folder_ids: ['portrait'] }] });
  assert.equal(gallery.children[0], node); assert.equal(node.children[0].image, image);
  assert.equal(decorated.at(-1).main, node.children[0]);
  assert.deepEqual(decorated.at(-1).value.folder_ids, ['portrait']);
});

test('unchanged refresh preserves image nodes and focus targets; metadata and click use newest record', () => {
  const { ctx, gallery, opened } = setup();
  const first = record(1), second = record(2);
  ctx.renderHistoryPage({ page: 1, pages: 1, total: 2, items: [first, second] });
  const nodes = [...gallery.children], images = gallery.querySelectorAll('img');
  const updated = { ...first, filename: 'updated.png' };
  ctx.renderHistoryPage({ page: 1, pages: 1, total: 2, items: [updated, second] });
  assert.deepEqual(gallery.children, nodes);
  assert.deepEqual(gallery.querySelectorAll('img'), images);
  assert.equal(nodes[0].querySelector('.card-copy strong').textContent, 'updated.png');
  nodes[0].querySelector('button').listeners.get('click')();
  assert.equal(opened.at(-1), updated);
  ctx.renderHistoryPage({ page: 1, pages: 1, total: 2, items: [second, updated] });
  assert.equal(gallery.children[0], nodes[1]);
  assert.equal(gallery.children[1], nodes[0]);
});

test('details load the original for both workbenches and release it only after the dialog closes', () => {
  const image = element('img');
  const negativeLabel = { firstChild: { textContent: '' } };
  const ctx = vm.createContext({
    ui: { detailImage: image, detailDialog: { open: true }, detailMeta: {}, detailPositive: {}, detailNegative: { closest: () => negativeLabel }, detailStats: {}, detailSelection: {} },
    resetRevealImageFile() {}, renderSingleDetail() {}, resetSingleDetail() {}, closeRegenerationDrawers() {},
    normalizeSettings: settings => ({ ...settings, loras: [] }), SECTIONS: [], escapeHtml: String,
    detailContext: { origin: 'random' }, closeImagePreview() {}, regenerationRequest: 0, regenerationOptions: {}, document: { querySelector: () => null },
  });
  load(ctx, 'loraRuntimeMarkup', 'renderDetail', 'finishDetailClose');
  for (const zero of [true, false]) {
    const item = record(7); item.settings.zero_negative = zero; item.negative_prompt = 'original negative';
    ctx.renderDetail(item);
    assert.equal(ctx.ui.detailNegative.value, 'original negative');
    assert.equal(negativeLabel.firstChild.textContent, zero ? '负面提示词 · 编码后条件置零' : '负面提示词');
    assert.match(ctx.ui.detailStats.innerHTML, zero ? /<dd>置零<\/dd>/ : /<dd>使用负面提示词<\/dd>/);
  }
  for (const workbench of ['random', 'single']) {
    ctx.renderDetail(record(7, workbench));
    assert.equal(image.src, '/api/images/7');
    image.onload = image.onerror = () => {};
    ctx.finishDetailClose();
    assert.equal(image.src, '/api/images/7', 'a stale close event must not clear an open dialog');
    ctx.ui.detailDialog.open = false;
    ctx.finishDetailClose();
    assert.equal(image.src, '');
    assert.equal(image.onload, null);
    assert.equal(image.onerror, null);
    ctx.ui.detailDialog.open = true;
  }
});
