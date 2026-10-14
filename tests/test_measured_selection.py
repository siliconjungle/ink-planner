import unittest
from ink_planner.measured_selection import choose
from ink_knowledge.selection_probes import policy

def report(candidate,baseline=None):
    return dict(rows=[dict(measurement='base',phase='call:run',complete_call_ns=[str(x)for x in (baseline or [100]*11)]),
                      dict(measurement='candidate',phase='call:run',complete_call_ns=[str(x)for x in candidate])])

class MeasuredSelection(unittest.TestCase):
    def test_releases_large_paired_gain_and_rejects_marginal_or_uncertain_winner(self):
        for samples,want in [([60]*11,'candidate'),([97]*11,'base'),([50]*6+[160]*5,'base')]:
            selected,receipt=choose(report(samples),{'call:run':1},'base',policy())
            self.assertEqual(selected,want)
            self.assertTrue(receipt['baseline_comparable'])
    def test_setup_amortisation_can_reverse_a_warm_winner(self):
        selected,_=choose(report([60]*11),{'call:run':1},'base',policy(),{'candidate':1000})
        self.assertEqual(selected,'base')
        selected,_=choose(report([60]*11),{'call:run':10000},'base',policy(),{'candidate':1000})
        self.assertEqual(selected,'candidate')
    def test_incomplete_and_unpaired_phases_cannot_authorise_switching(self):
        self.assertEqual(choose(report([10]*9),{'call:run':1},'base',policy())[0],'base')
        self.assertEqual(choose(report([10]*11),{'call:run':1,'call:other':1},'base',policy())[0],'base')

    def test_large_input_gain_cannot_hide_a_demonstrated_small_input_loss(self):
        rows=[]
        for plan,small,large in [('base',100,10000),('unguarded',180,1000),('guarded',100,1100)]:
            for phase,cost in [('small',small),('large',large)]:
                rows.append(dict(measurement=plan,phase=phase,complete_call_ns=[str(cost)]*11))
        selected,receipt=choose(dict(rows=rows),{'small':1000,'large':1000},'base',policy())
        self.assertEqual(selected,'guarded')
        self.assertEqual(receipt['estimates']['unguarded']['regressive_phases'],['small'])
        self.assertEqual(receipt['estimates']['guarded']['regressive_phases'],[])
        self.assertLess(float(receipt['estimates']['unguarded']['candidate_over_baseline']),.2)

    def test_noisy_phase_loss_is_not_claimed_as_demonstrated(self):
        rows=[]
        for plan,small,large in [('base',[100]*11,[10000]*11),('candidate',[80]*6+[130]*5,[1000]*11)]:
            for phase,costs in [('small',small),('large',large)]:
                rows.append(dict(measurement=plan,phase=phase,complete_call_ns=list(map(str,costs))))
        selected,receipt=choose(dict(rows=rows),{'small':1,'large':1},'base',policy())
        self.assertEqual(selected,'candidate')
        self.assertGreater(float(receipt['estimates']['candidate']['phases']['small']['candidate_over_baseline']),1)
        self.assertLess(float(receipt['estimates']['candidate']['phases']['small']['lower_bound']),1)

    def test_comparison_uses_the_shared_round_count_for_every_phase(self):
        rows=[]
        for plan,cost in [('base',100),('candidate',60)]:
            for phase,count in [('short',9),('long',11)]:
                rows.append(dict(measurement=plan,phase=phase,complete_call_ns=[str(cost)]*count))
        selected,receipt=choose(dict(rows=rows),{'short':1,'long':1},'base',policy())
        self.assertEqual(selected,'candidate')
        self.assertAlmostEqual(float(receipt['estimates']['candidate']['phases']['long']['candidate_over_baseline']),.6)


class CandidateIdentity(unittest.TestCase):
    def test_many_cheap_variants_do_not_use_every_slot_before_a_distinct_family(self):
        from ink_planner.measured_selection import shortlist
        states=[dict(source='baseline')]+[dict(source='variant-'+str(i))for i in range(20)]+[dict(source='different',price=100)]
        families=[set()]+[{'rewrites'}for _ in range(20)]+[{'representation'}]
        self.assertEqual(shortlist(states,lambda s:s.get('price',0),4,families),[0,1,21,2])
        # A proposal label never makes an unemittable state measurable.
        states[-1]['capability']={'emittable':False}
        self.assertEqual(shortlist(states,lambda s:s.get('price',0),4,families),[0,1,2,3])

    def test_family_diversity_keeps_operational_deduplication_and_baseline(self):
        from ink_planner.measured_selection import shortlist
        states=[dict(source='baseline'),dict(source='baseline',properties=['fact']),dict(source='new'),dict(source='new')]
        families=[set(),{'properties'},{'rewrite'},{'another-route'}]
        self.assertEqual(shortlist(states,lambda _:0,8,families),[0,2])
        self.assertEqual(shortlist(states,lambda _:0,1,families),[0])
        with self.assertRaisesRegex(ValueError,'family count'):shortlist(states,lambda _:0,8,[])

    def test_family_ancestry_follows_the_retained_first_admitted_path(self):
        from ink_planner.measured_selection import proposal_families
        edges=[dict(source='base',target='a',recipe='rewrite'),dict(source='a',target='b',recipe='layout'),
               dict(source='base',target='b',recipe='other-route'),dict(source='b',target='base',recipe='cycle'),
               dict(source='missing',target='unreached',recipe='unknown')]
        self.assertEqual(proposal_families(['base','a','b','unreached'],edges),[set(),{'rewrite'},{'rewrite','layout'},set()])
        self.assertEqual(proposal_families([],edges),[])

    def test_maintained_and_protocol_implementations_are_not_discarded_as_baseline(self):
        from ink_planner.measured_selection import shortlist
        states=[dict(source='same'),dict(source='same',maintained={'package_sha256':'sum'}),
                dict(source='same',maintained={'package_sha256':'parity'}),
                dict(source='same',protocol={'direct_reads':['query']}),
                dict(source='same',properties=[{'facts':['proof-only']}])]
        self.assertEqual(shortlist(states,lambda _:0,8),[0,1,2,3])
        states.append(dict(source='same',maintained={'package_sha256':'sum'}))
        self.assertEqual(shortlist(states,lambda _:0,8),[0,1,2,3])
