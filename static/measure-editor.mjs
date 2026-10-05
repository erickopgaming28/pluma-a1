import { measurePoints, snapMeasurementPoint, validateMeasurements } from './measure-tools.mjs';
import { worldPoint, localPoint } from './element-geometry.mjs';

const $ = selector => document.querySelector(selector);
const units = { mm: {factor:1, suffix:'mm'}, cm: {factor:10, suffix:'cm'}, in: {factor:25.4, suffix:'in'} };
const modeNames = {free:'Entre puntos', horizontal:'Horizontal', vertical:'Vertical'};

export function createMeasurementEditor(options) {
  let start = null, hover = null, current = null, calibrating = false, savedPanelOpen = false;
  const mode = () => $('#measureMode').value;
  const active = () => options.getScene().tool === 'measure';
  const number = value => (value / units[$('#measureUnit').value].factor).toLocaleString('es-MX', {maximumFractionDigits:3});
  const formatted = value => `${number(value)} ${units[$('#measureUnit').value].suffix}`;
  const result = item => measurePoints(item.start, item.end, item.mode);
  function syncPanel() {
    const tool = options.getScene().tool;
    const visible = active() || tool === 'margins' || savedPanelOpen;
    $('#measurementInspector').hidden = !visible;
    $('#showMeasurements').setAttribute('aria-pressed', visible && $('#savedMeasurements').open);
    $('#inspectorTitle').textContent = active() ? 'Regla' : tool === 'margins' ? 'Márgenes' : 'Medidas guardadas';
    $('#inspectorHint').textContent = active() ? 'Mide sobre la hoja sin perderla de vista.' : tool === 'margins' ? 'Ajusta el espacio libre alrededor del dibujo.' : 'Consulta las distancias y las guías de tu diseño.';
  }
  function showResult(item) {
    if (!item) {
      $('#measureResult').textContent = start ? 'Elige el segundo punto' : 'Elige el primer punto';
      $('#measureDetails').textContent = 'Toca dos puntos o escribe sus coordenadas en milímetros.';
      $('#calibrateScale').disabled = true;
      return;
    }
    const m = result(item);
    $('#measureResult').textContent = formatted(m.length);
    $('#measureDetails').textContent = `${modeNames[m.mode]} · ΔX ${formatted(m.dx)} · ΔY ${formatted(m.dy)} · ${m.angle.toFixed(1)}°`;
    const selected = options.getScene().selected;
    $('#calibrateScale').disabled = calibrating || item !== current || !selected || selected.type === 'text' || m.mode !== 'free' || m.length <= 0;
  }
  function syncList() {
    const measures = options.getMeasurements();
    $('#measureCount').textContent = measures.length;
    $('#measureToolbarCount').textContent = measures.length;
    const list = $('#measureList'); list.replaceChildren();
    measures.forEach((item,index) => {
      const row = document.createElement('li'), value = document.createElement('span'), remove = document.createElement('button');
      value.textContent = `${index + 1}. ${modeNames[item.mode]} · ${formatted(result(item).length)}`;
      remove.type = 'button'; remove.className = 'btn ghost'; remove.textContent = 'Eliminar';
      remove.setAttribute('aria-label',`Eliminar medida ${index + 1}`);
      remove.addEventListener('click', () => {
        options.beforeChange(); options.setMeasurements(options.getMeasurements().filter((_,i) => i !== index));
        if (current === item) current = null;
        syncList(); showResult(current); options.redraw();
      });
      row.append(value,remove); list.append(row);
    });
    $('#clearMeasures').disabled = !measures.length;
    $('#measureEmpty').hidden = !!measures.length;
  }
  function add(item) {
    try {
      const validated = validateMeasurements([...options.getMeasurements(),item]);
      if (result(item).length < .000001) { options.toast('Elige dos puntos distintos para medir.', true); return; }
      options.beforeChange(); options.setMeasurements(validated);
      current = validated.at(-1); start = null; hover = null;
      syncList(); showResult(current); options.redraw();
      $('#measureSaved').textContent = `Medida ${validated.length} guardada: ${formatted(result(current).length)}.`;
    } catch (error) { options.toast(error.message,true); }
  }
  function snapped(point,event) {
    const scene = options.getScene();
    if (!$('#measureSnap').checked || event?.altKey) return {point,snapped:false};
    return snapMeasurementPoint(point, {paper:scene.paper,boxes:scene.boxes,strokes:scene.strokes}, {k:scene.view.k,radiusPx:10,maxSegments:30000});
  }
  function move(point,event) {
    hover = snapped(point,event);
    $('#measureSnapHint').textContent = hover.snapped ? `Ajustado a ${hover.label}.` : hover.limited ? 'Archivo denso: también puedes usar las coordenadas exactas.' : 'Ajuste a bordes y trazos activado. Alt permite elegir libremente.';
    if (start) showResult({start,end:hover.point,mode:mode()});
    options.redraw();
  }
  function click(point,event) {
    const target = snapped(point,event);
    if (!start) { start = target.point; hover = target; current = null; showResult(null); options.redraw(); }
    else add({start,end:target.point,mode:mode()});
  }
  function cancel() { start = null; hover = null; showResult(current); options.redraw(); }
  function restored() { start = null; hover = null; current = null; syncList(); showResult(null); }
  function sceneChanged() {
    const available = !!options.getScene().selected;
    $('#measureWidth').disabled = $('#measureHeight').disabled = !available;
    $('#calibrateScale').disabled = calibrating || !available || options.getScene().selected.type === 'text' || !current || current.mode !== 'free' || !result(current).length;
  }
  function dimension(height) {
    const scene = options.getScene(), el = scene.selected;
    if (!el) return;
    const b = scene.boxes.find(box => box.id === el.id);
    add({start:worldPoint(b,b.rotation,0,0),end:worldPoint(b,b.rotation,height ? 0 : b.w,height ? b.h : 0),mode:'free'});
  }
  async function calibrate() {
    if (calibrating) return;
    const scene = options.getScene(), el = scene.selected, known = $('#knownDistance').valueAsNumber;
    if (!el || !current || !Number.isFinite(known) || known <= 0) { options.toast('Escribe una distancia real mayor que cero, en milímetros.',true); return; }
    const b = scene.boxes.find(box => box.id === el.id);
    if (current.mode !== 'free') { options.toast('Para ajustar la escala, mide entre dos puntos con «Entre puntos».',true); return; }
    if (![current.start,current.end].every(point => {
      const [x,y] = localPoint(b,b.rotation,...point);
      return x >= -.1 && y >= -.1 && x <= b.w + .1 && y <= b.h + .1;
    })) { options.toast('Los dos puntos deben estar dentro del elemento seleccionado.',true); return; }
    if (el.type === 'text') { options.toast('Ajusta la escala de una imagen o un plano importado.',true); return; }
    const factor = known / result(current).length;
    const original = current;
    calibrating = true;$('#calibrateScale').disabled = true;
    try {
      if (!await options.calibrate(el,factor)) return;
      if (current === original) {start = null; hover = null; current = null; showResult(null);}
      options.redraw();
    } finally {calibrating = false;sceneChanged();}
  }
  function bind() {
    $('#measureTool').addEventListener('click', () => options.setTool(active() ? '' : 'measure'));
    $('#showMeasurements').addEventListener('click', () => {
      if (active() || options.getScene().tool === 'margins') {
        $('#savedMeasurements').open = !$('#savedMeasurements').open;
      } else {
        savedPanelOpen = !savedPanelOpen;
        if (savedPanelOpen) $('#savedMeasurements').open = true;
      }
      syncPanel(); options.redraw();
    });
    $('#savedMeasurements').addEventListener('toggle', syncPanel);
    $('#closeInspector').addEventListener('click', () => {
      savedPanelOpen = false;
      if (active() || options.getScene().tool === 'margins') options.setTool('');
      else { syncPanel(); options.redraw(); }
      $('#measureTool').focus();
    });
    $('#measureMode').addEventListener('change', () => { if (start && hover) showResult({start,end:hover.point,mode:mode()}); options.redraw(); });
    $('#measureUnit').addEventListener('change', () => { syncList(); showResult(start && hover ? {start,end:hover.point,mode:mode()} : current); options.redraw(); });
    $('#measureSnap').addEventListener('change', () => { hover = null; options.redraw(); });
    $('#measureGuides').addEventListener('change',options.redraw);
    $('#cancelMeasure').addEventListener('click',cancel);
    $('#measureWidth').addEventListener('click',() => dimension(false));
    $('#measureHeight').addEventListener('click',() => dimension(true));
    $('#clearMeasures').addEventListener('click', () => {options.beforeChange();options.setMeasurements([]);restored();options.redraw();});
    $('#measureCoordinates').addEventListener('submit',event => {
      event.preventDefault();
      add({start:[$('#measureAX').valueAsNumber,$('#measureAY').valueAsNumber],end:[$('#measureBX').valueAsNumber,$('#measureBY').valueAsNumber],mode:mode()});
    });
    $('#calibrateScale').addEventListener('click',calibrate);
    restored(); sceneChanged(); syncPanel();
  }
  function toolChanged() {
    $('#measureTool').setAttribute('aria-pressed',active());
    $('#measurementTools').hidden = !active();
    syncPanel();
    start = null; hover = null; showResult(current); sceneChanged();
  }
  function drawLine(ctx,item,view,preview=false,index=null,occupied=[]) {
    const m = result(item), {k,ox,oy} = view;
    const screen = p => [ox+p[0]*k,oy+p[1]*k];
    const a = screen(m.start), b = screen(m.lineEnd), end = screen(m.end);
    ctx.save(); ctx.strokeStyle = '#075d50'; ctx.fillStyle = '#075d50'; ctx.lineWidth = 1.5;
    ctx.setLineDash(preview ? [5,4] : []);
    ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();ctx.setLineDash([]);
    if (m.mode !== 'free') {
      ctx.save();ctx.setLineDash([3,4]);ctx.strokeStyle='#456c63';
      ctx.beginPath();ctx.moveTo(...b);ctx.lineTo(...end);ctx.stroke();ctx.restore();
    }
    for (const p of [a,end]) { ctx.beginPath();ctx.arc(...p,4,0,Math.PI*2);ctx.fill(); }
    const text = `${index == null ? '' : `${index + 1}: `}${formatted(m.length)}`;
    ctx.font = '600 12px system-ui'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const width = ctx.measureText(text).width+14;
    const canvasWidth=ctx.canvas.width/(window.devicePixelRatio || 1),canvasHeight=ctx.canvas.height/(window.devicePixelRatio || 1);
    const midpoint=[(a[0]+b[0])/2,(a[1]+b[1])/2];
    const cx = Math.max(width/2+2,Math.min(midpoint[0],canvasWidth-width/2-2));
    const preferred = Math.max(35,Math.min(midpoint[1]-15,canvasHeight-13));
    let cy = null;
    // Keep measured geometry fixed; only stagger label boxes within the visible sheet.
    for (const offset of [0,-26,26,-52,52,-78,78,-104,104,-130,130]) {
      const y=Math.max(35,Math.min(preferred+offset,canvasHeight-13));
      const rect={left:cx-width/2-3,right:cx+width/2+3,top:y-14,bottom:y+14};
      if (occupied.every(other=>rect.right<=other.left || rect.left>=other.right || rect.bottom<=other.top || rect.top>=other.bottom)) {
        cy=y;occupied.push(rect);break;
      }
    }
    // Crowded drawings retain every exact value in the saved-measurements list.
    if (cy === null) {ctx.restore();return;}
    if (Math.abs(cy-preferred) > 1) {
      ctx.save();ctx.lineWidth=1;ctx.strokeStyle='#456c63';ctx.setLineDash([2,3]);
      ctx.beginPath();ctx.moveTo(...midpoint);ctx.lineTo(cx,cy+(cy<midpoint[1] ? 11 : -11));ctx.stroke();ctx.restore();
    }
    ctx.fillStyle = '#f4fff9'; ctx.fillRect(cx-width/2,cy-11,width,22);
    ctx.fillStyle = '#075d50'; ctx.fillText(text,cx,cy); ctx.restore();
  }
  function drawRulers(ctx,view,width,height,paper) {
    const {k,ox,oy}=view,unit=units[$('#measureUnit').value];
    const step = [1,2,5,10,20,50,100,200].map(v=>v*unit.factor).find(v=>v*k>=55) || 500*unit.factor;
    ctx.save();ctx.fillStyle='#f0f6f2';ctx.fillRect(28,0,width-28,25);ctx.fillRect(0,25,28,height-25);
    ctx.fillStyle='#294d43';ctx.strokeStyle='#749b8e';ctx.lineWidth=1;ctx.font='11px system-ui';
    ctx.textAlign='center';ctx.textBaseline='top';
    for(let t=Math.ceil(Math.max(0,(28-ox)/k)/step)*step;t<=Math.min(paper[0],(width-ox)/k);t+=step){
      const x=ox+t*k;ctx.beginPath();ctx.moveTo(x,20);ctx.lineTo(x,25);ctx.stroke();ctx.fillText(number(t),x,3);
    }
    for(let t=Math.ceil(Math.max(0,(25-oy)/k)/step)*step;t<=Math.min(paper[1],(height-oy)/k);t+=step){
      const y=oy+t*k;ctx.beginPath();ctx.moveTo(23,y);ctx.lineTo(28,y);ctx.stroke();
      ctx.save();ctx.translate(4,y);ctx.rotate(-Math.PI/2);ctx.fillText(number(t),0,0);ctx.restore();
    }
    ctx.fillStyle='#294d43';ctx.fillRect(0,0,28,25);ctx.fillStyle='#fff';ctx.fillText(unit.suffix,14,5);ctx.restore();
  }
  function draw(ctx,view,width,height) {
    const occupied=[];
    if ($('#measureGuides').checked) options.getMeasurements().forEach((item,i) => drawLine(ctx,item,view,false,i,occupied));
    if (!active()) return;
    drawRulers(ctx,view,width,height,options.getScene().paper);
    if (start && hover) drawLine(ctx,{start,end:hover.point,mode:mode()},view,true,null,occupied);
    if (hover) {
      const x=view.ox+hover.point[0]*view.k,y=view.oy+hover.point[1]*view.k;
      ctx.save();ctx.strokeStyle=hover.snapped ? '#075d50' : '#53647a';ctx.lineWidth=1.5;
      ctx.beginPath();ctx.moveTo(x-9,y);ctx.lineTo(x+9,y);ctx.moveTo(x,y-9);ctx.lineTo(x,y+9);ctx.stroke();ctx.restore();
    }
  }
  return {bind,click,move,cancel,restored,sceneChanged,toolChanged,draw};
}
