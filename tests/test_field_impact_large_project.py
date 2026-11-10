from services.field_impact.analyzer import analyze_project


def test_default_scans_more_than_old_1500_file_limit(tmp_path, monkeypatch):
    monkeypatch.delenv('FIELD_IMPACT_MAX_FILES', raising=False)
    for n in range(1502):
        (tmp_path / f'file_{n:04}.py').write_text('value = 1\n')
    result = analyze_project(str(tmp_path), 'value')
    assert result['files_scanned'] == 1502
    assert 'FILE_LIMIT_REACHED' not in result['warnings']


def test_explicit_limit_reports_partial_analysis(tmp_path):
    for n in range(4):
        (tmp_path / f'file_{n}.py').write_text('value = 1\n')
    result = analyze_project(str(tmp_path), 'value', max_files=2)
    assert result['files_scanned'] == 2
    assert 'FILE_LIMIT_REACHED' in result['warnings']
