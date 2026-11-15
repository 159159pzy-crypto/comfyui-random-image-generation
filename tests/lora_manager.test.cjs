const test = require('node:test');
const assert = require('node:assert/strict');
const { createController, groupRows, groupPath, descendants, bindingPayload, mergeDetailDraft, detailDirtyFields, metadataPatch, submitLibraryLoras, cardNavigationIndex, queryString } = require('../static/lora-manager.js');
const { configuredRemoval } = require('../static/lora-manager.js');

function harness({cache = false} = {}) {
  let profile = 'anima', loras = [], time = 0;
  const pending = [], changes = [];
  const controller = createController({
    request(url, options = {}) { return new Promise((resolve, reject) => pending.push({ url, options, resolve, reject })); },
    getProfile: () => profile, getLoras: () => loras,
    now: () => time, cacheTtl: cache ? 30000 : 0,
    onChange: state => changes.push({ loading: state.loading, ids: state.items.map(item => item.id), selected: [...state.selected] }),
  });
  return { controller, pending, changes, profile(value) { profile = value; }, loras(value) { loras = value; }, tick(ms) { time += ms; } };
}
const page = (ids, extra = {}) => ({ items: ids.map(id => ({ id, filename: `${id}.safetensors` })), total: ids.length, page: 1, total_pages: 1, groups: [], facets: {}, ...extra });

test('reopening an exact fresh query reuses its cache and expiry revalidates without blocking its cards', async () => {
  const h=harness({cache:true});let loading=h.controller.load();h.pending[0].resolve(page(['one']));await loading;
  h.controller.cancel();h.tick(29999);await h.controller.load();assert.equal(h.pending.length,1);
  h.tick(1);loading=h.controller.load();
  assert.deepEqual(h.controller.state.items.map(item=>item.id),['one']);
  assert.equal(h.controller.state.loading,false);assert.equal(h.controller.state.revalidating,true);
  assert.equal(new URL(h.pending[1].url,'http://local').searchParams.has('refresh'),false);
  h.pending[1].resolve(page(['one','two']));await loading;assert.equal(h.controller.state.revalidating,false);
});

test('query caches include search and host profile even when browsing every model', async () => {
  const h=harness({cache:true});h.controller.state.scope='all';
  let loading=h.controller.load();h.pending[0].resolve(page(['first']));await loading;
  loading=h.controller.update({q:'second'});h.pending[1].resolve(page(['second']));await loading;
  await h.controller.update({q:''});assert.equal(h.pending.length,2);assert.equal(h.controller.state.items[0].id,'first');
  h.profile('krea2');loading=h.controller.load();assert.equal(h.controller.state.loading,true);
  h.pending[2].resolve(page(['krea-view']));await loading;
  h.profile('anima');await h.controller.load();assert.equal(h.pending.length,3);assert.equal(h.controller.state.items[0].id,'first');
});

test('manual refresh always reaches the server and an offline cache remains read-only until recovery', async () => {
  const h=harness({cache:true});let loading=h.controller.load();h.pending[0].resolve(page(['one']));await loading;
  loading=h.controller.load({refresh:true});assert.equal(new URL(h.pending[1].url,'http://local').searchParams.get('refresh'),'true');
  h.pending[1].reject(new Error('offline'));await loading;assert.equal(h.controller.state.writable,false);
  loading=h.controller.load();assert.equal(h.pending.length,3);assert.equal(h.controller.state.writable,false);
  h.pending[2].resolve(page(['one']));await loading;assert.equal(h.controller.state.writable,true);
});

