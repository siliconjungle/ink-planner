import unittest
from ink_planner.planning_diagnostics import diagnose


def receipt():
    return dict(selected='a'*64, stop_reason='time', attempts=4, admitted_states=4,
                candidates=[dict(plan=c*64, measurement=c, capability=dict(emittable=True)) for c in 'abcd'],
                selection=dict(reason='baseline retained', excluded={'d': 'missing phases: call:q'}),
                measurement_acquisition=dict(shortlist=dict(measured_indices=[0,1,3]),
                                            acquisition=dict(rows=[dict(measurement='a')]),
                                            rejected=[dict(measurement='b', reason='output mismatch')]))


class PlanningDiagnostics(unittest.TestCase):
    def test_distinguishes_budgets_validation_and_missing_evidence(self):
        result = diagnose(receipt(), [dict(plan='e'*64, label='separately checked')])
        self.assertTrue(result['search']['exhausted'])
        self.assertEqual([r['gate'] for r in result['candidates']],
                         ['selected_baseline','measurement_failed','measurement_budget','measurement_evidence'])
        self.assertEqual(result['expected_opportunities'][0]['gate'], 'not_reached')
        self.assertIn('equivalent proof chains', result['expected_opportunities'][0]['reason'])

    def test_uncertainty_exclusion_overrides_coarse_cost_ranking(self):
        value = receipt()
        value['selection']['uncertainty_guard'] = dict(excluded={'d': 'paired uncertainty or minimum lifecycle gain guard'})
        self.assertEqual(diagnose(value)['candidates'][3]['gate'], 'profitability')

    def test_capability_decline_is_not_missing_measurement(self):
        value = receipt()
        value['candidates'][2]['capability'] = dict(emittable=False, reason='GPU instruction unsupported')
        self.assertEqual(diagnose(value)['candidates'][2]['gate'], 'backend_capability')

    def test_selected_plan_remains_distinct_from_baseline(self):
        value = receipt(); value['selected'] = 'd'*64
        value['selection'] = dict(reason='paired improvement', total_ns={'a':'10','d':'6'})
        result = diagnose(value)
        self.assertFalse(result['delivery']['baseline_retained'])
        self.assertEqual(result['candidates'][0]['gate'], 'measured_not_selected')
        self.assertEqual(result['candidates'][3]['gate'], 'selected_alternative')

    def test_legacy_and_missing_receipt_fields_are_unknown_not_exhaustive(self):
        result = diagnose({})
        self.assertIsNone(result['delivery']['baseline_retained'])
        self.assertEqual(result['search']['stop_reason'], 'unknown')
        self.assertFalse(result['search']['exhausted'])

    def test_recipe_summaries_preserve_partial_and_declined_reasons(self):
        value = receipt();value['recipes'] = [dict(recipe='laws', status='partial', reason='deadline', counts={'proposal':3}),
                                             dict(recipe='layouts', status='declined', reason='missing model')]
        result = diagnose(value)
        self.assertEqual(result['search']['recipes']['laws']['results']['proposal'],3)
        self.assertEqual(result['search']['recipes']['layouts']['reasons'], {'missing model':1})

    def test_memory_constraints_and_observation_only_receipts(self):
        value = receipt();value.pop('measurement_acquisition')
        value['selection'] = dict(excluded={'b':'unmeasured memory'}, observations={'c':['sample']})
        result=diagnose(value)
        self.assertEqual(result['candidates'][1]['gate'],'memory_evidence')
        self.assertTrue(result['candidates'][2]['measured'])


if __name__ == '__main__':
    unittest.main()
