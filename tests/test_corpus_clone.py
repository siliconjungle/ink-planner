import os
import json
from contextlib import closing
from unittest.mock import patch
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

    def test_dirty_mirrors_and_coordinator_files_are_not_copied_as_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            obj = entry('definition', 'clone-recovery', {'value': 9})
            publication = Store.publish(root, [obj], {'seed': identity(obj)})
            (root / 'store/names.json').write_text('{"dirty":"unpublished"}')
            (root / 'store/snapshot.json').write_text('{broken')
            for name in ['.ink-pointer-abandoned', '.ink-object-abandoned', '.publication.sqlite-stale']:
                (root / 'store' / name).write_text('must not copy')
            private = Path(directory) / 'private'
            clone_knowledge(root, private)
            copied = Store(private)
            self.assertEqual(copied.publication_id, publication.publication_id)
            self.assertEqual(copied.names, publication.names)
            self.assertEqual(copied.object(identity(obj)), obj)
            self.assertEqual(json.loads((private / 'store/names.json').read_bytes()), copied.names)
            self.assertEqual(json.loads((private / 'store/snapshot.json').read_bytes()), copied.snapshot)
            self.assertFalse(os.path.samefile(root / 'store/.publication.sqlite', private / 'store/.publication.sqlite'))
            for name in ['.ink-pointer-abandoned', '.ink-object-abandoned', '.publication.sqlite-stale']:
                self.assertFalse((private / 'store' / name).exists())

    def test_names_only_change_during_clone_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            obj = entry('definition', 'clone-generation', {'value': 1})
            publication = Store.publish(root, [obj], {'seed': identity(obj)})
            private = Path(directory) / 'private'
            original = os.link
            changed = []
            def race(source, destination, **kwargs):
                if Path(destination).parent.resolve() == (private / 'store/objects').resolve() and not changed:
                    changed.append(True)
                    publication.extend([], {'alias': identity(obj)})
                return original(source, destination, **kwargs)
            with patch('ink_planner.corpus.os.link', side_effect=race):
                with self.assertRaisesRegex(ValueError, 'publication changed during corpus isolation'):
                    clone_knowledge(root, private)
            self.assertEqual(Store(root).snapshot_id, publication.snapshot_id)
            self.assertIn('alias', Store(root).names)

    def test_relative_paths_copy_publication_with_or_without_an_index(self):
        for indexed in [False, True]:
            with self.subTest(indexed=indexed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'source'
                obj = entry('definition', 'relative-clone', {'value': 7})
                publication = Store.publish(root, [obj])
                if indexed:
                    publication.rebuild()
                private = Path(directory) / 'private'
                clone_knowledge(Path(os.path.relpath(root)), Path(os.path.relpath(private)))
                self.assertEqual(Store(private).snapshot_id, publication.snapshot_id)
                self.assertEqual(Store(private).object(identity(obj)), obj)
                if indexed:
                    self.assertFalse(os.path.samefile(root / 'index.sqlite', private / 'index.sqlite'))
                    with closing(sqlite3.connect(private / 'index.sqlite')) as copied, copied:
                        copied.execute('DELETE FROM names')
                    self.assertEqual(publication.object(identity(obj)), obj)

    def test_backup_connections_close_before_the_clone_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            with closing(sqlite3.connect(root / 'index.sqlite')) as database, database:
                database.execute('CREATE TABLE contents (value INTEGER)')
                database.execute('INSERT INTO contents VALUES (7)')
            connections = []
            original = sqlite3.connect
            def track(*args, **kwargs):
                connection = original(*args, **kwargs)
                connections.append(connection)
                return connection
            private = Path(directory) / 'private'
            with patch('ink_planner.corpus.sqlite3.connect', side_effect=track), \
                    patch('ink_planner.corpus._clone_file', return_value=False):
                clone_knowledge(root, private)
            self.assertEqual(len(connections), 2)
            for connection in connections:
                with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed'):
                    connection.execute('SELECT 1')
            with closing(sqlite3.connect(private / 'index.sqlite')) as database:
                self.assertEqual(database.execute('SELECT value FROM contents').fetchall(), [(7,)])

    def test_backup_failure_closes_both_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            with closing(sqlite3.connect(root / 'index.sqlite')) as database, database:
                database.execute('CREATE TABLE contents (value INTEGER)')
            class FailedBackup(sqlite3.Connection):
                def backup(self, target, **kwargs):
                    raise sqlite3.OperationalError('injected backup failure')
            original = sqlite3.connect
            connections = []
            def track(*args, **kwargs):
                connection = original(*args, factory=FailedBackup, **kwargs)
                connections.append(connection)
                return connection
            with patch('ink_planner.corpus.sqlite3.connect', side_effect=track), \
                    patch('ink_planner.corpus._clone_file', return_value=False):
                with self.assertRaisesRegex(sqlite3.OperationalError, 'injected backup failure'):
                    clone_knowledge(root, Path(directory) / 'private')
            self.assertEqual(len(connections), 2)
            for connection in connections:
                with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed'):
                    connection.execute('SELECT 1')

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
            with closing(sqlite3.connect(root / 'index.sqlite')) as database, database:
                database.execute('PRAGMA journal_mode=WAL')
                database.execute('CREATE TABLE entries (value INTEGER)')
                database.execute('INSERT INTO entries VALUES (1)')
                database.commit()
                private = Path(directory) / 'private'
                clone_knowledge(root, private)
                with closing(sqlite3.connect(private / 'index.sqlite')) as copied, copied:
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
