"""用合成数据库和存储适配器验证有限批次对账、完整性检查与孤儿保护。"""

import hashlib
import unittest
from datetime import datetime, timezone, timedelta
from reconcile import reconcile


class Repository:
    def __init__(self):
        self.expiring = []; self.ready = []; self.references = set(); self.states = {}; self.events = []
    def expired(self, limit): return self.expiring
    def candidates(self, limit): return self.ready
    def referenced(self, key): return key in self.references
    def set_status(self, item, state): self.states[item['id']] = state
    def audit(self, action, identifier): self.events.append((action, identifier))


class Storage:
    def __init__(self): self.objects = {}; self.fail_delete = False; self.removed = []
    def remove(self, key):
        if self.fail_delete: raise OSError('simulated unavailable')
        self.objects.pop(key, None); self.removed.append(key)
    def stat(self, key):
        if key not in self.objects: return None
        return len(self.objects[key][0])
    def chunks(self, key): yield self.objects[key][0]
    def list(self):
        for key, (_, modified) in list(self.objects.items()): yield key, modified


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(); self.storage = Storage(); self.now = datetime.now(timezone.utc)
    def test_failed_deletion_never_marks_expired_and_retry_is_idempotent(self):
        self.repo.expiring = [{'id': 1, 'object_key': 'raw/1/profile'}]
        self.storage.fail_delete = True
        result = reconcile(self.repo, self.storage, now=self.now)
        self.assertEqual(self.repo.states[1], 'deleting'); self.assertEqual(result['errors'], 1)
        self.storage.fail_delete = False
        reconcile(self.repo, self.storage, now=self.now)
        self.assertEqual(self.repo.states[1], 'expired')
    def test_orphans_require_grace_known_prefix_and_live_reference_recheck(self):
        old = self.now - timedelta(days=2)
        self.storage.objects = {'raw/orphan': (b'bad', old), 'raw/active': (b'x', old), 'binary/orphan': (b'x', old), 'binary/active': (b'x', old), 'binary/recent': (b'x', self.now), 'result/recent': (b'x', self.now), 'foreign/file': (b'x', old)}
        self.repo.references.update({'raw/active', 'binary/active'})
        reconcile(self.repo, self.storage, now=self.now)
        self.assertEqual(self.storage.removed, ['raw/orphan', 'binary/orphan'])
    def test_hash_corruption_quarantines_without_destroying_forensic_input(self):
        self.repo.ready = [{'id': 2, 'object_key': 'raw/2', 'size': 4, 'sha256': hashlib.sha256(b'good').hexdigest()}]
        self.repo.references.add('raw/2'); self.storage.objects['raw/2'] = (b'evil', self.now)
        reconcile(self.repo, self.storage, now=self.now)
        self.assertEqual(self.repo.states[2], 'quarantined'); self.assertFalse(self.storage.removed)
    def test_missing_object_revokes_ready_status(self):
        self.repo.ready = [{'id': 3, 'object_key': 'result/missing', 'size': 4, 'sha256': '0'*64}]
        reconcile(self.repo, self.storage, now=self.now)
        self.assertEqual(self.repo.states[3], 'missing')


if __name__ == '__main__': unittest.main()
