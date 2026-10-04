import { normalizeAngle, worldPoint, localPoint, rotatedBounds } from './element-geometry.mjs';
import { createCropEditor } from './crop-editor.mjs';
import { DesignHistory, designKey, validateDesignFile, fitPlacement } from './design-tools.mjs';
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), Math.max(lo, hi));

const SLIDERS = {
  text: [
    { k: 'size', label: 'Tamaño de letra', min: 4, max: 18, step: 0.5, unit: ' mm' },
    { k: 'line_spacing', label: 'Interlineado', min: 0.8, max: 2.2, step: 0.05, unit: '×' },
    { k: 'letter_spacing', label: 'Espacio entre letras', min: -10, max: 40, step: 1, unit: ' %' },
    { k: 'slant', label: 'Inclinación', min: -15, max: 25, step: 1, unit: '°' },
    { k: 'human', label: 'Naturalidad (pulso de mano)', min: 0, max: 100, step: 1, unit: ' %' },
  ],
  image: [
    { k: 'detail', label: 'Detalle de los contornos', min: 0, max: 300, step: 1, unit: ' %' },
    { k: 'threshold', label: 'Oscuridad que se conserva', min: 1, max: 254, step: 1, unit: '' },
    { k: 'photo_cleaning', label: 'Limpieza de textura en la foto', min: 0, max: 300, step: 1, unit: ' %' },
    { k: 'shade', label: 'Cantidad de sombreado', min: 0, max: 300, step: 1, unit: ' %' },
    { k: 'hatch_spacing', label: 'Separación del rayado', min: 0.3, max: 10, step: 0.05, unit: ' mm' },
    { k: 'dot_spacing', label: 'Separación de los puntos', min: 0.3, max: 10, step: 0.05, unit: ' mm' },
    { k: 'hatch_angle', label: 'Ángulo del rayado', min: -360, max: 360, step: 5, unit: '°' },
    { k: 'brightness', label: 'Brillo', min: -300, max: 300, step: 1, unit: ' %' },
    { k: 'contrast', label: 'Contraste', min: -99, max: 300, step: 1, unit: ' %' },
  ],
};
const DEFAULTS = {
  text: { font: 'EMSAllure', size: 9, line_spacing: 1.15, letter_spacing: 0, slant: 0, human: 55, align: 'left', join: true, seed: 1 },
  image: { mode: 'trazo', detail: 55, threshold: 160, photo_cleaning: 65, shade: 50, hatch_spacing: 1.2, dot_spacing: .7, hatch_angle: 45, brightness: 0, contrast: 0, cross: true, invert: false, seed: 1, portrait_style: 'suave' },
};
const SAMPLE = 'Querida Sofía:\n\nTe escribo esta carta sin tocar la pluma: la sostiene mi impresora 3D. ¿Verdad que parece letra de verdad?\n\nUn abrazo,\nErick';

// Lo que se guarda: los elementos colocados en la hoja (x, y, w en mm desde la esquina superior izquierda).
const S = { name: 'Mi diseño', elements: null, sel: null, nextId: 1 };
// Estado de la sesión: conversiones, vista, trabajo compuesto, impresora.
const R = { cfg: null, geo: null, rend: {}, seq: {}, thumbs: {}, view: null, job: null, over: null, dirty: false,
            anim: null, sent: null, status: {}, penReady: false, fileTarget: 'sel', tasks: {}, asyncImages: false,
            viewport: { zoom: 1, x: 0, y: 0 }, canvasTool: '', marginSaving: false };

let legacy = null;
try {
  const saved = JSON.parse(localStorage.getItem('pluma-a1') || 'null');
  if (saved && Array.isArray(saved.elements)) Object.assign(S, saved);
  else legacy = saved;  // versión anterior: un solo texto que ocupaba toda la hoja
} catch {}
const history = new DesignHistory();
let historyReady = false, historyTimer, fileBusy = false;
const persist = () => {
  try { localStorage.setItem('pluma-a1', JSON.stringify(S)); }
  catch { $('#saveHint').textContent = 'No se pudo guardar en este navegador. Descarga tu diseño con «Guardar diseño».'; }
  if (historyReady) {
    clearTimeout(historyTimer);
    historyTimer = setTimeout(() => { history.record(S); paintHistory(); }, 400);
    paintHistory();
  }
};

function paintHistory() {
  if (!historyReady) return;
  const changed = designKey(S) !== designKey(history.current);
  $('#undoDesign').disabled = fileBusy || !(history.canUndo || changed);
  $('#redoDesign').disabled = fileBusy || changed || !history.canRedo;
}
function flushHistory() { clearTimeout(historyTimer); history.record(S); paintHistory(); }
function restoreDesign(state) {
  stopAnim(); clearTimeout(rotationTimer); ++composeSeq;
  for (const el of S.elements) {
    ++R.seq[el.id]; clearTimeout(timers[el.id]); delete resizeCenters[el.id];
    if (R.tasks[el.id]) api(`/api/element/tasks/${R.tasks[el.id]}/cancel`, {}).catch(() => {});
  }
  Object.assign(S, state);
  S.nextId = Math.max(S.nextId, ...S.elements.map(e => e.id + 1), 1);
  S.elements.forEach(el => {
    el.opts = { ...DEFAULTS[el.type], ...el.opts };
    if (el.type === 'image' && !el.original) el.original = {x:el.x,y:el.y,w:el.w,rotation:el.rotation || 0,colorMode:el.colorMode,pen:el.pen};
  });
  R.rend = {}; R.thumbs = {}; R.over = null; R.dirty = true; R.job = null;
  $('#contactTest').textContent = 'Probar apoyo'; $('#designName').value = S.name;
  syncPanel(); persist(); draw(); paintStats();
  S.elements.forEach(el => touch(el, true));
  if (!S.elements.length) compose();
}
function travelHistory(direction) {
  if (fileBusy) return;
  flushHistory();
  const state = direction === 'undo' ? history.undo() : history.redo();
  if (!state) return;
  historyReady = false; restoreDesign(state); historyReady = true; paintHistory();
  toast(direction === 'undo' ? 'Cambio deshecho.' : 'Cambio rehecho.');
}

const blobDataUrl = blob => new Promise((resolve,reject) => {
  const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(blob);
});
function paintFileBusy(value) {
  fileBusy = value;
  $('#saveDesign').disabled = $('#openDesign').disabled = value; paintHistory();
  $('#newDesign').disabled = value;
}
async function saveDesignFile() {
  if (fileBusy) return;
  paintFileBusy(true);
  try {
    const snapshot = JSON.parse(JSON.stringify(S));
    const images = {};
    for (const el of snapshot.elements.filter(e => e.type === 'image')) {
      if (images[el.imageId]) continue;
      const response = await fetch('/api/image/' + encodeURIComponent(el.imageId) + '/preview');
      if (!response.ok) throw new Error('Vuelve a cargar la imagen que falta antes de guardar el diseño.');
      images[el.imageId] = await blobDataUrl(await response.blob());
    }
    const data = {format:'pluma-a1.design',version:1,...snapshot,images,pens:R.cfg.pens.map(p=>({name:p.name,color:p.color})),paper:R.geo.paper};
    validateDesignFile(data);
    const blob = new Blob([JSON.stringify(data)],{type:'application/json'});
    if (blob.size > 64*1024*1024) throw new Error('El diseño supera 64 MB. Guarda menos imágenes en este archivo.');
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = (S.name || 'Mi diseño').replace(/[^a-zA-Z0-9áéíóúñ _-]/gi,'').slice(0,80) + '.pluma.json';
    link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
    toast('Diseño descargado con sus imágenes. No incluye datos de la impresora.');
  } catch (error) { toast(error.message || 'No se pudo guardar. Inténtalo de nuevo.',true); }
  finally { paintFileBusy(false); }
}
async function openDesignFile(file) {
  if (!file || fileBusy) return;
  paintFileBusy(true);
  try {
    if (file.size > 64*1024*1024) throw new Error('Elige un diseño de hasta 64 MB.');
    let data;
    try { data = JSON.parse(await file.text()); } catch { throw new Error('No se pudo leer el diseño. Elige un archivo .pluma.json.'); }
    const next = validateDesignFile(data), ids = new Map();
    for (const el of next.elements.filter(e=>e.type==='image')) {
      if (ids.has(el.imageId)) continue;
      const blob = await (await fetch(data.images[el.imageId])).blob();
      const form = new FormData(); form.append('file',new File([blob],'imagen.png',{type:blob.type}));
      ids.set(el.imageId,(await api('/api/image/upload',form,true)).id);
    }
    let changedColors = false;
    for (const el of next.elements) {
      if (el.type === 'image') el.imageId = ids.get(el.imageId);
      const color = data.pens?.[el.pen]?.color;
      const match = R.cfg.pens.findIndex(p=>p.color.toLowerCase() === String(color).toLowerCase());
      if (match >= 0) el.pen = match;
      else if (color || el.pen >= R.cfg.pens.length) { el.pen=0; changedColors=true; }
      if (el.colorMode === 'multi' && JSON.stringify(data.pens?.map(p=>p.color)) !== JSON.stringify(R.cfg.pens.map(p=>p.color))) changedColors=true;
    }
    flushHistory(); historyReady = false; restoreDesign(next); historyReady = true; history.record(S); paintHistory();
    toast(changedColors ? 'Diseño abierto. Revisa los colores: tus plumas actuales son diferentes.' : 'Diseño abierto. Puedes deshacer para volver al anterior.');
    if (Array.isArray(data.paper) && data.paper.some((v,i)=>Math.abs(v-R.geo.paper[i])>.1))
      toast('El diseño usaba otra hoja. Se conserva tu hoja actual; revisa que todos los trazos quepan.');
  } catch (error) { toast(error.message || 'No se pudo abrir el diseño. Tu dibujo sigue aquí.',true); }
  finally { paintFileBusy(false); $('#projectFile').value = ''; }
}

async function api(path, body, raw) {
  const opt = body === undefined ? {} : raw ? { method: 'POST', body } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({ error: 'Respuesta inesperada del programa.' }));
  if (!r.ok || data.error) {
    const error = new Error(data.error || 'El programa no pudo completar la solicitud.');
    error.command = data.command;
    throw error;
  }
  return data;
}

let toastTimer;
function toast(msg, bad) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (bad ? ' bad' : '');
  // popover: así queda por encima de los diálogos abiertos
  if (t.matches(':popover-open')) t.hidePopover();
  t.showPopover();
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.hidePopover(), bad ? 9000 : 4000);
}

/* ---------- elementos ---------- */
const sel = () => S.elements.find(e => e.id === S.sel) || null;

function addText(text = 'Escribe aquí', opts) {
  const [x0, y0, x1] = R.geo.drawable;
  const last = S.elements[S.elements.length - 1];
  const y = last ? Math.min(box(last).y + box(last).h + 6, R.geo.drawable[3] - 20) : y0;
  const el = { id: S.nextId++, type: 'text', x: x0, y, w: x1 - x0, text, opts: { ...DEFAULTS.text, ...opts }, colorMode: 'single', pen: 0 };
  S.elements.push(el);
  return el;
}
function addImage(imageId) {
  const [x0, y0, x1, y1] = R.geo.drawable;
  const w = (x1 - x0) * 0.6;
  const el = { id: S.nextId++, type: 'image', x: x0 + (x1 - x0 - w) / 2, y: y0 + (y1 - y0) * 0.15, w, imageId,
               opts: { ...DEFAULTS.image }, colorMode: 'single', pen: 0 };
  S.elements.push(el);
  return el;
}

