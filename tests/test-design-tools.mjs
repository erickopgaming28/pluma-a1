import assert from 'node:assert/strict';
import {DesignHistory,validateDesignFile,fitPlacement} from '../static/design-tools.mjs';
import {rotatedBounds} from '../static/element-geometry.mjs';
import {validateVector} from '../static/vector-data.mjs';

const text={id:1,type:'text',text:'Hola',x:20,y:30,w:80,opts:{font:'EMSAllure',size:8},pen:0,colorMode:'single'};
const state={name:'Prueba',elements:[structuredClone(text)],sel:1,nextId:2};
const history=new DesignHistory(4);history.reset(state);
const selected={...state,sel:null};assert.equal(history.record(selected),false);
state.elements[0].x=25;history.record(state);state.elements[0].text='Cambio';history.record(state);
const undo=history.undo();assert.equal(undo.elements[0].text,'Hola');assert.equal(undo.elements[0].x,25);
undo.elements[0].x=999;assert.equal(history.current.elements[0].x,25);
assert.equal(history.redo().elements[0].text,'Cambio');
history.undo();history.record({...state,elements:[]});assert.equal(history.canRedo,false);
for(let i=0;i<12;i++)history.record({...state,name:'Diseño '+i});
assert.equal(history.entries.length,4);

const area=[10,30,138,200];
for(const angle of [0,37,90,-113,180]) {
  const b={x:-50,y:-90,w:300,h:180};const target=fitPlacement(b,angle,area);
  const bounds=rotatedBounds({...target,h:b.h*target.scale},angle);
  assert.ok(bounds.x>=area[0]-1e-8 && bounds.y>=area[1]-1e-8);
  assert.ok(bounds.x+bounds.w<=area[2]+1e-8 && bounds.y+bounds.h<=area[3]+1e-8);
}
assert.equal(fitPlacement({x:10,y:40,w:20,h:30},37,area).scale,1);

const file={format:'pluma-a1.design',version:1,name:'Ejemplo',elements:[text],sel:1,images:{}};
assert.equal(validateDesignFile(file).elements[0].text,'Hola');
assert.equal(validateDesignFile({...file,elements:[]}).nextId,1);
for(const data of [{...file,version:999},{...file,elements:[text,text]},
  {...file,elements:[{...text,w:Infinity}]},{...file,elements:[{...text,opts:{size:Infinity}}]},
  {...file,elements:[{...text,type:'image',imageId:'missing'}]},
  {...file,elements:[{...text,opts:{crop:{x:.9,y:0,w:.5,h:1}}}]}])assert.throws(()=>validateDesignFile(data));
const image={...text,type:'image',imageId:'offline',opts:{mode:'puntillismo',dot_spacing:.7},original:{x:2,y:3,w:50,rotation:12,pen:0,colorMode:'single'}};
assert.equal(validateDesignFile({...file,elements:[image],images:{offline:'data:image/png;base64,YWJj'}}).elements[0].original.rotation,12);

// A real-size small vector and world-space dimensions survive project reopening and undo.
const vector = {id:2,type:'vector',x:25,y:40,w:.1,rotation:37,pen:0,colorMode:'multi',opts:{},
  vector:{paths:[[0,0,1,1]],aspect:.5,source:{name:'circuit.svg',format:'svg',unit_scale:1,original_width_mm:.1,extra:'discard'},
    warnings:['El esquema contiene elementos no compatibles.']}};
const measurement = {start:[20,30],end:[50,70],mode:'horizontal'};
const project = validateDesignFile({...file,elements:[vector],measurements:[measurement],sel:2});
assert.equal(project.elements[0].w,.1); assert.equal(project.elements[0].vector.aspect,.5);
assert.equal(project.elements[0].colorMode,'single'); assert.equal(project.elements[0].vector.source.extra,undefined);
assert.deepEqual(project.elements[0].vector.paths,vector.vector.paths);
assert.deepEqual(project.elements[0].vector.warnings,vector.vector.warnings);
assert.deepEqual(project.measurements,[measurement]);
assert.deepEqual(validateDesignFile(file).measurements,[]); // Existing projects need no migration.
project.elements[0].vector.paths[0][0]=.2;
project.measurements[0].start[0]=25;
assert.equal(vector.vector.paths[0][0],0); assert.equal(measurement.start[0],20);

const portable = validateDesignFile({...file,elements:[vector],measurements:[measurement],sel:2});
const dimensionsHistory = new DesignHistory(); dimensionsHistory.reset(portable);
dimensionsHistory.record({...portable,measurements:[]});
assert.deepEqual(dimensionsHistory.undo().measurements,[measurement]);
assert.deepEqual(dimensionsHistory.redo().measurements,[]);
dimensionsHistory.undo();
const resized=structuredClone(portable);resized.elements[0].w=.2;dimensionsHistory.record(resized);
assert.equal(dimensionsHistory.canRedo,false);
assert.equal(dimensionsHistory.undo().elements[0].w,.1);
assert.deepEqual(dimensionsHistory.current.measurements,[measurement]);
assert.throws(()=>validateDesignFile({...file,elements:[{...vector,w:.099}]}));
assert.throws(()=>validateDesignFile({...file,elements:[{...vector,vector:{...vector.vector,paths:[[0,0,1,Infinity]]}}]}));
assert.throws(()=>validateDesignFile({...file,elements:[vector],measurements:[{...measurement,end:[NaN,70]}]}));
// Rejected project geometry matches the renderer's actual limits, before replacing a design.
assert.equal(validateVector({...vector.vector,paths:Array.from({length:30000},()=>[0,0,1,1])}).paths.length,30000);
assert.throws(()=>validateVector({...vector.vector,paths:Array.from({length:30001},()=>[0,0,1,1])}));
assert.equal(validateVector({...vector.vector,paths:[Array(240000).fill(0)]}).paths[0].length,240000);
assert.throws(()=>validateVector({...vector.vector,paths:[Array(240002).fill(0)]}));
for(const aspect of [0,.000001,100001,Infinity])assert.throws(()=>validateVector({...vector.vector,aspect}));
assert.throws(()=>validateVector({...vector.vector,paths:[[-.0000001,0,1,1]]}));
assert.throws(()=>validateVector({...vector.vector,paths:[[0,0,1.0000001,1]]}));
assert.equal(validateDesignFile({...file,elements:[{...vector,vector:{...vector.vector,aspect:20000}}]}).elements[0].w,.1);
assert.throws(()=>validateDesignFile({...file,elements:[{...vector,vector:{...vector.vector,aspect:20001}}]}));
console.log('Historial, ajuste con giro, proyectos vectoriales y medidas portables: correctos.');
