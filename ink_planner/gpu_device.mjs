// Device provenance for offline measurements. Never an applicability/profit fact.
export async function openMeasurementDevice() {
 const started=performance.now();
 if(!navigator.gpu)throw Error('WebGPU unavailable');
 const adapter=await navigator.gpu.requestAdapter({powerPreference:'high-performance'});
 if(!adapter)throw Error('WebGPU adapter unavailable');
 const info=adapter.info,limits={};
 for(const name of Object.getOwnPropertyNames(Object.getPrototypeOf(adapter.limits))) {
  if(name!=='constructor'&&typeof adapter.limits[name]==='number')limits[name]=adapter.limits[name];
 }
 const identity={schema:1,vendor:info.vendor,architecture:info.architecture,device:info.device,
  description:info.description,isFallbackAdapter:info.isFallbackAdapter??adapter.isFallbackAdapter??null,
  features:[...adapter.features].sort(),limits,userAgent:navigator.userAgent};
 const budget=268435456;
 const requiredLimits={
  maxStorageBufferBindingSize:Math.min(limits.maxStorageBufferBindingSize,budget),
  maxBufferSize:Math.min(limits.maxBufferSize,budget),
  maxStorageBuffersPerShaderStage:limits.maxStorageBuffersPerShaderStage};
 const device=await adapter.requestDevice({requiredLimits});
 identity.requiredLimits=requiredLimits;
 identity.deviceFeatures=[...device.features].sort();
 const actual={dispatches:0,submissions:0,readbacks:0};
 function observe(object,name,handler){
  const original=object[name].bind(object);
  Object.defineProperty(object,name,{value:(...args)=>handler(original,...args)});
 }
 observe(device.queue,'submit',(original,...args)=>{actual.submissions++;return original(...args);});
 observe(device,'createCommandEncoder',(original,...args)=>{
  const encoder=original(...args);
  observe(encoder,'beginComputePass',(original,...args)=>{
   const pass=original(...args);
   for(const name of ['dispatchWorkgroups','dispatchWorkgroupsIndirect'])observe(pass,name,(original,...args)=>{actual.dispatches++;return original(...args);});
   return pass;
  });
  return encoder;
 });
 observe(device,'createBuffer',(original,...args)=>{
  const buffer=original(...args);
  observe(buffer,'mapAsync',(original,mode,...args)=>{if(mode&GPUMapMode.READ)actual.readbacks++;return original(mode,...args);});
  return buffer;
 });
 return {device,identity,actual,setup_ns:Math.round((performance.now()-started)*1e6)};
}
