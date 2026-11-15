const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../static/character-classification.js'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
function fixture() {
  const ctx = vm.createContext({}); vm.runInContext(code, ctx); return ctx.CharacterClassification;
}
function analyzed(prompt, parts, noTrigger = false) {
  return { prompt, character_parts: parts.map(([text, role, source = 'dictionary']) => ({text, role, source})), character_no_trigger: noTrigger,
    character_status: parts.every(part => part[1] !== 'unknown') && (noTrigger || parts.some(part => part[1] === 'trigger')) ? 'ready' : 'needs_review',
    previews: {trigger_tags: prompt, trigger: null, trigger_tags_no_clothing: null} };
}
const initial = () => analyzed('blue eyes, hero, series, white shirt', [['blue eyes','feature'],['hero','trigger'],['series','trigger'],['white shirt','feature']]);

test('readiness requires complete roles and explicit confirmation for no trigger', () => {
  const api = fixture();
  assert.equal(api.isReady(initial()), true);
  assert.equal(api.isReady(analyzed('whale girl', [['whale girl','unknown']])), false);
  assert.equal(api.isReady(analyzed('blue eyes', [['blue eyes','feature']])), false);
  assert.equal(api.isReady(analyzed('blue eyes', [['blue eyes','feature']], true)), true);
  assert.equal(api.isReady({...analyzed('whale girl', [['whale girl','unknown']]), character_status:'ready'}), false);
});

test('manual correction sends labels in their original order without rewriting text', async () => {
  const api = fixture(), requests = [], snapshots = [];
  const input = {prompt:initial().prompt, title:'Hero'};
  const controller = api.createController({getInput:()=>input, initial:initial(), render(){},
    analyze: async body => { requests.push(plain(body)); return {...body, previews:initial().previews}; },
    onChange:(value,state)=>snapshots.push({value:plain(value),state:plain(state)}),
  });
  controller.actions.role(0,'trigger'); await controller.ensure();
  assert.deepEqual(requests[0].character_parts.map(part=>part.text), ['blue eyes','hero','series','white shirt']);
  assert.equal(requests[0].character_parts[0].source,'manual');
  assert.equal(requests[0].character_parts[0].role,'trigger');
  assert.equal(requests[0].prompt,input.prompt);
  assert.equal(snapshots.at(-1).state.pending,false);
});

test('text edits use previous results for reconciliation and never attach old parts to new text', async () => {
  const api = fixture(), requests = [];
  let input = {prompt: initial().prompt, title:'Hero'};
  const controller = api.createController({getInput:()=>input, initial:initial(), render(){}, analyze:async body=>{
    requests.push(plain(body)); return analyzed(body.prompt,[['hero','trigger'],['red eyes','unknown']]);
  }});
  input = {...input,prompt:'hero, red eyes'}; controller.invalidate(); await controller.ensure();
  assert.equal(requests[0].prompt,'hero, red eyes');
  assert.equal(requests[0].character_parts,undefined);
  assert.equal(requests[0].previous.prompt,initial().prompt);
  assert.equal(requests[0].reanalyze,true);
});

test('metadata edits retain classification and explicit reanalysis retains no-trigger confirmation', async () => {
  const api=fixture(), requests=[];
  let input={prompt:'blue eyes',title:'Old title'};
  const original=analyzed('blue eyes',[['blue eyes','feature','manual']],true);
  const controller=api.createController({getInput:()=>input,initial:original,render(){},analyze:async body=>{
    requests.push(plain(body));return {...original,...body,previews:original.previews};
  }});
  input={...input,title:'New title'};controller.invalidate();await controller.ensure();
  assert.deepEqual(requests[0].character_parts,original.character_parts);
  assert.equal(requests[0].reanalyze,undefined);
  await controller.refresh(true);
  assert.equal(requests[1].previous.prompt,'blue eyes');
  assert.equal(requests[1].character_no_trigger,true);
});

test('late analysis cannot overwrite a newer input or closed editor', async () => {
  const api = fixture(), first=deferred(), second=deferred(); let input={prompt:'old'}, alive=true;
  const controller=api.createController({getInput:()=>input,current:()=>alive,render(){},analyze:body=>body.prompt==='old'?first.promise:second.promise});
  const old=controller.refresh(); input={prompt:'new'}; controller.invalidate(); const next=controller.refresh();
  second.resolve(analyzed('new',[['new','trigger']])); await next;
  first.resolve(analyzed('old',[['old','trigger']])); assert.equal(await old,null);
  assert.equal(controller.value.prompt,'new');
  const third=deferred(); input={prompt:'closed'};
  const closing=api.createController({getInput:()=>input,current:()=>alive,render(){},analyze:()=>third.promise});
  const result=closing.refresh(); alive=false; closing.dispose(); third.resolve(analyzed('closed',[['closed','trigger']]));
  assert.equal(await result,null);
  assert.equal(closing.value,null);
});

test('failed analysis stays blocked until a successful retry', async () => {
  const api=fixture(); let fail=true;
  const controller=api.createController({getInput:()=>({prompt:'hero'}),render(){},analyze:async()=>{
    if(fail)throw new Error('offline'); return analyzed('hero',[['hero','trigger']]);
  }});
  await assert.rejects(controller.ensure(),/offline/);
  fail=false; assert.equal((await controller.ensure()).prompt,'hero');
});

