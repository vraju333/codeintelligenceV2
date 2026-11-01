"""Read-only Phase 10 semantic contract checks. Python standard library only."""
import argparse, datetime, json, pathlib, re, sys, urllib.request

CASES = [
 ('accountType','What is the impact of changing accountType?','COMBINED_ATTRIBUTE_EVIDENCE'),
 ('countryCode','What is the impact of changing countryCode?','COMBINED_ATTRIBUTE_EVIDENCE'),
 ('email','What is the impact of changing email?','COMBINED_ATTRIBUTE_EVIDENCE'),
 ('unknown','What is the impact of changing noSuchField_92831?','ATTRIBUTE_NOT_INDEXED'),
 ('history','Show historical changes to accountType','ATTRIBUTE_TO_JIRA_TO_COMMIT_SEMANTIC'),
 ('mapping','Show mapping documents for accountType','MAPPING_DOCUMENT_EVIDENCE'),
]

def post(base, payload, timeout):
    request = urllib.request.Request(base.rstrip('/')+'/api/knowledge-agent/ask', data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)

def validate(label, data):
    failures, reviews, observations = [], [], {}
    focus = data.get('focused_evidence') or {}
    text = json.dumps(data, ensure_ascii=False, default=str)
    if label == 'accountType':
        ops = focus.get('operations') or []
        methods = focus.get('methods') or focus.get('java_methods') or []
        jiras = focus.get('jira_matches') or focus.get('jiras') or []
        mappings = data.get('mapping_rows') or []
        observations.update(operations=ops, java_methods=methods, jiras=jiras, mapping_rows_count=len(mappings))
        if len(methods) != 9: reviews.append(f'Expected 9 source-method mentions; found {len(methods)}')
        if len(jiras) != 4: reviews.append(f'Expected 4 JIRA candidates; found {len(jiras)}')
        if len(ops) != 3: reviews.append(f'Expected 3 unique API candidates; found {len(ops)}')
        observed_ops = [(str(o.get('http_method','')).upper(),str(o.get('endpoint',''))) for o in ops if isinstance(o,dict)]
        if len(observed_ops) != len(set(observed_ops)): failures.append('Duplicate API operations in focused evidence')
        observed_jiras = {str(j.get('jira_id','')) for j in jiras if isinstance(j,dict)}
        for expected_jira in ('CAS-1','CAS-4','CAS-7','CAS-8'):
            if expected_jira not in observed_jiras: reviews.append('JIRA missing from focused evidence: '+expected_jira)
        for method in methods:
            if not isinstance(method,dict) or not method.get('file') or not method.get('line'):
                reviews.append('Java method missing source location: '+str(method))
        for op in ops:
            if isinstance(op,dict) and 'DATAFLOW_VERIFIED' in str(op.get('evidence_type','')):
                reviews.append('Review unexpected verified dataflow classification: '+str(op))
        expected_ops = {'POST /api/customers','PUT /api/customers/{customerId}','PATCH /api/customers/{customerId}/account-type'}
        # Some versions use a different key in the focused evidence: inspect all evidence, not only one shape.
        api_text = json.dumps(data.get('predictive_api_candidates') or data.get('predictive_impact') or {}, default=str)
        for op in sorted(expected_ops):
            method, path = op.split(' ',1)
            if op not in text and not (method in api_text and path in api_text):
                failures.append('Missing candidate API operation: '+op)
        for key in ('CAS-1','CAS-4','CAS-7','CAS-8'):
            if not re.search(r'\b'+re.escape(key)+r'\b',text): reviews.append('Expected JIRA not observed: '+key)
        for method in ('updateAccountType','validate','getAccountType','setAccountType'):
            if method not in text: reviews.append('Expected source method not observed: '+method)
        if 'NOT_VERIFIED' not in text and 'not verified' not in text.lower() and 'CANDIDATE' not in text:
            failures.append('No uncertainty/candidate classification')
        if len(mappings)!=4: reviews.append('Expected 4 mapping rows, found '+str(len(mappings)))
        # Prevent accidental assertion that QA imports were executed.
        if 'execution_verified' in data and data['execution_verified'] is True:
            failures.append('Execution marked verified: investigate QA evidence')
    elif label == 'countryCode':
        mapping = data.get('mapping_rows') or []
        observations['mapping_rows_count']=len(mapping)
        if len(mapping)!=4: reviews.append('Expected 4 mapping rows, found '+str(len(mapping)))
        if 'Customer_Account_Service_Mapping_V3.xlsx' not in text: reviews.append('V3 mapping document missing')
    elif label == 'email':
        observations['resolved_attribute']=data.get('attribute')
        if data.get('attribute') != 'customerEmail': reviews.append('Expected resolved attribute customerEmail; found '+str(data.get('attribute')))
        if 'CAS-3' not in text: reviews.append('CAS-3 historical evidence missing; confirm source')
        # Source-level correctness cannot be established by an API contract check.
        reviews.append('GROUND_TRUTH_REQUIRED: confirm email and customerEmail are equivalent in source and mapping contracts')
        observations['source_methods']=(focus.get('methods') or focus.get('java_methods') or [])
        observations['jira_matches']=(focus.get('jira_matches') or focus.get('jiras') or [])
        observations['candidate_operations']=(focus.get('operations') or [])
    elif label == 'unknown':
        if not re.search(r'no indexed attribute evidence|not indexed',text,re.I): failures.append('Unknown attribute did not return explicit no-evidence wording')
        if any(data.get(k) for k in ('predictive_api_candidates','candidate_jiras','mapping_rows')):
            failures.append('Unknown attribute has candidate impact evidence')
    elif label == 'history':
        if 'historical' not in text.lower() and 'commit' not in text.lower(): failures.append('History result lacks historical framing')
    elif label == 'mapping':
        rows = data.get('mapping_rows') or []
        observations['mapping_rows_count']=len(rows)
        if len(rows)!=4: reviews.append('Expected 4 mapping rows, found '+str(len(rows)))
    return failures, reviews, observations

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base',default='http://127.0.0.1:8081')
    parser.add_argument('--project',required=True)
    parser.add_argument('--out',default='phase10_semantic_report.json')
    parser.add_argument('--timeout',type=int,default=120)
    args=parser.parse_args()
    results=[]
    for label, query, expected in CASES:
        item={'case':label,'query':query,'expected_source':expected}
        try:
            data=post(args.base,{'query':query,'project_path':args.project},args.timeout)
            item['actual_source']=data.get('source')
            failures,reviews,observations=validate(label,data)
            if data.get('source')!=expected: failures.insert(0,'Unexpected source: '+str(data.get('source')))
            item.update(failures=failures,manual_reviews=reviews,observations=observations,
                        status='FAIL' if failures else 'REVIEW' if reviews else 'PASS')
        except Exception as exc:
            item.update(status='ERROR',failures=[type(exc).__name__+': '+str(exc)])
        results.append(item)
        print(f"{item['status']:6} {label}: {len(item.get('failures',[]))} failures, {len(item.get('manual_reviews',[]))} reviews")
    report={'timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'project':args.project,'base':args.base,'scope':'Semantic contract checks; not ground-truth code/dataflow verification',
            'cases':results,'counts':{status:sum(x['status']==status for x in results) for status in ('PASS','REVIEW','FAIL','ERROR')}}
    pathlib.Path(args.out).write_text(json.dumps(report,indent=2,ensure_ascii=False,default=str),encoding='utf-8')
    print('Report:',args.out)
    return 1 if any(x['status'] in ('FAIL','ERROR') for x in results) else 0
if __name__=='__main__': sys.exit(main())
