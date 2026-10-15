"""Validate synthetic proposal inputs through the original checked source.

A false ingress guard rejects a proposed input, never a theorem or candidate.
Every requested phase must retain calls. Supplied workload errors are unchanged.
"""
import hashlib,json
from ink_knowledge import identity
from .value_transfer import transfer

def input_identity(value):
    # Measurement packets preserve floats/signed zero and exact integers.
    # Canonical knowledge JSON itself deliberately contains no float numbers.
    return identity(transfer(value))

class ProbeInputError(ValueError):
    def __init__(self,message,receipt):
        super().__init__(message)
        self.input_validation=receipt

def reference_phases(planner,spec,remaining,*,automatic=False):
    raw=planner.original.read_bytes()if automatic else None
    guards=json.loads(raw)['program'].get('contracts',{}).get('precondition_checks',{})if automatic else {}
    phases={};cache={};rejected=[];guard_calls=0;reference_calls=0;reused=0
    def invoke(function,args):
        file=planner.directory/'reference-args.json';file.write_text(json.dumps(args,allow_nan=False))
        value=planner.invoke('run',planner.original,function,file,'--core',timeout=remaining())
        return json.loads(value)if isinstance(value,str)else value
    for phase,rows in spec['phases'].items():
        phases[phase]=[]
        for index,row in enumerate(rows):
            # One acquisition captures one original source; boundary encodings
            # do not alter the reference value. The input budget bounds this cache.
            key=input_identity([row['function'],row['args']])if automatic else None
            if automatic and key in cache:
                accepted,expected=cache[key];reused+=1
            else:
                accepted=True;expected=None;guard=guards.get(row['function'])
                if guard is not None:
                    condition=invoke(guard,row['args']);guard_calls+=1
                    if type(condition)is not bool:raise ValueError('source precondition guard did not return Bool')
                    accepted=condition
                if accepted:
                    # Guard errors and errors from an admitted call propagate;
                    # only a checked source guard returning false is filtered.
                    expected=invoke(row['function'],row['args']);reference_calls+=1
                if automatic:cache[key]=(accepted,expected)
            if accepted:phases[phase].append(dict(row,expected=expected))
            else:rejected.append(dict(phase=phase,index=index,function=row['function'],input_sha256=key,reason='source precondition is false'))
    receipt=dict(semantics='ink-automatic-probe-inputs-v1',proposed_calls=sum(map(len,spec['phases'].values())),accepted_calls=sum(map(len,phases.values())),
                 phases={phase:dict(proposed=len(spec['phases'][phase]),accepted=len(rows))for phase,rows in phases.items()},
                 rejected=rejected,guard_executions=guard_calls,reference_executions=reference_calls,reused_calls=reused,
                 scope='Checked source guard validation of synthetic inputs; not proof authority or observed usage')
    if automatic:
        if planner.original.read_bytes()!=raw:raise ValueError('source changed during automatic probe validation')
        receipt['source_sha256']=hashlib.sha256(raw).hexdigest()
        missing=[phase for phase,rows in phases.items()if not rows]
        receipt['complete']=not missing
        if missing:raise ProbeInputError('automatic probe phases have no source-admissible calls: '+', '.join(missing),receipt)
        accepted={phase:[{k:v for k,v in row.items()if k!='expected'}for row in rows]for phase,rows in phases.items()}
        receipt['accepted_inputs_sha256']=input_identity(dict(schema=spec['schema'],phases=accepted,profile=spec['profile']))
    return phases,receipt
