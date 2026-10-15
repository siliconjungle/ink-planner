"""Captured compiled Ink advice for exploration, never admission or selection."""
import hashlib
import json
import math
import os
import select
import shutil
import subprocess
import time
from collections import OrderedDict
from pathlib import Path

LIMIT=4_000_000


def document(value,maximum_bytes=LIMIT):
    """Prepare the complete canonical JSON tree without lossy numeric parsing.

    This is untrusted model input, not a new property or optimization premise.
    Flattening keeps source-depth independent of the public value-codec depth.
    """
    nodes=[];remaining=maximum_bytes
    def charge(size):
        nonlocal remaining
        remaining-=size
        if remaining<0:raise ValueError('search document byte budget')
    def visit(value,depth=0):
        if depth>128 or len(nodes)>=1_000_000:raise ValueError('search document budget')
        charge(32) # Lower bound for one serialized node, excluding its payload.
        index=len(nodes);nodes.append(None)
        if value is None:kind,text,children=0,'',[]
        elif type(value)is bool:kind,text,children=1,'true'if value else'false',[]
        elif type(value)is int:kind,text,children=2,str(value),[]
        elif type(value)is str:kind,text,children=3,value,[]
        elif isinstance(value,list):
            if len(value)>remaining//51:raise ValueError('search document byte budget')
            kind,text,children=4,'',(('',child)for child in value)
        elif isinstance(value,dict):
            if len(value)>remaining//51:raise ValueError('search document byte budget')
            if any(type(key)is not str for key in value):raise ValueError('search document object key')
            kind,text,children=5,'',sorted(value.items())
        else:raise ValueError('search document expects canonical JSON values')
        charge(len(text.encode('utf8')))
        edges=[]
        for key,child in children:
            charge(19+len(key.encode('utf8')))
            edges.append(dict(key=key,node=visit(child,depth+1)))
        nodes[index]=dict(kind=kind,value=text,edges=edges)
        return index
    root=visit(value)
    return dict(nodes=nodes,root=root)


def bounded(path,limit):
    with Path(path).open('rb') as stream:
        data=stream.read(limit+1)
    if len(data)>limit:raise ValueError('search goal input byte budget')
    return data


