"""Check shape filtering against exhaustive search, independently of proof trust."""
import random
import sys
import unittest
from pathlib import Path

# Immutable bounded objects keep accumulated archived research irrelevant.
ROOT=Path(__file__).resolve().parent/'fixtures/research-query'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research'))
from database_query import Database, scalar_endpoint
from rewrite_search import search, Unsupported


class Exhaustive:
    def __init__(self, rules):
        self.rules = rules

    def candidates(self, expression):
        return self.rules


class QueryTests(unittest.TestCase):
    def test_discrimination_preserves_exhaustive_search_results_and_proofs(self):
        database = Database(ROOT)
        self.assertEqual(len(database.query.rules), 8)
        rng = random.Random(0x1A4B)
        env = {'x': ('U64', {'Var': 'p0'}), 'y': ('U64', {'Var': 'p1'}),
               'a': ('Bool', {'Var': 'p2'}), 'b': ('Bool', {'Var': 'p3'})}

        def number(depth):
            if depth == 0 or rng.randrange(3) == 0:
                return rng.choice([{'Var': 'x'}, {'Var': 'y'}, {'Num': 0}, {'Num': 1}, {'Num': 2**64 - 1}])
            return {'Binary': [rng.choice(['+', '-', '*']), number(depth - 1), number(depth - 1)]}

        def boolean(depth):
            if depth == 0 or rng.randrange(4) == 0:
                return rng.choice([{'Var': 'a'}, {'Var': 'b'}, {'Bool': True}, {'Bool': False}])
            if rng.randrange(3) == 0:
                return {'Binary': [rng.choice(['==', '!=', '<', '<=', '>', '>=']), number(depth - 1), number(depth - 1)]}
            return {'Binary': [rng.choice(['&&', '||', '==', '!=']), boolean(depth - 1), boolean(depth - 1)]}

        for i in range(256):
            expression = number(3) if i % 2 else boolean(3)
            outcomes = []
            for rules in [database.query, Exhaustive(database.query.rules)]:
                used = []
                try:
                    result, proof = search(expression, env, rules, [100000], used)
                    outcomes.append((result, proof, used))
                except Unsupported as e:
                    outcomes.append(str(e))
            self.assertEqual(outcomes[0], outcomes[1], expression)

    def test_substitution_charges_the_expanded_argument_at_every_occurrence(self):
        v = {'Var': 'x'}
        repeated = v
        for _ in range(31):
            repeated = {'Binary': {'op': '+', 'left': repeated, 'right': v}}
        argument = {'U64': 1}
        for _ in range(15):
            argument = {'Binary': {'op': '+', 'left': argument, 'right': {'U64': 1}}}
        objects = {'f': {'declaration': {'Function': {
            'params': [['x', 'U64']], 'recursive': None, 'body': repeated}}}}
        term = {'Call': {'function': 'f', 'arguments': [argument]}}
        with self.assertRaisesRegex(ValueError, 'expansion limit'):
            scalar_endpoint(term, objects, [128])
        expanded = scalar_endpoint(term, objects, [5000])
        self.assertEqual(expanded['Binary']['op'], '+')

    def test_acyclic_flag_does_not_allow_circular_definition_expansion(self):
        term = {'Call': {'function': 'f', 'arguments': []}}
        objects = {'f': {'declaration': {'Function': {
            'params': [], 'recursive': None, 'body': term}}}}
        with self.assertRaisesRegex(ValueError, 'non-acyclic'):
            scalar_endpoint(term, objects, [10000])


if __name__ == '__main__':
    unittest.main()
