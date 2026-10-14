import tempfile,sqlite3,unittest
from pathlib import Path
from contextlib import closing
from ink_knowledge import Store,semantic_entry
from ink_knowledge.pattern_index import candidates
from ink_planner.semantic_search import discover_roots
from producers.semantic_laws import value,var,word,op,law
class PatternDiscovery(unittest.TestCase):
    def laws(self):
        s=value('U32');x=var(s,'x')
        mul=op(s,'binary:*',x,word('U32',1));add=op(s,'binary:+',x,word('U32',0));sub=op(s,'binary:-',x,word('U32',0))
        return [semantic_entry(v)for v in [law({'x':s},mul,add),law({'x':s},add,x),law({'x':s},sub,x)]],mul
    def test_reachable_heads_include_a_later_rule_without_loading_unrelated_heads(self):
        with tempfile.TemporaryDirectory()as tmp:
            objects,seed=self.laws();store=Store.publish(tmp,objects);store.refresh_index()
            all_=discover_roots(store,[value('U32')]);useful=discover_roots(store,[value('U32')],[seed])
            self.assertEqual(len(all_),3);self.assertEqual(len(useful),2)
            self.assertEqual(len(candidates(store,value('U32'),'Op:binary:*')),1)
    def test_legacy_index_and_missing_index_remain_conservative_then_migrate(self):
        with tempfile.TemporaryDirectory()as tmp:
            objects,seed=self.laws();store=Store.publish(tmp,objects);store.refresh_index()
            with closing(sqlite3.connect(store.root/'index.sqlite'))as db,db:
                db.execute('DROP TABLE pattern_roots');db.execute('DROP TABLE pattern_targets')
            self.assertEqual(len(discover_roots(store,[value('U32')],[seed])),3)
            store.refresh_index();self.assertEqual(len(discover_roots(store,[value('U32')],[seed])),2)
            (store.root/'index.sqlite').unlink()
            self.assertEqual(len(discover_roots(store,[value('U32')],[seed])),3)
    def test_wildcard_root_remains_applicable_at_every_head(self):
        with tempfile.TemporaryDirectory()as tmp:
            objects,seed=self.laws();s=value('U32');x=var(s,'x');objects.append(semantic_entry(law({'x':s},x,x)))
            store=Store.publish(tmp,objects);store.refresh_index()
            self.assertEqual(len(candidates(store,s,'Op:binary:*')),2)
