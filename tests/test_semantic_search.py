import unittest
from ink_planner.semantic_search import search


def variable(name):
    return {'sort': {'Value': 'U32'}, 'node': {'Free': name}}


class SearchProofAlternatives(unittest.TestCase):
    def test_rejected_proof_does_not_hide_valid_proof_for_same_result(self):
        x = variable('x')
        source = {'sort': x['sort'], 'node': {'Op': ['call:example', [x]]}}
        pattern = {'sort': x['sort'], 'node': {'Op': ['call:example', [variable('arg')]]}}
        base = {'params': {'arg': x['sort']}, 'from': pattern, 'to': variable('arg'), 'conditions': []}
        rejected, valid = dict(base), dict(base)
        rejected['conditions'] = [{'from': variable('arg'), 'to': variable('arg')}]
        attempts = []
        def checker(applications):
            attempts.append(applications[-1]['law'])
            return applications[-1]['law'] == 'valid'
        applications, receipt = search({'functions': {'f': source}},
            {'roots': ['rejected', 'valid'], 'objects': {'rejected': rejected, 'valid': valid}}, checker)
        self.assertEqual(attempts, ['rejected', 'valid'])
        self.assertEqual(applications[0]['law'], 'valid')
        self.assertLess(receipt['selected_cost'], receipt['initial_cost'])

    def test_rejection_and_budget_exhaustion_preserve_baseline(self):
        x = variable('x')
        source = {'sort': x['sort'], 'node': {'Op': ['call:example', [x]]}}
        law = {'params': {'x': x['sort']}, 'from': source, 'to': x, 'conditions': []}
        for budget in [0, 1]:
            applications, receipt = search({'functions': {'f': source}},
                {'roots': ['rejected'], 'objects': {'rejected': law}}, lambda _: False, budget=budget)
            self.assertEqual(applications, [])
            self.assertEqual(receipt['selected_cost'], receipt['initial_cost'])
            self.assertLessEqual(receipt['attempts'], budget)


if __name__ == '__main__':
    unittest.main()
