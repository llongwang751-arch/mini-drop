from scripts.verify_local_sre import collection_chain_checks


def test_partial_tool_success_does_not_pass_chain():
    result = collection_chain_checks(
        [{'status': 'COMPLETED', 'task_id': 'one'}, {'status': 'FAILED', 'task_id': 'two'}],
        [{'task_id': 'one', 'integrity_status': 'VERIFIED', 'size_bytes': 10}],
        [{'classification': {'decision': 'ACCEPT_SUPPORT'}}])
    assert not result['all_tools_completed']
    assert not result['every_task_has_verified_artifact']


def test_empty_or_rejected_evidence_does_not_pass_chain():
    assert not all(collection_chain_checks([], [], []).values())
    result = collection_chain_checks(
        [{'status': 'COMPLETED', 'task_id': 'one'}],
        [{'task_id': 'one', 'integrity_status': 'VERIFIED', 'size_bytes': 10}],
        [{'classification': {'decision': 'REJECT'}}])
    assert not result['accepted_evidence_present']


def test_complete_chain_passes_without_claiming_root_cause():
    result = collection_chain_checks(
        [{'status': 'COMPLETED', 'task_id': 'one'}],
        [{'task_id': 'one', 'integrity_status': 'VERIFIED', 'size_bytes': 10}],
        [{'classification': {'decision': 'ACCEPT_NEUTRAL'}}])
    assert all(result.values())
