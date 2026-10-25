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
    'firstName': (r'\bfirst\s*[_-]?\s*name\b', r'\bfirstName\b'),
    'lastName': (r'\blast\s*[_-]?\s*name\b', r'\blastName\b'),
    'primaryEmail': (r'\bprimary\s*[_-]?\s*email\b', r'\bprimaryEmail\b'),
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
    """Extract the field, not the surrounding natural-language request."""
    q = query or ''
    # HTTP paths are context, not the requested field. Strip them before
    # selecting the target attribute (e.g. firstName on /account-type).
    q = re.sub(r'(?i)\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s,;]*)', ' ', q)
    q = re.sub(r'(?<!\w)/(?:api/)?[\w{}./-]+', ' ', q)
    # Explicit attribute intent outranks unrelated field mentions.
    explicit = re.search(r'\b(?:impact|mapping|lineage|change|changing|attribute|field|property)\s+(?:of\s+|for\s+|on\s+|to\s+)?([A-Za-z_][A-Za-z0-9_]*)\b', q, re.I)
    if explicit and explicit.group(1).casefold() not in {'the','a','an','on','of','for','to','in','from','java','mapping','change','impact'}:
        word = explicit.group(1)
        for canonical, patterns in ALIASES.items():
            if any(re.fullmatch(pattern.replace(r'\b',''), word, re.I) for pattern in patterns):
                return canonical
        if re.search(r'[A-Z_]', word):
            return word
    for canonical, patterns in ALIASES.items():
        if any(re.search(pattern, q, re.I) for pattern in patterns):
            return canonical
    # Explicit Java-style member references take priority over prose.
    member = re.search(r'\b(?:[A-Z][\w]*\.)?([a-z][A-Za-z0-9_]*[A-Z][A-Za-z0-9_]*)\b', q)
    if member:
        return member.group(1)
    # A simple one-token query may be a valid snake_case field.
    if re.fullmatch(r'\s*[A-Za-z_][A-Za-z0-9_]*\s*', q):
        return q.strip()
    # Explicit field markers support arbitrary snake_case fields.
    marked = re.search(r'\b(?:attribute|field|column|property)\s*[:=]\s*([A-Za-z_][A-Za-z0-9_]*)\b', q, re.I)
    if marked:
        return marked.group(1)
    # Do not send a full English question to the mapping search engine.
    return ''


def mapping_change_impact_intent(query: str) -> bool:
    return bool(MAPPING.search(query or '') and re.search(
        r'\b(?:impact|affect|affected|changing|change|changes|modify|modification|regression)\b',
        query or '', re.I))


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
    if attribute == 'firstName': variants += ['first_name', 'first name']
    if attribute == 'lastName': variants += ['last_name', 'last name']
    if attribute == 'primaryEmail': variants += ['primary_email', 'primary email']
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
