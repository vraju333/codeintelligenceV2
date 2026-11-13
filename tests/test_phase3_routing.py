from services.agent.phase3_dataflow_service import intent

QUERIES = [
    'Trace how accountType flows from CustomerController.create() through CustomerXmlRequest and CustomerJpaMapper. Include source lines.',
    'Trace countryCode from the incoming XML request through normalization, validation, entity mapping, and repository.save().',
    'Analyze CustomerService.updateAccountType(). Show how accountType is modified, validated, mapped, and persisted.',
    'Identify all methods that read or modify Customer.accountType. Distinguish getters and setters.',
    'Trace customerId across CustomerController.replace(), CustomerService.replace(), XML mapping, and repository operations.',
]

def test_phase3_queries_are_routed():
    assert all(intent(q) for q in QUERIES)

def test_mapping_inventory_not_hijacked():
    assert not intent('List mapping documents associated with customerId')
