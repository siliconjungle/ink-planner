"""Approximate results use source-bound interpreter oracles, never self-checks."""
import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from ink_knowledge import identity
from ink_planner.offline_measurements import RUNNER, refinement_phases


class RefinementMeasurements(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cargo'),'native oracle check requires Cargo')
    def test_native_output_oracle_never_rounds_adjacent_full_width_integers(self):
        # Compile the actual comparison implementation used by the installed
        # native adapter, with independent boundary expectations.
        source=RUNNER.with_name('measure_calls.rs.txt').read_text()
        same=source[source.index('fn same('):source.index('fn consume(')]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'src').mkdir()
            (root/'Cargo.toml').write_text('[package]\nname="ink-native-oracle-test"\nversion="0.1.0"\nedition="2021"\n[workspace]\n[dependencies]\nserde_json="=1.0.151"\n')
            (root/'src/lib.rs').write_text('use serde_json::Value;\n'+same+'''\n#[test] fn exact_numeric_oracle(){
                use serde_json::json;
                assert!(!same(&json!(18446744073709551615u64),&json!(18446744073709551614u64)));
                assert!(!same(&json!(-9223372036854775808i64),&json!(-9223372036854775807i64)));
                assert!(same(&json!(18446744073709551615u64),&json!(18446744073709551615u64)));
                assert!(same(&json!(1),&json!(1.0)));
                assert!(!same(&json!(-0.0),&json!(0.0)));
                assert!(!same(&json!([18446744073709551615u64]),&json!([18446744073709551614u64])));
            }''')
            result=subprocess.run(['cargo','test','--offline','--quiet','--manifest-path',str(root/'Cargo.toml')],capture_output=True,text=True,timeout=120)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_oracle_identity_binds_source_plan_selected_module_and_call_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);package=dict(input_core_sha256='source',steps=[])
            phases={'call:run':[dict(function='run',args=[7],expected=8)]}
            response=dict(status='checked',semantics='ink-reference-work-v1',input='source',plan_sha256=identity(package),selected='selected',values=[7])
            calls=[]
            planner=SimpleNamespace(directory=root,original=root/'source',invoke=lambda *args,**kwargs:(calls.append((args,kwargs))or response))
            result=refinement_phases(planner,package,{'source':'selected'},root/'plan',phases,10)
            self.assertEqual(result['call:run'][0]['expected'],7)
            self.assertEqual(phases['call:run'][0]['expected'],8)
            self.assertEqual(calls[0][0][0],'plan-work')
            self.assertEqual(json.loads((root/'refinement-reference-calls.json').read_text()),[dict(function='run',args=[7])])
            for key,bad in [('status','unchecked'),('input','wrong'),('plan_sha256','wrong'),('selected','wrong'),('values',[])]:
                original=response[key];response[key]=bad
                with self.subTest(key=key),self.assertRaisesRegex(ValueError,'binding mismatch'):
                    refinement_phases(planner,package,{'source':'selected'},root/'plan',phases,10)
                response[key]=original

    def test_emitted_result_must_match_its_own_oracle_and_preserve_the_calls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);artifacts=[]
            meta=dict(state=None,named_types={},functions=[dict(name='run',internal=False,params=[dict(name='x',type={'kind':'u32'})],result={'kind':'u32'})])
            for name,value in [('baseline',8),('allowed',7),('wrong',6),('unproved',7),('changed-input',7)]:
                path=root/(name+'.mjs');path.write_text('export const metadata='+json.dumps(meta)+';export function call(){return '+str(value)+';}')
                artifact=dict(measurement=name,path=str(path))
                if name in ('allowed','wrong','changed-input'):
                    artifact['reference_phases']={'call:run':[dict(function='run',args=[99 if name=='changed-input'else 7],expected=7)]}
                artifacts.append(artifact)
            request=root/'request.json';request.write_text(json.dumps(dict(artifacts=artifacts,phases={'call:run':[dict(function='run',args=[7],expected=8)]},measurement=dict(rounds=9,minimum_batch_ms=1,maximum_calls_per_sample=1))))
            result=subprocess.run(['node',str(RUNNER),str(request)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            report=json.loads(result.stdout)
            self.assertEqual({row['measurement']for row in report['rows']},{'baseline','allowed'})
            self.assertEqual({row['measurement']for row in report['rejected']},{'wrong','unproved','changed-input'})


if __name__=='__main__':unittest.main()
