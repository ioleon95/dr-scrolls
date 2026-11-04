import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

// Minimal form DOM for the actual editor functions. No page startup or network.
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.style={};this.events={};this.type='';this.className='';this._value='';}
  set value(v){this._value=String(v);} get value(){return this._value;}
  set innerHTML(v){assert.equal(v,'');this.children.forEach(c=>c.parentNode=null);this.children=[];}
  setAttribute(k,v){this.attrs[k]=String(v);} getAttribute(k){return this.attrs[k];}
  appendChild(child){if(child.parentNode) child.parentNode.removeChild(child);child.parentNode=this;this.children.push(child);return child;}
  removeChild(child){this.children.splice(this.children.indexOf(child),1);child.parentNode=null;}
  replaceChild(child,old){if(child.parentNode) child.parentNode.removeChild(child);const i=this.children.indexOf(old);assert(i>=0);this.children[i]=child;child.parentNode=this;old.parentNode=null;}
  get lastChild(){return this.children.at(-1);}
  addEventListener(name,callback){this.events[name]=callback;}
  querySelectorAll(selector){
    const match=e=>selector==='[data-field]'?!!e.attrs['data-field']:e.className.split(' ').includes('runtime-row');
    return this.children.flatMap(c=>[...(match(c)?[c]:[]),...c.querySelectorAll(selector)]);
  }
}
const html=fs.readFileSync(new URL('../scrolls.html',import.meta.url),'utf8');
const hosts=Object.fromEntries(['mRuntimeMods','mRuntimeConds','mRuntimeKws','mStatPolicies','mEnginePolicies'].map(id=>[id,new Element('div')]));
const context=vm.createContext({document:{createElement:tag=>new Element(tag)},qs:id=>hosts[id]});
for(const name of ['STAT_KEYS','RUNTIME_OPS','RUNTIME_MODES','RUNTIME_CONDITIONS','RUNTIME_COUNTERS','RUNTIME_ACTIONS','RUNTIME_HOOKS']){
  const match=html.match(new RegExp('  var '+name+' = (\\[[\\s\\S]*?\\]);'));
  assert(match,name);vm.runInContext(`var ${name}=${match[1]};`,context);
}
const start=html.indexOf('  // Keep the complete authored record;'),end=html.indexOf('  function populateGodSelect(',start);
assert(start>=0&&end>start);vm.runInContext(html.slice(start,end),context);
const plain=v=>JSON.parse(JSON.stringify(v));
const catalog=JSON.parse(fs.readFileSync(process.argv[2] || new URL('runtime-fixture.json',import.meta.url),'utf8'));
for(const verse of catalog.verses){
  context.setRuntimeEditor(verse.runtime);
  assert.deepEqual(plain(context.readRuntime()),verse.runtime,verse.id+' runtime changed on untouched form save');
  assert.deepEqual(plain(context.validateRuntime(context.readRuntime())),[],verse.id+' structural validation');
}
context.setRuntimeEditor(null);
context.addRuntimeModifierRow();context.addRuntimeConditionalRow();context.addRuntimeKeywordRow();
assert.deepEqual(plain(context.readRuntime()),{
  modifiers:[{stat:'ATK',op:'Additive',value:1}],
  conditionalModifiers:[{mode:'condition',stat:'ATK',op:'Additive',value:1,condition:'GospelFull',intParam:0}],
  keywords:[{action:'Sanctify',hook:'on-pick',intParam:0,floatParam:0}],statPolicies:[],enginePolicies:[]
});
const mode=hosts.mRuntimeConds.children[0].children[0];mode.value='counterScaled';mode.events.change();
assert.deepEqual(plain(context.readRuntime()).conditionalModifiers,[{mode:'counterScaled',stat:'ATK',op:'Additive',counter:'Devour',perCounter:1}]);
const original={modifiers:[{stat:'ATK',op:'Additive',value:2,points:9}],conditionalModifiers:[],
  keywords:[{action:'CounterAdd',hook:'on-structure-fall',intParam:1,floatParam:0,counter:'Devour',extension:{nested:true}}],
  statPolicies:[{nested:{keep:true}}],enginePolicies:[],extension:{keep:true}};
context.setRuntimeEditor(original);
const number=hosts.mRuntimeMods.children[0].children.find(e=>e.attrs['data-field']==='value');number.value='3';
const changed=plain(context.readRuntime());const expected=structuredClone(original);expected.modifiers[0].value=3;
assert.deepEqual(changed,expected);assert.equal(original.modifiers[0].value,2,'Source object mutated');
number.value='';assert.throws(()=>context.readRuntime(),/finite numbers/);
context.setRuntimeEditor(original);hosts.mStatPolicies.value='{';assert.throws(()=>context.readRuntime());
context.setRuntimeEditor(original);hosts.mStatPolicies.value='null';assert(context.validateRuntime(context.readRuntime()).length>0);
context.setRuntimeEditor(catalog.verses.find(v=>v.runtime.conditionalModifiers.some(c=>c.mode==='eventScaled')).runtime);
const advanced=hosts.mRuntimeConds.children.find(e=>e._runtimeJson);assert(advanced);advanced._runtimeJson.value='[]';assert.throws(()=>context.readRuntime(),/JSON object/);
for(const script of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(script[1]);
console.log('PASS: complete catalog runtime round trips; hidden fields, policies, advanced modes, targeted edits, new rows, mode switch, invalid numeric/JSON input, and page syntax.');

