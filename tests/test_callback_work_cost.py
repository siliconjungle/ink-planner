"""Generic compile-time callback estimate distinguishes invariant arithmetic."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'planner'),str(ROOT/'knowledge')]
from ink_planner.semantic_search import cost,search
from producers.semantic_laws import value,var,bound,lam,op,binary

class CallbackWorkTests(unittest.TestCase):
 def test_literal_application_prices_body_and_argument_without_callback_wrapper(self):
  s=value('U32');argument=binary('*',var(s,'x'),var(s,'k'))
  body=binary('+',bound(s,0),var(s,'offset'))
  applied=op(s,'apply',lam(s,body),argument)
  self.assertEqual(cost(applied),cost(body)+cost(argument))
  self.assertEqual(cost(applied,{'estimate:let_binding':7}),cost(body)+cost(argument)+7)
  # Never discard eager argument work, even when the body does not use it.
  unused=op(s,'apply',lam(s,var(s,'constant')),argument)
  self.assertEqual(cost(unused),cost(var(s,'constant'))+cost(argument))

 def test_ordered_map_fold_fusion_is_visible_with_literal_callbacks(self):
  from ink_knowledge.law_templates import substitute_types
  from ink_planner.semantic_search import substitute
  from producers.pipeline_templates import templates
  s=value('F32');u=value('U32');xs=var(value({'List':'U32'}),'ids');table=var(value({'List':'F32'}),'table')
  mapper=lam(u,op(s,'method:at_or',table,bound(u,0),var(s,'fallback')))
  reducer=lam(s,lam(s,binary('+',bound(s,0),bound(s,1))))
  row=next(r for r in templates()if r['name']=='foldl-map')
  law=substitute_types(row['object']['payload']['law'],dict(A='U32',B='F32',S='F32'))
  args=dict(xs=xs,z=var(s,'seed'),f=mapper,r=reducer)
  before,after=[substitute(law[side],args)for side in ['from','to']]
  self.assertLess(cost(after),cost(before))
  catalogue={'objects':{'candidate':dict(law,**{'from':before,'to':after,'params':{'table':table['sort'],'ids':xs['sort'],'seed':s,'fallback':s}})},'roots':['candidate']}
  # Pricing is still no proof authority.
  self.assertEqual(search({'functions':{'f':before}},catalogue,lambda _:False)[0],[])

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
