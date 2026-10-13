import tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import ink_knowledge, producers
from ink_planner.compiled_planning import code_fingerprint
from ink_planner.compiled_cache import key


class AuthoringEpoch(unittest.TestCase):
    def test_work_model_and_workload_contents_and_presence_change_cache_identity(self):
        with tempfile.TemporaryDirectory() as directory, patch('ink_planner.offline_measurements.target_context',return_value={}):
            root=Path(directory)
            planner=SimpleNamespace(compiler_hash='a'*64,registry_digest='b'*64,code_hash='c'*64)
            options=SimpleNamespace()
            baseline=key(planner,'source','snapshot',options,[])
            for name in ['work_model','work_workload']:
                with self.subTest(input=name):
                    path=root/(name+'.json');path.write_text('{"version":1}')
                    setattr(options,name,path)
                    original=key(planner,'source','snapshot',options,[])
                    self.assertNotEqual(baseline,original)
                    path.write_text('{"version":2}')
                    self.assertNotEqual(original,key(planner,'source','snapshot',options,[]))
                    copy=root/(name+'-copy.json');copy.write_bytes(path.read_bytes())
                    changed=key(planner,'source','snapshot',options,[])
                    setattr(options,name,copy)
                    self.assertEqual(changed,key(planner,'source','snapshot',options,[]))
                    setattr(options,name,None)
                    self.assertEqual(baseline,key(planner,'source','snapshot',options,[]))

    def test_changed_installed_authoring_bytes_invalidate_cost_and_plan_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(ink_knowledge,'__file__',str(root/'ink_knowledge/__init__.py')), \
                 patch.object(producers,'__file__',str(root/'producers/__init__.py')), \
                 patch('ink_planner.offline_measurements.target_context',return_value={'host':'fixture'}):
                planner=SimpleNamespace(compiler_hash='a'*64,registry_digest='b'*64,code_hash=code_fingerprint())
                options=SimpleNamespace()
                previous=key(planner,'source','snapshot',options,[])
                for name in ['libraries/algorithm.ink','research/general-laws/policy.json','research/verified-kernel/Kernel.lean']:
                    file=root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('changed authoring bytes')
                    planner.code_hash=code_fingerprint()
                    current=key(planner,'source','snapshot',options,[])
                    self.assertNotEqual(previous,current,name)
                    self.assertEqual(current,key(planner,'source','snapshot',options,[]))
                    previous=current

    def test_holdout_and_margin_change_cache_identity(self):
        with tempfile.TemporaryDirectory() as directory, patch('ink_planner.offline_measurements.target_context',return_value={}):
            root=Path(directory);held=root/'holdout.json';held.write_text('{}')
            planner=SimpleNamespace(compiler_hash='a'*64,registry_digest='b'*64,code_hash='c'*64)
            options=SimpleNamespace(measure_holdout=held,measure_processes=3,minimum_measured_gain_ppm=100000)
            original=key(planner,'source','snapshot',options,[])
            for field,value in [('measure_processes',4),('minimum_measured_gain_ppm',200000)]:
                previous=getattr(options,field);setattr(options,field,value)
                self.assertNotEqual(original,key(planner,'source','snapshot',options,[]))
                setattr(options,field,previous)
            held.write_text('{"changed":true}')
            self.assertNotEqual(original,key(planner,'source','snapshot',options,[]))
