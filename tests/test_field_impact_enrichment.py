from services.field_impact.analyzer import enrich_field_impact
from services.field_impact.ui_schema_builder import build_field_impact_view

def test_classification_and_same_expression_flow():
    detail='target.setFirstName(trim(source.getFirstName()))'
    data={'field':'firstName','writers':[{'file':'src/main/java/CustomerXmlMapper.java','line':14,'method':'toModel','kind':'WRITE','detail':detail},
         {'file':'src/test/java/CustomerTest.java','line':12,'method':'test','kind':'WRITE','detail':'c.setFirstName("x")'}],
         'readers':[{'file':'src/main/java/CustomerXmlMapper.java','line':14,'method':'toModel','kind':'READ','detail':'source.getFirstName()'}],
         'declarations':[],'limitations':[]}
    result=enrich_field_impact(data)
    assert result['production_writers']==1 and result['test_writers']==1
    assert result['same_expression_flows'][0]['transformations']==['trim']
    assert result['same_expression_flows'][0]['verified_cross_method'] is False
    assert build_field_impact_view(result)['components'][0]['type']=='MetricRow'
