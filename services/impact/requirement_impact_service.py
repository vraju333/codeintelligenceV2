"""Read-only, evidence-scoped requirement/JIRA/attribute impact composition."""
from __future__ import annotations
import re
from pathlib import Path
from db_models import JiraKnowledge, MappingDefinition, MappingDocument, Scenario
from services.jira.attribute_jira_lookup_service import lookup
from services.lineage.mapping_intelligence_service import MappingIntelligenceService

KEY = re.compile(r'^[A-Z][A-Z0-9]+-\d+$')
CAMEL = re.compile(r'\b[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b')

def norm(v): return re.sub(r'[^a-z0-9]', '', str(v or '').lower())
def scope(v): return str(v or '').replace('\\', '/').rstrip('/').casefold()
def words(v): return re.findall(r'[a-z0-9]+', re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', str(v or '')).lower())
def mentions(text, attribute):
    parts = words(attribute)
    if not parts: return False
    pat = r'(?<![A-Za-z0-9])' + r'[_\s.\-/]*'.join(map(re.escape,parts)) + r'(?![A-Za-z0-9])'
    return bool(re.search(pat, text or '', re.I))

def analyze(db, mode, value, project=None):
    mode = (mode or '').lower().strip()
    value = (value or '').strip()
    if mode not in ('jira','requirement','attribute') or not value:
        raise ValueError('mode must be jira, requirement or attribute; value is required')
    if len(value)>12000: raise ValueError('Input is too long')
    project_key=scope(project)
    jira=None; source='USER_INPUT'; requirement=value
    if mode=='jira':
        key=value.upper()
        if not KEY.fullmatch(key): raise ValueError('Invalid JIRA key')
        jira=db.query(JiraKnowledge).filter(JiraKnowledge.jira_id==key).first()
        if jira is None:
            return {'status':'JIRA_NOT_INDEXED','mode':mode,'input':value,'project':project,
                    'note':'This issue is not in indexed JiraKnowledge. No requirement or historical relationships were inferred.',
                    'attributes':[],'historical_jiras':[],'mappings':[],'scenarios':[],'unavailable':['LIVE_JIRA_NOT_QUERIED']}
        if project_key and scope(jira.project_path)!=project_key:
            return {'status':'PROJECT_SCOPE_MISMATCH','mode':mode,'input':value,'project':project,
                    'note':'Indexed JIRA belongs to a different project scope. No cross-project evidence was merged.',
                    'attributes':[],'historical_jiras':[],'mappings':[],'scenarios':[]}
        requirement=' '.join([jira.title or '',jira.requirement or '']); source='INDEXED_JIRA_REQUIREMENT'
    if mode=='attribute':
        attributes=[value.split('.')[-1]]
    else:
        # Use indexed mapping target attributes as a project-specific vocabulary,
        # plus explicit camelCase attributes in the requirement. No LLM guesses.
        documents=db.query(MappingDocument).filter(MappingDocument.status=='ACTIVE').all()
        ids=[d.id for d in documents if not project_key or scope(d.project_path)==project_key]
        known=[]
        if ids:
            definitions=db.query(MappingDefinition).filter(MappingDefinition.document_id.in_(ids), MappingDefinition.status=='ACTIVE').all()
            known=[d.target_attribute for d in definitions if d.target_attribute]
        attributes=[]; seen=set()
        for candidate in list(CAMEL.findall(requirement))+known:
            if norm(candidate) in seen or not mentions(requirement,candidate): continue
            seen.add(norm(candidate)); attributes.append(candidate)
        attributes=attributes[:20]
    historical={}; mapping_rows=[]; mapping_docs={}; scenarios={}; warnings=[]
    for attr in attributes:
        found=lookup(db,attr,project_path=project)
        for row in found.get('matches',[]):
            if jira and row['jira_id']==jira.jira_id: continue
            record=historical.setdefault(row['jira_id'],{**row,'matched_attributes':[]})
            record['matched_attributes'].append(attr)
        try:
            lineage=MappingIntelligenceService().graph_lineage(attr,project,db=db)
            for row in lineage.get('mapping_rows',[]):
                identity=(row.get('id'),row.get('provenance',{}).get('document_id'))
                if not any((x.get('id'),x.get('provenance',{}).get('document_id'))==identity for x in mapping_rows):
                    mapping_rows.append(row)
            for d in lineage.get('mapping_documents',[]):mapping_docs[d.get('id',d.get('document_id'))]=d
        except Exception as exc:
            warnings.append(f'Mapping retrieval unavailable for {attr}: {type(exc).__name__}')
    # Scenario matches are textual evidence, NOT verified execution or dependency edges.
    for s in db.query(Scenario).all():
        if project_key and scope(s.project_path)!=project_key: continue
        searchable=' '.join(str(x or '') for x in [s.scenario_name,s.description,s.involved_classes,s.request_json,s.expected_db_effect])
        matches=[a for a in attributes if mentions(searchable,a)]
        if matches:
            scenarios[s.scenario_code]={'scenario_code':s.scenario_code,'name':s.scenario_name,'endpoint':s.endpoint,
                'http_method':s.http_method,'jira_id':s.jira_id,'matched_attributes':matches,
                'relationship':'SCENARIO_TEXT_MATCH','execution_verified':False}
    return {'status':'FOUND' if historical or mapping_rows or scenarios else 'NO_MATCH',
        'mode':mode,'input':value,'project':project,'requirement_source':source,
        'jira':{'jira_id':jira.jira_id,'summary':jira.title} if jira else None,
        'attributes':attributes,'historical_jiras':list(historical.values()),
        'mappings':mapping_rows,'mapping_documents':list(mapping_docs.values()),
        'scenarios':list(scenarios.values()),'warnings':warnings,
        'evidence_boundary':'Historical Jira matches are indexed requirement mentions, not verified implementation links. Scenario matches are textual, not verified test executions. Workbook mappings do not establish Neo4j edges.',
        'unavailable':['SOURCE_VERIFIED_CODE_IMPACT_NOT_COMPOSED','TEST_BASELINE_EVIDENCE_NOT_COMPOSED','RELEASE_EVIDENCE_NOT_COMPOSED']}
