"""Allowlisted declarative view data. The frontend controls component rendering."""
def build_field_impact_view(result: dict) -> dict:
    return {'schema_version':1,'view':'field_impact','components':[
        {'type':'MetricRow','props':{'metrics':[
            {'label':'Production writes','value':result.get('production_writers',0)},
            {'label':'Test writes','value':result.get('test_writers',0)},
            {'label':'Reads','value':len(result.get('readers',[]))},
            {'label':'Declarations','value':len(result.get('declarations',[]))}]}},
        {'type':'EvidenceTable','props':{'rows':result.get('writers',[])+result.get('readers',[])}},
        {'type':'FlowTable','props':{'rows':result.get('same_expression_flows',[])}},
    ],'limitations':result.get('limitations',[])}
