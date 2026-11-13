from pathlib import Path
from services.agent.semantic_response_fix import _intent, _concise_graph, _java_mapper_answer

def test_intents():
    assert _intent('Explain how CustomerXmlMapper and CustomerJpaMapper transform customer data') == 'java_mappers'
    assert _intent('Compare the CustomerId mapping across V1, V2, V3 and V4') == 'versions'
    assert _intent('Which customerId mapping version is the latest?') == 'latest'
    assert _intent('Show a concise call graph for CustomerService.replace()') == 'concise_graph'

def test_source_scoped(tmp_path):
    root=tmp_path/'src/main/java/x';root.mkdir(parents=True)
    (root/'CustomerService.java').write_text('class CustomerService {\n public Customer replace(String id, CustomerXmlRequest request) {\n repository.existsById(id);\n xmlMapper.toModel(request);\n validate(customer);\n repository.save(entity);\n return customer;\n }\n}\n')
    (root/'CustomerXmlMapper.java').write_text('class CustomerXmlMapper {\n public Customer toModel(CustomerXmlRequest source) {\n target.setEmail(lower(source.getEmail()));\n }\n}\n')
    graph=_concise_graph('concise call graph CustomerService.replace',tmp_path)
    assert 'repository.save' in graph['answer']
    assert 'Getter/setter' in graph['answer']
    mapper=_java_mapper_answer('Explain CustomerXmlMapper',tmp_path)
    assert 'target.setEmail' in mapper['answer']
