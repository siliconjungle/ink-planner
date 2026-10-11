import copy,tempfile,unittest
from pathlib import Path
from ink_knowledge import Store,identity
from ink_knowledge.implementation_candidates import candidate_entry,publish_candidates,identity_selection
from ink_planner.implementation_selection import candidate_package

class SharedKnowledge(unittest.TestCase):
    def test_reference_reconstructs_a_pinned_complete_view_and_survives_extension(self):
        package=identity_selection('7'*64)
        with tempfile.TemporaryDirectory()as temporary:
            root=Path(temporary);store,ids=publish_candidates(root,[package],shared_knowledge=True)
            candidate=store.object(ids[0]);self.assertNotIn('knowledge',candidate['payload']['package'])
            actual=candidate_package(store,ids[0]);self.assertNotIn('knowledge_ref',actual)
            self.assertEqual(actual['knowledge'],store.bundle([]))
            pin=store.snapshot_id
            publish_candidates(root,[identity_selection('8'*64)],shared_knowledge=True)
            old=Store(root,root/'store/snapshots'/(pin+'.json'))
            self.assertEqual(candidate_package(old,ids[0]),actual)
    def test_reference_rejects_ambiguity_and_dependency_mismatch(self):
        package=identity_selection('7'*64)
        for mode in ['ambiguous','roots','schema']:
            candidate=candidate_entry(package,shared_knowledge=True)
            if mode=='ambiguous':candidate['payload']['package']['knowledge']=package['knowledge']
            if mode=='roots':candidate['payload']['package']['knowledge_ref']['roots']=['1'*64]
            if mode=='schema':candidate['payload']['package']['knowledge_ref']['schema']=False
            with tempfile.TemporaryDirectory()as temporary:
                store=Store.publish(Path(temporary),[candidate])
                with self.assertRaises(ValueError):candidate_package(store,identity(candidate))
