from services.agent.java_attribute_flow_service import intent, _source_lines

QUERIES = [
    'Trace how accountType flows from CustomerController.create() through CustomerXmlRequest and CustomerJpaMapper. Include source lines.',
    'Trace countryCode from the incoming XML request through normalization, validation, entity mapping, and repository.save().',
    'Analyze CustomerService.updateAccountType(). Show how accountType is modified, validated, mapped, and persisted.',
    'Identify all methods that read or modify Customer.accountType. Distinguish getters and setters.',
    'Trace customerId across CustomerController.replace(), CustomerService.replace(), XML mapping, and repository operations.',
]

def test_routing():
    assert all(intent(q) for q in QUERIES)
    assert not intent('List mapping documents associated with customerId')

def test_source_observation():
    node={'source':'target.setAccountType(upper(source.getAccountType()));','line':19,
          'cls':'CustomerXmlMapper','name':'toModel','file':'CustomerXmlMapper.java'}
    o=_source_lines(node,'accountType')
    assert len(o)==1
    assert 'SAME_STATEMENT_GETTER_TO_SETTER_CANDIDATE' in o[0]['kinds']
