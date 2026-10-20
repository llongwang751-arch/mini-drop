"""Real isolated PostgreSQL/S3 deletion, corruption, orphan and lifecycle tests."""
import hashlib
import io
import os
from pathlib import Path
import unittest
from datetime import timedelta

from reconcile import reconcile


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'explicit isolated fixture required')
class StorageIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        from minio import Minio
        from maintenance import SQLRepository, S3Storage, configure_lifecycle
        dsn = os.environ['TEST_DATABASE_URL']
        if '/drop_ops_test?' not in dsn: raise RuntimeError('Refusing non-fixture database')
        cls.conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        for file in sorted(Path('/migrations').glob('*.sql')): cls.conn.execute(file.read_text())
        cls.client = Minio(os.environ['TEST_S3_ENDPOINT'],access_key=os.environ['TEST_S3_ACCESS_KEY'],secret_key=os.environ['TEST_S3_SECRET_KEY'],secure=False)
        cls.bucket = 'drop-ops-isolated-test'; cls.client.make_bucket(cls.bucket)
        configure_lifecycle(cls.client,cls.bucket)
        cls.repo = SQLRepository(cls.conn); cls.storage = S3Storage(cls.client,cls.bucket)
        cls.conn.execute("INSERT INTO users(id,username,display_name,role) VALUES('ops-fixture','ops-fixture','Isolated ops fixture','admin')")
        cls.task = cls.conn.execute("INSERT INTO tasks(name,kind,creator_id,request_id,collection_status,analysis_status) VALUES('Ops isolated fixture','IMPORT_COLLAPSED','ops-fixture','test','COLLECTED','SUCCESS') RETURNING id").fetchone()['id']
    @classmethod
    def tearDownClass(cls): cls.conn.close()
    def artifact(self,key,body,*,expired=False):
        row = self.conn.execute("INSERT INTO artifacts(task_id,kind,name,object_key,size,sha256,created_at,expires_at) VALUES(%s,'RAW','fixture',%s,%s,%s,now()-interval '10 minutes',now()+%s::interval) RETURNING *",(self.task,key,len(body),hashlib.sha256(body).hexdigest(),'-1 day' if expired else '1 day')).fetchone()
        self.client.put_object(self.bucket,key,io.BytesIO(body),len(body))
        return row
    def test_real_lifecycle_and_reconciliation(self):
        rules = self.client.get_bucket_lifecycle(self.bucket).rules
        self.assertIn(len(rules),(6,7))
        expired = self.artifact('raw/expired',b'expired',expired=True)
        missing = self.artifact('raw/missing',b'missing'); self.client.remove_object(self.bucket,missing['object_key'])
        corrupt = self.artifact('raw/corrupt',b'original'); self.client.put_object(self.bucket,corrupt['object_key'],io.BytesIO(b'corrupt!'),8)
        ready = self.artifact('raw/ready',b'verified')
        self.client.put_object(self.bucket,'raw/orphan',io.BytesIO(b'orphan'),6)
        self.client._create_multipart_upload(self.bucket,'raw/abandoned',{})
        result = reconcile(self.repo,self.storage,orphan_grace=timedelta(seconds=0),multipart_grace=timedelta(seconds=0))
        states = {r['id']:r for r in self.conn.execute('SELECT id,status,download_allowed FROM artifacts').fetchall()}
        self.assertEqual(states[expired['id']]['status'],'expired'); self.assertIsNone(self.storage.stat(expired['object_key']))
        self.assertEqual(states[missing['id']]['status'],'missing')
        self.assertEqual(states[corrupt['id']]['status'],'quarantined'); self.assertFalse(states[corrupt['id']]['download_allowed'])
        self.assertEqual(states[ready['id']]['status'],'ready'); self.assertIsNotNone(self.storage.stat(ready['object_key']))
        self.assertIsNone(self.storage.stat('raw/orphan')); self.assertEqual(result['errors'],0)
        self.assertEqual(result['multipart_aborted'],1)
        self.assertEqual(list(self.storage.multipart()),[])
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM audit_logs').fetchone()['n'],5)
        # A repeated pass neither invents additional events nor deletes valid inputs.
        again = reconcile(self.repo,self.storage,orphan_grace=timedelta(seconds=0))
        self.assertEqual(again['expired'],0); self.assertEqual(again['orphans'],0)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM audit_logs').fetchone()['n'],5)


if __name__=='__main__': unittest.main()
