import ast,re,types
from pathlib import Path
source=Path('/mnt/data/ci_latest_router.py').read_text()
mod=ast.parse(source)
names={'canonical','mentioned','infer_country_attribute','requirement_clauses','scoped_country_policy','detect_value_rule_conflicts','interpret_requirement_intent'}
functions=[node for node in mod.body if isinstance(node,ast.FunctionDef) and node.name in names]
ns={'re':re,'MappingDefinition':object}
exec(compile(ast.Module(body=functions,type_ignores=[]),'test','exec'),ns)
class Mapping:
    def __init__(self,a):self.target_attribute=a
maps=[Mapping('countryCode'),Mapping('email'),Mapping('accountType')]
def check(prompt,expected):
    attrs=ns['infer_country_attribute'](prompt,maps,ns['mentioned'] and [a for a in ['countryCode','email','accountType'] if ns['mentioned'](prompt,a)])
    # Real extractor also includes new camelCase names.
    attrs+= [a for a in ['customerTier'] if ns['mentioned'](prompt,a)]
    result=ns['interpret_requirement_intent'](prompt,attrs,maps)
    for key,val in expected.items():assert (set(result[key])==set(val) if isinstance(val,list) else result[key]==val),(key,result[key],val)
    return result
p='Accept only IN, US and GB for new customers. For existing customers during update, also accept CA and FR as legacy values. Explicitly report that create and update policies differ.'
r=check(p,{'modified_attributes':['countryCode'],'protected_attributes':[],'clarification_required':False})
assert ns['scoped_country_policy'](p,'countryCode')['update_additionally_allowed']==['CA','FR']
p='Enhance customer management by adding a new optional attribute customerTier with allowed values STANDARD, GOLD and PLATINUM. Accept customerTier in customer creation and update requests, persist it in the database, and return it in customer retrieval responses. Existing customers without customerTier must continue to work. Also update countryCode validation to accept only IN, US and GB, reject CA and FR, and preserve lowercase-to-uppercase normalization. Ensure that customerTier is correctly mapped across request DTO, service, mapper, entity, database and response DTO layers. Do not modify email validation, accountType mapping, or customer deletion behavior.'
r=check(p,{'new_attributes':['customerTier'],'modified_attributes':['countryCode'],'protected_attributes':['accountType','email'],'clarification_required':False})
p='For countryCode, accept CA and FR but reject CA and FR.'
r=check(p,{'clarification_required':True})
p='Change only countryCode validation in customer creation and update requests. Preserve email validation, accountType mapping and customer deletion behavior.'
r=check(p,{'modified_attributes':['countryCode'],'protected_attributes':['accountType','email']})
print('PASS: implicit country field, scoped policy, mixed new/modified/protected, contradiction, protected regression')
