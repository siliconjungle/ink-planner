// Explicit offline complete-call controls. Receipts never grant rewrite authority.
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {browserHost} from './browser_host.mjs';

const [browser,requestPath,sealed]=process.argv.slice(2),raw=await fs.readFile(requestPath);
const hash=v=>createHash('sha256').update(v).digest('hex');
if(hash(raw)!==sealed)throw Error('measurement request hash mismatch');
const request=JSON.parse(raw),artifacts=request.artifacts;
if(!Array.isArray(artifacts)||!artifacts.length||artifacts.length>32)throw Error('bounded artifacts required');
const helpers={
 '/adapter/transfer.mjs':fileURLToPath(new URL('value_transfer.mjs',import.meta.url)),
 '/adapter/values.mjs':fileURLToPath(new URL('gpu_measurement_values.mjs',import.meta.url)),
 '/adapter/device.mjs':fileURLToPath(new URL('gpu_device.mjs',import.meta.url))
};
const report=await browserHost(browser,artifacts.map(a=>a.path),helpers,async host=>{
 const result=await host.evaluate(`(${measure.toString()})(${JSON.stringify(request)},${JSON.stringify(host.base)})`,request.timeout_ms??120000);
 return {...result,browserVersion:host.browserVersion,browser_startup_ns:host.setup_ns};
});
console.log(JSON.stringify({...report,request_sha256:sealed,memory_scope:'Whole-browser/renderer/GPU peak memory is unknown; no zero or per-buffer estimate is substituted.',peak_rss_bytes:null}));

