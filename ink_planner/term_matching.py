"""Disposable exact-subtree matching cache and compiled structural filters.

Filters may only reject structural mismatches. Every surviving match goes through
ordinary binder-aware matching, and every application still needs kernel replay.
Terms are treated as immutable during one search invocation.
"""
from collections import OrderedDict

class Matcher:
    def __init__(self,objects,roots,key,children,match,substitute,memory_bytes):
        self.objects,self.key,self.children=objects,key,children
        self.match,self.substitute=match,substitute
        self.index={};self.filters={};self.cache=OrderedDict();self.bytes=0
        self.limit=max(1,memory_bytes//8)
        self.stats=dict(cache_hits=0,cache_misses=0,structural_rejections=0,match_attempts=0,evictions=0)
        self.add(roots)
    def add(self,roots):
        """Newly exposed template instances invalidate negative subtree hints."""
        added=False
        for ident in roots:
            if ident in self.filters:continue
            law=self.objects[ident];root=self.head(law['from'],law['params'])
            self.index.setdefault(root,[]).append(ident)
            self.filters[ident]=self.signatures(law['from'],law['params'])
            added=True
        if added:self.cache.clear();self.bytes=0
    def head(self,t,params=()):
        n=t['node'];sort=self.key(t['sort'])
        if isinstance(n,dict):
            if 'Free'in n and n['Free']in params:return sort,'*'
            if 'Op'in n:return sort,'Op:'+n['Op'][0]
            return sort,next(iter(n))
        return sort,str(n)
    def signatures(self,t,params=()):
        out=set()
        def visit(term,path):
            node=term['node']
            if isinstance(node,dict)and 'Free'in node and node['Free']in params:return
            out.add((path,*self.head(term)))
            for i,child in enumerate(self.children(term)):visit(child,path+(i,))
        visit(t,());return frozenset(out)
    def matches(self,target):
        root=self.head(target)
        ids=self.index.get(root,[])+self.index.get((root[0],'*'),[])
        # No candidate can match this typed head. Avoid serialising an entire
        # subtree just to cache a miss; add() exposes new heads immediately.
        if not ids:
            self.stats['cache_misses']+=1
            return []
        fingerprint=self.key(target)
        if fingerprint in self.cache:
            self.stats['cache_hits']+=1
            result,size=self.cache.pop(fingerprint);self.cache[fingerprint]=(result,size)
            return result
        self.stats['cache_misses']+=1
        signature=self.signatures(target);result=[]
        for ident in ids:
            law=self.objects[ident]
            if not self.filters[ident]<=signature:
                self.stats['structural_rejections']+=1;continue
            args={};self.stats['match_attempts']+=1
            if not self.match(law['from'],target,law['params'],args)or set(args)!=set(law['params']):continue
            new=self.substitute(law['to'],args)
            if new!=target:result.append((ident,args,new))
        size=len(fingerprint.encode())+len(self.key(result).encode())
        if size<=self.limit:
            while self.cache and (self.bytes+size>self.limit or len(self.cache)>=4096):
                _,(_,old)=self.cache.popitem(last=False);self.bytes-=old;self.stats['evictions']+=1
            self.cache[fingerprint]=(result,size);self.bytes+=size
        return result
    def receipt(self):return dict(self.stats,entries=len(self.cache),serialized_bytes=self.bytes,byte_limit=self.limit)
