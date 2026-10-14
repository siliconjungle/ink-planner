// Measurement-only tagged packets preserve exact integers and own record keys.
export function unpackTransfer(value){
 let nodes=0;
 function visit(v,depth){
  if(depth>128||++nodes>4000000||!Array.isArray(v))throw Error('measurement transfer budget');
  const [kind,x]=v;
  if(v.length!==(kind==='null'?1:2))throw Error('measurement transfer arity');
  switch(kind){
   case 'null':return null;
   case 'bool':if(typeof x!=='boolean')throw Error('transfer Bool');return x;
   case 'string':if(typeof x!=='string')throw Error('transfer String');return x;
   case 'integer':{
    if(typeof x!=='string'||x.length>4096||!/^(0|[1-9][0-9]*|-[1-9][0-9]*)$/.test(x))throw Error('transfer integer');
    const n=BigInt(x);return n>=-9007199254740991n&&n<=9007199254740991n?Number(n):n;
   }
   case 'number':{if(typeof x!=='string')throw Error('transfer number');const n=Number(x);if(!Number.isFinite(n))throw Error('transfer number');return n;}
   case 'array':if(!Array.isArray(x))throw Error('transfer array');return x.map(v=>visit(v,depth+1));
   case 'object':{
    if(!Array.isArray(x))throw Error('transfer object');const out={};
    for(const field of x){
     if(!Array.isArray(field)||field.length!==2||typeof field[0]!=='string'||Object.hasOwn(out,field[0]))throw Error('transfer object field');
     Object.defineProperty(out,field[0],{value:visit(field[1],depth+1),writable:true,enumerable:true,configurable:true});
    }return out;
   }
   default:throw Error('measurement transfer kind');
  }
 }
 return visit(value,0);
}
