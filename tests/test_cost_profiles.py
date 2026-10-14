import unittest,tempfile,json
from pathlib import Path
from ink_knowledge import Store
from ink_knowledge.cost_models import load_model
from ink_planner.cost_profiles import fit
class CostProfiles(unittest.TestCase):
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

class ProductCosts(unittest.TestCase):
 def test_database_basis_fits_size_times_horizon_and_rejects_unbounded_basis(self):
  with tempfile.TemporaryDirectory() as d:
   st=Store.publish(d,[]);rows=[]
   for backend,start,rate in [('cpu',100,10),('gpu',10000,1)]:
    for n in [1,100,1000]:
     for k in [1,16,64]:
      observation=dict(schema=1,program='0'*64,entries=[],plan='1'*64,target=dict(hardware='fixture',driver='fixture',toolchain='fixture',backend=backend,bridge='packed-v1'),workload=dict(shape={'features':{'input_elements':n,'iterations':k}},distribution='session-fixture',concurrency=1,residency='captured-owned'),metrics=dict(complete_call_ns=[str(start+rate*n*k)]*3,peak_bytes=None))
      rows.append(dict(scope='resident-fixture',observation=st.observe(observation)))
   _,profile=fit(st,rows,'fixture-target')
   self.assertEqual(profile['schema'],2)
   model=profile['models']['resident-fixture']
   for backend,start,rate in [('cpu',100,10),('gpu',10000,1)]:
    p=model['backends'][backend];self.assertEqual(p['products'][0]['features'],['input_elements','iterations'])
    # Independent interior points; no held-out observations used for fitting.
    for n,k in [(64,8),(512,32)]:
     predicted=p['intercept_ns']+sum(p['terms'][f]*v for f,v in [('input_elements',n),('iterations',k)])+p['products'][0]['coefficient_ns']*n*k
     self.assertLess(abs(predicted-(start+rate*n*k))/(start+rate*n*k),.02)
   for basis in [[["missing","iterations"]],[["iterations"]],[["iterations"]*4],True]:
    with self.assertRaises(ValueError):fit(st,rows,'fixture-target',basis=basis)
