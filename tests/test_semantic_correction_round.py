import sys
import types
from services.agent.semantic_response_fix import _concise_graph, _jpa_customer_id_evidence, _mapping_answer


def test_nested_call_order(tmp_path):
    root = tmp_path / 'src/main/java/x'
    root.mkdir(parents=True)
    (root / 'CustomerService.java').write_text('''class CustomerService {
 public Customer replace(String id, CustomerXmlRequest request) {
  repository.existsById(id);
  xmlMapper.toModel(request);
  validate(customer);
  return jpaMapper.toModel(
    repository.save(jpaMapper.toEntity(customer)));
 }
}''')
    result = _concise_graph('concise call graph CustomerService.replace', tmp_path)
    calls = [x['expression'] for x in result['call_edges']]
    assert calls.index('jpaMapper.toEntity') < calls.index('repository.save') < calls.index('jpaMapper.toModel')


def test_explicit_jpa_column(tmp_path):
    root = tmp_path / 'src/main/java/x'
    root.mkdir(parents=True)
    (root / 'CustomerJpaMapper.java').write_text('''class CustomerJpaMapper {
 public CustomerAccountEntity toEntity(Customer source) {
 target.setCustomerId(source.getCustomerId());
 }
}''')
    (root / 'CustomerAccountEntity.java').write_text('''class CustomerAccountEntity {
 @Id
 @Column(name="CUSTOMER_ID")
 private String customerId;
}''')
    evidence = '\n'.join(_jpa_customer_id_evidence(tmp_path))
    assert 'Explicit JPA physical column: CUSTOMER_ID' in evidence
    (root / 'CustomerAccountEntity.java').write_text('class CustomerAccountEntity {\n private String customerId;\n}')
    assert 'Physical database column NOT verified' in '\n'.join(_jpa_customer_id_evidence(tmp_path))


def test_compare_xml_versions_without_json_false_difference(monkeypatch):
    mod = types.ModuleType('services.agent.ask_mapping_documents_service')
    def row(v, kind, path):
        return {'source_type':kind, 'source_path':path, 'target_class':'Customer',
                'target_attribute':'customerId', 'mapping_rule':'Trim; must not be blank' if kind == 'XML' else 'Direct',
                'provenance':{'document_version':f'V{v}', 'document':f'{kind}_V{v}.xlsx',
                              'sheet':'Mapping','row':2,'source_ref':f'{kind}_V{v}.xlsx'}}
    rows = [row(v,'XML','/CustomerAccount/CustomerId') for v in (1,2,3,4)] + [row(v,'JSON','customer.customerId') for v in (1,2)]
    mod.mapping_documents_answer = lambda *a, **kw: {'mapping_rows':rows}
    monkeypatch.setitem(sys.modules, mod.__name__, mod)
    result = _mapping_answer(None, 'Compare CustomerId mapping across V1 V2 V3 V4','versions',None)
    assert 'V2 -> V3: no observed change' in result['answer']
    assert 'V2 -> V3: observed definition differs' not in result['answer']
