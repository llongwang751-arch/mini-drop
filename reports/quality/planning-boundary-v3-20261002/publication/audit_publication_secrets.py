"""Read-only in-memory secret comparison; never persist or print credential values."""
from pathlib import Path
import argparse
import importlib.util
import json
import re
ROOT=Path(__file__).resolve().parents[3]
STAGE=Path(__file__).resolve().parent
REPORT=ROOT/'reports/quality/planning-boundary-v3-20261002'
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
 assert not args.output.exists()
 provider=load('publication_auth',ROOT/'output/acceptance/deployment-20260930/run_strict.py')
 remote_code="import json,subprocess,re\nrows=json.loads(subprocess.check_output(['docker','inspect',*subprocess.check_output(['docker','ps','-q'],text=True).split()]))\nvalues=[]\nfor c in rows:\n for pair in c.get('Config',{}).get('Env',[]):\n  k,_,v=pair.partition('=')\n  if re.search('KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL',k,re.I) and len(v)>=8 and not k.endswith('_FILE') and not (k=='AGENT_GRPC_CLIENT_KEY' and v.startswith('/')):values.append(v)\nprint(json.dumps(values))\n"
 secrets=set(json.loads(provider.remote(remote_code)))
 secrets.add(provider.authenticated_client()._key)
 gh=load('publication_gh',ROOT/'output/quality/ci-validation-20260927/github_ci.py').session()
 authorization=gh.headers.get('Authorization','');token=authorization.partition(' ')[2]
 if len(token)>=8:secrets.add(token)
 files=[p for p in REPORT.rglob('*') if p.is_file()]
 patterns=(rb'ghp_[A-Za-z0-9]{36,}',rb'github_pat_[A-Za-z0-9_]{50,}',rb'sk-[A-Za-z0-9]{24,}',rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
 issues=[]
 for p in files:
  raw=p.read_bytes()
  if any(v.encode() in raw for v in secrets if len(v)>=8) or any(re.search(pattern,raw) for pattern in patterns):issues.append(p.relative_to(ROOT).as_posix())
 receipt={'status':'VERIFIED' if not issues else 'REVIEW_REQUIRED','scope':'Actual live container/API/Git credentials compared in memory only; known secret shapes; no credential values persisted','files_scanned':len(files),'distinct_actual_values_checked':len(secrets),'file_reference_environment_values_excluded':True,'flagged_paths':issues}
 args.output.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8');print(json.dumps(receipt))
 assert not issues,'credential-shaped publication requires review'
if __name__=='__main__':main()
