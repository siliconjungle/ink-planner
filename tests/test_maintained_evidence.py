"""Exercise the compiler-planner entry point's evidence-free selection policy.

Proof admission is replaced with an oracle here; synthetic plans/costs test
selection policy only, never the validity or performance of maintained folds.
"""
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from ink_knowledge import Store
from ink_planner.compiled_planning import main


class MaintainedEvidence(unittest.TestCase):
    def test_complete_planner_keeps_baseline_when_only_maintained_candidates_lack_evidence(self):
        for constraints in [[], ['--memory-limit','100'], ['--migration-count','1']]:
            with self.subTest(constraints=constraints), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);store=Store.publish(root/'knowledge',[]);store.rebuild()
                source=root/'source.json';source.write_text('{}')
                compiler=root/'compiler';compiler.write_text('proof oracle fixture')
                output=root/'selected.json';ident='a'*64
                baseline=dict(schema=1,semantics='ink-compiled-plan-v1',input_core_sha256=ident,steps=[])
                maintained=dict(baseline,steps=[dict(kind='maintained',package=dict(views=[{}]))])
                states=OrderedDict((str(i),(package,dict(source=ident,core={'program':{}},capability={'emittable':True})))for i,package in enumerate([baseline,maintained]))
                planner=SimpleNamespace(compiler_hash=hashlib.sha256(compiler.read_bytes()).hexdigest(),code_hash='code',registry_digest='registry',snapshot=store.snapshot_id,
                    notes=[],reused_steps=0,recipe_cache_hits=0,priority=lambda _:0,expand=lambda *_:iter(()),check=lambda _: {},invoke=lambda *_:None,close=lambda:None)
                args=['ink-plan','--compiler',str(compiler),'--source',str(source),'--core','--knowledge-root',str(store.root),
                      '--target','javascript','--measurement-mode','off','--cache','off','-o',str(output),*constraints]
                with patch.object(sys,'argv',args), patch('ink_planner.compiled_planning.Planner',return_value=planner), \
                     patch('ink_planner.compiled_planning.load',return_value=({'recipes':[]},'registry')), \
                     patch('ink_planner.compiled_planning.explore',return_value=(states,{})), \
                     patch('ink_planner.compiled_planning.code_fingerprint',return_value='code'), \
                     patch('ink_planner.compiled_planning.indexed_coverage',return_value={}), \
                     patch('ink_planner.offline_measurements.target_context',return_value={}), \
                     patch('ink_planner.compiled_planning.subprocess.check_output',return_value=json.dumps({'core_sha256':ident})), \
                     contextlib.redirect_stdout(io.StringIO()):
                    main()
                self.assertEqual(json.loads(output.read_text()),baseline)
                receipt=json.loads(Path(str(output)+'.receipt.json').read_text())
                self.assertEqual(len(receipt['candidates']),2)
                self.assertIn('missing comparable lifecycle evidence',receipt['selection']['reason'])


if __name__=='__main__':unittest.main()
