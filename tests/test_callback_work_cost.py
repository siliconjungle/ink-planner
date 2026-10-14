"""Generic compile-time callback estimate distinguishes invariant arithmetic."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'planner'),str(ROOT/'knowledge')]
from ink_planner.semantic_search import cost,search
from producers.semantic_laws import value,var,bound,lam,op,binary

class CallbackWorkTests(unittest.TestCase):
 def test_weight_hoisting_and_unknown_operator(self):
  s=value('U32');xs=var(value({'List':'U32'}),'xs');k=var(s,'k');x=bound(s,1);a=bound(s,0)
  def fold(body):return op(s,'custom:walk',xs,var(s,'zero'),lam(s,lam(s,body)))
  inner=fold(binary('+',binary('*',x,k),a))
  outer=binary('*',fold(binary('+',x,a)),k)
  self.assertEqual(cost(inner,{'estimate:loop_iterations':1}),cost(outer,{'estimate:loop_iterations':1}))
  self.assertLess(cost(outer),cost(inner))
  self.assertLess(cost(outer,{}),cost(inner,{}))
  policy={'estimate:loop_iterations':16}
  self.assertLess(cost(outer,policy),cost(inner,policy))
  law=dict(params={'xs':xs['sort'],'zero':s,'k':s},conditions=[],**{'from':inner,'to':outer})
  subject={'functions':{'f':inner}};catalogue={'objects':{'candidate':law},'roots':['candidate']}
  # Cost can suggest the candidate but cannot authorize its equality.
  self.assertEqual(search(subject,catalogue,lambda _:False,operation_costs=policy)[0],[])
  self.assertEqual(len(search(subject,catalogue,lambda _:True,operation_costs=policy)[0]),1)
 def test_scalar_arguments_never_receive_list_multiplier(self):
  s=value('U32');x=var(s,'x');callback=lam(s,binary('*',bound(s,0),x))
  call=op(s,'apply',callback,x)
  self.assertEqual(cost(call),cost(call,{'estimate:loop_iterations':16}))
 def test_representation_conversion_does_not_erase_repeated_work(self):
  s=value('U32');xs=var(value({'List':'U32'}),'xs');bs=value({'Buffer':'U32'})
  callback=lam(s,binary('+',bound(s,0),var(s,'k')))
  mapped=op(xs['sort'],'method:map',xs,callback)
  buffered=op(xs['sort'],'method:freeze',op(bs,'method:map',op(bs,'call:buffer',xs),callback))
  def repeated_work(t):return cost(t,{'estimate:loop_iterations':16})-cost(t,{'estimate:loop_iterations':1})
  self.assertEqual(repeated_work(mapped),repeated_work(buffered))
  self.assertGreater(repeated_work(buffered),0)
  # Database prices control participation; they never establish equality.
  disabled={'estimate:callback_carrier:Buffer':0}
  self.assertEqual(cost(buffered,dict(disabled,**{'estimate:loop_iterations':16})),cost(buffered,dict(disabled,**{'estimate:loop_iterations':1})))
if __name__=='__main__':unittest.main()
