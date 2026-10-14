"""Bounded process scheduling with live, fair recipe output consumption.

Each process owns a group so deadlines also stop nested proof/search workers.
Complete successes alone enter the disposable per-compilation recipe cache.
"""
import os,signal,subprocess,time
from collections import deque


def priority(job):
    return (job.get('recipe')or{}).get('priority',0)


def family(job):
    return (job.get('recipe')or{}).get('id',job['key'])


def interleave_stages(jobs,dispatch_counts=None):
    """Give each published stage a dispatch opportunity before its second job.

    Stage names are untrusted scheduling data, never applicability or authority.
    Smaller database priorities run first. Interleave stages within each priority;
    keep every job. Within each priority, previously unserved families get the
    next opportunity across expansions. Stable ordering breaks ties; without
    dispatch history, missing metadata retains FIFO.
    """
    counts=dispatch_counts or {}
    jobs=sorted(jobs,key=lambda job:counts.get(family(job),0))
    groups={}
    for job in jobs:
        stage=(job.get('recipe')or{}).get('stage','')
        groups.setdefault(priority(job),{}).setdefault(stage,deque()).append(job)
    ordered=[]
    for level in sorted(groups):
        stages=groups[level]
        while stages:
            for stage in list(stages):
                ordered.append(stages[stage].popleft())
                if not stages[stage]:del stages[stage]
    return ordered


def stop(process):
    # Kill the owned group even when its leader exited with live descendants.
    if os.name=='posix':
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        except PermissionError:
            # A reaped leader's group can already be outside our ownership.
            # Never turn an OS refusal on that vanished group into a compile error.
            if process.poll()is None:process.kill()
    elif process.poll()is None:
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    process.wait()


def run(jobs,workers,deadline,timeout,byte_limit=128_000_000,dispatch_counts=None):
    """Yield (job,event) in round-robin order; all processes close on early exit.

    Jobs supply command, env, output, cache and log paths. Output is NDJSON;
    registry/header validation and mathematical admission belong to the caller.
    """
    pending=deque(interleave_stages(jobs,dispatch_counts));active=[]
    def launch(job):
        started=time.monotonic();output=job['output'];cached=job['cache'].exists()
        if cached:
            output=job['cache'];process=None;logs=[]
        else:
            output.unlink(missing_ok=True);logs=[job[k].open('wb')for k in ('stdout','stderr')]
            try:process=subprocess.Popen(job['command'],env=job['env'],stdout=logs[0],stderr=logs[1],start_new_session=os.name=='posix')
            except Exception:
                for log in logs:log.close()
                raise
        return dict(job=job,process=process,logs=logs,output=output,cached=cached,position=0,pending=b'',started=started,rows=0)
    try:
        while pending or active:
            if time.monotonic()>=deadline:break
            while pending and len(active)<workers:
                # Give earlier priorities their first result after the header,
                # then let other families run alongside remaining variants.
                # A slow variant must not monopolise the overall time budget.
                if any(priority(pending[0])>priority(s['job'])and s['rows']<2 for s in active):break
                job=pending.popleft()
                active.append(launch(job))
                # Count actual opportunities, including cached/failed workers.
                # Merely queued jobs must not lose their place when a parent
                # expansion closes early. No result or proof is reused here.
                if dispatch_counts is not None:
                    name=family(job);dispatch_counts[name]=dispatch_counts.get(name,0)+1
            progress=False
            for state in list(active):
                job=state['job'];process=state['process'];error=None
                output=state['output']
                if output.exists():
                    if output.stat().st_size>byte_limit:error='recipe output byte budget'
                    else:
                        with output.open('rb')as stream:
                            stream.seek(state['position']);chunk=stream.read(65536)
                        state['position']+=len(chunk);state['pending']+=chunk
                        if len(state['pending'])>byte_limit:error='recipe line byte budget'
                        if chunk:progress=True
                if b'\n'in state['pending'] and error is None:
                    line,state['pending']=state['pending'].split(b'\n',1)
                    state['rows']+=1;progress=True
                    yield job,dict(kind='line',raw=line,cached=state['cached'])
                    # Drain one row per family before another row from this one.
                    continue
                if process is not None and time.monotonic()-state['started']>=timeout:error='proposal process time budget'
                ended=process is None or process.poll()is not None
                if ended and output.exists()and output.stat().st_size>state['position']and error is None:continue
                if ended or error:
                    if process is not None:stop(process)
                    for log in state['logs']:log.close()
                    if error is None and state['pending']:error='truncated recipe output'
                    if error is None and not state['rows']:error='missing recipe header'
                    if error is None and process is not None and process.returncode:error='recipe process exit '+str(process.returncode)
                    yield job,dict(kind='done',cached=state['cached'],complete=error is None,reason=error,
                        elapsed_seconds=str(time.monotonic()-state['started']))
                    active.remove(state);progress=True
            if not progress:time.sleep(min(.01,max(0,deadline-time.monotonic())))
    finally:
        for state in active:
            if state['process']is not None:stop(state['process'])
            for log in state['logs']:log.close()
