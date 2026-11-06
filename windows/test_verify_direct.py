import io
import unittest
from unittest.mock import patch
from verify_direct import Probe, ProbeError, parse

class Response(io.BytesIO):
    status=206
    def __init__(self,data,header):
        super().__init__(data);self.headers={'Content-Range':header}

class Opener:
    def __init__(self,bad=False):self.requests=[];self.bad=bad
    def open(self,req,timeout):
        self.requests.append(req)
        start,end=map(int,req.get_header('Range').split('=')[1].split('-'))
        return Response(b'a'*(end-start+1),f'bytes {start}-{end}/{11 if self.bad else 10}')

class Tests(unittest.TestCase):
    def make(self):
        p=Probe(emit=lambda s:None);p.sid='test';p.token='private';p.before={'total':5,'used':5}
        p.capacity=lambda:{'total':5,'used':5}
        p.call=lambda *a,**k:[{'download_url':'https://dl.quark.cn/test'}]
        return p
    def test_subdirectory(self):
        self.assertEqual(parse('https://pan.quark.cn/s/abc?pwd=1234#/list/share/sub'),('abc','sub','1234'))
    def test_reject_foreign_share(self):
        with self.assertRaises(ProbeError):parse('https://evil.example/s/abc')
    def test_mutations_blocked_before_network(self):
        p=Probe()
        for endpoint in ['file/delete','share/sharepage/save','file','file/upload']:
            with self.assertRaises(ProbeError):p.call(endpoint,{})
        self.assertEqual(p.audit,[])
    def test_range_success_despite_zero_capacity(self):
        p=self.make();o=Opener()
        with patch('verify_direct.build_opener',return_value=o):
            result=p.verify({'fid':'x','share_fid_token':'private','size':10})
        self.assertEqual(result['result'],'range_samples_passed')
        self.assertTrue(result['exceeds_available'])
        self.assertEqual(result['cloud_mutation_calls'],0)
        self.assertNotIn('private',str(result))
    def test_wrong_total_rejected(self):
        with patch('verify_direct.build_opener',return_value=Opener(True)):
            with self.assertRaises(ProbeError):self.make().verify({'fid':'x','share_fid_token':'private','size':10})
    def test_cookie_not_sent_to_foreign_cdn(self):
        p=self.make();p.cookie='private'
        p.call=lambda *a,**k:[{'download_url':'https://quark.cn.evil.example/test'}]
        with patch('verify_direct.build_opener') as network:
            with self.assertRaises(ProbeError):p.verify({'fid':'x','share_fid_token':'private','size':10})
            network.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
