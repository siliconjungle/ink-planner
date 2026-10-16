"""Branch price hypotheses stay local, hygienic and separate from admission."""
import copy,unittest
from ink_planner.semantic_search import cost,branch_cardinalities
from ink_knowledge.cost_models import structural_prices
from producers.semantic_laws import value,var,bound,lam,op,word,term
S=value('U32');L=value({'List':'U32'});B=value('Bool')
def fold(xs):
 body=op(S,'binary:+',bound(S,0),op(S,'binary:*',bound(S,1),bound(S,1)))
 return op(S,'call:foldl',xs,word('U32',0),lam(S,lam(S,body)))
def guard(xs,comparison='binary:==',reverse=False):
 args=[op(value('U64'),'call:count',xs),word('U64',0)]
 return op(B,comparison,*(args[::-1]if reverse else args))
def choose(c,a,b):return op(a['sort'],'call:choose',c,a,b)
class BranchCardinalityCost(unittest.TestCase):
 def test_guarded_arm_prices_its_empty_callback_without_leaking_to_other_arm(self):
  xs=var(L,'xs');base=fold(xs);prices=structural_prices()
  for comparison,empty_arm in [('binary:==',True),('binary:!=',False)]:
   for reverse in [False,True]:
    condition=guard(xs,comparison,reverse);c=cost(condition)
    arms=[base,word('U32',0)] if empty_arm else [word('U32',0),base]
    self.assertEqual(cost(choose(condition,*arms)),1+c+cost(base,{'estimate:loop_iterations':0}))
    # The nonempty arm still pays its full loop cost.
    arms=arms[::-1]
    self.assertEqual(cost(choose(condition,*arms)),1+c+cost(base))
  self.assertGreater(cost(choose(guard(xs),base,word('U32',0)),{'estimate:branch_max:call:choose':0}),cost(choose(guard(xs),base,word('U32',0))))
 def test_unrelated_receivers_and_nonzero_or_unconfigured_tests_keep_work(self):
  xs=var(L,'xs');ys=var(L,'ys');base=fold(ys)
  for condition in [guard(xs),op(B,'binary:==',op(value('U64'),'call:count',ys),word('U64',1)),op(B,'binary:==',op(value('U64'),'call:unknown',ys),word('U64',0))]:
   self.assertEqual(cost(choose(condition,base,word('U32',0))),1+cost(condition)+cost(base))
 def test_bound_facts_shift_through_lambdas_and_strict_receivers_still_run(self):
  xs=bound(L,0);condition=guard(xs);prices=structural_prices()
  local=branch_cardinalities(condition,True,prices,())
  self.assertEqual(local,((xs,0),))
  from ink_planner.semantic_search import _cost
  # Under this lambda, Bound(0) is unrelated, and Bound(1) is the guarded list.
  self.assertEqual(_cost(lam(L,fold(bound(L,0))),prices,local),cost(lam(L,fold(bound(L,0)))))
  self.assertEqual(_cost(lam(L,fold(bound(L,1))),prices,local),cost(lam(L,fold(bound(L,1))),{'estimate:loop_iterations':0}))
  mapped=op(L,'method:map',var(L,'ys'),lam(S,op(S,'binary:*',bound(S,0),bound(S,0))))
  empty=choose(guard(mapped),fold(mapped),word('U32',0))
  self.assertGreater(cost(empty),cost(mapped))
 def test_literal_choices_and_bounded_hypotheses_preserve_inputs(self):
  xs=var(L,'xs');base=fold(xs);source=choose(term(B,{'Bool':True}),word('U32',7),base);saved=copy.deepcopy(source)
  self.assertEqual(cost(source),1+cost(source['node']['Op'][1][0])+cost(word('U32',7)))
  self.assertEqual(source,saved)
  facts=tuple((var(L,str(i)),0)for i in range(64))
  self.assertIs(branch_cardinalities(guard(xs),True,structural_prices(),facts),facts)
if __name__=='__main__':unittest.main()