async function measure(request,base) {
 const {unpackTransfer}=await import(base+'/adapter/transfer.mjs');
 const {openMeasurementDevice}=await import(base+'/adapter/device.mjs');
 const {same,normalized,consume}=await import(base+'/adapter/values.mjs');
 const phases=unpackTransfer(request.phases);
 const controls=request.measurement??{},rounds=controls.rounds??11,minBatch=controls.minimum_batch_ms??5,maxCalls=controls.maximum_calls_per_sample??256;
 const processIndex=request.process_index??0;
 if(!Number.isInteger(rounds)||rounds<9||rounds>64||!Number.isInteger(minBatch)||minBatch<1||minBatch>50||!Number.isInteger(maxCalls)||maxCalls<1||maxCalls>65536||!Number.isInteger(processIndex)||processIndex<0||processIndex>7)throw Error('bounded measurement controls required');
 const {device,identity,actual,setup_ns:device_setup_ns}=await openMeasurementDevice();
 if(identity.isFallbackAdapter!==false){device.destroy();throw Error('hardware adapter required; fallback or unknown adapter declined');}
 let lost=false;device.lost.then(()=>lost=true);
 let seed=(0x7193abef^Math.imul(processIndex,0x9e3779b9))>>>0,sink=0;
 function shuffle(v){for(let i=v.length-1;i>0;i--){seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;const j=(seed>>>0)%(i+1);[v[i],v[j]]=[v[j],v[i]];}return v;}
 const lanes=[],rejected=[],rows=[];
 try {
  for(let index=0;index<request.artifacts.length;index++){
   const a=request.artifacts[index],root=base+'/artifact/'+index+'/',start=performance.now();let host;
   try {
    const manifest=await(await fetch(root+'manifest.json')).json();
    if(manifest.schema!==2||manifest.backend!=='webgpu-wgpu-v2'||manifest.runtime_abi!=='ink-packed-pipeline-v2')throw Error('packed supplied-device ABI required');
    if(manifest.core_sha256!==a.selected_core_sha256)throw Error('artifact source binding mismatch');
    const module=await import(root+'ink-gpu.mjs');host=await module.loadInk(root,{device});
    const functions=new Map(manifest.functions.map(f=>[f.name,f]));
    const captured=structuredClone(phases),original=structuredClone(captured);
    let first_call_ns=null;
    const invoke=async call=>{
     const started=first_call_ns===null?performance.now():null;
     const before={...actual};
     const output=await host.call(call.function,call.args,{backend:'gpu-required'});
     if(lost||output.backend!=='gpu'||!Number.isSafeInteger(output.counters?.dispatches)||output.counters.dispatches<=0)throw Error('actual device dispatch required; fallback/empty work declined');
     if(!Number.isSafeInteger(output.counters.readback_bytes)||output.counters.readback_bytes<=0)throw Error('complete device readback required');
     const observed={dispatches:actual.dispatches-before.dispatches,submissions:actual.submissions-before.submissions,readbacks:actual.readbacks-before.readbacks};
     if(observed.dispatches!==output.counters.dispatches||observed.dispatches<=0||observed.submissions<=0||observed.readbacks<=0)throw Error('provided-device dispatch/submission/readback missing or mismatched');
     sink^=consume(output.value);
     if(started!==null)first_call_ns=Math.round((performance.now()-started)*1e6);
     return {...output,observed_device:observed};
    };
    const validate=async()=>{
     for(const calls of Object.values(captured))for(const call of calls){
      const f=functions.get(call.function);if(!f)throw Error('missing function');
      const output=await invoke(call);
      if(!same(normalized(output.value,f.result),normalized(call.expected,f.result,true)))throw Error('complete output differs from independent reference');
     }
     if(!same(captured,original))throw Error('candidate mutated captured input');
    };
    const setup_ns=Math.round((performance.now()-start)*1e6),validationStart=performance.now();await validate();
    lanes.push({measurement:a.measurement,host,invoke,validate,setup_ns,first_call_ns,validation_ns:Math.round((performance.now()-validationStart)*1e6),phases:captured});
   }catch(error){if(host?.dispose)await host.dispose();else await host?.close?.();rejected.push({measurement:a.measurement,reason:String(error)});}
  }
  if(!lanes.some(l=>l.measurement===request.artifacts[0].measurement))throw Error('baseline failed independent device validation: '+JSON.stringify(rejected));
  for(const [phase,calls]of Object.entries(phases)){
   const invoke=async lane=>{let counter;for(const call of lane.phases[phase]){const r=await lane.invoke(call);counter={reported:r.counters,observed:r.observed_device};}return counter;};
   const counts=new Map(),samples=new Map(),counters=new Map();
   for(const lane of shuffle([...lanes])){
    for(let i=0;i<4;i++)await invoke(lane);
    let count=1;for(;;){const start=performance.now();for(let i=0;i<count;i++)await invoke(lane);if(performance.now()-start>=minBatch||count>=maxCalls)break;count=Math.min(count*2,maxCalls);}
    counts.set(lane.measurement,count);samples.set(lane.measurement,[]);
   }
   for(let round=0;round<rounds;round++)for(const lane of shuffle([...lanes])){
    const count=counts.get(lane.measurement),start=performance.now();let counter;
    for(let i=0;i<count;i++)counter=await invoke(lane);
    samples.get(lane.measurement).push(String(Math.round((performance.now()-start)*1e6/count/calls.length)));counters.set(lane.measurement,counter);
   }
   for(const lane of lanes)rows.push({measurement:lane.measurement,phase,complete_call_ns:samples.get(lane.measurement),calls_per_sample:counts.get(lane.measurement),phase_calls:calls.length,counters:counters.get(lane.measurement)});
  }
  const accepted=new Set();for(const lane of lanes){try{await lane.validate();accepted.add(lane.measurement);}catch(e){rejected.push({measurement:lane.measurement,reason:String(e)});}}
  if(!accepted.has(request.artifacts[0].measurement))throw Error('baseline failed post-measurement validation');
  return {schema:1,device:identity,device_setup_ns,rows:rows.filter(r=>accepted.has(r.measurement)),rejected,module_load:lanes.map(l=>({measurement:l.measurement,setup_ns:String(l.setup_ns),first_call_ns:String(l.first_call_ns),validation_ns:String(l.validation_ns)})),input_fingerprints:[...new Set(Object.values(phases).flat().map(c=>c.input_sha256))].sort(),sink,
   scope:'Explicit GPU-required complete async calls on one supplied hardware device: capture/uploads/dispatch/readback/decode and full output consumption included. Initialization/shader warmup separate; reference validation before/after timing. No CPU-placement, automatic selection, memory-bound or exclusive-machine claim.'};
 }finally{for(const lane of lanes){if(lane.host.dispose)await lane.host.dispose();else await lane.host.close?.();}device.destroy();}
}
