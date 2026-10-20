"""仅用于私有测试网络的 PUT 接收端，不是生产对象存储。"""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def do_PUT(self):
        size=int(self.headers.get('Content-Length','0'))
        if self.path!='/compat-fixture-put' or not 0<size<=33554432:
            self.send_error(400);return
        content=self.rfile.read(size)
        if len(content)!=size:self.send_error(400);return
        Path('/tmp/profile.folded').write_bytes(content)
        self.send_response(200);self.end_headers()
HTTPServer(('0.0.0.0',9000),Handler).serve_forever()
