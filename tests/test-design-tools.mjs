import assert from 'node:assert/strict';
import {DesignHistory,validateDesignFile,fitPlacement} from '../static/design-tools.mjs';
import {rotatedBounds} from '../static/element-geometry.mjs';

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
console.log('Historial, ajuste con giro e importación validada: correctos.');
