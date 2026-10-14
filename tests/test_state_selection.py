import copy,unittest
from ink_planner.state_selection import choose

def report(candidate=60,phases=None):
 phases=phases or {'update':10000,'module':1,'restore':1}
 rows=[]
 for i,load in enumerate([1,2,4]):
  for p in phases:
   for plan,cost in [('base',100),('candidate',candidate)]:
    n=1 if p=='module'else 9
    rows.append(dict(measurement=plan,process=i,phase=p,phase_kind='module_load'if p=='module'else'restore'if p=='restore'else'history',complete_call_ns=[str(cost*load)]*n))
 return dict(pairing='interleaved-state-lifecycle-v1',processes=3,rows=rows)

class PairedStateSelection(unittest.TestCase):
 def choose(self,r,profile=None,eligible=None):return choose(r,profile or {'update':10000,'module':1,'restore':1},'base',eligible or ['base','candidate'])
 def test_shared_load_variation_does_not_hide_consistent_lifecycle_gain(self):
  selected,receipt=self.choose(report());self.assertEqual(selected,'candidate');self.assertEqual(receipt['estimates']['candidate']['paired_process_ratios'],['3/5']*3)
 def test_tie_minimum_gain_and_one_slow_process_retain_baseline(self):
  for cost in [90,95,100]:self.assertEqual(self.choose(report(cost))[0],'base')
  r=report();r['rows'][-1]['complete_call_ns']=['100000000']*9;self.assertEqual(self.choose(r)[0],'base')
 def test_module_and_restore_costs_can_reverse_hot_winner(self):
  for phase in ['module','restore']:
   r=report()
   for row in r['rows']:
    if row['measurement']=='candidate'and row['phase']==phase:row['complete_call_ns']=['100000000']*len(row['complete_call_ns'])
   self.assertEqual(self.choose(r)[0],'base')
 def test_legacy_unpaired_missing_memory_or_phases_cannot_switch(self):
  r=report();r.pop('pairing');self.assertEqual(self.choose(r)[0],'base')
  r=report();r['rows'].pop();self.assertEqual(self.choose(r)[0],'base')
  self.assertEqual(self.choose(report(),eligible=['base'])[0],'base')
  r=report();r['processes']=2;self.assertEqual(self.choose(r)[0],'base')
  r=report();r['rows'][-1]['complete_call_ns']=['1']*8;self.assertEqual(self.choose(r)[0],'base')
 def test_duplicate_process_phase_is_rejected(self):
  r=report();r['rows'].append(copy.deepcopy(r['rows'][0]));
  with self.assertRaisesRegex(ValueError,'duplicate'):self.choose(r)
