import copy,random,unittest
from ink_planner.semantic_search import children,key,match,substitute,replace
from ink_planner.term_matching import Matcher

def term(name,*args):return dict(sort={'Value':'U32'},node={'Op':[name,list(args)]})
def var(name):return dict(sort={'Value':'U32'},node={'Free':name})

class Matching(unittest.TestCase):
    def test_new_instance_invalidates_cached_misses_and_preserves_existing_matches(self):
        x=var('x');objects={}
        matcher=Matcher(objects,[],key,children,match,substitute,100000)
        target=term('newly-exposed',var('input'))
        self.assertEqual(matcher.matches(target),[])
        objects['new']=dict(params={'x':x['sort']},conditions=[],**{'from':term('newly-exposed',x),'to':x})
        matcher.add(['new'])
        self.assertEqual(matcher.matches(target),[('new',{'x':var('input')},var('input'))])
        matcher.add(['new'])
        self.assertEqual(len(matcher.matches(target)),1)
    def test_filters_and_cache_preserve_full_match_results(self):
        x=var('x');objects={}
        for i in range(30):
            objects[str(i)]=dict(params={'x':x['sort']},conditions=[],**{'from':term('a',term('b'+str(i%5),x),x),'to':term('z',x)})
        # Repeated holes, mismatched inner heads, exact sort and literal binds.
        targets=[term('a',term('b'+str(i%7),var('value')),var('value'if i%2 else'other'))for i in range(60)]
        matcher=Matcher(objects,objects,key,children,match,substitute,10000000)
        for target in targets*2:
            expected=[]
            for ident,law in objects.items():
                args={}
                if match(law['from'],target,law['params'],args)and set(args)==set(law['params']):
                    expected.append((ident,args,substitute(law['to'],args)))
            self.assertEqual(matcher.matches(target),expected)
        self.assertGreater(matcher.stats['cache_hits'],50)
        self.assertGreater(matcher.stats['structural_rejections'],0)
    def test_binders_remain_capture_checked(self):
        s={'Value':'U32'};arrow={'Arrow':[s,s]};x=var('x')
        pattern=dict(sort=arrow,node={'Lambda':x})
        law=dict(params={'x':s},conditions=[],**{'from':pattern,'to':dict(sort=arrow,node={'Lambda':term('id',x)})})
        matcher=Matcher({'law':law},['law'],key,children,match,substitute,100000)
        bound=dict(sort=s,node={'Bound':0})
        self.assertEqual(matcher.matches(dict(sort=arrow,node={'Lambda':bound})),[])
        self.assertEqual(len(matcher.matches(dict(sort=arrow,node={'Lambda':var('external')}))),1)
    def test_replace_shares_siblings_without_mutating_inputs(self):
        source=term('pair',term('nested',var('a')),var('b'));original=copy.deepcopy(source)
        result=replace(source,(0,0),var('c'))
        self.assertEqual(source,original);self.assertIs(result['node']['Op'][1][1],source['node']['Op'][1][1])
    def test_cache_is_bounded_and_eviction_preserves_matching(self):
        x=var('x');law=dict(params={'x':x['sort']},conditions=[],**{'from':term('id',x),'to':x})
        matcher=Matcher({'law':law},['law'],key,children,match,substitute,8000)
        for i in range(100):self.assertEqual(matcher.matches(term('id',var(str(i))))[0][2],var(str(i)))
        self.assertLessEqual(matcher.bytes,matcher.limit);self.assertGreater(matcher.stats['evictions'],0)
