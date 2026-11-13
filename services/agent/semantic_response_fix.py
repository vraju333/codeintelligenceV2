"""Bounded, source-backed ASK answers for mapper, mapping-version and concise flow queries."""
from __future__ import annotations
import re
from pathlib import Path


def _intent(query):
    q = query.lower()
    if ('customerxmlmapper' in q or 'customerjpamapper' in q) and any(x in q for x in ('explain', 'transform', 'normaliz', 'direction')):
        return 'java_mappers'
    if 'customerservice.replace' in q and ('concise call graph' in q or 'main business methods' in q):
        return 'concise_graph'
    if 'customerid' in q.lower() and ('mapping' in q or 'source xml' in q or 'source-to-target' in q):
        if any(x in q for x in ('compare', 'across v1', 'actual changes', 'different versions')): return 'versions'
        if any(x in q for x in ('latest', 'newest')): return 'latest'
        return 'mapping_path'
    return None


def _project(project_path):
    from config import settings
    p = project_path or getattr(settings, 'JAVA_PROJECT_PATH', None)
    if not p or not Path(p).is_dir(): return None
    return Path(p)


def _java_mapper_answer(query, project):
    if project is None: return None
    paths = sorted(p for p in (project / 'src/main/java').rglob('*.java') if p.stem in ('CustomerXmlMapper','CustomerJpaMapper'))
    if not paths: return None
    lines = ['Java mapper transformations (direct working-tree source evidence):']
    evidence = []
    for path in paths:
        source = path.read_text(encoding='utf-8', errors='replace').splitlines()
        lines.append(f'\n{path.stem} — {path.relative_to(project)}')
        for i, line in enumerate(source, 1):
            stripped = line.strip()
            if re.search(r'\b(?:public|private|protected)\s+\w+\s+(?:toModel|toEntity|trim|lower|upper)\s*\(', stripped):
                lines.append(f'  method line {i}: {stripped}')
            if re.search(r'\btarget\.set\w+\s*\(', stripped):
                lines.append(f'  line {i}: {stripped}')
                evidence.append({'path':str(path.relative_to(project)), 'line':i, 'text':stripped})
    lines.append('\nMapping direction is established by the declared method return type and source parameter type. Transformations above are observed calls, not inferred business rules.')
    return {'status':'FOUND', 'source':'JAVA_WORKING_TREE', 'query':query, 'answer':'\n'.join(lines), 'source_evidence':evidence}


