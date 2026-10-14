"""Generate Ink programs, discover LLVM/fact alternatives and publish proved laws.

This is offline acquisition. Unchanged/unsupported/unproved cases remain in the
receipt. Every published law passes Ink's independent checker. Performance and
held-out workload selection remain separate gates.
"""
import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from ink_knowledge import Store, canonical, identity, semantic_entry
from producers.author_word_identities import checked_law
from producers.word_corpus import generate
from .llvm_learning import discover, discover_facts, parameters
from .learning_provenance import capture, verify


def run(compiler, clang, output, store_root, *, seed=0, count=32, depth=4, seconds=5):
    compiler, clang = Path(compiler).resolve(), Path(clang).resolve()
    output, store_root = Path(output).resolve(), Path(store_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Never overwrite evidence from a previous acquisition.
    if (output/'manifest.json').exists():
        raise ValueError('Choose a fresh acquisition output directory')
    toolchain = capture(output/'tool-sources')
    source, manifest = generate(seed, count, depth)
    path, core, subject = output/'generated.ink', output/'core.json', output/'subject.json'
    path.write_text(source)
    manifest.update(source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    compiler_sha256=hashlib.sha256(compiler.read_bytes()).hexdigest(),
                    clang_sha256=hashlib.sha256(clang.read_bytes()).hexdigest(),
                    clang_version=subprocess.check_output([str(clang),'--version'],text=True,timeout=30),
                    scope='Generated development inputs, not held-out generalisation evidence',
                    toolchain=toolchain)
    (output/'manifest.json').write_bytes(canonical(manifest))
    for args in [('emit-core',path,'-o',core),('emit-semantic',core,'--core','-o',subject)]:
        subprocess.run([str(compiler),*map(str,args)],check=True,capture_output=True,timeout=60)
    semantic=json.loads(subject.read_bytes())
    rows=[];objects={};names={}
    for function in manifest['functions']:
        name=function['name'];original=semantic['functions'][name]
        case=output/name;case.mkdir()
        suggestions=[]
        for engine in ('llvm','facts'):
            started=time.monotonic_ns()
            try:
                if engine=='llvm':
                    candidate, params, provenance=discover(original,clang,case)
                    proposed=[('llvm',candidate)]
                else:
                    proposed, provenance=discover_facts(original);params=parameters(original)
                if not proposed:rows.append(dict(function=name,engine=engine,status='no-candidate'))
                for label,candidate in proposed:
                    if candidate==original:
                        rows.append(dict(function=name,engine=engine,status='unchanged'));continue
                    if canonical(candidate) in suggestions:
                        rows.append(dict(function=name,engine=engine,status='duplicate-proposal'));continue
                    suggestions.append(canonical(candidate))
                    goal=dict(params=params,conditions=[],**{'from':original,'to':candidate})
                    try:
                        proposal,proof=checked_law(compiler,core,goal,case,seconds)
                        obj=semantic_entry(proposal);ident=identity(obj)
                        objects[ident]=obj;names[f'llvm-learning/{seed}/{name}/{label}']=ident
                        rows.append(dict(function=name,engine=engine,status='checked',law=ident,
                                         elapsed_ns=time.monotonic_ns()-started,provenance=provenance,proof=proof))
                    except (ValueError,subprocess.TimeoutExpired) as error:
                        rows.append(dict(function=name,engine=engine,status='unproved',reason=str(error)))
            except (ValueError,subprocess.TimeoutExpired) as error:
                rows.append(dict(function=name,engine=engine,status='unsupported',reason=str(error)))
            (output/'attempts.json').write_bytes(canonical(rows))
        print(json.dumps(dict(function=name,checked=sum(r.get('status')=='checked' for r in rows if r['function']==name))),flush=True)
    if hashlib.sha256(compiler.read_bytes()).hexdigest()!=manifest['compiler_sha256'] or hashlib.sha256(clang.read_bytes()).hexdigest()!=manifest['clang_sha256'] or hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['source_sha256']:
        raise ValueError('Acquisition inputs changed')
    verify(toolchain)
    store=(Store(store_root).extend(objects.values(),names) if Store.is_published(store_root)
           else Store.publish(store_root,objects.values(),names))
    receipt=dict(schema=1,seed=seed,programs=count,checked_unique_laws=len(objects),snapshot=store.snapshot_id,
                 manifest_sha256=hashlib.sha256((output/'manifest.json').read_bytes()).hexdigest(),
                 statuses={status:sum(r['status']==status for r in rows) for status in sorted({r['status'] for r in rows})},
                 profitability='unmeasured; no runtime policy installed')
    (output/'receipt.json').write_bytes(canonical(receipt))
    return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--compiler',type=Path,required=True);p.add_argument('--clang',type=Path,required=True)
    p.add_argument('--output-store',type=Path,required=True);p.add_argument('-o',type=Path,required=True)
    p.add_argument('--seed',type=int,default=0);p.add_argument('--count',type=int,default=32)
    p.add_argument('--depth',type=int,default=4);p.add_argument('--seconds',type=int,default=5)
    a=p.parse_args()
    if not 1<=a.seconds<=120:p.error('seconds must be in 1..120')
    print(json.dumps(run(a.compiler,a.clang,a.o,a.output_store,seed=a.seed,count=a.count,depth=a.depth,seconds=a.seconds)))


if __name__=='__main__':main()
