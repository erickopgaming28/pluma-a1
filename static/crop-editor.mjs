const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
const full = () => ({ x: 0, y: 0, w: 1, h: 1 });

/** Edita un rectángulo sobre el original; aplicar es la única operación que guarda. */
export function createCropEditor(onApply, onError) {
  const dialog = document.querySelector('#cropDlg');
  const canvas = document.querySelector('#cropCanvas'), ctx = canvas.getContext('2d');
  const fields = Object.fromEntries(['x', 'y', 'w', 'h'].map(k => [k, document.querySelector('#crop' + k.toUpperCase())]));
  const status = document.querySelector('#cropStatus'), apply = document.querySelector('#applyCrop');
  let image = null, rect = full(), view = null, drag = null, target = null, request = 0;
  const minimum = () => ({ w: Math.max(0.005, 2 / image.naturalWidth), h: Math.max(0.005, 2 / image.naturalHeight) });

  function paint() {
    if (!dialog.open) return;
    const { width, height } = canvas.getBoundingClientRect(), dpr = devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    if (!image) { view = null; return; }
    const scale = Math.min((width - 24) / image.naturalWidth, (height - 24) / image.naturalHeight);
    const w = image.naturalWidth * scale, h = image.naturalHeight * scale;
    const x = (width - w) / 2, y = (height - h) / 2;
    view = { x, y, w, h };
    ctx.drawImage(image, x, y, w, h);
    const left = x + rect.x * w, top = y + rect.y * h, rw = rect.w * w, rh = rect.h * h;
    ctx.beginPath(); ctx.rect(x, y, w, h); ctx.rect(left, top, rw, rh);
    ctx.fillStyle = 'rgba(0,0,0,.55)'; ctx.fill('evenodd');
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 3; ctx.strokeRect(left, top, rw, rh);
    ctx.strokeStyle = '#2743b8'; ctx.lineWidth = 1; ctx.strokeRect(left, top, rw, rh);
    ctx.fillStyle = '#fff';
    for (const cx of [left, left + rw]) for (const cy of [top, top + rh]) {
      ctx.fillRect(cx - 6, cy - 6, 12, 12); ctx.strokeRect(cx - 6, cy - 6, 12, 12);
    }
  }
  function sync() {
    for (const k in fields) fields[k].value = +(rect[k] * 100).toFixed(2);
    if (image) status.textContent = `${Math.round(rect.w * image.naturalWidth)} × ${Math.round(rect.h * image.naturalHeight)} píxeles del original.`;
    paint();
  }
  for (const [k, field] of Object.entries(fields)) field.addEventListener('change', () => {
    if (!image || !Number.isFinite(field.valueAsNumber)) { sync(); return; }
    const min = minimum(), v = field.valueAsNumber / 100;
    if (k === 'x' || k === 'y') rect[k] = clamp(v, 0, 1 - rect[k === 'x' ? 'w' : 'h']);
    else rect[k] = clamp(v, min[k], 1 - rect[k === 'w' ? 'x' : 'y']);
    sync();
  });
  for (const button of dialog.querySelectorAll('[data-crop-ratio]')) button.addEventListener('click', () => {
    if (!image) return;
    if (button.dataset.cropRatio === 'full') rect = full();
    else {
      const normalized = +button.dataset.cropRatio * image.naturalHeight / image.naturalWidth;
      const w = Math.min(1, normalized), h = Math.min(1, 1 / normalized);
      rect = { x: (1 - w) / 2, y: (1 - h) / 2, w, h };
    }
    sync();
  });
  const point = e => {
    const b = canvas.getBoundingClientRect();
    return [(e.clientX - b.left - view.x) / view.w, (e.clientY - b.top - view.y) / view.h];
  };
  canvas.addEventListener('pointerdown', e => {
    if (!view || e.button !== 0 || drag) return;
    const [x, y] = point(e);
    let corner = null;
    for (const right of [false, true]) for (const bottom of [false, true]) {
      if (Math.abs(x - (rect.x + (right ? rect.w : 0))) * view.w <= 16 && Math.abs(y - (rect.y + (bottom ? rect.h : 0))) * view.h <= 16) corner = { right, bottom };
    }
    if (!corner && (x < rect.x || x > rect.x + rect.w || y < rect.y || y > rect.y + rect.h)) return;
    drag = { id: e.pointerId, x, y, rect: { ...rect }, corner };
    canvas.setPointerCapture(e.pointerId); e.preventDefault();
  });
  canvas.addEventListener('pointermove', e => {
    if (!drag || drag.id !== e.pointerId) return;
    const [x, y] = point(e), start = drag.rect, min = minimum();
    if (!drag.corner) rect = { ...start, x: clamp(start.x + x - drag.x, 0, 1 - start.w), y: clamp(start.y + y - drag.y, 0, 1 - start.h) };
    else {
      const { right, bottom } = drag.corner;
      const left = right ? start.x : clamp(x, 0, start.x + start.w - min.w);
      const top = bottom ? start.y : clamp(y, 0, start.y + start.h - min.h);
      const endX = right ? clamp(x, start.x + min.w, 1) : start.x + start.w;
      const endY = bottom ? clamp(y, start.y + min.h, 1) : start.y + start.h;
      rect = { x: left, y: top, w: endX - left, h: endY - top };
    }
    sync();
  });
  canvas.addEventListener('pointerup', e => { if (drag?.id === e.pointerId) drag = null; });
  canvas.addEventListener('pointercancel', e => { if (drag?.id === e.pointerId) { rect = drag.rect; drag = null; sync(); } });
  dialog.addEventListener('close', () => { ++request; drag = null; target = null; });
  apply.addEventListener('click', () => {
    if (!image || !target) return;
    onApply(target, { ...rect }); dialog.close('apply');
  });
  new ResizeObserver(paint).observe(canvas);
  return {
    async open(el, url) {
      target = el; rect = { ...(el.opts.crop || full()) }; image = null; view = null;
      apply.disabled = true; for (const input of Object.values(fields)) input.disabled = true;
      status.textContent = 'Cargando la imagen original…'; dialog.showModal(); paint();
      const token = ++request;
      try {
        const source = new Image(); source.src = url; await source.decode();
        if (token !== request || !dialog.open) return;
        if (Math.min(source.naturalWidth, source.naturalHeight) < 2) throw new Error('La imagen debe tener al menos 2 × 2 píxeles.');
        image = source; apply.disabled = false; for (const input of Object.values(fields)) input.disabled = false;
        sync();
      } catch {
        if (token === request && dialog.open) { status.textContent = 'No se pudo cargar el original. Vuelve a cargar la imagen.'; onError(status.textContent); }
      }
    }
  };
}
