"""为已提交 Server span 提供有界私有 OTLP/HTTP 接收端与断言。"""
import json
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

DOCUMENTS = []


class Receiver(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        size = int(self.headers.get('Content-Length', '0'))
        if self.path != '/v1/traces' or not 0 < size <= 262144 or len(DOCUMENTS) >= 128:
            self.send_error(400)
            return
        document = json.loads(self.rfile.read(size))
        DOCUMENTS.append(document)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{}')

    def do_GET(self):
        if self.path != '/snapshot':
            self.send_error(404)
            return
        payload = json.dumps(DOCUMENTS).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def check():
    end = time.monotonic() + 8
    documents = []
    while time.monotonic() < end:
        documents = json.load(urllib.request.urlopen('http://127.0.0.1:4318/snapshot', timeout=2))
        spans = []
        for document in documents:
            for resource in document['resourceSpans']:
                service = {attribute['key']: attribute['value'].get('stringValue') for attribute in resource['resource']['attributes']}
                if service.get('service.name') != 'drop-server':
                    raise RuntimeError('Unexpected OTLP service identity')
                for scope in resource['scopeSpans']:
                    spans.extend(scope['spans'])
        if sum(span['name'] == 'queue.task' for span in spans) >= 2 and sum(span['name'] == 'dispatch.task' for span in spans) >= 1:
            break
        time.sleep(.2)
    else:
        raise RuntimeError('Committed Server queue/dispatch spans were not received')
    tasks = set()
    for span in spans:
        attributes = {attribute['key']: attribute['value'] for attribute in span['attributes']}
        if set(attributes) != {'drop.task_id', 'drop.request_id'}:
            raise RuntimeError('Server trace attributes exceed the fixed allowlist')
        task = attributes['drop.task_id']['intValue']
        if not task.isdigit() or int(task) <= 0 or not attributes['drop.request_id']['stringValue'].startswith('core-probe-'):
            raise RuntimeError('Server trace lost persisted task/request identity')
        tasks.add(task)
        if span.get('links') != [{'traceId':'1'*32,'spanId':'2'*16}] or span['traceId'] == '1'*32 or 'parentSpanId' in span:
            raise RuntimeError('Server trace did not preserve asynchronous link semantics')
        if int(span['endTimeUnixNano']) < int(span['startTimeUnixNano']) or span['status']['code'] != 1:
            raise RuntimeError('Committed Server trace has invalid timing or status')
    if any(token in json.dumps(documents) for token in ['http://', 'https://', 'put_url', 'raw_upload_url', 'isolated-fixture']):
        raise RuntimeError('Server trace leaked transport data')
    print(json.dumps({'schema_version':1,'service_name':'drop-server','queue_spans':sum(span['name']=='queue.task' for span in spans),'dispatch_spans':sum(span['name']=='dispatch.task' for span in spans),'linked_task_ids':sorted(tasks),'persisted_identity':True,'async_links':True,'fixed_safe_attributes':True,'private_receiver_actual':True}))


if sys.argv[1:] == ['check']:
    check()
else:
    HTTPServer(('0.0.0.0',4318),Receiver).serve_forever()
