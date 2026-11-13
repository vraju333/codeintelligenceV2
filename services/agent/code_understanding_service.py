"""Project-scoped, read-only Java source evidence. No LLM-written source snippets.

This is lexical analysis, not a Java compiler or symbol solver. The result states
that limitation explicitly. Never infer a call edge or a field binding.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

SKIP = {'.git', '.gradle', '.idea', 'build', 'target', 'out', '.venv', 'node_modules'}
CLASS = re.compile(r'\b(?:class|interface|record|enum)\s+([\w$]+)')
METHOD = re.compile(r'(?m)^[ \t]*(?:(?:@[\w.]+(?:\([^\n]*\))?)[ \t]*\n[ \t]*)*(?:(?:public|private|protected|static|final|synchronized|abstract|default|native|strictfp)[ \t]+)*[\w<>?,.\[\] \t]+?\s+([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws\s+[\w., \t]+)?\{')
WORD = re.compile(r'[A-Za-z_$][\w$]*')


def _intent(query):
    q = query.lower()
    # Source requests take precedence even if 'XML mappings' is also mentioned.
    source = bool(re.search(r'\b(java|source|code|class|methods?|implementation|references?|usages?|declarations?|line numbers?|crud|controller|repository)\b', q))
    if not source and not re.search(r"\b[A-Za-z_$][\w$]*Service\.[A-Za-z_$][\w$]*\b", query):
        return None, None
    if re.search(r'\b(references?|usages?|occurrences?)\b', q) and re.search(r'\b(find|every|all|group|list|show)\b', q):
        m = re.search(r'\b(?:to|of|for)\s+[`\'\"]?([A-Za-z_$][\w$]*)', query, re.I)
        # 'references to customerId' and 'references of customerId'.
        return 'references', m.group(1) if m else None
    if re.search(r'\b(validations?|business rules?)\b', q) and re.search(r'\b(replace|validate|method)\b', q):
        return 'validation', None
    if (all(x in q for x in ('creation', 'retrieval', 'updating', 'deletion')) or
            ('crud' in q and 'method' in q)):
        return 'crud', None
    if 'updat' in q and re.search(r'\b(methods?|implementation|source)\b', q) and 'customer' in q:
        return 'updates', None
    return None, None


def _masked(source):
    """Preserve offsets/newlines while masking Java comments and literals."""
    pattern = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"""[\s\S]*?"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
    return pattern.sub(lambda m: re.sub(r'[^\n]', ' ', m.group()), source)


