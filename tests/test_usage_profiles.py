import copy,tempfile,unittest,uuid
from unittest.mock import patch
from ink_knowledge import Store
from ink_knowledge.usage_profiles import publish,discover,validate
from ink_planner.usage_selection import select
from producers.contextual_properties import contextual_queries
from producers.semantic_laws import value,var,op,term,law,word
from producers.resource_plans import release_points,propose

def profile(source='a'*64,backend='rust',distribution='observed-session'):
 return dict(schema=1,semantics='ink-observed-usage-v1',recording_id=str(uuid.uuid4()),source=source,target=dict(backend=backend,hardware='observed-machine',driver='observed-driver',toolchain='observed-toolchain',bridge='owned'),workload=dict(distribution=distribution,shape=[10],concurrency=1,residency='host'),counts={'call:query':3,'call:update':1},peak_pending=3,peak_pending_bytes=120,events=8,failed=0,saturated=False,shapes=[dict(shape=['query',10,40],count=3)])
class Usage(unittest.TestCase):
 def test_publication_discovery_pages_and_keeps_source_target_cohorts_separate(self):
  with tempfile.TemporaryDirectory() as d:
   store=Store.publish(d,[])
   p=profile();store=publish(store,p);store=publish(store,p)
   second=profile(distribution='another');store=publish(store,second)
   store=publish(store,profile(source='b'*64));store=publish(store,profile(backend='javascript'))
   rows=discover(store,'a'*64,'rust');self.assertEqual(len(rows),2);self.assertEqual(sum(r['counts']['call:query'] for r in rows),6)
 def test_cumulative_windows_do_not_double_count_and_distinct_windows_do(self):
  with tempfile.TemporaryDirectory() as d:
   store=Store.publish(d,[]);p=profile();store=publish(store,p)
   newer=copy.deepcopy(p);newer['events']=12;newer['counts']['call:query']=5;store=publish(store,newer)
   rows=discover(store,'a'*64,'rust');self.assertEqual(rows[0]['counts']['call:query'],5)
   separate=profile();store=publish(store,separate);self.assertEqual(discover(store,'a'*64,'rust')[0]['counts']['call:query'],8)
 def test_bad_recordings_cannot_be_published_as_cost_evidence(self):
  for field,value_ in [('source','bad'),('counts',{'call:query':True}),('peak_pending',-1),('events',0),('shapes',[dict(shape=['x',-1,0],count=1)])]:
   p=profile();p[field]=value_
   with self.assertRaises(ValueError):validate(p)
 def test_selection_needs_every_cohort_and_baseline(self):
  rows=[dict(profile(),evidence=['1']),dict(profile(distribution='two'),evidence=['2'])]
  with patch('ink_planner.usage_selection.rank',side_effect=[('fast',dict(total_ns={'base':'100','fast':'20'})),('base',dict(total_ns={'base':'10'}))]):
   selected,receipt=select(None,'a'*64,['base','fast'],rows,'base');self.assertEqual(selected,'base');self.assertNotIn('fast',receipt['costs'])
  with patch('ink_planner.usage_selection.rank',return_value=('fast',dict(total_ns={'base':'100','fast':'20'}))):
   self.assertEqual(select(None,'a'*64,['base','fast'],rows,'base')[0],'fast')
  with patch('ink_planner.usage_selection.rank',return_value=('base',dict(total_ns={'fast':'20'}))):
   self.assertEqual(select(None,'a'*64,['base','fast'],rows,'base')[0],'base')
 def test_arbitrary_database_context_not_only_named_cardinality_probe(self):
  u=value('U32');b=value('Bool');x=var(u,'x');subject=op(u,'binary:+',var(u,'a'),word('U32',1));left=op(b,'binary:==',x,x)
  l=law({'x':u},left,term(b,{'Bool':True}))
  queries=list(contextual_queries(l,subject,{'a':u}));self.assertTrue(queries)
  self.assertTrue(any(q[1]==term(b,{'Bool':True}) for q in queries))
 def test_resource_strategies_preserve_output_and_use_observed_credits(self):
  graph=dict(stages=[dict(id='a',arguments=[]),dict(id='b',arguments=[{'Stage':'a'}])],output={'Stage':'b'})
  self.assertEqual(release_points(graph),[dict(after='b',values=['a'],inputs=[])])
  rows=list(propose(dict(route=graph,usage_profile=profile())));self.assertEqual(len(rows),4)
  self.assertEqual(rows[1]['step']['package']['admission'],dict(max_pending=3,max_pending_bytes=120))
if __name__=='__main__':unittest.main()
