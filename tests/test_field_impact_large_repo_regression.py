from services.field_impact import analyzer


def test_unrelated_java_parse_error_does_not_block_field(tmp_path, monkeypatch):
    (tmp_path / 'Broken.java').write_text('class Broken { ??? }')
    (tmp_path / 'Relevant.java').write_text('class Relevant { int AALTO_PRESENT; }')
    called = []

    def fake_parse(path, field):
        called.append(path.name)
        return ([{'kind': 'DECLARATION', 'file': str(path), 'line': 1,
                  'field': field, 'method': '', 'detail': field}], False)

    monkeypatch.setattr(analyzer, 'analyze_java', fake_parse)
    result = analyzer.analyze_project(str(tmp_path), 'AALTO_PRESENT')
    assert called == ['Relevant.java']
    assert result['files_scanned'] == 2
    assert result['warnings'] == []
    assert len(result['declarations']) == 1


def test_relevant_java_parse_error_remains_visible(tmp_path, monkeypatch):
    (tmp_path / 'Relevant.java').write_text('class Relevant { int AALTO_PRESENT; ??? }')
    monkeypatch.setattr(analyzer, 'analyze_java', lambda path, field: ([], True))
    result = analyzer.analyze_project(str(tmp_path), 'AALTO_PRESENT')
    assert any(w.startswith('PARSE_ERROR:') for w in result['warnings'])


def test_accessor_only_java_file_is_analyzed(tmp_path, monkeypatch):
    (tmp_path / 'Access.java').write_text('class Access { void f() { x.getAalto_present(); } }')
    called = []
    monkeypatch.setattr(analyzer, 'analyze_java', lambda path, field: (called.append(path.name) or [], False))
    analyzer.analyze_project(str(tmp_path), 'aalto_present')
    assert called == ['Access.java']
