// Offline UI verification. All HTTP requests are fulfilled by these fixtures.
// No application server, printer command or external network request is used.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLUMA_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = JSON.parse(await fs.readFile(path.join(root, 'tests/editor-fixture.json'), 'utf8'));
const artifactDir = path.join(root, 'tests/artifacts');
await fs.mkdir(artifactDir, { recursive: true });
const circuit = {
  paths: [[0,.5,.25,.5],[.25,.25,.65,.25,.65,.75,.25,.75,.25,.25],[.65,.5,1,.5],[.4,0,.4,.25],[.5,.75,.5,1]],
  aspect: .5,
  source: { name:'circuit.svg',format:'svg',unit_scale:1,original_width_mm:120,original_height_mm:60 },
  warnings: ['Solo se importan trazos; comprueba la escala del esquema.'],
};
const previewPNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==','base64');
const browser = await chromium.launch({headless:true,...(process.env.PLUMA_BROWSER_CHANNEL ? {channel:process.env.PLUMA_BROWSER_CHANNEL} : {})});

try {
  for (const [name,viewport] of [['escritorio',{width:1280,height:950}],['movil',{width:390,height:844}]]) {
    const context = await browser.newContext({viewport,acceptDownloads:true});
    const page = await context.newPage();
    const errors = [], calls = [], rendered = new Map();
    let renderNumber = 0;
    let holdCalibration=false, calibrationRequests=0, signalCalibrationStarted;
    const calibrationReleases=[];
    page.on('pageerror',error => errors.push(error.message));
    page.on('dialog',dialog => dialog.accept());
    function convert(body) {
      const h = body.type === 'vector' ? body.w * body.vector.aspect : body.type === 'image' ? body.w * .5 : 20;
      const paths = body.type === 'vector' ? body.vector.paths.map(p => p.map((v,i)=>v*body.w*(i%2 ? body.vector.aspect : 1)))
        : [[0,0,body.w,0,body.w,h,0,h,0,0]];
      const item = {render_id:`render-${++renderNumber}`,w:body.w,h,layers:[{name:'Azul',color:'#2743b8',pen:0,paths}]};
      rendered.set(item.render_id,item); return item;
    }
    await page.route('**/*',async route => {
      const request = route.request(), url = new URL(request.url()), endpoint=url.pathname;
      if (url.hostname !== 'pluma-measure-offline.test') {
        errors.push('Unexpected external request: '+url.href); return route.abort();
      }
      calls.push({endpoint,body:request.postData()});
      let data;
      if(endpoint==='/api/state') data={...structuredClone(fixture.state),app_version:'2026.10.04.21',features:{async_images:false,motion_settings:true,extended_images:true,paper_layout:true,portrait:true,stippling:true,local_ai:true,vector_import:true,pdf_import:true}};
      else if(endpoint==='/api/printer/status') data={configured:false,connected:false,state:'IDLE',local_job:{active:false}};
      else if(endpoint==='/api/ai/models') data={available:false,models:[],message:'Prueba sin IA local.'};
      else if(endpoint==='/api/element') {
        const body=request.postDataJSON();
        if(holdCalibration && body.type==='vector') {
          calibrationRequests++;
          signalCalibrationStarted?.();
          await new Promise(resolve=>calibrationReleases.push(resolve));
        }
        data=convert(body);
      }
      else if(endpoint==='/api/compose') {
        data=structuredClone(fixture.job);data.geometry=structuredClone(fixture.state.geometry);
        const body=request.postDataJSON();
        if(!body.items.length)data.pages[0].layers=[];
        // Measurement overlays must never be sent as printable items or paths.
        assert.ok(!Object.hasOwn(body,'measurements'));
        assert.ok(body.items.every(item=>rendered.has(item.render_id)));
      }
      else if(endpoint==='/api/import-vector') {
        const body=request.postData() || '', scale=Number(body.match(/name="scale"\r\n\r\n([^\r]+)/)?.[1] || 1);
        const extension=body.match(/filename="[^".]+\.([^".]+)"/)?.[1] || 'svg';
        const vector=structuredClone(circuit);vector.source.format=extension;
        vector.source.name=extension==='stl' ? 'model.stl' : 'circuit.svg';
        vector.source.unit_scale=scale;vector.source.original_width_mm=120*scale;vector.source.original_height_mm=60*scale;
        data={type:'vector',vector,w:120*scale,h:60*scale,segment_count:8,warnings:vector.warnings};
      }
      else if(endpoint==='/api/pdf/upload') {
        const body=request.postData() || '', selected=Number(body.match(/name="page"\r\n\r\n([^\r]+)/)?.[1] || 1);
        data={id:'pdf-image',width:1600,height:800,width_mm:100,height_mm:50,pages:3,page:selected,warnings:[`Se importó la página ${selected} de 3 como imagen.`]};
      }
      else if(endpoint==='/api/image/upload') data={id:'restored-image',width:1600,height:800};
      else if(/^\/api\/image\/[^/]+\/preview$/.test(endpoint)) return route.fulfill({body:previewPNG,contentType:'image/png'});
      else if(endpoint.startsWith('/api/')) {
        errors.push('Unexpected API call: '+endpoint); return route.fulfill({status:400,json:{error:'Solicitud no prevista en la prueba.'}});
      }
      if(data)return route.fulfill({json:data});
      const relative=endpoint==='/' ? 'index.html' : endpoint.slice(1),extension=path.extname(relative);
      const types={'.html':'text/html','.css':'text/css','.js':'text/javascript','.mjs':'text/javascript','.png':'image/png'};
      try {return route.fulfill({body:await fs.readFile(path.join(root,'static',relative)),contentType:types[extension] || 'application/octet-stream'});}
      catch{return route.fulfill({status:404,body:''});}
    });
    await page.addInitScript(() => {
      if(!localStorage.getItem('pluma-a1'))localStorage.setItem('pluma-a1',JSON.stringify({name:'Regla y circuitos',sel:1,nextId:2,measurements:[],elements:[
        {id:1,type:'text',x:30,y:70,w:80,rotation:0,text:'Circuito de prueba',opts:{font:'EMSAllure',size:8,human:0,align:'left',join:true,seed:1,line_spacing:1.15,letter_spacing:0,slant:0},colorMode:'single',pen:0}
      ]}));
    });
    await page.goto('http://pluma-measure-offline.test/');
    await page.waitForFunction(()=>!document.querySelector('#send').disabled);
    const state=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('pluma-a1')));
    const count=async expected=>assert.equal(Number(await page.locator('#measureCount').textContent()),expected);
    const result=async(expected,unit='mm')=>assert.equal(await page.locator('#measureResult').textContent(),`${expected} ${unit}`);
    const advanced=page.locator('.measure-advanced');
    async function coordinates(a,b,mode='free') {
      await page.locator('#measureMode').selectOption(mode);
      if(!await advanced.evaluate(el=>el.open))await advanced.locator('summary').click();
      for(const [id,value] of [['measureAX',a[0]],['measureAY',a[1]],['measureBX',b[0]],['measureBY',b[1]]])await page.locator('#'+id).fill(String(value));
      await page.locator('#measureCoordinates button[type="submit"]').click();
    }
    async function point(x,y,click=true) {
      await page.locator('#cv').scrollIntoViewIfNeeded();
      const frame=await page.locator('.canvas-wrap').boundingBox(), canvas=await page.locator('#cv').boundingBox();
      const zoom=parseFloat(await page.locator('#zoomValue').textContent())/100;
      const [pw,ph]=fixture.state.geometry.paper,k=Math.min((frame.width-48)/pw,(frame.height-36)/ph)*zoom;
      const ox=(frame.width-pw*k)/2,oy=(frame.height-ph*k)/2;
      await page.mouse[click ? 'click' : 'move'](canvas.x+ox+x*k,canvas.y+oy+y*k);
    }
    await page.locator('#measureTool').click();
    assert.equal(await page.locator('#measureTool').getAttribute('aria-pressed'),'true');
    await page.locator('#measureSnap').uncheck();
    await point(20,30);await point(50,70);await count(1);
    assert.ok(Math.abs(parseFloat(await page.locator('#measureResult').textContent())-50)<.2);
    const clicked=(await state()).measurements[0];
    assert.ok(Math.abs(clicked.start[0]-20)<.1 && Math.abs(clicked.end[1]-70)<.1);
    await coordinates([10,10],[40,50]);await result('50');await count(2);
    await coordinates([10,10],[40,50],'horizontal');await result('30');
    await coordinates([10,10],[40,50],'vertical');await result('40');await count(4);
    await page.locator('#measureUnit').selectOption('cm');await result('4','cm');
    await page.locator('#measureUnit').selectOption('in');await result('1.575','in');
    await page.locator('#measureUnit').selectOption('mm');await result('40');
    const beforeZoom=JSON.stringify((await state()).measurements);
    await page.locator('#zoomIn').click();await result('40');
    assert.equal(JSON.stringify((await state()).measurements),beforeZoom);
    await page.locator('#centerView').click();
    await page.locator('#rotationControls summary').click();await page.locator('#rotateRight').click();
    await page.locator('#measureWidth').click();await result('80');
    await page.locator('#measureHeight').click();await result('20');
    const rotated=(await state()).measurements.at(-2);
    assert.ok(Math.abs(rotated.start[0]-rotated.end[0])<1e-8);
    await page.locator('#savedMeasurements summary').click();
    await page.locator('#clearMeasures').click();await count(0);
    await page.locator('#undoDesign').click();await count(6);
    await page.locator('#redoDesign').click();await count(0);
    await coordinates([20,30],[50,70]);await result('50');
    await page.reload();await page.waitForFunction(()=>!document.querySelector('#send').disabled);await count(1);
    assert.equal((await state()).measurements[0].mode,'free');

    await page.locator('#addPlan').click();
    await page.locator('#planFile').setInputFiles({name:'circuit.svg',mimeType:'image/svg+xml',buffer:Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L120 60"/></svg>')});
    assert.equal(await page.locator('#planScaleLabel').textContent(),'Ajuste de tamaño');
    assert.match(await page.locator('#planScale option[value="1"]').textContent(),/Tamaño leído/);
    await page.locator('#planPlacement').selectOption('native');await page.locator('#importPlan').click();
    await page.waitForFunction(()=>JSON.parse(localStorage.getItem('pluma-a1')).elements.some(e=>e.type==='vector'));
    await page.waitForFunction(()=>document.querySelector('#busy').hidden);
    let imported=(await state()).elements.find(e=>e.type==='vector');assert.equal(imported.w,120);assert.equal(imported.vector.aspect,.5);
    assert.ok(await page.locator('#tab-vector').isVisible());
    await page.locator('#measureTool').click();
    await page.locator('#measureSnap').check();await page.locator('#cancelMeasure').click();
    await point(imported.x+.4,imported.y+30+.4);
    await point(imported.x+119.6,imported.y+30-.4,false);
    assert.ok(await page.locator('#calibrateScale').isDisabled(),'Una medida provisional no debe habilitar la calibración.');
    await point(imported.x+119.6,imported.y+30-.4);
    await result('120');
    const snapped=(await state()).measurements.at(-1);
    assert.deepEqual(snapped.start,[imported.x,imported.y+30]);
    assert.deepEqual(snapped.end,[imported.x+120,imported.y+30]);
    await page.locator('#measureWidth').click();await result('120');
    if(!await advanced.evaluate(el=>el.open))await advanced.locator('summary').click();
    await page.locator('#knownDistance').fill('0');await page.locator('#calibrateScale').click();
    assert.equal((await state()).elements.find(e=>e.type==='vector').w,120);
    assert.ok((await page.locator('#toast').textContent()).includes('mayor que cero'));
    await page.locator('#knownDistance').fill('60');
    holdCalibration=true;
    const calibrationStarted=new Promise(resolve=>signalCalibrationStarted=resolve);
    await page.locator('#calibrateScale').dblclick({delay:10});await calibrationStarted;
    assert.ok(await page.locator('#calibrateScale').isDisabled());
    assert.equal((await state()).elements.find(e=>e.type==='vector').w,60);
    // A queued duplicate click must also be ignored while native conversion is pending.
    await page.locator('#calibrateScale').dispatchEvent('click');
    assert.equal((await state()).elements.find(e=>e.type==='vector').w,60);
    assert.equal(calibrationRequests,1);
    holdCalibration=false;calibrationReleases.splice(0).forEach(resolve=>resolve());
    await page.waitForFunction(()=>JSON.parse(localStorage.getItem('pluma-a1')).elements.find(e=>e.type==='vector').w===60);
    await page.waitForFunction(()=>document.querySelector('#busy').hidden);
    await page.locator('#measureWidth').click();await result('60');
    const beforeFile=await state(),downloadEvent=page.waitForEvent('download');
    await page.locator('#saveDesign').click();const download=await downloadEvent;
    const design=JSON.parse(await fs.readFile(await download.path(),'utf8'));
    assert.deepEqual(design.measurements,beforeFile.measurements);
    assert.deepEqual(design.elements.find(e=>e.type==='vector').vector,imported.vector);
    await page.locator('#newDesign').click();await count(0);
    await page.locator('#projectFile').setInputFiles({name:'regla.pluma.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(design))});
    await page.waitForFunction(()=>JSON.parse(localStorage.getItem('pluma-a1')).elements.some(e=>e.type==='vector'));
    assert.deepEqual((await state()).measurements,design.measurements);
    assert.deepEqual((await state()).elements.find(e=>e.type==='vector').vector,design.elements.find(e=>e.type==='vector').vector);

    await page.locator('#planFile').setInputFiles({name:'model.stl',mimeType:'application/octet-stream',buffer:Buffer.from('solid test\nendsolid test')});
    assert.equal(await page.locator('#planScaleLabel').textContent(),'Unidad del STL');
    assert.equal(await page.locator('#planScale option[value="10"]').textContent(),'1 unidad = 1 cm');
    assert.ok(await page.locator('#planProjection').isVisible());assert.ok(await page.locator('#planEdges').isVisible());
    await page.locator('#planProjection').selectOption('xz');await page.locator('#planEdges').selectOption('features');
    await page.locator('#planScale').selectOption('10');await page.locator('#planPlacement').selectOption('fit');
    await page.locator('#importPlan').click();
    await page.waitForFunction(()=>JSON.parse(localStorage.getItem('pluma-a1')).elements.filter(e=>e.type==='vector').length===2);
    const stl=(await state()).elements.filter(e=>e.type==='vector').at(-1);
    assert.ok(stl.w<=124+.0001);assert.equal(stl.vector.source.unit_scale,10);
    const stlRequest=calls.filter(call=>call.endpoint==='/api/import-vector').at(-1).body;
    assert.match(stlRequest,/name="projection"\r\n\r\nxz/);assert.match(stlRequest,/name="edge_mode"\r\n\r\nfeatures/);

    await page.locator('#planFile').setInputFiles({name:'circuit.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.7\nOffline route fixture')});
    assert.ok(await page.locator('#planPage').isVisible());assert.ok(!await page.locator('#planProjection').isVisible());
    await page.locator('#planPage').fill('2');await page.locator('#planPlacement').selectOption('native');await page.locator('#importPlan').click();
    await page.waitForFunction(()=>JSON.parse(localStorage.getItem('pluma-a1')).elements.some(e=>e.type==='image'));
    const pdf=(await state()).elements.find(e=>e.type==='image');assert.equal(pdf.w,100);
    assert.match(calls.filter(call=>call.endpoint==='/api/pdf/upload').at(-1).body,/name="page"\r\n\r\n2/);
    assert.ok((await page.locator('#planImportStatus').textContent()).includes('2'));
    if(await page.locator('#measureTool').getAttribute('aria-pressed')!=='true')await page.locator('#measureTool').click();
    await page.locator('#measureWidth').click();await result('100');
    await page.locator('#measureSnap').check();
    await page.locator('#cancelMeasure').click();
    await page.locator('#savedMeasurements').evaluate(el=>el.open=true);
    await page.waitForFunction(()=>document.querySelector('#busy').hidden);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    const visibleCanvas=await page.locator('#cv').boundingBox();
    assert.ok(visibleCanvas.width>=250 && visibleCanvas.height>=300,
      `La regla debe conservar una hoja visible (${visibleCanvas.width} × ${visibleCanvas.height} px).`);
    const summary=await page.locator('#savedMeasurements summary').boundingBox();
    const guideLabel=await page.locator('#measureGuides').evaluate(el=>el.closest('label').getBoundingClientRect().height);
    assert.ok(summary.height>=44 && guideLabel>=44,'Los controles de medidas guardadas deben conservar áreas táctiles de al menos 44 px.');
    assert.equal(errors.length,0,errors.join('\n'));
    const printerCalls=calls.filter(call=>call.endpoint.startsWith('/api/printer/'));
    assert.ok(printerCalls.every(call=>call.endpoint==='/api/printer/status'));
    // Capture the complete integrated state only after assertions pass.
    if(name==='escritorio')await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:path.join(artifactDir,'regla-importacion-'+name+'.png'),fullPage:true});
    console.log(`Regla/importación ${name}: puntos, ejes, unidades, rotación, escala, historial, archivos, STL y PDF verificados.`);
    await context.close();
  }
} finally {await browser.close();}
