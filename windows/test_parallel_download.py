import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
import test_direct_download as fixtures
from test_direct_download import API,Network,Response,DATA
from direct_download import Downloader,Paused,ProbeError,output_path,atomic

class TrackedNetwork(Network):
    def __init__(self,delay=.015):
        super().__init__();self.guard=threading.Lock();self.active=0;self.peak=0;self.delay=delay
    def open(self,request,timeout):
        start,end=map(int,request.get_header('Range').split('=')[1].split('-'))
        with self.guard:self.requests.append((start,end));self.active+=1;self.peak=max(self.peak,self.active)
        owner=self
        class TrackedResponse(Response):
            def read(self,n=-1):
                time.sleep(owner.delay*(2 if start==0 else 1))
                return super().read(n)
            def close(self):
                if not self.closed:
                    with owner.guard:owner.active-=1
                super().close()
        return TrackedResponse(start,end)

class ParallelTests(unittest.TestCase):
    setUp=fixtures.Tests.setUp
    tearDown=fixtures.Tests.tearDown
    dl=fixtures.Tests.dl
    def test_multiple_requests_really_overlap_and_ordered_output(self):
        network=TrackedNetwork();path=self.dl(network=network,workers=4).download(self.f,self.dest)
        self.assertGreater(network.peak,1);self.assertLessEqual(network.peak,4)
        self.assertEqual(path.read_bytes(),DATA)
        state=json.loads(path.with_name(path.name+'.quark.json').read_text('utf-8'))
        self.assertEqual([c['start'] for c in state['chunks']],[0,262144,524288,786432])
    def test_serial_checkpoint_to_parallel(self):
        stop=threading.Event()
        with self.assertRaises(Paused):self.dl(stop=stop,progress=lambda n,t,p:stop.set() if n>=262144 else None).download(self.f,self.dest)
        network=TrackedNetwork();path=self.dl(network=network,workers=4).download(self.f,self.dest)
        self.assertEqual(min(x[0] for x in network.requests),262144);self.assertEqual(path.read_bytes(),DATA)
    def test_parallel_pause_then_serial_resume(self):
        stop=threading.Event()
        def checkpoint(path,state):
            atomic(path,state)
            if len(state['chunks'])==1:stop.set()
        with patch('direct_download.atomic',side_effect=checkpoint):
            with self.assertRaises(Paused):self.dl(stop=stop,network=TrackedNetwork(),workers=4).download(self.f,self.dest)
        network=Network();path=self.dl(network=network).download(self.f,self.dest)
        self.assertEqual(network.requests[0][0],262144);self.assertEqual(path.read_bytes(),DATA)
    def test_parallel_link_refresh(self):
        api=API();network=Network(failures=1)
        with patch('threading.Event.wait',return_value=False):path=self.dl(api=api,network=network,workers=4).download(self.f,self.dest)
        self.assertIn(True,api.calls);self.assertEqual(path.read_bytes(),DATA)
    def test_parallel_bad_range_never_finalizes(self):
        with self.assertRaises(ProbeError):self.dl(network=Network(wrong=True),workers=4).download(self.f,self.dest)
        self.assertFalse(output_path(self.dest,'test',self.f).exists())
    def test_parallel_md5_mismatch_never_finalizes(self):
        with self.assertRaises(ProbeError):self.dl(api=API('0'*32),workers=4).download(self.f,self.dest)
        self.assertFalse(output_path(self.dest,'test',self.f).exists())
    def test_throttled_server_stops_with_actionable_error(self):
        class Limited(Network):
            def open(self,*a,**k):raise HTTPError('https://dl.quark.cn/test',429,'limited',{},None)
        with self.assertRaisesRegex(ProbeError,'连接数降至 1 或 2'):self.dl(network=Limited(),workers=4).download(self.f,self.dest)
        self.assertFalse(output_path(self.dest,'test',self.f).exists())
    def test_slow_first_range_does_not_leave_other_workers_idle(self):
        release=threading.Event();ahead=threading.Event();network=Network();errors=[]
        class Uneven(Network):
            def open(inner,request,timeout):
                start=int(request.get_header('Range').split('=')[1].split('-')[0])
                if start==0:release.wait(4)
                if start>=2*65536:ahead.set()
                return network.open(request,timeout)
        dl=Downloader(API(),opener=Uneven(),workers=2,chunk_size=65536,emit=lambda s:None)
        def run():
            try:dl.download(self.f,self.dest)
            except Exception as e:errors.append(e)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(ahead.wait(2),'second worker should fetch ahead while the first range is slow')
            # Four buffered/pending ranges is the configured bounded lookahead.
            self.assertLessEqual(len(network.requests),3)
        finally:release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertEqual(output_path(self.dest,'test',self.f).read_bytes(),DATA)
    def test_32_connections_supported_and_bounded(self):
        dl=Downloader(API(),opener=Network(),workers=32,chunk_size=65536,emit=lambda s:None)
        self.assertEqual(dl.workers,32);self.assertEqual(Downloader(API(),workers=100).workers,32)
        path=dl.download(self.f,self.dest);self.assertEqual(path.read_bytes(),DATA)

if __name__=='__main__':unittest.main(verbosity=2)
