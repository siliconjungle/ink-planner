import json,subprocess,tempfile,unittest
from pathlib import Path
from ink_planner.offline_measurements import read_workload,RUNNER
class OfflineMeasurements(unittest.TestCase):
    def test_training_inputs_and_lifecycle_weights_are_bounded(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp)/'workload.json'
            good=dict(schema=1,phases={'call:run':[dict(function='run',args=[[1,2],1])]},profile={'call:run':1},workload={})
            path.write_text(json.dumps(good));read_workload(path)
            for weight in [-1,True,1_000_000_001]:
                good['profile']['call:run']=weight;path.write_text(json.dumps(good))
                with self.assertRaises(ValueError):read_workload(path)
    def test_wrong_output_and_input_mutation_are_not_measured(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);artifacts=[]
            meta=dict(state=None,named_types={},functions=[dict(name='run',internal=False,params=[dict(type={'kind':'list','element':{'kind':'u32'}})],result={'kind':'u32'})])
            bodies=['return args[0][0];','return 99;','args[0][0]=5;return 1;']
            for i,body in enumerate(bodies):
                path=root/f'{i}.mjs';path.write_text('export const metadata='+json.dumps(meta)+'; export function call(name,args){'+body+'}')
                artifacts.append(dict(measurement=str(i),path=str(path)))
            request=root/'request.json';request.write_text(json.dumps(dict(artifacts=artifacts,phases={'call:run':[dict(function='run',args=[[1]],expected=1)]})))
            r=subprocess.run(['node',str(RUNNER),str(request)],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr);report=json.loads(r.stdout)
            self.assertEqual({x['measurement']for x in report['rows']},{'0'})
            self.assertEqual({x['measurement']for x in report['rejected']},{'1','2'})
            artifacts[0]['path']=artifacts[1]['path'];request.write_text(json.dumps(dict(artifacts=artifacts,phases={'call:run':[dict(function='run',args=[[1]],expected=1)]})))
            self.assertNotEqual(subprocess.run(['node',str(RUNNER),str(request)],capture_output=True).returncode,0)
