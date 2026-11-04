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
const recipe = {id:1,name:hostile,source_url:'javascript:alert(1)',minutes:30,servings:4,protein_per_serving:32,tags:[hostile],ingredients:[{raw:hostile}],metadata:{description:hostile,images:['javascript:alert(1)'],authors:[{name:hostile}],rating:{value:4,best:5,count:3},times:{prepTime:'PT5M',cookTime:'PT35M',totalTime:'PT1H10M'},nutrition:{'@type':'NutritionInformation',calories:'627',proteinContent:'22.4 grams',sodiumContent:'646.7 milligrams'},nutrition_provenance:{basis:'source supplied; not independently verified',protein_field:'nutrition.proteinContent'},dates:{datePublished:'2025-09-20T00:00:00Z',dateModified:'2026-01-01T00:00:00Z'},reviews:[{reviewBody:hostile,author:{name:'CookPerson'},datePublished:'2025-10-05'}],instructions:[{type:'section',name:hostile,steps:[{type:'step',text:hostile}]}]}};
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
  assert(all.some(e=>e.textContent==='Total: 1 h 10 min'));
  assert(all.some(e=>e.textContent==='Protein: 22.4 g')&&all.some(e=>e.textContent==='Sodium: 646.7 mg')&&all.some(e=>e.textContent==='Calories: 627'));
  assert(all.some(e=>String(e.textContent||'').startsWith('CookPerson (2025-10-05): <img')));
  assert(all.some(e=>e.textContent==='Published: 2025-09-20'));
  assert(!all.some(e=>String(e.textContent||'').includes('"@type"')));
  console.log('Recipe UI rendering and hostile-content checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
