import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import Request
from urllib.error import HTTPError
from unittest.mock import patch,Mock
from range_transport import RangeOpener
from verify_direct import ProbeError

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.clients=[];owner=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def handle(self):
                try:super().handle()
                except (ConnectionResetError,ConnectionAbortedError):pass
            def log_message(self,*a):pass
            def do_GET(self):
                owner.clients.append(self.client_address)
                if self.path=='/redirect':
                    self.send_response(302);self.send_header('Location','https://example.com/never');self.send_header('Content-Length','0');self.end_headers();return
                if self.path=='/limit':
                    self.send_response(429);self.send_header('Content-Length','0');self.end_headers();return
                self.send_response(206);self.send_header('Content-Length','8');self.send_header('Content-Range','bytes 0-7/8');self.end_headers();self.wfile.write(b'12345678')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}';self.opener=RangeOpener(route='direct')
    def tearDown(self):self.opener.close();self.server.shutdown();self.server.server_close();self.thread.join(2)
    def test_reuses_connection_after_complete_reads(self):
        for _ in range(3):
            with self.opener.open(Request(self.url+'/data')) as r:self.assertEqual(r.read(),b'12345678')
        self.assertEqual(len(set(self.clients)),1)
    def test_partial_read_forces_new_connection(self):
        with self.opener.open(Request(self.url+'/data')) as r:self.assertEqual(r.read(2),b'12')
        self.assertIsNone(self.opener.connection)
        with self.opener.open(Request(self.url+'/data')) as r:self.assertEqual(r.read(),b'12345678')
        self.assertEqual(len(set(self.clients)),2)
    def test_redirect_not_followed_and_limit_preserves_code(self):
        with self.assertRaises(ProbeError):self.opener.open(Request(self.url+'/redirect',headers={'Cookie':'test-only'}))
        with self.assertRaises(HTTPError) as result:self.opener.open(Request(self.url+'/limit'))
        self.assertEqual(result.exception.code,429);self.assertEqual(len(self.clients),2)
    def test_system_proxy_uses_existing_route(self):
        self.opener.route='system';self.opener.proxies={'https':'http://proxy.invalid'};fallback=Mock()
        with patch('range_transport.proxy_bypass',return_value=False),patch('range_transport.build_opener',return_value=fallback):
            request=Request('https://download.quark.cn/file');self.opener.open(request)
        fallback.open.assert_called_once_with(request,timeout=20)
    def test_closed_keepalive_reconnects(self):
        with self.opener.open(Request(self.url+'/data')) as r:r.read()
        self.opener.connection.close()
        with self.opener.open(Request(self.url+'/data')) as r:self.assertEqual(r.read(),b'12345678')
        self.assertEqual(len(set(self.clients)),2)

if __name__=='__main__':unittest.main()
