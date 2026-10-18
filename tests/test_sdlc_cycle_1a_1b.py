import sys, json
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
sys.path.insert(0,'/mnt/data/cyclework/temp')
from database import Base
from db_models import KnowledgeDocument
from sdlc_models import SdlcTestExecution,SdlcRelease,SdlcReleaseSnapshot
from services.agent.sdlc_report_service import unique_cases, map_tests, release_report
from routers.sdlc_report_router import capture_baseline, CaptureRequest, compare_baselines
engine=create_engine('sqlite://')
Base.metadata.create_all(engine)
with Session(engine) as db:
 db.add(KnowledgeDocument(source_type='TEST',title='qa.csv',content='x',project_path='D:/sample',source_ref='jira:CAS-2:attachment:10000',status='ACTIVE',metadata_json=json.dumps({'source_kind':'JIRA','jira':'CAS-2','cases':[{'test_id':'TC1','jira':'CAS-2','scenario':'create customer','operation':'create','expected':'ok'}]})))
 db.add(SdlcRelease(project_path='D:/sample',release_key='OCT',jira_keys_json='["CAS-2"]'))
 db.commit()
 with patch('services.agent.sdlc_report_service.JiraCommitTraceService') as cls,patch('services.agent.sdlc_report_service.analyse_commit_paths',return_value={'commits':[]}):
  cls.return_value.analyse.return_value={'status':'NO_EXPLICIT_COMMIT_LINK','commits':[]}
  result=release_report(db,db.query(SdlcRelease).first())
  assert result['summary']['unique_jira_test_pairs']==1
  first=capture_baseline('OCT',CaptureRequest(project_path='D:/sample',captured_by='tester'),db)
  second=capture_baseline('OCT',CaptureRequest(project_path='D:/sample',captured_by='tester'),db)
  assert first['status']=='CAPTURED' and second['status']=='UNCHANGED',(first,second)
  db.add(SdlcTestExecution(project_path='D:/sample',jira_key='CAS-2',test_id='TC1',status='PASSED',git_revision='a'*40,evidence_ref='ci://test',executed_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc),recorded_by='tester'))
  db.commit()
  third=capture_baseline('OCT',CaptureRequest(project_path='D:/sample',captured_by='tester'),db)
  assert third['version']==2
  delta=compare_baselines('OCT',1,2,'D:/sample',db)
  assert delta['tests_changed'][0]['after_status']=='PASSED'
  print('PASS: report, QA ingestion, dedup, unchanged baseline, new version on execution, baseline comparison')
