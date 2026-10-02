"""Real SQLite FTS5 retrieval and lexical reranking with request-stage timings.

This is an extractive local fixture, NOT a remote LLM or the user's RAG repo.
Run: python -m demo.rag_service.app --port 8097. Controls stay in the test harness.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import sqlite3
import threading
import time
from uuid import uuid4


DOCUMENTS = [
    ("leave", "annual leave policy", "Employees request annual leave through the HR portal. Manager approval is required before travel."),
    ("expense", "travel expense reimbursement", "Upload a receipt to the finance portal within thirty days. Travel expenses require manager approval."),
    ("password", "password reset account access", "Use the account recovery portal to reset a forgotten password. Never share verification codes."),
    ("incident", "production incident reporting", "Report a production incident to the on-call engineer. Include the affected service and start time."),
]
QUESTIONS = [("annual leave policy", "leave"),("travel expense reimbursement", "expense"),
             ("password reset account access", "password"),("production incident reporting", "incident")]
DATASET_SHA = hashlib.sha256(json.dumps(DOCUMENTS, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Settings:
    rerank_candidates: int = 4
    shared_ingest_queue: bool = False
    dependency_latency_ms: int = 0
    dependency_timeout_ms: int = 600
    allow_extractive_fallback: bool = False


class KnowledgeService:
    def __init__(self, settings=Settings()):
        self.settings=settings
        self._query_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="query")
        self._ingest_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="ingest")
        self._db=sqlite3.connect(":memory:",check_same_thread=False)
        self._db.execute("CREATE VIRTUAL TABLE documents USING fts5(doc_id UNINDEXED,title,body)")
        # Multiple indexed chunks have actual text, so excessive reranking does
        # real SequenceMatcher work rather than burning CPU in an unrelated loop.
        for doc_id,title,body in DOCUMENTS:
            for chunk in range(96):
                self._db.execute("INSERT INTO documents VALUES (?,?,?)",(doc_id,title,
                    body+" "+("Operational reference for "+title+". ")*32+f" section {chunk}"))
        self._db.commit()
        self._close=False
        self.ingested_documents=0
        self._ingest_lock=threading.Lock()

    def query(self, question):
        if not isinstance(question,str) or not 1<=len(question)<=500: raise ValueError("question length must be 1..500")
        started=time.perf_counter()
        return self._query_pool.submit(self._query,question,started).result(timeout=30)

    def _query(self,question,started):
        stages={"queue":(time.perf_counter()-started)*1000}
        tokens=re.findall(r"[a-zA-Z0-9]+",question.lower())[:30]
        t=time.perf_counter()
        rows=self._db.execute("SELECT doc_id,title,body FROM documents WHERE documents MATCH ? ORDER BY rank LIMIT ?",
            (" OR ".join('"'+v+'"' for v in tokens) or '"no_match"',self.settings.rerank_candidates)).fetchall()
        stages['retrieval']=(time.perf_counter()-t)*1000
        t=time.perf_counter()
        ranked=sorted(rows,key=lambda row:SequenceMatcher(None,question.lower(),row[1]+" "+row[2],autojunk=False).ratio(),reverse=True)
        stages['rerank']=(time.perf_counter()-t)*1000
        t=time.perf_counter()
        degraded=False
        timeout=self.settings.dependency_latency_ms>self.settings.dependency_timeout_ms
        time.sleep(min(self.settings.dependency_latency_ms,self.settings.dependency_timeout_ms)/1000)
        if timeout:
            degraded=self.settings.allow_extractive_fallback
        stages['generation']=(time.perf_counter()-t)*1000
        success=bool(ranked) and (not timeout or degraded)
        best=ranked[0] if success else None
        answer=best[2].split(" Operational reference",1)[0] if best else None
        return {"trace_id":uuid4().hex,"service":"knowledge-api","generator":"EXTRACTIVE_LOCAL",
                "answer":answer,"citations":[best[0]] if best else [],"success":success,
                "degraded":degraded,"error":"DEPENDENCY_TIMEOUT" if timeout and not degraded else None,
                "latency_ms":(time.perf_counter()-started)*1000,"stage_ms":stages,
                "candidate_count":len(rows)}

    def ingest(self, batch_id):
        pool=self._query_pool if self.settings.shared_ingest_queue else self._ingest_pool
        return pool.submit(self._index_batch,batch_id)

    def _index_batch(self,batch_id):
        # A staging index, deliberately separate from the frozen query corpus.
        # The before/after experiment keeps the same import work running.
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE VIRTUAL TABLE staging USING fts5(body)")
            content=("document import knowledge archive retrieval policy reference "*256)
            db.executemany("INSERT INTO staging VALUES (?)",((content+str(batch_id)+str(i),) for i in range(1800)))
            db.commit()
        with self._ingest_lock: self.ingested_documents+=1800

    def close(self):
        self._query_pool.shutdown(wait=True,cancel_futures=False)
        self._ingest_pool.shutdown(wait=True,cancel_futures=False)
        self._db.close()


def serve(service,host="127.0.0.1",port=8097):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            if self.path!='/health': self.send_error(404);return
            self.respond({"status":"healthy","service":"knowledge-api","generator":"EXTRACTIVE_LOCAL"})
        def do_POST(self):
            if self.path!='/query': self.send_error(404);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=4096: raise ValueError('body size out of range')
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict) or set(body)!={'question'}: raise ValueError('only question is accepted')
                self.respond(service.query(body['question']))
            except (ValueError,KeyError,json.JSONDecodeError) as exc: self.respond({'error':str(exc)},400)
        def respond(self,value,status=200):
            payload=json.dumps(value,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    return ThreadingHTTPServer((host,port),Handler)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8097)
    args=parser.parse_args();service=KnowledgeService();server=serve(service,port=args.port)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close();service.close()
