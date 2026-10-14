import unittest
from ink_planner.adaptive_state import fit
class AdaptiveProfile(unittest.TestCase):
 def fixture(self):
  binding=dict(source='a'*64,compiler='b'*64,target='device',boundary='owned-serial-state-v1',variants=dict(direct='c'*64,indexed='d'*64))
  bundle=dict(binding=binding)
  observations=dict(binding=binding,variants={n:dict(cells={'action:read@s':[10,11,12]},peak_bytes=123)for n in binding['variants']},migrations=[dict(**{'from':'direct','to':'indexed'},samples_ns=[5,6,7],peak_bytes=300,min_state_elements=0,max_state_elements=100)])
  return bundle,observations
 def test_empirical_bounds_and_migration_costs(self):
  b,o=self.fixture();v=fit(b,o);self.assertEqual(v['profile']['variants']['direct']['cells']['action:read@s'],dict(low_ns=10,high_ns=12));self.assertEqual(v['profile']['migrations'][0]['high_ns'],7)
 def test_stale_and_incomplete_evidence(self):
  for mutate in [lambda o:o.update(binding={}),lambda o:o['variants']['direct'].update(peak_bytes=None),lambda o:o['variants']['direct']['cells'].update({'action:read@s':[1,2]}),lambda o:o['variants']['direct']['cells'].update({'action:read@s':[1,float('nan'),3]}),lambda o:o['migrations'].append(o['migrations'][0])]:
   b,o=self.fixture();mutate(o)
   with self.assertRaises(ValueError):fit(b,o)
if __name__=='__main__':unittest.main()
