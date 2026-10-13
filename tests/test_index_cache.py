import json,sqlite3,tempfile,unittest
from pathlib import Path
from ink_knowledge import Store
from ink_knowledge.store import entry,identity
from ink_knowledge.index_cache import MAX_SNAPSHOTS
class IndexCache(unittest.TestCase):
    def test_witnesses_shared_by_leaf_and_old_pins_survive_cache_eviction(self):
        with tempfile.TemporaryDirectory()as tmp:
            objects=[entry('definition','test',{'i':i})for i in range(64)]
            store=Store.publish(tmp,objects);store.refresh_index();old=store.snapshot_id;oid=identity(objects[0])
            with sqlite3.connect(store.root/'index.sqlite')as db:
                self.assertEqual(db.execute('SELECT count(*) FROM witnesses').fetchone()[0],1)
            for i in range(MAX_SNAPSHOTS+2):
                store=store.extend([entry('definition','test',{'i':100+i})]);store.refresh_index()
            with sqlite3.connect(store.root/'index.sqlite')as db:
                self.assertEqual(db.execute('SELECT count(*) FROM snapshots').fetchone()[0],MAX_SNAPSHOTS)
                self.assertIsNone(db.execute('SELECT 1 FROM snapshots WHERE id=?',(old,)).fetchone())
            pinned=Store(tmp,store.root/'store/snapshots'/f'{old}.json')
            self.assertEqual(pinned.object(oid),objects[0]);self.assertEqual(len(pinned.query(kind='definition')),64)
            pinned.refresh_index();self.assertEqual(pinned.object(oid),objects[0])
    def test_legacy_index_migrates_without_changing_exported_membership(self):
        with tempfile.TemporaryDirectory()as tmp:
            obj=entry('definition','test',{'i':1});store=Store.publish(tmp,[obj]);store.refresh_index();oid=identity(obj);before=store.bundle([oid]);path=store.root/'index.sqlite'
            with sqlite3.connect(path)as db:
                rows=list(db.execute('SELECT m.snapshot_id,m.entry_id,w.witness FROM members m JOIN witnesses w ON w.id=m.witness_id'))
                snaps=list(db.execute('SELECT id,root FROM snapshots'))
                db.execute('PRAGMA foreign_keys=OFF');db.execute('DROP TABLE members');db.execute('DROP TABLE witnesses');db.execute('DROP TABLE snapshots')
                db.execute('CREATE TABLE snapshots(id TEXT PRIMARY KEY,root TEXT)');db.executemany('INSERT INTO snapshots VALUES(?,?)',snaps)
                db.execute('CREATE TABLE members(snapshot_id TEXT,entry_id TEXT,witness TEXT,PRIMARY KEY(snapshot_id,entry_id))');db.executemany('INSERT INTO members VALUES(?,?,?)',rows)
            self.assertEqual(Store(tmp).bundle([oid]),before)
            Store(tmp).refresh_index();self.assertEqual(Store(tmp).bundle([oid]),before)
            with sqlite3.connect(path)as db:self.assertIn('witness_id',{r[1]for r in db.execute('PRAGMA table_info(members)')})

class WarmIndex(unittest.TestCase):
    def test_warm_ensure_does_not_rebuild_index_and_stale_pin_refreshes(self):
        with tempfile.TemporaryDirectory()as tmp:
            obj=entry('definition','test',{'i':1});store=Store.publish(tmp,[obj]);self.assertTrue(store.ensure_index())
            path=store.root/'index.sqlite';inode=path.stat().st_ino;before=store.bundle([identity(obj)])
            # Warm recency hints may update SQLite metadata. They must not
            # rebuild/replace the file or change authenticated membership.
            self.assertFalse(Store(tmp).ensure_index());self.assertEqual(path.stat().st_ino,inode)
            self.assertEqual(Store(tmp).bundle([identity(obj)]),before)
            store=store.extend([entry('definition','test',{'i':2})]);self.assertTrue(store.ensure_index())
            self.assertEqual(len(store.query(kind='definition')),2)
    def test_missing_pattern_tables_trigger_migration(self):
        with tempfile.TemporaryDirectory()as tmp:
            store=Store.publish(tmp,[entry('definition','test',{'i':1})]);store.ensure_index()
            with sqlite3.connect(store.root/'index.sqlite')as db:db.execute('DROP TABLE pattern_roots')
            self.assertTrue(Store(tmp).ensure_index())
    def test_warm_cache_still_authenticates_mutated_canonical_object(self):
        with tempfile.TemporaryDirectory()as tmp:
            obj=entry('definition','test',{'i':1});store=Store.publish(tmp,[obj]);store.ensure_index();ident=identity(obj)
            (store.root/'store/objects'/f'{ident}.json').write_text(json.dumps(dict(obj,payload={'i':2})))
            self.assertFalse(Store(tmp).ensure_index())
            with self.assertRaises(ValueError):Store(tmp).bundle([ident])
