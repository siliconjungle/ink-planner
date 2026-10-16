import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from ink_planner.gpu_measurements import acquire, RUNNER
from ink_knowledge import Store
from ink_planner.representation_selection import rank


class DeviceMeasurementControls(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_exact_values_independent_device_counters_and_asset_boundaries(self):
        script=Path(__file__).with_name('gpu-measurement-controls.mjs')
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_request_seal_rejects_before_launching_a_browser(self):
        with tempfile.TemporaryDirectory()as tmp:
            request=Path(tmp)/'request.json';request.write_text('{}')
            result=subprocess.run(['node',str(RUNNER),'/not-a-browser',str(request),'0'*64],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('request hash mismatch',result.stderr)

    @unittest.skipUnless(shutil.which('node') and os.name == 'posix', 'Node and POSIX required')
    def test_browser_host_budget_and_failure_cleanup(self):
        script = Path(__file__).with_name('browser-host-controls.mjs')
        result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bounds_decline_before_creating_any_experiment(self):
        with tempfile.TemporaryDirectory()as tmp:
            scratch=Path(tmp)/'not-created'
            for count in [0,9,True]:
                with self.assertRaisesRegex(ValueError,'fresh browser'):
                    acquire('absent','absent',[],'absent','absent','absent',scratch,processes=count)
            for seconds in [0,-1,3601]:
                with self.assertRaisesRegex(ValueError,'timeout'):
                    acquire('absent','absent',[],'absent','absent','absent',scratch,timeout=seconds)
            self.assertFalse(scratch.exists())

    def test_unknown_browser_memory_never_becomes_zero_cost_memory(self):
        with tempfile.TemporaryDirectory()as tmp:
            store=Store.publish(Path(tmp),[]);program='a'*64;baseline='b'*64;alternative='c'*64
            target=dict(hardware='observed-device',driver='browser',toolchain='bound',backend='webgpu',bridge='explicit-memory-unknown')
            workload=dict(shape={},distribution='controls',concurrency=1,residency='complete-call')
            from ink_planner.representation_selection import phase_workload
            for plan in [baseline,alternative]:
                obj=dict(schema=1,program=program,entries=[],plan=plan,target=target,workload=phase_workload(workload,'work'),metrics=dict(complete_call_ns=['100'if plan==baseline else'1']*9,peak_bytes=None))
                store.observe(obj)
            with self.assertRaisesRegex(ValueError,'memory'):
                rank(store,program,[baseline,alternative],target,workload,{'work':1},baseline,memory_limit=512000000)
