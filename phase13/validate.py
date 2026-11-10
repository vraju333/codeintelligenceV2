#!/usr/bin/env python3
"""Read-only by default Phase 13 smoke, evidence, load and reliability validator."""
import argparse, concurrent.futures, datetime, json, pathlib, statistics, time, urllib.error, urllib.parse, urllib.request

def normalize(s):
    return str(s or '').replace('\\\\', '/').replace('\\', '/').rstrip('/').casefold()

def request(base, method, route, payload=None, timeout=25):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(base.rstrip('/') + route, data=data, method=method,
                                 headers={'Content-Type':'application/json','Cache-Control':'no-cache'})
    start=time.perf_counter()
    try:
        with urllib.request.urlopen(req,timeout=timeout) as resp:
            raw=resp.read(); code=resp.status
        body=json.loads(raw.decode()) if raw else {}
        return {'ok':200<=code<300,'code':code,'ms':round((time.perf_counter()-start)*1000,2),'body':body}
    except urllib.error.HTTPError as e:
        return {'ok':False,'code':e.code,'ms':round((time.perf_counter()-start)*1000,2),'error':e.read(500).decode(errors='replace')}
    except Exception as e:
        return {'ok':False,'code':None,'ms':round((time.perf_counter()-start)*1000,2),'error':str(e)}

def check(name, passed, detail, severity='FAIL'):
    return {'check':name,'result':'PASS' if passed else severity,'detail':detail}

