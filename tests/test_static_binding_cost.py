"""A known lambda binding remains distinct from a dynamic callback call."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'planner'),str(ROOT/'knowledge')]
from ink_planner.semantic_search import cost,search
from producers.semantic_laws import value,var,bound,lam,op,binary,arrow
class StaticBindingCostTests(unittest.TestCase):
 def test_optional_price_preserves_argument_and_body_work(self):
  s=value('U32');x=var(s,'x');arg=binary('*',x,x);callback=lam(s,binary('+',bound(s,0),bound(s,0)));term=op(s,'apply',callback,arg)
  self.assertEqual(cost(term,{'apply':20}),20+cost(callback)+cost(arg))
  self.assertEqual(cost(term,{'apply':20,'estimate:let_binding':1}),1+cost(callback)+cost(arg))
  unknown=op(s,'apply',var(arrow(s,s),'f'),arg)
  self.assertEqual(cost(unknown,{'apply':20,'estimate:let_binding':1}),cost(unknown,{'apply':20}))
 def test_binding_estimate_cannot_admit_candidate(self):
  s=value('U32');x=var(s,'x');before=op(s,'apply',lam(s,binary('+',bound(s,0),bound(s,0))),x)
  candidate=dict(params={'x':s},conditions=[],**{'from':before,'to':x})
  catalogue={'objects':{'false':candidate},'roots':['false']}
  self.assertEqual(search({'functions':{'f':before}},catalogue,lambda _:False,operation_costs={'estimate:let_binding':1})[0],[])
if __name__=='__main__':unittest.main()
