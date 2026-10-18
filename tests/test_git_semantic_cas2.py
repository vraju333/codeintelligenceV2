import sys,types,tempfile,subprocess
from pathlib import Path
sys.modules['config']=types.SimpleNamespace(settings=types.SimpleNamespace(JAVA_PROJECT_PATH=None))
from services.regression.git_diff_service import GitDiffService
with tempfile.TemporaryDirectory() as d:
 p=Path(d); subprocess.run(['git','init','-q'],cwd=p,check=True);subprocess.run(['git','config','user.email','test@example.com'],cwd=p);subprocess.run(['git','config','user.name','Test'],cwd=p)
 f=p/'CustomerService.java';f.write_text('class CustomerService {\n private static final Pattern COUNTRY = Pattern.compile("^[A-Z]{2,3}$");\n public void get(){return;}\n public void validate(){ if(!COUNTRY.matcher("US").matches()) throw new RuntimeException("2 or 3");}\n}\n')
 subprocess.run(['git','add','.'],cwd=p,check=True);subprocess.run(['git','commit','-qm','initial'],cwd=p,check=True)
 f.write_text('class CustomerService {\n private static final Pattern COUNTRY =\n Pattern.compile("^[A-Z]{2}$");\n public void get() { return; }\n public void validate(){ if(!COUNTRY.matcher("US").matches()) throw new RuntimeException("exactly 2");}\n}\n')
 from config import settings
 settings.JAVA_PROJECT_PATH=str(p)
 result=GitDiffService().analyse_changes()['changed_files'][0]
 print(result['semantic_analysis']);print(result['changed_methods'])
 assert [m['method_name'] for m in result['changed_methods']]==['validate']
 assert result['semantic_analysis']['constant_changes'][0]['symbol']=='COUNTRY'
