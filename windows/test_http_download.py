"""Real loopback HTTP + disk + durable checkpoint; no Quark account or internet."""
import hashlib
import json
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from direct_download import Downloader,Paused,atomic,output_path

class HttpDownloadTest(unittest.TestCase):
    def test_real_http_range_pause_parallel_resume_and_local_skip(self):
        content=bytes(range(251))*9000;requests=[];guard=threading.Lock()
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def handle(self):
                try:super().handle()
                except (ConnectionResetError,ConnectionAbortedError):pass  # Intentional pause closes a partially read response.
            def log_message(self,*args):pass
            def do_GET(self):
                match=re.fullmatch(r'bytes=(\d+)-(\d+)',self.headers.get('Range',''))
                if not match:self.send_error(416);return
                start,end=map(int,match.groups())
                with guard:requests.append((start,end))
                data=content[start:end+1]
                self.send_response(206);self.send_header('Content-Length',str(len(data)))
                self.send_header('Content-Range',f'bytes {start}-{end}/{len(content)}');self.end_headers()
                try:self.wfile.write(data)
                except (ConnectionError,OSError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        class API:
            sid='loopback'
            def direct_link(self,f,refresh=False):return f'http://127.0.0.1:{server.server_port}/file',{},hashlib.md5(content).hexdigest()
        f={'fid':'real-http','display':'目录/sample.bin','path_parts':['目录','sample.bin'],'size':len(content)}
        try:
            with tempfile.TemporaryDirectory() as temp:
                dest=Path(temp);stop=threading.Event()
                def commit(path,state):
                    atomic(path,state)
                    if len(state['chunks'])==2:stop.set()
                with patch('direct_download.atomic',side_effect=commit):
                    with self.assertRaises(Paused):Downloader(API(),stop=stop,chunk_size=262144,workers=4,emit=lambda s:None).download(f,dest)
                target=output_path(dest,'loopback',f)
                checkpoint=json.loads(target.with_name(target.name+'.quark.json').read_text('utf-8'))
                offset=sum(c['length'] for c in checkpoint['chunks']);self.assertEqual(offset,524288)
                requests.clear()
                result=Downloader(API(),chunk_size=262144,workers=4,emit=lambda s:None).download(f,dest)
                self.assertEqual(min(start for start,end in requests),offset)
                self.assertEqual(hashlib.sha256(result.read_bytes()).digest(),hashlib.sha256(content).digest())
                requests.clear();repeat=Downloader(API(),emit=lambda s:None)
                self.assertEqual(repeat.download(f,dest),result);self.assertEqual(requests,[]);self.assertEqual(repeat.result_kind,'existing')
        finally:server.shutdown();server.server_close();thread.join(3)

if __name__=='__main__':unittest.main()
