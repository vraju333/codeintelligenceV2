"""Evidence-only mapping-document ASK fast path; never falls back to Git."""
from __future__ import annotations
import re

MAPPING = re.compile(r'\b(mapping|mappings|mapped|lineage|source[ -]to[ -]target)\b', re.I)
DOCUMENT = re.compile(r'\b(document|documents|file|files|workbook|excel|spreadsheet|sheet|sheets|xls|xlsx)\b', re.I)
HISTORICAL = re.compile(r'\b(commit|git|historical code|code change|validation change|regex change)\b', re.I)
ALIASES = {
    'countryCode': (r'\bcountry\s*[_-]?\s*code\b', r'\bcountryCode\b'),
    'customerEmail': (r'\bcustomer\s*[_-]?\s*email\b', r'\bcustomerEmail\b'),
    'organizationCode': (r'\borganization\s*[_-]?\s*code\b', r'\borganizationCode\b'),
    'accountType': (r'\baccount\s*[_-]?\s*type\b', r'\baccountType\b'),
}

def mapping_document_intent(query: str) -> bool:
    """Mapping-only queries, including natural language mapping explanations."""
    q = query or ''
    if mapping_comparison_intent(q):
        return False
    if HISTORICAL.search(q):
        return False
    return bool(MAPPING.search(q) and (
        DOCUMENT.search(q)
        or re.search(r'\b(how|where|which|what|explain|show|find|describe|rule|transformation|xml|json|field|attribute)\b', q, re.I)
        or re.search(r'\b(map|mapped|mapping|lineage)\b', q, re.I)
    ))


def mapping_comparison_intent(query: str) -> bool:
    """Explicit cross-source comparison, not generic mapping retrieval."""
    q = query or ''
    has_mapping = bool(MAPPING.search(q) or re.search(r'\b(xml|workbook|excel)\b', q, re.I))
    has_code = bool(re.search(r'\b(java|code|validation|regex|implementation|service)\b', q, re.I))
    has_compare = bool(re.search(r'\b(compare|comparison|match|matches|consistent|consistency|mismatch|discrepancy|align|same|different|versus|vs)\b', q, re.I))
    return has_mapping and has_code and has_compare


def extract_attribute(query: str) -> str:
    for canonical, patterns in ALIASES.items():
        if any(re.search(p, query or '', re.I) for p in patterns):
            return canonical
    # Unknown attributes can still be searched without assuming a known schema.
    cleaned = re.sub(r'\b(please|show|find|give|me|the|all|for|related|to|of|mapping|mappings|mapped|map|how|is|does|the|documents?|files?|workbooks?|excel|sheets?|lineage|source|target)\b', ' ', query or '', flags=re.I)
    return ' '.join(cleaned.split()).strip(' ?,.')

