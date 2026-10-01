"""New code must preserve the pinned pre-seven campaign's historical results."""
import hashlib
import json
from pathlib import Path

from scripts.build_engineering_diagnosis import generate


ROOT=Path(__file__).resolve().parents[1]
FROZEN=ROOT/'tests/fixtures/engineering_diagnosis_before_seven_gaps.json'
FROZEN_SHA='534f96c9a48c180419657afe3e44ceb96fb89aad837d950cd213eee6a29aca56'


def test_frozen_pre_seven_contract_and_real_pinned_evidence_keep_the_previous_score(monkeypatch):
    original=FROZEN.read_bytes()
    assert hashlib.sha256(original).hexdigest()==FROZEN_SHA
    before_contract=json.loads(original)
    assert 'current_campaign_id' not in before_contract
    live_contract=(ROOT/'contracts/engineering_diagnosis.json').resolve()
    actual_read_text=Path.read_text
    intercepted=[]

    def frozen_contract_only(path, *args, **kwargs):
        if path.resolve()==live_contract:
            intercepted.append(path)
            return original.decode('utf-8')
        return actual_read_text(path,*args,**kwargs)

    # Only the selected source contract changes. The generator reads all
    # historical manifests, case bytes and raw files from their real paths,
    # and checks every original SHA without copied fixtures or symlinks.
    monkeypatch.setattr(Path,'read_text',frozen_contract_only)
    document=json.loads(generate(ROOT))
    assert len(intercepted)==1
    assert document['diagnosis_accepted']==14
    assert document['localization_accepted']==4 and document['refuted']==5
    assert document['registered_scenarios']==document['evaluated_scenarios']==21
    assert document['evaluation_mode']=='MIXED_LIVE_CAMPAIGNS'
    assert document['fresh_live_scenarios']==18 and document['regraded_prior_scenarios']==3
    assert document['fresh_live_run'] is False and document['not_evaluated']==[]
    assert 'current_campaign_id' not in document
    assert FROZEN.read_bytes()==original
