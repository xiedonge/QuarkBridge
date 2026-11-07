"""Offline contract executed inside every frozen application, without user credentials."""
import io,json
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import urlsplit
from verify_direct import Probe,ProbeError

class Reply(io.BytesIO):
    def __init__(self,data):
        super().__init__(json.dumps({'code':0,'data':data}).encode('utf-8'))
        self.headers=Message()

class Scenario:
    def __init__(self,encoding,persistent):
        self.encoding=encoding;self.persistent=persistent;self.downloads=0;self.paths=[]
    def open(self,req,timeout=30):
        path=urlsplit(req.full_url).path.split('/clouddrive/')[1];self.paths.append(path)
        if path=='share/sharepage/token':return Reply({'stoken':'fresh'})
        if path=='share/sharepage/detail':return Reply({'list':[{'fid':'fixture','size':10,'share_fid_token':'fresh-file'}]})
        if path=='member':return Reply({'total_capacity':10,'use_capacity':0})
        assert path=='file/download'
        self.downloads+=1
        if self.downloads==1 or self.persistent:
            body=json.dumps({'code':23018,'message':'fixture'}).encode(self.encoding)
            raise HTTPError(req.full_url,400,'fixture',Message(),io.BytesIO(body))
        payload=json.loads(req.data)
        assert payload['fids_token']==['fresh-file'] and payload['stoken']=='fresh'
        return Reply([{'download_url':'https://dl.quark.cn/fixture'}])

def check_error_recovery():
    for encoding in ('utf-8','utf-8-sig','utf-16','utf-32'):
        for persistent in (False,True):
            p=Probe(emit=lambda message:None);p.sid='fixture';p.password='';p.token='old'
            p.opener=Scenario(encoding,persistent)
            f={'fid':'fixture','size':10,'parent_fid':'0','share_fid_token':'old-file'}
            if persistent:
                try:p.direct_link(f)
                except ProbeError as error:
                    assert '账号容量接口可用' in str(error)
                    assert 'code=23018' in str(error)
                else:raise AssertionError('Persistent rejection was swallowed')
            else:assert p.direct_link(f)[0]=='https://dl.quark.cn/fixture'
            assert p.opener.downloads==2
            assert all(path in ('file/download','share/sharepage/token','share/sharepage/detail','member') for path in p.opener.paths)