test('no-trigger confirmation is explicit and adding a trigger clears it', async () => {
  const api=fixture(), sent=[];
  const original=analyzed('blue eyes',[['blue eyes','feature']]);
  const controller=api.createController({getInput:()=>({prompt:'blue eyes'}),initial:original,render(){},analyze:async body=>{
    sent.push(plain(body)); return {...body,previews:{trigger_tags:body.prompt,trigger:'',trigger_tags_no_clothing:body.prompt}};
  }});
  controller.actions.noTrigger(true); await controller.ensure(); assert.equal(sent[0].character_no_trigger,true);
  controller.actions.role(0,'trigger'); await controller.ensure(); assert.equal(sent[1].character_no_trigger,false);
});

test('unknown previews stay visibly unavailable and tag text is escaped', () => {
  const api=fixture(), root={classList:{add(){}},querySelector:()=>({addEventListener(){}}),querySelectorAll:()=>[]};
  api.render(root,{value:analyzed('<img src=x>', [['<img src=x>','unknown']]),busy:false,dirty:false,error:''},{refresh(){},role(){},noTrigger(){}});
  assert.match(root.innerHTML,/&lt;img src=x&gt;/);
  assert.doesNotMatch(root.innerHTML,/<img src=x>/);
  assert.match(root.innerHTML,/完成分类确认后显示/);
});

test('import submit availability excludes pending classification but permits explicitly skipped rows', () => {
  const ctx=vm.createContext({CharacterClassification:fixture(),importPreview:null,ui:{commitImport:{disabled:false},importBundleName:{value:'My roles'}}});
  for(const name of ['importCharacterReady','updateImportCommitState']) {
    const start=app.indexOf(`function ${name}(`); vm.runInContext(app.slice(start,app.indexOf('\n}',start)+2),ctx);
  }
  ctx.importPreview={saving:false,rows:[{status:'new',action:'create',item:{section:'character',...initial()}}]};
  ctx.updateImportCommitState(); assert.equal(ctx.ui.commitImport.disabled,false);
  ctx.importPreview.rows[0]._classificationPending=true;
  ctx.updateImportCommitState(); assert.equal(ctx.ui.commitImport.disabled,true);
  ctx.importPreview.rows[0].action='skip';
  ctx.importPreview.rows.push({status:'new',action:'create',item:{section:'clothing',prompt:'white shirt'}});
  ctx.updateImportCommitState(); assert.equal(ctx.ui.commitImport.disabled,false);
});

test('rebuilding import rows during analysis preserves the manual draft', async () => {
  const api=fixture(), oldRead=deferred(), sent=[];
  const ctx=vm.createContext({});
  const start=app.indexOf('function updateImportCharacterDraft(');
  vm.runInContext(app.slice(start,app.indexOf('\n}',start)+2),ctx);
  const row={item:analyzed('hero, odd tag',[['hero','trigger'],['odd tag','unknown']])};
  const old=api.createController({initial:row.item,getInput:()=>row.item,render(){},analyze:()=>oldRead.promise,
    onChange:(value,state)=>ctx.updateImportCharacterDraft(row,value,state)});
  old.actions.role(1,'feature');
  assert.equal(row.item.character_parts[1].role,'feature');
  assert.equal(row.item.character_parts[1].source,'manual');
  assert.equal(row._classificationPending,true);
  old.dispose();
  const replacement=api.createController({initial:row.item,getInput:()=>row.item,render(){},analyze:async body=>{
    sent.push(plain(body));return {...body,previews:{trigger_tags:body.prompt,trigger:'hero',trigger_tags_no_clothing:body.prompt}};
  },onChange:(value,state)=>ctx.updateImportCharacterDraft(row,value,state)});
  await replacement.refresh();
  oldRead.resolve(analyzed('hero, odd tag',[['hero','trigger'],['odd tag','unknown']]));
  await Promise.resolve();
  assert.equal(sent[0].character_parts[1].source,'manual');
  assert.equal(row.item.character_parts[1].role,'feature');
  assert.equal(row._classificationPending,false);
});

test('saving a character preserves raw text and captures the form after analysis finishes', async () => {
  const api=fixture(), ready=deferred(), saved=[];
  const value=key=>({value:key});
  const ui={customPrompt:value('whale_girl'),customTitle:value('Old title'),customSubtitle:value(''),customGender:value('unknown'),customHair:value(''),customEye:value(''),customSeries:value(''),customCategory:value(''),customTraits:value(''),customGroupChecks:{querySelectorAll:()=>[]},customDialog:{}};
  let normalized=0;
  const controller={ensure:()=>ready.promise};
  const ctx=vm.createContext({ui,CharacterClassification:api,customWriteBusy:false,activeSection:'character',customEditorSession:1,editingCustom:{id:'custom:hero'},customCharacterController:controller,
    currentProfile:()=> 'anima',normalizePromptFields:async()=>{normalized++;},selectionHas:()=>true,motionDialog(){},toast(){},
    request:async (url,options)=>{saved.push(JSON.parse(options.body));return {id:'custom:hero'};}});
  const start=app.indexOf('async function saveCustom(');vm.runInContext(app.slice(start,app.indexOf('\n}',start)+2),ctx);
  const saving=ctx.saveCustom({preventDefault(){}});
  ui.customTitle.value='Latest title';
  ready.resolve(analyzed('whale_girl',[['whale_girl','trigger','manual']]));await saving;
  assert.equal(normalized,0);
  assert.equal(saved[0].title,'Latest title');
  assert.equal(saved[0].prompt,'whale_girl');
  assert.equal(saved[0].character_parts[0].text,'whale_girl');
});
