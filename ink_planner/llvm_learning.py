"""Offline LLVM suggestions, admitted only by ordinary Ink equality proofs.

The bridge covers total first-order unsigned word/Boolean expressions. LLVM is
an untrusted proposal engine. No poison/undef, memory, control-flow graphs,
floating point, arbitrary calls or caller assumptions cross this boundary.
Returned flags are discarded and the resulting total Ink expression must be
proved equivalent for every input. Runtime never calls this module.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from ink_knowledge import Store, canonical, identity, semantic_entry
from producers.semantic_laws import value, var, word, term, op
from producers.author_word_identities import checked_law
from .learning_provenance import capture, verify

TYPES = {'U32': 'i32', 'U64': 'i64', 'Bool': 'i1'}
KINDS = {v: k for k, v in TYPES.items()}
OPS = {'binary:+': 'add', 'binary:-': 'sub', 'binary:*': 'mul',
       'call:word_and': 'and', 'call:word_or': 'or', 'call:word_xor': 'xor',
       'binary:&&': 'and', 'binary:||': 'or'}
PRED = {'binary:==': 'eq', 'binary:!=': 'ne', 'binary:<': 'ult',
        'binary:<=': 'ule', 'binary:>': 'ugt', 'binary:>=': 'uge'}


def kind(t):
    k = t.get('sort', {}).get('Value')
    if not isinstance(k, str) or k not in TYPES:
        raise ValueError('LLVM learning supports only u32/u64/Bool')
    return k


def parameters(source):
    params = {}
    remaining = 4096
    def visit(t, depth=0):
        nonlocal remaining
        remaining -= 1
        if depth > 64 or remaining < 0:
            raise ValueError('LLVM learning expression budget')
        kind(t)
        node = t['node']
        if 'Free' in node:
            name = node['Free']
            if name in params and params[name] != t['sort']:
                raise ValueError('Inconsistent parameter sorts')
            params[name] = t['sort']
        elif not ('Word' in node or 'Bool' in node or 'Op' in node):
            raise ValueError('Unsupported LLVM learning term')
        for child in node.get('Op', [None, []])[1]:
            visit(child, depth+1)
    visit(source)
    if len(params) > 32:
        raise ValueError('LLVM learning parameter budget')
    return dict(sorted(params.items()))


def export_ir(source):
    params = parameters(source)
    names = {n: '%p' + str(i) for i, n in enumerate(params)}
    lines, memo = [], {}
    def instruction(body):
        name = '%v' + str(len(lines))
        lines.append('  ' + name + ' = ' + body)
        return name
    def emit(t):
        key = canonical(t)
        if key in memo:
            return memo[key]
        ty = TYPES[kind(t)]
        node = t['node']
        if 'Free' in node:
            return names[node['Free']]
        if 'Word' in node:
            return str(node['Word'])
        if 'Bool' in node:
            return 'true' if node['Bool'] else 'false'
        name, args = node['Op']
        operands = [emit(a) for a in args]
        if name in OPS and len(args) == 2:
            result = instruction(f'{OPS[name]} {ty} {operands[0]}, {operands[1]}')
        elif name in PRED and len(args) == 2:
            result = instruction(f'icmp {PRED[name]} {TYPES[kind(args[0])]} {operands[0]}, {operands[1]}')
        elif name == 'call:word_not' and len(args) == 1:
            result = instruction(f'xor {ty} {operands[0]}, -1')
        elif name == 'unary:!' and len(args) == 1:
            result = instruction(f'xor i1 {operands[0]}, true')
        elif name == 'call:choose' and len(args) == 3:
            result = instruction(f'select i1 {operands[0]}, {ty} {operands[1]}, {ty} {operands[2]}')
        elif name in ('call:wrapping_shl', 'call:wrapping_shr') and len(args) == 2:
            width = 32 if ty == 'i32' else 64
            # LLVM shifts poison on oversized counts; Ink masks them. Materialise
            # that mask before LLVM sees the operation, including dynamic counts.
            count = instruction(f'and i32 {operands[1]}, {width-1}')
            if width == 64:
                count = instruction(f'zext i32 {count} to i64')
            shift = 'shl' if name.endswith('shl') else 'lshr'
            result = instruction(f'{shift} {ty} {operands[0]}, {count}')
        elif name == 'call:word_widen_u32_to_u64' and len(args) == 1:
            result = instruction(f'zext i32 {operands[0]} to i64')
        elif name == 'call:word_narrow_u64_to_u32' and len(args) == 1:
            result = instruction(f'trunc i64 {operands[0]} to i32')
        else:
            raise ValueError('Unsupported LLVM learning operation: ' + name)
        memo[key] = result
        return result
    returned = emit(source)
    signature = ', '.join(TYPES[s['Value']] + ' ' + names[n] for n, s in params.items())
    result = (f'define {TYPES[kind(source)]} @candidate({signature}) {{\nentry:\n' +
              '\n'.join(lines) + f'\n  ret {TYPES[kind(source)]} {returned}\n}}\n')
    return result, params


def import_ir(text, params, result_sort):
    if len(text) > 1_000_000:
        raise ValueError('LLVM output byte budget')
    functions = re.findall(r'^define\b[^\n]*@candidate\([^\n]*\)[^\n]*\{\n(.*?)^}', text, re.M | re.S)
    if len(functions) != 1:
        raise ValueError('Expected one LLVM candidate function')
    values = {'%p' + str(i): var(s, n) for i, (n, s) in enumerate(params.items())}
    returned = None
    nodes = 0
    def operand(token, ty):
        token = token.strip()
        if token in values:
            t = values[token]
            if TYPES[kind(t)] != ty:
                raise ValueError('LLVM operand type mismatch')
            return t
        if ty == 'i1' and token in ('true', 'false', '0', '1'):
            return term(value('Bool'), {'Bool': token in ('true', '1')})
        if ty in ('i32', 'i64') and re.fullmatch(r'-?\d+', token):
            return word(KINDS[ty], int(token) % (1 << int(ty[1:])))
        raise ValueError('Unsupported LLVM value: ' + token)
    for raw in functions[0].splitlines():
        line = raw.split(';', 1)[0].strip()
        if not line or re.fullmatch(r'[\w.]+:', line):
            continue
        if returned is not None:
            raise ValueError('LLVM instructions after return')
        ret = re.fullmatch(r'ret (i1|i32|i64) (.+)', line)
        if ret:
            returned = operand(ret[2], ret[1]); continue
        assignment = re.fullmatch(r'(%[\w.]+) = (.+)', line)
        if not assignment or assignment[1] in values:
            raise ValueError('Unsupported LLVM instruction: ' + line)
        dest, body = assignment.groups()
        binary = re.fullmatch(r'(add|sub|mul|and|or|xor|shl|lshr) ((?:(?:nuw|nsw|disjoint|exact) )*)(i1|i32|i64) ([^,]+), (.+)', body)
        cmp = re.fullmatch(r'icmp (eq|ne|ult|ule|ugt|uge) (i1|i32|i64) ([^,]+), (.+)', body)
        select = re.fullmatch(r'select i1 ([^,]+), (i1|i32|i64) ([^,]+), (i1|i32|i64) (.+)', body)
        cast = re.fullmatch(r'(zext|trunc) (?:(?:nneg|nuw|nsw) )*(i1|i32|i64) (.+) to (i1|i32|i64)', body)
        if binary:
            name, flags, ty, a, b = binary.groups()
            left, right = operand(a, ty), operand(b, ty)
            if ty == 'i1':
                if name not in ('and', 'or', 'xor'):
                    raise ValueError('Unsupported LLVM Boolean arithmetic')
                result = op(value('Bool'), {'and':'binary:&&', 'or':'binary:||', 'xor':'binary:!='}[name], left, right)
            elif name in ('shl', 'lshr'):
                count = right if ty == 'i32' else op(value('U32'), 'call:word_narrow_u64_to_u32', right)
                result = op(left['sort'], 'call:wrapping_' + ('shl' if name == 'shl' else 'shr'), left, count)
            else:
                operator = {'add':'binary:+', 'sub':'binary:-', 'mul':'binary:*',
                            'and':'call:word_and', 'or':'call:word_or', 'xor':'call:word_xor'}[name]
                result = op(left['sort'], operator, left, right)
        elif cmp:
            pred, ty, a, b = cmp.groups()
            result = op(value('Bool'), {v:k for k,v in PRED.items()}[pred], operand(a, ty), operand(b, ty))
        elif select:
            cond, ty, a, other_ty, b = select.groups()
            if ty != other_ty:
                raise ValueError('LLVM select type mismatch')
            result = op(value(KINDS[ty]), 'call:choose', operand(cond, 'i1'), operand(a, ty), operand(b, ty))
        elif cast:
            name, src, a, dst = cast.groups()
            arg = operand(a, src)
            if (name, src, dst) == ('zext', 'i32', 'i64'):
                result = op(value('U64'), 'call:word_widen_u32_to_u64', arg)
            elif (name, src, dst) == ('trunc', 'i64', 'i32'):
                result = op(value('U32'), 'call:word_narrow_u64_to_u32', arg)
            elif name == 'zext' and src == 'i1' and dst in ('i32', 'i64'):
                result = op(value(KINDS[dst]), 'call:choose', arg, word(KINDS[dst], 1), word(KINDS[dst], 0))
            else:
                raise ValueError('Unsupported LLVM cast')
        else:
            raise ValueError('Unsupported LLVM instruction: ' + body)
        nodes += 1
        if nodes > 4096 or len(canonical(result)) > 1_000_000:
            raise ValueError('LLVM imported expression budget')
        values[dest] = result
    if returned is None or returned['sort'] != result_sort:
        raise ValueError('LLVM return sort mismatch')
    parameters(returned)  # Bound expanded tree size/depth before proof work.
    return returned


def discover(source, clang, directory):
    ir, params = export_ir(source)
    before, after = directory / 'before.ll', directory / 'after.ll'
    before.write_text(ir)
    command = [str(clang), '-x', 'ir', '-O2', '-S', '-emit-llvm',
               '-target', 'wasm32-unknown-unknown', str(before), '-o', str(after)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError(result.stderr[-4000:])
    candidate = import_ir(after.read_text(), params, source['sort'])
    return candidate, params, dict(input_ir_sha256=hashlib.sha256(before.read_bytes()).hexdigest(),
                                  output_ir_sha256=hashlib.sha256(after.read_bytes()).hexdigest(),
                                  stderr=result.stderr, command=command)


def discover_facts(source):
    from producers.word_facts import analyse, candidates
    from dataclasses import asdict
    return list(candidates(source)), dict(facts=asdict(analyse(source)),
                                         authority='untrusted analysis; independent equality proof required')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--compiler', type=Path, required=True)
    p.add_argument('--clang', type=Path)
    p.add_argument('--proposal-engine', choices=('llvm', 'facts'), default='llvm')
    p.add_argument('--core', type=Path, required=True)
    p.add_argument('--function', required=True)
    p.add_argument('--output-store', type=Path, required=True)
    p.add_argument('--seconds', type=int, default=10)
    p.add_argument('-o', type=Path, required=True)
    a = p.parse_args()
    if not 1 <= a.seconds <= 120:
        p.error('solver seconds must be in 1..120')
    if a.proposal_engine == 'llvm' and a.clang is None:
        p.error('--clang is required for LLVM proposals')
    outputs = [a.o, a.o.with_suffix('.receipt.json'), a.o.with_suffix('.tools'),
               a.o.with_suffix('.before.ll'), a.o.with_suffix('.after.ll')]
    if any(path.exists() for path in outputs):
        p.error('Choose a fresh discovery output path; existing evidence is preserved')
    compiler, core = a.compiler.resolve(), a.core.resolve()
    clang = a.clang.resolve() if a.clang else None
    inputs = (compiler, core, clang) if clang else (compiler, core)
    hashes = {str(x): hashlib.sha256(x.read_bytes()).hexdigest() for x in inputs}
    version = subprocess.check_output([str(clang), '--version'], text=True, timeout=30) if clang else None
    with tempfile.TemporaryDirectory(prefix='ink-llvm-learning-') as tmp:
        d = Path(tmp)
        toolchain = capture(d/'tool-sources')
        subject = d / 'subject.json'
        subprocess.run([str(compiler), 'emit-semantic', str(core), '--core', '-o', str(subject)],
                       check=True, capture_output=True, timeout=60)
        semantic = json.loads(subject.read_bytes())
        source = semantic['functions'][a.function]
        if a.proposal_engine == 'llvm':
            candidate, params, receipt = discover(source, clang, d)
        else:
            alternatives, receipt = discover_facts(source)
            if not alternatives:
                raise ValueError('Word analysis found no candidate; source is unchanged')
            # This chooses what to prove, not what a runtime should execute.
            label, candidate = alternatives[0]
            params = parameters(source)
            receipt['proposal'] = label
        if candidate == source:
            print(json.dumps(dict(status='unchanged', changed=False,
                                  profitability='No candidate published')))
            return
        goal = dict(params=params, conditions=[], **{'from':source, 'to':candidate})
        proposal, proof = checked_law(compiler, core, goal, d, a.seconds)
        obj = semantic_entry(proposal)
        st = Store.publish(d / 'selected', [obj])
        package = dict(schema=1, semantics='ink-evidence-v1', input_core_sha256=semantic['input_core_sha256'],
                       knowledge=st.bundle([identity(obj)]), applications=[dict(function=a.function,
                       path=[], law=identity(obj), arguments={n:var(s,n) for n,s in params.items()}, premises=[])])
        selection = d / 'selection.json'; selection.write_bytes(canonical(package))
        subprocess.run([str(compiler), 'check-selection', str(core), str(selection)],
                       check=True, capture_output=True, timeout=60)
        for path, digest in hashes.items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ValueError('Acquisition input changed: ' + path)
        verify(toolchain)
        store = (Store(a.output_store).extend([obj]) if (a.output_store/'store/snapshot.json').exists()
                 else Store.publish(a.output_store, [obj]))
        a.o.parent.mkdir(parents=True, exist_ok=True)
        a.o.write_bytes(selection.read_bytes())
        shutil.copytree(d/'tool-sources', a.o.with_suffix('.tools'))
        receipt.update(schema=1, kind='ink-llvm-discovery-v1', inputs=hashes, clang_version=version,
                       proposal_engine=a.proposal_engine,
                       toolchain=toolchain,
                       law=identity(obj), snapshot=store.snapshot_id, proof=proof,
                       changed=candidate != source, profitability='unmeasured; discovery is not automatic selection')
        a.o.with_suffix('.receipt.json').write_bytes(canonical(receipt))
        for name in ('before.ll','after.ll'):
            if (d/name).exists():a.o.with_suffix('.' + name).write_bytes((d/name).read_bytes())
        print(json.dumps(dict(status='checked', law=identity(obj), changed=candidate != source,
                              profitability=receipt['profitability'])))


if __name__ == '__main__':
    main()
