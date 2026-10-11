"""External prices guide search while the existing checker remains authoritative."""
import json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'planner'),str(ROOT/'knowledge')]
from ink_planner.semantic_search import cost,load_cost_model,search
from producers.semantic_laws import op,value,var

class CostModelTests(unittest.TestCase):
    def setUp(self):
        self.s=value('U32');self.x=var(self.s,'x')
        self.a=op(self.s,'expensive',self.x);self.b=op(self.s,'cheap',self.x)
    def test_default_preserved_and_receivers_charged(self):
        self.assertEqual(cost(self.a),2)
        self.assertEqual(cost(self.a,{}),cost(self.a))
        self.assertEqual(cost(self.a,{'expensive':70}),71)
        self.assertEqual(cost(op(self.s,'expensive',self.a),{'expensive':70}),141)
    def test_search_prices_never_override_rejection(self):
        law=dict(params={'x':self.s},conditions=[],**{'from':self.a,'to':self.b})
        subject={'functions':{'f':self.a}};catalogue={'objects':{'law':law},'roots':['law']}
        seen=[]
        apps,receipt=search(subject,catalogue,lambda proposal:seen.append(proposal) or False,operation_costs={'expensive':90,'cheap':0})
        self.assertTrue(seen);self.assertEqual(apps,[]);self.assertEqual(receipt['rejected'],1)
        apps,receipt=search(subject,catalogue,lambda _:True,operation_costs={'expensive':90,'cheap':0})
        self.assertEqual([a['law'] for a in apps],['law']);self.assertEqual(receipt['selected_cost'],1)
        self.assertEqual(search(subject,catalogue,lambda _:True)[0],[])
    def test_malformed_external_prices_rejected(self):
        models=[[],{},dict(schema=True,operation_costs={}),dict(schema=1,operation_costs={'x':True}),dict(schema=1,operation_costs={'x':-1}),dict(schema=1,operation_costs={'x':1.5}),dict(schema=1,operation_costs={'':0}),dict(schema=1,operation_costs={'x':1000001}),dict(schema=1,operation_costs={},trusted=True)]
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'prices.json'
            for model in models:
                path.write_text(json.dumps(model))
                with self.subTest(model=model),self.assertRaises(ValueError):load_cost_model(path)
            path.write_text(json.dumps(dict(schema=1,operation_costs={'new:operation':0})))
            self.assertEqual(load_cost_model(path),{'new:operation':0})

if __name__=='__main__':unittest.main()
