import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ink_planner.corpus import copy_sqlite_snapshot


class SQLiteSnapshots(unittest.TestCase):
    def test_private_copy_and_shared_read_fence(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = [Path(temporary)/name for name in ['source.sqlite', 'private.sqlite']]
            with closing(sqlite3.connect(source)) as db, db:
                db.execute('create table values_seen(value integer)')
                db.execute('insert into values_seen values(7)')
            def inspect_lock(a, b):
                writer = sqlite3.connect(a, timeout=0)
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        with writer:
                            writer.execute('insert into values_seen values(9)')
                finally:
                    writer.close()
                return False
            with patch('ink_planner.corpus._clone_file', side_effect=inspect_lock) as clone:
                copy_sqlite_snapshot(source, target)
                clone.assert_called_once()
            with closing(sqlite3.connect(target)) as db, db:
                self.assertEqual(db.execute('select * from values_seen').fetchall(), [(7,)])
                db.execute('insert into values_seen values(11)')
            with closing(sqlite3.connect(source)) as db:
                self.assertEqual(db.execute('select * from values_seen').fetchall(), [(7,)])
            with self.assertRaises(ValueError):
                copy_sqlite_snapshot(source, target)

    def test_wal_backup_contains_committed_uncheckpointed_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = [Path(temporary)/name for name in ['source.sqlite', 'private.sqlite']]
            writer = sqlite3.connect(source)
            try:
                writer.execute('pragma journal_mode=wal')
                writer.execute('pragma wal_autocheckpoint=0')
                writer.execute('create table values_seen(value integer)')
                writer.execute('insert into values_seen values(13)'); writer.commit()
                self.assertGreater(Path(str(source)+'-wal').stat().st_size, 0)
                with patch('ink_planner.corpus._clone_file') as clone:
                    copy_sqlite_snapshot(source, target)
                    clone.assert_not_called()
                with closing(sqlite3.connect(target)) as db, db:
                    self.assertEqual(db.execute('select * from values_seen').fetchall(), [(13,)])
                    db.execute('insert into values_seen values(17)')
                self.assertEqual(writer.execute('select * from values_seen').fetchall(), [(13,)])
            finally:
                writer.close()

    def test_native_optional_clone_remains_an_independent_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = [Path(temporary)/name for name in ['source.sqlite', 'private.sqlite']]
            with closing(sqlite3.connect(source)) as db, db:
                db.execute('create table values_seen(value integer)')
                db.execute('insert into values_seen values(19)')
            copy_sqlite_snapshot(source, target)
            with closing(sqlite3.connect(target)) as db, db:
                db.execute('update values_seen set value=23')
            with closing(sqlite3.connect(source)) as db:
                self.assertEqual(db.execute('select * from values_seen').fetchall(), [(19,)])


if __name__ == '__main__':
    unittest.main()
