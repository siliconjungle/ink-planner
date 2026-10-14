import unittest
from ink_planner.semantic_search import interleave_premise_classes,search

def term(name,*args):return dict(sort={'Value':'U32'},node={'Op':[name,list(args)]})
def var(name):return dict(sort={'Value':'U32'},node={'Free':name})

class PremiseFairness(unittest.TestCase):
    def test_class_order_is_stable_complete_and_does_not_use_names(self):
        rows=[(i,None,None,str(i),{'conditions':([]if i in [3,7]else[{}])},None)for i in range(10)]
        ordered=interleave_premise_classes(rows)
        self.assertEqual([r[0]for r in ordered],[3,0,7,1,2,4,5,6,8,9])
        self.assertEqual(interleave_premise_classes(rows[:3]),rows[:3])
        self.assertEqual(interleave_premise_classes([]),[])

    def test_derived_guards_have_a_separate_fair_queue(self):
        laws = [dict(conditions=[], dependencies=['proof']),
                dict(conditions=[{}], dependencies=[]),
                dict(conditions=[], dependencies=[]),
                dict(conditions=[], dependencies=['other'])]
        rows = [(i,None,None,str(i),law,None) for i,law in enumerate(laws)]
        self.assertEqual([r[0] for r in interleave_premise_classes(rows)], [2,1,0,3])

    def test_rejected_shortcuts_cannot_starve_checked_alternative(self):
        source=term('source',var('input'))
        params={'x':{'Value':'U32'}}
        base=dict(params=params,**{'from':term('source',var('x'))},proof='Normalize',dependencies=[])
        laws={str(i):dict(base,to=term('shortcut'+str(i),var('x')),conditions=[{'from':var('x'),'to':term('zero')}])for i in range(12)}
        laws['alternative']=dict(base,to=term('alternative',var('x')),conditions=[])
        catalogue=dict(roots=list(laws),objects=laws)
        costs={'source':100,'alternative':50,**{'shortcut'+str(i):0 for i in range(12)}}
        seen=[]
        def check(proposal):
            seen.append(proposal[-1]['law'])
            return proposal[-1]['law']=='alternative'
        applications,receipt=search(dict(functions={'run':source}),catalogue,check,budget=2,rounds=1,operation_costs=costs)
        self.assertEqual(seen[0],'alternative')
        self.assertEqual(applications[-1]['law'],'alternative')
        rejected,_=search(dict(functions={'run':source}),catalogue,lambda _:False,budget=2,rounds=1,operation_costs=costs)
        self.assertEqual(rejected,[])

if __name__=='__main__':unittest.main()
