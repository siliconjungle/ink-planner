// Offline local-browser host. No runtime profitability probes or external sites.
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';

export function assetServer(roots, helpers={}) {
 return http.createServer(async(req,res)=>{
  res.setHeader('Cross-Origin-Opener-Policy','same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy','require-corp');
  try {
   if(req.method!=='GET')throw Error('GET required');
   const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
   if(name==='/'){res.setHeader('Content-Type','text/html');res.end('<!doctype html><title>Ink offline measurement</title>');return;}
   let file=helpers[name];
   if(!file){
    const match=/^\/artifact\/(\d+)\/(.+)$/.exec(name);
    if(!match||!roots[Number(match[1])])throw Error('unknown asset');
    const root=await fs.realpath(roots[Number(match[1])]);
    file=await fs.realpath(path.resolve(root,match[2]));
    const relative=path.relative(root,file);
    if(relative.startsWith('..')||path.isAbsolute(relative))throw Error('asset outside bundle');
   }
   const type={'.mjs':'text/javascript','.wasm':'application/wasm','.wgsl':'text/plain','.json':'application/json'}[path.extname(file)];
   if(!type)throw Error('unsupported asset');
   res.setHeader('Content-Type',type);res.end(await fs.readFile(file));
  }catch{res.statusCode=404;res.end();}
 });
}

async function connect(url,signal) {
 if(signal?.aborted)throw Error('browser operation cancelled');
 const socket=new WebSocket(url),pending=new Map();let next=1;
 await new Promise((resolve,reject)=>{
  const abort=()=>{clearTimeout(timer);socket.close();reject(Error('browser operation cancelled'));};
  const timer=setTimeout(()=>{signal?.removeEventListener('abort',abort);socket.close();reject(Error('browser connection time budget'));},10000);
  signal?.addEventListener('abort',abort,{once:true});
  socket.addEventListener('open',()=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);resolve();},{once:true});
  socket.addEventListener('error',error=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);reject(error);},{once:true});
 });
 socket.addEventListener('message',event=>{
  const response=JSON.parse(event.data),waiter=pending.get(response.id);
  if(!waiter)return;
  pending.delete(response.id);clearTimeout(waiter.timer);
  if(response.error)waiter.reject(Error(JSON.stringify(response.error)));else waiter.resolve(response.result);
 });
 socket.addEventListener('close',()=>{for(const waiter of pending.values()){clearTimeout(waiter.timer);waiter.reject(Error('browser connection closed'));}pending.clear();});
 return {
  send(method,params={},timeout=120000){return new Promise((resolve,reject)=>{
   const id=next++,timer=setTimeout(()=>{pending.delete(id);reject(Error('browser command time budget'));},timeout);
   pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));
  });},
  close(){socket.close();}
 };
}

export async function browserHost(executable,roots,helpers,action,{timeout_ms=120000}={}) {
 if(!Number.isInteger(timeout_ms)||timeout_ms<1||timeout_ms>3300000)throw Error('bounded browser host time budget required');
 const started=performance.now(),server=assetServer(roots,helpers),profile=await fs.mkdtemp(path.join(os.tmpdir(),'ink-offline-browser-'));
 let child,client,control,timer,caught,stopped=false,port;
 const cancellation=new AbortController();
 const check=()=>{if(stopped)throw Error('browser operation cancelled');};
 try {
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const base=`http://127.0.0.1:${server.address().port}`;
  child=spawn(executable,['--headless=new','--no-first-run','--no-default-browser-check','--enable-unsafe-webgpu',
   '--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{stdio:['ignore','ignore','pipe']});
  let failure=null,errors='';child.once('error',e=>failure=e);child.stderr.on('data',b=>{errors=(errors+b).slice(-4000);});
  await fs.writeFile(path.join(profile,'storage-retention.json'),JSON.stringify({schema:1,keep:[{path:'.',reason:'Owned live offline browser profile; do not retire before observed browser exit.'}],safe_to_remove:[]}));
  const work=async()=>{
   const deadline=Date.now()+15000;
   while(Date.now()<deadline){
    check();if(failure)throw failure;
    if(child.exitCode!==null)throw Error('browser exited: '+errors);
    try{port=(await fs.readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0];break;}catch{}
    await delay(25);
   }
   check();if(!port||!/^\d{1,5}$/.test(port))throw Error('browser startup time budget or invalid port');
   const signal=()=>AbortSignal.any([cancellation.signal,AbortSignal.timeout(10000)]);
   const info=await(await fetch(`http://127.0.0.1:${port}/json/version`,{signal:signal()})).json();check();
   control=await connect(info.webSocketDebuggerUrl,cancellation.signal);check();
   const target=await(await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(base+'/')}`,{method:'PUT',signal:signal()})).json();check();
   client=await connect(target.webSocketDebuggerUrl,cancellation.signal);check();
   await client.send('Page.enable');check();await client.send('Page.navigate',{url:base+'/'});check();
   return await action({base,browserVersion:info.Browser,setup_ns:Math.round((performance.now()-started)*1e6),async evaluate(expression,timeout=120000){
    check();const result=await client.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},timeout);check();
    if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
   }});
  };
  return await Promise.race([work(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('browser host time budget')),timeout_ms);})]);
 } catch(error) {caught=error;throw error;}
 finally {
  stopped=true;clearTimeout(timer);cancellation.abort();
  // A budget can expire during startup before the control socket is ready.
  // Recover only this profile's endpoint and request its normal shutdown.
  if(!control&&child?.pid&&child.exitCode===null){
   const deadline=Date.now()+15000;
   while(Date.now()<deadline&&!control&&child.exitCode===null){
    try{
     const p=(await fs.readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0];
     if(!/^\d{1,5}$/.test(p))throw Error('invalid owned browser port');
     const info=await(await fetch(`http://127.0.0.1:${p}/json/version`,{signal:AbortSignal.timeout(1000)})).json();
     control=await connect(info.webSocketDebuggerUrl,AbortSignal.timeout(1000));
    }catch{await delay(25);}
   }
  }
  try{await control?.send('Browser.close',{},5000);}catch{}
  client?.close();control?.close();
  if(child?.pid&&child.exitCode===null)await Promise.race([new Promise(resolve=>child.once('exit',resolve)),delay(5000)]);
  const exited=!child?.pid||child.exitCode!==null;
  if(exited)await fs.rm(profile,{recursive:true,force:true});
  const cleanup={pid:child?.pid??null,profile,exited,profile_retained:!exited};
  if(caught)caught.browser_cleanup=cleanup;
  server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
  if(!exited){
   child.unref();child.stderr.unref?.();
   const error=Error('owned browser remains active; protected profile retained: '+JSON.stringify(cleanup));error.browser_cleanup=cleanup;throw error;
  }
 }
}
