#!/usr/bin/env python3
"""Offline evidence review. Candidate matches are NOT independently verified ground truth."""
import argparse, json, pathlib, collections

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--report',default='phase13-results/report.json')
    p.add_argument('--expected',default='phase13/expected_country_code.json')
    p.add_argument('--output',default='phase13-results/country_code_review.md')
    a=p.parse_args()
    data=json.loads(pathlib.Path(a.report).read_text(encoding='utf-8'))
    expected=json.loads(pathlib.Path(a.expected).read_text(encoding='utf-8'))
    impact=data.get('api_responses',{}).get('impact',{}).get('body',{})
    jira=sorted({x.get('jira_id') for x in impact.get('historical_jiras',[]) if x.get('jira_id')})
    tests=impact.get('test_cases',[])
    test_ids=sorted({x.get('test_id') for x in tests if x.get('test_id')})
    methods=impact.get('java_evidence',{}).get('methods',[])
    production=[x for x in methods if '/main/' in str(x.get('file','')).replace('\\','/')]
    mappings=impact.get('mappings',[])
    source_refs=collections.defaultdict(set)
    for t in tests:
        if t.get('test_id'):source_refs[t['test_id']].add(str(t.get('source_ref','')))
    docs= data.get('api_responses',{}).get('mapping_documents',{}).get('body',{}).get('documents',[])
    filenames=collections.defaultdict(list)
    for d in docs:filenames[str(d.get('source_ref') or d.get('filename')).replace('\\','/').casefold()].append(d.get('id'))
    duplicate_files={k:v for k,v in filenames.items() if len(v)>1}
    lines=['# countryCode — Phase 13 evidence review','', '**Status: CANDIDATE ONLY — not independently verified against source repository or test executions.**','',f"Ground truth approved: **{bool(expected.get('approved'))}**",'',f'- JIRA requirement mentions: {len(jira)} — '+', '.join(jira),f'- Java method mentions: {len(methods)} ({len(production)} in main source)',f'- Mapping row matches: {len(mappings)}',f'- Imported test case entries: {len(tests)}; distinct test IDs: {len(test_ids)}',f'- Duplicate mapping source paths: {len(duplicate_files)}','', '## Production Java method candidates']
    lines += [f"- `{m.get('file')}::{m.get('method')}` (line {m.get('line')})" for m in production]
    lines += ['','## Test case provenance (same test may be imported from different sources)']
    lines += [f"- `{tid}`: {len(source_refs[tid])} source(s)" for tid in test_ids]
    lines += ['','## Duplicate mapping source paths']
    lines += [f'- `{k}`: document IDs {v}' for k,v in duplicate_files.items()] or ['- None detected']
    lines += ['','## Mapping requirement conflicts']
    lines += [f"- `{c.get('attribute')}`: requirement `{c.get('requirement')}` vs mapping `{c.get('document_rule')}` in `{c.get('provenance',{}).get('document')}`" for c in impact.get('mapping_conflicts',[])]
    lines += ['','## Evidence limitations','- `code_impact.status` is '+str(impact.get('code_impact',{}).get('status')),'- Git verified changes: '+str(len(impact.get('verified_code_changes',[]))),'- Test execution verified: '+str(sum(bool(t.get('execution_verified')) for t in tests)),'- Baseline impact is not composed in this response.','- Candidate call paths are not AST/CFG or verified field-level data flow.','', '## Next verification actions','1. Open the Java files above and confirm each method really transforms, validates, persists, or exposes countryCode.','2. Confirm JIRA relationships against requirements; a text mention alone is not a dependency.','3. Confirm the test IDs and execution results in test documents/CI.','4. Confirm mapping version metadata and determine whether duplicate source records are historical or erroneous.','5. Only then set approved=true and calculate precision/recall against independently reviewed labels.']
    out=pathlib.Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text('\n'.join(lines)+'\n',encoding='utf-8');print('Review:',out.resolve());return 0
if __name__=='__main__':raise SystemExit(main())
