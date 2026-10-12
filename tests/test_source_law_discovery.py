import tempfile
import unittest
from pathlib import Path
from ink_knowledge import Store, entry, identity
from ink_planner.semantic_search import discover_roots


class SourceLawDiscovery(unittest.TestCase):
    def test_both_admitted_markers_are_discovered_without_unknown_markers(self):
        sort = {"Value": "U32"}
        markers = ["ink-conditional-laws-v1", "ink-general-laws-v1", "unknown-v1"]
        objects = [entry("rewrite", marker, {"untrusted": marker}, result=sort)
                   for marker in markers]
        with tempfile.TemporaryDirectory() as directory:
            store = Store.publish(Path(directory), objects)
            store.rebuild()
            self.assertEqual(discover_roots(store, [sort]),
                             {identity(obj) for obj in objects[:2]})
            self.assertEqual(discover_roots(store, [{"Value": "I32"}]), set())
        # These are discovery candidates; the independent kernel still admits proofs.