test('favorite changes are immediate and per-item pending state survives old reads then rolls back on failure', async () => {
  const h=harness();let loading=h.controller.load();h.pending[0].resolve(page(['one','two'],{items:[{id:'one',favorite:false},{id:'two',favorite:false}]}));await loading;
  loading=h.controller.load();const oldDetail=h.controller.readCurrent();
  const first=h.controller.beginMutation('one',{favorite:true}),second=h.controller.beginMutation('two',{favorite:true});
  assert.equal(h.controller.state.items[0].favorite,true);assert.equal(h.controller.state.items[1].favorite,true);
  assert.equal(h.controller.beginMutation('one',{favorite:false}),null);assert.equal(oldDetail(),false);
  h.pending[1].resolve(page(['one','two'],{items:[{id:'one',favorite:false},{id:'two',favorite:false}]}));await loading;
  assert.equal(h.controller.state.items[0].favorite,true);
  assert.equal(h.controller.state.revalidationNeeded,true,'an invalidated read must be retried after concurrent writes settle');
  h.controller.finishMutation(first,null,{failed:true});assert.equal(h.controller.state.items[0].favorite,false);
  assert.equal(h.controller.state.pendingMutations.has('two'),true);
  h.controller.finishMutation(second,{id:'two',favorite:true});assert.equal(h.controller.state.items[1].favorite,true);
});

test('successful favorites reject reads started while the write was pending and update the reusable cache', async () => {
  const h=harness({cache:true});let loading=h.controller.load();h.pending[0].resolve(page(['one'],{items:[{id:'one',favorite:false}]}));await loading;
  const mutation=h.controller.beginMutation('one',{favorite:true});loading=h.controller.load({revalidate:true});
  h.controller.finishMutation(mutation,{id:'one',favorite:true});h.pending[1].resolve(page(['one'],{items:[{id:'one',favorite:false}]}));await loading;
  assert.equal(h.controller.state.items[0].favorite,true);
  await h.controller.load();assert.equal(h.pending.length,2);assert.equal(h.controller.state.items[0].favorite,true);
});

test('unfavoriting retains the pending card then clamps the last page and expires collection caches', async () => {
  const h=harness({cache:true});h.controller.state.collection='__all__';h.controller.state.page=2;
  let loading=h.controller.load();h.pending[0].resolve(page(['last'],{items:[{id:'last',favorite:true}],total:61,total_pages:2,page:2}));await loading;
  const mutation=h.controller.beginMutation('last',{favorite:false});assert.equal(h.controller.state.items.length,1);assert.equal(h.controller.state.page,2);
  h.controller.finishMutation(mutation,{id:'last',favorite:false});assert.equal(h.controller.state.items.length,0);assert.equal(h.controller.state.total,60);assert.equal(h.controller.state.page,1);
  loading=h.controller.load({revalidate:true});assert.equal(new URL(h.pending[1].url,'http://local').searchParams.get('page'),'1');
  h.pending[1].resolve(page(['first'],{total:60,total_pages:1}));await loading;
});

test('a partially saved favorite remains visible and cannot be edited until confirmed', async () => {
  const h=harness();h.controller.state.collection='__all__';let loading=h.controller.load();
  h.pending[0].resolve(page(['one'],{items:[{id:'one',favorite:true}]}));await loading;
  const mutation=h.controller.beginMutation('one',{favorite:false});h.controller.finishMutation(mutation,null,{failed:true,partial:true});
  assert.equal(h.controller.state.items.length,1);assert.equal(h.controller.state.items[0].favorite,false);
  assert.equal(h.controller.state.items[0].pending_local,true);assert.equal(h.controller.state.total,1);
  assert.equal(h.controller.beginMutation('one',{favorite:true}),null);
});

test('late writes update their cache without injecting old items into a different profile or owning its view', async () => {
  const h=harness({cache:true});let loading=h.controller.load();h.pending[0].resolve(page(['old'],{items:[{id:'old',favorite:false}]}));await loading;
  const mutation=h.controller.beginMutation('old',{favorite:true});h.controller.cancel();assert.equal(mutation.current(),false);
  h.profile('krea2');loading=h.controller.load();h.pending[1].resolve(page(['new']));await loading;
  h.controller.finishMutation(mutation,{id:'old',favorite:true});assert.equal(h.controller.state.known.has('old'),false);
  assert.deepEqual(h.controller.state.items.map(item=>item.id),['new']);
  h.profile('anima');await h.controller.load();assert.equal(h.pending.length,2);assert.equal(h.controller.state.items[0].favorite,true);
});

