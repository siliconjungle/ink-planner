// Offline measurement packet tags are distinct from application value ABIs.
// Invocation uses the same prebound typed full-observation API for every lane.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {performance} from 'node:perf_hooks';
if(process.argv[2]==='--describe'){
 const mod=await import(pathToFileURL(process.argv[3]));
 if(!mod.metadata?.state)throw Error('state baseline metadata missing');
 console.log(JSON.stringify({state:mod.metadata.state,named:mod.metadata.named_types}));
 process.exit(0);
}
const raw=fs.readFileSync(process.argv[2]);
if(raw.byteLength>64000000)throw Error('state measurement request byte limit');
const request=JSON.parse(raw),meta=request.metadata;
const artifacts=request.artifacts??[request.artifact],modules=new Map(),loads=new Map();
const offset=(request.process_index??0)%artifacts.length;
for(let i=0;i<artifacts.length;i++){const a=artifacts[(i+offset)%artifacts.length],started=performance.now();modules.set(a.measurement,await import(pathToFileURL(a.path)));loads.set(a.measurement,String(performance.now()-started));}
let transferNodes=0;
function unpack(v,d=0){
 if(d>128||++transferNodes>1000000||!Array.isArray(v))throw Error('measurement transfer budget');
 const [kind,x]=v;
 switch(kind){
  case 'null':return null;
  case 'bool':if(typeof x!=='boolean')throw Error('transfer Bool');return x;
  case 'string':if(typeof x!=='string')throw Error('transfer String');return x;
  case 'integer':if(typeof x!=='string'||x.length>4096||!/^[-]?[0-9]+$/.test(x))throw Error('transfer integer');return BigInt(x);
  case 'number':{const n=Number(x);if(!Number.isFinite(n))throw Error('transfer number');return n;}
  case 'array':return x.map(v=>unpack(v,d+1));
  case 'object':{const out={};for(const [k,v]of x)Object.defineProperty(out,k,{value:unpack(v,d+1),writable:true,enumerable:true,configurable:true});return out;}
  default:throw Error('measurement transfer kind');
 }
}
function resolve(t){return t.kind==='ref'?resolve(meta.named[t.name]):t;}
const bits=new DataView(new ArrayBuffer(4));
function input(v,descriptor){
 const t=resolve(descriptor);
 switch(t.kind){
  case 'u64':return BigInt(v);
  case 'u32':case 'i32':return Number(v);
  case 'int':return v&&typeof v==='object'?v:{Int:String(v)};
  case 'f32':return v&&typeof v==='object'?{F32Bits:Number(v.F32Bits)}:Number(v);
  case 'record':return Object.fromEntries(t.fields.map(f=>[f.name,input(v[f.name],f.type)]));
  case 'list':case 'vector':return v.map(x=>input(x,t.element));
  case 'option':return Object.hasOwn(v,'Some')?{Some:input(v.Some,t.element)}:{None:null};
  case 'result':return Object.hasOwn(v,'Ok')?{Ok:input(v.Ok,t.ok)}:{Err:input(v.Err,t.error)};
  default:return v;
 }
}
function value(v,descriptor){
 const t=resolve(descriptor);
 switch(t.kind){
  case 'u64':case 'u32':case 'i32':return String(v);
  case 'int':return String(v&&typeof v==='object'?v.Int:v);
  case 'f32':if(v&&typeof v==='object')return {bits:Number(v.F32Bits)};bits.setFloat32(0,Number(v),true);return {bits:bits.getUint32(0,true)};
  case 'record':return t.fields.map(f=>[f.name,value(v[f.name],f.type)]);
  case 'list':case 'vector':return v.map(x=>value(x,t.element));
  case 'option':return Object.hasOwn(v,'Some')?{Some:value(v.Some,t.element)}:{None:null};
  case 'result':return Object.hasOwn(v,'Ok')?{Ok:value(v.Ok,t.ok)}:{Err:value(v.Err,t.error)};
  default:return v;
 }
}
const actions=new Map(meta.state.actions.map(a=>[a.name,a])),eventTypes=new Map(meta.state.events.map(e=>[e.name,e.type]));
function outcome(reply,action){
 return {result:value(reply.result,action.result),committed:reply.committed,version:String(reply.version),
         events:reply.events.map(e=>({commit:String(e.commit),position:String(e.position),channel:e.channel,value:value(e.value,eventTypes.get(e.channel))}))};
}
function calls(rows){
 return rows.map(c=>{const a=actions.get(c.call);if(!a||c.args.length!==a.params.length)throw Error('state action arguments');
  return {action:a,args:c.args.map((v,i)=>input(v,a.params[i].type))};});
}
const setup=calls(unpack(request.setup)),seed=Buffer.from(request.seed,'hex'),expectedSetup=unpack(request.setup_expected);
const phases=Object.fromEntries(Object.entries(request.phases).map(([name,p])=>[name,{...p,calls:calls(p.calls?unpack(p.calls):[]),expected:unpack(p.expected),snapshot:Buffer.from(p.snapshot,'hex')}]));
const capturedInputs=structuredClone({setup:setup.map(c=>c.args),phases:Object.fromEntries(Object.entries(phases).map(([n,p])=>[n,p.calls.map(c=>c.args)]))});
let sink=0;
function consume(v){
 if(v===null||v===undefined)return;
 if(typeof v==='bigint'){sink^=Number(v&0xffffffffn);return;}
 if(typeof v==='number'){bits.setFloat32(0,v,true);sink^=bits.getUint32(0,true);return;}
 if(typeof v==='boolean'){sink^=Number(v);return;}
 if(typeof v==='string'){sink^=v.length;return;}
 for(const x of Object.values(v))consume(x);
}
function bindings(state){return new Map([...actions].map(([n])=>[n,state.bind(n)]));}
function history(bound,rows){for(const c of rows)consume(bound.get(c.action.name)(...c.args));}
async function validate(mod){
 assert.deepStrictEqual({setup:setup.map(c=>c.args),phases:Object.fromEntries(Object.entries(phases).map(([n,p])=>[n,p.calls.map(c=>c.args)]))},capturedInputs,'candidate mutated owned inputs');
 const state=mod.createState(),bound=bindings(state);
 for(let i=0;i<setup.length;i++)assert.deepStrictEqual(outcome(bound.get(setup[i].action.name)(...setup[i].args),setup[i].action),outcome(expectedSetup[i],setup[i].action));
 assert.deepStrictEqual(Buffer.from(await state.checkpoint()),seed,'setup portable snapshot');
 for(const p of Object.values(phases)){
  await state.restore(seed);
  for(let i=0;i<p.calls.length;i++)assert.deepStrictEqual(outcome(bound.get(p.calls[i].action.name)(...p.calls[i].args),p.calls[i].action),outcome(p.expected[setup.length+i],p.calls[i].action));
  assert.deepStrictEqual(Buffer.from(await state.checkpoint()),p.snapshot,'history portable snapshot');
 }
}
const rows=[],rejected=[],lanes=[];let peakRSS=0;
for(const a of artifacts){const mod=modules.get(a.measurement);try{await validate(mod);lanes.push({artifact:a,mod});}catch(e){rejected.push({measurement:a.measurement,reason:String(e.stack??e)});}}
if(!lanes.some(l=>l.artifact.measurement===artifacts[0].measurement))throw Error('state baseline failed validation');
for(const [phase,p]of Object.entries(phases)){
 const prepared=[];
 for(const lane of lanes){
  if(p.kind==='module_load'){rows.push({measurement:lane.artifact.measurement,phase,phase_kind:p.kind,complete_call_ns:[String(Math.max(1,Math.round(Number(loads.get(lane.artifact.measurement))*1e6)))],phase_calls:0,units_per_sample:1});continue;}
  const count=p.kind==='history'?16:4,mod=lane.mod;
  const state=mod.createState(),bound=bindings(state);await state.restore(seed);
  const measure=async()=>{
   let total=0;
   for(let i=0;i<count;i++){
    if(p.kind==='history')await state.restore(seed);
    const started=performance.now();
    if(p.kind==='history')history(bound,p.calls);
    else if(p.kind==='initialise'){const fresh=mod.createState();history(bindings(fresh),setup);consume(fresh.version);}
    else if(p.kind==='checkpoint')consume(await state.checkpoint());
    else if(p.kind==='restore'){await state.restore(seed);consume(state.version);}
    total+=performance.now()-started;
   }
   return String(Math.max(1,Math.round(total*1e6/count)));
  };
  prepared.push({...lane,measure,samples:[],count});
 }
 for(let warm=0;warm<2;warm++)for(const lane of prepared)await lane.measure();
 if(globalThis.gc)globalThis.gc();
 for(let round=0;round<9;round++){
  const order=(round+(request.process_index??0))%2?[...prepared].reverse():prepared;
  for(const lane of order)lane.samples.push(await lane.measure());
 }
 for(const lane of prepared)rows.push({measurement:lane.artifact.measurement,phase,phase_kind:p.kind,complete_call_ns:lane.samples,phase_calls:p.calls.length,units_per_sample:lane.count});
 peakRSS=Math.max(peakRSS,process.resourceUsage().maxRSS*1024);
}
for(const lane of lanes)await validate(lane.mod);
peakRSS=Math.max(peakRSS,process.resourceUsage().maxRSS*1024);
for(const row of rows)row.peak_rss_bytes=peakRSS;
console.log(JSON.stringify({schema:3,pairing:'interleaved-state-lifecycle-v1',node:process.version,rows,rejected,
 module_load:lanes.map(l=>({measurement:l.artifact.measurement,module_load_ms:loads.get(l.artifact.measurement)})),peak_rss_bytes:peakRSS,sink,
 scope:'Independent restored histories; setup/checkpoint/restore explicitly weighted; full observations; fresh process containing isolated candidate states; alternating paired rounds; shared-process total peak RSS is a conservative bound including all candidates'}));
