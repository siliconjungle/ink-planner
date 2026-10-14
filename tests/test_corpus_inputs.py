import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ink_planner.corpus import bind_context, run_program
from ink_planner.corpus_inputs import CapturedInputs, digest


class CorpusInputs(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        compiler = os.environ.get('INK_COMPILER')
        if not compiler:
            self.skipTest('INK_COMPILER is required for actual source capture')
        self.args = SimpleNamespace(compiler=Path(compiler).resolve(), timeout=60,
                                    scratch=self.root, manifest=self.root / 'corpus.json',
                                    knowledge_root=self.root / 'knowledge', target='javascript',
                                    planning_effort='quick')
        self.source = self.root / 'main.ink'

    def manifest(self):
        self.args.manifest.write_text(json.dumps(dict(schema=1, semantics='ink-planning-corpus-v1',
            programs=[dict(name='program', source='main.ink', split='heldout',
                           sha256=digest(self.source.read_bytes()))])))

    def capture(self):
        return CapturedInputs(self.args)

    def run_core(self, data):
        path = self.root / 'execute.core.json'
        path.write_bytes(data)
        arguments = self.root / 'args.json'
        arguments.write_text('[7]')
        result = subprocess.run([str(self.args.compiler), 'run', str(path), 'value', str(arguments), '--core'],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_complete_import_capture_survives_original_edits_and_binds_resume(self):
        sub = self.root / 'sub'; sub.mkdir()
        leaf = self.root / 'absolute leaf.ink'
        leaf.write_text('module leaf; fn bump(x:u32)->u32{return x+1;}\n')
        helper = sub / 'helper.ink'
        helper.write_text('module helper; import ' + json.dumps(str(leaf)) + ' as leaf;\n'
                          'fn apply(x:u32)->u32{return leaf.bump(x);}\n')
        self.source.write_text('module main;\n/* λ import "not-a-file" as no; */\n'
            'import "sub/helper.ink" as h; import "std:words" as words;\n'
            'fn value(x:u32)->u32{return words.min32(h.apply(x),100);}\n')
        self.manifest()
        original = self.capture()
        item = original.programs['program']
        files = json.loads(item['capture'])['files']
        self.assertEqual({entry['module'] for entry in files}, {'main', 'helper', 'leaf', 'ink.std.words'})
        self.assertEqual({entry['path']: entry['source'] for entry in files}[str(leaf.resolve())], leaf.read_text())
        self.assertEqual(self.run_core(item['core']), 8)
        unchanged = self.capture()
        self.assertEqual(original.context(), unchanged.context())
        destination = self.root / 'saved'
        original.save(destination)
        unchanged.save(destination, verify=True)
        context = self.root / 'context.json'
        bind_context(context, original.context(), False)
        leaf.write_text('module leaf; fn bump(x:u32)->u32{return x+2;}\n')
        changed = self.capture()
        self.assertNotEqual(original.context(), changed.context())
        with self.assertRaisesRegex(ValueError, 'exact original'):
            bind_context(context, changed.context(), True)
        self.assertEqual(self.run_core(changed.programs['program']['core']), 9)
        self.source.unlink(); helper.unlink(); leaf.unlink()
        self.assertEqual(self.run_core(item['core']), 8)
        original.save(destination, verify=True)
        core_path = destination / (item['core_sha256'] + '.core.json')
        core_path.write_bytes(changed.programs['program']['core'])
        with self.assertRaisesRegex(ValueError, 'saved input missing or changed'):
            original.save(destination, verify=True)
        core_path.unlink()
        with self.assertRaisesRegex(ValueError, 'saved input missing or changed'):
            original.save(destination, verify=True)

    @unittest.skipIf(os.name == 'nt', 'symlink privilege is platform dependent')
    def test_symlink_import_resolves_relative_to_its_actual_parent(self):
        real = self.root / 'real'; real.mkdir()
        leaf = real / 'leaf.ink'
        leaf.write_text('module leaf; fn value(x:u32)->u32{return x+3;}')
        helper = real / 'helper.ink'
        helper.write_text('module helper; import "leaf.ink" as leaf; fn value(x:u32)->u32{return leaf.value(x);}')
        (self.root / 'alias.ink').symlink_to(helper)
        self.source.write_text('module main; import "alias.ink" as h; fn value(x:u32)->u32{return h.value(x);}')
        self.manifest()
        captured = self.capture().programs['program']
        self.assertEqual(self.run_core(captured['core']), 10)
        paths = {row['path'] for row in json.loads(captured['capture'])['files']}
        self.assertIn(str(helper.resolve()), paths)
        self.assertIn(str(leaf.resolve()), paths)

    def test_edit_between_manifest_validation_and_compiler_read_is_rejected(self):
        self.source.write_text('module main; fn value(x:u32)->u32{return x;}')
        self.manifest()
        original_run = subprocess.run
        def mutate(command, **kwargs):
            self.source.write_text('module main; fn value(x:u32)->u32{return 0;}')
            return original_run(command, **kwargs)
        with patch('ink_planner.corpus_inputs.subprocess.run', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'source changed during capture'):
                self.capture()

    def test_worker_consumes_captured_core_after_original_and_archive_change(self):
        self.source.write_text('module main; fn value(x:u32)->u32{return x;}')
        self.manifest()
        inputs = self.capture()
        out = self.root / 'out'; out.mkdir()
        inputs.save(out / 'inputs')
        self.source.write_text('not source')
        prepared = inputs.programs['program']
        (out / 'inputs' / (prepared['core_sha256'] + '.core.json')).write_bytes(b'changed archive')
        commands = []
        def worker(command, **kwargs):
            commands.append(command)
            self.assertIn('--core', command)
            core = Path(command[command.index('--source') + 1])
            self.assertEqual(core.read_bytes(), prepared['core'])
            self.assertEqual(self.run_core(core.read_bytes()), 7)
            return SimpleNamespace(returncode=1, stderr='intentional failure after checking real captured core')
        # Stub expensive knowledge/search only. The core above is executed by the actual compiler.
        original_run = subprocess.run
        def dispatch(command, **kwargs):
            if 'ink_planner.compiled_planning' in command:
                return worker(command, **kwargs)
            return original_run(command, **kwargs)
        with patch('ink_planner.corpus.private_copy'), patch('ink_planner.corpus.subprocess.run', side_effect=dispatch):
            record = run_program(inputs.manifest['programs'][0], prepared, self.args, out)
        self.assertEqual(len(commands), 1)
        self.assertEqual(record['status'], 1)
        self.assertEqual(record['core_sha256'], prepared['core_sha256'])
        self.assertGreaterEqual(record['seconds'], record['source_capture_seconds'])

    def test_failed_compilation_is_not_saved_as_captured_input(self):
        self.source.write_text('module main; fn value(x:u32)->Bool{return x;}')
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'input capture failed'):
            self.capture()
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['corpus.json', 'main.ink'])
