"""Cost-model regressions; checked applicability is a separate authority."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'planner'),str(ROOT/'knowledge')]
from ink_planner.semantic_search import cost
from producers.semantic_laws import bound,lam,op,value,var,word


class CollectionAccessCostTests(unittest.TestCase):
    def setUp(self):
        self.s=value('U32');self.ls=value({'List':'U32'})
        self.xs=var(self.ls,'xs');self.ys=var(self.ls,'ys');self.z=var(self.s,'fallback')

    def head(self,xs,fallback):
        return op(self.s,'method:at_or',xs,word('U32',0),fallback)

    def test_two_direct_lookups_can_beat_materialising_a_concat(self):
        combined=op(self.ls,'method:concat',self.xs,self.ys)
        self.assertLess(cost(self.head(self.xs,self.head(self.ys,self.z))),cost(self.head(combined,self.z)))

    def test_lookup_and_count_still_pay_for_a_computed_receiver(self):
        mapped=op(self.ls,'method:map',self.xs,lam(self.s,bound(self.s,0)))
        counted=lambda xs:op(value('U64'),'call:count',xs)
        self.assertGreater(cost(self.head(mapped,self.z)),cost(self.head(self.xs,self.z)))
        self.assertGreater(cost(counted(mapped)),cost(counted(self.xs)))
        # Charge the complete receiver, including the authored callback model.
        # The catalogue's default iteration hypothesis can change independently.
        for iterations in [1,7]:
            prices={'method:map':13,'estimate:loop_iterations':iterations,
                    'estimate:callback_carrier:List':1,'estimate:list_cardinality':0}
            score=lambda term:cost(term,prices)
            self.assertEqual(score(counted(mapped))-score(counted(self.xs)),
                             13+iterations*score(lam(self.s,bound(self.s,0))))


if __name__=='__main__':unittest.main()