def _methods(source):
    masked = _masked(source)
    for match in METHOD.finditer(masked):
        if match.group(1) in {'if','else','for','while','switch','catch','try','do','synchronized','return','throw','new'}:
            continue
        depth, end = 1, None
        for i in range(match.end(), len(masked)):
            if masked[i] == '{':
                depth += 1
            elif masked[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is not None:
            start = match.start()
            yield {'name': match.group(1), 'start': start, 'end': end,
                   'line_number': source.count('\n', 0, start) + 1,
                   'signature': source[start:match.end()].strip().rsplit('{', 1)[0].strip(),
                   'source': source[start:end].strip()}


def _evidence(file, root, sha, cls, method):
    return {'class': cls, 'method': method['name'], 'signature': method['signature'],
            'relative_path': file.relative_to(root).as_posix(), 'file_path': str(file),
            'line_number': method['line_number'], 'sha256': sha,
            'source': method['source'], 'source_kind': 'LIVE_WORKING_TREE',
            'verification': 'EXACT_FILE_READ_SHA256'}


def answer(query: str, project_path: str | None):
    mode, identifier = _intent(query)
    if not mode:
        return None
    if not project_path or not Path(project_path).expanduser().is_dir():
        return {'status': 'PROJECT_NOT_SELECTED', 'source': 'LIVE_JAVA_SOURCE', 'query': query,
                'answer': 'A valid selected project directory is required for live Java source analysis. No indexed mappings or LLM output substituted.'}
    root = Path(project_path).expanduser().resolve()
    evidence, errors, files_scanned = [], [], 0
    for file in sorted(root.rglob('*.java')):
        if SKIP.intersection(file.relative_to(root).parts):
            continue
        try:
            data = file.read_bytes()
            source = data.decode('utf-8')
        except (OSError, UnicodeError) as exc:
            errors.append(f'{file}: {type(exc).__name__}')
            continue
        files_scanned += 1
        sha = hashlib.sha256(data).hexdigest()
        masked = _masked(source)
        classes = list(CLASS.finditer(masked))
        cls = classes[0].group(1) if classes else file.stem
        methods = list(_methods(source))
        if mode == 'references':
            if not identifier:
                return {'status': 'INVALID_QUERY', 'source': 'LIVE_JAVA_SOURCE', 'query': query,
                        'answer': 'Specify the exact identifier whose Java references should be listed.'}
            occurrences = []
            for m in re.finditer(r'\b' + re.escape(identifier) + r'\b', masked):
                owner = next((x['name'] for x in methods if x['start'] <= m.start() < x['end']), '<class-level>')
                line = source.count('\n', 0, m.start()) + 1
                occurrences.append({'method': owner, 'line_number': line,
                                    'source_line': source.splitlines()[line - 1].strip()})
            # One displayed source line per method/line, while retaining the raw
            # lexical occurrence count for accurate metrics.
            raw_occurrence_count = len(occurrences)
            occurrences = list({(x['method'], x['line_number']): x for x in occurrences}.values())
            if occurrences:
                evidence.append({'class': cls, 'relative_path': file.relative_to(root).as_posix(),
                                 'file_path': str(file), 'sha256': sha,
                                 'occurrence_count': raw_occurrence_count, 'distinct_source_lines': len(occurrences), 'references': occurrences})
            continue
        for method in methods:
            name = method['name'].lower()
            operations = []
            if name in {'create', 'save', 'insert', 'add'}: operations.append('CREATE')
            if name in {'get', 'getall', 'find', 'findbyid', 'list', 'read'}: operations.append('READ')
            if name in {'replace', 'update', 'updateaccounttype', 'patch'}: operations.append('UPDATE')
            if name in {'delete', 'remove', 'deletebyid'}: operations.append('DELETE')
            include = (mode == 'crud' and bool(operations) or
                       mode == 'validation' and cls.lower() == 'customerservice' and name in {'replace', 'validate', 'require'} or
                       mode == 'updates' and name in {'replace', 'updateaccounttype', 'update', 'patch'} and 'customer' in (cls + str(file)).lower())
            if include:
                item = _evidence(file, root, sha, cls, method)
                item['operations'] = operations
                evidence.append(item)
    lines = [f'Live Java source results ({mode}) for {root}:']
    if mode == 'references':
        lines.append(f'Identifier: {identifier}; {sum(x["occurrence_count"] for x in evidence)} lexical code occurrences in {len(evidence)} files.')
        for item in evidence:
            lines.append(f'\n{item["relative_path"]} ({item["occurrence_count"]} occurrences) [SHA256 {item["sha256"][:12]}]')
            lines.extend(f'  {x["method"]}:{x["line_number"]} {x["source_line"]}' for x in item['references'])
    elif mode == 'crud':
        for op in ('CREATE', 'READ', 'UPDATE', 'DELETE'):
            selected = [x for x in evidence if op in x['operations']]
            lines.append(f'\n{op} ({len(selected)} lexical method candidates):')
            lines.extend(f'  {x["class"]}.{x["method"]} — {x["relative_path"]}:{x["line_number"]} [SHA256 {x["sha256"][:12]}] {x["signature"]}' for x in selected)
        lines.append('\nOther repository/entity/mapper/test relationships are not verified by method-name classification.')
    else:
        for item in evidence:
            lines.append(f'\n{item["class"]}.{item["method"]} — {item["relative_path"]}:{item["line_number"]} [SHA256 {item["sha256"][:12]}]\n{item["source"]}')
    if not evidence:
        lines.append('No matching Java source evidence found. No source code was invented.')
    if errors:
        lines.append(f'Unreadable files ({len(errors)}): ' + '; '.join(errors[:30]))
    lines.append('\nLimitations: lexical Java analysis only; no compiler-resolved references, call edges, or full CRUD dependency proof. Strings/comments excluded from reference counts. Source snippets are exact working-tree bytes decoded as UTF-8.')
    return {'status': 'FOUND' if evidence else 'NOT_FOUND', 'query': query, 'project_path': str(root),
            'source': 'DETERMINISTIC_LIVE_JAVA_CODE_UNDERSTANDING', 'mode': mode,
            'files_scanned': files_scanned, 'evidence': evidence, 'warnings': errors,
            'current_source_verified': not errors, 'semantic_bindings_verified': False,
            'answer': '\n'.join(lines)}