class SearchGoal:
    def __init__(self,compiler,source,context,entry,directory,*,core=False,revision=0,
                 timeout=1.0,compile_timeout=120,memory_mb=128,input_bytes=LIMIT,node='node',context_wire=False):
        if not 0<timeout<=30 or not 16<=memory_mb<=512 or not 1<=input_bytes<=LIMIT:
            raise ValueError('search goal process budget')
        self.timeout,self.memory_mb,self.input_bytes=timeout,memory_mb,input_bytes
        self.process=None;self.buffer=bytearray();self.generation=0;self.disabled=False
        self.queries=self.hits=self.failures=0;self.rows=[];self.dropped_rows=0;self.failure=None
        self.memo=OrderedDict();self.started_ns=time.monotonic_ns()
        self.module=Path(directory)/'search-goal.mjs';self.core=Path(directory)/'search-goal.core.json'
        self.context_format='wire'if context_wire else'json'
        self.context=Path(directory)/('search-goal-context.bin'if context_wire else'search-goal-context.json')
        self.context.write_bytes(bounded(context,LIMIT))
        def run(*args):
            result=subprocess.run([str(compiler),*map(str,args)],capture_output=True,text=True,timeout=compile_timeout)
            if result.returncode:raise ValueError(result.stderr[-3000:])
        if core:self.core.write_bytes(bounded(source,16_000_000))
        else:run('emit-core',source,'-o',self.core)
        core_bytes=bounded(self.core,16_000_000)
        run('build',self.core,'--core','--target','javascript','-o',self.module)
        self.module_sha=hashlib.sha256(bounded(self.module,16_000_000)).hexdigest()
        self.core_sha=hashlib.sha256(core_bytes).hexdigest()
        self.context_sha=hashlib.sha256(self.context.read_bytes()).hexdigest()
        worker=Path(__file__).with_suffix('.mjs')
        try:
            self.process=subprocess.Popen([node,f'--max-old-space-size={memory_mb}',str(worker),str(self.module),
                str(self.context),entry,str(revision),str(input_bytes),self.context_format],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            ready=self.receive(time.monotonic()+timeout)
            if ready.get('schema')!=1 or ready.get('ready')is not True:
                raise ValueError(ready.get('error','search goal not ready'))
            self.binding={k:ready[k]for k in ['source','entry','revision','context','context_schema','input_schema','context_format','node']}
            self.binding.update(module_sha256=self.module_sha,core_sha256=self.core_sha,
                context_file_sha256=self.context_sha,worker_sha256=hashlib.sha256(worker.read_bytes()).hexdigest())
        except BaseException:
            self.close();raise
        self.setup_ns=time.monotonic_ns()-self.started_ns

    def close(self):
        process=self.process;self.process=None
        if process is not None:
            if process.poll()is None:process.kill()
            process.wait()
            process.stdin.close();process.stdout.close()

    def receive(self,deadline):
        while True:
            at=self.buffer.find(b'\n')
            if at>=0:
                line=bytes(self.buffer[:at]);del self.buffer[:at+1]
                return json.loads(line)
            remaining=deadline-time.monotonic()
            if remaining<=0 or not select.select([self.process.stdout],[],[],remaining)[0]:
                raise TimeoutError('search goal response time budget')
            chunk=os.read(self.process.stdout.fileno(),65536)
            if not chunk:raise ValueError('search goal output closed')
            if len(self.buffer)+len(chunk)>65536:raise ValueError('search goal response byte budget')
            self.buffer.extend(chunk)

    def send(self,payload,deadline):
        fd=self.process.stdin.fileno();os.set_blocking(fd,False);offset=0
        while offset<len(payload):
            remaining=deadline-time.monotonic()
            if remaining<=0 or not select.select([],[fd],[],remaining)[1]:
                raise TimeoutError('search goal input time budget')
            try:offset+=os.write(fd,payload[offset:offset+65536])
            except BlockingIOError:continue

    def score(self,state,ident,target,frequencies,default,deadline=None):
        if self.disabled:return default
        started=time.monotonic_ns();request_id=self.queries+1
        try:
            if self.process is None:raise ValueError('search goal process closed')
            if not math.isfinite(default)or abs(default)>1e100:raise ValueError('search default priority budget')
            candidate=dict(id=ident,source=state['source'],target=target,state=document(state,self.input_bytes),
                default_priority=default,frequencies=[dict(entry=k,calls=str(v))for k,v in sorted(frequencies.items())])
            payload=json.dumps(dict(schema=1,id=request_id,candidate=candidate),ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()+b'\n'
            if len(payload)>self.input_bytes:raise ValueError('search goal request byte budget')
            # Bind complete checked data, not only semantic/proof-erased memo IDs.
            key=hashlib.sha256(json.dumps(candidate,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            if key in self.memo:
                self.hits+=1;self.memo.move_to_end(key);return self.memo[key]
            self.queries+=1
            deadline=min(deadline or float('inf'),time.monotonic()+self.timeout)
            self.send(payload,deadline);reply=self.receive(deadline)
            value=reply.get('score')
            if reply.get('id')!=request_id or type(value)not in (int,float)or not math.isfinite(value)or abs(value)>1e100:
                raise ValueError(reply.get('error','search goal response identity/scalar'))
            self.memo[key]=value
            if len(self.memo)>512:self.memo.popitem(last=False)
            if len(self.rows)<4096:self.rows.append(dict(candidate=ident,input_sha256=key,priority=value,default=default,elapsed_ns=time.monotonic_ns()-started))
            else:self.dropped_rows+=1
            return value
        except (ValueError,TypeError,KeyError,OverflowError,OSError,TimeoutError)as error:
            self.disabled=True;self.generation+=1;self.failures+=1;self.failure=str(error)[-1000:];self.close()
            return default

    def export(self,output):
        artifacts={}
        for name,path in [('module',self.module),('core',self.core),('context',self.context)]:
            expected={'module':self.module_sha,'core':self.core_sha,'context':self.context_sha}[name]
            if hashlib.sha256(bounded(path,LIMIT if name=='context'else 16_000_000)).hexdigest()!=expected:raise ValueError('captured search goal artifact changed')
            suffix={'module':'.search-goal.mjs','core':'.search-goal.core.json','context':'.search-goal-context.'+('bin'if self.context_format=='wire'else'json')}[name]
            destination=Path(str(output)+suffix);destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,destination);artifacts[name]=destination.name
        return dict(binding=self.binding,budgets=dict(timeout_seconds=self.timeout,node_heap_mb=self.memory_mb,input_bytes=self.input_bytes),
            setup_ns=self.setup_ns,queries=self.queries,memo_hits=self.hits,failures=self.failures,disabled=self.disabled,
            failure=self.failure,priorities=self.rows,dropped_priorities=self.dropped_rows,artifacts=artifacts,
            scope='Captured compiled Ink exploration advice only; full admission and comparable-evidence selection remain mandatory. Node heap budget excludes process overhead.')
