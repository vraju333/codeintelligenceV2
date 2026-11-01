from pathlib import Path
from tempfile import TemporaryDirectory
from services.agent.java_field_flow_evidence import inspect_method_field


def test_field_get_set_condition():
    with TemporaryDirectory() as d:
        root = Path(d)
        file = root / 'Sample.java'
        file.write_text('class Sample {}')
        method = {'class': 'Sample', 'name': 'update', 'file': file, 'line': 10,
                  'body': '{\n  if (customer.getCountryCode() == null) {\n'
                          '    customer.setCountryCode("US");\n  }\n}'}
        found = inspect_method_field(method, 'countryCode', root)
        kinds = {f['kind'] for f in found}
        assert 'FIELD_GETTER_CALL' in kinds
        assert 'FIELD_SETTER_CALL' in kinds
        assert 'FIELD_CONDITION_CANDIDATE' in kinds
        assert all('VERIFIED' not in f['status'] or 'NOT_' in f['status'] for f in found)


def test_unrelated_field_not_misclassified():
    with TemporaryDirectory() as d:
        root = Path(d)
        file = root / 'Sample.java'
        file.write_text('class Sample {}')
        method = {'class': 'Sample', 'name': 'updateAccountType', 'file': file, 'line': 1,
                  'body': '{ if (customer.getAccountType() == null) customer.setAccountType("A"); }'}
        assert inspect_method_field(method, 'countryCode', root) == []
