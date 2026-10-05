// Layout regression checks use workspace assets and offline API fixtures only.
// They never connect to a printer or start a hardware command.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLUMA_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = JSON.parse(await fs.readFile(path.join(root, 'tests/editor-fixture.json'), 'utf8'));
const appVersion=(await fs.readFile(path.join(root,'app.py'),'utf8')).match(/^APP_VERSION\s*=\s*['"]([^'"]+)['"]/m)?.[1];
assert.ok(appVersion,'La captura debe usar la versión real de app.py.');
const captureOnly=process.argv.includes('--capture-only');
const artifacts = path.join(root, '.impeccable', 'review');
await fs.mkdir(artifacts, {recursive:true});
const browser = await chromium.launch({headless:true,...(process.env.PLUMA_BROWSER_CHANNEL ? {channel:process.env.PLUMA_BROWSER_CHANNEL} : {})});
const variants = [
  ['amplio',{width:1920,height:1080}],
  ['usuario',{width:1919,height:913}],
  ['portatil',{width:1440,height:900}],
  ['compacto',{width:1280,height:950}],
  ['tablet',{width:1024,height:768}],
  ['movil',{width:390,height:844}],
  ['pantalla-grande',{width:2560,height:1440}],
];
const failures=[];
const box = async (page,selector) => {
  const bounds=await page.locator(selector).boundingBox();
  return bounds && {...bounds,y:bounds.y+await page.evaluate(()=>scrollY)};
};
const stableSheet = (a,b,message) => {
  assert.ok(Math.abs(a.height-b.height)<2,message+` (${a.height} → ${b.height} px)`);
  assert.ok(Math.abs(a.y-b.y)<2,message+` (Y ${a.y} → ${b.y} px)`);
};
async function noOverflow(page,name) {
  const state = await page.evaluate(() => ({width:innerWidth,doc:document.documentElement.scrollWidth,body:document.body.scrollWidth}));
  assert.ok(state.doc<=state.width && state.body<=state.width,`${name}: la vista no debe desbordarse horizontalmente: ${JSON.stringify(state)}`);
}
async function settled(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

try {
  for(const [name,viewport] of variants.filter(([name])=>!captureOnly || ['usuario','movil'].includes(name))) {
    const context = await browser.newContext({viewport});
    const page = await context.newPage(),errors=[],calls=[];
    try {
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',async route=> {
      const request=route.request(),url=new URL(request.url()),endpoint=url.pathname;
      if(url.hostname!=='pluma-layout-offline.test') {
        errors.push('Unexpected external request: '+url.href);return route.abort();
      }
      calls.push({endpoint,method:request.method()});
      let data;
      if(endpoint==='/api/state') data={...structuredClone(fixture.state),app_version:appVersion,features:{async_images:false,motion_settings:true,extended_images:true,paper_layout:true,portrait:true,stippling:true,local_ai:true,vector_import:true,pdf_import:true}};
      else if(endpoint==='/api/printer/status') data={configured:false,connected:false,state:'IDLE',local_job:{active:false}};
      else if(endpoint==='/api/ai/models') data={available:false,models:[],message:'Prueba sin IA local.'};
      else if(endpoint==='/api/compose') {
        data=structuredClone(fixture.job);data.pages[0].layers=[];
      }
      else if(endpoint.startsWith('/api/')) {
        errors.push('Unexpected API call: '+endpoint);return route.fulfill({status:400,json:{error:'Solicitud no prevista en la prueba.'}});
      }
      if(data)return route.fulfill({json:data});
      const relative=endpoint==='/' ? 'index.html' : endpoint.slice(1);
      const types={'.html':'text/html','.css':'text/css','.js':'text/javascript','.mjs':'text/javascript','.png':'image/png'};
      try {return route.fulfill({body:await fs.readFile(path.join(root,'static',relative)),contentType:types[path.extname(relative)] || 'application/octet-stream'});}
      catch{return route.fulfill({status:404,body:''});}
    });
    await page.addInitScript(() => {
      if(!localStorage.getItem('pluma-a1'))localStorage.setItem('pluma-a1',JSON.stringify({name:'Mi diseño',sel:null,nextId:1,measurements:[],elements:[]}));
    });
    await page.goto('http://pluma-layout-offline.test/');
    await page.waitForFunction(()=>document.querySelector('#elementCount').textContent==='0' && document.querySelector('#busy').hidden && !!document.querySelector('#paperInfo').textContent);
    await settled(page);
    if(captureOnly) {
      await page.locator('#measureTool').click();
      await page.locator('.measure-advanced summary').click();
      await page.locator('#measureMode').selectOption(name==='usuario' ? 'horizontal' : 'free');
      for(const [id,value] of [['measureAX',20],['measureAY',30],['measureBX',name==='usuario' ? 120 : 50],['measureBY',name==='usuario' ? 30 : 70]])await page.locator('#'+id).fill(String(value));
      await page.locator('#measureCoordinates button[type="submit"]').click();
      await page.locator('#savedMeasurements').evaluate(el=>el.open=true);
      await page.locator('.measure-advanced').evaluate(el=>el.open=false);
      await page.locator('#measurementInspector').evaluate(el=>el.scrollTop=0);
      await page.evaluate(()=>scrollTo(0,0));await settled(page);
      const file=name==='usuario' ? 'user-1919.png' : 'distribucion-movil-una-medida.png';
      await page.screenshot({path:path.join(artifacts,file),fullPage:true});
      console.log(`Captura actualizada: ${file}; versión ${appVersion}.`);
      continue;
    }
    await noOverflow(page,name+' cerrada');
    const inspector=page.locator('#measurementInspector');
    assert.ok(!await inspector.isVisible(),name+': el inspector debe dejar espacio libre al empezar.');
    const closed=await box(page,'#cv');
    assert.ok(closed.width>=250 && closed.height>=360,name+': la hoja necesita espacio para trabajar.');
    assert.ok(await page.locator('#emptyDesign').isVisible());
    if(name==='amplio')await page.screenshot({path:path.join(artifacts,'distribucion-sin-regla.png'),fullPage:true});
    await page.locator('#measureTool').click();await settled(page);
    assert.equal(await page.locator('#measureTool').getAttribute('aria-pressed'),'true');
    assert.ok(await inspector.isVisible());
    const opened=await box(page,'#cv'),rail=await box(page,'#measurementInspector');
    if(viewport.width>=1180) {
      assert.ok(opened.height>=450,name+': abrir la regla no debe reducir la hoja a una franja.');
      assert.ok(rail.width>=320 && rail.width<=360,name+': el inspector necesita una columna legible.');
      assert.ok(rail.x>=opened.x+opened.width-2,name+': la regla debe ir al costado de la hoja.');
      assert.ok(opened.width>=500,name+': la hoja debe conservar al menos 500 px de ancho.');
    } else {
      assert.ok(rail.y>=opened.y+opened.height-2,name+': en pantallas pequeñas la regla debe seguir a la hoja.');
    }
    stableSheet(closed,opened,name+': abrir la regla debe conservar la altura de trabajo');
    const beforeDetails=await box(page,'#cv');
    await page.locator('.measure-advanced summary').click();
    await page.locator('#savedMeasurements summary').click();await settled(page);
    const afterDetails=await box(page,'#cv');
    stableSheet(beforeDetails,afterDetails,name+': los detalles deben crecer dentro del inspector');
    await page.locator('#measureAX').fill('20');await page.locator('#measureAY').fill('30');
    await page.locator('#measureBX').fill('50');await page.locator('#measureBY').fill('70');
    await page.locator('#measureCoordinates button[type="submit"]').click();
    assert.equal(await page.locator('#measureResult').textContent(),'50 mm');
    assert.equal(await page.locator('#measureCount').textContent(),'1');
    await noOverflow(page,name+' avanzada');
    if(['amplio','movil','usuario'].includes(name)) {
      if(name==='usuario') {
        await page.locator('#measureMode').selectOption('horizontal');
        await page.locator('#measureBX').fill('120');await page.locator('#measureBY').fill('30');
        await page.locator('#measureCoordinates button[type="submit"]').click();
        await page.locator('#measureList li').first().getByRole('button').click();
        assert.equal(await page.locator('#measureCount').textContent(),'1');
        assert.equal(await page.locator('#measureResult').textContent(),'100 mm');
      }
      await page.locator('.measure-advanced').evaluate(el=>el.open=false);
      await inspector.evaluate(el=>el.scrollTop=0);await settled(page);
      await page.evaluate(()=>scrollTo(0,0));
      await page.screenshot({path:path.join(artifacts,name==='usuario' ? 'user-1919.png' : 'distribucion-'+name+'-una-medida.png'),fullPage:true});
    }

    // Dense saved lists must scroll without stealing space from the sheet.
    await page.evaluate(()=> {
      const design=JSON.parse(localStorage.getItem('pluma-a1'));
      design.measurements=Array.from({length:24},(_,i)=>({start:[20,30+i*4],end:[70,30+i*4],mode:'horizontal'}));
      localStorage.setItem('pluma-a1',JSON.stringify(design));
    });
    await page.reload();await page.waitForFunction(()=>document.querySelector('#measureCount').textContent==='24');
    await settled(page);
    assert.ok(!await inspector.isVisible(),name+': cargar medidas debe conservar el espacio de dibujo.');
    const listButton=page.locator('#showMeasurements');
    assert.ok(await listButton.isVisible(),name+': las medidas guardadas requieren un acceso visible.');
    await listButton.click();await settled(page);
    assert.ok(await inspector.isVisible());
    assert.ok(await page.locator('#measureGuides').isVisible(),name+': mostrar guías debe estar disponible sin activar la regla.');
    assert.equal(await page.locator('#measureTool').getAttribute('aria-pressed'),'false');
    assert.equal(await page.locator('#measureList li').count(),24);
    const scrolling=await page.locator('#measureList').evaluate(el=>({scroll:el.scrollHeight,height:el.clientHeight,overflow:getComputedStyle(el).overflowY}));
    assert.ok(scrolling.scroll>scrolling.height && ['auto','scroll'].includes(scrolling.overflow),name+': las medidas extensas deben tener desplazamiento propio.');
    const summary=await box(page,'#savedMeasurements summary');
    const guideHeight=await page.locator('#measureGuides').evaluate(el=>el.closest('label').getBoundingClientRect().height);
    assert.ok(summary.height>=44 && guideHeight>=44,name+': los controles de la lista deben conservar áreas táctiles.');
    await page.locator('#closeInspector').click();await settled(page);
    assert.ok(!await inspector.isVisible());
    assert.equal(await page.locator('#measureTool').evaluate(el=>el===document.activeElement),true,name+': cerrar las herramientas debe devolver el foco a Regla.');
    await page.locator('#editMargins').click();await settled(page);
    assert.ok(await inspector.isVisible());
    assert.ok(await page.locator('#marginTools').isVisible());
    assert.ok(!await page.locator('#measurementTools').isVisible());
    assert.ok(await page.locator('#marginLeft').isEnabled());
    assert.ok(await page.locator('#marginRight').isEnabled());
    stableSheet(opened,await box(page,'#cv'),name+': editar márgenes debe conservar la hoja');
    await page.locator('#showMeasurements').click();
    assert.ok(!await page.locator('#savedMeasurements').evaluate(el=>el.open));
    await page.locator('#showMeasurements').click();
    assert.ok(await page.locator('#measureGuides').isVisible(),name+': la lista debe permanecer disponible al editar márgenes.');
    await page.locator('#closeInspector').click();await settled(page);
    assert.ok(!await inspector.isVisible());
    assert.equal(await page.locator('#editMargins').getAttribute('aria-pressed'),'false');
    await page.locator('#measureTool').click();await settled(page);
    await page.locator('.measure-advanced').evaluate(el=>el.open=true);
    await page.locator('#savedMeasurements').evaluate(el=>el.open=true);await settled(page);
    const dense=await box(page,'#cv');
    stableSheet(opened,dense,name+': regla y lista extensa deben conservar la hoja');
    if(viewport.width>=1180) {
      const scrollState=await inspector.evaluate(el=>({scroll:el.scrollHeight,height:el.clientHeight,overflow:getComputedStyle(el).overflowY}));
      assert.ok(['auto','scroll'].includes(scrollState.overflow),name+': las opciones avanzadas deben permitir desplazarse dentro de su columna.');
      if(scrollState.scroll>scrollState.height) {
        await inspector.evaluate(el=>el.scrollTop=el.scrollHeight);
        assert.ok(await inspector.evaluate(el=>el.scrollTop>0),name+': el contenido extenso debe desplazarse en su columna.');
        stableSheet(dense,await box(page,'#cv'),name+': desplazar la columna no debe mover la hoja');
        await inspector.evaluate(el=>el.scrollTop=0);
      }
      const pageState=await page.evaluate(()=>({height:innerHeight,doc:document.documentElement.scrollHeight,outside:[...document.querySelectorAll('body *')].map(el=>{const b=el.getBoundingClientRect();return {tag:el.tagName,id:el.id,class:el.className,bottom:Math.round(b.bottom),height:Math.round(b.height),position:getComputedStyle(el).position};}).filter(el=>el.height>0 && el.bottom>innerHeight+2).slice(-8)}));
      assert.ok(pageState.doc<=pageState.height+2,name+': el escritorio debe caber sin alargar toda la página: '+JSON.stringify(pageState));
    }
    await noOverflow(page,name+' lista extensa');
    assert.equal(errors.length,0,errors.join('\n'));
    assert.ok(calls.filter(call=>call.endpoint.startsWith('/api/printer/')).every(call=>call.endpoint==='/api/printer/status'));
    await page.locator('.measure-advanced').evaluate(el=>el.open=false);
    await inspector.evaluate(el=>el.scrollTop=0);await settled(page);
    await page.evaluate(()=>scrollTo(0,0));
    if(name!=='usuario')await page.screenshot({path:path.join(artifacts,'distribucion-'+name+'.png'),fullPage:true});
    console.log(`Distribución ${name}: hoja ${Math.round(dense.width)} × ${Math.round(dense.height)} px; regla, coordenadas y 24 medidas sin desbordamiento.`);
    } catch(error) {
      failures.push(`${name}: ${error.message}`);
      console.error(`${name}: ${error.message}`);
    } finally {await context.close();}
  }
  assert.deepEqual(failures,[],failures.join('\n'));
} finally {await browser.close();}
