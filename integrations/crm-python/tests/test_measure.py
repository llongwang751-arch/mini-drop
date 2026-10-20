import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('measure', Path(__file__).parents[1] / 'harness/measure.py')
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)


class OutputContractTests(unittest.TestCase):
    def test_json_equivalence_cannot_hide_python_type_regression(self):
        baseline = {'ramp': ['#123456'], 'enabled': True}
        candidate = {'ramp': ('#123456',), 'enabled': True}
        self.assertEqual(json.dumps(baseline), json.dumps(candidate))
        self.assertNotEqual(measure.typed(baseline), measure.typed(candidate))
        self.assertNotEqual(measure.typed(True), measure.typed(1))
        self.assertNotEqual(measure.typed(1), measure.typed(1.0))

    def test_mapping_order_is_not_an_output_change(self):
        self.assertEqual(measure.typed({'a': [1], 'b': None}), measure.typed({'b': None, 'a': [1]}))


if __name__ == '__main__':
    unittest.main()
