"""Real cache files remain optional under concurrent eviction and malformed data."""
import json
import contextlib
import errno
import tempfile
import time
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from ink_planner import compiled_cache,plan_cache


class CompiledCacheIO(unittest.TestCase):
    def test_optional_write_failures_preserve_prior_packet_and_remove_partial_files(self):
        for client,stage in [(c,s)for c in ['compiled','search']for s in ['mkdir','mkstemp','write','replace','prune']]:
            with self.subTest(client=client,stage=stage),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);key='a'*64;old={'steps':[]};new={'steps':[],'new':True}
                cache=root/('compiled-cache'if client=='compiled'else'search-cache')
                save=lambda value:compiled_cache.save(root,key,value,{})if client=='compiled'else plan_cache.save(cache,key,json.dumps(value).encode())
                load=lambda:compiled_cache.load(root,key)['package']if client=='compiled'else json.loads(plan_cache.load(cache,key))
                self.assertTrue(save(old))
                sentinel=cache/'unrelated.txt';sentinel.write_bytes(b'keep')
                original=plan_cache.os.fdopen
                @contextlib.contextmanager
                def partial_write(fd,*args,**kwargs):
                    with original(fd,*args,**kwargs) as file:
                        class Writer:
                            def write(self,data):
                                file.write(data[:17]);file.flush()
                                raise OSError(errno.ENOSPC,'injected full device')
                        yield Writer()
                targets={'mkdir':(Path,'mkdir'),'mkstemp':(plan_cache.tempfile,'mkstemp'),
                    'write':(plan_cache.os,'fdopen'),'replace':(plan_cache.os,'replace'),
                    'prune':(plan_cache,'prune')}
                target,name=targets[stage]
                failure=partial_write if stage=='write' else OSError(errno.EACCES,'injected unavailable cache')
                with patch.object(target,name,side_effect=failure):
                    self.assertFalse(save(new))
                self.assertEqual(load(),new if stage=='prune' else old)
                self.assertEqual(sentinel.read_bytes(),b'keep')
                self.assertEqual(list(cache.glob('.ink-*')),[])

    def test_nonstorage_errors_are_not_hidden_as_cache_misses(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                compiled_cache.save(temporary,'a'*64,{},dict(invalid=float('nan')))

    def test_concurrent_eviction_during_inventory_preserves_published_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cache=root/'compiled-cache';cache.mkdir()
            vanished=cache/('0'*64+'.json');vanished.write_text('{}')
            original_stat=Path.stat;original_lstat=Path.lstat;removed=[]
            def remove_once(path):
                if path==vanished and not removed:
                    path.unlink();removed.append(path)
            def stat(path,*args,**kwargs):
                remove_once(path);return original_stat(path,*args,**kwargs)
            def lstat(path):
                remove_once(path);return original_lstat(path)
            key='1'*64
            with patch.object(Path,'stat',stat),patch.object(Path,'lstat',lstat):
                compiled_cache.save(root,key,{'steps':[]},{'unchecked_checks':0})
            self.assertEqual(removed,[vanished])
            self.assertEqual(compiled_cache.load(root,key)['package'],{'steps':[]})

    def test_eviction_preserves_nonregular_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cache=root/'compiled-cache';cache.mkdir()
            directory=cache/('a'*64+'.json');directory.mkdir()
            other=root/'keep';other.write_bytes(b'keep')
            link=cache/('b'*64+'.json');link.symlink_to(other)
            notes=cache/'notes.json';notes.write_bytes(b'notes')
            for i in range(130):compiled_cache.save(root,f'{i:064x}',{}, {})
            self.assertTrue(directory.is_dir());self.assertTrue(link.is_symlink())
            self.assertEqual(other.read_bytes(),b'keep');self.assertEqual(notes.read_bytes(),b'notes')
            self.assertEqual(len(list(cache.glob('*.json'))),131)

    def test_replacement_between_metadata_and_read_cannot_bypass_byte_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cache=root/'compiled-cache';cache.mkdir();key='a'*64
            path=cache/(key+'.json');path.write_text('{}')
            value=dict(schema=1,key=key,created_unix=time.time(),receipt={},package={'padding':'x'*512})
            original_open=Path.open;original_stat=Path.stat;replaced=[]
            def replace(file):
                if file==path and not replaced:
                    with original_open(file,'w')as out:json.dump(value,out)
                    replaced.append(True)
            def stat(file,*args,**kwargs):
                info=original_stat(file,*args,**kwargs);replace(file);return info
            def open_file(file,*args,**kwargs):
                if args and args[0]=='rb':replace(file)
                return original_open(file,*args,**kwargs)
            with patch.object(compiled_cache,'LIMIT',256),patch.object(Path,'open',open_file),patch.object(Path,'stat',stat):
                self.assertIsNone(compiled_cache.load(root,key))
            self.assertEqual(replaced,[True])

    def test_malformed_optional_packets_are_misses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cache=root/'compiled-cache';cache.mkdir();key='a'*64;path=cache/(key+'.json')
            for receipt in [None,[],False,'bad']:
                with self.subTest(receipt=receipt):
                    path.write_text(json.dumps(dict(schema=1,key=key,created_unix=time.time(),receipt=receipt)))
                    self.assertIsNone(compiled_cache.load(root,key))
            depth=sys.getrecursionlimit()+100
            path.write_text('['*depth+'0'+']'*depth)
            self.assertIsNone(compiled_cache.load(root,key))

if __name__=='__main__':unittest.main()
