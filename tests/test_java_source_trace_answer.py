from pathlib import Path
from services.agent.java_source_trace_answer import answer


def test_trace_and_missing_customer(tmp_path):
    root = tmp_path / 'src' / 'main' / 'java'
    root.mkdir(parents=True)
    (root / 'CustomerService.java').write_text('''class CustomerService {
 public Customer replace(String customerId, CustomerXmlRequest request) {
  if (!repository.existsById(customerId))
   throw new CustomerNotFoundException(customerId);
  Customer customer = xmlMapper.toModel(request);
  return jpaMapper.toModel(repository.save(jpaMapper.toEntity(customer)));
 }
}''')
    (root / 'CustomerController.java').write_text('class CustomerController { Customer replace(String customerId, CustomerXmlRequest request) { return service.replace(customerId, request); } }')
    missing = answer('What happens when customerId does not exist during CustomerService.replace()?', str(tmp_path))
    assert 'throws CustomerNotFoundException' in missing['answer']
    trace = answer('Trace XML request to database persistence using exact Java source files.', str(tmp_path))
    assert 'CustomerService.java' in trace['answer']
    assert 'repository.save' in trace['answer']
    controller = answer('Trace the controller to service to repository persistence flow with exact Java source evidence.', str(tmp_path))
    assert 'CustomerController.java' in controller['answer']


def test_unrelated_query_not_intercepted(tmp_path):
    assert answer('Find JIRA issues linked to customerId', str(tmp_path)) is None
