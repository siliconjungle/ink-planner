import copy,json,tempfile,unittest
from pathlib import Path
from ink_knowledge import Store
from ink_knowledge.store import entry
from ink_knowledge.law_templates import generate,substitute_types
from producers.list_slice_laws import named_laws
class LawTemplates(unittest.TestCase):
    def fixture(self,root,actual='U32'):
        generic=named_laws({'Named':'$A'},{'Named':'$B'})[0][1]
        obj=entry('definition','ink-semantic-law-template-v1',dict(type_parameters=['A','B'],law=generic,dependencies={}))
        store=Store.publish(root,[obj]);store.refresh_index()
        body=substitute_types(generic['from'],dict(A=actual,B='Bool'))
        return store,dict(functions={'run':body}),dict(program=dict(functions=[],records={},enums={}))
    def test_infers_input_and_output_sorts_from_typed_structure(self):
        with tempfile.TemporaryDirectory()as tmp:
            store,subject,core=self.fixture(tmp)
            objects,receipts=generate(store,subject,core)
            self.assertEqual(len(objects),1);self.assertEqual(receipts[0]['instances'],1)
            v=next(iter(objects.values()))['payload'];self.assertEqual(v['params']['mapper'],{'Arrow':[{'Value':'U32'},{'Value':'Bool'}]})
            self.assertNotIn('$A',json.dumps(v));self.assertNotIn('$B',json.dumps(v))
    def test_unrelated_shapes_and_mismatched_repeated_types_do_not_instantiate(self):
        with tempfile.TemporaryDirectory()as tmp:
            store,subject,core=self.fixture(tmp)
            subject['functions']['run']['node']['Op'][0]='method:drop'
            self.assertFalse(generate(store,subject,core)[0])
            store,subject,core=self.fixture(tmp)
            subject['functions']['run']['node']['Op'][1][0]['node']['Op'][1][1]['sort']['Arrow'][1]={'Value':'F32'}
            self.assertFalse(generate(store,subject,core)[0])
    def test_old_snapshot_has_no_templates_and_nominal_instances_bind_context(self):
        with tempfile.TemporaryDirectory()as tmp:
            old=Store.publish(tmp,[]);old.refresh_index();pin=old.root/'store/snapshots'/f'{old.snapshot_id}.json'
            store,subject,core=self.fixture(tmp,{'Named':'Row'})
            core['program']['records']={'Row':[['value','U32']]}
            self.assertFalse(generate(Store(tmp,pin),subject,core)[0])
            objects,_=generate(store,subject,core)
            self.assertEqual(next(iter(objects.values()))['payload']['definition_context']['records'],core['program']['records'])
