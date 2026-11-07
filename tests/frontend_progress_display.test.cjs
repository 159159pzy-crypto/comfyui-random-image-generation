const createFrontendContext = require('./helpers/frontend-context.cjs');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function setup() {
  const element = () => ({ hidden: false, textContent: '', style: {}, parentElement: { hidden: true } });
  const ui = Object.fromEntries(['batchState', 'progressCount', 'progressFill', 'sampleFill', 'sampleProgress', 'stopButton', 'startButton', 'batchError'].map(key => [key, element()]));
  const ctx = createFrontendContext({ ui, currentBatch: null, currentQueue: [], lastTerminalBatch: '',
    renderQueue() {}, updateGenerationAvailability() {}, loadHistory() {} });
  for (const name of ['batchProgressLabel', 'renderBatch']) {
    const start = source.indexOf(`function ${name}(`);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), ctx);
  }
  return { ctx, ui };
}
function batch(overrides = {}) {
  return { id: 'anima-batch', status: 'running', stage: 'generating', total: 40, completed: 0,
    prompt_id: 'prompt-1', settings: { workbench: 'random', model_profile: 'anima' }, ...overrides };
}

test('server stage labels describe v4 progress and identify a failed stage', () => {
  const { ctx, ui } = setup();
  ctx.renderBatch(batch({ progress_stage: 'post_sampler', progress_stage_label: '整图后采样', progress_node: 'v4:post', progress: { value: 3, max: 16 } }));
  assert.equal(ui.sampleProgress.textContent, '整图后采样 3/16');
  ctx.renderBatch(batch({ status: 'error', error: '显存不足', failed_stage_label: '像素高清' }));
  assert.match(ui.batchError.textContent, /像素高清失败：显存不足/);
  ctx.renderBatch(batch({ status: 'error', error: '像素高清失败：显存不足', failed_stage_label: '像素高清' }));
  assert.equal(ui.batchError.textContent, '像素高清失败：显存不足');
});

test('random batches update progress without requesting a separate preview', async () => {
  for (const workbench of ['random']) {
    const { ctx, ui } = setup();
    const requests = [];
    ctx.fetch = async url => { requests.push(url); return { ok: false, status: 204 }; };
    const current = batch({ preview_id: 4, preview_epoch: 1, preview_node: '701:1245',
      progress_node: '701:1245', progress: { value: 3, max: 30 },
      settings: { workbench, model_profile: 'anima' } });

    ctx.renderBatch(current);
    await new Promise(resolve => setImmediate(resolve));

    assert.equal(ctx.currentBatch, current);
    assert.equal(ui.batchState.textContent, '正在生成');
    assert.equal(ui.progressCount.textContent, '0 / 40');
    assert.equal(ui.sampleProgress.textContent, '第一段采样 3/30');
    assert.equal(ui.sampleFill.style.transform, 'scaleX(0.1)');
    assert.equal(ui.stopButton.disabled, false);
    assert.equal(ui.startButton.textContent, '加入队列');
    assert.deepEqual(requests, []);
  }
});

test('Anima model loading and first-step wait remain visible without fabricated progress', () => {
  const { ctx, ui } = setup();
  for (const [node, label] of [['167:56', '正在准备模型'], ['1026:1011', '正在准备提示词'], ['701:1245', '第一段采样 · 等待首步']]) {
    ctx.renderBatch(batch({ progress_node: node }));
    assert.equal(ui.sampleProgress.hidden, false);
    assert.equal(ui.sampleProgress.textContent, label);
    assert.equal(ui.sampleFill.parentElement.hidden, true);
    assert.equal(ui.sampleFill.style.transform, 'scaleX(0)');
    assert.equal(ui.progressCount.textContent, '0 / 40');
  }
});

