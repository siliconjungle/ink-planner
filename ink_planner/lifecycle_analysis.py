"""Offline hindsight diagnostic over comparable replayed windows; never a runtime policy.

Every variant must start each window from the same logical prefix. Window prices
include that window's calls, not restore used solely to acquire counterfactuals.
Setup and boundary migrations are charged separately. No interpolation for gaps.
"""
import argparse
import math
import statistics
from .adaptive_state import read, write
from pathlib import Path


def analyze(data, max_switches=32):
    if data.get('schema') != 1 or data.get('kind') != 'ink-lifecycle-replay-v1':
        raise ValueError('lifecycle replay schema')
    binding = data['binding']
    ids = sorted(binding['variants'])
    windows = data['windows']
    if not 1 <= len(ids) <= 16 or not 1 <= len(windows) <= 4096:
        raise ValueError('lifecycle size budget')
    if type(max_switches) is not int or not 0 <= max_switches <= 64:
        raise ValueError('switch budget')
    cap = min(max_switches, len(windows)-1)
    if len(windows)*len(ids)*(cap+1) > 1_000_000:
        raise ValueError('lifecycle search budget')

    def price(samples):
        if not isinstance(samples, list) or not 3 <= len(samples) <= 4096:
            raise ValueError('three comparable replay samples required')
        if any(type(x) not in (int,float) or not math.isfinite(x) or not 0 <= x <= 1e15 for x in samples):
            raise ValueError('invalid replay price')
        return statistics.median(samples)
    if set(data['setup_ns']) != set(ids):
        raise ValueError('setup coverage')
    setup = {v:price(data['setup_ns'][v]) for v in ids}
    limits=data.get('limits')
    allowed=set(ids)
    if limits is not None:
        memory=limits['memory_limit_bytes'];pause=limits['max_pause_ns']
        if type(memory)is not int or memory<1 or type(pause)not in (int,float) or not math.isfinite(pause) or pause<0:
            raise ValueError('resource limits')
        peaks=data['peak_bytes']
        if set(peaks)!=set(ids) or any(type(n)is not int or n<1 for n in peaks.values()):raise ValueError('peak coverage')
        allowed={v for v in ids if peaks[v]<=memory}
        if not allowed:raise ValueError('no resource-eligible fixed variant')
    costs, edges = [], []
    for w in windows:
        if set(w['costs_ns']) != set(ids) or type(w['calls']) is not int or w['calls'] < 1:
            raise ValueError('window coverage')
        # Acquisition must record a shared snapshot identity, even for a miss.
        digest = w['snapshot_sha256']
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('replay prefix identity')
        costs.append({v:price(w['costs_ns'][v]) for v in ids})
        e = {}
        for m in w['migrations']:
            key=(m['from'],m['to'])
            if key[0] not in ids or key[1] not in ids or key[0]==key[1] or key in e:
                raise ValueError('migration endpoints')
            cost=price(m['samples_ns'])
            if limits is not None:
                peak=m['peak_bytes']
                if type(peak)is not int or peak<1:raise ValueError('migration peak')
                if peak>memory or max(m['samples_ns'])>pause:continue
            e[key]=cost
        edges.append(e)
    fixed={v:setup[v]+sum(c[v] for c in costs) for v in sorted(allowed)}
    # At time zero any fixed variant can be constructed; pay measured setup.
    states={(v,0):setup[v]+costs[0][v] for v in sorted(allowed)}
    parents=[]
    for t in range(1,len(windows)):
        new={}; back={}
        for (old,k),total in sorted(states.items()):
            for v in sorted(allowed):
                switch=int(v!=old); nk=k+switch
                if nk>cap or (switch and (old,v) not in edges[t]):
                    continue
                value=total+costs[t][v]+(edges[t][old,v] if switch else 0)
                key=(v,nk)
                if key not in new or value<new[key]:
                    new[key]=value;back[key]=(old,k)
        states=new;parents.append(back)
    best=min(states,key=lambda k:(states[k],k[1],k[0])); total=states[best]
    schedule=[best[0]]; key=best
    for back in reversed(parents):
        key=back[key];schedule.append(key[0])
    schedule.reverse()
    def schedule_cost(schedule):
        if len(schedule)!=len(windows) or any(v not in ids for v in schedule):
            raise ValueError('schedule coverage')
        value=setup[schedule[0]]+sum(c[v] for c,v in zip(costs,schedule))
        for t in range(1,len(schedule)):
            if schedule[t]!=schedule[t-1]:
                edge=(schedule[t-1],schedule[t])
                if edge not in edges[t]: raise ValueError('schedule has unmeasured migration')
                value+=edges[t][edge]
        return value
    output=dict(schema=1,binding=binding,fixed_ns=fixed,best_fixed=min(fixed,key=fixed.get),
                hindsight_ns=total,hindsight_schedule=schedule,hindsight_switches=best[1],
                available_switching_gain_ns=max(0.,min(fixed.values())-total),resource_limits=limits,
                scope='Median replay cost diagnostic at declared window boundaries; hindsight sees the future. Not an executable policy or a performance guarantee.')
    if 'controller' in data:
        c=data['controller'];model=schedule_cost(c['schedule']);actual=price(c['elapsed_ns'])
        output.update(controller_replay_ns=model,controller_elapsed_ns=actual,
                      controller_choice_gap_ns=model-total,execution_residual_ns=actual-model)
        output['residual_scope']='Residual includes host overhead, timing noise and replay/session differences; not a causal overhead estimate.'
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',type=Path);p.add_argument('-o',type=Path,required=True);p.add_argument('--max-switches',type=int,default=32)
    a=p.parse_args();write(a.o,analyze(read(a.input,64_000_000),a.max_switches))
if __name__=='__main__':main()
