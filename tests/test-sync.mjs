import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const html=fs.readFileSync(new URL('../scrolls.html',import.meta.url),'utf8');
const plain=value=>JSON.parse(JSON.stringify(value));
const sample={extension:{keep:true},_meta:{gods:{}},verses:[{id:'TEST-01',name:'Original'}]};
function section(start,end){
  const a=html.indexOf(start),b=html.indexOf(end,a);
  assert(a>=0&&b>a); return html.slice(a,b);
}
function harness(storage=new Map()){
  const controls={},requests=[],downloads=[],alerts=[];
  const ctx=vm.createContext({
    localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
    location:{protocol:'http:',origin:'http://nas.test'},
    qs:id=>controls[id] ||= {value:'',style:{},textContent:''},
    alert:s=>alerts.push(s),confirm:()=>false,download:(...args)=>downloads.push(args),
    refreshEnums(){},renderGods(){},render(){},updateOvNote(){},openModal(){},
    DATA:plain(sample),META:plain(sample._meta),STAT:{},GODS:{},DISK_GODS:{},VERSES:[],
    LS_KEY:'dr_verse_overrides_v1',RUNTIME_SCHEMA_VERSION:1,
    fetch:async(url,options)=>{requests.push({url,...options}); return ctx.respond(url,options);}
  });
  vm.runInContext(section('  // ---- localStorage overrides:', '  // state'),ctx);
  vm.runInContext('applyOverrides(); applyGodOverrides();',ctx);
  vm.runInContext(section('  function syncedMeta(){','  function exportVersesJs()'),ctx);
  vm.runInContext(section('  var syncTokens={};','  function ghHeaders('),ctx);
  vm.runInContext(section('  function ghApplyData(','  function ghLoad('),ctx);
  vm.runInContext(section('  // ===== NAS backend:', '  function resetOverrides()'),ctx);
  return {ctx,controls,requests,downloads,alerts,storage};
}
const response=(status,data=sample,etag='"r1"')=>({status,ok:status>=200&&status<300,headers:{get:()=>etag},json:async()=>plain(data)});
function edit(h,name='Local'){
  h.ctx.OV.upserts['TEST-01']={id:'TEST-01',name};
  h.ctx.applyOverrides();h.ctx.saveOverrides(h.ctx.OV);
}

