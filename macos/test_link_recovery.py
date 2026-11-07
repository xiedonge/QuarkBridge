import unittest
from unittest.mock import Mock
from verify_direct import Probe, APIError, ProbeError

class RecoveryTests(unittest.TestCase):
    def make(self):
        p=Probe(emit=lambda message:None);p.sid='test';p.password='';p.token='old'
        return p,{'fid':'file','size':10,'parent_fid':'folder','share_fid_token':'old-file-token'}
    def test_recovery_refreshes_both_tokens_and_preserves_share_fid(self):
        p,f=self.make();calls=[]
        def call(path,body=None,**query):
            calls.append((path,body,query))
            if path=='share/sharepage/token':return {'stoken':'new'}
            if path=='share/sharepage/detail':
                self.assertEqual(query['pdir_fid'],'folder')
                return {'list':[dict(f,share_fid_token='new-file-token')]}
            if len(calls)==1:raise APIError(path,23018,400)
            self.assertEqual(body['fids'],['file'])
            self.assertEqual(body['fids_token'],['new-file-token'])
            self.assertEqual(body['stoken'],'new')
            return [{'download_url':'https://dl.quark.cn/test'}]
        p.call=call
        self.assertEqual(p.direct_link(f)[0],'https://dl.quark.cn/test')
        self.assertEqual([c[0] for c in calls],['file/download','share/sharepage/token','share/sharepage/detail','file/download'])
    def test_persistent_rejection_is_bounded_and_diagnosed(self):
        p,f=self.make();p._direct_link=Mock(side_effect=APIError('file/download',23018,400))
        p.call=Mock(return_value={'total_capacity':10})
        with self.assertRaises(ProbeError) as caught:p.direct_link(f)
        self.assertEqual(p._direct_link.call_count,2)
        self.assertIn('账号容量接口可用',str(caught.exception))
        self.assertNotIn('登录已过期',str(caught.exception))
        self.assertNotIn('old-file-token',str(caught.exception))
    def test_explicit_login_failure_distinguished(self):
        p,f=self.make();p._direct_link=Mock(side_effect=APIError('file/download',23018,400))
        p.call=Mock(side_effect=ProbeError('登录已过期'))
        with self.assertRaisesRegex(ProbeError,'账号接口明确提示登录已过期'):p.direct_link(f)
    def test_other_failures_are_not_retried(self):
        p,f=self.make();p._direct_link=Mock(side_effect=APIError('file/download',999,400))
        with self.assertRaises(APIError):p.direct_link(f)
        self.assertEqual(p._direct_link.call_count,1)
    def test_changed_file_is_not_downloaded(self):
        p,f=self.make()
        p.call=Mock(side_effect=[APIError('file/download',23018,400),{'stoken':'new'},{'list':[dict(f,size=11)]}])
        with self.assertRaisesRegex(ProbeError,'大小已改变'):p.direct_link(f)
        self.assertEqual(p.call.call_count,3)
    def test_missing_file_is_not_downloaded(self):
        p,f=self.make()
        p.call=Mock(side_effect=[APIError('file/download',23018,400),{'stoken':'new'},{'list':[]}])
        with self.assertRaisesRegex(ProbeError,'已移除'):p.direct_link(f)
        self.assertEqual(p.call.call_count,3)
