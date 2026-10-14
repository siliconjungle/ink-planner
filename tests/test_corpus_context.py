import json
import tempfile
import unittest
from pathlib import Path
from ink_planner.corpus import bind_context, write_checkpoint


class CorpusContext(unittest.TestCase):
    def test_resume_requires_saved_matching_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'context.json'
            current = dict(compiler='a', snapshot='b', registry='c', producers='d', planner='e', manifest='f', timeout=300)
            with self.assertRaisesRegex(ValueError, 'exact original'):
                bind_context(path, current, True)
            bind_context(path, current, False)
            bind_context(path, current, True)
            for field in current:
                changed = dict(current, **{field: 'different'})
                with self.assertRaisesRegex(ValueError, 'exact original'):
                    bind_context(path, changed, True)
            self.assertEqual(json.loads(path.read_text()), current)

    def test_failed_checkpoint_preserves_previous_records(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'records.json'
            previous = [{'name': 'one', 'status': 0}]
            write_checkpoint(path, previous)
            with patch('ink_planner.corpus.os.replace', side_effect=OSError('interrupted')):
                with self.assertRaises(OSError):
                    write_checkpoint(path, [{'name': 'two', 'status': 0}])
            self.assertEqual(json.loads(path.read_text()), previous)
            self.assertEqual(list(Path(temporary).iterdir()), [path])