test('random generation shows real steps, reset on second sampler and show decode before completion', () => {
  for (const model_profile of ['anima']) {
    const { ctx, ui } = setup();
    const current = batch({ settings: { workbench: 'random', model_profile } });
    ctx.renderBatch({ ...current, progress_node: '701:1245', progress: { value: 12, max: 30 } });
    assert.equal(ui.sampleProgress.textContent, '第一段采样 12/30');
    assert.equal(ui.sampleFill.style.transform, 'scaleX(0.4)');
    assert.equal(ui.sampleFill.parentElement.hidden, false);
    ctx.renderBatch({ ...current, progress_node: '701:1246' });
    assert.equal(ui.sampleProgress.textContent, '第二段采样 · 等待首步');
    assert.equal(ui.sampleFill.parentElement.hidden, true);
    ctx.renderBatch({ ...current, progress_node: '701:1246', progress: { value: 3, max: 15 } });
    assert.equal(ui.sampleProgress.textContent, '第二段采样 3/15');
    assert.equal(ui.sampleFill.style.transform, 'scaleX(0.2)');
    ctx.renderBatch({ ...current, progress_node: '701:539' });
    assert.equal(ui.sampleProgress.textContent, '正在解码图像');
    assert.equal(ui.sampleFill.parentElement.hidden, true);
    ctx.renderBatch({ ...current, status: 'completed', completed: 40 });
    assert.equal(ui.sampleProgress.hidden, true);
    assert.equal(ui.progressFill.style.transform, 'scaleX(1)');
  }
});

test('submission, queued execution, disconnect and completed execution have distinct states', () => {
  const { ctx } = setup();
  assert.equal(ctx.batchProgressLabel(batch({ prompt_id: '' })), '正在提交任务');
  assert.equal(ctx.batchProgressLabel(batch()), '等待生成进度');
  assert.equal(ctx.batchProgressLabel(batch({ progress_node: '701:1245', preview_status: 'disconnected' })), '进度连接已断开，正在重连');
  assert.equal(ctx.batchProgressLabel(batch({ progress_node: '' })), '正在收取生成结果');
  assert.equal(ctx.batchProgressLabel(batch({ progress_node: 'v2:output' })), '正在保存图片');
});

test('normal sampling and postprocessing use their own measured step totals', () => {
  const { ctx } = setup();
  for (const [node, label] of [['691:1240', '底图采样'], ['704:1248', '潜空间二次采样'], ['712:1208', 'SeedVR2 采样']]) {
    assert.equal(ctx.batchProgressLabel(batch({ progress_node: node, progress: { value: 2, max: 8 } })), `${label} 2/8`);
  }
});





test('a local stop exposes unconfirmed remote cancellation and clears it for the next job', () => {
  const { ctx, ui } = setup();
  const warning = '旧版 ComfyUI 无法安全取消正在执行的任务，远端任务可能继续执行';
  ctx.renderBatch(batch({ status: 'stopped', cancel_warning: warning }));
  assert.equal(ui.batchState.textContent, '已停止');
  assert.equal(ui.batchError.hidden, false);
  assert.equal(ui.batchError.textContent, warning);
  assert.equal(ui.stopButton.disabled, true);
  ctx.renderBatch(batch({ id: 'next-batch' }));
  assert.equal(ui.batchError.hidden, true);
  assert.equal(ui.batchError.textContent, '');
});

test('completed generation preserves the current page while selecting or browsing a collection', () => {
  for (const mode of ['selection', 'collection', 'ordinary']) {
    const { ctx } = setup(), reads = [];
    ctx.historyPage = 4;
    ctx.artworkSelectionActive = () => mode === 'selection';
    ctx.historyDetailState = { homeQuery: { source: '', folderId: mode === 'collection' ? 'portrait' : '' } };
    ctx.loadHistory = page => reads.push(page);
    ctx.renderBatch(batch({ status: 'completed', completed: 40 }));
    assert.deepEqual(reads, [mode === 'ordinary' ? 1 : 4]);
  }
});

test('new completed output invalidates stale page and fullscreen reads once per batch', () => {
  const { ctx } = setup(); let invalidations = 0;
  ctx.invalidateHistoryView = () => invalidations++;
  const completed = batch({ status: 'completed', completed: 40 });
  ctx.renderBatch(completed); ctx.renderBatch(completed);
  assert.equal(invalidations, 1);
  ctx.renderBatch({ ...completed, id: 'next' });
  assert.equal(invalidations, 2);
});
