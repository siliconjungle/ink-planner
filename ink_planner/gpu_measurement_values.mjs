// Exact typed controls for offline GPU results. No proof or codec authority.
export function same(a,b){
 if(Object.is(a,b))return true;
 if(a===null||b===null||typeof a!=='object'||typeof b!=='object')return false;
 if(Array.isArray(a)!==Array.isArray(b))return false;
 const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(k=>Object.hasOwn(b,k)&&same(a[k],b[k]));
}
export function normalized(value,type,expected=false,depth=0,budget={n:0}){
 if(depth>128||++budget.n>4000000)throw Error('output budget');
 if(type.kind==='u64'){
  if(!expected&&!['number','string'].includes(typeof value))throw Error('U64 GPU result requires exact JSON word');
  if(typeof value==='number'&&!Number.isSafeInteger(value))throw Error('inexact U64 output');
  if(!['number','bigint','string'].includes(typeof value)||typeof value==='string'&&!/^(0|[1-9][0-9]*)$/.test(value))throw Error('U64 output');
  const n=BigInt(value);if(n<0n||n>18446744073709551615n)throw Error('U64 output range');
  if(!expected&&typeof value==='string'&&n<=9007199254740991n)throw Error('noncanonical U64 JSON word');return n;
 }
 if(type.kind==='f32'){
  const words=new Uint32Array(1),floats=new Float32Array(words.buffer);
  if(value&&typeof value==='object'&&Object.keys(value).length===1&&Object.hasOwn(value,'F32Bits')){
   if(!Number.isInteger(value.F32Bits)||value.F32Bits<0||value.F32Bits>4294967295)throw Error('F32 raw-bit range');
   words[0]=value.F32Bits;
  }else if(typeof value==='number'&&!Number.isNaN(value))floats[0]=value;else throw Error('F32 output');
  return {F32Bits:words[0]};
 }
 if(type.kind==='u32'||type.kind==='i32'){
  const lower=type.kind==='u32'?0:-2147483648,upper=type.kind==='u32'?4294967295:2147483647;
  if(!Number.isInteger(value)||value<lower||value>upper)throw Error('word output range');return value;
 }
 if(type.kind==='bool'){if(typeof value!=='boolean')throw Error('Bool output');return value;}
 if(type.kind==='string'){if(typeof value!=='string')throw Error('String output');return value;}
 if(['list','vector'].includes(type.kind)){
  if(!Array.isArray(value)||type.kind==='vector'&&value.length!==type.size)throw Error('sequence output');
  return value.map(v=>normalized(v,type.element,expected,depth+1,budget));
 }
 if(type.kind==='record'){
  if(!value||Array.isArray(value)||Object.keys(value).length!==type.fields.length)throw Error('record output');
  return Object.fromEntries(type.fields.map(f=>{if(!Object.hasOwn(value,f.name))throw Error('record field');return[f.name,normalized(value[f.name],f.type,expected,depth+1,budget)];}));
 }
 throw Error('unsupported device result descriptor');
}
const bits=new DataView(new ArrayBuffer(8));
export function consume(value){
 if(typeof value==='bigint'){let h=0,n=value<0n?-value:value;do{h=Math.imul(h,31)^Number(n&4294967295n);n>>=32n;}while(n);return h;}
 if(typeof value==='number'){bits.setFloat64(0,value,true);return bits.getUint32(0,true)^bits.getUint32(4,true);}
 if(typeof value==='boolean')return Number(value);
 if(typeof value==='string'){let h=0;for(let i=0;i<value.length;i++)h=Math.imul(h,31)^value.charCodeAt(i);return h;}
 if(value===null)return 0;
 let h=0;for(const v of Object.values(value))h=Math.imul(h,31)^consume(v);return h;
}
