#!/usr/bin/env python3
"""在隔离 PostgreSQL 环境验证新旧 Agent/Server 两个方向的真实协议兼容。

使用保留的 V1 发布镜像，执行真实 PROC_CPU，将 PUT 字节与已提交 SHA 核对，
并记录能力协商与故障恢复证据。
"""
import hashlib,json,os,pathlib,re,secrets,subprocess,sys,time
ROOT=pathlib.Path(__file__).resolve().parents[2]
TLS=pathlib.Path(sys.argv[1]).resolve(strict=True)
OLD=os.environ.get('DROP_V1_IMAGE','drop-core:20261016.1')
TOOLS='drop-core-full-tools-base:v2'
RUN_ID=os.environ.get('DROP_CORE_EVIDENCE_RUN_ID',secrets.token_hex(8))
if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',RUN_ID):raise RuntimeError('Use a fresh bounded compatibility evidence run ID')
OUTPUT=ROOT/'core/evidence'/('compat-collected-'+RUN_ID);OUTPUT.mkdir()
def command(args,check=True):
    result=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=45)
    if check and result.returncode:raise RuntimeError('Isolated compatibility command failed: '+result.stderr[-1200:])
    return result.stdout.strip()

OLD_IMAGE_ID=command(['docker','image','inspect','--format','{{.Id}}',OLD])
TOOLS_IMAGE_ID=command(['docker','image','inspect','--format','{{.Id}}',TOOLS])
BINARY_HASHES={name:hashlib.sha256((ROOT/'build'/name).read_bytes()).hexdigest() for name in ('drop_agent','drop_server')}
CATALOG_SHA256=hashlib.sha256((ROOT/'contracts/task-kinds.json').read_bytes()).hexdigest()
def run_pair(new_agent):
    name='agent2_server1' if new_agent else 'agent1_server2'
    owner=secrets.token_hex(8)
    prefix=f'drop-core-compat-{os.getpid()}-{owner}-{name}'
    network=prefix+'-network';containers=[]
    directory=OUTPUT/name;directory.mkdir(exist_ok=True)
    def start(suffix,options,image,args):
        container=prefix+'-'+suffix
        containers.append(container)
        command(['docker','run','-d','--name',container,'--label','io.drop.owner='+owner,'--label','io.drop.purpose=isolated-core-compat','--network',network,*options,image,*args])
        return container
    def sql(statement):return command(['docker','exec',pg,'psql','-U','postgres','-d','drop','-v','ON_ERROR_STOP=1','-qtAc',statement])
    def wait_query(statement,timeout=60):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                value=sql(statement)
                if value:return value
            except RuntimeError:pass
            time.sleep(.5)
        raise RuntimeError('Isolated SQL wait expired')
    def certs(role):return ['-v',f'{TLS}/ca.crt:/tls/ca.crt:ro','-v',f'{TLS}/{role}.crt:/tls/identity.crt:ro','-v',f'{TLS}/{role}.key:/tls/identity.key:ro','-e','TLS_CA_FILE=/tls/ca.crt','-e','TLS_CERT_FILE=/tls/identity.crt','-e','TLS_KEY_FILE=/tls/identity.key']
    try:
        command(['docker','network','create','--internal','--label','io.drop.owner='+owner,network])
        migration_mounts=[value for file in sorted((ROOT/'db/migrations').glob('*.sql')) for value in ['-v',f'{file}:/docker-entrypoint-initdb.d/{file.name}:ro']]
        expected_schema=max(int(file.name.split('_',1)[0]) for file in (ROOT/'db/migrations').glob('*.sql'))
        pg=start('postgres',['--network-alias','postgres','--memory','256m','--cpus','.25','--tmpfs','/var/lib/postgresql/data:rw,size=192m','-e','POSTGRES_DB=drop','-e','POSTGRES_USER=postgres','-e','POSTGRES_PASSWORD=isolated-fixture',*migration_mounts],'postgres:17.11-alpine',['postgres','-c','shared_buffers=24MB','-c','max_connections=12'])
        wait_query(f'SELECT 1 FROM schema_migrations WHERE version={expected_schema}');time.sleep(2)
        server_options=['--network-alias','drop-server','--read-only','--cap-drop','ALL','--user','10001:10001','--memory','192m','--cpus','.25','--tmpfs','/tmp:rw,size=16m','-e','DATABASE_URL=postgresql://postgres:isolated-fixture@postgres/drop','-e','METRICS_LISTEN=off','-e','TASK_KINDS_FILE=/catalog.json','-v',f'{ROOT}/contracts/task-kinds.json:/catalog.json:ro',*certs('server')]
        if not new_agent:server_options+=['-v',f'{ROOT}/build/drop_server:/usr/local/bin/drop_server:ro']
        server=start('server',server_options,OLD,[])
        uploads=start('uploads',['--network-alias','uploads','--read-only','--cap-drop','ALL','--user','10001:10001','--memory','64m','--cpus','.1','--tmpfs','/tmp:rw,size=40m,mode=1777','--entrypoint','/usr/bin/python3','-v',f'{ROOT}/core/tests/compat_upload.py:/compat_upload.py:ro'],TOOLS,['/compat_upload.py'])
        agent_options=['--read-only','--cap-drop','ALL','--user','10001:10001','--memory','192m','--cpus','.3','--tmpfs','/var/lib/drop-agent:rw,size=512m,uid=10001,gid=10001,mode=0700','--tmpfs','/tmp:rw,size=32m,mode=1777','--entrypoint','/usr/local/bin/drop_agent','-e','AGENT_ID=aliyun-local','-e','RUN_CONTROLLED_WORKLOAD=true',*certs('agent')]
        if new_agent:agent_options+=['-e','DROP_SERVER_ADDRESSES=unreachable:50051,drop-server:50051','-v',f'{ROOT}/build/drop_agent:/usr/local/bin/drop_agent:ro','-v',f'{ROOT}/contracts/task-kinds.json:/etc/drop/task-kinds.json:ro']
        else:agent_options+=['-e','DROP_SERVER_ADDRESS=drop-server:50051']
        agent=start('agent',agent_options,TOOLS if new_agent else OLD,[])
        registered=json.loads(wait_query("SELECT json_build_object('version',version,'capabilities',capabilities,'pid',resources->'processes'->0->'pid') FROM agents WHERE id='aliyun-local' AND resources->'processes'->0->'pid' IS NOT NULL",90))
        pid=0 if new_agent else int(registered['pid'])
        parameters=json.dumps({'pid':pid,'scope':'process','duration_seconds':1,'frequency_hz':10})
        sql("INSERT INTO users(id,username,display_name,role) VALUES('compat-fixture','compat-fixture','Isolated compatibility fixture','viewer')")
        task=int(sql(f"INSERT INTO tasks(name,kind,target_agent_id,creator_id,request_id,params,collection_status,collection_deadline) VALUES('Isolated compatibility fixture','PROC_CPU','aliyun-local','compat-fixture','compat-fixture','{parameters}'::jsonb,'QUEUED',now()+interval '120 seconds') RETURNING id"))
        sql(f"INSERT INTO task_attempts(task_id,attempt,agent_id,status,raw_object_key,raw_upload_url) VALUES({task},1,'aliyun-local','QUEUED','fixture/compat/profile.folded','http://uploads:9000/compat-fixture-put')")
        result=json.loads(wait_query(f"SELECT json_build_object('collection_status',collection_status,'error_code',error_code) FROM tasks WHERE id={task} AND collection_status IN ('COLLECTED','FAILED')",60))
        if result['collection_status']!='COLLECTED':raise RuntimeError('Real compatibility collection failed: '+str(result))
        artifact=json.loads(sql(f"SELECT json_build_object('size',size,'sha256',sha256,'format',format) FROM artifacts WHERE task_id={task} AND kind='RAW'"))
        captured=subprocess.run(['docker','exec',uploads,'cat','/tmp/profile.folded'],capture_output=True,check=True,timeout=30)
        (directory/'profile.folded').write_bytes(captured.stdout)
        raw=(directory/'profile.folded').read_bytes()
        if len(raw)!=artifact['size'] or hashlib.sha256(raw).hexdigest()!=artifact['sha256'] or b'(procfs)' not in raw:raise RuntimeError('Uploaded real profile differs from committed evidence')
        if new_agent:
            metrics=command(['docker','exec',agent,'python3','-c','import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:9465/metrics").read().decode())'])
            if 'drop_negotiated_protocol_version 1' not in metrics:raise RuntimeError('Agent2 did not negotiate protocol1')
            if any(kind not in ('PERF_CPU','PROC_CPU') for kind in registered['capabilities']):raise RuntimeError('Protocol1 advertised unsupported V2 kinds')
        selected_binary='drop_agent' if new_agent else 'drop_server'
        report={'case':name,'success':True,'agent_version':registered['version'],'negotiated_protocol':1,'unreachable_preferred_server_failed_over':new_agent,'controlled_pid0_fallback':new_agent,'raw':artifact,'new_binary_sha256':{selected_binary:BINARY_HASHES[selected_binary]},'old_runtime_image_id':OLD_IMAGE_ID,'tools_image_id':TOOLS_IMAGE_ID,'catalog_sha256':CATALOG_SHA256,'fixture_source':'real isolated PROC_CPU Agent/Server execution, private PUT sink, no production mutation'}
        (directory/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');return report
    finally:
        for container in reversed(containers):
            if command(['docker','inspect','--format','{{index .Config.Labels "io.drop.owner"}}',container],False)==owner:
                command(['docker','rm','-f',container],False)
        if command(['docker','network','inspect','--format','{{index .Labels "io.drop.owner"}}',network],False)==owner:
            command(['docker','network','rm',network],False)
results=[run_pair(True),run_pair(False)]
(OUTPUT/'evidence.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