test('a failed favorite restores the same visible item after navigation without owning the new view', async () => {
  const h=harness();let loading=h.controller.load();h.pending[0].resolve(page(['one'],{items:[{id:'one',favorite:false}]}));await loading;
  const mutation=h.controller.beginMutation('one',{favorite:true});loading=h.controller.update({q:'one'});
  h.pending[1].resolve(page(['one'],{items:[{id:'one',favorite:false}]}));await loading;
  assert.equal(mutation.current(),false);assert.equal(h.controller.state.items[0].favorite,true);
  h.controller.finishMutation(mutation,null,{failed:true});
  assert.equal(h.controller.state.items[0].favorite,false);assert.equal(h.controller.state.known.get('one').favorite,false);
  assert.equal(h.controller.state.pendingMutations.size,0);assert.equal(h.controller.state.q,'one');
});

test('shared all-profile items settle late favorites without importing the old view flags', async t => {
  for(const outcome of ['success','failure','partial'])await t.test(outcome,async()=>{
    const h=harness({cache:true});h.controller.state.scope='all';let loading=h.controller.load();
    h.pending[0].resolve(page(['shared'],{items:[{id:'shared',favorite:false,available:true,writable:true,offline:false,cached:false,notes:'before'}]}));await loading;
    const mutation=h.controller.beginMutation('shared',{favorite:true});h.profile('krea2');loading=h.controller.load();
    h.pending[1].resolve(page(['shared'],{items:[{id:'shared',favorite:false,available:false,writable:false,offline:true,cached:true,notes:'newer note'}]}));await loading;
    assert.equal(mutation.current(),false);assert.equal(h.controller.state.items[0].favorite,true);
    assert.equal(h.controller.state.items[0].available,false,'pending metadata must not carry the previous view availability');
    const saved={id:'shared',favorite:true,available:true,writable:true,offline:false,cached:false,notes:'canonical'};
    h.controller.finishMutation(mutation,saved,{failed:outcome!=='success',partial:outcome==='partial'});
    const current=h.controller.state.items[0];assert.equal(current.favorite,outcome!=='failure');
    assert.equal(current.available,false);assert.equal(current.writable,false);assert.equal(current.offline,true);assert.equal(current.cached,true);
    assert.equal(current.notes,outcome==='success'?'canonical':'newer note');
    assert.equal(!!current.pending_local,outcome==='partial');assert.equal(h.controller.state.pendingMutations.size,0);
    assert.equal(h.controller.state.known.get('shared').favorite,current.favorite);
  });
});

test('authoritative detail reads replace runtime flags instead of retaining stale view availability', async () => {
  const h=harness({cache:true});let loading=h.controller.load();
  h.pending[0].resolve(page(['one'],{items:[{id:'one',available:true,writable:true,offline:false,cached:false}]}));await loading;
  h.controller.replace({id:'one',available:false,writable:false,offline:false,cached:true},{fromRead:true});
  for(const item of [h.controller.state.items[0],h.controller.state.known.get('one')]){
    assert.equal(item.available,false);assert.equal(item.writable,false);assert.equal(item.cached,true);
  }
  h.controller.cancel();await h.controller.load();assert.equal(h.pending.length,1);
  assert.equal(h.controller.state.items[0].available,false);assert.equal(h.controller.state.items[0].writable,false);
});

test('metadata and group mutations invalidate fresh list caches instead of hiding saved changes for thirty seconds', async () => {
  const h=harness({cache:true});let loading=h.controller.load();h.pending[0].resolve(page(['one']));await loading;
  h.controller.replace({id:'one',tags:['saved']},{changedFields:['tags']});loading=h.controller.load();
  assert.equal(h.pending.length,2);h.pending[1].resolve(page(['one'],{items:[{id:'one',tags:['saved']}]}));await loading;
  h.controller.invalidate();loading=h.controller.load();assert.equal(h.pending.length,3);h.pending[2].resolve(page(['one'],{groups:[{id:'new',name:'New'}]}));await loading;
  assert.equal(h.controller.state.groups[0].id,'new');
});

