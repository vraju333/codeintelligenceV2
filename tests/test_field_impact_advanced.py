from services.field_impact.advanced_analysis import advanced_evidence
from services.field_impact.what_if import simulate_requirement

def test_branch_and_call_candidate(tmp_path):
    (tmp_path/'Demo.java').write_text('class Demo { public void alpha() { if (true) { beta(); } } public void beta() { } }')
    result={'project_path':str(tmp_path),'field':'firstName','writers':[{'file':'Demo.java','method':'alpha','kind':'WRITE','line':1}],
            'readers':[],'declarations':[],'same_expression_flows':[]}
    out=advanced_evidence(result)
    if out['advanced_warnings']:
        import pytest
        pytest.skip('tree-sitter-java not installed in test environment')
    assert out['control_flow']['branch_sites']
    assert out['data_flow']['candidate_cross_method_calls']
    assert not out['data_flow']['verified_cross_method']

def test_what_if_does_not_claim_fullname_exists():
    data=[{'field':f,'writers':[],'readers':[],'declarations':[]} for f in ['firstName','lastName','fullName']]
    out=simulate_requirement('CAS-6: fullName from firstName and lastName',data)
    assert out['proposed_changes'][0]['implemented_in_source'] is False

def test_what_if_excludes_unobserved_field_from_existing_evidence():
    data = [
        {'field': 'firstName', 'writers': [{'file': 'Customer.java'}], 'readers': [], 'declarations': []},
        {'field': 'lastName', 'writers': [], 'readers': [], 'declarations': [{'file': 'Customer.java'}]},
        {'field': 'fullName', 'writers': [], 'readers': [], 'declarations': []},
    ]
    out = simulate_requirement('CAS-6: Generate fullName from firstName and lastName', data)
    assert [x['field'] for x in out['existing_code_evidence']] == ['firstName', 'lastName']
    assert out['proposed_changes'][0]['implemented_in_source'] is False
    assert out['proposed_changes'][0]['evidence_level'] == 'REQUIREMENT_INFERENCE'


def test_what_if_keeps_real_fullname_syntax_when_present():
    data = [
        {'field': 'firstName', 'writers': [], 'readers': [], 'declarations': []},
        {'field': 'lastName', 'writers': [], 'readers': [], 'declarations': []},
        {'field': 'fullName', 'writers': [], 'readers': [], 'declarations': [{'file': 'Customer.java'}]},
    ]
    out = simulate_requirement('fullName from firstName and lastName', data)
    assert [x['field'] for x in out['existing_code_evidence']] == ['fullName']
    assert out['proposed_changes'][0]['implemented_in_source'] is True
