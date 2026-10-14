"""Composed list traversals retain their estimated total element count."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'planner'),str(ROOT/'knowledge')]
from ink_planner.semantic_search import cost,estimated_list_length
from producers.semantic_laws import value,var,bound,lam,op,binary,term
class CardinalityCostTests(unittest.TestCase):
 def test_known_lengths_and_conservative_compositions(self):
  s=value('U32');ls=value({'List':'U32'});xs=var(ls,'xs');ys=var(ls,'ys');f=lam(s,bound(s,0))
  empty=term(ls,'Nil');one=term(ls,{'Cons':[var(s,'x'),empty]})
  self.assertEqual(estimated_list_length(one,16),1)
  mapped=op(ls,'method:map',xs,f);filtered=op(ls,'method:filter',mapped,f);concat=op(ls,'method:concat',filtered,ys)
  self.assertEqual(estimated_list_length(concat,16),32)
  self.assertEqual(estimated_list_length(op(ls,'method:zip',concat,one,f),16),1)
  buffer=op(value({'Buffer':'U32'}),'call:buffer',one)
  self.assertEqual(estimated_list_length(buffer,16),1)
  self.assertEqual(estimated_list_length(op(ls,'method:freeze',buffer),16),1)
 def test_concat_callback_work_matches_two_ordered_folds(self):
  s=value('U32');ls=value({'List':'U32'});xs=var(ls,'xs');ys=var(ls,'ys');z=var(s,'z');g=lam(s,lam(s,binary('+',bound(s,1),bound(s,0))))
  fold=lambda a,b:op(s,'call:foldr',a,b,g)
  whole=fold(op(ls,'method:concat',xs,ys),z);split=fold(xs,fold(ys,z))
  policy={'estimate:loop_iterations':16,'estimate:list_cardinality':1}
  # Callback and input work are equal; only concat/fold structural prices differ.
  self.assertEqual(cost(whole,policy)-cost(whole,dict(policy,**{'estimate:loop_iterations':1})),cost(split,policy)-cost(split,dict(policy,**{'estimate:loop_iterations':1})))
if __name__=='__main__':unittest.main()