test('a late search response cannot replace a more recent result, even if transport ignores abort', async () => {
  const h = harness();
  const first = h.controller.update({ q: 'alice' });
  const second = h.controller.update({ q: 'bob' });
  assert.equal(h.pending[0].options.signal.aborted, true);
  h.pending[1].resolve(page(['bob'])); await second;
  h.pending[0].resolve(page(['alice'])); await first;
  assert.deepEqual(h.controller.state.items.map(item => item.id), ['bob']);
  assert.equal(h.controller.state.loading, false);
});

test('typed search is committed before debounce so repeated expand redraws cannot restore the previous query', async () => {
  const h = harness();
  const first = h.controller.load(); h.pending[0].resolve(page(['old'])); await first;
  h.controller.stage({ q: 'denia' });
  // Expand/collapse synchronizes the input from controller state immediately,
  // while the deferred network read is still pending.
  let input = 'denia';
  for (let i = 0; i < 4; i++) input = h.controller.state.q;
  assert.equal(input, 'denia');
  assert.notEqual(h.controller.state.resultKey, JSON.stringify(h.controller.query()), 'previous results must stay noninteractive');
  const searched = h.controller.load();
  assert.equal(new URL(h.pending[1].url, 'http://localhost').searchParams.get('q'), 'denia');
  h.pending[1].resolve(page(['denia'])); await searched;
  assert.equal(h.controller.state.resultKey, JSON.stringify(h.controller.query()));
  assert.deepEqual(h.controller.state.items.map(item => item.id), ['denia']);
});

test('changing the host model discards in-flight inventory and selections from the old model', async () => {
  const h = harness();
  h.controller.toggle('anima-only');
  const old = h.controller.load();
  h.profile('krea2');
  const fresh = h.controller.load();
  h.pending[1].resolve(page(['krea'])); await fresh;
  h.pending[0].resolve(page(['anima'])); await old;
  assert.deepEqual([...h.controller.state.selected], []);
  assert.deepEqual(h.controller.state.items.map(item => item.id), ['krea']);
  assert.equal(h.controller.state.known.has('anima'), false);
});

test('select all captures the full query and includes off-page ids without altering previous selections', async () => {
  const h = harness();
  h.controller.state.page = 3;
  h.controller.state.q = 'style';
  h.controller.state.tag = ['painting', 'warm'];
  h.controller.state.tag_exclude = ['watermark'];
  h.controller.state.collection = 'group-a';
  h.controller.toggle('previous');
  const selection = h.controller.selectAll();
  const body = JSON.parse(h.pending[0].options.body);
  assert.deepEqual(body.query.tag, ['painting', 'warm']);
  assert.deepEqual(body.query.tag_exclude, ['watermark']);
  assert.equal(body.query.collection, 'group-a');
  assert.equal(body.query.page, undefined);
  assert.equal(body.query.limit, undefined);
  h.pending[0].resolve({ ids: ['visible', 'off-page', 'previous'], revision: 9 }); await selection;
  assert.deepEqual([...h.controller.state.selected], ['previous', 'visible', 'off-page']);
  assert.equal(h.controller.state.selectionRevision, 9);
});

test('changing filters during select all rejects that operation instead of selecting unrelated resources', async () => {
  const h = harness();
  h.controller.toggle('kept');
  const selection = h.controller.selectAll();
  const load = h.controller.update({ folder: 'characters' });
  h.pending[0].resolve({ ids: ['old-query'] }); await selection;
  h.pending[1].resolve(page(['new-query'])); await load;
  assert.deepEqual([...h.controller.state.selected], ['kept']);
  assert.equal(h.controller.state.selectionLoading, false);
});

test('selection failures do not erase chosen items and leave retry available', async () => {
  const h = harness(); h.controller.toggle('kept');
  const selection = h.controller.selectAll();
  h.pending[0].reject(new Error('offline'));
  await assert.rejects(selection, /offline/);
  assert.deepEqual([...h.controller.state.selected], ['kept']);
  assert.equal(h.controller.state.selectionLoading, false);
});

