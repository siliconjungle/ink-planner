"""Work-model selection is gated on validation and never beats real timings."""
import math
import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ink_planner.work_model import acquire, call_weights, predict, select, validate_model


def model(**overrides):
    value = dict(schema=1, semantics='ink-work-model-v1', slope=0.8, sigma=0.1, z=2.0, min_gain=0.05,
                 validation=dict(status='validated'))
    value.update(overrides)
    return value


def work(*rows):
    return dict(rows=[dict(measurement=m, weighted=str(f), steps=steps) for m, f, steps in rows])


class Validation(unittest.TestCase):
    def test_unvalidated_or_malformed_models_are_refused(self):
        validate_model(model())
        for bad in [model(validation=dict(status='rejected')), model(validation=None), model(slope=0),
                    model(slope=float('nan')), model(sigma=-1), model(schema=2), model(semantics='other')]:
            with self.assertRaises(ValueError):
                validate_model(bad)


class Prediction(unittest.TestCase):
    def test_prediction_follows_the_fitted_slope_and_spread(self):
        speedup, lower = predict(model(), 1000, 250)
        self.assertAlmostEqual(speedup, 4 ** 0.8)
        self.assertAlmostEqual(lower, math.exp(0.8 * math.log(4) - 0.2))
        self.assertEqual(predict(model(), 1000, 1000), (1.0, math.exp(-0.2)))
        self.assertEqual(predict(model(), 0, 10), (1.0, 1.0))


class Acquisition(unittest.TestCase):
    def acquire_calls(self, repetitions, profile=None):
        spec = dict(schema=1, phases={
            'call:a': [dict(function='a', args=[])],
            'call:b': [dict(function='b', args=[n]) for n in [0, 1, 2] * repetitions]},
            profile=profile or {'call:a': 1, 'call:b': 1}, workload={})
        counts = {'base': [100] + [9, 10, 11] * repetitions,
                  'candidate': [10] + [19, 20, 21] * repetitions}
        packages = [dict(steps=[]), dict(steps=[dict(kind='rewrite')])]
        states = [dict(source='base'), dict(source='candidate')]
        measurements = ['base', 'candidate']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'workload.json';path.write_text(json.dumps(spec))
            def invoke(command, original, staged, request, *args):
                name = staged.name.split('.')[0]
                calls = json.loads(request.read_text())
                self.assertEqual(len(calls), len(counts[name]))
                return dict(semantics='ink-reference-work-v1', fuel=counts[name],
                            selected=name, values=[0] * len(calls))
            planner = SimpleNamespace(directory=root, original='source', invoke=invoke)
            with patch('ink_planner.work_model.target_iterations', return_value=(counts, [])):
                return acquire(planner, packages, states, measurements, path)

    def test_repeating_one_phases_samples_preserves_work_and_selection(self):
        reports = [self.acquire_calls(repetitions) for repetitions in [1, 10]]
        for report in reports:
            self.assertEqual([r['weighted'] for r in report['rows']], ['110', '30'])
            self.assertEqual([r['weighted_iterations'] for r in report['rows']], ['110', '30'])
            for feature in ['reference_fuel', 'target_iterations']:
                chosen, _ = select(model(feature=feature), 'digest', report,
                                   ['base', 'candidate'], ['base', 'candidate'], 'base')
                self.assertEqual(chosen, 'candidate')

    def test_phase_frequencies_still_change_the_objective(self):
        report = self.acquire_calls(1, {'call:a': 1, 'call:b': 10})
        self.assertEqual([r['weighted'] for r in report['rows']], ['200', '210'])
        chosen, _ = select(model(), 'digest', report, ['base', 'candidate'], ['base', 'candidate'], 'base')
        self.assertEqual(chosen, 'base')

    def test_fractional_means_are_exact_and_selectable(self):
        spec = dict(phases={'call:a': [0, 1, 2], 'call:b': [0, 1]},
                    profile={'call:a': 2, 'call:b': 3})
        weights = call_weights(spec)
        total = sum(c * w for c, w in zip([1, 2, 2, 1, 2], weights))
        self.assertEqual(total, Fraction(47, 6))
        w = work(('base', 20, []), ('candidate', total, ['rewrite']))
        chosen, _ = select(model(), 'digest', w, ['base', 'candidate'], ['base', 'candidate'], 'base')
        self.assertEqual(chosen, 'candidate')


class Selection(unittest.TestCase):
    measurements = ['base', 'props', 'rewrite', 'rewrite+props', 'worse']
    def test_picks_the_largest_predicted_gain_with_the_simplest_plan(self):
        w = work(('base', 1000, []), ('props', 1000, ['properties']), ('rewrite', 200, ['rewrite']),
                 ('rewrite+props', 200, ['rewrite', 'properties']), ('worse', 3000, ['rewrite']))
        chosen, selection = select(model(), 'digest', w, self.measurements, self.measurements, 'base')
        self.assertEqual(chosen, 'rewrite')
        self.assertEqual(selection['reason'], 'calibrated reference-work model')
        self.assertEqual(selection['model_sha256'], 'digest')

    def test_keeps_the_baseline_when_no_bound_clears_the_margin(self):
        w = work(('base', 1000, []), ('rewrite', 950, ['rewrite']), ('worse', 3000, ['rewrite']))
        chosen, selection = select(model(), 'digest', w, ['base', 'rewrite', 'worse'], ['base', 'rewrite', 'worse'], 'base')
        self.assertEqual(chosen, 'base')
        self.assertIn('baseline retained', selection['reason'])

    def test_ineligible_candidates_are_never_chosen(self):
        w = work(('base', 1000, []), ('rewrite', 100, ['rewrite']))
        chosen, _ = select(model(), 'digest', w, ['base', 'rewrite'], ['base'], 'base')
        self.assertEqual(chosen, 'base')

    def test_memory_and_migration_requirements_keep_the_baseline(self):
        w = work(('base', 1000, []), ('rewrite', 1, ['rewrite']))
        for constraints in [dict(memory_limit=0), dict(memory_limit=1_000_000),
                            dict(migration_count=1), dict(memory_limit=1024, migration_count=2)]:
            with self.subTest(constraints=constraints):
                chosen, receipt = select(model(), 'digest', w, ['base', 'rewrite'],
                                         ['base', 'rewrite'], 'base', **constraints)
                self.assertEqual(chosen, 'base')
                self.assertIn('lacks memory or migration lifecycle evidence', receipt['reason'])
                self.assertEqual(receipt['model_sha256'], 'digest')


if __name__ == '__main__':
    unittest.main()
