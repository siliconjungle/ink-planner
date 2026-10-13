import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from ink_planner.corpus import clone_knowledge
from ink_knowledge import Store, identity
from ink_knowledge.store import entry


class CorpusClone(unittest.TestCase):
    def test_only_the_authenticated_current_publication_is_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            old = entry('definition', 'test-v1', {'value': 0})
            current = [entry('definition', 'test-v1', {'value': n}) for n in range(1, 9)]
            previous = Store.publish(root, [old])
            publication = Store.publish(root, current, leaf_size=2)
            private = Path(directory) / 'private'
            clone_knowledge(root, private)
            copied = Store(private)
            self.assertEqual(copied.snapshot_id, publication.snapshot_id)
            self.assertEqual(copied.members(), publication.members())
            for value in current:
                self.assertEqual(copied.object(identity(value)), value)
            self.assertFalse((private / 'store/objects' / (identity(old) + '.json')).exists())
            self.assertFalse((private / 'store/snapshots' / (previous.snapshot_id + '.json')).exists())

    def test_immutable_inputs_share_files_but_index_writes_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            for name in ['store/objects/a.json', 'research/custom/input.json',
                         'libraries/custom.ink', 'producers/custom.py', 'custom.json',
                         'build/unused', 'compiled-cache/unused', '.git',
                         'producers/__pycache__/unused']:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            outside = Path(directory) / 'external-cache'
            outside.mkdir()
            (outside / 'do-not-copy').write_text('cache')
            (root / 'research/custom/target').symlink_to(outside, target_is_directory=True)
            with sqlite3.connect(root / 'index.sqlite') as database:
                database.execute('PRAGMA journal_mode=WAL')
                database.execute('CREATE TABLE entries (value INTEGER)')
                database.execute('INSERT INTO entries VALUES (1)')
                database.commit()
                private = Path(directory) / 'private'
                clone_knowledge(root, private)
                with sqlite3.connect(private / 'index.sqlite') as copied:
                    self.assertEqual(copied.execute('SELECT value FROM entries').fetchall(), [(1,)])
                    copied.execute('INSERT INTO entries VALUES (2)')
                self.assertEqual(database.execute('SELECT value FROM entries').fetchall(), [(1,)])
            for name in ['store/objects/a.json', 'research/custom/input.json',
                         'libraries/custom.ink', 'producers/custom.py', 'custom.json']:
                self.assertTrue(os.path.samefile(root / name, private / name))
            self.assertFalse(os.path.samefile(root / 'index.sqlite', private / 'index.sqlite'))
            for name in ['build', 'compiled-cache', '.git', 'producers/__pycache__']:
                self.assertFalse((private / name).exists())
            self.assertTrue((private / 'research/custom/target').is_symlink())


if __name__ == '__main__':
    unittest.main()
