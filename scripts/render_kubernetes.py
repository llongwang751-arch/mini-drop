"""Render reviewable Kubernetes resources; credentials are supplied separately."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'deploy/kubernetes'
TAG = '20261018.1'
items = []


def resource(kind, name, spec=None, **extra):
    versions = {'Deployment':'apps/v1','DaemonSet':'apps/v1','PodDisruptionBudget':'policy/v1','HorizontalPodAutoscaler':'autoscaling/v2','NetworkPolicy':'networking.k8s.io/v1','Job':'batch/v1'}
    obj = {'apiVersion':versions.get(kind,'v1'),'kind':kind,'metadata':{'name':name,'namespace':'drop'}}
    if spec is not None: obj['spec'] = spec
    obj.update(extra); items.append(obj); return obj


items.append({'apiVersion':'v1','kind':'Namespace','metadata':{'name':'drop','labels':{'app.kubernetes.io/part-of':'drop','pod-security.kubernetes.io/enforce':'restricted'}}})
resource('ConfigMap','drop-config',data={'DROP_SERVER_ADDRESS':'drop-server:50051','PUBLIC_URL':'https://120.24.187.205','S3_ENDPOINT':'minio:9000','S3_BUCKET':'drop-artifacts','OTEL_EXPORTER_OTLP_ENDPOINT':'http://otel-collector:4318','OTEL_EXPORTER_OTLP_TRACES_ENDPOINT':'http://otel-collector:4318/v1/traces'})


def workload(name, image, replicas, *, env=None, command=None, tls=None, secrets=(), memory='256Mi', cpu='500m', port=None, heap=False):
    labels = {'app.kubernetes.io/name':name,'app.kubernetes.io/part-of':'drop'}
    variables = [{'name':k,'value':v} for k,v in (env or {}).items()]
    volumes = [{'name':'tmp','emptyDir':{'sizeLimit':'256Mi' if not heap else '8Gi'}}]
    mounts = [{'name':'tmp','mountPath':'/tmp'}]
    if secrets:
        volumes.append({'name':'secrets','secret':{'secretName':'drop-runtime','defaultMode':0o440,'items':[{'key':key,'path':key} for key in secrets]}})
        # Core deliberately rejects leaf symlinks for its generic KEY_FILE
        # reader. Bind each Secret item as a regular read-only file. These
        # values are loaded at startup; rotate them with a controlled rollout.
        mounts.extend({'name':'secrets','subPath':key,'mountPath':'/run/secrets/'+key,'readOnly':True} for key in secrets)
    if tls:
        volumes.append({'name':'tls','secret':{'secretName':'drop-'+tls+'-tls','defaultMode':0o440}})
        mounts.append({'name':'tls','mountPath':'/etc/drop/tls','readOnly':True})
        variables += [{'name':'TLS_'+key+'_FILE','value':'/etc/drop/tls/'+filename} for key,filename in [('CA','ca.crt'),('CERT',tls+'.crt'),('KEY',tls+'.key')]]
    container = {'name':name,'image':image+':'+TAG,'envFrom':[{'configMapRef':{'name':'drop-config'}}],'env':variables,'resources':{'requests':{'cpu':'100m','memory':'128Mi','ephemeral-storage':'128Mi'},'limits':{'cpu':cpu,'memory':memory,'ephemeral-storage':'512Mi' if not heap else '8Gi'}},'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},'volumeMounts':mounts}
    if command: container['command'] = command
    if port: container['ports'] = [{'name':'service','containerPort':port}]
    if name == 'api':
        container['readinessProbe'] = {'httpGet':{'path':'/health/ready','port':8080},'periodSeconds':5}
        container['livenessProbe'] = {'httpGet':{'path':'/health/live','port':8080},'periodSeconds':15}
    elif name == 'web':
        for probe in ('readinessProbe','livenessProbe'): container[probe] = {'httpGet':{'path':'/healthz','port':8080},'periodSeconds':10}
    elif name == 'drop-server':
        container['readinessProbe'] = {'tcpSocket':{'port':50051},'periodSeconds':5}
    elif name.startswith('analyzer'):
        container['readinessProbe'] = {'exec':{'command':['python','-m','drop_analyzer.health']},'initialDelaySeconds':15,'periodSeconds':15}
        container['livenessProbe'] = {'exec':{'command':['python','-m','drop_analyzer.health']},'initialDelaySeconds':45,'periodSeconds':30}
    elif name == 'maintenance':
        container['readinessProbe'] = {'httpGet':{'path':'/healthz','port':9105},'initialDelaySeconds':20,'periodSeconds':15}
    pod = {'automountServiceAccountToken':False,'terminationGracePeriodSeconds':150 if name.startswith('analyzer') else 45,'securityContext':{'runAsNonRoot':True,'runAsUser':10001,'runAsGroup':10001,'fsGroup':10001,'seccompProfile':{'type':'RuntimeDefault'}},'containers':[container],'volumes':volumes,'affinity':{'podAntiAffinity':{'preferredDuringSchedulingIgnoredDuringExecution':[{'weight':100,'podAffinityTerm':{'labelSelector':{'matchLabels':labels},'topologyKey':'kubernetes.io/hostname'}}]}}}
    if heap: pod.update({'nodeSelector':{'drop.io/high-memory':'true'},'tolerations':[{'key':'drop.io/high-memory','operator':'Equal','value':'true','effect':'NoSchedule'}]})
    resource('Deployment',name,{'replicas':replicas,'selector':{'matchLabels':labels},'strategy':{'type':'RollingUpdate','rollingUpdate':{'maxUnavailable':0,'maxSurge':1}},'template':{'metadata':{'labels':labels},'spec':pod}})
    if port: resource('Service',name,{'selector':labels,'ports':[{'name':'service','port':port,'targetPort':'service'}]})
    if replicas > 1: resource('PodDisruptionBudget',name,{'minAvailable':1,'selector':{'matchLabels':labels}})


db = {'DATABASE_URL_FILE':'/run/secrets/database_url'}
s3 = {'S3_ACCESS_KEY_FILE':'/run/secrets/s3_access_key','S3_SECRET_KEY_FILE':'/run/secrets/s3_secret_key'}
workload('drop-server','drop-core',2,env={**db,'METRICS_LISTEN':'0.0.0.0:9101'},tls='server',secrets=('database_url',),port=50051)
workload('api','drop-api',2,env={**db,**s3,'ADMIN_USERNAME_FILE':'/run/secrets/admin_username','ADMIN_PASSWORD_FILE':'/run/secrets/admin_password'},tls='api',secrets=('database_url','s3_access_key','s3_secret_key','admin_username','admin_password'),port=8080)
workload('web','drop-web',2,port=8080,memory='128Mi',cpu='300m')
workload('analyzer','drop-analyzer',2,env={**db,**s3,'ANALYZER_QUEUE':'small','ANALYSIS_MEMORY_BYTES':'536870912'},secrets=('database_url','s3_access_key','s3_secret_key'),memory='768Mi',cpu='1')
workload('analyzer-heap','drop-analyzer',1,env={**db,**s3,'ANALYZER_QUEUE':'high_memory','ANALYSIS_MEMORY_BYTES':'3221225472','ANALYSIS_WALL_SECONDS':'900','ANALYSIS_CPU_SECONDS':'600'},secrets=('database_url','s3_access_key','s3_secret_key'),memory='4Gi',cpu='2',heap=True)
workload('maintenance','drop-ops',1,env={**db,**s3,'ALERT_WEBHOOK_TOKEN_FILE':'/run/secrets/alert_webhook_token'},secrets=('database_url','s3_access_key','s3_secret_key','alert_webhook_token'),memory='128Mi',cpu='200m')
resource('HorizontalPodAutoscaler','api',{'scaleTargetRef':{'apiVersion':'apps/v1','kind':'Deployment','name':'api'},'minReplicas':2,'maxReplicas':8,'metrics':[{'type':'Resource','resource':{'name':'cpu','target':{'type':'Utilization','averageUtilization':65}}}]})
resource('NetworkPolicy','default-deny-ingress',{'podSelector':{},'policyTypes':['Ingress']})
resource('NetworkPolicy','control-plane',{'podSelector':{},'policyTypes':['Ingress'],'ingress':[{'from':[{'podSelector':{}},{'namespaceSelector':{'matchLabels':{'drop.io/agents':'true'}}}]}]})
resource('NetworkPolicy','public-web',{'podSelector':{'matchLabels':{'app.kubernetes.io/name':'web'}},'policyTypes':['Ingress'],'ingress':[{'ports':[{'protocol':'TCP','port':8080}]}]})
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'resources.json').write_text(json.dumps({'apiVersion':'v1','kind':'List','items':items},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
(OUT/'kustomization.yaml').write_text('apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n  - resources.json\n',encoding='utf8')
print(f'Rendered {len(items)} Kubernetes resources; no secrets included')
