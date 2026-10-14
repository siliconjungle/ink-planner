"""Real checker/emitter replay must publish the exact selection it checked.

Work counts below are synthetic ranking fixtures, not measured performance.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ink_knowledge import Store, identity, semantic_entry
from producers.semantic_laws import value, var, word, op, law
from ink_planner import physical_selection as selector
from ink_planner.physical_features import COMPONENTS, file_hash


class PhysicalSelection(unittest.TestCase):
    def run_compiler(self, *args):
        result = subprocess.run([str(self.compiler), *map(str, args)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def fixture(self, root):
        project = Path(__file__).resolve().parents[2]
        self.compiler = Path(os.environ.get('INK_COMPILER', project/'target/debug/ink')).resolve()
        source = root/'source.ink'; source.write_text('module physical_fixture;fn entry(x:u32)->u32{return x+0;}')
        core = root/'core.json'
        digest = json.loads(self.run_compiler('emit-core', source, '-o', core).stdout)['core_sha256']
        self.original_core = core.read_bytes()
        s = value('U32'); x = var(s, 'x')
        obj = semantic_entry(law({'x': s}, op(s, 'binary:+', x, word('U32', 0)), x))
        ident = identity(obj); store = Store.publish(root/'knowledge', [obj])
        cohort = dict(target='javascript', host={'engine': 'synthetic-test'}, boundary='owned',
                      emitter_sha256=file_hash(self.compiler), entry_abi='test-values')
        fixture = root/'fixture.json'; fixture.write_text('{"args":[7],"expected":7}')
        entries = []; self.packages = {}
        for name, count, applications in [
            ('base', 10, []),
            ('fast', 1, [dict(function='entry', path=[], law=ident, arguments={'x': x}, premises=[])])]:
            d = root/name; d.mkdir()
            selection = d/'selection.json'
            selection.write_text(json.dumps(dict(schema=1, semantics='ink-evidence-v1',
                input_core_sha256=digest, knowledge=store.bundle([ident] if applications else []),
                applications=applications), indent=2)+'\n')
            self.packages[name] = selection.read_bytes()
            selected = d/'selected.json'; artifact = d/'program.mjs'
            self.run_compiler('emit-core', core, '--core', '--selection', selection, '-o', selected)
            self.run_compiler('build', selected, '--core', '--target', 'javascript', '-o', artifact)
            paths = dict(original_core=core, selected_core=selected, selection=selection,
                         artifact=artifact, fixture=fixture)
            for item in ['instrumenter', 'instrumented', 'runner']:
                paths[item] = d/item; paths[item].write_text('synthetic '+item)
            instrumentation = dict(artifact_sha256=file_hash(artifact),
                instrumented_sha256=file_hash(paths['instrumented']), instrumenter_sha256=file_hash(paths['instrumenter']))
            paths['instrumentation'] = d/'instrumentation.json'
            paths['instrumentation'].write_text(json.dumps(instrumentation))
            row = dict(id='fixture', entry='entry', boundary='owned',
                       components={k: count if k=='loop_iterations' else 0 for k in COMPONENTS},
                       known={k: True for k in COMPONENTS}, unknown=[])
            trace = dict(status='untimed-complete-output-replayed', rows=[row],
                **{name+'_sha256': file_hash(paths[name]) for name in
                   ['artifact', 'fixture', 'instrumentation', 'instrumented', 'runner']})
            paths['trace'] = d/'trace.json'; paths['trace'].write_text(json.dumps(trace))
            features = d/'features.json'
            features.write_text(json.dumps(dict(schema=1, kind='ink-target-work-v1', cohort=cohort,
                bindings={k: dict(path=str(p), sha256=file_hash(p)) for k,p in paths.items()},
                entry='entry', fixture_id='fixture', components=row['components'], known=row['known'], unknown=[],
                scope='Synthetic unit-test counts; no measured work or speed claim')))
            entries.append(dict(id=name, selection=str(selection), selected_core=str(selected),
                                artifact=str(artifact), features=str(features)))
        index = root/'candidates.json'; index.write_text(json.dumps(entries))
        model = root/'model.json'; model.write_text(json.dumps(dict(schema=1, kind='ink-target-work-prices-v1',
            cohort=cohort, weights={'loop_iterations': 1}, training=dict(protocol_sha256='0'*64,
            observations_sha256='0'*64, scope='synthetic regression'))))
        output = root/'result.json'
        args = ['physical_selection', '--compiler', str(self.compiler), '--core', str(core),
                '--knowledge-root', str(root/'knowledge'), '--snapshot', str(store.data/'snapshot.json'),
                '--candidates', str(index), '--model', str(model), '--output', str(output)]
        return args, entries, core, index, model, output

    def test_ranked_output_and_receipt_use_checked_snapshots_after_caller_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); args, entries, core, index, model, output = self.fixture(root)
            digests = {key: file_hash(p) for key,p in [('core',core),('model',model),('index',index)]}
            original_rank = selector.rank_observations
            def rank_then_change(*args):
                result = original_rank(*args)
                self.assertEqual(result[0], 'fast')
                # Simulate an editor replacing inputs after successful replay/ranking.
                for path in [Path(entries[1]['selection']), core, index, model]:
                    path.write_text('changed after checking')
                return result
            with patch.object(sys, 'argv', args), patch.object(selector, 'rank_observations', rank_then_change), contextlib.redirect_stdout(io.StringIO()):
                selector.main()
            self.assertEqual(output.read_bytes(), self.packages['fast'])
            receipt = json.loads(Path(str(output)+'.physical.json').read_text())
            self.assertEqual(receipt['model_sha256'], digests['model'])
            self.assertEqual(receipt['input_core_file_sha256'], digests['core'])
            self.assertEqual(receipt['candidates_sha256'], digests['index'])
            self.assertEqual(file_hash(output), receipt['checked']['fast']['selection_sha256'])
            replay_core = root/'original.json'; replay_core.write_bytes(self.original_core)
            self.run_compiler('check-selection', replay_core, output)

    def test_missing_physical_evidence_still_publishes_checked_baseline_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); args, entries, core, index, model, output = self.fixture(root)
            Path(entries[0]['features']).unlink()
            original_check = selector.checked_candidate
            def check_then_change(*args):
                # Main must replay all candidates against its private core snapshot.
                self.assertNotEqual(Path(args[1]), core)
                self.assertEqual(Path(args[1]).read_bytes(), self.original_core)
                result = original_check(*args)
                Path(args[3]['selection']).write_text('changed after checking')
                return result
            with patch.object(sys, 'argv', args), patch.object(selector, 'checked_candidate', check_then_change), contextlib.redirect_stdout(io.StringIO()):
                selector.main()
            self.assertEqual(output.read_bytes(), self.packages['base'])
            receipt = json.loads(Path(str(output)+'.physical.json').read_text())
            self.assertFalse(receipt['costs']['baseline_comparable'])
            self.run_compiler('check-selection', core, output)


if __name__ == '__main__':
    unittest.main()
