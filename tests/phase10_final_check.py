#!/usr/bin/env python3
"""Read-only Phase 10 Knowledge Hub and investigation contract checks."""
import argparse, json, sys, urllib.request, urllib.error, re
from datetime import datetime, timezone

def request(base, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(base.rstrip('/')+path, data=data, headers={'Content-Type':'application/json','Accept':'application/json' if path != '/api/investigations/events' else 'text/event-stream'}, method='GET' if data is None else 'POST')
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode('utf-8')

def main():
    p=argparse.ArgumentParser(); p.add_argument('--base',default='http://127.0.0.1:8081'); p.add_argument('--project',required=True); p.add_argument('--out',default='phase10_final_report.json'); a=p.parse_args()
    report={'timestamp_utc':datetime.now(timezone.utc).isoformat(),'project':a.project,'checks':[],'notes':['Read-only checks do not establish automatic refresh, actual source correctness, or UI rendering parity.']}
    def add(name,status,details): report['checks'].append({'name':name,'status':status,'details':details})
    try:
        s=json.loads(request(a.base,'/api/knowledge-sync/status')); sources=s.get('sources',[])
        def normalize_project(value):
            return re.sub(r'/+', '/', str(value or '').strip().replace('\\', '/')).rstrip('/').casefold()
        own=[x for x in sources if normalize_project(x.get('project_path')) == normalize_project(a.project)]
        add('knowledge_sync_sources','PASS' if own else 'REVIEW',{'auto_sync_enabled':s.get('auto_sync_enabled'),'interval_seconds':s.get('interval_seconds'),'project_source_count':len(own),'project_sources':own,'all_source_count':len(sources)})
        ids=[x.get('id') for x in sources]; add('source_ids_unique','PASS' if len(ids)==len(set(ids)) else 'FAIL',{'ids':ids})
        collisions=[]
        for x in sources:
            for y in sources:
                if x is y: continue
                if x.get('location') and x.get('location')==y.get('location') and x.get('source_type')==y.get('source_type') and x.get('project_path')!=y.get('project_path') and x.get('enabled') and y.get('enabled'):
                    collisions.append([x.get('id'),y.get('id')])
        add('cross_project_folder_collisions','PASS' if not collisions else 'REVIEW',{'collisions':collisions})
        def jira_key(source):
            if source.get('source_type') != 'JIRA' or not source.get('enabled'):
                return None
            cfg=source.get('config') or {}
            if cfg.get('project_key'): return str(cfg['project_key']).upper()
            match=re.search(r'\bproject\s*=\s*[\"\']?([A-Z][A-Z0-9_]{1,20})',str(cfg.get('jql') or ''),re.I)
            return match.group(1).upper() if match else None
        jira_conflicts=[]
        for i,x in enumerate(sources):
            for y in sources[i+1:]:
                if jira_key(x) and jira_key(x)==jira_key(y) and normalize_project(x.get('project_path'))!=normalize_project(y.get('project_path')):
                    jira_conflicts.append({'jira_project':jira_key(x),'source_ids':[x.get('id'),y.get('id')],'project_paths':[x.get('project_path'),y.get('project_path')]})
        add('cross_project_jira_conflicts','REVIEW' if jira_conflicts else 'PASS',{'conflicts':jira_conflicts})
    except Exception as e: add('knowledge_sync_sources','ERROR',str(e))
    try:
        runs=json.loads(request(a.base,'/api/knowledge-sync/runs?limit=50')).get('runs',[])
        add('knowledge_sync_runs','PASS' if runs else 'REVIEW',{'count':len(runs),'recent_runs':runs[:15]})
    except Exception as e: add('knowledge_sync_runs','ERROR',str(e))
    for attr in ('accountType','email'):
        query=f'What is the impact of changing {attr}?'; payload={'query':query,'project_path':a.project}
        try:
            data=json.loads(request(a.base,'/api/investigations/analyse',payload)); result=data.get('result',{}); investigation=data.get('investigation',{})
            events_raw=request(a.base,'/api/investigations/events',payload)
            events=[]; snapshot=None
            for chunk in events_raw.split('\n\n'):
                typ=None; body=None
                for line in chunk.splitlines():
                    if line.startswith('event: '):typ=line[7:].strip()
                    if line.startswith('data: '):
                        try:body=json.loads(line[6:])
                        except json.JSONDecodeError: pass
                if typ:events.append(typ)
                if typ=='STATE_SNAPSHOT':snapshot=body
            same_source=bool(snapshot and snapshot.get('result',{}).get('source')==result.get('source'))
            same_attr=bool(snapshot and snapshot.get('result',{}).get('attribute')==result.get('attribute'))
            panel_fields=[p.get('source_field') for p in investigation.get('evidence',[]) if isinstance(p,dict)]
            ok=bool(result.get('source')=='COMBINED_ATTRIBUTE_EVIDENCE' and same_source and same_attr and 'RUN_FINISHED' in events and 'RUN_ERROR' not in events)
            add('investigation_parity_'+attr,'PASS' if ok else 'FAIL',{'source':result.get('source'),'attribute':result.get('attribute'),'snapshot_source_equal':same_source,'snapshot_attribute_equal':same_attr,'events':events,'panel_fields':panel_fields,'mapping_rows':len(result.get('mapping_rows') or []),'api_candidates':len(result.get('predictive_api_candidates') or []),'note':'This compares two backend endpoints, not rendered IntelliJ/Web UI screenshots.'})
        except Exception as e: add('investigation_parity_'+attr,'ERROR',str(e))
    counts={s:sum(x['status']==s for x in report['checks']) for s in ('PASS','REVIEW','FAIL','ERROR')};report['counts']=counts
    with open(a.out,'w',encoding='utf-8') as f:json.dump(report,f,indent=2,ensure_ascii=False,default=str)
    print(json.dumps(counts)); print('Report:',a.out)
    return 1 if counts['FAIL'] or counts['ERROR'] else 0
if __name__=='__main__':sys.exit(main())
