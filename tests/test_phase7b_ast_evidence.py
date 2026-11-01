from pathlib import Path
from tempfile import TemporaryDirectory
from services.agent.java_ast_evidence import inspect_java_ast


def test_jdk_ast_resolves_method_and_attribute_condition():
    with TemporaryDirectory() as d:
        root = Path(d)
        folder = root / 'src/main/java/demo'
        folder.mkdir(parents=True)
        (folder / 'Customer.java').write_text('''package demo;
class Customer { String countryCode; String getCountryCode() { return countryCode; } }
class Service { void validate(Customer c) { if (c.getCountryCode() == null) { throw new IllegalArgumentException(); } } }
''')
        output = inspect_java_ast(root, 'countryCode')
        assert output['status'] == 'AST_INSPECTED', output['warnings']
        assert any(x['target'].endswith('Customer.getCountryCode') and x['status'] == 'SYMBOL_RESOLVED' for x in output['calls'])
        assert any(x['status'] == 'AST_CONDITION_NOT_CFG_VERIFIED' for x in output['conditions'])


def test_missing_java_source_does_not_claim_resolution():
    with TemporaryDirectory() as d:
        output = inspect_java_ast(Path(d), 'countryCode')
        assert output['status'] == 'UNAVAILABLE'
        assert not output['calls']