/** Caja del elemento en mm de hoja. El alto sale de la conversión (texto) o de la proporción (imagen). */
function box(el) {
  const r = R.rend[el.id];
  const h = !r ? 20 : el.type === 'image' ? r.h * el.w / r.w : r.h;
  return { x: el.x, y: el.y, w: el.w, h };
}

function select(id) {
  if (id !== null && R.over?.contact_test) {
    R.over = null; $('#contactTest').textContent = 'Probar apoyo'; compose();
  }
  S.sel = id;
  persist();
  syncPanel();
  if (!R.anim) draw();
}

/** Algo cambió en un elemento: vuelve a convertirlo (con una pequeña espera mientras se arrastra un control). */
const timers = {};
const resizeCenters = {};
function touch(el, now) {
  R.over = null;
  R.seq[el.id] = (R.seq[el.id] || 0) + 1;
  if (R.tasks[el.id]) api(`/api/element/tasks/${R.tasks[el.id]}/cancel`, {}).catch(() => {});
  if (R.rend[el.id]) R.rend[el.id].pending = true;
  R.dirty = true;
  persist();
  paintStats();
  clearTimeout(timers[el.id]);
  timers[el.id] = setTimeout(() => renderEl(el), now ? 0 : 220);
}

let busy = 0;
async function convertImage(el, body, seq) {
  const { task } = await api('/api/element/tasks', body);
  const current = () => seq === R.seq[el.id] && S.elements.includes(el);
  if (current()) R.tasks[el.id] = task;
  try {
    while (current()) {
      const state = await api(`/api/element/tasks/${task}`);
      if (!current()) return null;
      if (state.state === 'done') return state.result;
      if (state.state === 'failed') throw new Error(state.message || 'No se pudo preparar la imagen.');
      if (state.state === 'canceled') return null;
      if (S.sel === el.id && $('#busy').textContent !== state.message) $('#busy').textContent = state.message;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    return null;
  } finally {
    if (!current()) api(`/api/element/tasks/${task}/cancel`, {}).catch(() => {});
    if (R.tasks[el.id] === task) delete R.tasks[el.id];
  }
}
async function renderEl(el) {
  const seq = R.seq[el.id] = (R.seq[el.id] || 0) + 1;
  $('#busy').hidden = !++busy;
  $('#busy').textContent = el.type === 'image' ? 'Preparando la imagen…' : 'Convirtiendo el texto…';
  try {
    const body = { id: el.id, type: el.type, text: el.text, image: el.imageId, opts: el.opts,
                   w: el.w, color_mode: el.colorMode, pen: el.pen };
    const r = el.type === 'image' && R.asyncImages ? await convertImage(el, body, seq) : await api('/api/element', body);
    if (r && seq === R.seq[el.id] && S.elements.includes(el)) {
      R.rend[el.id] = r;
      if (resizeCenters[el.id]) {
        const [cx, cy] = resizeCenters[el.id];
        el.x = cx - el.w / 2; el.y = cy - box(el).h / 2;
        if (!drag || drag.el !== el) delete resizeCenters[el.id];
        persist();
      }
      if (!R.anim) draw();
      if (el.id === S.sel) syncGeometry();
    }
  } catch (e) {
    if (seq === R.seq[el.id]) {
      R.rend[el.id] = { w: el.w, h: el.w * 0.7, layers: [], missing: true };
      toast(e.message, true);
    }
  }
  $('#busy').hidden = !--busy;
  if (!busy) compose();
}

let composeSeq = 0;
async function compose() {
  if (R.over) return;
  if (S.elements.some(e => !R.rend[e.id] || R.rend[e.id].missing || R.rend[e.id].pending)) {
    R.dirty = true; paintStats(); return;
  }
  const seq = ++composeSeq;
  const items = S.elements.filter(e => R.rend[e.id] && !R.rend[e.id].missing).map(e => ({ render_id: R.rend[e.id].render_id, x: e.x, y: e.y, rotation: angleOf(e) }));
  const first = S.elements.find(e => e.type === 'text');
  try {
    const job = await api('/api/compose', { items, title: first ? first.text.replace(/\{\d\}/g, '').slice(0, 40) : 'dibujo' });
    if (seq !== composeSeq) return;
    R.job = job; R.geo = job.geometry; R.dirty = false;
  } catch (e) {
    if (seq !== composeSeq) return;
    if (e.message === 'stale') { S.elements.forEach(el => renderEl(el)); return; }  // el programa se reinició
    toast(e.message, true);
  }
  paintStats();
}

const fmtTime = s => s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;
function paintStats() {
  if (R.geo?.paper) $('#paperInfo').textContent = R.geo.paper.map(v => Number(v.toFixed(1))).join(' × ') + ' mm';
  const page = R.job && R.job.pages[0];
  const has = !!(page && page.layers.length);
  const st = has && page.stats;
  const dots = has ? strokes().reduce((n,l)=>n+l.paths.filter(p=>p.length===4 && p[0]===p[2] && p[1]===p[3]).length,0) : 0;
  $('#emptyDesign').hidden = !!S.elements.length;
  $('#stats').innerHTML = !has ? (S.elements.length ? R.dirty ? 'Convirtiendo…' : 'No hay trazos. Prueba otro estilo o ajusta la oscuridad, el brillo y el contraste.' : 'Añade un texto o una imagen para empezar.')
    : (st.draw_mm === 0 && dots ? `<b>${dots.toLocaleString('es')}</b> puntos` : `<b>${(st.draw_mm / 1000).toFixed(1)} m</b> de trazo`)
      + ` · <b>${st.lifts.toLocaleString('es')}</b> levantadas · unos <b>${fmtTime(st.seconds)}</b>`
      + (page.layers.length > 1 ? ` · <b>${page.layers.length}</b> plumas` : '')
      + (R.over ? ' · marca de calibración' : '')
      + (R.job.outside ? ' · <b class="warn">Hay algo fuera de la zona que alcanza la pluma</b>' : '');
  $('#send').disabled = !has || R.dirty || R.job.outside || !!R.placing;
  $('#simulate').disabled = !has;
}

/* ---------- panel de controles (siempre del elemento seleccionado) ---------- */
const syncers = [];
function setOpt(group, key, v) {
  const el = sel();
  if (!el || el.type !== group) return;
  el.opts[key] = v;
  if (group === 'image' && ['mode', 'portrait_style'].includes(key)) paintImageOptions(el);
  touch(el);
}

function buildSliders(group) {
  const hints = {
    detail:'Más detalle conserva trazos pequeños; también puede aumentar el ruido.',
    threshold:'Un valor mayor incluye más zonas oscuras de la imagen.',
    photo_cleaning:'Suaviza textura y ruido antes de generar el recorrido.',
    shade:'Añade más marcas en las zonas oscuras para representar sus tonos.',
    hatch_spacing:'Menor separación da sombras más densas y más recorridos.',
    dot_spacing:'Menor separación añade puntos y tarda más en dibujarse.',
    hatch_angle:'Cambia la dirección de las líneas que forman las sombras.',
    brightness:'Aclara u oscurece la imagen antes de convertirla.',
    contrast:'Separa los tonos claros y oscuros para destacar las formas.',
    human:'Añade pequeñas variaciones a la letra; cero produce trazos más regulares.'
  };
  $(`#${group}Sliders`).innerHTML = SLIDERS[group].map(c => `
    <label class="field"><span class="lbl">${c.label}<output></output></span>
    <div class="range-row"><input type="range" min="${c.min}" max="${c.max}" step="${c.step}" data-k="${c.k}" data-unit="${c.unit}" aria-label="${c.label}">
    <input type="number" min="${c.min}" max="${c.max}" step="${c.step}" data-number="${c.k}" aria-label="Valor de ${c.label}"></div>
    ${hints[c.k] ? `<small class="control-hint">${hints[c.k]}</small>` : ''}</label>`).join('');
  for (const inp of $$(`#${group}Sliders input[type="range"]`)) {
    const number = inp.closest('label').querySelector('[data-number]');
    const show = () => {
      const extra = group === 'image' && inp.dataset.unit === ' %' && Math.abs(+inp.value) > 100;
      inp.closest('label').classList.toggle('extended', extra);
      inp.closest('label').querySelector('output').textContent = inp.value + inp.dataset.unit + (extra ? ' · extra' : '');
      number.value = inp.value;
      inp.setAttribute('aria-valuetext', inp.value + inp.dataset.unit + (extra ? ', supera el 100 por ciento' : ''));
      paintExtendedHint();
    };
    inp.addEventListener('input', () => { show(); setOpt(group, inp.dataset.k, +inp.value); });
    number.addEventListener('change', () => {
      if (!number.checkValidity() || !Number.isFinite(number.valueAsNumber)) {
        toast(`Escribe un valor entre ${number.min} y ${number.max}.`,true); show(); return;
      }
      inp.value = number.value; show(); setOpt(group, inp.dataset.k, +inp.value);
    });
    syncers.push(el => { if (el.type === group) { inp.value = el.opts[inp.dataset.k] ?? DEFAULTS[group][inp.dataset.k]; show(); } });
  }
}
function paintExtendedHint() {
  $('#extendedHint').hidden = !$$('#imageSliders .extended').some(label => !label.hidden);
}

function bindOptions() {
  for (const seg of $$('.seg[data-opt]')) {
    const [group, key] = seg.dataset.opt.split('.');
    const paint = el => $$('button', seg).forEach(b => {
      const on = b.dataset.v === el.opts[key]; b.classList.toggle('on',on); b.setAttribute('aria-pressed',on);
    });
    seg.addEventListener('click', e => { const b = e.target.closest('button'); if (b) { setOpt(group, key, b.dataset.v); paint(sel()); } });
    syncers.push(el => { if (el.type === group) paint(el); });
  }
  for (const cb of $$('input[type=checkbox][data-opt]')) {
    const [group, key] = cb.dataset.opt.split('.');
    cb.addEventListener('change', () => setOpt(group, key, cb.checked));
    syncers.push(el => { if (el.type === group) cb.checked = !!el.opts[key]; });
  }
}

function buildFonts(fonts) {
  $('#fonts').innerHTML = fonts.map(f => `
    <button type="button" data-id="${f.id}" title="${f.label}">
      <svg viewBox="${f.sample.box.join(' ')}"><path d="${f.sample.d}"/></svg><small>${f.label}</small>
    </button>`).join('');
  const paint = el => $$('#fonts button').forEach(b => b.classList.toggle('on', b.dataset.id === el.opts.font));
  $('#fonts').addEventListener('click', e => { const b = e.target.closest('button'); if (b) { setOpt('text', 'font', b.dataset.id); paint(sel()); } });
  syncers.push(el => { if (el.type === 'text') paint(el); });
}

const label = el => el.type === 'image' ? `Imagen ${el.id}` : (el.text.replace(/\{\d\}/g, '').trim().split('\n')[0] || `Texto ${el.id}`);

function paintColors() {
  const el = sel();
  if (!el) return;
  const pens = R.cfg.pens;
  if (el.pen >= pens.length) el.pen = 0;
  const multi = el.colorMode === 'multi';
  $$('#colorMode button').forEach(b => b.classList.toggle('on', b.dataset.v === el.colorMode));
  const insert = multi && el.type === 'text';
  $('#penChips').innerHTML = pens.map((p, i) => `
    <button type="button" class="chip ${!multi && i === el.pen ? 'on' : ''}" data-i="${i}" style="--c:${p.color}"><i></i>${insert ? `{${i + 1}} ` : ''}${esc(p.name)}</button>`).join('');
  $('#colorHint').textContent = !multi ? 'Este elemento se dibuja con una sola pluma.'
    : el.type === 'text' ? 'Toca un color para cambiar de pluma desde donde está el cursor. Queda una marca como {2} en el texto, que no se escribe.'
    : 'La imagen se reparte entre tus plumas según el color más parecido. Los contornos van con la más oscura.';
}

function syncPanel() {
  const el = sel();
  $('#elementCount').textContent = S.elements.length;
  $('#elList').innerHTML = S.elements.map(e => `
    <button type="button" class="chip ${e.id === S.sel ? 'on' : ''}" data-id="${e.id}" aria-pressed="${e.id === S.sel}"><svg class="element-icon" viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${e.type === 'image' ? '<rect x="2.5" y="3" width="15" height="14" rx="2"/><path d="m3 14 4-4 4 4 3-3 3 3"/>' : '<path d="M4 5V3h12v2M10 3v14M7 17h6"/>'}</svg><span>${esc(label(e))}</span></button>`).join('');
  $('#tab-text').hidden = !el || el.type !== 'text';
  $('#tab-image').hidden = !el || el.type !== 'image';
  $('#tab-colors').hidden = !el;
  $('#noSel').hidden = !!el;
  $('#rotationControls').hidden = !el;
  $('#designName').value = S.name || 'Mi diseño';
  if (!el) return;
  $('#rotationAngle').value = $('#rotationSlider').value = angleOf(el);
  syncGeometry();
  syncers.forEach(f => f(el));
  if (el.type === 'text' && $('#text').value !== el.text) $('#text').value = el.text;
  if (el.type === 'image') {
    $('#thumb').hidden = false;
    $('#thumb').src = imagePreview(el);
    paintImageOptions(el);
  }
  paintColors();
}

const imagePreview = el => '/api/image/' + encodeURIComponent(el.imageId) + '/preview';
function paintImageOptions(el) {
  const mode = el.opts.mode, hatch = ['boceto', 'rayado', 'retrato'].includes(mode);
  $('#imageModeHint').textContent = {
    trazo: 'Una línea por el centro de cada trazo oscuro, sin contorno, relleno ni sombreado. Ideal para dibujos y letras sobre fondo claro. Las partes separadas o ramificadas pueden necesitar varias levantadas.',
    contornos: 'Dibuja los bordes de las formas, sin sombrear. Una línea gruesa puede tener dos bordes.',
    boceto: 'Combina contornos y rayado para representar las sombras.',
    rayado: 'Representa las sombras con líneas paralelas, sin contornos.',
    fotolinea: 'Conserva bordes y rasgos de la foto como líneas, sin representar sus tonos de gris. Para dar volumen al rostro, usa Retrato con sombras.',
    retrato: 'Conserva rasgos y sombras para dar volumen al rostro. El botón prepara brillo y contraste neutros y usa tu pluma más oscura. Menor separación da más detalle y tarda más. La A1 mantiene una altura de apoyo fija: los grises se aproximan con la densidad de los trazos.',
    puntillismo: 'Representa luces y sombras con puntos separados, sin unirlos con líneas. Menor separación conserva más detalle y aumenta el tiempo: la punta sube y baja en cada punto. La vista supone una punta de 0.3 mm; el tamaño real depende de tu instrumento.'
  }[mode] || '';
  for (const input of $$('#imageSliders input[type="range"]')) {
    const key = input.dataset.k;
    input.closest('label').hidden = key === 'threshold' ? mode !== 'trazo' : key === 'photo_cleaning' ? !['fotolinea', 'retrato', 'puntillismo'].includes(mode)
      : key === 'dot_spacing' ? mode !== 'puntillismo' : key === 'shade' ? !(hatch || mode === 'puntillismo')
      : ['hatch_spacing', 'hatch_angle'].includes(key) ? !hatch : key === 'detail' && ['rayado', 'puntillismo'].includes(mode);
    const heading = input.closest('label').querySelector('.lbl');
    if (key === 'shade') heading.firstChild.textContent = mode === 'puntillismo' ? 'Densidad de los puntos' : 'Cantidad de sombreado';
    if (key === 'detail') heading.firstChild.textContent = mode === 'trazo' ? 'Detalle de la línea' : 'Detalle de los contornos';
    if (key === 'hatch_spacing') heading.firstChild.textContent = mode === 'retrato' ? 'Separación de los trazos' : 'Separación del rayado';
  }
  $('input[data-opt="image.cross"]').closest('label').hidden = !hatch || (mode === 'retrato' && el.opts.portrait_style !== 'rayado');
  $('#portraitStyles').hidden = mode !== 'retrato';
  $('[data-photo-mode]').classList.toggle('on', mode === 'fotolinea');
  $('[data-photo-mode]').setAttribute('aria-pressed', mode === 'fotolinea');
  $('#portraitMode').classList.toggle('on', mode === 'retrato');
  $('#portraitMode').setAttribute('aria-pressed', mode === 'retrato');
  $('#stipplingMode').classList.toggle('on', mode === 'puntillismo');
  $('#stipplingMode').setAttribute('aria-pressed', mode === 'puntillismo');
  $('#restoreCrop').disabled = !el.opts.crop;
  $('#cropSummary').textContent = el.opts.crop ? `Recorte: ${(el.opts.crop.w * 100).toFixed(1)} % del ancho × ${(el.opts.crop.h * 100).toFixed(1)} % del alto original.` : 'Se usa la imagen completa.';
  paintExtendedHint();
}
function syncGeometry() {
  const el = sel(); if (!el) return;
  const b = box(el);
  for (const k of ['x', 'y', 'w', 'h']) $('#element' + k.toUpperCase()).value = +b[k].toFixed(2);
  $('#elementH').disabled = el.type !== 'image' || !R.rend[el.id] || R.rend[el.id].missing || R.rend[el.id].pending;
  const pending = !R.rend[el.id] || R.rend[el.id].missing || R.rend[el.id].pending || R.placing;
  $('#centerElement').disabled = $('#fitElement').disabled = !!pending;
}

async function placeSelected(shrink) {
  const el = sel(); if (!el || R.placing || !R.rend[el.id] || R.rend[el.id].pending || R.rend[el.id].missing) return;
  flushHistory(); R.placing = true; R.dirty=true; paintStats(); syncGeometry(); stopAnim(); R.over = null;
  try {
    if (shrink) for (let attempt=0; attempt<3; attempt++) {
      const target = fitPlacement(box(el), angleOf(el), R.geo.drawable);
      if (target.scale >= .999) break;
      el.w = clamp(target.w,15,400);
      if (el.type === 'text') el.opts.size = Math.max(4,el.opts.size * target.scale);
      clearTimeout(timers[el.id]); await renderEl(el);
      if (!S.elements.includes(el)) return;
    }
    const target = fitPlacement(box(el), angleOf(el), R.geo.drawable,false);
    el.x=target.x;el.y=target.y;R.dirty=true;++composeSeq;persist();flushHistory();syncPanel();draw();await compose();
    const b=rotatedBounds(box(el),angleOf(el)),[x0,y0,x1,y1]=R.geo.drawable;
    toast(shrink && (b.w>x1-x0+.1 || b.h>y1-y0+.1)
      ? 'Este elemento sigue siendo grande. Reduce el texto o usa una hoja con más espacio.'
      : shrink ? 'Elemento centrado dentro del área de trabajo.' : 'Elemento centrado.');
  } finally { R.placing=false;syncGeometry();paintStats(); }
}
function setGeometry(key, value) {
  const el = sel(); if (!el) return;
  if (!Number.isFinite(value) || ((key === 'w' || key === 'h') && value <= 0)) { toast('Escribe una medida válida.', true); syncGeometry(); return; }
  stopAnim(); R.over = null; delete resizeCenters[el.id];
  if (key === 'w' || key === 'h') {
    const b = box(el);
    if (key === 'h' && (el.type !== 'image' || !b.h)) return;
    el.w = clamp(key === 'w' ? value : value * b.w / b.h, 15, 400);
    touch(el, true);
  } else { el[key] = value; R.dirty = true; ++composeSeq; persist(); compose(); }
  syncGeometry(); draw();
}

/* ---------- la hoja: dibujo, arrastre y cambio de tamaño ---------- */
const cv = $('#cv'), ctx = cv.getContext('2d');
const previewPaths = new WeakMap();
const HANDLE = 7;  // medio lado del tirador, en px de pantalla
const angleOf = el => Number.isFinite(+el.rotation) ? normalizeAngle(+el.rotation) : 0;

let rotationTimer;
function rotateSelected(value) {
  const el = sel(); if (!el) return;
  if (!Number.isFinite(value)) { toast('Escribe un ángulo válido.', true); syncPanel(); return; }
  stopAnim(); R.over = null;
  el.rotation = normalizeAngle(value);
  $('#rotationAngle').value = $('#rotationSlider').value = el.rotation;
  R.dirty = true; ++composeSeq; persist(); draw(); paintStats();
  clearTimeout(rotationTimer); rotationTimer = setTimeout(compose, 120);
}

/** Trazos a pintar, en orden de dibujo: [{color, paths, x, y, s}] */
function strokes() {
  if (R.over) return R.over.pages[0].layers.map(l => ({ color: l.color, paths: l.paths, x: 0, y: 0, s: 1 }));
  const out = [];
  for (const el of S.elements) {
    const r = R.rend[el.id];
    if (!r) continue;
    const s = el.type === 'image' ? el.w / r.w : 1;
    const b = box(el);
    for (const l of r.layers) out.push({ color: l.color, paths: l.paths, x: el.x, y: el.y, s, width: r.preview_width || .42,
                                       angle: angleOf(el), cx: b.w / 2, cy: b.h / 2 });
  }
  return out;
}

function draw(limit = Infinity) {
  const wrap = cv.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  if (cv.width !== Math.round(wrap.width * dpr) || cv.height !== Math.round(wrap.height * dpr)) {
    cv.width = Math.round(wrap.width * dpr); cv.height = Math.round(wrap.height * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, wrap.width, wrap.height);
  if (!R.geo) return;
  const [pw, ph] = R.geo.paper;
  const k = Math.min((wrap.width - 48) / pw, (wrap.height - 36) / ph) * R.viewport.zoom;
  const ox = (wrap.width - pw * k) / 2 + R.viewport.x, oy = (wrap.height - ph * k) / 2 + R.viewport.y;
  R.view = { k, ox, oy };

  ctx.save();
  ctx.shadowColor = 'rgba(0,0,0,.25)'; ctx.shadowBlur = 18; ctx.shadowOffsetY = 5;
  ctx.fillStyle = '#fdfcf8';
  ctx.fillRect(ox, oy, pw * k, ph * k);
  ctx.restore();

  // zona de la hoja que la pluma no alcanza
  const [rx0, ry0, rx1, ry1] = R.geo.reach;
  ctx.save();
  ctx.beginPath(); ctx.rect(ox, oy, pw * k, ph * k); ctx.rect(ox + rx0 * k, oy + ry0 * k, (rx1 - rx0) * k, (ry1 - ry0) * k);
  ctx.clip('evenodd');
  ctx.fillStyle = 'rgba(120,110,95,.16)'; ctx.fillRect(ox, oy, pw * k, ph * k);
  ctx.strokeStyle = 'rgba(120,110,95,.3)'; ctx.lineWidth = 1; ctx.beginPath();
  for (let d = -ph * k; d < pw * k; d += 9) { ctx.moveTo(ox + d, oy); ctx.lineTo(ox + d + ph * k, oy + ph * k); }
  ctx.stroke();
  ctx.restore();
  if (ry0 > 14) {
    ctx.fillStyle = 'rgba(80,72,60,.8)'; ctx.font = '12px system-ui'; ctx.textAlign = 'center';
    ctx.fillText('La pluma no llega a esta zona', ox + pw * k / 2, oy + ry0 * k / 2 + 4);
  }
  const [dx0, dy0, dx1, dy1] = R.geo.drawable;
  ctx.setLineDash([4, 5]); ctx.strokeStyle = 'rgba(39,67,184,.35)'; ctx.lineWidth = 1;
  ctx.strokeRect(ox + dx0 * k, oy + dy0 * k, (dx1 - dx0) * k, (dy1 - dy0) * k);
  ctx.setLineDash([]);
  if (R.canvasTool === 'margins') {
    const m = margins(), x0 = m.left, y0 = m.top, x1 = pw - m.right, y1 = ph - m.bottom;
    ctx.strokeStyle = '#2743b8'; ctx.lineWidth = 1.5;
    ctx.strokeRect(ox + x0 * k, oy + y0 * k, (x1 - x0) * k, (y1 - y0) * k);
    for (const [side, [x, y]] of Object.entries(marginHandles())) {
      ctx.fillStyle = '#fdfcf8'; ctx.beginPath(); ctx.arc(ox + x * k, oy + y * k, 8, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    }
  }

  ctx.lineCap = ctx.lineJoin = 'round';
  ctx.lineWidth = Math.max(0.7, 0.42 * k);
  ctx.globalAlpha = 0.86;
  let left = limit, tip = null;
  for (const g of strokes()) {
    if (left <= 0) break;
    const a = (g.angle || 0) * Math.PI / 180, c = Math.cos(a), sn = Math.sin(a);
    const cx = g.cx || 0, cy = g.cy || 0;
    const X = ox + (g.x + cx - c * cx + sn * cy) * k;
    const Y = oy + (g.y + cy - sn * cx - c * cy) * k, f = g.s * k;
    const px = (x, y) => X + (c * x - sn * y) * f;
    const py = (x, y) => Y + (sn * x + c * y) * f;
    ctx.strokeStyle = g.color;
    ctx.lineWidth = Math.max(.7, (g.width || .42) * k);
    if (limit === Infinity) {
      let cached = previewPaths.get(g.paths);
      if (!cached) {
        cached = { lines: new Path2D(), centers: [] };
        for (const p of g.paths) {
          if (p.length < 4) continue;
          if (p.length === 4 && p[0] === p[2] && p[1] === p[3]) {
            cached.centers.push([p[0], p[1]]); continue;
          }
          cached.lines.moveTo(p[0], p[1]);
          for (let i = 2; i < p.length; i += 2) cached.lines.lineTo(p[i], p[i + 1]);
        }
        previewPaths.set(g.paths, cached);
      }
      ctx.save();
      ctx.transform(c * f, sn * f, -sn * f, c * f, X, Y);
      ctx.lineWidth /= f;
      ctx.stroke(cached.lines);
      const radius = ctx.lineWidth / 2;
      if (cached.centers.length) {
        if (cached.radius !== radius) {
          cached.dots = new Path2D(); cached.radius = radius;
          for (const [x, y] of cached.centers) {
            cached.dots.moveTo(x + radius, y); cached.dots.arc(x, y, radius, 0, Math.PI * 2);
          }
        }
        ctx.fillStyle = g.color; ctx.fill(cached.dots);
      }
      ctx.restore();
      continue;
    }
    ctx.beginPath();
    for (const p of g.paths) {
      if (left <= 0) break;
      const n = Math.min(p.length / 2, left + 1);
      if (p.length === 4 && p[0] === p[2] && p[1] === p[3]) {
        ctx.fillStyle = g.color;
        const dot = new Path2D(); dot.arc(px(p[0], p[1]), py(p[0], p[1]), ctx.lineWidth / 2, 0, Math.PI * 2); ctx.fill(dot);
      } else {
        ctx.moveTo(px(p[0], p[1]), py(p[0], p[1]));
        for (let j = 1; j < n; j++) ctx.lineTo(px(p[2 * j], p[2 * j + 1]), py(p[2 * j], p[2 * j + 1]));
      }
      left -= p.length / 2 - 1;
      if (left <= 0) tip = [px(p[2 * n - 2], p[2 * n - 1]), py(p[2 * n - 2], p[2 * n - 1]), g.color];
    }
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  if (tip) { ctx.fillStyle = tip[2]; ctx.beginPath(); ctx.arc(tip[0], tip[1], 4, 0, 7); ctx.fill(); }
  if (limit !== Infinity || R.over) return;

  // cajas de los elementos: tenue al pasar por encima, marcada con tirador la seleccionada
  for (const el of S.elements) {
    const b = rotatedBounds(box(el), angleOf(el)), on = el.id === S.sel;
    const out = b.x < dx0 - 0.5 || b.y < dy0 - 0.5 || b.x + b.w > dx1 + 0.5 || b.y + b.h > dy1 + 0.5;
    const r = R.rend[el.id];
    if (r && r.missing) {
      ctx.fillStyle = 'rgba(120,110,95,.12)'; ctx.fillRect(ox + b.x * k, oy + b.y * k, b.w * k, b.h * k);
      ctx.fillStyle = 'rgba(80,72,60,.9)'; ctx.font = '12px system-ui'; ctx.textAlign = 'center';
      ctx.fillText('Vuelve a cargar esta imagen', ox + (b.x + b.w / 2) * k, oy + (b.y + b.h / 2) * k);
    }
    if (!on && !out && el.id !== R.hover) continue;
    ctx.strokeStyle = out ? '#c8322b' : on ? '#2743b8' : 'rgba(39,67,184,.4)';
    ctx.lineWidth = on ? 1.5 : 1;
    ctx.setLineDash(on ? [] : [3, 3]);
    ctx.beginPath();
    b.points.forEach(([x, y], i) => i ? ctx.lineTo(ox + x * k, oy + y * k) : ctx.moveTo(ox + x * k, oy + y * k));
    ctx.closePath(); ctx.stroke();
    ctx.setLineDash([]);
    if (on) {
      ctx.fillStyle = '#2743b8';
      const [hx, hy] = b.points[2];
      ctx.fillRect(ox + hx * k - HANDLE, oy + hy * k - HANDLE, HANDLE * 2, HANDLE * 2);
      const bb = box(el), top = worldPoint(bb, angleOf(el), bb.w / 2, 0), rot = rotationHandle(el);
      ctx.strokeStyle = '#2743b8'; ctx.beginPath(); ctx.moveTo(ox + top[0] * k, oy + top[1] * k);
      ctx.lineTo(ox + rot[0] * k, oy + rot[1] * k); ctx.stroke();
      ctx.fillStyle = '#fdfcf8'; ctx.beginPath(); ctx.arc(ox + rot[0] * k, oy + rot[1] * k, 8, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    }
  }
}

const pagePoint = e => {
  const r = cv.getBoundingClientRect();
  return [(e.clientX - r.left - R.view.ox) / R.view.k, (e.clientY - r.top - R.view.oy) / R.view.k];
};
const onHandle = (el, x, y) => {
  const b = box(el), t = (HANDLE + 6) / R.view.k;
  const [hx, hy] = worldPoint(b, angleOf(el), b.w, b.h);
  return Math.abs(x - hx) < t && Math.abs(y - hy) < t;
};
const rotationHandle = el => { const b = box(el); return worldPoint(b, angleOf(el), b.w / 2, -32 / R.view.k); };
const onRotation = (el, x, y) => { const [hx, hy] = rotationHandle(el); return Math.hypot(x - hx, y - hy) < 22 / R.view.k; };
const margins = () => ({ left: R.cfg.paper.margin, top: R.cfg.paper.margin, right: R.cfg.paper.margin, bottom: R.cfg.paper.margin, ...R.cfg.paper.margins });
function marginHandles() {
  const m = margins(), [w, h] = R.geo.paper, cx = (m.left + w - m.right) / 2, cy = (m.top + h - m.bottom) / 2;
  return { left: [m.left, cy], top: [cx, m.top], right: [w - m.right, cy], bottom: [cx, h - m.bottom] };
}
function hitMargin(x, y) {
  return Object.entries(marginHandles()).find(([, [hx, hy]]) => Math.hypot(x - hx, y - hy) < 22 / R.view.k)?.[0];
}
function syncMargins() { for (const input of $$('[data-margin]')) input.value = +margins()[input.dataset.margin].toFixed(2); }
function setCanvasTool(tool) {
  R.canvasTool = tool;
  $('#panView').setAttribute('aria-pressed', tool === 'pan'); $('#editMargins').setAttribute('aria-pressed', tool === 'margins');
  $('#marginTools').hidden = tool !== 'margins'; syncMargins(); draw();
  cv.style.cursor = tool === 'pan' ? 'grab' : 'default';
}
function zoomView(factor) {
  R.viewport.zoom = clamp(R.viewport.zoom * factor, .5, 4);
  $('#zoomValue').textContent = Math.round(R.viewport.zoom * 100) + ' %'; draw();
}
async function saveMargins(value) {
  if (R.marginSaving) { syncMargins(); return; }
  R.marginSaving = true; R.dirty = true; ++composeSeq; paintStats();
  $('#editMargins').disabled = true;
  $$('[data-margin], #resetMargins').forEach(input => input.disabled = true);
  try {
    const result = await api('/api/paper-layout', { margins: value });
    R.cfg.paper = result.paper; R.geo = result.geometry;
    S.elements.forEach(el => touch(el, true));
    if (!S.elements.length) { R.dirty = false; paintStats(); }
  } catch (error) { toast(error.message, true); await compose(); }
  finally {
    R.marginSaving = false; $('#editMargins').disabled = !R.paperLayout;
    $$('[data-margin], #resetMargins').forEach(input => input.disabled = false);
    syncMargins(); draw();
  }
}
const hitElement = (x, y) => [...S.elements].reverse().find(el => {
  const b = box(el);
  const [u, v] = localPoint(b, angleOf(el), x, y);
  return u >= -1 && u <= b.w + 1 && v >= -1 && v <= b.h + 1;
});

let drag = null;
function bindCanvas() {
  cv.addEventListener('pointerdown', e => {
    if (!R.view) return;
    cv.focus({preventScroll:true});
    if (R.marginSaving) return;
    if (R.canvasTool === 'pan' || e.button === 1) {
      drag = { mode: 'pan', clientX: e.clientX, clientY: e.clientY, x: R.viewport.x, y: R.viewport.y };
      cv.setPointerCapture(e.pointerId); e.preventDefault(); cv.style.cursor = 'grabbing'; return;
    }
    if (R.over) { R.over = null; compose(); }
    const [x, y] = pagePoint(e);
    const cur = sel();
    const side = R.canvasTool === 'margins' && hitMargin(x, y);
    if (side) drag = { mode: 'margin', side, before: margins() };
    else if (cur && onRotation(cur, x, y)) {
      const b = box(cur), cx = b.x + b.w / 2, cy = b.y + b.h / 2;
      drag = { mode: 'rotate', el: cur, cx, cy, start: Math.atan2(y - cy, x - cx), angle: angleOf(cur) };
      clearTimeout(rotationTimer);
    }
    else if (cur && onHandle(cur, x, y)) {
      const b = box(cur);
      drag = { mode: 'size', el: cur, cx: b.x + b.w / 2, cy: b.y + b.h / 2 };
      resizeCenters[cur.id] = [drag.cx, drag.cy];
    }
    else {
      const hit = hitElement(x, y);
      select(hit ? hit.id : null);
      if (hit) drag = { mode: 'move', el: hit, dx: x - hit.x, dy: y - hit.y };
    }
    if (drag) {
      stopAnim(); ++composeSeq;
      if (drag.el) { R.dirty = true; paintStats(); }
      cv.setPointerCapture(e.pointerId); e.preventDefault();
    }
  });
  cv.addEventListener('pointermove', e => {
    if (!R.view) return;
    if (drag?.mode === 'pan') {
      R.viewport.x = drag.x + e.clientX - drag.clientX; R.viewport.y = drag.y + e.clientY - drag.clientY; draw(); return;
    }
    const [x, y] = pagePoint(e);
    if (!drag) {
      const cur = sel(), hit = hitElement(x, y);
      cv.style.cursor = R.canvasTool === 'pan' ? 'grab' : R.canvasTool === 'margins' && hitMargin(x, y) ? 'crosshair'
        : cur && onRotation(cur, x, y) ? 'grab' : cur && onHandle(cur, x, y) ? 'nwse-resize' : hit ? 'move' : 'default';
      const h = hit ? hit.id : null;
      if (h !== R.hover) { R.hover = h; if (!R.anim) draw(); }
      return;
    }
    if (drag.mode === 'margin') {
      const m = margins(), [w, h] = R.geo.paper, side = drag.side;
      m[side] = +clamp(side === 'left' ? x : side === 'top' ? y : side === 'right' ? w - x : h - y, 0, 100).toFixed(1);
      const [rx0, ry0, rx1, ry1] = R.geo.reach;
      if (Math.min(w - m.right, rx1) - Math.max(m.left, rx0) >= 10 && Math.min(h - m.bottom, ry1) - Math.max(m.top, ry0) >= 10) {
        R.cfg.paper.margins = m; syncMargins(); draw();
      }
      return;
    }
    const el = drag.el;
    if (drag.mode === 'move') {
      // La edición es libre; compose y el servidor comprueban el alcance al enviar.
      el.x = x - drag.dx;
      el.y = y - drag.dy;
    } else if (drag.mode === 'rotate') {
      el.rotation = normalizeAngle(drag.angle + (Math.atan2(y - drag.cy, x - drag.cx) - drag.start) * 180 / Math.PI);
      $('#rotationAngle').value = $('#rotationSlider').value = +el.rotation.toFixed(1);
    } else {
      const a = angleOf(el) * Math.PI / 180;
      el.w = clamp(2 * (Math.cos(a) * (x - drag.cx) + Math.sin(a) * (y - drag.cy)), 15, 400);
      el.x = drag.cx - el.w / 2; el.y = drag.cy - box(el).h / 2;
      if (el.type === 'text') touch(el);  // el texto se vuelve a repartir en renglones con el ancho nuevo
    }
    R.dirty = true;
    draw();
  });
  const end = () => {
    if (!drag) return;
    const d = drag;
    drag = null;
    if (d.mode === 'pan') { cv.style.cursor = R.canvasTool === 'pan' ? 'grab' : 'default'; return; }
    if (d.mode === 'margin') {
      const value = margins(); R.cfg.paper.margins = d.before;
      saveMargins(value); return;
    }
    syncGeometry();
    persist();
    if (d.mode === 'size') touch(d.el, true); else { paintStats(); compose(); }
  };
  cv.addEventListener('pointerup', end);
  cv.addEventListener('pointercancel', end);
  cv.addEventListener('pointerleave', () => { if (!drag && R.hover != null) { R.hover = null; if (!R.anim) draw(); } });

  // soltar un archivo de imagen sobre la hoja crea un elemento nuevo
  const wrap = cv.parentElement;
  wrap.addEventListener('dragover', e => e.preventDefault());
  wrap.addEventListener('drop', e => { e.preventDefault(); loadImage(e.dataTransfer.files[0], 'new'); });
}

function stopAnim() { if (R.anim) cancelAnimationFrame(R.anim); R.anim = null; $('#simulate').textContent = 'Simular trazo'; }
function simulate() {
  if (R.anim) { stopAnim(); draw(); return; }
  const total = strokes().reduce((a, g) => a + g.paths.reduce((b, p) => b + p.length / 2 - 1, 0), 0);
  const dur = Math.min(14000, Math.max(3500, total * 1.2));
  const t0 = performance.now();
  $('#simulate').textContent = 'Detener';
  const step = t => {
    const f = (t - t0) / dur;
    if (f >= 1) { stopAnim(); draw(); return; }
    draw(Math.max(1, total * f));
    R.anim = requestAnimationFrame(step);
  };
  R.anim = requestAnimationFrame(step);
}

/* ---------- imagen ---------- */
async function loadImage(file, target) {
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  $('#busy').hidden = !++busy;
  try {
    const r = await api('/api/image/upload', fd, true);
    let el = target === 'sel' ? sel() : null;
    if (el && el.type === 'image') { el.imageId = r.id; delete el.opts.crop; }
    else el = addImage(r.id);
    el.original = { x: el.x, y: el.y, w: el.w, rotation: 0, colorMode: el.colorMode, pen: el.pen };
    R.thumbs[el.id] = imagePreview(el);
    select(el.id);
    touch(el, true);
  } catch (e) { toast(e.message, true); }
  $('#busy').hidden = !--busy;
}

/** Trabajo ya armado por el programa (la marca de calibración): se muestra en lugar de los elementos. */
function showJob(job) {
  stopAnim();
  if (!R.over) R.previewSelection=S.sel;
  R.over = job; R.job = job; R.geo = job.geometry; R.dirty = false;
  select(null);
  paintStats();
}

/* ---------- impresora ---------- */
const configuredTransport = () => ['stream', 'gcode_file', 'project_file'].includes(R.cfg?.printer?.transport) ? R.cfg.printer.transport : 'stream';
const direct = { pending: false, statusStamp: '', command: '', failed: false };
let controlPending = false;
const ACTIVE_STATES = ['RUNNING', 'PREPARE', 'PAUSE', 'SLICING', 'STOPPING'];
const printerBusy = () => ACTIVE_STATES.includes(R.status.state) || !!R.status.local_job?.active;

function effectivePrinterStatus(status) {
  const local = status.local_job;
  const matching = R.sent?.transport === 'stream' && local?.kind === R.sent.kind;
  const useLocal = local && (local.active || (matching && !ACTIVE_STATES.includes(status.state)));
  return useLocal ? { ...status, state: local.state, percent: local.percent, layer: local.layer, remaining: null, local: true, message: local.message } : status;
}

function operationError(id, message = '') {
  const element = $(id);
  element.textContent = message;
  element.hidden = !message;
}

function paintDirectControls() {
  const homed = $('#directHomed').checked;
  const ready = $('#directPenReady').checked;
  const blocked = direct.pending || printerBusy();
  for (const button of $$('[data-jog]')) button.disabled = blocked || !homed;
  $('#penUpDirect').disabled = blocked || !homed;
  $('#penDownDirect').disabled = blocked || !homed || !ready;
  $('#homeDirect').disabled = blocked;
  $('#probeDirect').disabled = blocked;
  $('#closeMove').disabled = direct.pending;
  for (const input of $$('#moveDlg input, #moveDlg select')) input.disabled = direct.pending;
  $('#moveDlg').setAttribute('aria-busy', String(direct.pending));
  $('#directGate').textContent = printerBusy() ? 'La A1 tiene un trabajo activo. Los comandos manuales estarán disponibles cuando termine.'
    : !homed ? 'Confirma la referencia de los ejes para habilitar los movimientos.'
    : 'Los movimientos están habilitados. Envía un solo paso y observa su resultado antes del siguiente.';
}

function showDirectResult(message, command, failed = false) {
  const result = $('#directResult');
  result.textContent = message;
  result.classList.toggle('bad', failed);
  if (command !== undefined) {
    direct.command = command || '';
    $('#directGcode').textContent = direct.command;
    $('#directDetails').hidden = !direct.command;
  }
}

function openDirect() {
  const { z_down, z_up } = R.cfg.pen;
  $('#directPenHeights').textContent = `Alturas guardadas: escritura ${z_down} mm; viaje ${z_up} mm. Cámbialas en Ajustes si tu soporte requiere otras alturas.`;
  $('#directHomed').checked = false;
  $('#directPenReady').checked = false;
  paintDirectControls();
  $('#moveDlg').showModal();
}

async function sendDirect(action, extra = {}) {
  if (direct.pending) return;
  if (printerBusy()) {
    showDirectResult('La A1 tiene un trabajo activo. Espera a que termine o cancélalo antes de mover los ejes.', undefined, true);
    return;
  }
  const homed = $('#directHomed').checked;
  const penReady = $('#directPenReady').checked;
  if (['jog', 'pen_up', 'pen_down'].includes(action) && !homed) {
    showDirectResult('Confirma que los ejes están referenciados y la zona está libre antes de moverlos.', undefined, true);
    return;
  }
  if (action === 'pen_down' && !penReady) {
    showDirectResult('Confirma que la altura de escritura está calibrada antes de bajar la pluma.', undefined, true);
    return;
  }
  if (action === 'home' && !confirm('Referenciar ejes mueve la A1 y utiliza la boquilla. Retira la pluma y la hoja, y deja toda la cama y su recorrido libres. ¿Ya está despejada para enviar el homing?')) return;
  direct.pending = true;
  direct.failed = false;
  direct.statusStamp = JSON.stringify(R.status.direct_command || null);
  showDirectResult(action === 'probe' ? 'Comprobando si la A1 acepta comandos, sin movimiento…' : 'Enviando comando. Observa la impresora…', '');
  paintDirectControls();
  if (action === 'home' || (action === 'jog' && extra.axis === 'Z')) {
    R.penReady = false;
    $('#directPenReady').checked = false;
  }
  if (action === 'home') $('#directHomed').checked = false;
  try {
    const result = await api('/api/printer/direct', {
      action, ...extra,
      homing_confirmed: homed,
      clear_for_home: action === 'home',
      pen_ready: penReady,
    });
    if (result.ok === false) {
      const error = new Error(result.message || 'La A1 no aceptó el comando. Revisa la conexión y su pantalla.');
      error.command = result.command;
      throw error;
    }
    showDirectResult(`${result.message || 'Comando enviado.'} La respuesta del comando no confirma la posición física; compruébala en la A1.`, result.command);
  } catch (error) {
    direct.failed = true;
    showDirectResult(`${error.message} Revisa la pantalla de la A1 y su posición antes de volver a enviar.`, error.command, true);
  } finally {
    direct.pending = false;
    paintDirectControls();
    pollStatus();
  }
}

function paintDirectStatus(status) {
  paintDirectControls();
  const command = status.direct_command;
  if (!command || direct.pending) return;
  const stamp = JSON.stringify(command);
  if (stamp === direct.statusStamp || (direct.command && command.command && direct.command !== command.command)) return;
  direct.statusStamp = stamp;
  if (direct.failed) {
    if (['error', 'unknown'].includes(command.phase) && command.command) {
      direct.command = command.command;
      $('#directGcode').textContent = command.command;
      $('#directDetails').hidden = false;
    }
    return;
  }
  if (command.message) showDirectResult(`${command.message} Comprueba la posición física en la A1.`, command.command, ['error', 'unknown'].includes(command.phase));
}

const STATES = { IDLE: 'Lista', PREPARE: 'Preparando', RUNNING: 'Dibujando', PAUSE: 'En pausa', FINISH: 'Lista', FAILED: 'Trabajo fallido', CANCELED: 'Trabajo cancelado', STOPPING: 'Cancelando', SLICING: 'Preparando' };
async function pollStatus() {
  try {
    R.status = await api('/api/printer/status');
    const s = effectivePrinterStatus(R.status);
    const pill = $('#printerPill'), label = pill.lastElementChild;
    const name = s.name || 'A1';
    const busy = ACTIVE_STATES.includes(s.state);
    paintDirectStatus(R.status);
    if (R.sent && R.sent.kind === 'adjust') {
      if (busy) R.sent.sawActive = true;
      if (s.state === 'PAUSE') R.sent.sawPause = true;
      if (s.state === 'FINISH' && (s.local || (R.sent.sawActive && R.sent.sawPause)) && !R.sent.finished && !R.sent.calibrationInvalidated && !s.print_error && (s.local || s.last_dispatch?.phase !== 'error')) {
        R.sent.finished = true; R.penReady = true;
        toast('La A1 confirmó el fin del ajuste. Revisa que la punta toque la hoja antes de enviar el dibujo.');
      } else if (['FAILED', 'CANCELED', 'STOPPING'].includes(s.state) || s.print_error || (!s.local && s.last_dispatch?.phase === 'error')) {
        R.penReady = false;
      }
    }
    pill.className = 'pill ' + (!s.configured ? '' : s.error || s.print_error || s.state === 'FAILED' ? 'bad' : !s.connected ? 'bad' : busy ? 'busy' : 'ok');
    label.textContent = !s.configured ? 'Impresora sin configurar'
      : s.error ? 'Sin acceso a la impresora'
      : !s.connected ? `${name}: sin conexión`
      : s.print_error ? `${name}: revisar aviso en pantalla`
      : `${name}: ${STATES[s.state] || 'Conectada'}${s.state === 'RUNNING' && s.percent != null ? ` ${s.percent} %` : ''}`;
    pill.title = s.error || s.print_error_message || '';
    const b = $('#banner');
    const dispatch = s.last_dispatch;
    const terminalLocal = s.local && ['FINISH', 'FAILED', 'CANCELED'].includes(s.state);
    b.hidden = !(s.connected && busy) && !s.print_error && !terminalLocal && !(dispatch && ['error', 'sending'].includes(dispatch.phase));
    if (s.connected && s.print_error) {
      b.innerHTML = `<span><b>Revisa la A1.</b> ${esc(s.print_error_message)}</span>`;
    } else if (terminalLocal) {
      b.innerHTML = `<span>${esc(s.message || (s.state === 'FINISH' ? 'Transmisión finalizada. Revisa el resultado en la A1.' : 'La transmisión se detuvo. Revisa el estado de la A1 antes de iniciar otro trabajo.'))}</span>`;
    } else if (s.state === 'STOPPING') {
      b.innerHTML = `<span>${esc(s.message || 'Cancelación enviada. Esperando a que la A1 termine el movimiento pendiente…')}</span>`;
    } else if (dispatch && dispatch.phase === 'error' && !busy) {
      b.innerHTML = `<span><b>El trabajo no inició.</b> ${esc(dispatch.message)}</span>`;
    } else if (dispatch && dispatch.phase === 'sending' && !busy) {
      b.innerHTML = `<span>${esc(dispatch.message)}</span>`;
    } else if (!b.hidden) {
      const guide = R.sent && R.sent.kind === 'guide' ? R.sent : null;
      const adjust = R.sent && R.sent.kind === 'adjust';
      const pen = R.sent && !guide && !adjust && R.sent.names[s.layer || 0];
      let msg, next = 'Reanudar';
      if (adjust) {
        msg = s.state === 'PAUSE' ? 'Baja la pluma hasta que <b>toque la hoja</b>, apriétala y pulsa Listo.'
          : R.sent.resumed ? 'Reanudar enviado. Esperando a que la A1 confirme el fin del ajuste…'
          : 'La A1 informa de un trabajo activo de ajuste. Espera a la pausa antes de colocar la pluma.';
        next = 'Listo';
      } else if (guide && s.state === 'PAUSE') {
        // paso 0 = colocar la pluma; después, una pausa por cada esquina
        const n = guide.names.length;
        msg = guide.step === 0 ? 'Coloca la pluma (punta ~4 mm por debajo de la boquilla) y pulsa Siguiente.'
          : `<b>${guide.step} de ${n}.</b> ${esc(guide.names[Math.min(guide.step, n) - 1])}. Acomoda la hoja y pulsa ${guide.step >= n ? 'Terminar' : 'Siguiente'}.`;
        next = guide.step === 0 || guide.step < n ? 'Siguiente' : 'Terminar';
      } else if (guide) msg = 'Moviendo la pluma al siguiente punto…';
      else msg = s.state === 'PAUSE'
        ? `En pausa. Baja la pluma${pen ? ` «${esc(pen)}»` : ''} hasta que toque la hoja y pulsa Reanudar.`
        : `Dibujando${s.percent != null ? ` · ${s.percent} %` : ''}${s.remaining ? ` · faltan ${s.remaining} min` : ''}`;
      if (s.local && s.message) msg = esc(s.message);
      b.innerHTML = `<span>${msg}</span>` + (s.state === 'PAUSE'
        ? `<button class="btn primary" data-act="resume" ${controlPending ? 'disabled' : ''}>${next}</button>` : guide || adjust ? '' : `<button class="btn" data-act="pause" ${controlPending ? 'disabled' : ''}>Pausar</button>`)
        + `<button class="btn" data-act="stop" ${controlPending ? 'disabled' : ''}>Cancelar trabajo</button>`;
    }
  } catch {}
}

async function openSend() {
  if (!R.job || R.dirty || busy) { toast('Espera a que termine la conversión.', true); return; }
  try {
    const check = await api('/api/preflight', { job: R.job.job, page: 0, layer: 'all', pen_ready: R.penReady });
    $('#preflight').textContent = `Recorrido válido · ${check.area.join(' × ')} mm útiles · ${fmtTime(check.seconds)} estimados · ${check.lifts} bajadas de pluma. ${check.warnings.join(' ')}`;
  } catch (e) { toast(e.message, true); return; }
  const layers = R.job.pages[0].layers;
  let html = `<label><input type="radio" name="what" value="all" checked> ${layers.length > 1 ? 'Todos los colores en un solo trabajo (se pausa para cambiar de pluma)' : 'El dibujo completo'}</label>`;
  if (layers.length > 1) html += layers.map((l, i) =>
    `<label><input type="radio" name="what" value="${i}"><i style="--c:${l.color}"></i> Solo ${esc(l.name)}</label>`).join('');
  $('#sendWhat').innerHTML = html;
  $('#sendTransport').value = configuredTransport();
  $('#penReady').checked = !!R.penReady;
  paintSendSteps();
  operationError('#sendError');
  $('#sendDlg').showModal();
}
const paintSendSteps = () => {
  const ready = $('#penReady').checked;
  $('#stepsFull').hidden = ready; $('#stepsReady').hidden = !ready;
};
const sendChoice = () => $('#sendWhat input:checked').value;

async function doSend() {
  const btn = $('#doSend');
  btn.disabled = true; btn.textContent = 'Enviando…';
  operationError('#sendError');
  try {
    if (R.dirty || busy) throw new Error('El diseño cambió. Cierra esta ventana y revisa la nueva vista previa.');
    const layer = sendChoice();
    const layers = R.job.pages[0].layers;
    const r = await api('/api/send', { job: R.job.job, page: 0, layer, pen_ready: $('#penReady').checked, transport: $('#sendTransport').value });
    R.sent = { kind: 'draw', transport: r.transport || $('#sendTransport').value, names: layer === 'all' ? layers.map(l => l.name) : [layers[+layer].name] };
    if (!$('#penReady').checked) R.penReady = false;
    $('#sendDlg').close();
    toast(r.message);
    pollStatus();
  } catch (e) { operationError('#sendError', e.message); }
  btn.disabled = false; btn.textContent = 'Enviar';
}

/* ---------- ajustes ---------- */
let pensDraft = [], foundName = null;
function paintPensEdit() {
  $('#pensEdit').innerHTML = pensDraft.map((p, i) => `
    <div><span>${i + 1}</span><input type="color" value="${p.color}" data-i="${i}" data-f="color" aria-label="Color de la pluma ${i + 1}">
    <input value="${esc(p.name)}" data-i="${i}" data-f="name" aria-label="Nombre de la pluma ${i + 1}">
    <button type="button" class="btn ghost" data-del="${i}" aria-label="Quitar pluma" ${pensDraft.length < 2 ? 'disabled' : ''}>×</button></div>`).join('');
}
function openSettings() {
  for (const el of $$('[data-cfg]')) {
    const [a, b] = el.dataset.cfg.split('.');
    if (el.type === 'checkbox') el.checked = !!R.cfg[a][b]; else el.value = R.cfg[a][b] ?? '';
  }
  $('[data-cfg="printer.transport"]').value = configuredTransport();
  // el código guardado no se devuelve a la página: dejar el campo vacío lo conserva
  $('[data-cfg="printer.access_code"]').placeholder = R.cfg.printer.has_code ? 'Guardado (escribe para cambiarlo)' : '8 caracteres';
  const size = $('[data-cfg="paper.size"]');
  if (![...size.options].some(o => o.value === R.cfg.paper.size)) size.value = 'custom';
  $('.custom-size').hidden = size.value !== 'custom';
  pensDraft = R.cfg.pens.map(p => ({ ...p }));
  paintPensEdit();
  $('#found').innerHTML = '';
  $('#settingsDlg').showModal();
}
function readSettings() {
  const out = { printer: {}, paper: {}, pen: {}, pens: pensDraft };
  for (const el of $$('[data-cfg]')) {
    const [a, b] = el.dataset.cfg.split('.');
    out[a][b] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? (+el.value || 0) : el.value.trim();
  }
  if (foundName) out.printer.name = foundName;
  out.paper.margins = out.paper.margin === R.cfg.paper.margin ? R.cfg.paper.margins ?? null : null;
  return out;
}
async function saveConfig(data, quiet) {
  const r = await api('/api/config', data);
  R.cfg = r.config; R.geo = r.geometry;
  paintMotion();
  R.penReady = false;
  if (R.sent?.kind === 'adjust') R.sent.calibrationInvalidated = true;
  syncPanel();
  S.elements.forEach(el => touch(el, true));  // colores de pluma y hoja pueden haber cambiado
  draw();
  pollStatus();
  if (!quiet) toast('Ajustes guardados.');
}

/* ---------- velocidad del próximo dibujo; conserva el ajuste físico ---------- */
function paintMotion() {
  $('#openMotion').textContent = `Velocidad · ${R.cfg.pen.draw_speed} mm/s`;
}
function openMotion() {
  for (const input of $$('[data-motion]')) input.value = R.cfg.pen[input.dataset.motion];
  $('#motionError').hidden = true;
  $('#motionDlg').showModal();
}
async function saveMotion() {
  const form = $('#motionDlg form');
  // Los campos avanzados se abren si requieren una corrección con teclado.
  if ($$('[data-motion]').some(input => !input.validity.valid)) {
    $('.motion-advanced').open = true;
    form.reportValidity(); return;
  }
  const button = $('#saveMotion'); button.disabled = true;
  $('#motionError').hidden = true;
  try {
    const data = Object.fromEntries($$('[data-motion]').map(input => [input.dataset.motion, input.valueAsNumber]));
    const result = await api('/api/motion-settings', data);
    Object.assign(R.cfg.pen, result.pen);
    paintMotion();
    $('#motionDlg').close();
    await compose();
    toast('Velocidad guardada para el próximo dibujo.');
  } catch (error) {
    $('#motionError').textContent = error.message; $('#motionError').hidden = false;
  } finally { button.disabled = false; }
}

/* ---------- arranque ---------- */
async function init() {
  const st = await api('/api/state');
  R.cfg = st.config; R.geo = st.geometry;
  R.asyncImages = !!st.features?.async_images;
  R.paperLayout = !!st.features?.paper_layout;
  $('#portraitMode').disabled = !st.features?.portrait;
  $('#stipplingMode').disabled = !st.features?.stippling;
  $('#editMargins').disabled = !R.paperLayout;
  if (!st.features?.extended_images) for (const c of SLIDERS.image) {
    if (['detail', 'shade', 'photo_cleaning', 'contrast'].includes(c.k)) c.max = 100;
    if (c.k === 'brightness') { c.min = -100; c.max = 100; }
  }
  $('#openMotion').hidden = false;
  $('#openMotion').disabled = !st.features?.motion_settings;
  $('#updateNotice').hidden = !!st.features?.motion_settings && !!st.features?.extended_images && R.paperLayout && !!st.features?.portrait && !!st.features?.stippling;
  $('#updateNotice').textContent = 'Actualización preparada: cuando termine el dibujo, cierra la terminal de Pluma A1 y vuelve a abrir Iniciar Pluma A1.bat. Después recarga esta página para activar las herramientas nuevas.';
  paintMotion();
  $('#appVersion').textContent = st.app_version ? `Versión ${st.app_version}` : 'Versión anterior: reinicia la terminal para cargar las mejoras';
  $('#lanUrl').textContent = st.lan_url || 'no disponible (sin red local)';
  let theme='system';try {theme=localStorage.getItem('pluma-a1-theme') || 'system';} catch {}
  if (!['system','light','dark'].includes(theme)) theme='system';
  $('#themeChoice').value=theme;
  document.documentElement.style.colorScheme=theme==='system' ? 'light dark' : theme;
  $('#themeChoice').addEventListener('change',event=>{
    const value=event.target.value;document.documentElement.style.colorScheme=value==='system' ? 'light dark' : value;
    try {localStorage.setItem('pluma-a1-theme',value);} catch {}
  });

  if (!S.elements) {
    S.elements = [];
    const first = addText(legacy && legacy.content || SAMPLE, legacy && legacy.text);
    S.sel = first.id;
  }
  S.elements.forEach(el => {
    el.opts = { ...DEFAULTS[el.type], ...el.opts };
    if (el.type === 'image' && !el.original) el.original = { x: el.x, y: el.y, w: el.w, rotation: 0, colorMode: el.colorMode, pen: el.pen };
  });
  buildFonts(st.fonts);
  history.reset(S); historyReady=true;paintHistory();
  $('#undoDesign').addEventListener('click',()=>travelHistory('undo'));
  $('#redoDesign').addEventListener('click',()=>travelHistory('redo'));
  $('#designName').value=S.name || 'Mi diseño';
  $('#designName').addEventListener('input',event=>{S.name=event.target.value.slice(0,80);persist();});
  $('#saveDesign').addEventListener('click',saveDesignFile);
  $('#openDesign').addEventListener('click',()=>$('#projectFile').click());
  $('#projectFile').addEventListener('change',event=>openDesignFile(event.target.files[0]));
  $('#newDesign').addEventListener('click',()=>{
    if (fileBusy) return;flushHistory();restoreDesign({name:'Mi diseño',elements:[],sel:null,nextId:S.nextId});
    flushHistory();toast('Hoja vacía lista. Puedes deshacer para recuperar tu diseño.');
  });
  $('#centerElement').addEventListener('click',()=>placeSelected(false));
  $('#fitElement').addEventListener('click',()=>placeSelected(true));
  $('#helpButton').addEventListener('click',()=>{
    $('#quickHelp').open=!$('#quickHelp').open;
    $('#helpButton').setAttribute('aria-expanded',$('#quickHelp').open);
    if ($('#quickHelp').open) $('#quickHelp').scrollIntoView({block:'nearest'});
  });
  document.addEventListener('keydown',event=>{
    const typing=event.target.closest('input,textarea,select,[contenteditable="true"]');
    const key=event.key.toLowerCase(),mod=event.ctrlKey || event.metaKey;
    if (mod && key==='s') {event.preventDefault();saveDesignFile();return;}
    if (typing || document.querySelector('dialog[open]') || fileBusy) return;
    if (mod && (key==='z' || key==='y')) {event.preventDefault();travelHistory(key==='y' || event.shiftKey ? 'redo' : 'undo');return;}
    if (mod && key==='d') {event.preventDefault();$('#dupEl').click();return;}
    if (key==='delete' && sel()) {event.preventDefault();$('#delEl').click();return;}
    if (event.target===cv && ['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key) && sel()) {
      event.preventDefault();const el=sel(),step=event.shiftKey ? 5 : 1;
      if (event.key==='ArrowLeft') el.x-=step;if (event.key==='ArrowRight') el.x+=step;
      if (event.key==='ArrowUp') el.y-=step;if (event.key==='ArrowDown') el.y+=step;
      R.dirty=true;++composeSeq;persist();syncGeometry();draw();compose();
    }
  });
  buildSliders('text'); buildSliders('image');
  bindOptions();
  function prepareTonalImage(mode) {
    const el = sel(); if (el?.type !== 'image') return;
    el.opts = { ...el.opts, mode, detail: 70, photo_cleaning: 85, shade: 100, portrait_style: 'suave',
                hatch_spacing: .35, dot_spacing: .7, hatch_angle: 45, cross: true, brightness: 0, contrast: 0, invert: false };
    el.colorMode = 'single';
    el.pen = R.cfg.pens.map((p, i) => {
      const c = p.color.slice(1).match(/../g).map(v => parseInt(v, 16));
      return { i, tone: .2126*c[0]+.7152*c[1]+.0722*c[2] };
    }).sort((a,b) => a.tone-b.tone)[0].i;
    touch(el, true); syncPanel();
  }
  $('#portraitMode').addEventListener('click', () => prepareTonalImage('retrato'));
  $('#stipplingMode').addEventListener('click', () => prepareTonalImage('puntillismo'));
  bindCanvas();
  $('#panView').addEventListener('click', () => setCanvasTool(R.canvasTool === 'pan' ? '' : 'pan'));
  $('#editMargins').addEventListener('click', () => setCanvasTool(R.canvasTool === 'margins' ? '' : 'margins'));
  $('#zoomIn').addEventListener('click', () => zoomView(1.25));
  $('#zoomOut').addEventListener('click', () => zoomView(.8));
  $('#centerView').addEventListener('click', () => { R.viewport = { zoom: 1, x: 0, y: 0 }; $('#zoomValue').textContent = '100 %'; draw(); });
  for (const input of $$('[data-margin]')) input.addEventListener('change', () => saveMargins({ ...margins(), [input.dataset.margin]: input.valueAsNumber }));
  $('#resetMargins').addEventListener('click', () => saveMargins(null));
  syncPanel();

  const cropEditor = createCropEditor((el, crop) => {
    if (!S.elements.includes(el)) return;
    el.opts.crop = crop; touch(el, true); syncPanel();
  }, message => toast(message, true));
  $('#cropImage').addEventListener('click', () => { const el = sel(); if (el?.type === 'image') cropEditor.open(el, imagePreview(el)); });
  $('#restoreCrop').addEventListener('click', () => { const el = sel(); if (el?.type === 'image') { delete el.opts.crop; touch(el, true); syncPanel(); } });
  $('#resetImageAdjustments').addEventListener('click', () => {
    const el = sel(); if (el?.type !== 'image') return;
    el.opts = { ...DEFAULTS.image, ...(el.opts.crop ? { crop: el.opts.crop } : {}) };
    touch(el, true); syncPanel();
  });
  $('#resetOriginalImage').addEventListener('click', () => {
    const el = sel(); if (el?.type !== 'image') return;
    stopAnim(); clearTimeout(rotationTimer); delete resizeCenters[el.id];
    Object.assign(el, el.original); el.opts = { ...DEFAULTS.image };
    touch(el, true); syncPanel(); draw();
  });
  for (const k of ['x', 'y', 'w', 'h']) $('#element' + k.toUpperCase()).addEventListener('change', e => setGeometry(k, e.target.valueAsNumber));
  $('#thumb').addEventListener('error', () => { $('#thumb').hidden = true; });

  const ta = $('#text');
  $('#rotationSlider').addEventListener('input', e => rotateSelected(+e.target.value));
  $('#rotationAngle').addEventListener('change', e => rotateSelected(e.target.valueAsNumber));
  $('#rotateLeft').addEventListener('click', () => { const el = sel(); if (el) rotateSelected(angleOf(el) - 90); });
  $('#rotateRight').addEventListener('click', () => { const el = sel(); if (el) rotateSelected(angleOf(el) + 90); });
  $('#rotateReset').addEventListener('click', () => rotateSelected(0));
  ta.addEventListener('input', () => {
    const el = sel(); if (!el || el.type !== 'text') return;
    el.text = ta.value; touch(el);
    const chip = $(`#elList [data-id="${el.id}"] span`); if (chip) chip.textContent = label(el);
  });

  $('#addText').addEventListener('click', () => { const el = addText(); select(el.id); touch(el, true); ta.focus(); ta.select(); });
  $('#addImage').addEventListener('click', () => { R.fileTarget = 'new'; $('#file').click(); });
  $('#elList').addEventListener('click', e => { const b = e.target.closest('button'); if (b) select(+b.dataset.id); });
  $('#delEl').addEventListener('click', () => {
    const el = sel(); if (!el) return;
    S.elements = S.elements.filter(e => e !== el);
    ++R.seq[el.id];
    if (R.tasks[el.id]) api(`/api/element/tasks/${R.tasks[el.id]}/cancel`, {}).catch(() => {});
    delete R.rend[el.id];
    R.over = null;
    select(null);
    compose();
  });
  $('#dupEl').addEventListener('click', () => {
    const el = sel(); if (!el) return;
    const copy = { ...JSON.parse(JSON.stringify(el)), id: S.nextId++, x: el.x + 6, y: el.y + 6 };
    S.elements.push(copy);
    if (R.thumbs[el.id]) R.thumbs[copy.id] = R.thumbs[el.id];
    select(copy.id); touch(copy, true);
  });

  $('#colorMode').addEventListener('click', e => {
    const b = e.target.closest('button'), el = sel(); if (!b || !el) return;
    el.colorMode = b.dataset.v; paintColors(); touch(el, true);
  });
  $('#penChips').addEventListener('click', e => {
    const b = e.target.closest('button'), el = sel(); if (!b || !el) return;
    const i = +b.dataset.i;
    if (el.colorMode === 'multi') {
      if (el.type !== 'text') return;
      ta.setRangeText(`{${i + 1}}`, ta.selectionStart, ta.selectionEnd, 'end');
      ta.focus(); el.text = ta.value;
    } else el.pen = i;
    paintColors(); touch(el, true);
  });
  $('#reseed').addEventListener('click', () => { const el = sel(); if (el) setOpt('text', 'seed', (el.opts.seed || 1) + 1); });

  const drop = $('#drop');
  $('#file').addEventListener('change', e => {
    loadImage(e.target.files[0], R.fileTarget);
    R.fileTarget = 'sel'; e.target.value = '';
  });
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); loadImage(e.dataTransfer.files[0], 'sel'); });

  $('#simulate').addEventListener('click', simulate);
  $('#send').addEventListener('click', openSend);
  $('#movePrinter').addEventListener('click', openDirect);
  $('#probeDirect').addEventListener('click', () => sendDirect('probe'));
  $('#homeDirect').addEventListener('click', () => sendDirect('home'));
  $('#penUpDirect').addEventListener('click', () => sendDirect('pen_up'));
  $('#penDownDirect').addEventListener('click', () => sendDirect('pen_down'));
  $('#directHomed').addEventListener('change', paintDirectControls);
  $('#directPenReady').addEventListener('change', paintDirectControls);
  $('#moveDlg').addEventListener('cancel', event => { if (direct.pending) event.preventDefault(); });
  for (const button of $$('[data-jog]')) button.addEventListener('click', () => {
    const axis = button.dataset.jog;
    const distance = +$('#jogStep' + axis).value * +button.dataset.sign;
    sendDirect('jog', { axis, distance });
  });
  $('#guide').addEventListener('click', () => {
    const [pw, ph] = R.geo.paper, [x0, y0, x1, y1] = R.geo.reach;
    const full = x0 < 0.5 && y0 < 0.5 && x1 > pw - 0.5 && y1 > ph - 0.5;
    $('#guideList').innerHTML = full ? '<li>La pluma alcanza las cuatro esquinas de la hoja.</li>'
      : '<li>La A1 sólo se mueve 256 × 256 mm y la pluma no llega a toda la hoja: donde no alcance una esquina, señalará el punto más cercano y te diré a cuántos milímetros queda.</li>';
    operationError('#guideError');
    $('#guideDlg').showModal();
  });
  $('#level').addEventListener('click', () => $('#levelDlg').showModal());
  $('#doLevel').addEventListener('click', async e => {
    const btn = e.target; btn.disabled = true; btn.textContent = 'Enviando…';
    try {
      const r = await api('/api/printer/level', {});
      R.penReady = false;  // tras nivelar hay que volver a ajustar la pluma
      $('#levelDlg').close(); toast(r.message);
    } catch (err) { toast(err.message, true); }
    btn.disabled = false; btn.textContent = 'Nivelar ahora';
  });
  $('#penReady').addEventListener('change', paintSendSteps);
  $('#adjust').addEventListener('click', () => { operationError('#adjustError'); $('#adjustDlg').showModal(); });
  $('[data-photo-mode]').addEventListener('click', () => {
    setOpt('image', 'mode', 'fotolinea'); syncPanel();
  });
  $('#contactTest').addEventListener('click', async () => {
    try {
      if (R.over?.contact_test) {
        R.over = null; select(S.elements.some(e=>e.id===R.previewSelection) ? R.previewSelection : S.elements[0]?.id ?? null); compose(); draw();
        $('#contactTest').textContent = 'Probar apoyo'; return;
      }
      showJob({ ...await api('/api/contact-test', {}), contact_test: true });
      $('#contactTest').textContent = 'Volver al diseño';
      toast('Prueba preparada: envía las 9 cruces y compara las filas trasera, central y frontal. Deben marcar por igual. Tu diseño sigue guardado.');
    } catch (e) { toast(e.message, true); }
  });
  $('#doAdjust').addEventListener('click', async e => {
    const btn = e.target; btn.disabled = true; btn.textContent = 'Enviando…';
    operationError('#adjustError');
    try {
      const r = await api('/api/adjust', { transport: configuredTransport() });
      R.sent = { kind: 'adjust', transport: r.transport || configuredTransport() }; R.penReady = false;
      $('#adjustDlg').close(); toast(r.message); pollStatus();
    } catch (err) { operationError('#adjustError', err.message); }
    btn.disabled = false; btn.textContent = 'Enviar y empezar';
  });
  $('#doGuide').addEventListener('click', async e => {
    const btn = e.target; btn.disabled = true; btn.textContent = 'Enviando…';
    operationError('#guideError');
    try {
      const r = await api('/api/guide', { transport: configuredTransport() });
      R.sent = { kind: 'guide', transport: r.transport || configuredTransport(), names: r.points, step: 0 };
      R.penReady = false;
      $('#guideDlg').close(); toast(r.message); pollStatus();
    } catch (err) { operationError('#guideError', err.message); }
    btn.disabled = false; btn.textContent = 'Enviar y empezar';
  });
  $('#doSend').addEventListener('click', doSend);
  $('#download').addEventListener('click', () => {
    location.href = `/api/export?job=${R.job.job}&page=0&layer=${sendChoice()}&ready=${$('#penReady').checked ? 1 : 0}`;
  });
  $('#downloadGcode').addEventListener('click', () => {
    location.href = `/api/export?job=${R.job.job}&page=0&layer=${sendChoice()}&ready=${$('#penReady').checked ? 1 : 0}&fmt=gcode`;
  });
  $('#downloadSvg').addEventListener('click', () => {
    location.href = `/api/export?job=${R.job.job}&page=0&layer=${sendChoice()}&fmt=svg`;
  });
  $('#gentleProfile').addEventListener('click', () => {
    $('[data-cfg="pen.draw_speed"]').value = 20;
    $('[data-cfg="pen.travel_speed"]').value = 60;
    toast('Perfil de prueba: escritura 20 mm/s y viaje 60 mm/s. Pulsa Guardar.');
  });
  $('#banner').addEventListener('click', async e => {
    const act = e.target.dataset.act; if (!act) return;
    if (controlPending) return;
    if (act === 'stop' && !confirm('¿Cancelar el trabajo en curso?')) return;
    controlPending = true;
    for (const button of $$('#banner button')) button.disabled = true;
    try {
      await api('/api/printer/control', { action: act });
      if (act === 'resume' && R.sent && R.sent.kind === 'guide') R.sent.step++;
      if (act === 'resume' && R.sent && R.sent.kind === 'adjust') {
        R.sent.resumed = true;
        toast('Reanudar enviado. Espera la confirmación de fin del ajuste.');
      }
      if (act === 'stop') {
        R.penReady = false;
        if (R.sent?.transport !== 'stream') R.sent = null;
      }
      $('#banner').hidden = true;
      setTimeout(pollStatus, 1500);
    } catch (err) { toast(err.message, true); }
    finally {
      controlPending = false;
      for (const button of $$('#banner button')) button.disabled = false;
    }
  });

  $('#openSettings').addEventListener('click', openSettings);
  $('#openMotion').addEventListener('click', openMotion);
  $('#saveMotion').addEventListener('click', saveMotion);
  $('#motionDlg form').addEventListener('submit', e => {
    if (e.submitter?.value !== 'cancel') { e.preventDefault(); saveMotion(); }
  });
  for (const button of $$('[data-motion-profile]')) button.addEventListener('click', () => {
    const values = { gentle: [20, 60, 10, 1000], normal: [40, 150, 20, 2500], fast: [60, 180, 20, 2500], mega: [80, 200, 30, 3000] }[button.dataset.motionProfile];
    ['draw_speed', 'travel_speed', 'z_speed', 'accel'].forEach((key, i) => $(`[data-motion="${key}"]`).value = values[i]);
  });
  $('#printerPill').addEventListener('click', openSettings);
  $('[data-cfg="paper.size"]').addEventListener('change', e => ($('.custom-size').hidden = e.target.value !== 'custom'));
  $('#pensEdit').addEventListener('input', e => { const { i, f } = e.target.dataset; if (f) pensDraft[i][f] = e.target.value; });
  $('#pensEdit').addEventListener('click', e => { const d = e.target.dataset.del; if (d !== undefined) { pensDraft.splice(+d, 1); paintPensEdit(); } });
  $('#addPen').addEventListener('click', () => { if (pensDraft.length < 8) { pensDraft.push({ name: `Pluma ${pensDraft.length + 1}`, color: '#7a3fb5' }); paintPensEdit(); } });
  $('#saveSettings').addEventListener('click', async () => {
    try { await saveConfig(readSettings()); $('#settingsDlg').close(); } catch (e) { toast(e.message, true); }
  });
  $('#discover').addEventListener('click', async e => {
    const btn = e.target; btn.disabled = true; btn.textContent = 'Buscando (6 s)…';
    try {
      const r = await api('/api/printer/discover', {});
      $('#found').innerHTML = r.printers.length ? r.printers.map(p =>
        `<button type="button" class="btn" data-ip="${esc(p.ip)}" data-serial="${esc(p.serial)}" data-name="${esc(p.name)}">${esc(p.name || p.model || 'Bambu')} · ${esc(p.ip)}</button>`).join('')
        : '<p class="hint">No encontré ninguna. Escribe la IP y el número de serie a mano (están en la pantalla de la impresora).</p>';
    } catch (err) { toast(err.message, true); }
    btn.disabled = false; btn.textContent = 'Buscar mi impresora en la red';
  });
  $('#found').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    $('[data-cfg="printer.ip"]').value = b.dataset.ip;
    $('[data-cfg="printer.serial"]').value = b.dataset.serial;
    foundName = b.dataset.name;
    $('[data-cfg="printer.access_code"]').focus();
  });
  $('#calDraw').addEventListener('click', async () => {
    try {
      await saveConfig(readSettings(), true);
      const job = await api('/api/calibration', {});
      Object.values(timers).forEach(clearTimeout);
      showJob(job);
      $('#settingsDlg').close();
      toast('Marca lista en la vista previa: pulsa «Enviar a la impresora». Toca la hoja para volver a tu diseño.');
    } catch (e) { toast(e.message, true); }
  });
  $('#calFix').addEventListener('click', () => {
    const ox = $('[data-cfg="pen.offset_x"]'), oy = $('[data-cfg="pen.offset_y"]');
    ox.value = (+ox.value + (+$('#calX').value - 30)).toFixed(1);
    oy.value = (+oy.value + (+$('#calY').value - 30)).toFixed(1);
    $('#calX').value = $('#calY').value = 30;
    toast('Posición corregida. Pulsa Guardar.');
  });

  new ResizeObserver(() => { if (!R.anim) draw(); }).observe(cv.parentElement);
  S.elements.forEach(el => touch(el, true));
  paintStats();
  pollStatus();
  setInterval(pollStatus, 3000);
}

const SERVER = 'http://127.0.0.1:8765/';
function problem(html) {
  const b = $('#banner');
  b.hidden = false;
  b.innerHTML = `<span>${html}</span>`;
}
// cualquier fallo de la página se muestra arriba en vez de quedar oculto en la consola
addEventListener('error', e => problem(`<b>Error en la página:</b> ${esc(e.message)} (${esc((e.filename || '').split('/').pop())}:${e.lineno})`));
addEventListener('unhandledrejection', e => problem(`<b>Error en la página:</b> ${esc(e.reason && e.reason.message || e.reason)}`));

if (location.protocol === 'file:') {
  // abierto como archivo suelto: la página necesita el programa de Python detrás
  problem(`Esta página está abierta como archivo y así no funciona. <a href="${SERVER}"><b>Abrir Pluma A1 aquí</b></a> (antes arranca «Iniciar Pluma A1.bat»).`);
  fetch(SERVER + 'api/state', { mode: 'no-cors' }).then(() => location.replace(SERVER), () => {});
} else {
  init().catch(e => problem(`<b>No pude arrancar:</b> ${esc(e.message)}. Comprueba que la ventana negra de «Iniciar Pluma A1.bat» sigue abierta y recarga con Ctrl+F5.`));
}
