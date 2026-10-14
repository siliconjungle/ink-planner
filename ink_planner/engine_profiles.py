"""Offline full-engine observations; use the ordinary bounded cost-model fitter.

This importer checks measurement provenance, not program equality or timing
truth. Its scope never substitutes engine measurements into a session profile.
"""
import json,math
from ink_knowledge import identity
from .cost_profiles import fit

LIFECYCLE='engine-create/session-open/steps/full-read/session-close/engine-close-v1'

def fit_records(store,records):
    if not isinstance(records,list)or not 3<=len(records)<=32:
        raise ValueError('three fresh engine training processes required')
    processes=set();cohort=None;coverage=None;rows=[]
    for record in records:
        if record.get('schema')!=1 or record.get('mode')!='train' or record.get('lifecycle')!=LIFECYCLE:
            raise ValueError('complete engine training protocol required')
        process=record.get('process_id')
        if not isinstance(process,str)or not process or process in processes:
            raise ValueError('distinct training processes required')
        processes.add(process)
        current={k:record[k]for k in ['target_key','environment','device','source_hashes','training_protocol']}
        if cohort is not None and current!=cohort:raise ValueError('mixed engine measurement provenance')
        cohort=current
        if record['device'].get('isFallbackAdapter') is not False:
            raise ValueError('actual GPU adapter evidence required')
        cases=record.get('cases')
        if not isinstance(cases,list)or not 1<=len(cases)<=1024:raise ValueError('engine case budget')
        seen=set()
        for case in cases:
            context=case['context'];scope=context['scope']
            try:tag=json.loads(scope)[0]
            except (ValueError,TypeError,IndexError):raise ValueError('invalid engine scope')
            if tag!='ink-resident-engine-v1' or context.get('boundary')!='captured-owned':
                raise ValueError('session observations cannot fit an engine model')
            if case.get('validated')is not True or case.get('backends')!={'cpu':'cpu','gpu':'gpu'}:
                raise ValueError('validated explicit CPU and actual GPU execution required')
            key=identity(dict(program=case['core_sha256'],family=case['family'],context=context))
            if key in seen:raise ValueError('duplicate engine case')
            seen.add(key)
            for backend in ['cpu','gpu']:
                if len(rows)>=4096:raise ValueError('engine observation budget')
                samples=case['complete_engine_ns'][backend]
                if not isinstance(samples,list)or not 3<=len(samples)<=256 or any(type(n)not in (int,float)or not math.isfinite(n)or n<=0 for n in samples):
                    raise ValueError('complete engine timing samples required')
                observation=dict(schema=1,program=case['core_sha256'],entries=[],plan=case['core_sha256'],
                    target=dict(hardware=json.dumps(record['device'],sort_keys=True),driver=json.dumps(record['environment'],sort_keys=True),
                                toolchain=identity(record['source_hashes']),backend=backend,bridge='packed-v1'),
                    workload=dict(shape={'features':context['features'],'lifecycle':LIFECYCLE},
                                  distribution={'protocol':record['training_protocol'],'family':case['family']},concurrency=1,residency=context['boundary']),
                    metrics=dict(complete_call_ns=[str(max(1,round(n)))for n in samples],peak_bytes=None))
                rows.append(dict(scope=scope,observation=store.observe(observation)))
        if coverage is not None and seen!=coverage:raise ValueError('training process case coverage differs')
        coverage=seen
    ident,profile=fit(store,rows,cohort['target_key'])
    return ident,profile,rows
