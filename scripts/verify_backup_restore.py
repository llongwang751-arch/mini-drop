"""Linux Docker-host recovery drill. Production is read-only; retain all backups.

Restore into a fresh test database and bucket. A PostgreSQL exported snapshot
keeps counts consistent with pg_dump even while diagnoses continue to write.
Object bytes are backed up and SHA-256 checked after restore, not just counted.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def run(args, **kwargs):
    return subprocess.check_output(args, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-container", default="mini-drop-control-postgres-1")
    parser.add_argument("--worker-container", default="mini-drop-control-diagnosis-worker-1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    db = "mini_drop_restore_test_" + stamp
    bucket = "mini-drop-restore-test-" + stamp
    pg = args.postgres_container
    inspected = json.loads(run(["docker", "inspect", pg]))[0]
    env = dict(x.split("=", 1) for x in inspected["Config"]["Env"])
    user, source_db = env["POSTGRES_USER"], env["POSTGRES_DB"]
    base = ["docker", "exec", "-i", pg]
    psql = [*base, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-qAt", "-U", user]
    keeper = subprocess.Popen([*psql, "-d", source_db], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    def query(sql):
        keeper.stdin.write(sql + "\n"); keeper.stdin.flush()
        row = keeper.stdout.readline().strip()
        if not row: raise RuntimeError("snapshot transaction failed")
        return row
    try:
        snapshot = query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT pg_export_snapshot();")
        if not re.fullmatch(r"[A-Fa-f0-9-]+", snapshot): raise RuntimeError("invalid snapshot ID")
        tables = json.loads(query("SELECT json_agg(tablename ORDER BY tablename) FROM pg_tables WHERE schemaname='public';"))
        counts = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            counts[table] = int(query(f"SELECT count(*) FROM public.{quoted};"))
        dump = output / "postgres.dump"
        with dump.open("xb") as handle:
            subprocess.run([*base, "pg_dump", "-U", user, "-d", source_db, "-Fc", "--snapshot=" + snapshot],
                           stdout=handle, check=True)
        os.chmod(dump, 0o600)
    finally:
        if keeper.poll() is None:
            keeper.stdin.write("ROLLBACK;\n\\q\n"); keeper.stdin.flush()
        keeper.communicate(timeout=15)
    subprocess.run([*base, "createdb", "-U", user, db], check=True)
    with dump.open("rb") as handle:
        subprocess.run([*base, "pg_restore", "-U", user, "-d", db, "--no-owner", "--no-privileges", "--exit-on-error"],
                       stdin=handle, check=True)
    restored = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        restored[table] = int(run([*psql, "-d", db, "-c", f"SELECT count(*) FROM public.{quoted};"]))
    assert counts == restored, "PostgreSQL restored row counts differ from dump snapshot"
    object_code = '''import os,json,hashlib,pathlib
from server.app.storage import _client
m=_client(request_timeout_seconds=30)
source=os.environ.get('MINIO_BUCKET','mini-drop')
target=__BUCKET__
folder=pathlib.Path('/tmp')/target;folder.mkdir(mode=0o700)
objects=list(m.list_objects(source,recursive=True))
assert sum(x.size for x in objects)<1024**3, 'drill exceeds 1GiB bound'
assert not m.bucket_exists(target), 'refuse to reuse restore bucket'
m.make_bucket(target)
manifest=[]
for i,obj in enumerate(objects):
 p=folder/str(i)
 m.fget_object(source,obj.object_name,str(p))
 before=hashlib.sha256(p.read_bytes()).hexdigest()
 m.fput_object(target,obj.object_name,str(p))
 response=m.get_object(target,obj.object_name)
 try: after=hashlib.sha256(response.read()).hexdigest()
 finally: response.close();response.release_conn()
 assert before==after, 'restored object digest mismatch'
 manifest.append({'object_key':obj.object_name,'file':str(i),'size_bytes':p.stat().st_size,'sha256':before})
assert len(list(m.list_objects(target,recursive=True)))==len(manifest)
(folder/'manifest.json').write_text(json.dumps(manifest))
print(json.dumps({'source_bucket':source,'restore_bucket':target,'objects':len(manifest),'bytes':sum(x['size_bytes'] for x in manifest),'sha256_verified':True,'backup_directory':str(folder)}))
'''.replace("__BUCKET__", repr(bucket))
    objects = json.loads(run(["docker", "exec", "-i", args.worker_container, "python", "-"],
                             input=object_code.encode()))
    subprocess.run(["docker", "cp", args.worker_container + ":" + objects["backup_directory"],
                    str(output / "minio")], check=True)
    result = {"passed": True, "generated_at": datetime.now(timezone.utc).isoformat(),
              "source_database": source_db, "restored_database": db, "snapshot": snapshot,
              "table_counts": counts, "restored_table_counts": restored,
              "dump_sha256": hashlib.sha256(dump.read_bytes()).hexdigest(), "objects": objects,
              "retained_test_resources": True,
              "boundary": "Isolated restore drill on same host; not disaster-site failover or a long-term backup schedule."}
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"passed": True, "output": str(output), "tables": len(tables), "objects": objects}))


if __name__ == "__main__":
    main()
