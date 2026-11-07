const createFrontendContext = require('./helpers/frontend-context.cjs');
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../static/shared-favorites.js"), "utf8");
const app = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));
function context(extra = {}) {
  let profile = "anima", persists = 0;
  const elements = {}, messages = [];
  const sandbox = createFrontendContext({
    console, URLSearchParams, setTimeout, clearTimeout, structuredClone,
    SECTIONS: ["character", "clothing", "pose", "background", "expression"],
    SECTION_META: { character: { label: "角色" }, pose: { label: "姿势" } },
    currentProfile: () => profile, schedulePersist: () => persists++, toast: message => messages.push(message),
    Event: class { constructor(type, options) { this.type = type; Object.assign(this, options); } },
    document: { getElementById: id => elements[id] ||= { value: "", textContent: "", hidden: false }, querySelectorAll: () => [], contains: () => true },
    localStorage: { setItem() {} }, ...extra,
  });
  vm.runInContext(source, sandbox);
  vm.runInContext('for (const section of SECTIONS) sharedFavoritesState.reconciled.set(`anima:${section}`, true);', sandbox);
  return { ctx: sandbox, elements, messages, persists: () => persists, profile: value => profile = value, run: code => vm.runInContext(code, sandbox) };
}
function appFunction(name) {
  let start = app.indexOf(`function ${name}(`);
  if (app.slice(start - 6, start) === "async ") start -= 6;
  return app.slice(start, app.indexOf("\n}", start) + 2);
}

test("bulk favorite completion for an old category does not replace the active category data", async () => {
  let resolve;
  const { ctx, run } = context({
    ui: { favoriteSelectionGroups: { querySelectorAll: () => [{ value: "default" }] }, confirmFavoriteSelection: {} },
    activeSection: "pose", draft: { pools: { pose: { mode: "include", ids: ["p"] } } },
    favoritesData: { items: ["current"] }, request: () => new Promise(done => resolve = done),
  });
  run(appFunction("saveFavoriteSelection"));
  const pending = ctx.saveFavoriteSelection({ preventDefault() {} });
  ctx.activeSection = "expression"; resolve({ items: ["stale"] }); await pending;
  assert.deepEqual(ctx.favoritesData.items, ["current"]);
});

test("first-open reconciliation runs once per model and section and never emits a broadcast loop", async () => {
  const calls = [], notices = [];
  const { ctx, run } = context({ request: async (url, options) => { calls.push({ url, options }); return { model_profile: "anima", section: "pose", updatedItemIds: ["p"], deletedItemIds: [], favoritesSync: { state: "complete", updatedCount: 1, deletedCount: 0 } }; }, localStorage: { setItem: (...args) => notices.push(args) } });
  run("sharedFavoritesState.reconciled.clear()");
  await Promise.all([ctx.ensurePoolReconciled("anima", "pose"), ctx.ensurePoolReconciled("anima", "pose")]);
  await ctx.ensurePoolReconciled("anima", "pose");
  assert.equal(calls.length, 1); assert.equal(calls[0].url, "/api/favorites/pose/reconcile?model_profile=anima"); assert.equal(calls[0].options.method, "POST");
  assert.equal(notices.length, 0);
});

test("Anima source cards cancel all linked favorites by source ID while adding remains item-ID based", async () => {
  for (const section of ["character", "clothing", "pose", "background", "expression"]) {
    const calls = [];
    const { ctx, run } = context({
      activeSection: section, favoritesData: {}, favoritesAvailable: true,
      requestFavorites: async (url, options) => { calls.push({ url, body: JSON.parse(options.body) }); return { items: [], groups: [] }; },
      reconcileFavoriteCollection: () => false, currentView: () => ({ filters: { collection: "" } }),
      renderPoolItems() {}, renderPoolSidebar() {}, loadPool: async () => {},
    });
    run(appFunction("toggleFavorite"));
    await ctx.toggleFavorite({ id: "source", favorite_ids: ["alias-one", "alias-two"] }, false);
    await ctx.toggleFavorite({ id: "source" }, true);
    assert.equal(calls.length, 2); assert.equal(calls[0].url, `/api/favorites/${section}/item`);
    assert.deepEqual(calls[0].body, { source_item_id: "source", favorite: false });
    assert.deepEqual(calls[1].body, { id: "source", favorite: true });
  }
});

