import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ink_knowledge import Store
from ink_planner.compiled_cache import key, load, save
from ink_planner.measurement_context import configuration, execution_context, node_command
from ink_planner.offline_measurements import target_context, acquire


class MeasurementContext(unittest.TestCase):
    def planner(self, target='javascript'):
        options = SimpleNamespace(target=target, memory_bytes=512000000)
        return SimpleNamespace(options=options, compiler_hash='a'*64, code_hash='b'*64,
                               registry_digest='c'*64)

    def test_actual_node_jit_mode_separates_costs_and_selected_cache(self):
        planner = self.planner()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            # Preserve only PATH so Node is the actual installed executable.
            with patch.dict(os.environ, {'PATH': self.path}):
                normal = target_context(planner)
                before = key(planner, 'source', 'snapshot', planner.options, [])
                save(tmp, before, dict(steps=[]), dict(unchecked_checks=0))
                store = Store.publish(Path(tmp)/'knowledge', [])
                workload = dict(shape={}, distribution='test', concurrency=1, residency='owned')
                observation = dict(schema=1, program='d'*64, entries=[], plan='e'*64,
                                   target=normal, workload=workload,
                                   metrics=dict(complete_call_ns=['1'], peak_bytes=None))
                store.observe(observation)
                self.assertEqual(len(list(store.iter_observations('d'*64, ['e'*64], normal, workload))), 1)
                with patch.dict(os.environ, {'NODE_OPTIONS': '--jitless'}):
                    changed = target_context(planner)
                    after = key(planner, 'source', 'snapshot', planner.options, [])
                    self.assertNotEqual(normal, changed)
                    self.assertNotEqual(before, after)
                    self.assertIsNone(load(tmp, after))
                    self.assertEqual(list(store.iter_observations('d'*64, ['e'*64], changed, workload)), [])
                self.assertIsNotNone(load(tmp, before))

    def setUp(self):
        self.path = os.environ['PATH']

    def test_heap_and_state_flags_match_actual_spawned_node(self):
        planner = self.planner()
        with tempfile.TemporaryDirectory() as tmp:
            runner = Path(tmp)/'flags.mjs'
            runner.write_text('console.log(JSON.stringify(process.execArgv));')
            normal = target_context(planner)
            planner.options.memory_bytes = 64000000
            self.assertNotEqual(normal, target_context(planner))
            for state, describe in [(False, False), (True, False), (False, True)]:
                command = node_command(planner.options, runner, state=state, describe=describe)
                flags = json.loads(subprocess.check_output(command, text=True))
                expected = [] if describe else (['--expose-gc'] if state else []) + ['--max-old-space-size=64']
                self.assertEqual(flags, expected)
            self.assertNotEqual(target_context(planner), target_context(planner, state=True))
            self.assertNotEqual(target_context(planner), target_context(planner, encoded=True))

    def test_known_controls_bind_key_even_when_host_inspection_declines(self):
        for target, name, value in [('javascript', 'NODE_OPTIONS', '--jitless'),
                                    ('rust', 'RUSTFLAGS', '-C opt-level=1'),
                                    ('rust', 'CARGO_PROFILE_RELEASE_LTO', 'true'),
                                    ('javascript', 'INK_THREADS', '8')]:
            with self.subTest(target=target, name=name), \
                 patch('ink_planner.offline_measurements.target_context', side_effect=subprocess.TimeoutExpired('node', 15)):
                planner = self.planner(target)
                with patch.dict(os.environ, {}, clear=True):
                    before = key(planner, 'source', 'snapshot', planner.options, [])
                    with patch.dict(os.environ, {name: value}):
                        self.assertNotEqual(before, key(planner, 'source', 'snapshot', planner.options, []))

    def test_native_config_and_wrapper_content_changes_are_bound(self):
        planner = self.planner('rust')
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp); home = root/'cargo'; home.mkdir()
            cargo = root/'.cargo'; cargo.mkdir()
            wrapper = root/'wrapper'; wrapper.write_text('one'); wrapper.chmod(0o755)
            with patch('ink_planner.measurement_context.Path.cwd', return_value=root), \
                 patch.dict(os.environ, {'CARGO_HOME': str(home), 'RUSTC_WRAPPER': str(wrapper)}):
                baseline = configuration(planner.options)
                config = cargo/'config.toml'; config.write_text('[build]\nrustflags=["-Copt-level=1"]\n')
                changed = configuration(planner.options)
                self.assertNotEqual(baseline, changed)
                config.write_text('[build]\nrustflags=["-Copt-level=2"]\n')
                self.assertNotEqual(changed, configuration(planner.options))
                config.unlink()
                self.assertEqual(baseline, configuration(planner.options))
                wrapper.write_text('two')
                self.assertNotEqual(baseline, configuration(planner.options))
                wrapper.write_text('one')
                (home/'config').write_text('[profile.release]\nopt-level=1\n')
                self.assertNotEqual(baseline, configuration(planner.options))
                (home/'config').unlink()
                (root/'rust-toolchain.toml').write_text('[toolchain]\nchannel="stable"\n')
                self.assertNotEqual(baseline, configuration(planner.options))

    def test_config_budget_declines_and_environment_values_are_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'.cargo').mkdir()
            file = root/'.cargo/config.toml'; file.write_bytes(b'x'*1_000_001)
            with patch('ink_planner.measurement_context.Path.cwd', return_value=root):
                with self.assertRaisesRegex(ValueError, 'configuration file budget'):
                    configuration(self.planner('rust').options)
        with patch.dict(os.environ, {}, clear=True):
            planner = self.planner()
            before = configuration(planner.options)
            with patch.dict(os.environ, {'UNRELATED_SECRET': 'private sentinel'}):
                self.assertEqual(before, configuration(planner.options))
            with patch.dict(os.environ, {'NODE_OPTIONS': '--jitless'}):
                context = configuration(planner.options)
                self.assertNotIn('--jitless', json.dumps(context))
                self.assertEqual(context['controls']['NODE_OPTIONS'], hashlib.sha256(b'--jitless').hexdigest())
            for name in ('CARGO_TARGET_DIR', 'CARGO_BUILD_JOBS'):
                native = configuration(self.planner('rust').options)
                with patch.dict(os.environ, {name: 'irrelevant inherited value'}):
                    self.assertEqual(native, configuration(self.planner('rust').options))

    def test_configuration_change_during_acquisition_publishes_no_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store = Store.publish(root/'knowledge', [])
            compiler = root/'compiler'; compiler.write_text('fixture')
            from ink_planner.compiled_planning import code_fingerprint
            planner = self.planner(); planner.directory = root; planner.original = root/'source'
            planner.compiler = compiler; planner.compiler_hash = hashlib.sha256(compiler.read_bytes()).hexdigest()
            planner.code_hash = code_fingerprint()
            planner.options.measure_timeout = 30; planner.options.check_timeout = 30
            meta = dict(state=None, named_types={}, functions=[dict(name='run', internal=False, params=[], result={'kind':'u32'})])
            def invoke(command, *args, **kwargs):
                if command == 'run':
                    return 1
                args[-1].write_text('export const metadata='+json.dumps(meta)+';export function call(){return 1;}')
                os.environ['NODE_OPTIONS'] = '--jitless'
            planner.invoke = invoke
            workload = root/'workload.json'
            workload.write_text(json.dumps(dict(schema=1, phases={'call:run':[dict(function='run', args=[])]},
                profile={'call:run':1}, workload=dict(shape={}, distribution='test', concurrency=1, residency='owned'))))
            with patch.dict(os.environ):
                os.environ.pop('NODE_OPTIONS', None)
                with patch.object(store, 'observe', wraps=store.observe) as observed:
                    with self.assertRaisesRegex(ValueError, 'machinery changed'):
                        acquire(planner, store, [dict(input_core_sha256='a'*64)], [{}], ['b'*64], workload,
                                dict(warmup=1, samples=1, iterations=1, processes=1))
                    observed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