def main():
    ap=argparse.ArgumentParser(description='CodeIntelligence Phase 13 validation (read-only unless --sync is used)')
    ap.add_argument('--base',default='http://127.0.0.1:8081')
    ap.add_argument('--project',default=r'D:\AIlearning\customer-account-service')
    ap.add_argument('--expected',default=str(pathlib.Path(__file__).with_name('expected_country_code.json')))
    ap.add_argument('--output',default='phase13-results')
    ap.add_argument('--requests',type=int,default=30)
    ap.add_argument('--workers',type=int,default=5)
    ap.add_argument('--sync',action='store_true',help='Opt in to running registered source sync twice; may write to DB')
    args=ap.parse_args(); checks=[]; responses={}
    q=urllib.parse.urlencode({'project_path':args.project})
    endpoints={'mapping_documents':f'/api/mapping-intelligence/documents?{q}&sync=false',
               'test_documents':f'/api/test-suites/documents?{q}&sync=false',
               'knowledge_sources':f'/api/knowledge-sync/status?{q}',
               'impact':'/api/requirement-impact/analyze'}
    for name,route in endpoints.items():
        method='POST' if name=='impact' else 'GET'
        payload={'mode':'attribute','value':'countryCode','project':args.project} if name=='impact' else None
        r=request(args.base,method,route,payload);responses[name]=r
        checks.append(check('API '+name,r['ok'],f"HTTP {r['code']}; {r['ms']} ms; {r.get('error','')}"))
    mapping=responses['mapping_documents'].get('body',{}); docs=mapping.get('documents',[]) if isinstance(mapping,dict) else []
    if responses['mapping_documents']['ok']:
        checks.append(check('mapping project isolation',all(normalize(d.get('project_path'))==normalize(args.project) for d in docs),f'{len(docs)} mapping records'))
        checks.append(check('mapping documents present',len(docs)>0,f'{len(docs)} records; {len({d.get("filename") for d in docs})} unique filenames'))
        checks.append(check('mapping row evidence',any(int(d.get('row_count') or 0)>0 for d in docs),'At least one imported mapping has rows'))
    tests=responses['test_documents'].get('body',{})
    if responses['test_documents']['ok']:
        checks.append(check('test documents returned',isinstance(tests,dict),f'keys: {list(tests)[:12] if isinstance(tests,dict) else "unexpected payload"}'))
    impact=responses['impact'].get('body',{})
    if responses['impact']['ok']:
        checks.append(check('impact response structure',isinstance(impact,dict) and ('attributes' in impact or 'mappings' in impact),f'keys: {list(impact)[:20] if isinstance(impact,dict) else "unexpected payload"}'))
    expected=json.loads(pathlib.Path(args.expected).read_text(encoding='utf-8'))
    # Ground truth must be confirmed by a human. An empty/unapproved expectation is not a PASS.
    if not expected.get('approved'):
        checks.append(check('ground truth approved',False,'Expected evidence is a draft. Verify actual code/JIRA/test IDs and set approved=true.','NOT_EVALUATED'))
    else:
        observed=json.dumps(impact,ensure_ascii=False).casefold()
        for term in expected.get('must_mention',[]):
            checks.append(check('impact evidence '+term,term.casefold() in observed, 'Exact evidence term appears in API JSON'))
        for term in expected.get('must_not_mention',[]):
            checks.append(check('impact isolation '+term,term.casefold() not in observed,'Forbidden evidence term absent'))
        positives=[x for x in expected.get('must_mention',[]) if x.casefold() in observed]
        checks.append(check('expected-term recall',len(positives)==len(expected.get('must_mention',[])),f'{len(positives)}/{len(expected.get("must_mention",[]))} expected terms found'))
    # Bounded concurrent GET test. Never use an import/sync endpoint for load testing.
    route=endpoints['mapping_documents']; n=max(1,min(args.requests,500)); workers=max(1,min(args.workers,30))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        load=list(pool.map(lambda _:request(args.base,'GET',route,timeout=35),range(n)))
    lat=[r['ms'] for r in load]; failures=[r for r in load if not r['ok']]
    s=sorted(lat); p95=s[min(len(s)-1,int(.95*(len(s)-1)))]; summary={'requests':n,'workers':workers,'success':n-len(failures),'failed':len(failures),'p50_ms':statistics.median(lat),'p95_ms':p95,'max_ms':max(lat)}
    checks.append(check('concurrent API reliability',not failures,f'{n-len(failures)}/{n} HTTP successes'))
    checks.append(check('mapping API p95 <= 2000ms',p95<=2000,f'p95={p95}ms (local development target, adjust for hardware)'))
    # Repeat reads to verify stable responses without changing DB.
    after=request(args.base,'GET',route)
    if responses['mapping_documents']['ok'] and after['ok']:
        first={(d.get('id'),d.get('filename'),d.get('row_count')) for d in docs}
        second={(d.get('id'),d.get('filename'),d.get('row_count')) for d in after.get('body',{}).get('documents',[])}
        checks.append(check('read-only result stability',first==second,f'{len(first)} records before, {len(second)} after'))
    if args.sync:
        src=responses['knowledge_sources'].get('body',{})
        candidates=src.get('sources',[]) if isinstance(src,dict) else []
        if not isinstance(candidates,list): candidates=[]
        candidates=[x for x in candidates if normalize(x.get('project_path'))==normalize(args.project) and x.get('enabled',True) and 'FOLDER' in str(x.get('source_type','')).upper()]
        for source in candidates:
            sid=source.get('id'); a=request(args.base,'POST',f'/api/knowledge-sync/sources/{sid}/sync'); b=request(args.base,'POST',f'/api/knowledge-sync/sources/{sid}/sync')
            checks.append(check(f'sync source {sid} twice',a['ok'] and b['ok'],f'first={a.get("body",a.get("error"))}; second={b.get("body",b.get("error"))}'))
    out=pathlib.Path(args.output);out.mkdir(parents=True,exist_ok=True)
    report={'generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'project':args.project,'base':args.base,'checks':checks,'load':summary,'api_responses':responses}
    (out/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False,default=str),encoding='utf-8')
    lines=['# CodeIntelligence Phase 13 results','',f'Project: `{args.project}`','', '| Check | Result | Details |','|---|---|---|']
    for c in checks: lines.append('| '+ ' | '.join(str(c[k]).replace('|','/').replace('\n',' ')[:220] for k in ('check','result','detail'))+' |')
    lines+=['','## Load test',f'Concurrent requests: {n}; workers: {workers}; p50: {summary["p50_ms"]} ms; p95: {p95} ms; failures: {len(failures)}','', 'No results have been independently verified against the local Java repository.']
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'.join(f'{c["result"]:14} {c["check"]}: {c["detail"]}' for c in checks));print('Reports:',out.resolve())
    return 1 if any(c['result']=='FAIL' for c in checks) else 0
if __name__=='__main__':raise SystemExit(main())
