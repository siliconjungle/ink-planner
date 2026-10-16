import os
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from ink_planner.corpus import copy_sqlite_snapshot


class AtomicIndex(unittest.TestCase):
    def test_replacement_during_clone_falls_back_to_original_read_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            a, b, dest = [Path(temporary)/n for n in ['source.sqlite', 'replacement.sqlite', 'copy.sqlite']]
            for path, value in [(a, 31), (b, 37)]:
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute('create table values_seen(value integer)')
                    db.execute('insert into values_seen values(?)', (value,))
            def replace_during_clone(source, destination):
                os.replace(b, a)
                shutil.copyfile(source, destination)
                return True
            with patch('ink_planner.corpus._clone_file', side_effect=replace_during_clone):
                copy_sqlite_snapshot(a, dest)
            with closing(sqlite3.connect(dest)) as db:
                self.assertEqual(db.execute('select * from values_seen').fetchall(), [(31,)])
            with closing(sqlite3.connect(a)) as db:
                self.assertEqual(db.execute('select * from values_seen').fetchall(), [(37,)])


if __name__ == '__main__':
    unittest.main()
