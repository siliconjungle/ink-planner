import json,tempfile,unittest
from pathlib import Path
from ink_knowledge import Store
from ink_knowledge.store import entry
from ink_knowledge.proposals import modules,generate
class SnapshotProducers(unittest.TestCase):
    def test_publication_enables_only_its_pinned_snapshot(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);old=Store.publish(root,[]);old.refresh_index();pin=root/'store/snapshots'/f'{old.snapshot_id}.json'
            current=old.extend([entry('definition','ink-semantic-producers-v1',dict(module='producers.list_slice_laws'))]);current.refresh_index()
            self.assertNotIn('producers.list_slice_laws',modules(Store(root,pin))[0])
            self.assertIn('producers.list_slice_laws',modules(current)[0])
    def test_discovery_does_not_allow_shells_or_external_imports(self):
        for name in ['os','producers.foo;rm','producers.__dict__','producers./foo']:
            with tempfile.TemporaryDirectory()as tmp:
                store=Store.publish(tmp,[entry('definition','ink-semantic-producers-v1',dict(module=name))]);store.refresh_index()
                with self.assertRaises(ValueError):modules(store)
