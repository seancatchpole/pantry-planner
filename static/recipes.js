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
async function recipeDetail(id) {
  try {
    const r = await api('/api/recipes/' + id), m = r.metadata || {}, panel = $('#recipe-detail');
    panel.replaceChildren(); panel.style.display = '';
    const close = node('button', 'Close detail', panel, 'btn'); close.onclick = () => panel.style.display = 'none';
    sourceHeader(r, panel);
    node('h4', 'Ingredients', panel);
    if ((m.ingredient_sections || []).length) {
      for (const section of m.ingredient_sections) { node('h5', section.name, panel); const ul=node('ul',null,panel); for(const ing of section.ingredients) node('li',ing.raw,ul); }
    } else { const ul = node('ul', null, panel); for (const i of r.ingredients) node('li', i.raw || `${i.qty || 'some'} ${i.unit} ${i.name}`, ul); }
    node('h4', 'Instructions', panel);
    if (m.instructions?.length) renderSteps(m.instructions, panel); else node('pre', r.steps, panel).style.whiteSpace='pre-wrap';
    for (const [title,value] of [['Yield',m.yield_text],['Times',m.times],['Nutrition (source supplied)',m.nutrition],['Nutrition provenance',m.nutrition_provenance],['Embedded source reviews',m.reviews],['Video metadata',m.video],['Tips',m.tips],['Dates',m.dates]]) {
      if (value == null) continue;
      const detail = node('details', null, panel); node('summary', title, detail); node('pre', typeof value==='string' ? value : JSON.stringify(value,null,2), detail).style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';
    }
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
