import tempfile, unittest
from pathlib import Path
from ink_planner import plan_cache
class PlanCache(unittest.TestCase):
    def test_concurrent_eviction_during_inventory_does_not_fail_publication(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); vanished=root/('0'*64+'.json');vanished.write_bytes(b'{}')
            original_stat=Path.stat;original_lstat=Path.lstat;removed=[]
            def remove_once(path):
                if path==vanished and not removed:
                    path.unlink();removed.append(path)
            def racing_stat(path,*args,**kwargs):
                remove_once(path);return original_stat(path,*args,**kwargs)
            def racing_lstat(path):
                remove_once(path);return original_lstat(path)
            key='1'*64
            with patch.object(Path,'stat',racing_stat),patch.object(Path,'lstat',racing_lstat):
                plan_cache.save(root,key,b'{"candidate":"checked elsewhere"}')
            self.assertEqual(removed,[vanished])
            self.assertEqual(plan_cache.load(root,key),b'{"candidate":"checked elsewhere"}')

    def test_eviction_ignores_nonregular_and_unrelated_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cache=root/'cache';cache.mkdir()
            directory=cache/('a'*64+'.json');directory.mkdir()
            other=root/'keep.json';other.write_bytes(b'untouched')
            link=cache/('b'*64+'.json');link.symlink_to(other)
            unrelated=cache/'notes.json';unrelated.write_bytes(b'notes')
            for i in range(257):plan_cache.save(cache,f'{i:064x}',b'{}')
            self.assertTrue(directory.is_dir());self.assertTrue(link.is_symlink())
            self.assertEqual(other.read_bytes(),b'untouched');self.assertEqual(unrelated.read_bytes(),b'notes')
            self.assertEqual(len(list(cache.glob('*.json'))),259)

    def test_compressed_proof_data_invalidates_search_and_corpus_context(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from ink_planner.corpus import execution_context
        from ink_knowledge import Store
        import producers
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);knowledge=root/'knowledge';Store.publish(knowledge,[])
            package=knowledge/'producers';data=package/'data';data.mkdir(parents=True)
            (package/'__init__.py').write_text('')
            proof=data/'proof.json.gz';proof.write_bytes(b'first compressed proof')
            compiler=root/'compiler';compiler.write_text('compiler')
            core=root/'core';core.write_text('core')
            manifest=root/'manifest';manifest.write_text('manifest')
            snapshot=knowledge/'store/snapshot.json'
            args=SimpleNamespace(knowledge_root=knowledge,compiler=compiler,manifest=manifest,
                                 target='javascript',planning_effort='quick',timeout=60)
            with patch.object(producers,'__file__',str(package/'__init__.py')):
                first=plan_cache.fingerprint(compiler,core,snapshot,None,10,2)
                context=execution_context(args)
                proof.write_bytes(b'changed compressed proof')
                self.assertNotEqual(first,plan_cache.fingerprint(compiler,core,snapshot,None,10,2))
                self.assertNotEqual(context,execution_context(args))
    def test_every_explicit_input_invalidates_and_storage_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);paths=[root/name for name in ['compiler','core','snapshot','cost','context']]
            for path in paths:path.write_text(path.name)
            def key(budget=100,rounds=8):return plan_cache.fingerprint(*paths[:4],budget,rounds,paths[4])
            original=key();self.assertNotEqual(original,key(101));self.assertNotEqual(original,key(rounds=9))
            for path in paths:
                raw=path.read_bytes();path.write_bytes(raw+b'!');self.assertNotEqual(original,key());path.write_bytes(raw)
            cache=root/'cache';plan_cache.save(cache,original,b'{"untrusted":true}')
            self.assertEqual(plan_cache.load(cache,original),b'{"untrusted":true}')
            (cache/(original+'.json')).write_bytes(b'invalid');self.assertIsNone(plan_cache.load(cache,original))
            with self.assertRaises(ValueError):plan_cache.save(cache,original,b' '*(plan_cache.MAX_BYTES+1))
            for i in range(260):plan_cache.save(cache,f'{i:064x}',b'{}')
            self.assertEqual(len(list(cache.glob('*.json'))),256)