test('pagination stays bounded at sixty items and preserves selection across pages', async () => {
  const h = harness();
  let loading = h.controller.load();
  assert.equal(new URL(h.pending[0].url, 'http://localhost').searchParams.get('limit'), '60');
  h.pending[0].resolve(page(['one', 'two'], { total: 122, total_pages: 3 })); await loading;
  h.controller.selectPage();
  loading = h.controller.update({ page: 2 });
  h.pending[1].resolve(page(['three'], { total: 122, total_pages: 3, page: 2 })); await loading;
  h.controller.toggle('three');
  assert.deepEqual([...h.controller.state.selected], ['one', 'two', 'three']);
  assert.equal(h.controller.state.page, 2);
  assert.equal(h.controller.state.pages, 3);
});

test('current configuration filtering uses host filenames in both page and all-result queries', async () => {
  const h = harness();
  h.loras([{ filename: 'people/alice.safetensors' }, { filename: 'styles/ink.safetensors' }]);
  const loading = h.controller.update({ in_configured: 'false' });
  const url = new URL(h.pending[0].url, 'http://localhost');
  assert.deepEqual(url.searchParams.getAll('configured_filenames'), ['people/alice.safetensors', 'styles/ink.safetensors']);
  assert.equal(url.searchParams.get('in_configured'), 'false');
  assert.equal(url.searchParams.has('configured'), false);
  h.pending[0].resolve(page(['new'])); await loading;
  const selecting = h.controller.selectAll();
  const query = JSON.parse(h.pending[1].options.body).query;
  assert.deepEqual(query.configured_filenames, ['people/alice.safetensors', 'styles/ink.safetensors']);
  h.pending[1].resolve({ ids: ['new'] }); await selecting;
});

test('a configuration change while selecting all invalidates the captured query', async () => {
  const h = harness();
  h.controller.state.in_configured = 'true'; h.loras([{ filename: 'before' }]);
  const selecting = h.controller.selectAll();
  h.loras([{ filename: 'after' }]);
  h.pending[0].resolve({ ids: ['before'] }); await selecting;
  assert.equal(h.controller.state.selected.size, 0);
});

test('all-results removal uses the frozen filenames for unvisited pages and never matches a basename', async () => {
  const h = harness();
  h.loras([{filename:'first/shared.safetensors'}, {filename:'second/shared.safetensors'}, {filename:'other/shared.safetensors'}]);
  h.controller.state.known.set('first',{id:'first',filename:'first/shared.safetensors'});
  const selecting=h.controller.selectAll();
  h.pending[0].resolve({ids:['first','second'],resources:[{id:'first',filename:'first/shared.safetensors'},{id:'second',filename:'second/shared.safetensors'}],revision:4});
  await selecting;
  h.controller.markOffline('fixture offline');
  assert.deepEqual(configuredRemoval([...h.controller.state.selected],h.controller.state.known,h.controller.query().configured_filenames || [{filename:'first/shared.safetensors'},{filename:'second/shared.safetensors'},{filename:'other/shared.safetensors'}],h.controller.state.selectionResources),['first/shared.safetensors','second/shared.safetensors']);
  assert.equal(h.controller.state.known.has('second'),false,'selection references must not masquerade as full model metadata');
});

test('stale all-results responses cannot leak filename mappings across profiles', async () => {
  const h=harness();const selecting=h.controller.selectAll();h.profile('krea2');h.controller.profileChanged();
  h.pending[0].resolve({ids:['old'],resources:[{id:'old',filename:'old.safetensors'}]});await selecting;
  assert.equal(h.controller.state.selectionResources.size,0);
});

test('failed refresh retains a successful same-query cache explicitly read-only', async () => {
  const h = harness();
  let loading = h.controller.load(); h.pending[0].resolve(page(['old'])); await loading;
  loading = h.controller.load(); h.pending[1].reject(new Error('plugin offline')); await loading;
  assert.deepEqual(h.controller.state.items.map(item=>item.id), ['old']);
  assert.equal(h.controller.state.error, 'plugin offline');
  assert.equal(h.controller.state.loading, false);
  assert.equal(h.controller.state.offline, true);
  assert.equal(h.controller.state.cached, true);
  assert.equal(h.controller.state.writable, false);
  assert.equal(h.controller.state.resultKey, JSON.stringify(h.controller.query()));
});

