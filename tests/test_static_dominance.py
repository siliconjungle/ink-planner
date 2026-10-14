import unittest
from ink_planner.static_dominance import choose, dominant


def plan(*kinds, views=1):
    return dict(schema=1, steps=[dict(kind=k, package=dict(views=[{}] * views)) for k in kinds])


class StaticDominance(unittest.TestCase):
    def test_only_pure_maintained_plans_are_dominant(self):
        self.assertTrue(dominant(plan('maintained')))
        self.assertFalse(dominant(plan()))
        self.assertFalse(dominant(plan('maintained', 'protocol')))
        self.assertFalse(dominant(plan('representation')))

    def test_most_maintained_keeps_wins_and_unemittable_plans_are_skipped(self):
        packages = [plan(), plan('protocol'), plan('maintained', views=1), plan('maintained', views=2), plan('maintained', views=3)]
        states = [{}, {}, {}, {}, {'capability': {'emittable': False}}]
        self.assertEqual(choose(packages, states), 3)
        self.assertIsNone(choose(packages[:2], states[:2]))


if __name__ == '__main__':
    unittest.main()

class ReplacementCoverage(unittest.TestCase):
    def test_repeated_packages_do_not_outvote_more_maintained_keeps(self):
        repeated=plan('maintained','maintained','maintained',views=1)
        both=plan('maintained',views=2)
        self.assertEqual(choose([repeated,both],[{},{}]),1)