def mapping_documents_answer(db, query: str) -> dict:
    from services.lineage.mapping_intelligence_service import MappingIntelligenceService
    service = MappingIntelligenceService()
    attribute = extract_attribute(query)
    if not attribute:
        return {'status':'NEEDS_ATTRIBUTE', 'source':'MAPPING_DOCUMENT_EVIDENCE', 'query':query,
                'answer':'Specify an attribute to search mapping documents (for example, countryCode).',
                'mapping_documents':[], 'mapping_rows':[]}
    variants = [attribute]
    if attribute == 'countryCode': variants += ['country_code', 'country code']
    if attribute == 'customerEmail': variants += ['customer_email', 'customer email']
    if attribute == 'organizationCode': variants += ['organization_code', 'organization code']
    if attribute == 'accountType': variants += ['account_type', 'account type']
    rows, seen, discovery = [], set(), {}
    for variant in variants:
        result = service.search(db, variant, 250)
        discovery = result.get('workbook_discovery') or discovery
        for row in result.get('matches') or []:
            provenance = row.get('provenance') or {}
            identity = row.get('id') or (provenance.get('document_id'), provenance.get('sheet'), provenance.get('row'))
            if identity not in seen:
                seen.add(identity)
                rows.append(row)
    documents = {}
    for row in rows:
        provenance = row.get('provenance') or {}
        doc_id = provenance.get('document_id') or row.get('document_id') or row.get('mapping_document_id')
        title = provenance.get('document') or row.get('document_title') or row.get('title') or row.get('filename')
        key = str(doc_id or title or provenance.get('source_ref') or 'unspecified')
        if key not in documents:
            documents[key] = {'document_id':doc_id, 'title':title or 'Mapping document (title unavailable)',
                              'source_ref':provenance.get('source_ref'), 'row_count':0}
        documents[key]['row_count'] += 1
    lines = [f'Mapping documents for {attribute}', f'Matching mapping rows: {len(rows)}',
             f'Matching documents: {len(documents)}']
    if rows:
        for doc in documents.values():
            lines.append(f"- {doc['title']} ({doc['row_count']} matching rows)" + (f" | source={doc['source_ref']}" if doc.get('source_ref') else ''))
        lines.append('Source-to-target mapping rows and provenance are available in the structured evidence below.')
    else:
        lines.append('No matching mapping rows were found in the current project scope. No historical Git result was substituted.')
    if discovery.get('status') in ('IMPORT_FAILED','PROJECT_NOT_ACCESSIBLE','NO_WORKBOOKS_FOUND'):
        lines.append('Mapping workbook discovery: ' + str(discovery['status']))
    return {'status':'FOUND' if rows else 'NOT_FOUND', 'source':'MAPPING_DOCUMENT_EVIDENCE',
            'query':query, 'attribute':attribute, 'answer':'\n'.join(lines),
            'mapping_documents':list(documents.values()), 'mapping_rows':rows,
            'mapping_count':len(rows), 'workbook_discovery':discovery,
            'note':'Results are imported mapping evidence, not inferred Git/code relationships.'}


def mapping_comparison_answer(db, query: str) -> dict:
    """Combine independently retrieved evidence; do not invent code validation rules."""
    mapping = mapping_documents_answer(db, query)
    attribute = mapping.get('attribute') or ''
    result = {
        'status': 'NOT_VERIFIED', 'source': 'MAPPING_CODE_COMPARISON',
        'query': query, 'attribute': attribute,
        'mapping_documents': mapping.get('mapping_documents', []),
        'mapping_rows': mapping.get('mapping_rows', []),
        'mapping_count': mapping.get('mapping_count', 0),
        'mapping_evidence': mapping,
        'code_evidence': None,
        'comparison_status': 'NOT_VERIFIED',
        'answer': '',
    }
    if not mapping.get('mapping_rows'):
        result['answer'] = 'No mapping evidence found for this attribute. Code-to-mapping consistency cannot be established.'
        return result
    try:
        from services.agent.ask_evidence_service import attribute_change_history
        code = attribute_change_history(db, query, attribute, include_qa=False)
    except (ImportError, AttributeError, ValueError, TypeError) as exc:
        result['answer'] = ('Mapping evidence found, but code validation evidence could not be retrieved. '
                            'Comparison not verified. ' + type(exc).__name__)
        return result
    result['code_evidence'] = code
    code_text = str(code.get('answer') or '') if isinstance(code, dict) else ''
    code_source = code.get('source') if isinstance(code, dict) else None
    # Do not assert equality/inequality based on prose, which is not a reliable
    # representation of executable constraints. Show both pieces of evidence.
    result['answer'] = (
        mapping['answer'] + '\n\nCODE EVIDENCE\n' +
        (code_text if code_text else 'No source-verified code evidence returned.') +
        '\n\nComparison status: NOT_VERIFIED. Review the source mapping rule and '
        'the source-verified Java rule before declaring a mismatch or match.'
    )
    result['code_source'] = code_source
    return result
