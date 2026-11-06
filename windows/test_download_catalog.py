import json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from direct_download import Downloader,Paused,output_path
from download_catalog import PotentialDuplicate
from test_direct_download import API,Network,DATA

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.f={'fid':'a','size':len(DATA),'display':'original/video.mp4','path_parts':['original','video.mp4'],'file_name':'video.mp4'}
    def tearDown(self):self.tmp.cleanup()
    def dl(self,api=None,network=None,**kw):return Downloader(api or API(),opener=network or Network(),chunk_size=262144,emit=lambda s:None,**kw)
    def test_completed_in_other_subfolder_skipped_without_range(self):
        first=self.dl().download(self.f,self.root)
        changed=dict(self.f,path_parts=['different','video.mp4']);network=Network()
        result=self.dl(network=network).download(changed,self.root)
        self.assertEqual(result,first);self.assertEqual(network.requests,[])
    def test_other_share_and_file_id_matching_hash_reuses_completed(self):
        first=self.dl().download(self.f,self.root)
        api=API();api.sid='second';changed=dict(self.f,fid='b',display='renamed.mp4',file_name='renamed.mp4',path_parts=['renamed.mp4'])
        network=Network();dl=self.dl(api,network);self.assertEqual(dl.download(changed,self.root),first)
        self.assertEqual(dl.result_kind,'existing');self.assertEqual(network.requests,[])
    def test_cross_share_partial_continues_original_path(self):
        stop=threading.Event()
        with self.assertRaises(Paused):self.dl(stop=stop,progress=lambda d,t,p:stop.set() if d>=262144 else None).download(self.f,self.root)
        api=API();api.sid='second';changed=dict(self.f,fid='b',path_parts=['elsewhere','video.mp4']);network=Network()
        result=self.dl(api,network).download(changed,self.root)
        self.assertEqual(result,output_path(self.root,'test',self.f));self.assertEqual(network.requests[0][0],262144)
        self.assertEqual(result.read_bytes(),DATA);self.assertFalse(output_path(self.root,'second',changed).exists())
    def test_loose_renamed_file_hash_match_skips(self):
        loose=self.root/'old'/'custom-name.mp4';loose.parent.mkdir();loose.write_bytes(DATA)
        network=Network();result=self.dl(network=network).download(self.f,self.root)
        self.assertEqual(result,loose.resolve());self.assertEqual(network.requests,[])
    def test_same_name_and_size_different_content_waits_for_choice(self):
        loose=self.root/'old'/'video.mp4';loose.parent.mkdir();loose.write_bytes(b'x'*len(DATA))
        network=Network()
        with self.assertRaises(PotentialDuplicate):self.dl(network=network).download(self.f,self.root)
        self.assertEqual(network.requests,[]);self.assertEqual(loose.read_bytes(),b'x'*len(DATA))
    def test_user_can_download_different_content_without_overwrite(self):
        loose=output_path(self.root,'test',self.f);loose.parent.mkdir(parents=True);loose.write_bytes(b'x'*len(DATA))
        dl=self.dl();dl.allow_suspect=True;result=dl.download(self.f,self.root)
        self.assertNotEqual(result,loose);self.assertEqual(result.read_bytes(),DATA);self.assertEqual(loose.read_bytes(),b'x'*len(DATA))
    def test_completed_stat_cache_avoids_rehashing_whole_file(self):
        first=self.dl().download(self.f,self.root)
        changed=dict(self.f,path_parts=['elsewhere','video.mp4'])
        with patch('download_catalog.Catalog.hash',side_effect=AssertionError('no rehash needed')):
            self.assertEqual(self.dl().download(changed,self.root),first)
    def test_legacy_record_without_source_can_resume_after_move(self):
        stop=threading.Event()
        with self.assertRaises(Paused):self.dl(stop=stop,progress=lambda d,t,p:stop.set() if d>=262144 else None).download(self.f,self.root)
        target=output_path(self.root,'test',self.f);record=target.with_name(target.name+'.quark.json')
        state=json.loads(record.read_text('utf-8'));state.pop('source',None);record.write_text(json.dumps(state),'utf-8')
        network=Network();changed=dict(self.f,path_parts=['new','video.mp4'])
        self.assertEqual(self.dl(network=network).download(changed,self.root),target)
        self.assertEqual(network.requests[0][0],262144)
    def test_missing_json_partial_is_not_blindly_appended(self):
        target=output_path(self.root,'test',self.f);target.parent.mkdir(parents=True)
        part=target.with_name(target.name+'.quark.part');part.write_bytes(DATA[:262144])
        from verify_direct import ProbeError
        with self.assertRaises(ProbeError):self.dl().download(self.f,self.root)
        self.assertEqual(part.read_bytes(),DATA[:262144])
    def test_download_into_subfolder_checks_whole_library(self):
        first=self.dl().download(self.f,self.root)
        changed=dict(self.f,fid='b',file_name='other.mp4',display='other.mp4',path_parts=['other.mp4'])
        dl=self.dl();dl.library_root=self.root
        self.assertEqual(dl.download(changed,self.root/'new-subfolder'),first)
    def test_checksum_failed_data_is_not_rehashed_or_redownloaded(self):
        from direct_download import ChecksumMismatch
        with self.assertRaises(ChecksumMismatch):self.dl(api=API('0'*32)).download(self.f,self.root)
        network=Network()
        with patch('direct_download.hashlib.sha256',wraps=__import__('hashlib').sha256) as h:
            with self.assertRaises(ChecksumMismatch):self.dl(api=API('0'*32),network=network).download(self.f,self.root)
            # Identity digests are tiny. No saved data segment is read/hashed again.
            self.assertTrue(all(not c.args or len(c.args[0])<1000 for c in h.call_args_list))
        self.assertEqual(network.requests,[])

if __name__=='__main__':unittest.main()
