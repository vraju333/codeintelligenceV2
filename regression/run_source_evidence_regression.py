"""Run the source-evidence ASK smoke regression once against a running backend.

Requires only Python stdlib. Exits nonzero on any transport or smoke assertion failure.
Smoke checks are NOT a replacement for source-grounded correctness review.
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base-url', default='http://127.0.0.1:8081')
    p.add_argument('--project-path', required=True)
    p.add_argument('--timeout', type=int, default=90)
    p.add_argument('--output', default='source_evidence_regression_results.json')
    args = p.parse_args()
    cases = json.loads((Path(__file__).parent/'source_evidence_regression_cases.json').read_text(encoding='utf-8'))
    assert len(cases) == 29
    results=[]
    for case in cases:
        data=json.dumps({'query':case['question'],'project_path':args.project_path}).encode('utf-8')
        request=Request(args.base_url.rstrip('/')+'/api/knowledge-agent/ask',data=data,
                        headers={'Content-Type':'application/json'},method='POST')
        try:
            with urlopen(request,timeout=args.timeout) as response:
                payload=json.loads(response.read().decode('utf-8'))
            answer=str(payload.get('answer') or '')
            passed=bool(answer.strip()) and (not case.get('must_contain_any') or
                    any(x.lower() in answer.lower() for x in case['must_contain_any'])) and not any(
                    x.lower() in answer.lower() for x in case.get('must_not_contain',[]))
            item={'id':case['id'],'passed':passed,'source':payload.get('source'),
                  'status':payload.get('status'),'answer':answer}
        except (URLError,TimeoutError,ValueError) as exc:
            item={'id':case['id'],'passed':False,'error':f'{type(exc).__name__}: {exc}'}
        results.append(item)
        print(f"{'PASS' if item['passed'] else 'FAIL'} {case['id']}",flush=True)
    Path(args.output).write_text(json.dumps({'note':'Smoke assertions only; manual semantic evaluation still required for claims not covered by assertions.',
                                              'passed':sum(x['passed'] for x in results),'total':len(results),
                                              'results':results},indent=2),encoding='utf-8')
    print(f"{sum(x['passed'] for x in results)}/{len(results)} smoke checks passed. Report: {args.output}")
    return 0 if all(x['passed'] for x in results) else 1

if __name__=='__main__': sys.exit(main())
