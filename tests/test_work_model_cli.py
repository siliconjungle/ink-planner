"""Exercise CLI selection and warm-cache policy with a stubbed proof boundary."""
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ink_knowledge import identity
from ink_planner import compiled_planning


class WorkModelCLI(unittest.TestCase):
    def test_opt_in_constraints_and_warm_cache(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary)
            compiler = root / 'compiler';compiler.write_bytes(b'compiler fixture')
            source = root / 'source.json';source.write_text('{}')
            workload = root / 'calls.json';workload.write_text('{}')
            model = root / 'model.json'
            model_value = dict(schema=1, semantics='ink-work-model-v1', feature='target_iterations',
                               slope=1, sigma=0, z=2, min_gain=0.05, validation=dict(status='validated'))
            model.write_text(json.dumps(model_value))
            output = root / 'plan.json'
            store = Mock(snapshot_id='snapshot')
            planner = Mock(compiler_hash=hashlib.sha256(compiler.read_bytes()).hexdigest(),
                           code_hash='code', registry_digest='registry', snapshot='snapshot',
                           reused_steps=0, recipe_cache_hits=0, notes=[])
            def explore(initial, *args):
                alternative = dict(initial, steps=[dict(kind='rewrite', package={})])
                return {'base': (initial, dict(source='base')),
                        'candidate': (alternative, dict(source='candidate'))}, dict(attempts=1)
            explore_mock = stack.enter_context(patch.object(compiled_planning, 'explore', side_effect=explore))
            for name, value in [('Store', store), ('Planner', planner), ('load', (dict(recipes=[]), 'registry')),
                                ('code_fingerprint', 'code'), ('indexed_coverage', {})]:
                stack.enter_context(patch.object(compiled_planning, name, return_value=value))
            stack.enter_context(patch.object(compiled_planning.subprocess, 'check_output', return_value='{"core_sha256":"source"}'))
            stack.enter_context(patch('ink_knowledge.usage_profiles.discover', return_value=[]))
            stack.enter_context(patch('ink_knowledge.implementation_candidates.publish_candidates',
                                      return_value=(store, ['base', 'candidate'])))
            stack.enter_context(patch.object(compiled_planning, 'measurement_id', side_effect=lambda candidate, *_: candidate))
            stack.enter_context(patch('ink_planner.offline_measurements.target_context', return_value={}))
            work = dict(rows=[dict(measurement='base', weighted_iterations='100', steps=[]),
                              dict(measurement='candidate', weighted_iterations='1', steps=['rewrite'])])
            acquisition = stack.enter_context(patch('ink_planner.work_model.acquire', return_value=work))
            common = ['ink-plan', '--compiler', str(compiler), '--source', str(source), '--core',
                      '--knowledge-root', str(root), '--target', 'javascript', '--measurement-mode', 'off', '-o', str(output)]
            opted_in = ['--work-model', str(model), '--work-workload', str(workload)]
            def run(extra):
                with patch('sys.argv', common + extra), contextlib.redirect_stdout(io.StringIO()):
                    compiled_planning.main()
                return json.loads(output.read_text()), json.loads(Path(str(output) + '.receipt.json').read_text())

            baseline, _ = run([])
            self.assertEqual(baseline['steps'], [])
            acquisition.assert_not_called()
            selected, receipt = run(opted_in)
            self.assertTrue(selected['steps'])
            self.assertNotIn('cache', receipt)
            searches = explore_mock.call_count
            cached, receipt = run(opted_in)
            self.assertEqual(cached, selected)
            self.assertTrue(receipt['cache']['hit'])
            self.assertEqual(explore_mock.call_count, searches)

            model_value['min_gain'] = 1000
            model.write_text(json.dumps(model_value))
            selected, receipt = run(opted_in)
            self.assertEqual(selected['steps'], [])
            self.assertNotIn('cache', receipt)
            model_value['min_gain'] = 0.05
            model.write_text(json.dumps(model_value))
            workload.write_text('{"changed":true}')
            selected, receipt = run(opted_in)
            self.assertTrue(selected['steps'])
            self.assertNotIn('cache', receipt)

            for options in [['--memory-limit', '0'], ['--memory-limit', '1024'], ['--migration-count', '1']]:
                with self.subTest(options=options):
                    selected, receipt = run(opted_in + options)
                    self.assertEqual(selected['steps'], [])
                    self.assertIn('lacks memory or migration', receipt['selection']['reason'])
                    replay = planner.invoke.call_args.args
                    self.assertEqual(replay[0], 'check-plan')
                    self.assertEqual(receipt['selected'], identity(baseline))
