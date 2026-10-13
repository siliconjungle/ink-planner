import json,tempfile,unittest
from pathlib import Path
from ink_planner.state_measurements import read_workload,transfer,fingerprints
from ink_planner.offline_measurements import independent_workloads
class StateWorkloads(unittest.TestCase):
    def workload(self,key=1):
        return dict(schema=2,setup=[dict(call='seed',args=[0,3])],
            phases={'initialise':dict(kind='initialise'),'update':dict(kind='history',calls=[dict(call='write',args=[key,2**64-1]),dict(call='read',args=[])]),'restore':dict(kind='restore')},
            profile={'initialise':1,'update':100,'restore':1},workload=dict(shape={},distribution='test',concurrency=1,residency='restored'))
    def test_state_history_holdout_is_disjoint_while_reads_and_setup_may_recur(self):
        with tempfile.TemporaryDirectory()as tmp:
            a,b=Path(tmp)/'a.json',Path(tmp)/'b.json'
            a.write_text(json.dumps(self.workload()));b.write_text(json.dumps(self.workload(2)))
            independent_workloads(a,b)
            b.write_bytes(a.read_bytes())
            with self.assertRaisesRegex(ValueError,'histories overlap'):independent_workloads(a,b)
    def test_lifecycle_fields_and_positive_objective_are_bounded(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp)/'workload.json'
            for change in [lambda v:v['profile'].update(update=-1),lambda v:v['phases']['restore'].update(calls=[]),lambda v:v['phases']['update']['calls'][0].update(extra=True),lambda v:v['profile'].update(initialise=0,update=0,restore=0)]:
                v=self.workload();change(v);p.write_text(json.dumps(v))
                with self.assertRaises(ValueError):read_workload(p)
            p.write_text(json.dumps(self.workload()));v,d=read_workload(p);self.assertEqual(v,self.workload());self.assertEqual(len(d),64)
    def test_explicit_transfer_preserves_large_integers_negative_zero_and_record_names(self):
        v={'integer':'a user field','InkInteger':'18446744073709551615','number':2**64-1,'zero':-0.0,'nested':[False,None]}
        packed=transfer(v)
        self.assertEqual(packed[0],'object')
        fields=dict(packed[1])
        self.assertEqual(fields['number'],['integer','18446744073709551615'])
        self.assertEqual(fields['zero'],['number','-0.0'])
        self.assertEqual(fields['InkInteger'],['string','18446744073709551615'])
    def test_different_complete_transition_sequences_have_different_fingerprints(self):
        v=self.workload();a=fingerprints(v);v['phases']['update']['calls'].reverse()
        self.assertFalse(a&fingerprints(v))
