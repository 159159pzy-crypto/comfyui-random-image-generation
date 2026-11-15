const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = app.indexOf('function renderStorageStatus(');
assert.ok(start >= 0);
const renderSource = app.slice(start, app.indexOf('\n}', start) + 2);

function harness(discovery, storage = {}) {
  const panel = { hidden: true, textContent: '' };
  Object.defineProperty(panel, 'innerHTML', {
    set() { throw new Error('Discovery diagnostics must be rendered as text'); },
  });
  let protectedCalls = 0;
  const context = vm.createContext({
    config: { catalog: { discovery }, storage_status: storage },
    document: {
      getElementById: id => { assert.equal(id, 'storageWarning'); return panel; },
      querySelectorAll: selector => { assert.equal(selector, 'dialog'); return []; },
    },
    protectStorageControls: () => { protectedCalls++; },
  });
  vm.runInContext(renderSource, context);
  context.renderStorageStatus();
  return { context, panel, protectedCalls: () => protectedCalls };
}

test('missing tools warning keeps searched locations and recovery instructions readable as text', () => {
  const searched = [
    String.raw`D:\工作目录\ComfyUI_windows_portable\ComfyUI\custom_nodes`,
    '/tmp/Anima <img src=x onerror=alert(1)>/custom_nodes',
  ];
  const message = `未找到 Anima Tools。已检查：\n${searched.join('\n')}\n请用 --anima-tools-dir 指定词库。`;
  const { panel, protectedCalls } = harness({ status: 'missing', searched_paths: searched, message });
  assert.equal(panel.hidden, false);
  assert.equal(panel.textContent, message);
  for (const location of searched) assert.ok(panel.textContent.includes(location));
  assert.ok(panel.textContent.includes('--anima-tools-dir'));
  assert.equal(protectedCalls(), 1);
});

test('explicit invalid path remains visible with an empty searched_paths list', () => {
  const location = String.raw`E:\我的词库\不存在`;
  const message = `Anima Tools 路径无效: ${location}；缺少: js/character_data.js。请修正指定路径并重启。`;
  const { panel } = harness({ status: 'invalid', source: 'argument', path: location, searched_paths: [], message });
  assert.equal(panel.hidden, false);
  assert.equal(panel.textContent, message);
  assert.ok(panel.textContent.includes(location));
  assert.ok(panel.textContent.includes('js/character_data.js'));
  assert.ok(!panel.textContent.includes('undefined'));
});

test('discovery diagnostics coexist with read-only warnings and clear after successful discovery', () => {
  const discovery = { status: 'missing', searched_paths: ['/tmp/custom_nodes'], message: '未找到词库：/tmp/custom_nodes' };
  const storage = { history: { path: '/tmp/history.sqlite3', read_only: true, reason: '数据库只读' } };
  const { context, panel, protectedCalls } = harness(discovery, storage);
  assert.equal(panel.textContent, '/tmp/history.sqlite3：数据库只读\n未找到词库：/tmp/custom_nodes');
  context.config.catalog.discovery = { status: 'available', searched_paths: [], message: '' };
  context.renderStorageStatus();
  assert.equal(panel.hidden, false);
  assert.equal(panel.textContent, '/tmp/history.sqlite3：数据库只读');
  context.config.storage_status = {};
  context.renderStorageStatus();
  assert.equal(panel.hidden, true);
  assert.equal(panel.textContent, '');
  assert.equal(protectedCalls(), 3);
});

test('initialization keeps discovery diagnostics in the warning panel without repeating them as a toast', async () => {
  const message = '未找到 Anima Tools。已检查：\n/tmp/ComfyUI/custom_nodes\n请用 --anima-tools-dir 指定词库。';
  const discovery = { status: 'missing', searched_paths: ['/tmp/ComfyUI/custom_nodes'], message };
  const { context, panel } = harness(discovery);
  const otherWarnings = ['损坏的收藏文件已备份', '预设已从备份恢复'];
  const payload = { defaults: {}, catalog: { discovery }, storage_status: {}, warnings: [otherWarnings[0], message, otherWarnings[1]] };
  const messages = [];
  panel.hidden = true;
  panel.textContent = '';
  Object.assign(context, {
    initialTheme: () => 'light', applyTheme() {},
    request: async url => { assert.equal(url, '/api/config'); return payload; },
    toast: warning => messages.push(warning),
    loadStoredView() {}, loadStoredDraft: async () => true,
    initializeRandomWorkbench() {}, initializePoolLifecycle() {}, initializeLoraManager() {},
    ui: { appError: { hidden: true }, draftStatus: {} }, initialized: false,
    setInterval() {},
    loadResources: async () => {}, refreshConnection: async () => {}, pollBatch: async () => {},
    loadHistory: async () => {}, loadLoraInventory: async () => {},
    loadArtistFavorites: async () => {}, loadStylePresets: async () => {},
  });
  context.document.addEventListener = () => {};
  const initializeStart = app.indexOf('async function initialize(');
  assert.ok(initializeStart >= 0);
  vm.runInContext(app.slice(initializeStart, app.indexOf('\n}', initializeStart) + 2), context);

  await context.initialize();

  assert.equal(context.initialized, true);
  assert.equal(context.ui.appError.hidden, true);
  assert.deepEqual(messages, otherWarnings);
  assert.equal(panel.hidden, false);
  assert.equal(panel.textContent, message);
});