test('offline fallback cannot present another query result as if it matched the requested filters', async () => {
  const h=harness();let loading=h.controller.load();h.pending[0].resolve(page(['anima']));await loading;
  loading=h.controller.update({q:'unseen'});h.pending[1].reject(new Error('offline'));await loading;
  assert.deepEqual(h.controller.state.items,[]);assert.equal(h.controller.state.cached,false);
  loading=h.controller.update({q:''});h.pending[2].reject(new Error('offline'));await loading;
  assert.deepEqual(h.controller.state.items.map(item=>item.id),['anima']);assert.equal(h.controller.state.cached,true);
});

test('server-side offline snapshots remain browseable but never imply writable or fresh state, then recover on refresh', async () => {
  const h=harness();let loading=h.controller.load();
  h.pending[0].resolve(page(['cached'],{offline:true,cached:true,writable:false,error:'connection refused',last_synced_at:'2026-11-07T00:00:00Z'}));
  assert.equal(await loading,false);assert.equal(h.controller.state.writable,false);assert.equal(h.controller.state.cached,true);
  loading=h.controller.load({refresh:true});h.pending[1].resolve(page(['fresh'],{offline:false,cached:false,writable:true,error:null}));
  assert.equal(await loading,true);assert.equal(h.controller.state.offline,false);assert.equal(h.controller.state.cached,false);assert.equal(h.controller.state.writable,true);assert.equal(h.controller.state.error,'');
  assert.equal(h.controller.state.known.has('cached'),false);
});

test('only completed online refreshes give failed covers another retry generation', async () => {
  const h = harness();
  let loading = h.controller.load(); h.pending[0].resolve(page(['one'])); await loading;
  assert.equal(h.controller.state.previewRetryGeneration, 0);
  h.controller.toggle('one'); h.controller.clearSelection();
  h.controller.replace({id: 'one', filename: 'one.safetensors', favorite: true});
  assert.equal(h.controller.state.previewRetryGeneration, 0, 'ordinary renders must not authorize image retries');

  loading = h.controller.load({refresh: true});
  assert.equal(h.controller.state.previewRetryGeneration, 0, 'wait for the inventory response');
  h.pending[1].resolve(page(['one'])); assert.equal(await loading, true);
  assert.equal(h.controller.state.previewRetryGeneration, 1);

  loading = h.controller.load(); h.pending[2].resolve(page(['one'])); await loading;
  assert.equal(h.controller.state.previewRetryGeneration, 1, 'ordinary reads do not retry failed covers');
  loading = h.controller.load({refresh: true}); h.pending[3].resolve(page(['one'], {writable: false})); await loading;
  assert.equal(h.controller.state.previewRetryGeneration, 2, 'local metadata write restrictions do not prevent online image recovery');
});

test('offline snapshots and failed refreshes do not authorize cover retries', async () => {
  const h = harness();
  let loading = h.controller.load({refresh: true}); h.pending[0].resolve(page(['one'])); await loading;
  loading = h.controller.load({refresh: true}); h.pending[1].reject(new Error('plugin offline')); await loading;
  assert.equal(h.controller.state.previewRetryGeneration, 1);
  assert.equal(h.controller.state.cached, true);
  loading = h.controller.load({refresh: true});
  h.pending[2].resolve(page(['one'], {offline: true, cached: true, writable: false, error: 'plugin offline'}));
  assert.equal(await loading, false);
  assert.equal(h.controller.state.previewRetryGeneration, 1);
});

test('obsolete refresh responses never grant another cover retry', async t => {
  for (const invalidation of ['cancel', 'query', 'model', 'newer response']) {
    await t.test(invalidation, async () => {
      const h = harness();
      let loading = h.controller.load({refresh: true}); h.pending[0].resolve(page(['one'])); await loading;
      const stale = h.controller.load({refresh: true});
      if (invalidation === 'cancel') h.controller.cancel();
      if (invalidation === 'query') h.controller.state.q = 'changed';
      if (invalidation === 'model') h.profile('krea2');
      if (invalidation === 'newer response') {
        loading = h.controller.load({refresh: true}); h.pending[2].resolve(page(['newer'])); await loading;
      }
      const generation = h.controller.state.previewRetryGeneration;
      h.pending[1].resolve(page(['stale'])); assert.equal(await stale, false);
      assert.equal(h.controller.state.previewRetryGeneration, generation);
    });
  }
});

