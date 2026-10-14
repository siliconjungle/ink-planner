"""Restart time bounds and optional ordering remain compile-time hints."""
import unittest
from unittest.mock import patch
from ink_planner.stochastic_search import search_restarts
from ink_planner.semantic_search import search
from ink_planner.neighbourhood import Neighbourhood


class RestartDeadlines(unittest.TestCase):
    def test_restarts_share_one_clock_and_aggregate_stops(self):
        now=[0.0];calls=[]
        def run(*args,**options):
            calls.append(options);now[0]+=options['time_seconds']
            return [],dict(attempts=1,rejected=0,generated_proposals=1,selected_cost=1,
                memo=dict(rounds=1,timed_out=True,memory_exhausted=len(calls)==2))
        with patch('ink_planner.stochastic_search.time.monotonic',side_effect=lambda:now[0]):
            _,receipt=search_restarts(run,{}, {},lambda _:True,budget=16,restarts=4,time_seconds=5)
        self.assertEqual(len(calls),4);self.assertEqual(now[0],5)
        self.assertEqual(receipt['attempts'],4);self.assertTrue(receipt['memo']['timed_out'])
        self.assertTrue(receipt['memo']['memory_exhausted']);self.assertEqual(receipt['memo']['rounds'],4)

    def test_a_long_call_does_not_start_more_restarts(self):
        now=[0.0];calls=[]
        def run(*args,**options):
            calls.append(options);now[0]+=6
            return [],dict(attempts=1,rejected=0,generated_proposals=1,selected_cost=1,memo={})
        with patch('ink_planner.stochastic_search.time.monotonic',side_effect=lambda:now[0]):
            _,r=search_restarts(run,{}, {},lambda _:True,budget=16,restarts=4,time_seconds=5)
        self.assertEqual(len(calls),1);self.assertEqual(r['search_policy']['restarts'],1)
        self.assertTrue(r['memo']['timed_out'])

    def test_zero_time_and_budget_preserve_real_search_baseline(self):
        x=dict(sort={'Value':'U32'},node={'Free':'x'})
        source=dict(sort=x['sort'],node={'Op':['call:start',[x]]})
        catalogue=dict(roots=['law'],objects={'law':dict(params={'x':x['sort']},conditions=[],**{'from':source,'to':x})})
        for options in [dict(time_seconds=0),dict(budget=0)]:
            apps,r=search_restarts(search,dict(functions={'f':source}),catalogue,lambda _:self.fail('zero budget checked'),**options)
            self.assertEqual(apps,[]);self.assertEqual(r['attempts'],0)
            self.assertEqual(r['search_policy']['restarts'],1)

    def test_ordering_receives_actual_neighbour_queries_without_admission_power(self):
        x=dict(sort={'Value':'U32'},node={'Free':'x'})
        term=lambda n:dict(sort=x['sort'],node={'Op':[n,[x]]})
        laws={n:dict(params={'x':x['sort']},conditions=[],**{'from':term('start'),'to':term(n)}) for n in ['cheap','hinted']}
        class Hints:
            def __init__(self):self.queries=[];self.seen=[]
            def hints(self,target):self.queries.append(target);return ['hinted']
            def order(self,pending,hinted,round_index):
                self.seen.append(set(hinted))
                return sorted(pending,key=lambda p:p[2][-1]['law']not in hinted)
        hints=Hints();checked=[]
        def check(apps):checked.append(apps[-1]['law']);return apps[-1]['law']=='cheap'
        apps,r=search(dict(functions={'f':term('start')}),dict(roots=list(laws),objects=laws),check,
            neighbourhood=hints,budget=2,rounds=1,operation_costs={'start':100,'cheap':1,'hinted':2})
        self.assertTrue(hints.queries);self.assertTrue(all('hinted' in h for h in hints.seen))
        self.assertEqual(checked,['hinted','cheap']);self.assertEqual(apps[-1]['law'],'cheap')

    def test_nonfinite_or_negative_time_is_rejected(self):
        for duration in [-1,float('nan'),float('inf')]:
            with self.assertRaisesRegex(ValueError,'time budget'):
                search_restarts(None,{}, {},None,time_seconds=duration)


class NeighbourBoundary(unittest.TestCase):
    def fixture(self, labels):
        class NP:
            float32=float
            def asarray(self,value,**kwargs):return value
        class Index:
            def knn_query(self,*args,**kwargs):return labels,[[0]]
        n=object.__new__(Neighbourhood);n.ids=('first','last');n.k=1
        n.index=Index();n.np=NP();n.cache={};n.cache_bytes=0
        n.receipt=dict(queries=0,failures=[],active=True)
        return n

    def test_malformed_labels_disable_hints_and_release_cache(self):
        target=dict(sort={'Value':'U32'},node={'Free':'x'})
        for labels in [[[-1]],[[2]],[[0.5]],[[True]],[],[[]],[[0],[1]]]:
            n=self.fixture(labels);n.cache={'old':('first',)};n.cache_bytes=3
            self.assertEqual(n.hints(target),());self.assertFalse(n.receipt['active'])
            self.assertEqual(n.cache,{});self.assertEqual(n.cache_bytes,0)
        n=self.fixture([[0,0]]);n.k=2
        self.assertEqual(n.hints(target),());self.assertFalse(n.receipt['active'])

    def test_query_cache_has_byte_bound_and_reuses_valid_labels(self):
        n=self.fixture([[1]])
        term=lambda i:dict(sort={'Value':'U32'},node={'Word':i})
        with patch('ink_planner.neighbourhood.MAX_CACHE_BYTES',80):
            for i in range(8):
                self.assertEqual(n.hints(term(i)),('last',));self.assertLessEqual(n.cache_bytes,80)
            count=n.receipt['queries'];n.hints(term(7));self.assertEqual(n.receipt['queries'],count)
            huge=dict(sort={'Value':'U32'},node={'Free':'x'*100})
            self.assertEqual(n.hints(huge),('last',));self.assertLessEqual(n.cache_bytes,80)
            n.hints(huge);self.assertEqual(n.receipt['queries'],count+2)


if __name__=='__main__':unittest.main()
