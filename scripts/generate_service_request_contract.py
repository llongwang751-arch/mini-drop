"""Refresh only the service diagnosis body schema from its source model."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.app.drop_insight.managed_services import StartServiceDiagnosis

path=ROOT/'docs/contracts/openapi.v1.json'
text=path.read_text(encoding='utf-8')
document=json.loads(text)
operation=document['paths']['/api/v2/services/{service_id}/diagnoses']['post']
old=operation['requestBody']
new={'required':True,'content':{'application/json':{'schema':StartServiceDiagnosis.model_json_schema()}}}
# Keep the existing hand-authored route file's layout and other contracts.
line=next(line for line in text.splitlines() if '"requestBody"' in line and '"AUTONOMOUS", "ASSISTED"' in line)
indent=line[:len(line)-len(line.lstrip())]
text=text.replace(line,indent+'"requestBody": '+json.dumps(new,ensure_ascii=False)+',',1)
path.write_text(text,encoding='utf-8')
