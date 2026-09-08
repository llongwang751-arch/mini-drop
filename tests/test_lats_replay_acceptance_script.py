from scripts.verify_lats_replay_showcase import _snapshot


def test_acceptance_reads_snapshot_proof_from_folded_search_projection() -> None:
    tree = {
        "search": {
            "environment_semantics": "FROZEN_REPLAY",
            "snapshot": {
                "snapshot_id": "snapshot-v1",
                "snapshot_digest": "a" * 64,
                "reset_count": 4,
                "frozen": True,
            },
        }
    }

    assert _snapshot(tree, {}) == {
        "snapshot_id": "snapshot-v1",
        "snapshot_digest": "a" * 64,
        "reset_count": 4,
        "frozen": True,
    }
