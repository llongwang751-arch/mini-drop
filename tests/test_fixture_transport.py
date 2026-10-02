"""Real sockets and independent ranking oracles for the endurance path."""
from contextlib import contextmanager
from dataclasses import replace
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest

from demo.rag_service.app import KnowledgeService, QUESTIONS, Settings
from scripts import run_load_endurance as load


@contextmanager
def endpoint(responses):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            observed.append((self.client_address, self.rfile.read(int(self.headers['Content-Length']))))
            status, body = responses[len(observed)-1]
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/query', observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def ask(url, transport):
    now = time.perf_counter()
    return load.request(url, 0, now, now, 2, transport)


def test_real_connection_reuse_preserves_quality_and_http_errors_without_retry():
    answer = json.dumps({'success': True, 'citations': ['leave']}).encode()
    transport = load.FixtureTransport()
    try:
        with endpoint([(503, answer), (200, answer)]) as (url, observed):
            failed, passed = ask(url, transport), ask(url, transport)
            assert len(observed) == 2
            assert observed[0][0] == observed[1][0]
            assert failed['http_status'] == 503 and not failed['quality_passed']
            assert failed['error'] == 'HTTP_ERROR'
            assert passed['success'] and passed['quality_passed']
            assert failed['client_timing']['new_connection'] is True
            assert passed['client_timing']['new_connection'] is False
    finally:
        transport.close()
    assert not transport.connections


def test_invalid_json_remains_a_failed_attempt_with_its_http_status():
    transport = load.FixtureTransport()
    try:
        with endpoint([(200, b'not-json')]) as (url, observed):
            row = ask(url, transport)
            assert len(observed) == 1
            assert row['http_status'] == 200 and not row['success']
            assert row['error'] == 'JSONDecodeError'
            assert row['latency_ms'] > 0
    finally:
        transport.close()


@pytest.mark.parametrize('url', ['https://127.0.0.1/query', 'http://example.com/query',
                                  'http://user:secret@127.0.0.1/query'])
def test_pool_cannot_be_used_as_an_arbitrary_remote_http_client(url):
    transport = load.FixtureTransport()
    with pytest.raises(ValueError):
        transport.post(url, b'{}', 1)
    assert not transport.connections


def test_failed_write_discards_connection_and_does_not_retry(monkeypatch):
    calls = []

    class Broken:
        sock = None
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            self.sock = object()
        def request(self, *args):
            calls.append('write')
            raise ConnectionResetError('peer reset')
        def close(self):
            calls.append('close')

    monkeypatch.setattr(load.http.client, 'HTTPConnection', Broken)
    transport = load.FixtureTransport()
    row = ask('http://127.0.0.1/query', transport)
    assert calls == ['write', 'close']
    assert row['error'] == 'ConnectionResetError'
    assert not transport.connections and transport.local.connection is None


def test_bounded_cache_keeps_rankings_equal_and_new_content_cannot_hit_old_score():
    cached = KnowledgeService(Settings(cache_rerank=True))
    original = KnowledgeService()
    try:
        for question, expected in QUESTIONS:
            first, uncached = cached.query(question), original.query(question)
            assert first['citations'] == uncached['citations'] == [expected]
            assert first['answer'] == uncached['answer']
        misses = cached._score.cache_info().misses
        for question, _ in QUESTIONS:
            cached.query(question)
        assert cached._score.cache_info().misses == misses
        before = cached._score('annual leave', 'annual leave policy')
        changed = cached._score('annual leave', 'password reset')
        assert changed == SequenceMatcher(None, 'annual leave', 'password reset', autojunk=False).ratio()
        assert before != changed
        for i in range(1100):
            cached._score('q', str(i))
        assert cached._score.cache_info().currsize == 1024
    finally:
        cached.close()
        original.close()
    assert cached._score.cache_info().currsize == 0


@pytest.mark.parametrize('change', [{'reuse_connections': 1}, {'cache_rerank': 'true'}])
def test_optimization_flags_are_explicit_booleans(change):
    with pytest.raises(ValueError):
        replace(load.Plan(), **change).validate()


def test_blocked_route_does_not_block_another_thread_or_hide_its_latency():
    from concurrent.futures import ThreadPoolExecutor
    blocked, release = threading.Event(), threading.Event()
    observed=[]

    def server(slow):
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                observed.append((slow,self.client_address))
                if slow:
                    blocked.set()
                    assert release.wait(3)
                body=b'{"success":true,"citations":["leave"]}'
                self.send_response(200);self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body)
        http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        return http,thread

    transport=load.FixtureTransport()
    first,first_thread=server(True);second,second_thread=server(False)
    urls=tuple(f'http://127.0.0.1:{http.server_port}/query' for http in (first,second))
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            slow=executor.submit(ask,urls,transport)
            assert blocked.wait(1)
            try:
                # Completion while the first response is withheld is the oracle;
                # no timing estimate or external network is needed to prove isolation.
                fast=[executor.submit(ask,urls,transport).result(timeout=1) for _ in range(5)]
                assert not slow.done()
                assert all(r['success'] and r['quality_passed'] and r['transport_index']==1 for r in fast)
                assert sum(not item[0] for item in observed)==5
                assert len({item[1] for item in observed if not item[0]})==1
            finally:
                release.set()
            row=slow.result(timeout=1)
            assert row['success'] and row['transport_index']==0 and row['latency_ms']>0
            assert len(observed)==6  # Both slow and healthy attempts retained, no retry.
    finally:
        release.set();transport.close()
        for http,thread in ((first,first_thread),(second,second_thread)):
            http.shutdown();http.server_close();thread.join(timeout=3)
    assert not transport.connections


@pytest.mark.parametrize('routes',[(),tuple('http://127.0.0.1/query' for _ in range(9)),('http://example.com/query',)])
def test_route_pool_is_bounded_and_local(routes):
    transport=load.FixtureTransport()
    with pytest.raises(ValueError):transport.route(routes)


def test_route_pool_cannot_change_mid_measurement():
    transport=load.FixtureTransport()
    first=('http://127.0.0.1:10001/query','http://127.0.0.1:10002/query')
    assert transport.route(first)==(first[0],0)
    with pytest.raises(ValueError):transport.route((first[1],))
