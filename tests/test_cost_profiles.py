import unittest,tempfile,json,copy
from pathlib import Path
from ink_knowledge import Store
from ink_knowledge.cost_models import load_model
from ink_planner.cost_profiles import fit
class CostProfiles(unittest.TestCase):
 def test_authoring_respects_consumer_scope_and_target_budgets(self):
  with tempfile.TemporaryDirectory()as d:
   st=Store.publish(d,[])
   o=dict(schema=1,program='0'*64,entries=[],plan='1'*64,target=dict(hardware='fixture',driver='fixture',toolchain='fixture',backend='cpu',bridge='packed-v1'),workload=dict(shape={'features':{'input_elements':1}},distribution='fixture',concurrency=1,residency='captured-owned'),metrics=dict(complete_call_ns=['100'],peak_bytes=None))
   observation=st.observe(o)
   with self.assertRaisesRegex(ValueError,'model budget'):fit(st,[dict(scope=str(i),observation=observation)for i in range(257)],'fixture')
   with self.assertRaisesRegex(ValueError,'invalid fitting request'):fit(st,[dict(scope='one',observation=observation)],'x'*4097)
   self.assertFalse((st.data/'cost-models').exists())
 def test_mixed_measurement_cohorts_cannot_be_pooled_under_one_label(self):
  original=dict(schema=1,program='0'*64,entries=[],plan='1'*64,target=dict(hardware='host-a',driver='driver-a',toolchain='compiler-a',backend='cpu',bridge='packed-v1'),workload=dict(shape={'features':{'input_elements':100},'ordering':'ascending'},distribution='uniform',concurrency=1,residency='captured-owned'),metrics=dict(complete_call_ns=['1000'],peak_bytes=None))
  changes=[(('target','hardware'),'host-b'),(('target','driver'),'driver-b'),
           (('target','toolchain'),'compiler-b'),(('target','bridge'),'packed-v2'),
           (('workload','distribution'),'skewed'),(('workload','concurrency'),2),
           (('workload','shape','ordering'),'descending'),(('program',),'2'*64),(('plan',),'3'*64)]
  for path,value in changes:
   with self.subTest(path=path),tempfile.TemporaryDirectory()as d:
    st=Store.publish(d,[]);changed=copy.deepcopy(original);cursor=changed
    for part in path[:-1]:cursor=cursor[part]
    cursor[path[-1]]=value
    changed['workload']['shape']['features']['input_elements']=200
    rows=[dict(scope='same-label',observation=st.observe(o))for o in [original,changed]]
    with self.assertRaisesRegex(ValueError,'incomparable observation provenance'):fit(st,rows,'host-label')
 def test_backend_specific_toolchains_remain_comparable(self):
  with tempfile.TemporaryDirectory()as d:
   st=Store.publish(d,[]);rows=[]
   for backend in ['cpu','gpu']:
    for n in [100,200]:
     o=dict(schema=1,program='0'*64,entries=[],plan='1'*64,target=dict(hardware='fixture',driver=backend+'-driver',toolchain=backend+'-compiler',backend=backend,bridge='packed-v1'),workload=dict(shape={'features':{'input_elements':n}},distribution='fixture',concurrency=1,residency='captured-owned'),metrics=dict(complete_call_ns=[str(n+100)],peak_bytes=None))
     rows.append(dict(scope='compiled-fixture',observation=st.observe(o)))
   _,profile=fit(st,rows,'fixture-target')
   self.assertEqual(set(profile['models']['compiled-fixture']['backends']),{'cpu','gpu'})
 def test_fit_persists_provenance_and_intersects_domains(self):
  with tempfile.TemporaryDirectory() as d:
   st=Store.publish(d,[]);rows=[]
   for backend,base,slope,ns in [('cpu',100,10,[0,100,200]),('gpu',1000,1,[100,200,300])]:
    for n in ns:
     o=dict(schema=1,program='0'*64,entries=[],plan='1'*64,target=dict(hardware='fixture',driver='fixture',toolchain='fixture',backend=backend,bridge='packed-v1'),workload=dict(shape={'features':{'input_elements':n}},distribution='fixture',concurrency=1,residency='captured-owned'),metrics=dict(complete_call_ns=[str(base+slope*n)],peak_bytes=None))
     rows.append(dict(scope='compiled-fixture',observation=st.observe(o)))
   id,profile=fit(st,rows,'fixture-target');m=profile['models']['compiled-fixture']
   self.assertEqual(m['domains']['input_elements'],[100,200]);self.assertEqual(load_model(st,id),profile)
   self.assertAlmostEqual(m['backends']['cpu']['terms']['input_elements'],10,places=4)
   # Cost models are deliberately outside mathematical snapshot membership.
   self.assertEqual(st.members(),{})
   record=json.loads((st.data/'cost-models'/f'{id}.json').read_text());self.assertEqual(len(record['observations']),6)
   record['observations'][0]='0'*64;(st.data/'cost-models'/f'{id}.json').write_text(json.dumps(record))
   with self.assertRaises(ValueError):load_model(st,id)
 def test_missing_or_forged_evidence_cannot_fit(self):
  with tempfile.TemporaryDirectory() as d:
   st=Store.publish(d,[])
   with self.assertRaises(ValueError):fit(st,[],'fixture')
   with self.assertRaises((ValueError,FileNotFoundError)):fit(st,[dict(scope='compiled',observation='0'*64)],'fixture')
if __name__=='__main__':unittest.main()
