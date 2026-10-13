"""Work-model selection is gated on validation and never beats real timings."""
import math
import unittest

from ink_planner.work_model import predict, select, validate_model


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


if __name__ == '__main__':
    unittest.main()
