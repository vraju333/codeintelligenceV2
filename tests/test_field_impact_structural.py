from pathlib import Path
from services.field_impact.structural_analysis import structural_evidence
from services.field_impact.git_sync import _fields

def test_structural_candidates_are_not_verified(tmp_path):
    (tmp_path/'Demo.java').write_text('public class Demo {\n public void alpha() { beta(); }\n public void beta() { if (true) { } }\n}')
    result={'project_path':str(tmp_path),'field':'firstName','writers':[{'file':'Demo.java','method':'alpha','kind':'WRITE'}], 'readers':[], 'declarations':[], 'limitations':[]}
    out=structural_evidence(result)
    assert any(e['callee']=='beta' and not e['verified_cross_method'] for e in out['candidate_call_edges'])
    assert out['analysis_capabilities']['cfg']=='NOT_FULLY_RESOLVED'

def test_field_discovery(tmp_path):
    (tmp_path/'Demo.java').write_text('class Demo { private String firstName; private int age = 2; }')
    from unittest.mock import patch
    with patch('services.field_impact.git_sync.settings.FIELD_SYNC_MAX_FIELDS',40):
        assert _fields(tmp_path,['Demo.java'])==['age','firstName']
