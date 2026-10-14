import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from ink_knowledge import Store, identity, semantic_entry
from ink_planner.learning_provenance import capture, verify
from ink_planner.llvm_corpus import run
from producers.author_word_identities import checked_law
from producers.semantic_laws import value, var


class LearningProvenance(unittest.TestCase):
    def test_exact_source_bytes_saved_and_changes_additions_deletions_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); sources = root/'sources'; sources.mkdir()
            source = sources/'author.py'; original = b'# exact authoring bytes\n'
            source.write_bytes(original); roots = {'author':sources}
            snapshot = capture(root/'capture', roots)
            self.assertEqual((root/'capture/author/author.py').read_bytes(), original)
            verify(snapshot, roots)
            source.write_bytes(b'# changed\n')
            with self.assertRaisesRegex(ValueError,'changed'): verify(snapshot, roots)
            source.write_bytes(original); extra=sources/'extra.py'; extra.write_text('')
            with self.assertRaisesRegex(ValueError,'changed'): verify(snapshot, roots)
            extra.unlink(); source.unlink()
            with self.assertRaisesRegex(ValueError,'changed'): verify(snapshot, roots)
            with self.assertRaises(FileExistsError): capture(root/'capture', roots)

    def test_generated_acquisition_appends_checked_laws_and_preserves_evidence(self):
        compiler = Path(os.environ.get('INK_COMPILER','target/debug/ink')).resolve()
        clang = shutil.which(os.environ.get('CLANG','clang'))
        self.assertTrue(compiler.is_file()); self.assertIsNotNone(clang)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'source.ink'; core=root/'core.json'
            source.write_text('module provenance_test;fn identity(x:u32)->u32{return x;}')
            import subprocess
            subprocess.run([str(compiler),'emit-core',str(source),'-o',str(core)],check=True,capture_output=True)
            s=value('U32'); x=var(s,'x')
            law,_=checked_law(compiler,core,dict(params={'x':s},conditions=[],**{'from':x,'to':x}),root,5)
            obj=semantic_entry(law); existing=identity(obj)
            destination=root/'publication';Store.publish(destination,[obj],{'previous':existing})
            output=root/'acquisition'
            receipt=run(compiler,clang,output,destination,seed=831731,count=4,depth=4,seconds=5)
            self.assertGreater(receipt['checked_unique_laws'],0)
            self.assertIn(existing,Store(destination).members())
            self.assertEqual(json.loads((destination/'store/names.json').read_text())['previous'],existing)
            manifest=json.loads((output/'manifest.json').read_text())
            self.assertIn('toolchain',manifest)
            for name,digest in manifest['toolchain']['sources'].items():
                import hashlib
                self.assertEqual(hashlib.sha256((output/'tool-sources'/name).read_bytes()).hexdigest(),digest)
            snapshot=Store(destination).snapshot_id
            with self.assertRaisesRegex(ValueError,'fresh'):run(compiler,clang,output,destination,count=4)
            self.assertEqual(Store(destination).snapshot_id,snapshot)


if __name__=='__main__': unittest.main()