test('a successful item edit updates the local offline cache rather than restoring superseded metadata', async () => {
  const h=harness();let loading=h.controller.load();h.pending[0].resolve(page(['one']));await loading;
  h.controller.replace({id:'one',filename:'one.safetensors',tags:['saved']});
  loading=h.controller.load();h.pending[1].reject(new Error('offline'));await loading;
  assert.deepEqual(h.controller.state.items[0].tags,['saved']);
});

test('gallery arrow navigation follows actual columns and clamps at page boundaries', () => {
  assert.equal(cardNavigationIndex(0,60,5,'ArrowRight'),1);
  assert.equal(cardNavigationIndex(6,60,5,'ArrowDown'),11);
  assert.equal(cardNavigationIndex(6,60,5,'ArrowUp'),1);
  assert.equal(cardNavigationIndex(0,60,5,'ArrowLeft'),0);
  assert.equal(cardNavigationIndex(59,60,5,'ArrowDown'),59);
  assert.equal(cardNavigationIndex(13,17,2,'ArrowDown'),15);
  assert.equal(cardNavigationIndex(3,17,1,'ArrowDown'),4);
  assert.equal(cardNavigationIndex(6,60,5,'Home'),0);
  assert.equal(cardNavigationIndex(6,60,5,'End'),59);
  assert.equal(cardNavigationIndex(6,60,5,'Enter'),6);
});

test('closing cancels all pending reads and their results cannot change the reopened library', async () => {
  const h = harness(); const old = h.controller.load(); const selected = h.controller.selectAll();
  h.controller.cancel();
  const reopened = h.controller.load(); h.pending[2].resolve(page(['fresh'])); await reopened;
  h.pending[0].resolve(page(['stale'])); h.pending[1].resolve({ ids: ['stale'] }); await Promise.all([old, selected]);
  assert.deepEqual(h.controller.state.items.map(item => item.id), ['fresh']);
  assert.equal(h.controller.state.selected.size, 0);
});

test('group navigation handles nested groups, collapse and malformed cycles without recursion loops', () => {
  const groups = [{ id: 'root', name: '收藏', parentId: null }, { id: 'child', name: '人物', parentId: 'root' },
    { id: 'leaf', name: 'Alice', parentId: 'child' }, { id: 'other', name: '画风', parentId: null }];
  assert.deepEqual(groupRows(groups).map(row => [row.id, row.depth]), [['root', 0], ['child', 1], ['leaf', 2], ['other', 0]]);
  assert.deepEqual(groupRows(groups, new Set(['child'])).map(row => row.id), ['root', 'child', 'other']);
  assert.equal(groupPath(groups[2], groups), '收藏 / 人物 / Alice');
  assert.deepEqual([...descendants(groups, 'child')], ['child', 'leaf']);
  const cycle = [{ id: 'a', name: 'A', parentId: 'b' }, { id: 'b', name: 'B', parentId: 'a' }];
  assert.equal(groupPath(cycle[0], cycle), 'B / A');
  assert.deepEqual([...descendants(cycle, 'a')], ['a', 'b']);
});

test('tag queries encode repeated values rather than joining tags ambiguously', () => {
  const params = new URLSearchParams(queryString({ tag: ['a,b', '风格'], tag_exclude: ['x y'], recursive: false, q: '' }));
  assert.deepEqual(params.getAll('tag'), ['a,b', '风格']);
  assert.deepEqual(params.getAll('tag_exclude'), ['x y']);
  assert.equal(params.get('recursive'), 'false');
  assert.equal(params.has('q'), false);
});

