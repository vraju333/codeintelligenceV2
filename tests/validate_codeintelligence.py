"""Read-only CodeIntelligence API smoke/accuracy checks; Python standard library only."""
import argparse, datetime, json, pathlib, re, sys, urllib.request, urllib.error

def post(base, route, payload, timeout):
    request=urllib.request.Request(base.rstrip('/')+route, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode('utf-8', errors='replace')

def examine(label, response, expected_attribute=None):
    issues=[]
    if not isinstance(response, dict): return ['Non-object response']
    body=json.dumps(response, ensure_ascii=False)
    if 'Traceback (most recent call last)' in body: issues.append('Server traceback exposed')
    if expected_attribute and expected_attribute.lower() not in body.lower(): issues.append('Requested attribute not represented in response')
    if label=='accountType prospective':
        if 'COMBINED_ATTRIBUTE_EVIDENCE' not in body and 'COMPLETE_ATTRIBUTE_HISTORY' not in body:
            issues.append('Unexpected route/source: expected combined prospective impact')
        if 'NOT_VERIFIED' not in body and 'not verified' not in body.lower() and 'candidate' not in body.lower():
            issues.append('Missing candidate/verification qualifier')
    return issues

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--base', default='http://127.0.0.1:8081')
    p.add_argument('--project', required=True, help='Absolute indexed project path on your machine')
    p.add_argument('--out', default='codeintelligence_validation_report.json')
    p.add_argument('--timeout', type=int, default=120)
    args=p.parse_args()
    cases=[('accountType prospective','What is the impact of changing accountType?','accountType'),('countryCode prospective','What is the impact of changing countryCode?','countryCode'),('email prospective','What is the impact of changing email?','email'),('unknown attribute','What is the impact of changing noSuchField_92831?','noSuchField_92831'),('historical distinction','Show historical changes to accountType','accountType'),('mapping search','Show mapping documents for accountType','accountType')]
    results=[]
    for label,query,attribute in cases:
        item={'case':label,'query':query,'status':'ERROR','issues':[]}
        try:
            answer=json.loads(post(args.base,'/api/knowledge-agent/ask',{'query':query,'project_path':args.project},args.timeout))
            item['source']=answer.get('source')
            item['issues']=examine(label,answer,attribute)
            item['status']='REVIEW' if item['issues'] else 'SMOKE_PASS'
            item['answer_excerpt']=str(answer.get('answer') or answer.get('summary') or '')[:600]
            if label=='accountType prospective':
                item['evidence_fields']=sorted(answer.keys())
                try:
                    investigation=json.loads(post(args.base,'/api/investigations/analyse',{'query':query,'project_path':args.project},args.timeout))
                    item['investigation_status']='PASS' if isinstance(investigation.get('investigation'),dict) and isinstance(investigation.get('result'),dict) else 'REVIEW'
                    item['investigation_source']=investigation.get('result',{}).get('source')
                    if item['investigation_source'] != item['source']:
                        item['issues'].append('Ask/investigation source differs')
                except Exception as exc: item['investigation_status']=f'ERROR: {type(exc).__name__}: {exc}'
                try:
                    events=post(args.base,'/api/investigations/events',{'query':query,'project_path':args.project},args.timeout)
                    names=re.findall(r'^event:\s*(\S+)',events,re.M)
                    item['stream_events']=names
                    if 'STATE_SNAPSHOT' not in names or 'RUN_FINISHED' not in names: item['issues'].append('Streaming snapshot or completion missing')
                    if 'RUN_ERROR' in names: item['issues'].append('Streaming run error')
                except Exception as exc: item['stream_error']=f'{type(exc).__name__}: {exc}'
                if item['issues']: item['status']='REVIEW'
        except Exception as exc:
            item['issues'].append(f'{type(exc).__name__}: {exc}')
        results.append(item)
        print(f"{item['status']:11} {label}: {'; '.join(item['issues']) or 'no automated smoke issues'}")
    report={'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'base':args.base,'project':args.project,'disclaimer':'SMOKE_PASS means endpoint/shape checks only, not semantic correctness or production readiness.','cases':results}
    pathlib.Path(args.out).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Report:',args.out)
    return 1 if any(x['status']=='ERROR' for x in results) else 0
if __name__=='__main__':sys.exit(main())
