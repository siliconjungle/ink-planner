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