test("the favorite editor opened from an Anima source card removes all source associations in one request", async () => {
  const calls = [];
  const { ctx, run } = context({
    activeSection: "pose", editingFavoriteItem: { id: "source", favorite_ids: ["alias-one", "alias-two"] }, favoritesData: {},
    ui: { favoriteDialog: {} }, requestFavorites: async (url, options) => { calls.push({ url, body: JSON.parse(options.body) }); return { items: [], groups: [] }; },
    reconcileFavoriteCollection: () => false, loadPool: async () => {},
  });
  ctx.motionDialog = () => {}; run(appFunction("removeFavorite")); await ctx.removeFavorite();
  assert.equal(calls.length, 1); assert.deepEqual(calls[0].body, { source_item_id: "source", favorite: false });
});

test("Anima item deletion sends one write, preserves a newer editor, and refreshes through the lifecycle only", async () => {
  let resolve; const calls = [], notices = [], dialogs = [];
  const { ctx, run } = context({
    activeSection: "pose", editingCustom: { id: "source" }, customEditorSession: 1, customWriteBusy: false,
    confirm: () => true, request: (url, options) => { calls.push({ url, options }); return new Promise(done => resolve = done); },
    ui: { customDialog: {} }, poolSourceChanged: value => notices.push(plain(value)),
  });
  ctx.poolSourceChanged = value => notices.push(plain(value));
  ctx.motionDialog = (...args) => dialogs.push(args); run(appFunction("deleteCustomItem"));
  const pending = ctx.deleteCustomItem(); await ctx.deleteCustomItem();
  assert.equal(calls.length, 1); assert.match(calls[0].url, /source\?model_profile=anima/);
  ctx.editingCustom = { id: "new-editor" }; ctx.customEditorSession++;
  resolve({ deleted: true, deletedItemIds: ["source"], favoritesSync: { state: "complete", deletedCount: 1 } }); await pending;
  assert.equal(notices.length, 1); assert.deepEqual(notices[0].deletedItemIds, ["source"]);
  assert.equal(dialogs.length, 0); assert.equal(ctx.customWriteBusy, false); assert.equal(ctx.editingCustom.id, "new-editor");
});

test("Anima failed item deletion keeps the editor and does not prune or broadcast success", async () => {
  const notices = [], dialogs = [];
  const { ctx, elements, run } = context({
    activeSection: "pose", editingCustom: { id: "source" }, customEditorSession: 1, customWriteBusy: false,
    confirm: () => true, request: async () => { throw Object.assign(new Error("收藏不可写"), { payload: { source_committed: false, recovery_required: true } }); },
    ui: { customDialog: {} }, poolSourceChanged: value => notices.push(value),
  });
  ctx.poolSourceChanged = value => notices.push(plain(value));
  ctx.motionDialog = (...args) => dialogs.push(args); run(appFunction("deleteCustomItem")); await ctx.deleteCustomItem();
  assert.equal(notices.length, 0); assert.equal(dialogs.length, 0); assert.equal(ctx.customWriteBusy, false);
  assert.match(elements.customMutationError.textContent, /更改未提交/); assert.match(elements.customMutationError.textContent, /恢复存储/);
});

test("Anima import overwrite broadcasts its source result without replaying writes for a list refresh", async () => {
  const calls = [], notices = [];
  const preview = { rows: [{ action: "overwrite", item: { id: "source" } }] };
  const { ctx, run } = context({
    activeSection: "pose", importPreview: preview, ui: { commitImport: {}, importBundleName: { value: "Bundle" }, importDialog: {} },
    selectedImportGroupIds: () => ["group"], request: async (url, options) => { calls.push({ url, body: JSON.parse(options.body) }); return { updatedItemIds: ["source"], deletedItemIds: [], bundleName: "Bundle", imported: 0, updated: 1, skipped: 0 }; },
    poolSourceChanged: value => notices.push(plain(value)),
  });
  ctx.poolSourceChanged = value => notices.push(plain(value));
  ctx.motionDialog = () => {}; run(appFunction("commitCustomImport")); await ctx.commitCustomImport();
  assert.equal(calls.length, 1); assert.equal(calls[0].url, "/api/custom-prompts/import"); assert.equal(calls[0].body.section, "pose");
  assert.deepEqual(notices[0].updatedItemIds, ["source"]); assert.equal(notices[0].model_profile, "anima");
});
