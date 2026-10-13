import unittest
from ink_planner.near_misses import NearMisses, classify, premise_shape, skeleton
from ink_planner.semantic_search import search


def variable(name, sort='U32'):
    return {'sort': {'Value': sort}, 'node': {'Free': name}}


def op(name, args, sort='U32'):
    return {'sort': {'Value': sort}, 'node': {'Op': [name, args]}}


def boolean(value):
    return {'sort': {'Value': 'Bool'}, 'node': {'Bool': value}}


class Classification(unittest.TestCase):
    def test_checker_messages_map_to_documented_gates(self):
        self.assertEqual(classify('premise 1 of abc: normalisation did not establish equality'), ('premise', 1))
        self.assertEqual(classify('law does not match exact selected expression'), ('endpoint', None))
        self.assertEqual(classify('application law is not an enabled catalogue root'), ('not_root', None))
        self.assertEqual(classify('closure: checker session not ready'), ('closure', None))
        self.assertEqual(classify(None), ('unexplained', None))
        self.assertEqual(classify('something unforeseen'), ('other', None))

    def test_premise_shapes_erase_law_parameters_but_keep_structure(self):
        condition = {'from': op('binary:==', [variable('j'), variable('k')], 'Bool'), 'to': boolean(False)}
        self.assertEqual(premise_shape(condition, {'j': None, 'k': None}), 'binary:==(_, _) = Bool:False')
        self.assertEqual(skeleton(op('call:f', [op('call:g', [op('call:h', [variable('x')])])]), {'x': None}),
                         'call:f(call:g(call:h(..)))')


class Collector(unittest.TestCase):
    def test_counts_are_bounded_and_separate_admission_from_rejection(self):
        law = {'params': {'x': None}, 'conditions': [{'from': variable('x'), 'to': variable('x')}]}
        near = NearMisses()
        for _ in range(3):
            near.matched('l', law)
        near.checked('l', law, False, 'premise 0 of l: not established')
        near.checked('l', law, True)
        receipt = near.receipt(extra='kept')
        entry = receipt['laws']['l']
        self.assertEqual((entry['matched'], entry['checked'], entry['admitted'], entry['rejected'], entry['unchecked']),
                         (3, 2, 1, 1, 1))
        self.assertEqual(receipt['premise_shapes'], {'_ = _': 1})
        self.assertEqual(receipt['totals']['unchecked'], 1)
        self.assertEqual(receipt['extra'], 'kept')
        self.assertIn('no proof', receipt['scope'])


class SearchIntegration(unittest.TestCase):
    def test_search_records_premise_failures_without_changing_its_result(self):
        x = variable('x')
        source = op('call:example', [x])
        pattern = op('call:example', [variable('arg')])
        guarded = {'params': {'arg': x['sort']}, 'from': pattern, 'to': variable('arg'),
                   'conditions': [{'from': op('binary:==', [variable('arg'), variable('arg')], 'Bool'), 'to': boolean(True)}]}
        catalogue = {'roots': ['guarded'], 'objects': {'guarded': guarded}}
        messages = []
        def checker(applications):
            if applications and applications[-1]['law'] == 'guarded':
                messages.append('premise 0 of guarded: equality not established')
                return False
            return True
        near = NearMisses()
        plain, plain_receipt = search({'functions': {'f': source}}, catalogue, checker)
        observed, receipt = search({'functions': {'f': source}}, catalogue, checker,
                                   near_misses=near, reason=lambda: messages[-1] if messages else None)
        self.assertEqual(plain, observed)
        self.assertEqual(plain_receipt['selected_cost'], receipt['selected_cost'])
        result = near.receipt()
        self.assertEqual(result['reasons'], {'premise': 1})
        self.assertEqual(result['premise_shapes'], {'binary:==(_, _) = Bool:True': 1})
        self.assertEqual(result['instance_shapes'], {'binary:==(free, free) = Bool:True': 1})
        self.assertEqual(result['laws_only_rejected'], 1)

    def test_budget_exhaustion_reports_unchecked_matches(self):
        x = variable('x')
        source = op('call:example', [x])
        law = {'params': {'x': x['sort']}, 'from': source, 'to': x, 'conditions': []}
        near = NearMisses()
        applications, receipt = search({'functions': {'f': source}}, {'roots': ['l'], 'objects': {'l': law}},
                                       lambda _: True, budget=0, near_misses=near)
        self.assertEqual(applications, [])
        result = near.receipt()
        self.assertEqual(result['totals']['matched'], 1)
        self.assertEqual(result['totals']['unchecked'], 1)
        self.assertEqual(result['laws']['l']['unchecked'], 1)


if __name__ == '__main__':
    unittest.main()
