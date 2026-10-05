import { validateMeasurements } from './measure-tools.mjs';
import { validateVector } from './vector-data.mjs';
const clone = value => JSON.parse(JSON.stringify(value));
export const designKey = state => JSON.stringify({ name: state.name || 'Mi diseño', elements: state.elements, measurements:state.measurements || [] });

export class DesignHistory {
  constructor(limit = 60) { this.limit = limit; this.entries = []; this.index = -1; }
  reset(state) { this.entries = [clone(state)]; this.index = 0; }
  record(state) {
    if (this.index >= 0 && designKey(this.entries[this.index]) === designKey(state)) return false;
    this.entries.splice(this.index + 1);
    this.entries.push(clone(state));
    if (this.entries.length > this.limit) this.entries.shift();
    this.index = this.entries.length - 1; return true;
  }
  get canUndo() { return this.index > 0; }
  get canRedo() { return this.index < this.entries.length - 1; }
  get current() { return this.entries[this.index]; }
  undo() { if (!this.canUndo) return null; return clone(this.entries[--this.index]); }
  redo() { if (!this.canRedo) return null; return clone(this.entries[++this.index]); }
}

export function validateDesignFile(data) {
  if (!data || data.format !== 'pluma-a1.design' || data.version !== 1)
    throw new Error('Elige un diseño .pluma.json guardado desde Pluma A1.');
  if (!Array.isArray(data.elements) || data.elements.length > 100)
    throw new Error('El diseño debe contener hasta 100 elementos.');
  const ids = new Set();
  const elements = data.elements.map(e => {
    if (!e || !['text','image','vector'].includes(e.type) || !Number.isSafeInteger(e.id) || e.id < 1 || ids.has(e.id))
      throw new Error('El archivo contiene un elemento inválido o repetido.');
    ids.add(e.id);
    if (![e.x,e.y,e.w,e.rotation ?? 0].every(v => typeof v === 'number' && Number.isFinite(v))
        || e.w < (e.type === 'vector' ? .1 : 15) || e.w > 400 || Math.abs(e.x) > 10000 || Math.abs(e.y) > 10000)
      throw new Error('Revisa la posición y el tamaño de los elementos del archivo.');
    if (!e.opts || typeof e.opts !== 'object' || Array.isArray(e.opts))
      throw new Error('Faltan los ajustes de un elemento.');
    for (const [key,value] of Object.entries(e.opts)) {
      if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('El diseño tiene un ajuste numérico inválido.');
      if (value !== null && typeof value === 'object' && key !== 'crop') throw new Error('El diseño tiene un ajuste inválido.');
    }
    if (e.opts.crop) {
      const {x,y,w,h}=e.opts.crop;
      if (![x,y,w,h].every(v=>typeof v==='number' && Number.isFinite(v)) || x<0 || y<0 || w<=0 || h<=0 || x+w>1.000000001 || y+h>1.000000001)
        throw new Error('El recorte de una imagen queda fuera del original.');
    }
    if (e.type === 'text' && (typeof e.text !== 'string' || e.text.length > 50000))
      throw new Error('Cada texto puede tener hasta 50,000 caracteres.');
    if (e.type === 'image' && (typeof e.imageId !== 'string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(e.imageId)
        || ['__proto__','constructor','prototype'].includes(e.imageId) || !Object.hasOwn(data.images || {},e.imageId)
        || typeof data.images?.[e.imageId] !== 'string'
        || !/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+=*$/.test(data.images[e.imageId])
        || data.images[e.imageId].length > 22 * 1024 * 1024))
      throw new Error('Falta una imagen o su contenido no es válido. Guarda de nuevo el diseño original.');
    const out = { id:e.id, type:e.type, x:e.x, y:e.y, w:e.w, rotation:e.rotation || 0,
      colorMode:e.colorMode === 'multi' ? 'multi' : 'single', pen:Number.isInteger(e.pen) && e.pen >= 0 ? e.pen : 0,
      opts:clone(e.opts) };
    if (e.type === 'text') out.text = e.text;
    else if (e.type === 'vector') {
      out.vector = validateVector(e.vector);out.colorMode = 'single';
      if (e.w * out.vector.aspect > 2000) throw new Error('El alto del plano supera 2000 mm. Reduce su tamaño.');
    }
    else {
      out.imageId = e.imageId;
      if (e.original && [e.original.x,e.original.y,e.original.w,e.original.rotation ?? 0].every(v=>typeof v==='number' && Number.isFinite(v))
          && e.original.w>=15 && e.original.w<=400)
        out.original={x:e.original.x,y:e.original.y,w:e.original.w,rotation:e.original.rotation || 0,
          colorMode:e.original.colorMode==='multi' ? 'multi' : 'single',pen:Number.isInteger(e.original.pen) ? e.original.pen : 0};
    }
    return out;
  });
  return { name: typeof data.name === 'string' ? data.name.slice(0,80) : 'Mi diseño', elements, measurements:validateMeasurements(data.measurements),
    sel:elements.some(e => e.id === data.sel) ? data.sel : elements[0]?.id ?? null,
    nextId:Math.max(0,...elements.map(e => e.id))+1 };
}

export function fitPlacement(b, angle, area, shrink = true) {
  const a = angle * Math.PI / 180;
  const bw = Math.abs(Math.cos(a)) * b.w + Math.abs(Math.sin(a)) * b.h;
  const bh = Math.abs(Math.sin(a)) * b.w + Math.abs(Math.cos(a)) * b.h;
  const [x0,y0,x1,y1] = area;
  const scale = shrink ? Math.min(1,(x1-x0)/bw,(y1-y0)/bh) : 1;
  return { scale, x:(x0+x1-b.w*scale)/2, y:(y0+y1-b.h*scale)/2, w:b.w*scale };
}
