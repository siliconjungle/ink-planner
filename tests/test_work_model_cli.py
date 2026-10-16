"""Exercise CLI selection and warm-cache policy with a stubbed proof boundary."""
import contextlib
import hashlib
import io
import json
import os
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
                           reused_steps=0, recipe_cache_hits=0, recipe_dispatches={}, notes=[],
                           search_goal=None)
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

            # A context change after the initial lookup must not publish the
            # historical choice or re-key its old costs under a new context.
            from types import SimpleNamespace
            from ink_planner.compiled_cache import execution_binding
            binding = execution_binding(planner, SimpleNamespace(target='javascript', memory_bytes=512000000))
            with patch('ink_planner.compiled_cache.execution_binding',
                       side_effect=[binding, dict(binding, changed=True), dict(binding, changed=True)]):
                with self.assertRaisesRegex(ValueError, 'execution context changed during planning'):
                    run(opted_in)
            self.assertTrue(any(n.get('reason')=='execution context changed during cached replay' for n in planner.notes))

            # Publication happens after emission. Changing settings there must
            # not stamp the old measured choice under a freshly computed key.
            with patch.dict(os.environ):
                os.environ.pop('NODE_OPTIONS', None)
                def emitted(*args, **kwargs):
                    os.environ['NODE_OPTIONS'] = '--jitless'
                    return SimpleNamespace(returncode=0, stderr='')
                extra = opted_in + ['--budget', '17', '--emit', str(root/'selected.mjs')]
                with patch.object(compiled_planning.subprocess, 'run', side_effect=emitted):
                    _, first_publication = run(extra)
                    self.assertNotIn('cache', first_publication)
                    _, changed_host = run(extra)
                    self.assertNotIn('cache', changed_host)
                    _, changed_warm = run(extra)
                    self.assertTrue(changed_warm['cache']['hit'])

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
