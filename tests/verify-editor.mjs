// Offline browser QA: every request is fulfilled from fixtures or workspace files.
// No local server, printer connection or hardware command is started.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { worldPoint, localPoint, rotatedBounds, normalizeAngle } from '../static/element-geometry.mjs';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLUMA_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = JSON.parse(await fs.readFile(path.join(root, 'tests/editor-fixture.json'), 'utf8'));
const imageFixtures = JSON.parse(await fs.readFile(path.join(root, 'tests/image-editor-fixture.json'), 'utf8'));
const b = { x: 30, y: 40, w: 70, h: 20 };
for (const angle of [0, 90, -90, 180, 37]) {
  const p = worldPoint(b, angle, 12, 7), back = localPoint(b, angle, ...p);
  assert.ok(Math.abs(back[0] - 12) < 1e-9 && Math.abs(back[1] - 7) < 1e-9);
}
assert.equal(normalizeAngle(397), 37);
assert.equal(Math.round(rotatedBounds(b, 90).w), 20);
assert.equal(Math.round(rotatedBounds(b, 90).h), 70);

const browser = await chromium.launch({ ...(process.env.PLUMA_BROWSER_CHANNEL ? { channel: process.env.PLUMA_BROWSER_CHANNEL } : {}), headless: true });
await fs.mkdir(path.join(root, 'tests/artifacts'), { recursive: true });
try {
  for (const [name, viewport] of [['escritorio', { width: 1280, height: 950 }], ['movil', { width: 390, height: 844 }]]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    const errors = [], compositions = [], conversions = [];
    page.on('pageerror', e => errors.push(e.message));
    let rendered = new Map(), tasks = new Map(), slowNext = false, taskNumber = 0;
    let motionPen = structuredClone(fixture.state.config.pen), savedSpeeds = 0;
    let editorPaper = structuredClone(fixture.state.config.paper), savedMargins = [];
    const editorGeometry = () => {
      const geo = structuredClone(fixture.state.geometry), m = editorPaper.margins || Object.fromEntries(['left','top','right','bottom'].map(k => [k, editorPaper.margin]));
      const [w,h] = geo.paper, [x0,y0,x1,y1] = geo.reach;
      geo.drawable = [Math.max(m.left,x0), Math.max(m.top,y0), Math.min(w-m.right,x1), Math.min(h-m.bottom,y1)];
      return geo;
    };
    let motionSettingsSupported = true;
    const canceled = [];
    function convert(body) {
      conversions.push(body);
      const sample = structuredClone(body.type === 'image' ? imageFixtures[(['fotolinea','retrato','puntillismo'].includes(body.opts.mode) ? 'contornos' : body.opts.mode || 'boceto') + '-' + (body.opts.crop?.w === .5 ? 'middle' : 'full')] : fixture.text);
      const ratio = body.w / sample.w;
      if (body.type === 'image') {
        sample.h *= ratio;
        for (const l of sample.layers) l.paths = l.paths.map(p => p.map(v => v * ratio));
        if (body.opts.mode === 'puntillismo') {
          sample.preview_width = .3;
          for (const l of sample.layers) l.paths = l.paths.flatMap(p => Array.from({length:Math.floor(p.length/4)},(_,i)=>[p[i*4],p[i*4+1],p[i*4],p[i*4+1]]));
        }
      }
      sample.w = body.w;
      sample.render_id += '-' + body.id + '-' + taskNumber;
      rendered.set(sample.render_id, sample);
      return sample;
    }
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      const endpoint = url.pathname;
      let data;
      if (endpoint === '/api/state') data = { ...fixture.state, geometry: editorGeometry(), config: { ...fixture.state.config, paper: editorPaper, pen: motionPen }, app_version: '2026.10.03.17', features: { async_images: true, motion_settings: motionSettingsSupported, extended_images: true, paper_layout: true, portrait: motionSettingsSupported, stippling: motionSettingsSupported } };
      else if (endpoint === '/api/paper-layout') {
        const value = route.request().postDataJSON().margins;
        savedMargins.push(value); editorPaper.margins = value;
        data = { paper: editorPaper, geometry: editorGeometry() };
      }
      else if (endpoint === '/api/config') {
        const settings = route.request().postDataJSON();
        Object.assign(editorPaper, settings.paper); Object.assign(motionPen, settings.pen);
        data = { config: { ...fixture.state.config, paper: editorPaper, pen: motionPen }, geometry: editorGeometry() };
      }
      else if (endpoint === '/api/motion-settings') {
        Object.assign(motionPen, route.request().postDataJSON()); savedSpeeds++;
        data = { pen: motionPen, next_job_only: true };
      }
      else if (endpoint === '/api/contact-test') {
        data = { ...structuredClone(fixture.job), contact_test: true };
        data.pages[0].layers[0].paths = [[20, 20, 26, 20], [23, 17, 23, 23]];
      }
      else if (endpoint === '/api/printer/status') data = { configured: false, connected: false, state: 'IDLE', local_job: { active: false } };
      else if (endpoint === '/api/element') {
        const body = route.request().postDataJSON();
        data = convert(body);
      } else if (endpoint === '/api/element/tasks') {
        const token = 'task-' + ++taskNumber;
        tasks.set(token, { result: convert(route.request().postDataJSON()), remaining: slowNext ? 100 : 0 });
        slowNext = false;
        return route.fulfill({ status: 202, json: { task: token } });
      } else if (endpoint.startsWith('/api/element/tasks/')) {
        const token = endpoint.split('/')[4], task = tasks.get(token);
        if (endpoint.endsWith('/cancel')) {
          canceled.push(token); task.canceled = true; data = { state: 'canceled' };
        } else data = task.canceled ? { state: 'canceled' } : task.remaining-- > 0
          ? { state: 'running', message: 'Ordenando los trazos de la imagen…' }
          : { state: 'done', result: task.result };
      } else if (endpoint === '/api/compose') {
        const body = route.request().postDataJSON(); compositions.push(body);
        data = structuredClone(fixture.job);
        data.geometry = editorGeometry();
        const [x0, y0, x1, y1] = data.geometry.reach;
        data.outside = body.items.some(it => {
          const r = rendered.get(it.render_id);
          if (!r) return false;
          return r.layers.some(l => l.paths.some(p => {
            for (let i = 0; i < p.length; i += 2) {
              const [x, y] = worldPoint({ x: it.x, y: it.y, w: r.w, h: r.h }, it.rotation || 0, p[i], p[i + 1]);
              if (x < x0 || x > x1 || y < y0 || y > y1) return true;
            }
            return false;
          }));
        });
      } else if (endpoint === '/api/image/offline/preview') return route.fulfill({ body: await fs.readFile(path.join(root, 'tests/artifacts/image-source.png')), contentType: 'image/png' });
      else if (endpoint.startsWith('/api/')) throw new Error('Unexpected API call: ' + endpoint);
      if (data) return route.fulfill({ json: data });
      const relative = endpoint === '/' ? 'index.html' : endpoint.slice(1);
      const contentTypes = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.mjs': 'text/javascript', '.png': 'image/png' };
      try {
        return route.fulfill({ body: await fs.readFile(path.join(root, 'static', relative)), contentType: contentTypes[path.extname(relative)] || 'application/octet-stream' });
      } catch { return route.fulfill({ status: 404, body: '' }); }
    });
    await page.addInitScript(() => { if (!localStorage.getItem('pluma-a1')) localStorage.setItem('pluma-a1', JSON.stringify({ sel: 1, nextId: 3, elements: [
      { id: 1, type: 'text', x: 30, y: 70, w: 90, text: 'Hola\nMi pluma', opts: { font: 'EMSAllure', size: 8, human: 0, align: 'left', join: true, seed: 1, line_spacing: 1.15, letter_spacing: 0, slant: 0 }, colorMode: 'single', pen: 0 },
      { id: 2, type: 'image', x: 40, y: 145, w: 55, imageId: 'offline', opts: { mode: 'contornos', detail: 55, shade: 50, hatch_spacing: 1.2, hatch_angle: 45, brightness: 0, contrast: 0, cross: true, invert: false }, colorMode: 'single', pen: 0 }
    ] })); });
    await page.goto('http://pluma-offline.test/');
    await page.waitForFunction(() => !document.querySelector('#send').disabled);
    await page.locator('#openMotion').click();
    await page.locator('[data-motion-profile="fast"]').click();
    assert.equal(await page.locator('[data-motion="draw_speed"]').inputValue(), '60');
    await page.screenshot({ path: path.join(root, 'tests/artifacts/velocidad-' + name + '.png') });
    await page.locator('#saveMotion').click();
    await page.waitForFunction(() => !document.querySelector('#motionDlg').open);
    assert.equal(savedSpeeds, 1);
    assert.ok((await page.locator('#openMotion').textContent()).includes('60 mm/s'));
    await page.locator('#openMotion').click();
    await page.locator('[data-motion="draw_speed"]').fill('81');
    await page.locator('#saveMotion').click();
    assert.equal(savedSpeeds, 1);
    assert.ok(await page.locator('#motionDlg').isVisible());
    await page.locator('#motionDlg button[value="cancel"]').click();
    assert.equal(await page.locator('#elementCount').textContent(), '2');
    assert.equal(await page.locator('#paperInfo').textContent(), fixture.state.geometry.paper.join(' × ') + ' mm');
    assert.ok(!await page.locator('#rotationAngle').isVisible());
    await page.locator('.preparation summary').click();
    assert.ok(!await page.locator('#contactTest').isVisible());
    await page.locator('.preparation summary').click();
    assert.ok(await page.locator('#contactTest').isVisible());
    if (name === 'movil') await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(root, 'tests/artifacts/diseno-' + name + '.png') });
    if (name === 'escritorio') {
      // Intermediate widths, small phones and dark theme keep the canvas usable.
      for (const [variant, size, theme] of [
        ['tablet', { width: 1024, height: 768 }, 'light'],
        ['oscuro', { width: 1440, height: 900 }, 'dark'],
        ['movil-pequeno', { width: 360, height: 780 }, 'light']
      ]) {
        await page.setViewportSize(size); await page.emulateMedia({ colorScheme: theme });
        await page.waitForTimeout(100);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        const canvas = await page.locator('#cv').boundingBox();
        assert.ok(canvas.width > 250 && canvas.height > 300);
        await page.screenshot({ path: path.join(root, 'tests/artifacts/diseno-' + variant + '.png') });
      }
      await page.setViewportSize(viewport); await page.emulateMedia({ colorScheme: 'light' });
      await page.evaluate(() => window.scrollTo(0, 0));
    }
    await page.locator('#rotationControls summary').click();
    assert.ok(await page.locator('#rotationAngle').isVisible());
    await page.locator('#rotateRight').click();
    assert.equal(await page.locator('#rotationAngle').inputValue(), '90');
    await page.waitForTimeout(220);
    assert.equal(compositions.at(-1).items[0].rotation, 90);
    await page.locator('#rotationAngle').fill('37');
    await page.locator('#rotationAngle').press('Tab');
    await page.waitForTimeout(220);
    assert.equal(compositions.at(-1).items[0].rotation, 37);
    if (name === 'escritorio') {
      // Hit-test and dragging must follow the rotated box, not its old rectangle.
      const cv = await page.locator('#cv').boundingBox();
      const [pw, ph] = fixture.state.geometry.paper;
      const k = Math.min((cv.width - 48) / pw, (cv.height - 36) / ph);
      const ox = (cv.width - pw * k) / 2, oy = (cv.height - ph * k) / 2;
      const cx = cv.x + ox + 75 * k, cy = cv.y + oy + (70 + fixture.text.h / 2) * k;
      await page.mouse.move(cx, cy); await page.mouse.down();
      await page.mouse.move(cx + 5 * k, cy + 3 * k, { steps: 3 }); await page.mouse.up();
      await page.waitForTimeout(220);
      const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('pluma-a1')));
      assert.equal(saved.sel, 1); assert.equal(saved.elements[0].rotation, 37);
      assert.ok(Math.abs(saved.elements[0].x - 35) < 0.01);
      // Move the rotated text outside the paper: the editor must retain that
      // placement, while sending stays blocked until it is moved back inside.
      const inside = structuredClone(saved.elements[0]);
      const movedCenterX = cx + 5 * k, movedCenterY = cy + 3 * k;
      await page.mouse.move(movedCenterX, movedCenterY); await page.mouse.down();
      await page.mouse.move(movedCenterX - 65 * k, movedCenterY, { steps: 4 }); await page.mouse.up();
      await page.waitForFunction(() => document.querySelector('#stats .warn') && document.querySelector('#send').disabled);
      const outside = await page.evaluate(() => JSON.parse(localStorage.getItem('pluma-a1')).elements[0]);
      assert.ok(Math.abs(outside.x + 30) < .01);
      assert.equal(outside.rotation, 37);
      await page.screenshot({ path: path.join(root, 'tests/artifacts/movimiento-libre-escritorio.png') });
      // The warning may wrap the footer and resize the canvas: use its new scale.
      const outsideCv = await page.locator('#cv').boundingBox();
      const outsideK = Math.min((outsideCv.width - 48) / pw, (outsideCv.height - 36) / ph);
      const outsideOx = outsideCv.x + (outsideCv.width - pw * outsideK) / 2;
      const outsideOy = outsideCv.y + (outsideCv.height - ph * outsideK) / 2;
      const backX = outsideOx + (outside.x + outside.w / 2) * outsideK;
      const backY = outsideOy + (outside.y + fixture.text.h / 2) * outsideK;
      await page.mouse.move(backX, backY); await page.mouse.down();
      await page.mouse.move(backX + 65 * outsideK, backY, { steps: 4 }); await page.mouse.up();
      await page.waitForFunction(() => !document.querySelector('#send').disabled);
      const returned = await page.evaluate(() => JSON.parse(localStorage.getItem('pluma-a1')).elements[0]);
      assert.ok(Math.abs(returned.x - inside.x) < .01);
      assert.ok(Math.abs(returned.y - inside.y) < .01);
      assert.equal(returned.w, inside.w); assert.equal(returned.rotation, inside.rotation);
      const current = saved.elements[0];
      const [hx, hy] = worldPoint({ x: current.x, y: current.y, w: current.w, h: fixture.text.h }, 37, current.w, fixture.text.h);
      const handleX = cv.x + ox + hx * k, handleY = cv.y + oy + hy * k;
      await page.mouse.move(handleX, handleY); await page.mouse.down();
      await page.mouse.move(handleX + 3 * k * Math.cos(37 * Math.PI / 180), handleY + 3 * k * Math.sin(37 * Math.PI / 180));
      await page.mouse.up(); await page.waitForTimeout(260);
      const resized = await page.evaluate(() => JSON.parse(localStorage.getItem('pluma-a1')).elements[0]);
      assert.ok(Math.abs(resized.w - 96) < 0.01);
      assert.ok(Math.abs(resized.x + resized.w / 2 - 80) < 0.01);
      await page.reload();
      await page.waitForFunction(() => !document.querySelector('#send').disabled);
      await page.locator('#rotationControls summary').click();
      assert.equal(await page.locator('#rotationAngle').inputValue(), '37');
      await page.screenshot({ path: path.join(root, 'tests/artifacts/rotacion-escritorio.png') });
    } else {
      await page.locator('#elList [data-id="2"]').click();
      await page.locator('#rotateLeft').click();
      await page.waitForTimeout(220);
      assert.equal(compositions.at(-1).items[1].rotation, -90);
      await page.screenshot({ path: path.join(root, 'tests/artifacts/rotacion-movil.png'), fullPage: true });
    }
    // Keyboard fields, crop pointer gestures, cancel/apply/reset and real style choices.
    await page.locator('#elList [data-id="2"]').click();
    await page.locator('#elementX').fill('35'); await page.locator('#elementX').press('Tab');
    await page.waitForTimeout(180);
    assert.equal(compositions.at(-1).items[1].x, 35);
    await page.locator('#elementH').fill('40'); await page.locator('#elementH').press('Tab');
    await page.waitForTimeout(300);
    assert.ok(Math.abs(conversions.at(-1).w - 60) < 1e-8);
    await page.locator('[data-opt="image.mode"] [data-v="trazo"]').click();
    await page.waitForTimeout(300);
    assert.equal(conversions.at(-1).opts.mode, 'trazo');
    assert.equal(await page.locator('[data-opt="image.mode"] [data-v="trazo"]').textContent(), 'Una línea');
    assert.ok(await page.locator('[data-k="threshold"]').isVisible());
    assert.ok(!await page.locator('[data-k="hatch_spacing"]').isVisible());
    await page.locator('#cropImage').click();
    await page.waitForFunction(() => !document.querySelector('#applyCrop').disabled);
    await page.locator('[data-crop-ratio="1"]').click();
    assert.ok(Math.abs(+(await page.locator('#cropW').inputValue()) - 66.67) < .02);
    const cropBox = await page.locator('#cropCanvas').boundingBox();
    const scale = Math.min((cropBox.width - 24) / 300, (cropBox.height - 24) / 200);
    const originX = cropBox.x + (cropBox.width - 300 * scale) / 2;
    const originY = cropBox.y + (cropBox.height - 200 * scale) / 2;
    await page.mouse.move(originX + 50 * scale, originY);
    await page.mouse.down(); await page.mouse.move(originX + 65 * scale, originY + 20 * scale); await page.mouse.up();
    assert.ok(+(await page.locator('#cropX').inputValue()) > 20);
    await page.locator('#cropDlg button[value="cancel"]').click();
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('pluma-a1')).elements[1].opts.crop), undefined);
    await page.locator('#cropImage').click();
    await page.waitForFunction(() => !document.querySelector('#applyCrop').disabled);
    // Set size first so x/y can move into the desired middle crop.
    for (const [field, value] of [['W','50'],['H','50'],['X','25'],['Y','25']]) {
      await page.locator('#crop' + field).fill(value); await page.locator('#crop' + field).press('Tab');
    }
    await page.screenshot({ path: path.join(root, 'tests/artifacts/recorte-' + name + '.png') });
    await page.locator('#applyCrop').click();
    await page.waitForTimeout(300);
    assert.deepEqual(conversions.at(-1).opts.crop, { x: .25, y: .25, w: .5, h: .5 });
    await page.reload(); await page.waitForFunction(() => !document.querySelector('#send').disabled);
    assert.ok((await page.locator('#cropSummary').textContent()).includes('50.0'));
    assert.equal(await page.locator('[data-opt="image.mode"] .on').getAttribute('data-v'), 'trazo');
    await page.screenshot({ path: path.join(root, 'tests/artifacts/imagen-' + name + '.png'), fullPage: name === 'movil' });
    await page.locator('[data-opt="image.mode"] [data-v="rayado"]').click();
    assert.ok(await page.locator('[data-k="hatch_spacing"]').isVisible());
    assert.ok(!await page.locator('[data-k="threshold"]').isVisible());
    await page.locator('#resetImageAdjustments').click();
    await page.waitForTimeout(300);
    assert.equal(conversions.at(-1).opts.mode, 'trazo');
    assert.deepEqual(conversions.at(-1).opts.crop, { x: .25, y: .25, w: .5, h: .5 });
    await page.locator('#restoreCrop').click(); await page.waitForTimeout(300);
    assert.equal(conversions.at(-1).opts.crop, undefined);
    assert.ok(await page.locator('#restoreCrop').isDisabled());
    // Changing an option cancels a large conversion; only the newest result is composed.
    slowNext = true;
    await page.locator('[data-opt="image.mode"] [data-v="contornos"]').click();
    await page.waitForFunction(() => document.querySelector('#busy').textContent.includes('Ordenando'));
    assert.ok(await page.locator('#busy').isVisible());
    const obsolete = 'task-' + taskNumber;
    await page.locator('[data-opt="image.mode"] [data-v="trazo"]').click();
    await page.waitForFunction(() => !document.querySelector('#send').disabled && document.querySelector('#busy').hidden);
    assert.ok(canceled.includes(obsolete));
    assert.equal(conversions.at(-1).opts.mode, 'trazo');
    assert.ok(!compositions.at(-1).items.some(it => it.render_id === tasks.get(obsolete).result.render_id));
    assert.equal(await page.locator('#appVersion').textContent(), 'Versión 2026.10.03.17');
    await page.locator('[data-photo-mode]').click();
    await page.waitForFunction(() => !document.querySelector('#send').disabled && document.querySelector('#busy').hidden);
    assert.equal(conversions.at(-1).opts.mode, 'fotolinea');
    assert.equal(await page.locator('[data-photo-mode]').getAttribute('aria-pressed'), 'true');
    assert.ok(await page.locator('[data-k="photo_cleaning"]').isVisible());
    assert.ok(!await page.locator('[data-k="threshold"]').isVisible());
    assert.ok(!await page.locator('[data-k="hatch_spacing"]').isVisible());
    // Extended controls reach the conversion API, warn in text/color, and reset.
    await page.locator('[data-k="contrast"]').fill('220');
    await page.locator('[data-k="detail"]').fill('240');
    await page.waitForFunction(() => document.querySelector('#busy').hidden && !document.querySelector('#send').disabled);
    await page.waitForTimeout(300);
    assert.equal(conversions.at(-1).opts.contrast, 220);
    assert.equal(conversions.at(-1).opts.detail, 240);
    assert.ok(await page.locator('[data-k="contrast"]').evaluate(el => el.closest('label').classList.contains('extended')));
    assert.ok(await page.locator('#extendedHint').isVisible());
    await page.screenshot({ path: path.join(root, 'tests/artifacts/ajustes-extra-' + name + '.png'), fullPage: name === 'movil' });
    // Pan/zoom affect the view only. Centering restores the original canvas.
    const beforeView = await page.evaluate(() => localStorage.getItem('pluma-a1'));
    await page.locator('#panView').click(); await page.locator('#cv').scrollIntoViewIfNeeded();
    let canvas = await page.locator('#cv').boundingBox();
    const viewBefore = await page.locator('#cv').screenshot();
    await page.mouse.move(canvas.x + canvas.width / 2, canvas.y + canvas.height / 2);
    await page.mouse.down(); await page.mouse.move(canvas.x + canvas.width / 2 + 45, canvas.y + canvas.height / 2 + 25, { steps: 6 }); await page.mouse.up();
    assert.notDeepEqual(await page.locator('#cv').screenshot(), viewBefore);
    await page.locator('#zoomIn').click(); assert.equal(await page.locator('#zoomValue').textContent(), '125 %');
    await page.locator('#centerView').click(); assert.equal(await page.locator('#zoomValue').textContent(), '100 %');
    assert.equal(await page.evaluate(() => localStorage.getItem('pluma-a1')), beforeView);
    await page.locator('#panView').click();
    await page.locator('#zoomIn').click();
    // Rotation handle turns the image itself, leaving size and position intact.
    const selected = await page.evaluate(() => JSON.parse(localStorage.getItem('pluma-a1')).elements.find(e => e.id === 2));
    const renderedImage = rendered.get(compositions.at(-1).items.find(it => it.render_id.includes('-2-')).render_id);
    await page.locator('#cv').scrollIntoViewIfNeeded(); canvas = await page.locator('#cv').boundingBox();
    const [pw,ph] = fixture.state.geometry.paper;
    let k = Math.min((canvas.width - 48) / pw, (canvas.height - 36) / ph) * 1.25;
    let ox = canvas.x + (canvas.width-pw*k)/2, oy = canvas.y + (canvas.height-ph*k)/2;
    const bb = { x:selected.x, y:selected.y, w:selected.w, h:renderedImage.h };
    const handle = worldPoint(bb, selected.rotation || 0, bb.w/2, -32/k);
    const center = [bb.x+bb.w/2,bb.y+bb.h/2], vx = handle[0]-center[0], vy = handle[1]-center[1];
    await page.mouse.move(ox+handle[0]*k,oy+handle[1]*k); await page.mouse.down();
    await page.mouse.move(ox+(center[0]-vy)*k,oy+(center[1]+vx)*k,{steps:8}); await page.mouse.up();
    await page.waitForTimeout(180);
    const rotated = await page.evaluate(() => JSON.parse(localStorage.getItem('pluma-a1')).elements.find(e => e.id === 2));
    assert.ok(Math.abs(normalizeAngle(rotated.rotation-(selected.rotation||0)-90)) < .1);
    assert.deepEqual([rotated.x,rotated.y,rotated.w],[selected.x,selected.y,selected.w]);
    await page.locator('#centerView').click();
    await page.locator('#resetOriginalImage').click();
    await page.waitForFunction(() => document.querySelector('#busy').hidden && !document.querySelector('#send').disabled);
    const reset = await page.evaluate(() => JSON.parse(localStorage.getItem('pluma-a1')).elements.find(e => e.id === 2));
    assert.deepEqual([reset.x,reset.y,reset.w,reset.rotation],[reset.original.x,reset.original.y,reset.original.w,0]);
    assert.equal(reset.opts.crop,undefined); assert.equal(reset.opts.contrast,0); assert.equal(reset.opts.detail,55);
    assert.ok(!await page.locator('#extendedHint').isVisible());
    // Drag an independent margin, retain it on reload, then restore uniform guides.
    await page.locator('#editMargins').click(); await page.locator('#cv').scrollIntoViewIfNeeded();
    canvas = await page.locator('#cv').boundingBox();
    k = Math.min((canvas.width-48)/pw,(canvas.height-36)/ph);
    ox = canvas.x+(canvas.width-pw*k)/2; oy = canvas.y+(canvas.height-ph*k)/2;
    await page.mouse.move(ox+12*k,oy+ph/2*k); await page.mouse.down();
    await page.mouse.move(ox+20*k,oy+ph/2*k,{steps:6}); await page.mouse.up();
    await page.waitForFunction(() => !document.querySelector('#editMargins').disabled && document.querySelector('#busy').hidden);
    assert.ok(Math.abs(savedMargins.at(-1).left-20) < .2);
    assert.equal(savedMargins.at(-1).right,12);
    await page.reload(); await page.waitForFunction(() => !document.querySelector('#send').disabled);
    await page.locator('#editMargins').click();
    assert.ok(Math.abs(+await page.locator('#marginLeft').inputValue()-20) < .2);
    // Saving unrelated settings must retain the four independent guides.
    await page.locator('#openSettings').click();
    await page.locator('#saveSettings').click();
    await page.waitForFunction(() => !document.querySelector('#settingsDlg').open && document.querySelector('#busy').hidden);
    assert.ok(Math.abs(editorPaper.margins.left-20) < .2);
    await page.screenshot({ path: path.join(root, 'tests/artifacts/margenes-' + name + '.png'), fullPage: name === 'movil' });
    await page.locator('#resetMargins').click();
    await page.waitForFunction(() => document.querySelector('#busy').hidden && !document.querySelector('#editMargins').disabled);
    assert.equal(savedMargins.at(-1),null);
    await page.locator('#editMargins').click();
    await page.locator('#portraitMode').click();
    await page.waitForFunction(() => document.querySelector('#busy').hidden && !document.querySelector('#send').disabled);
    const portraitOptions = conversions.at(-1).opts;
    assert.equal(portraitOptions.mode, 'retrato');
    assert.equal(portraitOptions.portrait_style, 'suave');
    assert.equal(portraitOptions.brightness, 0); assert.equal(portraitOptions.contrast,0);
    assert.equal(portraitOptions.hatch_spacing,.35); assert.equal(conversions.at(-1).pen,1);
    assert.equal(+await page.locator('[data-k="hatch_spacing"]').getAttribute('step'), .05);
    assert.equal(+await page.locator('[data-k="hatch_spacing"]').evaluate(el=>el.value),.35);
    assert.ok(await page.locator('#portraitStyles').isVisible());
    assert.ok(!await page.locator('[data-opt="image.cross"]').isVisible());
    await page.screenshot({path:path.join(root,'tests/artifacts/retrato-editor-'+name+'.png'),fullPage:name==='movil'});
    await page.locator('[data-opt="image.portrait_style"] [data-v="rayado"]').click();
    assert.ok(await page.locator('[data-opt="image.cross"]').isVisible());
    await page.waitForTimeout(300);
    assert.equal(conversions.at(-1).opts.portrait_style,'rayado');
    await page.locator('#stipplingMode').click();
    await page.waitForFunction(() => document.querySelector('#busy').hidden && !document.querySelector('#send').disabled);
    assert.equal(conversions.at(-1).opts.mode,'puntillismo');
    assert.equal(conversions.at(-1).opts.dot_spacing,.7);
    assert.equal(conversions.at(-1).opts.shade,100);
    assert.ok(await page.locator('[data-k="dot_spacing"]').isVisible());
    assert.ok(!await page.locator('[data-k="hatch_spacing"]').isVisible());
    assert.ok(!await page.locator('[data-k="detail"]').isVisible());
    assert.ok(!await page.locator('#portraitStyles').isVisible());
    assert.equal(await page.locator('#stipplingMode').getAttribute('aria-pressed'),'true');
    // Filled dot paths must be painted by the actual editor, including cached redraw.
    const visibleDotFills=await page.evaluate(() => {
      window.__dotFills=0;const fill=CanvasRenderingContext2D.prototype.fill;
      CanvasRenderingContext2D.prototype.fill=function(...args){if(args[0] instanceof Path2D)window.__dotFills++;return fill.apply(this,args);};
      return window.__dotFills;
    });
    await page.locator('#zoomIn').click();
    assert.ok(await page.evaluate(() => window.__dotFills)>visibleDotFills);
    await page.locator('#centerView').click();
    await page.screenshot({path:path.join(root,'tests/artifacts/puntillismo-editor-'+name+'.png'),fullPage:name==='movil'});
    await page.locator('[data-photo-mode]').click();
    await page.waitForFunction(() => document.querySelector('#busy').hidden && !document.querySelector('#send').disabled);
    await page.screenshot({ path: path.join(root, 'tests/artifacts/foto-lineas-' + name + '.png'), fullPage: name === 'movil' });
    const designBefore = await page.evaluate(() => JSON.parse(localStorage.getItem('pluma-a1')).elements);
    await page.locator('#contactTest').click();
    await page.waitForFunction(() => document.querySelector('#contactTest').textContent === 'Volver al diseño');
    assert.equal(await page.locator('#contactTest').textContent(), 'Volver al diseño');
    await page.locator('#contactTest').click();
    await page.waitForFunction(() => !document.querySelector('#send').disabled);
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('pluma-a1')).elements), designBefore);
    assert.deepEqual(errors, []);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    motionSettingsSupported = false;
    await page.reload();
    await page.waitForFunction(() => !document.querySelector('#send').disabled);
    assert.ok(await page.locator('#openMotion').isDisabled());
    assert.ok(await page.locator('#portraitMode').isDisabled());
    assert.ok(await page.locator('#stipplingMode').isDisabled());
    assert.ok(await page.locator('#updateNotice').isVisible());
    console.log(name + ': rotación, medidas, recorte, estilos, guardado y conversión con progreso/cancelación verificados; sin impresora.');
    await context.close();
  }
} finally { await browser.close(); }
