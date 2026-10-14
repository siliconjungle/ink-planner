import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import venv

from ink_knowledge import Store
from ink_planner.corpus import bind_context
from ink_planner.corpus_environment import tool_identity


class CorpusEnvironment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.repository = Path(__file__).resolve().parents[2]
        cls.environments = []
        for name in ['without', 'with']:
            directory = cls.root / name
            venv.create(directory, with_pip=False)
            python = directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            purelib = Path(subprocess.check_output([str(python), '-c',
                "import sysconfig;print(sysconfig.get_path('purelib'))"], text=True).strip())
            cls.environments.append((python, purelib))
        cls.knowledge = cls.root / 'knowledge'
        Store.publish(cls.knowledge, [])
        cls.compiler = cls.root / 'compiler'
        cls.compiler.write_bytes(b'fixed compiler identity, not executed by context capture')
        cls.manifest = cls.root / 'manifest.json'
        cls.manifest.write_text('{}')
        cls.environment = dict(os.environ, PYTHONPATH=os.pathsep.join([
            str(cls.repository / 'planner'), str(cls.repository / 'knowledge')]))
        cls.script = '''import json,sys
from pathlib import Path
from types import SimpleNamespace
from ink_planner.corpus import execution_context
args=SimpleNamespace(knowledge_root=Path(sys.argv[1]),compiler=Path(sys.argv[2]),manifest=Path(sys.argv[3]),target='test',planning_effort='quick',timeout=60)
print(json.dumps(execution_context(args),sort_keys=True))
'''

    def context(self, index, environment=None):
        python, _ = self.environments[index]
        result = subprocess.run([str(python), '-c', self.script, str(self.knowledge),
                                 str(self.compiler), str(self.manifest)],
                                env=environment or self.environment, capture_output=True,
                                text=True, check=True, timeout=120)
        return json.loads(result.stdout)

    def install_fixture(self):
        _, directory = self.environments[1]
        package = directory / 'pysat'
        package.mkdir(exist_ok=True)
        (package / '__init__.py').write_text('__version__ = "fixture"\n')
        (package / 'solvers.py').write_text('fixture = 1\n')
        (directory / 'pysolvers.py').write_text('native_fixture = 1\n')
        metadata = directory / 'python_sat-1.0.dist-info'
        metadata.mkdir(exist_ok=True)
        (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: python-sat\nVersion: 1.0\n')
        (metadata / 'RECORD').write_text('\n'.join(name + ',,' for name in [
            'pysat/__init__.py', 'pysat/solvers.py', 'pysolvers.py',
            'python_sat-1.0.dist-info/METADATA', 'python_sat-1.0.dist-info/RECORD']) + '\n')
        return directory

    def test_actual_interpreters_and_dependency_bytes_bind_resume(self):
        directory = self.install_fixture()
        absent = self.context(0)
        present = self.context(1)
        self.assertFalse(absent['environment']['python']['modules']['pysat']['available'])
        self.assertTrue(present['environment']['python']['modules']['pysat']['available'])
        self.assertEqual(present, self.context(1))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'context.json'
            bind_context(path, present, False)
            bind_context(path, present, True)
            with self.assertRaisesRegex(ValueError, 'exact original'):
                bind_context(path, absent, True)
            # Same distribution version, interpreter and package location;
            # both a Python implementation edit and native-module edit matter.
            for file in ['pysat/solvers.py', 'pysolvers.py']:
                with (directory / file).open('a') as output:
                    output.write('changed_fixture = True\n')
                changed = self.context(1)
                self.assertEqual(present['environment']['python']['distributions']['python-sat']['version'],
                                 changed['environment']['python']['distributions']['python-sat']['version'])
                with self.assertRaisesRegex(ValueError, 'exact original'):
                    bind_context(path, changed, True)
            self.assertEqual(json.loads(path.read_text()), present)

    def test_shadowed_import_changes_context_without_distribution_changes(self):
        import shutil
        self.install_fixture()
        original = self.context(1)
        shadow = self.knowledge / 'pysat'
        shadow.mkdir()
        try:
            (shadow / '__init__.py').write_text('raise RuntimeError("identity capture must not import this")\n')
            changed = self.context(1)
            self.assertEqual(original['environment']['python']['distributions'],
                             changed['environment']['python']['distributions'])
            self.assertNotEqual(original['environment']['python']['modules']['pysat'],
                                changed['environment']['python']['modules']['pysat'])
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'context.json'
                bind_context(path, original, False)
                with self.assertRaisesRegex(ValueError, 'exact original'):
                    bind_context(path, changed, True)
        finally:
            shutil.rmtree(shadow)

    def test_controls_are_bound_without_serializing_unrelated_secrets(self):
        original = self.context(0)
        secret = 'test-only-credential-must-not-appear'
        environment = dict(self.environment, INK_TEST_SECRET=secret)
        unchanged = self.context(0, environment)
        self.assertEqual(original, unchanged)
        self.assertNotIn(secret, json.dumps(unchanged))
        changed = self.context(0, dict(environment, PYTHONHASHSEED='314159'))
        self.assertNotEqual(original, changed)
        self.assertNotIn(secret, json.dumps(changed))
        self.assertNotEqual(changed['environment']['controls']['PYTHONHASHSEED'], '314159')

    @unittest.skipUnless(os.name == 'posix', 'fixture executable uses a POSIX shell')
    def test_measurement_host_content_and_version_response_are_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / 'node'
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$CORPUS_FIXTURE_VERSION"\n')
            executable.chmod(0o755)
            with patch('ink_planner.corpus_environment.shutil.which', return_value=str(executable)):
                with patch.dict(os.environ, CORPUS_FIXTURE_VERSION='fixture-version-1'):
                    first = tool_identity('node', ['--version'])
                    self.assertEqual(first, tool_identity('node', ['--version']))
                with patch.dict(os.environ, CORPUS_FIXTURE_VERSION='fixture-version-2'):
                    second = tool_identity('node', ['--version'])
                    with executable.open('a') as output:
                        output.write('# changed executable bytes\n')
                    third = tool_identity('node', ['--version'])
            self.assertEqual(first['sha256'], second['sha256'])
            self.assertNotEqual(first['stdout_sha256'], second['stdout_sha256'])
            self.assertNotEqual(second['sha256'], third['sha256'])
            with patch('ink_planner.corpus_environment.shutil.which', return_value=None):
                self.assertEqual(tool_identity('node', ['--version']), {'available': False})


if __name__ == '__main__':
    unittest.main()
