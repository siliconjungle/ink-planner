import unittest
from ink_planner.semantic_search import search
from planner.tests.test_term_matching import var,term

class AlternativePremises(unittest.TestCase):
    def test_unusable_first_lemma_cannot_hide_a_checked_later_proof(self):
        x=var('x');goal=dict(**{'from':term('method:witness',x),'to':x})
        false=dict(sort={'Value':'Bool'},node={'Bool':False})
        true=dict(sort={'Value':'Bool'},node={'Bool':True})
        root=dict(params={'x':x['sort']},conditions=[goal],**{'from':term('method:main',x),'to':x})
        bad=dict(params={'x':x['sort']},conditions=[dict(**{'from':false,'to':true})],**goal)
        good=dict(params={'x':x['sort']},conditions=[],**goal)
        catalogue=dict(roots=['root'],objects={'root':root,'bad':bad,'good':good})
        def check(applications):
            if not applications:return True
            proof=applications[-1]['premises'][0]
            return isinstance(proof,dict)and proof.get('Use',{}).get('law')=='good'
        apps,receipt=search(dict(functions={'run':term('method:main',var('input'))}),catalogue,check,budget=8)
        self.assertEqual(apps[-1]['premises'][0]['Use']['law'],'good')
        self.assertEqual(receipt['rejected'],2)
    def test_false_conditions_stay_rejected_when_no_certificate_checks(self):
        x=var('x');law=dict(params={'x':x['sort']},conditions=[dict(**{'from':x,'to':term('different',x)})],**{'from':term('method:main',x),'to':x})
        apps,receipt=search(dict(functions={'run':term('method:main',var('input'))}),dict(roots=['law'],objects={'law':law}),lambda _:False,budget=8)
        self.assertEqual(apps,[]);self.assertGreater(receipt['rejected'],0)
