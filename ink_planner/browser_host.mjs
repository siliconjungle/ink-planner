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

async function connect(url) {
 const socket=new WebSocket(url),pending=new Map();let next=1;
 await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>{socket.close();reject(Error('browser connection time budget'));},10000);
  socket.addEventListener('open',()=>{clearTimeout(timer);resolve();},{once:true});
  socket.addEventListener('error',error=>{clearTimeout(timer);reject(error);},{once:true});
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

export async function browserHost(executable,roots,helpers,action) {
 const started=performance.now();
 const server=assetServer(roots,helpers),profile=await fs.mkdtemp(path.join(os.tmpdir(),'ink-offline-browser-'));
 let child,client,control;
 try {
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const base=`http://127.0.0.1:${server.address().port}`;
  child=spawn(executable,['--headless=new','--no-first-run','--no-default-browser-check','--enable-unsafe-webgpu',
   '--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{stdio:['ignore','ignore','pipe']});
  let failure=null,errors='';child.once('error',e=>failure=e);child.stderr.on('data',b=>{errors=(errors+b).slice(-4000);});
  const deadline=Date.now()+15000;let port;
  while(Date.now()<deadline){
   if(failure)throw failure;
   if(child.exitCode!==null)throw Error('browser exited: '+errors);
   try{port=(await fs.readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0];break;}catch{}
   await delay(25);
  }
  if(!port||!/^\d{1,5}$/.test(port))throw Error('browser startup time budget or invalid port');
  const info=await(await fetch(`http://127.0.0.1:${port}/json/version`,{signal:AbortSignal.timeout(10000)})).json();control=await connect(info.webSocketDebuggerUrl);
  const target=await(await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(base+'/')}`,{method:'PUT',signal:AbortSignal.timeout(10000)})).json();
  client=await connect(target.webSocketDebuggerUrl);
  await client.send('Page.enable');await client.send('Page.navigate',{url:base+'/'});
  return await action({base,browserVersion:info.Browser,setup_ns:Math.round((performance.now()-started)*1e6),async evaluate(expression,timeout=120000){
   const result=await client.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},timeout);
   if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));
   return result.result.value;
  }});
 } finally {
  try{await control?.send('Browser.close',{},5000);}catch{}
  client?.close();control?.close();
  if(child?.pid&&child.exitCode===null)await Promise.race([new Promise(resolve=>child.once('exit',resolve)),delay(5000)]);
  // Do not remove a profile while its owned browser may still use it.
  if(!child?.pid||child.exitCode!==null)await fs.rm(profile,{recursive:true,force:true});
  await new Promise(resolve=>server.close(resolve));
 }
}
