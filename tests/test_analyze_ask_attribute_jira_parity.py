"""Regression tests for Analyze Current Change attribute/JIRA evidence parity."""
import ast
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'services' / 'engineering_assistant' / 'engineering_assistant_service.py'


def load_attribute_filter():
    """Exercise the real method without importing optional backend dependencies."""
    module = ast.parse(SOURCE.read_text(encoding='utf-8'))
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'EngineeringAssistantService')
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_meaningful_change_attributes')
    fn.decorator_list = []
    isolated = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    namespace = {'re': __import__('re'), 'Any': __import__('typing').Any}
    exec(compile(isolated, str(SOURCE), 'exec'), namespace)
    return namespace['_meaningful_change_attributes']


class AnalyzeAskParityTests(unittest.TestCase):
    def test_filters_jdk_methods_and_keeps_business_fields(self):
        filter_fn = load_attribute_filter()
        result = {'change_summary': {
            'changed_attributes': ['all', 'argument', 'blank', 'compile', 'contains', 'create', 'COUNTRY', 'countryCode', 'accountType'],
            'changed_symbols': [{'symbol_type': 'ATTRIBUTE', 'symbol': 'countryCode'}],
        }}
        self.assertEqual(filter_fn(result), ['countryCode', 'accountType'])

    def test_getter_symbol_normalization(self):
        filter_fn = load_attribute_filter()
        result = {'change_summary': {
            'changed_attributes': ['countryCode'],
            'changed_symbols': [{'symbol_type': 'ATTRIBUTE', 'symbol': 'getCountryCode'}],
        }}
        self.assertEqual(filter_fn(result), ['countryCode'])

    def test_no_arbitrary_jira_or_attributes(self):
        filter_fn = load_attribute_filter()
        result = {'change_summary': {'changed_attributes': ['compile', 'all', 'contains'], 'changed_symbols': []}}
        self.assertEqual(filter_fn(result), [])


if __name__ == '__main__':
    unittest.main()
