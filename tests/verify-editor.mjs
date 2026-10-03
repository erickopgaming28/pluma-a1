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
    let rendered = new Map();
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      const endpoint = url.pathname;
      let data;
      if (endpoint === '/api/state') data = fixture.state;
      else if (endpoint === '/api/printer/status') data = { configured: false, connected: false, state: 'IDLE', local_job: { active: false } };
      else if (endpoint === '/api/element') {
        const body = route.request().postDataJSON();
        conversions.push(body);
        const sample = structuredClone(body.type === 'image' ? imageFixtures[(body.opts.mode || 'boceto') + '-' + (body.opts.crop?.w === .5 ? 'middle' : 'full')] : fixture.text);
        const ratio = body.w / sample.w;
        if (body.type === 'image') {
          sample.h *= ratio;
          for (const l of sample.layers) l.paths = l.paths.map(p => p.map(v => v * ratio));
        }
        sample.w = body.w;
        sample.render_id += '-' + body.id;
        rendered.set(sample.render_id, sample);
        data = sample;
      } else if (endpoint === '/api/compose') {
        const body = route.request().postDataJSON(); compositions.push(body);
        data = structuredClone(fixture.job);
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
    assert.deepEqual(errors, []);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    console.log(name + ': rotación, medidas, recorte con ratón/teclado, estilos, guardado y restauración verificados; sin impresora.');
    await context.close();
  }
} finally { await browser.close(); }
