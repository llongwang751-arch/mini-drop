"""Private, bounded HTML dependency for linkding acceptance, not a business API."""
import json,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

CONTROL=Path('/var/lib/mini-drop-business/linkding-fixture.json')

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path!='/page':self.send_error(404);return
        config=json.loads(CONTROL.read_text()) if CONTROL.exists() else {}
        delay=min(1,max(0,float(config.get('delay_seconds',0))))
        time.sleep(delay)
        body=b'<!doctype html><html><head><title>Mini-Drop controlled document</title><meta name="description" content="Own acceptance fixture"></head><body>Test document</body></html>'
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass

if __name__=='__main__':ThreadingHTTPServer(('172.17.0.1',18110),Handler).serve_forever()
