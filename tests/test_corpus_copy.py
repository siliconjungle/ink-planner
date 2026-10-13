import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ink_planner.corpus import private_copy


class PrivateKnowledgeCopy(unittest.TestCase):
    def test_indexes_are_private_and_objects_stay_shared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'root'
            (root / 'store' / 'objects').mkdir(parents=True)
            immutable = root / 'store' / 'objects' / 'a.json'
            immutable.write_text('{}\n')
            (root / 'index-published.json').write_text('{"published":[]}\n')
            with sqlite3.connect(root / 'index.sqlite') as db:
                db.execute('create table seen(id text)')
            scratch = Path(directory) / 'scratch'
            private_copy(root, scratch)
            # Immutable content stays hard-linked; nothing is duplicated.
            self.assertEqual(os.stat(immutable).st_ino, os.stat(scratch / 'store/objects/a.json').st_ino)
            for name in ['index.sqlite', 'index-published.json']:
                self.assertNotEqual(os.stat(root / name).st_ino, os.stat(scratch / name).st_ino, name)
            # A private evaluation writing its index leaves the shared one unchanged.
            with sqlite3.connect(scratch / 'index.sqlite') as db:
                db.execute("insert into seen values ('private observation')")
            with sqlite3.connect(root / 'index.sqlite') as db:
                self.assertEqual(db.execute('select count(*) from seen').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
