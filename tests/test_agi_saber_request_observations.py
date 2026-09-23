import importlib.util
import json
from pathlib import Path


def test_office_store_is_bounded_and_content_free(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'integrations' / 'agi_saber' / 'request_observations.py'
    spec = importlib.util.spec_from_file_location('office_request_observations', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / 'observations.json'
    store = module.OfficeObservationStore(path, limit=2)
    for i in range(3):
        store.append({'request_id': f'{i:032x}', 'stage_ms': {'retrieval_ms': i}})
    payload = json.loads(path.read_text(encoding='utf-8'))
    assert payload['schema_version'] == 'mini-drop.office-observations.v1'
    assert [row['request_id'] for row in payload['records']] == [f'{i:032x}' for i in (1, 2)]
    assert 'query' not in path.read_text(encoding='utf-8')
