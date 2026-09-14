"""Generate the public measurement schemas from the Pydantic source contract."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from server.app.drop_insight.business_acceptance import MeasurementWindow,AcceptancePolicy

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    for name,model in [('business-measurement',MeasurementWindow),('business-acceptance-policy',AcceptancePolicy)]:
        path=ROOT/'docs/contracts'/(name+'.schema.json')
        content=json.dumps(model.model_json_schema(),ensure_ascii=False,indent=2)+'\n'
        if args.check:
            if not path.exists() or path.read_text(encoding='utf-8')!=content:raise SystemExit(f'contract drift: {path.name}')
        else:path.write_text(content,encoding='utf-8')
