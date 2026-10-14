import copy,unittest
from ink_planner.compiled_planning import fingerprint,explore
from producers.state_protocols import candidates,base
class WholeProtocolTests(unittest.TestCase):
 def state(self):
  return dict(source='a'*64,properties=[],core={'program':{'states':[],'records':{},'keeps':[{'name':'sum'}],'contracts':{}}},protocol_capabilities=[dict(action='write',preflight=True,direct_keep=None),dict(action='read',preflight=False,direct_keep=0)],maintained={'keeps':['sum']},emit_target='javascript')
 def test_protocol_axes_are_distinct_complete_implementation_states(self):
  s=self.state();rows=list(candidates(s));self.assertEqual(len(rows),4)
  seen={fingerprint(dict(s,protocol=r['step']['package']))for r in rows};self.assertEqual(len(seen),4)
  self.assertNotIn(fingerprint(s),seen)
 def test_event_storage_is_independent_and_source_generic(self):
  s=self.state();s['core']['program']['events']={'arbitrary_event':'Bool'}
  rows=list(candidates(s));event=next(r for r in rows if r['label'].endswith('event-columns'));self.assertTrue(event['step']['package']['event_columns']);self.assertNotEqual(fingerprint(s),fingerprint(dict(s,protocol=event['step']['package'])))
  runs=next(r for r in rows if r['label'].endswith('event-runs'));self.assertTrue(runs['step']['package']['event_columns']);self.assertTrue(runs['step']['package']['event_runs']);self.assertNotEqual(fingerprint(dict(s,protocol=event['step']['package'])),fingerprint(dict(s,protocol=runs['step']['package'])))
  s['protocol']=runs['step']['package'];plain=next(r for r in candidates(s)if r['label'].endswith('event-columns'));self.assertFalse(plain['step']['package']['event_runs'])
 def test_transactional_source_requests_are_not_overridden(self):
  s=self.state();s['core']['program']['contracts']['action_protocols']={'write':'transactional'}
  rows=list(candidates(s));self.assertEqual(len(rows),2);self.assertTrue(all(r['step']['package']['actions']==[]for r in rows))
 def test_existing_protocol_composes_without_repeating_identical_edges(self):
  s=self.state();rows=list(candidates(s));s['protocol']=rows[-1]['step']['package'];again=list(candidates(s));self.assertEqual(len(again),1);self.assertTrue(again[0]['label'].endswith('inline-maintenance'));self.assertNotEqual(fingerprint(s),fingerprint(dict(s,protocol=again[0]['step']['package'])))
 def test_no_maintained_proof_means_no_direct_getter_proposal(self):
  s=self.state();s['maintained']=None;rows=list(candidates(s));self.assertEqual(len(rows),1);self.assertEqual(rows[0]['step']['package']['direct_reads'],[])
 def test_snapshot_execution_is_an_independent_joint_plan_axis(self):
  s=self.state();s['core']['program']['states']=[dict(name='T',ty={'Table':['U64','U64']})]
  def check(p):
   state=copy.deepcopy(s)
   for step in p['steps']:state['protocol']=step['package']
   return state
  states,receipt=explore(dict(steps=[]),lambda state,pool:candidates(state),check,lambda state:0,time_seconds=10)
  self.assertEqual(receipt['stop_reason'],'fixed-point');self.assertEqual(len(states),32)
  self.assertEqual(len({fingerprint(v)for _,v in states.values()}),32)
 def test_graph_composes_protocol_and_read_axes_before_measurement(self):
  s=self.state()
  def check(p):
   state=copy.deepcopy(s)
   for step in p['steps']:state['protocol']=step['package']
   return state
  def expand(state,states):yield from candidates(state)
  states,receipt=explore(dict(steps=[]),expand,check,lambda s:0,time_seconds=10)
  self.assertEqual(receipt['stop_reason'],'fixed-point');self.assertEqual(len(states),8)
if __name__=='__main__':unittest.main()
