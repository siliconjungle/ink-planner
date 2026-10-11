import unittest
from ink_planner.representation_selection import rank,phase_workload
class Store:
    def __init__(self,records):self.records=records
    def observations(self,program,plans,target,workload):
        return [r for r in self.records if r['program']==program and r['plan'] in plans and r['target']==target and r['workload']==workload]
class Selection(unittest.TestCase):
    def setUp(self):
        self.source='1'*64;self.base='2'*64;self.fast='3'*64;self.target={'hardware':'cpu'};self.workload={'distribution':'mixed'};self.records=[]
    def observe(self,plan,phase,cost,peak=100):
        self.records.append(dict(program=self.source,plan=plan,target=self.target,workload=phase_workload(self.workload,phase),metrics=dict(complete_call_ns=[str(cost)],peak_bytes=peak)))
    def choose(self,counts,**kw):return rank(Store(self.records),self.source,[self.base,self.fast],self.target,self.workload,counts,self.base,**kw)[0]
    def test_update_query_mix_and_initialisation_change_selection(self):
        for p,init,query,update in [(self.base,10,100,10),(self.fast,1000,1,30)]:
            for phase,cost in [('initialise',init),('action:query',query),('action:update',update)]:self.observe(p,phase,cost)
        self.assertEqual(self.choose({'initialise':1,'action:query':1,'action:update':100}),self.base)
        self.assertEqual(self.choose({'initialise':1,'action:query':100,'action:update':1}),self.fast)
    def test_migration_and_missing_memory_are_charged(self):
        self.observe(self.base,'action:query',100);self.observe(self.fast,'action:query',1,peak=None)
        self.observe(self.fast,'migrate:'+self.base,1000)
        self.assertEqual(self.choose({'action:query':1},migration_count=1),self.base)
        self.assertEqual(self.choose({'action:query':100},migration_count=1),self.fast)
        self.assertEqual(self.choose({'action:query':100},memory_limit=100),self.base)
    def test_missing_baseline_phase_or_incomparable_evidence_keeps_baseline(self):
        self.observe(self.fast,'action:query',1)
        self.assertEqual(self.choose({'action:query':100}),self.base)
        self.observe(self.base,'action:query',100)
        self.records[0]['target']={'hardware':'different'}
        self.assertEqual(self.choose({'action:query':100}),self.base)
    def test_no_zero_memory_or_impossible_baseline_budget(self):
        self.observe(self.base,'action:query',100,peak=None);self.observe(self.fast,'action:query',1,peak=0)
        with self.assertRaises(ValueError):self.choose({'action:query':100},memory_limit=100)
    def test_missing_phase_cannot_silently_satisfy_memory_limit(self):
        self.observe(self.base,'action:query',100)
        with self.assertRaises(ValueError):self.choose({'initialise':1,'action:query':1},memory_limit=100)
    def test_migration_evidence_binds_exact_bridge(self):
        self.observe(self.base,'action:query',100);self.observe(self.fast,'action:query',1)
        self.observe(self.fast,'migrate:old-bridge',1)
        selected,_=rank(Store(self.records),self.source,[self.base,self.fast],self.target,self.workload,{'action:query':100},self.base,migration_count=1,migration_phases={self.fast:'migrate:actual-bridge'})
        self.assertEqual(selected,self.base)
    def test_ties_and_zero_budget_are_deterministic(self):
        for plan in [self.base,self.fast]:self.observe(plan,'action:query',100)
        self.assertEqual(self.choose({'action:query':1}),self.base)
        for counts in [{},{'action:query':-1},{'action:query':True},{'action:query':0}]:
            with self.assertRaises(ValueError):self.choose(counts)
if __name__=='__main__':unittest.main()
