"""受控合成 HTTP CPU 业务；只监听本进程回环地址，不导入 CRM 或生产数据。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time


def work():
    value = 17
    for index in range(30000):
        value = (value * 33 + index) % 1000000007
    return value


EXPECTED = work()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path != '/work':
            self.send_error(404)
            return
        started = time.perf_counter()
        body = json.dumps({'checksum': work(), 'server_ms': (time.perf_counter() - started) * 1000}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(connection):
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.request_queue_size = 32
    connection.send(server.server_address[1])
    connection.close()
    server.serve_forever(poll_interval=.05)
