import tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import ink_knowledge, producers
from ink_planner.compiled_planning import code_fingerprint
from ink_planner.compiled_cache import key


class AuthoringEpoch(unittest.TestCase):
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