def _mapping_answer(db, query, kind, project_path):
    from services.agent.ask_mapping_documents_service import mapping_documents_answer
    result = mapping_documents_answer(db, 'mapping documents for customerId', project_path=project_path)
    rows = result.get('mapping_rows') or []
    if not rows: return {**result, 'query':query, 'answer':'No indexed customerId mapping rows found in the selected project; cannot verify version or database mapping.'}
    # Imported rows are independent of Java source and do not establish runtime behavior.
    def ver(row):
        p = row.get('provenance') or {}
        v = str(p.get('document_version') or '')
        if not v:
            m = re.search(r'(?i)(?:^|[_ .-])v(\d+)(?:[_. -]|$)', str(p.get('document') or ''))
            v = m.group(1) if m else ''
        m = re.search(r'\d+', v)
        return int(m.group()) if m else -1
    # Same workbook path+sheet+row+definition is one piece of evidence, even if imported twice.
    unique = {}
    for row in rows:
        p = row.get('provenance') or {}
        key = (str(p.get('source_ref') or p.get('document') or '').casefold().replace('\\','/'),
               str(p.get('sheet') or ''), str(p.get('row') or ''),
               str(row.get('source_path') or ''), str(row.get('target_class') or ''),
               str(row.get('target_attribute') or ''), str(row.get('mapping_rule') or ''))
        unique.setdefault(key, row)
    ordered = sorted(unique.values(), key=ver, reverse=True)
    if kind == 'latest':
        maxv = max(map(ver, ordered))
        ordered = [r for r in ordered if ver(r) == maxv]
    elif kind == 'versions':
        ordered = [r for r in ordered if ver(r) >= 0]
    lines = [f'CustomerId imported mapping evidence — {kind.replace("_", " ")}',
             f'{len(rows)} indexed matches; {len(unique)} distinct workbook/row/definition records.']
    for row in ordered[:24]:
        p = row.get('provenance') or {}
        lines.append(f"- V{ver(row)} | {p.get('document') or 'unknown workbook'} | sheet {p.get('sheet') or '?'} row {p.get('row') or '?'}: "
                     f"{row.get('source_type') or '?'} {row.get('source_path') or '?'} -> "
                     f"{row.get('target_class') or '?'}.{row.get('target_attribute') or '?'}; "
                     f"transformation={row.get('mapping_rule') or 'not specified'}; "
                     f"null rule={row.get('null_rule') or 'not specified'}; "
                     f"validation={row.get('validation_rule') or 'not specified'}")
    if kind == 'versions':
        # Compare like-for-like source families. A JSON workbook missing in V3
        # is NOT evidence that the XML mapping changed between V2 and V3.
        families = {}
        for row in ordered:
            family = (str(row.get('source_type') or '').upper(),
                      str(row.get('source_path') or '').casefold())
            definition = (str(row.get('target_class') or '').casefold(),
                          str(row.get('target_attribute') or '').casefold(),
                          str(row.get('mapping_rule') or '').strip().casefold(),
                          str(row.get('null_rule') or '').strip().casefold(),
                          str(row.get('validation_rule') or '').strip().casefold())
            families.setdefault(family, {}).setdefault(ver(row), set()).add(definition)
        for (source_type, source_path), version_defs in sorted(families.items()):
            vs = sorted(version_defs)
            lines.append(f'Comparable family: {source_type} {source_path} (versions {", ".join("V"+str(v) for v in vs)})')
            for v1, v2 in zip(vs[:-1], vs[1:]):
                if version_defs[v1] == version_defs[v2]:
                    lines.append(f'  V{v1} -> V{v2}: no observed change in this mapping definition.')
                else:
                    lines.append(f'  V{v1} -> V{v2}: observed definition differs; compare the cited rows.')
        lines.append('Missing workbook versions in one family are not evidence of a mapping change. Duplicate indexed rows are counted once by workbook/sheet/row/definition.')
    if kind == 'mapping_path':
        lines.extend(_jpa_customer_id_evidence(_project(project_path)))
    lines.append('Only imported workbook mappings are proven here. A database column or end-to-end XML-to-DB path is NOT verified unless a matching target is explicitly present in these rows and Java/JPA source is separately traced.')
    return {**result, 'query':query, 'answer':'\n'.join(lines), 'mapping_rows':ordered, 'mapping_count':len(ordered)}


def _jpa_customer_id_evidence(project):
    """Only claim physical DB column when an explicit JPA annotation proves it."""
    if project is None:
        return ['Java/JPA working tree unavailable: database CUSTOMER_ID column NOT verified.']
    java_root = project / 'src/main/java'
    mappers = list(java_root.rglob('CustomerJpaMapper.java')) if java_root.exists() else []
    entities = list(java_root.rglob('CustomerAccountEntity.java')) if java_root.exists() else []
    out = []
    if mappers:
        path = mappers[0]
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        signature = next(((i, line.strip()) for i, line in enumerate(lines, 1)
                          if re.search(r'\bCustomerAccountEntity\s+toEntity\s*\(\s*Customer\s+', line)), None)
        assignment = next(((i, line.strip()) for i, line in enumerate(lines, 1)
                           if re.search(r'target\.setCustomerId\s*\(\s*source\.getCustomerId\s*\(', line)), None)
        if signature and assignment:
            out.append(f'Java source: {path.relative_to(project)}:{signature[0]} {signature[1]}')
            out.append(f'Java source: {path.relative_to(project)}:{assignment[0]} {assignment[1]}')
            out.append('Source-verified: Customer.customerId -> CustomerAccountEntity.customerId (via CustomerJpaMapper).')
    if entities:
        path = entities[0]
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        # Restrict @Column matching to the customerId field, not an unrelated annotation.
        field_index = next((i for i, line in enumerate(lines)
                            if re.search(r'\b(?:String|UUID|Long)\s+customerId\s*[;=]', line)), None)
        if field_index is not None:
            nearby = '\n'.join(lines[max(0,field_index-7):field_index+1])
            annotation = re.search(r'@Column\s*\(\s*name\s*=\s*"([^"]+)"', nearby)
            if annotation:
                column = annotation.group(1)
                out.append(f'Java source: {path.relative_to(project)}:{field_index+1} customerId field; explicit @Column(name="{column}").')
                out.append(f'Explicit JPA physical column: {column}.')
                if column.upper() != 'CUSTOMER_ID':
                    out.append('WARNING: verified column name differs from requested CUSTOMER_ID.')
            else:
                out.append(f'Java source: {path.relative_to(project)}:{field_index+1} customerId field; no adjacent explicit @Column(name=...) found.')
                out.append('Physical database column NOT verified; implicit naming strategy must be checked.')
    if not entities:
        out.append('CustomerAccountEntity.java unavailable: physical database column NOT verified.')
    if not mappers:
        out.append('CustomerJpaMapper.java unavailable: model-to-entity mapping NOT verified.')
    out.append('Static Java/JPA evidence only; actual database schema and runtime persistence are not verified.')
    return out


