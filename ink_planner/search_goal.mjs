// Bounded pure-call worker. The goal is checked Ink compiled before this starts;
// no application action, training, proof admission or profitability probe runs.
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const [modulePath,contextPath,entry,revisionText,inputLimitText,contextFormat]=process.argv.slice(2);
const limit=Number(inputLimitText),revision=Number(revisionText);
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const emit=value=>process.stdout.write(JSON.stringify(value)+'\n');
try {
 if(!Number.isSafeInteger(limit)||limit<1||limit>4_000_000
    ||!Number.isSafeInteger(revision)||revision<0
    ||!/^[A-Za-z0-9_.]{1,256}$/.test(entry))throw Error('search goal configuration');
 const contextBytes=await fs.readFile(contextPath);
 if(contextBytes.length>4_000_000)throw Error('search context byte budget');
 const module=await import(pathToFileURL(modulePath));
 const declaration=module.metadata?.functions?.find(f=>f.name===entry&&!f.internal);
 const codecs=Object.hasOwn(module.compiledValues??{},entry)?module.compiledValues[entry]:null;
 if(!declaration||declaration.params.length!==2||declaration.params[0].type.kind!=='record'
    ||declaration.result.kind!=='f64'||!codecs
    ||typeof module.prepareCompiled!=='function'||typeof module.callPreparedCompiled!=='function')
   throw Error('search goal requires compiled fn(search.Candidate, context)->f64');
 const input=codecs.params[0],contextCodec=codecs.params[1],output=codecs.result;
 if(!['json','wire'].includes(contextFormat))throw Error('search context format');
 // Schema-directed JSON ingress retains integer tokens, enum/record shapes and
 // negative zero. Wire ingress additionally preserves nonfinite raw float bits.
 const captured=contextFormat==='wire'?contextCodec.decode(contextBytes):contextCodec.decode(
   new module.TypedJsonCodec(new module.ValueCodec(contextCodec.descriptor)).parseBinary(contextBytes.toString('utf8')));
 const wire=contextCodec.encode(captured);
 if(wire.length>4_000_000)throw Error('search context wire budget');
 const prepared=module.prepareCompiled(entry,[[1,captured]]);
 emit({schema:1,ready:true,source:module.metadata.core_sha256,entry,revision,
   context:digest(wire),context_schema:contextCodec.identity,input_schema:input.identity,
   context_format:contextFormat,node:process.versions});
 function evaluate(request){
   if(request.schema!==1||!Number.isSafeInteger(request.id)||request.id<1)throw Error('search request identity');
   const candidate=request.candidate;
   if(!candidate.state||!Array.isArray(candidate.frequencies)||candidate.frequencies.length>4096)
     throw Error('search candidate shape');
   const frequencies=candidate.frequencies.map(row=>{
     if(typeof row.calls!=='string'||row.calls.length>20||!/^(0|[1-9][0-9]*)$/.test(row.calls))throw Error('search frequency');
     const calls=BigInt(row.calls);if(calls>18446744073709551615n)throw Error('search frequency');
     return {entry:row.entry,calls};
   });
   const argument=input.capture({id:candidate.id,source:candidate.source,target:candidate.target,
     state:candidate.state,default_priority:candidate.default_priority,frequencies});
   const bytes=output.encodeCompact(module.callPreparedCompiled(prepared,[argument]));
   if(bytes.length!==9||bytes[0]!==1)throw Error('search priority binary64 codec');
   const score=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength).getFloat64(1,true);
   if(!Number.isFinite(score)||Math.abs(score)>1e100)throw Error('search priority must be finite and bounded');
   return score;
 }
 let buffered=Buffer.alloc(0),failed=false;
 process.stdin.on('data',chunk=>{
   if(failed)return;
   if(buffered.length+chunk.length>limit){failed=true;emit({error:'search request byte budget'});process.stdin.destroy();return;}
   buffered=Buffer.concat([buffered,chunk]);
   let at;
   while((at=buffered.indexOf(10))>=0){
     const line=buffered.subarray(0,at);buffered=buffered.subarray(at+1);let id=null;
     try{const request=JSON.parse(line);id=request.id;emit({id,score:evaluate(request)});}
     catch(error){emit({id,error:String(error).slice(0,1000)});}
   }
 });
 process.stdin.on('end',()=>{if(buffered.length)emit({error:'incomplete search request'});});
} catch(error){emit({ready:false,error:String(error).slice(0,1000)});process.exitCode=1;}
