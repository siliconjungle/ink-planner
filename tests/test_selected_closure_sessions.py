import json
from pathlib import Path
import tempfile
import unittest
from ink_planner.semantic_search import SelectionChecker, search, discovery_objects, discover_roots

class Store:
    def __init__(self): self.offered=[]
    def bundle(self, roots):
        self.offered.append(tuple(roots))
        return {'roots':list(roots),'objects':{}}

class SelectedClosureSessions(unittest.TestCase):
    def test_discovery_pages_are_independent_of_small_proof_budget(self):
        ids = [f'{i:064x}' for i in range(1100)]
        class Paged:
            def __init__(self): self.requests=[]
            def query(self, **kwargs):
                self.requests.append(kwargs)
                start=0 if kwargs['after'] is None else ids.index(kwargs['after'])+1
                return ids[start:start+kwargs['limit']]
        store=Paged()
        self.assertEqual(discover_roots(store, [{'Value':'U32'}]), set(ids))
        self.assertEqual([r['limit'] for r in store.requests], [1024,1024])
        self.assertEqual(store.requests[1]['after'], ids[1023])

    def test_discovery_does_not_construct_one_limited_authority_bundle(self):
        class ManyObjects:
            def object(self,ident):return {'dependencies': ['shared'] if ident!='shared' else []}
            def bundle(self,roots):raise AssertionError('unused aggregate authority bundle')
        objects=discovery_objects(ManyObjects(),[f'root-{i}' for i in range(1100)])
        self.assertEqual(len(objects),1101)

    def test_unused_heavy_discovery_is_not_admitted_and_invalid_choice_rejects(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);compiler=directory/'checker.py'
            compiler.write_text('''#!/usr/bin/env python3
import json,sys
roots=json.load(open(sys.argv[3]))['roots']
if 'heavy-unused' in roots:
    sys.stderr.write('semantic proof work/depth limit')
    raise SystemExit(1)
print(json.dumps({'ready':True}),flush=True)
for line in sys.stdin:
    print(json.dumps({'checked':'invalid' not in roots}),flush=True)
''')
            compiler.chmod(0o755);store=Store();checker=SelectionChecker(compiler,directory/'input.core',store,directory)
            x={'sort':{'Value':'U32'},'node':{'Free':'x'}}
            source={'sort':x['sort'],'node':{'Op':['call:example',[x]]}}
            base={'params':{'x':x['sort']},'from':source,'to':x,'conditions':[]}
            unused=dict(base,**{'from':{'sort':x['sort'],'node':{'Op':['call:unmatched',[x]]}}})
            try:
                applications,receipt=search({'functions':{'entry':source}},
                    {'roots':['heavy-unused','invalid','valid'],'objects':{'heavy-unused':unused,'invalid':base,'valid':base}},checker.check)
                self.assertEqual(applications[0]['law'],'valid')
                self.assertEqual(receipt['rejected'],1)
                self.assertNotIn(('heavy-unused',),store.offered)
                self.assertNotIn('invalid',checker.checked_roots)
                self.assertTrue(checker.check(applications))
                self.assertFalse(checker.check([{'law':'heavy-unused'}]))
                self.assertEqual(checker.rejected_closures,1)
            finally: checker.close()
    def test_premise_dependencies_and_bounded_session_eviction(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);compiler=directory/'checker.py'
            compiler.write_text('''#!/usr/bin/env python3
import json,sys
print(json.dumps({'ready':True}),flush=True)
for line in sys.stdin:print(json.dumps({'checked':True}),flush=True)
''');compiler.chmod(0o755)
            store=Store();checker=SelectionChecker(compiler,directory/'input.core',store,directory)
            try:
                for i in range(12):
                    self.assertTrue(checker.check([{'law':f'root-{i}','premises':[{'Use':{'law':'support'}}]}]))
                    self.assertLessEqual(len(checker.sessions),8)
                self.assertTrue(all('support' in roots for roots in store.offered))
            finally: checker.close()
            self.assertEqual(checker.sessions,{})

if __name__=='__main__':unittest.main()