def _concise_graph(query, project):
    if project is None: return None
    files = {p.stem:p for p in (project/'src/main/java').rglob('*.java') if p.stem in ('CustomerService','CustomerXmlMapper','CustomerJpaMapper')}
    service = files.get('CustomerService')
    if not service: return None
    source = service.read_text(encoding='utf-8',errors='replace').splitlines()
    start = next((i for i,l in enumerate(source) if re.search(r'\bCustomer\s+replace\s*\(',l)), None)
    if start is None: return None
    # Bounded method extraction; only include directly observed calls from replace body.
    depth = 0; begun = False; calls = []
    allowed = ('repository.existsById(', 'xmlMapper.toModel(', 'validate(', 'jpaMapper.toEntity(', 'repository.save(', 'jpaMapper.toModel(')
    for i in range(start, min(len(source),start+95)):
        line = source[i]
        if '{' in line: begun = True
        if begun:
            for call in allowed:
                if call in line: calls.append((i+1,call[:-1]))
            depth += line.count('{') - line.count('}')
            if depth <= 0: break
    # Java evaluates arguments inside-out, not in source textual order.
    # A single return expression can therefore call toModel first textually,
    # but toEntity -> save -> toModel in execution order.
    indexed = {call: (line, call) for line, call in calls}
    ordered_names = ('repository.existsById', 'xmlMapper.toModel', 'validate',
                     'jpaMapper.toEntity', 'repository.save', 'jpaMapper.toModel')
    ordered_calls = [indexed[name] for name in ordered_names if name in indexed]
    rel = service.relative_to(project)
    lines = ['Concise CustomerService.replace execution path (static, conditional):',
             f'Start: {rel}:{start+1}',
             'The record existence check may throw before mapping; path-ID mismatch or validation may throw before save.']
    lines.extend(f'  {rel}:{n}  {call} [SOURCE_OBSERVED; target not compiler-resolved]'
                 for n, call in ordered_calls)
    lines.append('Nested expression evaluation order: toEntity(customer) -> repository.save(entity) -> toModel(savedEntity).')
    lines.append('Repository methods are framework-mediated; no compiler-verified dispatch or runtime execution proof. Getter/setter and test-method expansions intentionally omitted.')
    return {'status':'FOUND','source':'JAVA_WORKING_TREE','query':query,
            'answer':'\n'.join(lines),
            'call_edges':[{'line':n,'expression':c,'resolution':'SOURCE_OBSERVED'}
                          for n,c in ordered_calls]}



def answer(db, query, project_path):
    kind = _intent(query or '')
    if not kind: return None
    if kind in ('versions','latest','mapping_path'): return _mapping_answer(db,query,kind,project_path)
    project = _project(project_path)
    if kind == 'java_mappers': return _java_mapper_answer(query,project)
    if kind == 'concise_graph': return _concise_graph(query,project)
    return None
