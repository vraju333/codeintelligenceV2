"""Focused extraction regressions; no database or FastAPI runtime required."""
import ast
import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'routers' / 'requirement_impact_router.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))

def load_function(name):
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    code = compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec')
    namespace = {'re': re, 'canonical': lambda s: re.sub(r'[^a-z0-9]', '', s.lower()),
                 'mentioned': lambda text, attr: attr.lower() in text.lower()}
    exec(code, namespace)
    return namespace[name]

reject = load_function('explicit_rejected_values')
allowed = load_function('explicit_allowed_values')

REQ = ('Add optional customerTier with STANDARD, GOLD and PLATINUM values for customer '
       'creation and update. Persist it and return it in retrieval responses. '
       'Also change countryCode validation to accept only IN, US and GB and reject CA and FR. '
       'Do not modify email validation, accountType mapping or customer deletion behavior.')

def test_explicit_reject_values_same_clause():
    assert reject(REQ, 'countryCode') == ['CA', 'FR']

def test_no_spurious_reject_verb_as_code():
    assert reject('Update countryCode to accept only IN, US and GB, reject CA and FR', 'countryCode') == ['CA', 'FR']

def test_allowed_values_unchanged():
    assert allowed(REQ, 'countryCode') == ['IN', 'US', 'GB']

def test_no_invented_rejected_values():
    assert reject('Update countryCode to accept only IN, US and GB', 'countryCode') == []

def test_new_attribute_diagnostics_and_operation_cases_wired():
    source = SOURCE.read_text(encoding='utf-8')
    assert "'mapping_source_gaps': mapping_source_gaps" in source
    assert "'NEW_ATTRIBUTE_IMPLEMENTATION_GAP_REVIEW_REQUIRED'" in source
    assert "'OPERATION_VALUE_VALIDATION'" in source