// Exact loaded revision accompanies the outgoing snapshot; extensions survive.
{
  const h=harness();h.ctx.respond=()=>response(200);
  assert.equal(await h.ctx.nasLoad(false),true);edit(h);
  h.ctx.respond=()=>response(200,{},'"r2"');
  assert.equal(await h.ctx.nasSave(),true);
  const put=h.requests.at(-1);assert.equal(put.headers['If-Match'],'"r1"');
  assert.deepEqual(JSON.parse(put.body).extension,{keep:true});
  assert.equal(h.ctx.hasOverrides(),0);assert.equal(h.ctx.NAS_BASELINE.etag,'"r2"');
}
// Conflict survives reload. Auto-load cannot silently rebase local changes.
{
  const h=harness();h.ctx.respond=()=>response(200);await h.ctx.nasLoad(false);edit(h);
  h.ctx.respond=()=>response(412);
  assert.equal(await h.ctx.nasSave(),false);assert.equal(h.ctx.VERSES[0].name,'Local');
  const reloaded=harness(h.storage);
  reloaded.ctx.respond=()=>response(200,{...sample,verses:[{id:'TEST-01',name:'Remote'}]},'"r2"');
  assert.equal(await reloaded.ctx.nasLoad(true),false);
  assert.equal(reloaded.ctx.VERSES[0].name,'Local');assert.equal(reloaded.ctx.NAS_BASELINE.etag,'"r1"');
  assert.equal(await reloaded.ctx.nasLoad(false),false);assert.equal(reloaded.downloads.length,0);
  reloaded.ctx.confirm=()=>true;
  assert.equal(await reloaded.ctx.nasLoad(false),true);
  assert.equal(JSON.parse(reloaded.downloads[0][1]).verses[0].name,'Local');
  assert.equal(reloaded.ctx.VERSES[0].name,'Remote');assert.equal(reloaded.ctx.hasOverrides(),0);
}
// Legacy pending changes cannot overwrite a catalog just by fetching a new ETag.
{
  const h=harness();edit(h);h.ctx.respond=()=>response(200);
  assert.equal(await h.ctx.nasSave(),false);assert(h.requests.every(r=>r.method!=='PUT'));
  assert.equal(h.ctx.VERSES[0].name,'Local');
}
// Create-only bootstrap, duplicate saves, and in-flight local mutations.
{
  const h=harness();h.ctx.respond=()=>response(404);
  let release;
  h.ctx.respond=(_url,o)=>o.method==='PUT'?new Promise(r=>release=r):response(404);
  const pending=h.ctx.nasSave();await new Promise(r=>setImmediate(r));
  assert.equal(await h.ctx.nasSave(),false);
  assert.equal(h.requests.at(-1).headers['If-None-Match'],'*');
  edit(h,'Later');release(response(200,{},'"r2"'));
  assert.equal(await pending,true);assert.equal(h.ctx.VERSES[0].name,'Later');assert(h.ctx.hasOverrides());
}
// Changed destination and servers without revision support fail closed.
{
  const h=harness();h.ctx.respond=()=>response(200);await h.ctx.nasLoad(false);edit(h);
  h.ctx.nasStore({base:'http://other.test',token:''});
  assert.equal(await h.ctx.nasSave(),false);assert(h.requests.every(r=>r.method!=='PUT'));
  h.ctx.respond=()=>response(200,sample,null);assert.equal(await h.ctx.nasLoad(false),false);
}
// Tokens migrate out of persistent storage and remain available in page memory.
{
  const storage=new Map([['dr_nas_cfg_v1',JSON.stringify({base:'http://nas.test',token:'test-token'})]]);
  const h=harness(storage);assert.equal(h.ctx.nasCfg().token,'test-token');
  assert(!storage.get('dr_nas_cfg_v1').includes('test-token'));
  h.ctx.nasStore({base:'http://nas.test',token:'new-test-token'});
  assert(!storage.get('dr_nas_cfg_v1').includes('token'));assert.equal(h.ctx.nasHeaders()['X-Auth-Token'],'new-test-token');
  assert.equal(harness(storage).ctx.nasCfg().token,'');
}
// Hidden God fields and nested rule extensions survive an ordinary form save.
{
  const h=harness();h.ctx.editingGod='Sample';h.ctx.gTraitsSel=['Kindness'];
  h.ctx.GODS.Sample={custom:{keep:true},rule:{hidden:42}};
  for(const name of ['gName','gFaction','gAccent','gIdentity','gAnchor','gTagline','gRuleName','gRuleText'])h.ctx.qs(name).value='Sample';
  vm.runInContext(section('  function collectGod(){','  function validateGod('),h.ctx);
  const god=plain(h.ctx.collectGod());assert.deepEqual(god.custom,{keep:true});assert.equal(god.rule.hidden,42);
}
// Mutation entry points reject edits during a sync rather than losing a delete
// of a newly-created record while the outgoing snapshot is being acknowledged.
{
  const h=harness();h.ctx.NAS_BUSY=true;
  for(const [name,end] of [['saveVerse','deleteVerse'],['deleteVerse','refreshEnums'],['saveGod','deleteGod'],['deleteGod',null]]){
    const marker='  function '+name+'(){';
    const start=html.indexOf(marker),next=html.indexOf('\n  function ',start+marker.length);
    const code=html.slice(start,next);
    vm.runInContext(code,h.ctx);h.ctx[name]();
  }
  assert.equal(h.alerts.length,4);
}
for(const script of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(script[1]);
console.log('PASS: revision saves, conflicts/reload, backup recovery, legacy drafts, create-only bootstrap, destination changes, in-flight edits, token migration, God extensions, and script syntax.');
