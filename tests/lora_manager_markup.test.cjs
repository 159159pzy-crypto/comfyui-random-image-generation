const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../static/lora-manager.css'), 'utf8');
const compact = html.match(/<form id="lmCompactPane"[^>]*>[\s\S]*?<\/form>/)?.[0];
const dialog = html.match(/<dialog id="loraDialog"[^>]*>[\s\S]*?<\/dialog>/)?.[0];
const normalize = value => value.replace(/\s+/g, ' ').trim();

// Frozen from the original quick-add dialog. Its visible UI has exactly one
// authorized addition: the expand icon beside the existing close button.
const originalQuickAdd = `<form method="dialog">
  <div class="dialog-heading"><div><span class="kicker">LOCAL LORA</span><h2>添加本地 LoRA</h2></div><button id="closeLora" class="icon-button dark" type="button" title="关闭" aria-label="关闭">×</button></div>
  <label class="search-field lora-search"><span aria-hidden="true">⌕</span><input id="loraSearch" type="search" placeholder="搜索 LoRA、目录或触发词"></label>
  <div id="loraCatalog" class="lora-catalog"></div>
  <div id="loraCatalogEmpty" class="inline-empty" hidden>没有匹配的本地 LoRA。</div>
  <div class="dialog-actions"><span id="loraCatalogMeta">正在读取本地列表</span><span></span><button id="cancelLora" class="button ghost" type="button">完成</button></div>
</form>`;

test('quick add retains its original content and components except the icon entry', () => {
  assert.ok(compact);
  const restored = compact.replace(' id="lmCompactPane"', '')
    .replace(/<button id="lmExpandCompact"[\s\S]*?<\/button>/, '')
    .replace('<div class="lm-compact-actions">', '')
    .replace(/(<button id="closeLora"[\s\S]*?<\/button>)<\/div>/, '$1');
  assert.equal(normalize(restored), normalize(originalQuickAdd));
  const icon = compact.match(/<button id="lmExpandCompact"[\s\S]*?<\/button>/)?.[0];
  assert.match(icon, /aria-label="展开 LoRA 管理"/);
  assert.match(icon, /<svg\b/);
  assert.equal(icon.replace(/<[^>]+>/g, '').trim(), '', 'the entry must remain icon-only');
});

test('quick add and full management own separate nodes inside one native dialog', () => {
  assert.ok(dialog);
  assert.match(dialog, /^<dialog id="loraDialog" class="compact-dialog lora-dialog">/);
  assert.match(dialog, /<\/form>\s*<section id="lmExpandedPane"[^>]* hidden inert>/);
  const expanded = dialog.slice(dialog.indexOf('<section id="lmExpandedPane"'));
  for (const id of ['loraSearch', 'loraCatalog', 'loraCatalogEmpty', 'loraCatalogMeta', 'closeLora', 'cancelLora']) {
    assert.match(compact, new RegExp(`id="${id}"`));
    assert.doesNotMatch(expanded, new RegExp(`id="${id}"`));
  }
  for (const id of ['lmSearch', 'lmCatalog', 'lmCatalogEmpty', 'lmCatalogMeta', 'lmClose', 'lmDone']) {
    assert.match(expanded, new RegExp(`id="${id}"`));
  }
  assert.match(dialog, /id="lmShellSurface" aria-hidden="true"/);
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(ids.length, new Set(ids).size, 'duplicated IDs would wire the wrong pane');
});

test('library has folder-first navigation, accessible tag choices, and removal action', () => {
  const sidebar = dialog.match(/<nav id="lmSidebar"[\s\S]*?<\/nav>/)?.[0];
  assert.ok(sidebar.indexOf('id="lmFolders"') < sidebar.indexOf('id="lmGroups"'));
  for (const id of ['lmFilterChips', 'lmFilterTagSearch', 'lmIncludeTags', 'lmExcludeTags', 'lmBatchRemove']) {
    assert.match(dialog, new RegExp(`id="${id}"`));
  }
  assert.match(dialog, /id="lmBatchRemove"[^>]*>移出当前配置<\/button>/);
  for (const id of ['lmTagFilter', 'lmExcludeTagFilter']) {
    assert.match(dialog, new RegExp(`id="${id}"[^>]* hidden[^>]*tabindex="-1"`));
  }
});

test('sorting is available in the toolbar without opening the filters', () => {
  const toolbar = dialog.slice(dialog.indexOf('<div class="lm-toolbar">'), dialog.indexOf('<section id="lmFilters"'));
  const filters = dialog.match(/<section id="lmFilters"[\s\S]*?<\/section>/)?.[0];
  assert.match(toolbar, /class="lm-sort-control"><span class="sr-only">排序<\/span><select id="lmSort"/);
  assert.match(toolbar, /id="lmSortOrder"[^>]*aria-label="[^"]+"[^>]*>↑<\/button>/);
  assert.doesNotMatch(filters, /id="lmSort"|id="lmSortOrder"/);
  assert.match(css, /\.lm-toolbar-actions\s*\{[^}]*overflow-x:\s*auto/);
});

test('manager styles do not redefine original quick-add controls or compact geometry', () => {
  assert.doesNotMatch(css, /\.lm-dialog\b|#loraCatalog\b|#loraSearch\b|#loraCatalogMeta\b|\.lora-catalog\b/);
  for (const match of css.matchAll(/#lmCompactPane\s*\{([^{}]*)\}/g)) {
    assert.doesNotMatch(match[1], /(?:^|;)\s*(?:width|height|padding(?:-\w+)?|margin(?:-\w+)?)\s*:/);
  }
  for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = match[1].trim();
    // The glass material has a persistent owner across both modes; only
    // expanded/morphing rules may override the original compact geometry.
    if (/\b(?:width|height|padding|margin|border-radius)\s*:/.test(match[2]) && selector.includes('#loraDialog')) {
      for (const part of selector.split(',')) {
        if (part.includes('#loraDialog')) assert.match(part, /\.lm-(?:expanded|morphing)/, part);
      }
    }
  }
  assert.match(css, /\.lm-body\s*\{[^}]*grid-template-columns:\s*224px minmax\(0, 1fr\)/);
  assert.match(css, /\.lm-detail-open \.lm-body\s*\{[^}]*360px/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.match(css, /prefers-reduced-transparency:\s*reduce/);
});
