// No dependencies: exercise recipe rendering with a minimal DOM and mocked API.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.style={}; this.value=''; }
  set innerHTML(v) { throw new Error('Unsafe innerHTML sink'); }
  append(el) { this.children.push(el); }
  replaceChildren() { this.children=[]; }
  scrollIntoView() {}
}
const elements = new Map();
const $ = s => { if (!elements.has(s)) elements.set(s,new Element('div')); return elements.get(s); };
const hostile = '<img src=x onerror=alert(1)>';
const recipe = {id:1,name:hostile,source_url:'javascript:alert(1)',minutes:30,servings:4,protein_per_serving:32,tags:[hostile],ingredients:[{raw:hostile}],metadata:{description:hostile,images:['javascript:alert(1)'],authors:[{name:hostile}],rating:{value:4,best:5,count:3},instructions:[{type:'section',name:hostile,steps:[{type:'step',text:hostile}]}],reviews:[{reviewBody:hostile}]}};
const context=vm.createContext({console,URL,URLSearchParams,document:{createElement:t=>new Element(t)},$,toast:msg=>{throw new Error(msg)},api:async path=>path.includes('browse')?{items:[recipe],total:1,offset:0}:recipe});
vm.runInContext(fs.readFileSync('static/recipes.js','utf8'),context);
(async()=>{
  assert.equal(vm.runInContext("safeURL('javascript:alert(1)')",context),'');
  assert.equal(vm.runInContext("safeURL('data:text/html,x')",context),'');
  assert.equal(vm.runInContext("safeURL('//example.com/img.jpg')",context),'https://example.com/img.jpg');
  await context.loadRecipes(); await context.recipeDetail(1);
  const all=[]; function walk(e){all.push(e);e.children.forEach(walk);} walk($('#recipecards'));walk($('#recipe-detail'));
  assert(all.some(e=>e.textContent===hostile));
  assert(!all.some(e=>e.src?.startsWith('javascript:') || e.href?.startsWith('javascript:')));
  assert(all.some(e=>e.tag==='ol'));assert(all.some(e=>e.textContent==='Embedded source reviews'));
  console.log('Recipe UI rendering and hostile-content checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
