'use strict';
let recipeOffset = 0, importPreview = null;
const safeURL = value => {
  try { const u = new URL(String(value).startsWith('//') ? 'https:' + value : value); return ['http:', 'https:'].includes(u.protocol) && !u.username && !u.password ? u.href : ''; } catch { return ''; }
};
function node(tag, value, parent, cls) {
  const el = document.createElement(tag); if (value != null) el.textContent = String(value);
  if (cls) el.className = cls; if (parent) parent.append(el); return el;
}
function sourceHeader(r, parent) {
  const m = r.metadata || {}, p = r.preference || {};
  node('h3', r.name, parent);
  const img = (m.images || [])[0];
  const url = safeURL(typeof img === 'object' && img ? img.url || img.contentUrl : img);
  if (url) { const i = node('img', null, parent); i.src = url; i.alt = r.name; i.loading = 'lazy'; i.referrerPolicy = 'no-referrer'; i.style.cssText='width:100%;max-height:220px;object-fit:cover;border-radius:8px'; }
  const source = safeURL(r.source_url);
  if (source) { const a = node('a', m.source_name || 'Original recipe', parent); a.href = source; a.target = '_blank'; a.rel = 'noopener noreferrer'; }
  const rating = m.rating || {};
  node('p', rating.value == null ? 'Source rating: not supplied' : `★ ${rating.value}/${rating.best ?? 5} · ${rating.count ?? rating.review_count ?? 'unknown'} ratings (source)`, parent);
  node('p', `Family feedback: 👍 ${p.ups || 0} / 👎 ${p.downs || 0}`, parent, 'muted');
  node('p', (m.authors || []).map(a => typeof a === 'object' && a ? a.name : a).filter(Boolean).join(', '), parent);
  node('p', m.description || '', parent);
  node('p', `${r.minutes || '?'} min · serves ${r.servings} · ${r.protein_per_serving}g protein/serving (source or estimate)`, parent, 'muted');
  const badges = node('div', null, parent, 'badges');
  for (const tag of (r.tags || []).slice(0, 40)) node('span', tag, badges, 'badge');
}
async function loadRecipes() {
  const params = new URLSearchParams({offset: recipeOffset, limit: 24, q: $('#recipe-search').value, source: $('#recipe-source').value, tag: $('#recipe-tag').value, category: $('#recipe-category').value, sort: $('#recipe-sort').value});
  try {
    const data = await api('/api/recipes/browse?' + params);
    const grid = $('#recipecards'); grid.replaceChildren();
    for (const r of data.items) {
      const card = node('article', null, grid, 'card'); sourceHeader(r, card);
      const button = node('button', 'Full recipe', card, 'btn'); button.onclick = () => recipeDetail(r.id);
    }
    $('#recipe-page').textContent = `${data.total ? data.offset + 1 : 0}–${Math.min(data.total,data.offset+data.items.length)} of ${data.total}`;
    $('#recipe-next').disabled = data.offset + data.items.length >= data.total;
  } catch(e) { toast(e.message); }
}
function renderSteps(steps, parent) {
  const ol = node('ol', null, parent);
  for (const step of steps || []) {
    const li = node('li', null, ol);
    if (step.type === 'section') { node('h4', step.name, li); renderSteps(step.steps, li); }
    else node('p', [step.name, step.text].filter(Boolean).join(': '), li);
  }
}
function isoMins(s) {
  const t = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$/.exec(String(s || ''));
  if (!t || !(t[1] || t[2] || t[3])) return String(s || '');
  const m = (+t[1] || 0) * 1440 + (+t[2] || 0) * 60 + (+t[3] || 0);
  return m >= 60 ? `${Math.floor(m / 60)} h${m % 60 ? ' ' + (m % 60) + ' min' : ''}` : `${m} min`;
}
const NUTR_LABELS = [['calories','Calories'],['carbohydrateContent','Carbs'],['proteinContent','Protein'],['fatContent','Fat'],['saturatedFatContent','Saturated fat'],['unsaturatedFatContent','Unsaturated fat'],['transFatContent','Trans fat'],['cholesterolContent','Cholesterol'],['sodiumContent','Sodium'],['fiberContent','Fiber'],['sugarContent','Sugar']];
const tidy = v => {
  const s = String(v).replace(/\bgrams?\b/g, 'g').replace(/\bmilligrams?\b/g, 'mg').replace(/\bkilograms?\b/g, 'kg').replace(/\bmilliliters?\b/g, 'ml');
  if (/^-?\d+(?:\.\d{2,})$/.test(s)) return String(Math.round(parseFloat(s) * 10) / 10);
  return s;
};
function metaDetails(title, parent, rows) {
  rows = (rows || []).filter(r => r && r[1] != null && String(r[1]).trim() !== '');
  if (!rows.length) return;
  const d = node('details', null, parent); node('summary', title, d);
  for (const [label, value] of rows) node('p', label ? `${label}: ${value}` : String(value), d);
}
async function recipeDetail(id) {
  try {
    const r = await api('/api/recipes/' + id), m = r.metadata || {}, panel = $('#recipe-detail');
    panel.replaceChildren(); panel.style.display = '';
    const close = node('button', 'Close detail', panel, 'btn'); close.onclick = () => panel.style.display = 'none';
    sourceHeader(r, panel);
    node('h4', 'Ingredients', panel);
    if ((m.ingredient_sections || []).length) {
      for (const section of m.ingredient_sections) { node('h5', section.name, panel); const ul=node('ul',null,panel); for(const ing of section.ingredients) { const li=node('li',ing.raw,ul); if(ing.allergy_note) node('span',' ⚠ '+ing.allergy_note, li, 'muted'); } }
    } else { const ul = node('ul', null, panel); for (const i of r.ingredients) { const li = node('li', i.raw || `${i.qty || 'some'} ${i.unit} ${i.name}`, ul); if (i.allergy_note) node('span', ' ⚠ ' + i.allergy_note, li, 'muted'); } }
    node('h4', 'Instructions', panel);
    if (m.instructions?.length) renderSteps(m.instructions, panel); else node('pre', r.steps, panel).style.whiteSpace='pre-wrap';
    if (m.yield_text) node('p', `Yield: ${m.yield_text}`, panel, 'muted');
    metaDetails('Times', panel, m.times && [['Prep', isoMins(m.times.prepTime)], ['Cook', isoMins(m.times.cookTime)], ['Total', isoMins(m.times.totalTime)]]);
    const n = m.nutrition || {};
    const known = new Set(NUTR_LABELS.map(([k]) => k));
    metaDetails('Nutrition (source supplied)', panel, NUTR_LABELS.filter(([k]) => n[k] != null).map(([k, label]) => [label, tidy(n[k])])
      .concat(Object.keys(n).filter(k => !known.has(k) && k !== '@type' && n[k] != null).map(k => [k, tidy(n[k])])));
    const pr = m.nutrition_provenance || {};
    metaDetails('Nutrition provenance', panel, [['Basis', pr.basis], ['Protein from', pr.protein_field]]);
    metaDetails('Embedded source reviews', panel, (m.reviews || []).map(v => ['', [(v && v.author && (v.author.name || v.author)) || 'reader',
      v && v.datePublished ? ` (${String(v.datePublished).slice(0, 10)})` : '',
      v && v.reviewBody ? ': ' + v.reviewBody : ''].join('')]));
    metaDetails('Videos', panel, (m.video || []).map(v => ['', [v && v.name, v && v.uploadDate ? `(${String(v.uploadDate).slice(0, 10)})` : ''].filter(Boolean).join(' ')]));
    metaDetails('Tips', panel, (Array.isArray(m.tips) ? m.tips : [m.tips]).filter(Boolean).map(t => ['', typeof t === 'string' ? t : JSON.stringify(t)]));
    metaDetails('Dates', panel, m.dates && [['Published', String(m.dates.datePublished || '').slice(0, 10)], ['Modified', String(m.dates.dateModified || '').slice(0, 10)]]);
    panel.scrollIntoView({behavior:'smooth'});
  } catch(e) { toast(e.message); }
}
async function previewImport() {
  importPreview = null; $('#import-save').disabled = true;
  try {
    const content = $('#import-content').value;
    const payload = {url: $('#import-url').value, preview: true}; if (content.trim()) payload.content = content;
    const data = await api('/api/recipes/import', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    importPreview = {url: data.recipe.source_url, content: data.recipe.metadata.raw_jsonld, preview:false};
    $('#import-status').textContent = JSON.stringify({name:data.recipe.name, source:data.recipe.source_url, ingredients:data.recipe.ingredients, rating:data.recipe.metadata.rating},null,2);
    $('#import-save').disabled = false;
  } catch(e) { $('#import-status').textContent = e.message; }
}
async function saveImport() {
  if (!importPreview) return;
  try {
    const result = await api('/api/recipes/import', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(importPreview)});
    $('#import-status').textContent = `${result.updated ? 'Updated existing' : 'Imported'} recipe #${result.id}`;
    importPreview = null; $('#import-save').disabled=true; loadRecipes();
  } catch(e) { $('#import-status').textContent=e.message; }
}
