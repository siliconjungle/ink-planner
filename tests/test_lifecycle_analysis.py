import itertools
import unittest
from ink_planner.lifecycle_analysis import analyze

class LifecycleAnalysis(unittest.TestCase):
 def fixture(self):
  return dict(schema=1,kind='ink-lifecycle-replay-v1',binding={'variants':dict(a='a'*64,b='b'*64)},setup_ns=dict(a=[2]*3,b=[4]*3),windows=[dict(calls=10,snapshot_sha256='c'*64,costs_ns=dict(a=[x]*3,b=[y]*3),migrations=[dict(**{'from':a,'to':b},samples_ns=[3]*3)for a,b in [('a','b'),('b','a')]])for x,y in [(1,9),(10,1),(10,1),(1,9)]])
 def test_matches_exhaustive_schedules_with_switch_costs_and_budget(self):
  d=self.fixture()
  for budget in range(4):
   r=analyze(d,budget);all_costs=[]
   for s in itertools.product('ab',repeat=4):
    switches=sum(x!=y for x,y in zip(s,s[1:]))
    if switches<=budget:all_costs.append(d['setup_ns'][s[0]][0]+sum(w['costs_ns'][v][0] for w,v in zip(d['windows'],s))+3*switches)
   self.assertEqual(r['hindsight_ns'],min(all_costs))
   self.assertLessEqual(r['hindsight_ns'],min(r['fixed_ns'].values()))
 def test_missing_migration_is_ineligible_not_free(self):
  d=self.fixture()
  for w in d['windows']:w['migrations']=[]
  r=analyze(d);self.assertEqual(r['hindsight_ns'],min(r['fixed_ns'].values()));self.assertEqual(r['hindsight_switches'],0)
 def test_measured_resource_constraints_remove_edges_and_candidates(self):
  d=self.fixture();d['limits']=dict(memory_limit_bytes=100,max_pause_ns=2);d['peak_bytes']=dict(a=50,b=75)
  for w in d['windows']:
   for m in w['migrations']:m['peak_bytes']=90
  r=analyze(d);self.assertEqual(r['hindsight_switches'],0)
  d['limits']['memory_limit_bytes']=60;r=analyze(d);self.assertEqual(r['best_fixed'],'a');self.assertEqual(r['hindsight_schedule'],['a']*4)
 def test_real_controller_residual_and_invalid_data(self):
  d=self.fixture();d['controller']=dict(schedule=['a']*4,elapsed_ns=[30]*3)
  r=analyze(d);self.assertEqual(r['controller_replay_ns'],24);self.assertEqual(r['execution_residual_ns'],6)
  for bad in [float('nan'),-1,True]:
   d=self.fixture();d['windows'][0]['costs_ns']['a']=[bad]*3
   with self.assertRaises(ValueError):analyze(d)
  d=self.fixture();d['windows'][0]['costs_ns'].pop('b')
  with self.assertRaises(ValueError):analyze(d)
