"""Real cache files remain optional under concurrent eviction and malformed data."""
import json
import tempfile
import time
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from ink_planner import compiled_cache


class CompiledCacheIO(unittest.TestCase):
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
