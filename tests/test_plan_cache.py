import tempfile, unittest
from pathlib import Path
from ink_planner import plan_cache
class PlanCache(unittest.TestCase):
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
