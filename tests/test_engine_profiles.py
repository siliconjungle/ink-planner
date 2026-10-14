import copy,json,tempfile,unittest
from pathlib import Path
from ink_knowledge import Store
from ink_planner.engine_profiles import fit_records,LIFECYCLE

def records():
    result=[]
    for process in range(3):
        cases=[]
        for n in [1,100,1000]:
            for k in [1,16,64]:
                cases.append(dict(family='fixture',core_sha256='0'*64,validated=True,backends={'cpu':'cpu','gpu':'gpu'},
                    context=dict(scope=json.dumps(['ink-resident-engine-v1','compiled']),boundary='captured-owned',features={'input_elements':n,'iterations':k}),
                    complete_engine_ns={b:[base+rate*n*k]*3 for b,base,rate in [('cpu',100,10),('gpu',10000,1)]}))
        result.append(dict(schema=1,mode='train',lifecycle=LIFECYCLE,process_id=str(process),target_key='device',
            environment={'browser':'fixture'},device={'isFallbackAdapter':False},source_hashes={'host':'1'*64},training_protocol={'seed':17},cases=cases))
    return result

class EngineProfiles(unittest.TestCase):
    def test_complete_costs_fit_with_immutable_provenance_and_no_session_alias(self):
        with tempfile.TemporaryDirectory()as d:
            store=Store.publish(d,[]);ident,profile,rows=fit_records(store,records())
            self.assertEqual(len(rows),54);self.assertEqual(len(profile['models']),1)
            scope=next(iter(profile['models']));self.assertEqual(json.loads(scope)[0],'ink-resident-engine-v1')
            for backend,start,rate in [('cpu',100,10),('gpu',10000,1)]:
                model=profile['models'][scope]['backends'][backend]
                for n,k in [(64,8),(512,32)]:
                    estimate=model['intercept_ns']+model['terms']['input_elements']*n+model['terms']['iterations']*k+model['products'][0]['coefficient_ns']*n*k
                    self.assertLess(abs(estimate-(start+rate*n*k))/(start+rate*n*k),.02)
            saved=json.loads((store.data/'cost-models'/f'{ident}.json').read_text())
            self.assertEqual(set(saved['observations']),{r['observation']for r in rows})
            self.assertFalse(store.members())
    def test_incomparable_incomplete_and_evaluation_observations_do_not_publish_a_model(self):
        variants=[]
        for change in [lambda r:r[0].update(mode='evaluation'),lambda r:r[0].update(lifecycle='session'),
                       lambda r:r[1].update(process_id='0'),lambda r:r[1]['source_hashes'].update(host='2'*64),
                       lambda r:r[1]['device'].update(isFallbackAdapter=True),lambda r:r[1]['cases'].pop(),
                       lambda r:r[0]['cases'].append(copy.deepcopy(r[0]['cases'][0])),
                       lambda r:r[0]['cases'][0]['context'].update(scope=json.dumps(['ink-resident-session-v2','compiled'])),
                       lambda r:r[0]['cases'][0]['backends'].update(gpu='cpu'),
                       lambda r:r[0]['cases'][0]['complete_engine_ns'].update(cpu=[None,10,20]),
                       lambda r:r[0]['cases'][0]['complete_engine_ns'].update(cpu=[0,10,20]),
                       lambda r:r[0]['cases'][0].update(validated=False)]:
            rows=records();change(rows);variants.append(rows)
        variants.append(records()[:2])
        for rows in variants:
            with self.subTest(rows=variants.index(rows)),tempfile.TemporaryDirectory()as d:
                store=Store.publish(d,[])
                with self.assertRaises(ValueError):fit_records(store,rows)
                self.assertFalse((store.data/'cost-models').exists())

if __name__=='__main__':unittest.main()
