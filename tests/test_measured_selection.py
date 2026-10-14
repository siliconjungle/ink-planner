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


class CandidateIdentity(unittest.TestCase):
    def test_maintained_and_protocol_implementations_are_not_discarded_as_baseline(self):
        from ink_planner.measured_selection import shortlist
        states=[dict(source='same'),dict(source='same',maintained={'package_sha256':'sum'}),
                dict(source='same',maintained={'package_sha256':'parity'}),
                dict(source='same',protocol={'direct_reads':['query']}),
                dict(source='same',properties=[{'facts':['proof-only']}])]
        self.assertEqual(shortlist(states,lambda _:0,8),[0,1,2,3])
        states.append(dict(source='same',maintained={'package_sha256':'sum'}))
        self.assertEqual(shortlist(states,lambda _:0,8),[0,1,2,3])
