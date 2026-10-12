import copy,tempfile,unittest
from pathlib import Path
from ink_knowledge import Store,semantic_entry
from ink_knowledge.store import identity
from ink_knowledge.proposal_views import ProposalViews
from producers.semantic_laws import value,var,word,op,law
class ProposalTransport(unittest.TestCase):
    def objects(self):
        s=value('U32');x=var(s,'x')
        return [semantic_entry(law({'x':s},op(s,'binary:'+operation,x,word('U32',constant)),x))for operation,constant in [('+',0),('*',1)]]
    def test_selected_views_share_root_without_copying_unused_bodies(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);objects=self.objects();source=Store.publish(root/'source',objects);items={identity(o):o for o in objects};transport=ProposalViews(source,items,root/'transport');ids=list(items)
            a=transport.bundle([ids[0]]);b=transport.bundle([ids[1]])
            self.assertEqual(a['snapshot'],b['snapshot']);self.assertEqual(set(a['objects']),{ids[0]});self.assertEqual(set(b['objects']),{ids[1]})
            self.assertFalse((root/'transport/store/objects').exists())
    def test_hash_mutation_is_rejected_before_transport(self):
        with tempfile.TemporaryDirectory()as tmp:
            objects=self.objects();source=Store.publish(Path(tmp)/'source',objects);items={identity(o):copy.deepcopy(o)for o in objects};ident=next(iter(items));transport=ProposalViews(source,items,Path(tmp)/'transport')
            items[ident]['payload']['proof']={'Hypothesis':0}
            with self.assertRaisesRegex(ValueError,'content mismatch'):transport.bundle([ident])
