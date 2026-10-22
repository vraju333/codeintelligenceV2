from pathlib import Path
from services.field_impact.analyzer import analyze_project

def test_python_multiple_writers_and_branch(tmp_path:Path):
    (tmp_path/'a.py').write_text('''def update(c):\n    if c.firstName:\n        c.fullName = c.firstName\n    else:\n        c.fullName = "unknown"\n    return c.fullName\n''')
    r=analyze_project(str(tmp_path),'fullName')
    assert len(r['writers'])==2
    assert len(r['readers'])==1
    assert r['multiple_writers']
    assert len(r['branch_qualified_references'])>=2

def test_java_setter_and_getter(tmp_path:Path):
    (tmp_path/'C.java').write_text('''class C { void f(Customer c) { c.setFullName("A"); c.getFullName(); } }''')
    r=analyze_project(str(tmp_path),'fullName')
    assert len(r['writers'])==1
    assert len(r['readers'])==1
