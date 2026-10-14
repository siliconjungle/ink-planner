"""Optional HNSW proposal hints over structural features, never proof authority.

Exact typed discovery and matching still enumerate every eligible law. The graph
only changes proposal ordering; errors restore ordinary ordering. These features
are a deterministic sketch, not a learned semantic embedding.
"""
import hashlib
import json
import math
import operator
import time

FEATURE_SCHEMA = 'ink-structural-sketch-v1-64'
MAX_CACHE_BYTES = 16_000_000


def sketch(term):
    vector = [0.0] * 64
    nodes = depth = 0
    def walk(t, level):
        nonlocal nodes, depth
        if level > 128 or nodes >= 65536: raise ValueError('structural sketch bound')
        nodes += 1; depth = max(depth, level)
        n = t['node']
        tag = n['Op'][0] if isinstance(n, dict) and 'Op' in n else next(iter(n)) if isinstance(n, dict) else str(n)
        token = json.dumps([t['sort'], tag], sort_keys=True, separators=(',', ':')).encode()
        bucket = int.from_bytes(hashlib.blake2b(token, digest_size=8).digest(), 'little') % 62
        vector[bucket] += 1
        children = n['Op'][1] if isinstance(n, dict) and 'Op' in n else [n['Lambda']] if isinstance(n, dict) and 'Lambda' in n else n['Cons'] if isinstance(n, dict) and 'Cons' in n else []
        for child in children: walk(child, level + 1)
    walk(term, 0)
    vector[62] = math.log1p(nodes); vector[63] = math.log1p(depth)
    norm = math.sqrt(sum(x*x for x in vector)) or 1
    return [x/norm for x in vector]


class Neighbourhood:
    def __init__(self, catalogue, *, seed=173, k=16, provider=None):
        if not 1 <= k <= 256 or not 0 <= seed < 2**31: raise ValueError('HNSW parameters')
        self.ids = tuple(sorted(set(catalogue['roots']))); self.k = k
        self.index = None; self.np = None; self.cache = {}; self.cache_bytes = 0
        self.receipt = dict(feature_schema=FEATURE_SCHEMA, entries=len(self.ids), seed=seed,
                            k=k, M=16, ef_construction=100, ef=64, queries=0, failures=[],
                            authority='ordering hints; complete exact candidate set and proof replay retained')
        start = time.perf_counter_ns()
        try:
            if not 1 <= len(self.ids) <= 65536: raise ValueError('HNSW catalogue size 1..65536')
            if provider is None:
                import hnswlib
                import numpy
            else: hnswlib, numpy = provider
            self.np = numpy
            rows = numpy.asarray([sketch(catalogue['objects'][i]['from']) for i in self.ids], dtype=numpy.float32)
            index = hnswlib.Index(space='l2', dim=64)
            index.init_index(max_elements=len(self.ids), ef_construction=100, M=16, random_seed=seed)
            index.add_items(rows, numpy.arange(len(self.ids)), num_threads=1)
            index.set_ef(max(64, k)); index.set_num_threads(1)
            self.index = index
        except Exception as error: self.receipt['failures'].append(str(error))
        self.receipt['setup_ns'] = time.perf_counter_ns() - start
        self.receipt['active'] = self.index is not None

    def hints(self, target):
        if self.index is None: return ()
        key = json.dumps(target, sort_keys=True, separators=(',', ':'))
        if key in self.cache: return self.cache[key]
        try:
            labels, _ = self.index.knn_query(self.np.asarray([sketch(target)], dtype=self.np.float32),
                                             k=min(self.k, len(self.ids)), num_threads=1)
            if len(labels)!=1 or len(labels[0])!=min(self.k,len(self.ids)):
                raise ValueError('invalid HNSW label shape')
            indices=[operator.index(i) for i in labels[0]]
            if any(isinstance(i,bool) for i in labels[0]) or any(not 0<=i<len(self.ids) for i in indices) or len(set(indices))!=len(indices):
                raise ValueError('invalid HNSW labels')
            result = tuple(self.ids[i] for i in indices)
            self.receipt['queries'] += 1
            size=len(key.encode())
            if size<=MAX_CACHE_BYTES:
                if len(self.cache)>=4096 or self.cache_bytes+size>MAX_CACHE_BYTES:
                    self.cache.clear();self.cache_bytes=0
                self.cache[key]=result;self.cache_bytes+=size
            return result
        except Exception as error:
            self.receipt['failures'].append(str(error)); self.index = None
            self.receipt['active'] = False
            self.cache.clear();self.cache_bytes=0
            return ()

    def order(self, pending, hinted, round_index):
        if self.index is None or not pending: return pending
        ordered = [c for c in pending if c[2][-1]['law'] in hinted]
        ordered += [c for c in pending if c[2][-1]['law'] not in hinted]
        if round_index % 2 == 0:
            first = pending[0]; ordered = [first] + [c for c in ordered if c is not first]
        return ordered
