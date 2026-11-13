"""Focused, read-only Java source traces for evidence-sensitive ASK questions.

Source observations are exact working-tree lines, not runtime or compiler verification.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

SKIP = {'.git', '.gradle', '.idea', 'build', 'target', 'out', '.venv', 'node_modules'}


def _mode(query: str):
    q = query.lower()
    if re.search(r'\btrace\b', q) and re.search(r'\bxml\b', q) and re.search(r'\b(?:database|persistence|repository)\b', q):
        return 'xml_persistence'
    if re.search(r'\btrace\b', q) and re.search(r'\bcontroller\b', q) and re.search(r'\bservice\b', q) and re.search(r'\b(?:repository|persistence)\b', q):
        return 'controller_persistence'
    if re.search(r'\b(?:what happens|behavior|behaviour)\b', q) and re.search(r'\b(?:does not exist|not exist|missing|not found)\b', q) and re.search(r'\breplace\b', q):
        return 'missing_customer'
    if re.search(r'\b(?:regression tests?|existing tests?)\b', q) and re.search(r'\breplace\b', q) and re.search(r'\b(?:identify|list|show|find|relevant)\b', q):
        return 'replace_tests'
    return None


def answer(query: str, project_path: str | None):
    mode = _mode(query)
    if not mode:
        return None
    if not project_path or not Path(project_path).expanduser().is_dir():
        return dict(query=query, source='DETERMINISTIC_JAVA_SOURCE_TRACE', status='PROJECT_NOT_SELECTED',
                    answer='Select a valid Java project directory. No source trace was performed.')
    root = Path(project_path).expanduser().resolve()
    names = {
        'xml_persistence': {'CustomerController.java', 'CustomerXmlRequest.java', 'CustomerXmlMapper.java', 'CustomerService.java', 'CustomerJpaMapper.java', 'CustomerAccountRepository.java', 'CustomerAccountEntity.java'},
        'controller_persistence': {'CustomerController.java', 'CustomerService.java', 'CustomerAccountRepository.java', 'CustomerJpaMapper.java'},
        'missing_customer': {'CustomerService.java', 'CustomerNotFoundException.java'},
        'replace_tests': set(),
    }[mode]
    records = []
    for path in sorted(root.rglob('*.java')):
        if SKIP.intersection(path.relative_to(root).parts):
            continue
        if names and path.name not in names:
            continue
        if mode == 'replace_tests' and 'test' not in {part.lower() for part in path.relative_to(root).parts}:
            continue
        try:
            raw = path.read_bytes()
            content = raw.decode('utf-8')
        except (OSError, UnicodeError):
            continue
        records.append((path.relative_to(root).as_posix(), content.splitlines(), hashlib.sha256(raw).hexdigest()[:12]))
    if not records:
        return dict(query=query, source='DETERMINISTIC_JAVA_SOURCE_TRACE', status='NO_SOURCE_MATCH',
                    answer='No matching readable Java source files were found in the selected project.')

    patterns = {
        'xml_persistence': {
            'CustomerController.java': r'@(?:Post|Put)Mapping|CustomerXmlRequest|service\.(?:create|replace)\(',
            'CustomerXmlRequest.java': r'@JacksonXml|class CustomerXmlRequest|customerId',
            'CustomerXmlMapper.java': r'\btoModel\(|setCustomerId\(|getCustomerId\(',
            'CustomerService.java': r'\b(?:create|replace|validate)\(|xmlMapper\.toModel\(|jpaMapper\.to(?:Entity|Model)\(|repository\.(?:save|existsById)\(',
            'CustomerJpaMapper.java': r'\btoEntity\(|\btoModel\(|setCustomerId\(',
            'CustomerAccountRepository.java': r'interface |JpaRepository|CrudRepository',
            'CustomerAccountEntity.java': r'@Entity|@Table|@Id|@Column|customerId',
        },
        'controller_persistence': {
            'CustomerController.java': r'@(?:Post|Put|Patch|Delete|Get)Mapping|service\.replace\(|Customer replace\(',
            'CustomerService.java': r'\breplace\(|repository\.(?:existsById|save)\(|xmlMapper\.toModel\(|jpaMapper\.to(?:Entity|Model)\(',
            'CustomerAccountRepository.java': r'interface |JpaRepository|CrudRepository',
            'CustomerJpaMapper.java': r'\btoEntity\(|\btoModel\(',
        },
        'missing_customer': {
            'CustomerService.java': r'\breplace\(|repository\.existsById\(customerId\)|throw new CustomerNotFoundException\(customerId\)',
            'CustomerNotFoundException.java': r'class CustomerNotFoundException|CustomerNotFoundException\(',
        },
    }
    evidence, lines = [], []
    if mode == 'replace_tests':
        # Restrict to test methods containing a direct replace invocation, not every test in a class.
        from services.agent.code_understanding_service import _methods
        for rel, src_lines, sha in records:
            source = '\n'.join(src_lines)
            for method in _methods(source):
                if re.search(r'\b(?:service\.)?replace\s*\(', method['source']) and method['name'] != 'replace':
                    # Tests may use assertThrows lambdas; method names are source-verified.
                    item = {'file': rel, 'method': method['name'], 'line': method['line_number'], 'sha256': sha}
                    evidence.append(item)
                    lines.append(f"EXISTING TEST CANDIDATE: {rel}:{method['line_number']} {method['name']}() [SHA256 {sha}]")
        lines.append('These are observed test methods that invoke replace(); no test execution, coverage, or JIRA link is verified.')
        lines.append('Recommended additional tests: NOT DETERMINED by this source-only query.')
    else:
        lines.append('Observed Java working-tree source (not a runtime trace; cross-class edges are inferred, not compiler-verified):')
        for rel, src_lines, sha in records:
            pat = patterns[mode].get(Path(rel).name)
            if not pat:
                continue
            hits = [(i, line.strip()) for i, line in enumerate(src_lines, 1) if re.search(pat, line)]
            if not hits:
                continue
            lines.append(f'\nFILE {rel} [SHA256 {sha}]')
            for number, value in hits[:24]:
                lines.append(f'  {number}: {value}')
                evidence.append({'file': rel, 'line': number, 'source_line': value, 'sha256': sha})
        if mode == 'missing_customer':
            service_lines = next((src for rel, src, _ in records if Path(rel).name == 'CustomerService.java'), [])
            joined = '\n'.join(service_lines)
            if re.search(r'if\s*\(\s*!repository\.existsById\(customerId\)\s*\)\s*throw\s+new\s+CustomerNotFoundException\(customerId\)', joined):
                lines.append('\nSOURCE-BACKED OUTCOME: when existsById(customerId) is false, replace() throws CustomerNotFoundException before mapping and save.')
            else:
                lines.append('\nOUTCOME NOT VERIFIED: inspect the full replace() method; the required branch was not matched.')
        elif mode == 'xml_persistence':
            lines.append('\nCandidate flow: HTTP XML request -> CustomerXmlRequest -> CustomerController -> CustomerService -> CustomerXmlMapper -> CustomerJpaMapper -> repository.save -> CustomerAccountEntity.')
            lines.append('This sequence is a source-backed candidate only where the displayed calls/declarations exist; database execution and XML deserialization are NOT VERIFIED.')
        else:
            lines.append('\nCandidate flow: CustomerController.replace -> CustomerService.replace -> CustomerJpaMapper.toEntity -> repository.save. Repository framework implementation/database execution NOT VERIFIED.')
    return {'query': query, 'source': 'DETERMINISTIC_JAVA_SOURCE_TRACE',
            'status': 'FOUND' if evidence else 'NO_SOURCE_MATCH', 'answer': '\n'.join(lines),
            'source_evidence': evidence, 'compiler_verified': False, 'execution_verified': False}