test('root-folder exact browsing preserves the empty folder parameter', () => {
  const params = new URLSearchParams(queryString({ root: 'F:/models/loras', folder: '', recursive: false }));
  assert.equal(params.has('folder'), true);
  assert.equal(params.get('folder'), '');
  assert.equal(new URLSearchParams(queryString({ root: '', folder: '', recursive: false })).has('folder'), false);
});

test('saving bindings only sends trusted editable fields and never invents a zero weight from an empty input', () => {
  assert.deepEqual(bindingPayload([{ id: 'one', filename: 'server-path', sha256: 'server-hash', trigger_words: ['server-word'], status: 'recognized', strength: '0.75', enabled: false }]), [{ id: 'one', strength: .75, enabled: false }]);
  for (const strength of ['', null, 'NaN', Infinity, -101, 101]) assert.throws(() => bindingPayload([{ id: 'one', strength }]), /绑定权重/);
  assert.deepEqual(bindingPayload([{ id: 'one', strength: 0 }]), [{ id: 'one', strength: 0, enabled: true }]);
  assert.deepEqual(bindingPayload([{ id: 'one', strength: 1, status: 'changed', sha256: 'old', rebind: true }]), [{ id: 'one', strength: 1, enabled: true, rebind: true }]);
  assert.equal('rebind' in bindingPayload([{ id: 'one', strength: 1, status: 'changed' }])[0], false);
});

test('saved detail values never cover later batch edits when the detail is reopened', () => {
  const before = { tags: ['角色绑定验收', 'ui-verified'], notes: 'note', default_strength: .8, groupIds: ['group'], trigger_words: ['alice'] };
  const clean = mergeDetailDraft(before);
  assert.equal(Object.values(detailDirtyFields(before, clean)).some(Boolean), false);
  const after = { ...before, tags: [...before.tags, 'batch-ui-verified'] };
  assert.equal(mergeDetailDraft(after).tags, '角色绑定验收, ui-verified, batch-ui-verified');
  // A genuine unsaved note is preserved, while untouched tags still follow the server.
  const edited = { ...clean, notes: 'unfinished note' };
  edited.dirty = detailDirtyFields(before, edited);
  const restored = mergeDetailDraft(after, edited);
  assert.equal(restored.notes, 'unfinished note');
  assert.equal(restored.tags, '角色绑定验收, ui-verified, batch-ui-verified');
  assert.deepEqual(metadataPatch(after, restored), { notes: 'unfinished note' });
});

test('saving one metadata field does not overwrite stale untouched fields or resurrect dormant favorite groups', () => {
  const item = { tags: ['old'], notes: '', default_strength: 1, groupIds: ['remembered'], favorite: false, trigger_words: [] };
  const edit = { ...mergeDetailDraft(item), notes: 'new note' };
  assert.deepEqual(metadataPatch(item, edit), { notes: 'new note' });
  assert.deepEqual(metadataPatch(item, mergeDetailDraft(item)), {});
});

test('manager additions reach the real host adapter with default weight, classification and trigger metadata intact', async () => {
  const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
  const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
  const start = source.indexOf('function addManagedLoras(');
  const inventory = new Map(), received = [];
  const context = vm.createContext({ draft: { loras: [] }, currentProfile: () => 'anima',
    loraItem: filename => inventory.get(filename), replaceLoraInventoryItem: item => inventory.set(item.filename,item),
    normalizedPath: value => value.toLowerCase().replaceAll('\\','/'), renderLoras() {}, schedulePersist() {}, reconcileLoraTriggers() {},
  });
  vm.runInContext(source.slice(start, source.indexOf('\n}',start)+2),context);
  const item = {id:'new',filename:'newly-scanned.safetensors',model_profile:'anima',classification_status:'recognized',default_strength:.35,trigger_words:['alice'],available:true};
  const result = await submitLibraryLoras([item], values => { received.push(...values); return context.addManagedLoras(values); });
  assert.equal(result.added.length,1);
  assert.equal(result.added[0].strength,.35);
  assert.equal(received[0].model_profile,'anima');
  assert.equal(received[0].classification_status,'recognized');
  assert.deepEqual(received[0].trigger_words,['alice']);
  assert.notEqual(received[0],item,'host mutations must not modify library cache objects');
});
