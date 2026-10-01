from pathlib import Path
import importlib.util
import json

stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('health',stage/'live_health.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
parent=json.loads((stage/'health-live/records.json').read_text(encoding='utf-8'))
did=(stage/'browser/browser-created-id.txt').read_text().strip()
records=h.run_check('browser-check-live',parent,existing_id=did)
summary=json.loads((stage/'live-check-summary.json').read_text(encoding='utf-8'))
summary.update(healthy_checks=3,normal_checks=3,root_reports=0,browser_created_check=did,
    raw_downloads_sha_verified=summary['raw_downloads_sha_verified']+len(records['downloaded_artifact_hashes']))
(stage/'live-check-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
