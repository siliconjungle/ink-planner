import copy,unittest
from ink_planner.compiled_planning import fingerprint,explore
from producers.state_protocols import candidates,base
class WholeProtocolTests(unittest.TestCase):
 def state(self):
  return dict(source='a'*64,properties=[],core={'program':{'states':[],'records':{},'keeps':[{'name':'sum'}],'contracts':{}}},protocol_capabilities=[dict(action='write',preflight=True,direct_keep=None),dict(action='read',preflight=False,direct_keep=0)],maintained={'keeps':['sum']},emit_target='javascript')
 def test_protocol_axes_are_distinct_complete_implementation_states(self):
  s=self.state();rows=list(candidates(s));self.assertEqual(len(rows),3)
  seen={fingerprint(dict(s,protocol=r['step']['package']))for r in rows};self.assertEqual(len(seen),3)
  self.assertNotIn(fingerprint(s),seen)
 def test_transactional_source_requests_are_not_overridden(self):
  s=self.state();s['core']['program']['contracts']['action_protocols']={'write':'transactional'}
  rows=list(candidates(s));self.assertEqual(len(rows),1);self.assertEqual(rows[0]['step']['package']['actions'],[])
 def test_existing_protocol_composes_without_repeating_identical_edges(self):
  s=self.state();rows=list(candidates(s));s['protocol']=rows[-1]['step']['package'];self.assertEqual(list(candidates(s)),[])
 def test_no_maintained_proof_means_no_direct_getter_proposal(self):
  s=self.state();s['maintained']=None;rows=list(candidates(s));self.assertEqual(len(rows),1);self.assertEqual(rows[0]['step']['package']['direct_reads'],[])
 def test_snapshot_execution_is_an_independent_joint_plan_axis(self):
  s=self.state();s['core']['program']['states']=[dict(name='T',ty={'Table':['U64','U64']})]
  def check(p):
   state=copy.deepcopy(s)
   for step in p['steps']:state['protocol']=step['package']
   return state
  states,receipt=explore(dict(steps=[]),lambda state,pool:candidates(state),check,lambda state:0,time_seconds=10)
  self.assertEqual(receipt['stop_reason'],'fixed-point');self.assertEqual(len(states),8)
  self.assertEqual(len({fingerprint(v)for _,v in states.values()}),8)
 def test_graph_composes_protocol_and_read_axes_before_measurement(self):
  s=self.state()
  def check(p):
   state=copy.deepcopy(s)
   for step in p['steps']:state['protocol']=step['package']
   return state
  def expand(state,states):yield from candidates(state)
  states,receipt=explore(dict(steps=[]),expand,check,lambda s:0,time_seconds=10)
  self.assertEqual(receipt['stop_reason'],'fixed-point');self.assertEqual(len(states),4)
if __name__=='__main__':unittest.main()
