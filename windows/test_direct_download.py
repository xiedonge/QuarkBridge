import hashlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch
from direct_download import Downloader,Paused,ProbeError,ChecksumMismatch,output_path,file_lock,export_unverified

DATA=bytes(range(256))*4096
class API:
    sid='test'
    def __init__(self,md5=None):self.calls=[];self.md5=md5 or hashlib.md5(DATA).hexdigest()
    def direct_link(self,f,refresh=False):
        self.calls.append(refresh)
        return 'https://dl.quark.cn/test',{'Cookie':'test-secret'},self.md5
class Response(io.BytesIO):
    status=206
    def __init__(self,start,end,wrong=False):
        super().__init__(DATA[start:end+1]);self.headers={'Content-Range':f'bytes {start}-{end}/{len(DATA)+(1 if wrong else 0)}','ETag':'"stable-source"'}
class Network:
    def __init__(self,failures=0,wrong=False):self.requests=[];self.failures=failures;self.wrong=wrong
    def open(self,request,timeout):
        start,end=map(int,request.get_header('Range').split('=')[1].split('-'))
        self.requests.append((start,end))
        if self.failures:
            self.failures-=1;raise HTTPError('https://dl.quark.cn/test',403,'expired',{},None)
        return Response(start,end,self.wrong)
class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.dest=Path(self.tmp.name)
        self.f={'fid':'123','size':len(DATA),'file_name':'中文视频.mp4','display':'课程/中文视频.mp4','path_parts':['课程','中文视频.mp4']}
    def tearDown(self):self.tmp.cleanup()
    def dl(self,**kwargs):return Downloader(kwargs.pop('api',API()),opener=kwargs.pop('network',Network()),emit=lambda s:None,chunk_size=256*1024,**kwargs)
    def test_complete_original_name_and_hash(self):
        path=self.dl().download(self.f,self.dest)
        self.assertEqual(path.name,'中文视频.mp4');self.assertEqual(path.read_bytes(),DATA)
        record=path.with_name(path.name+'.quark.json').read_text('utf-8')
        self.assertNotIn('test-secret',record);self.assertNotIn('download_url',record)
        self.assertEqual(json.loads(record)['validation'],'length+server_md5')
    def test_pause_then_new_process_resume(self):
        stop=threading.Event()
        def progress(done,total,phase):
            if done>=256*1024:stop.set()
        with self.assertRaises(Paused):self.dl(stop=stop,progress=progress).download(self.f,self.dest)
        network=Network();path=self.dl(network=network).download(self.f,self.dest)
        self.assertEqual(network.requests[0][0],256*1024);self.assertEqual(path.read_bytes(),DATA)
    def test_expired_link_refresh_and_retry(self):
        api=API();network=Network(failures=1)
        with patch('threading.Event.wait',return_value=False):path=self.dl(api=api,network=network).download(self.f,self.dest)
        self.assertEqual(api.calls,[False,True]);self.assertEqual(path.read_bytes(),DATA)
    def test_wrong_range_stops_without_output(self):
        with self.assertRaises(ProbeError):self.dl(network=Network(wrong=True)).download(self.f,self.dest)
        self.assertFalse(output_path(self.dest,'test',self.f).exists())
    def test_mid_response_disconnect_rolls_back_and_retries(self):
        class CutResponse(Response):
            def read(self,n=-1):
                if self.tell()>0:raise OSError('connection lost')
                return super().read(min(n,100))
        class CutNetwork(Network):
            def open(self,request,timeout):
                r=super().open(request,timeout)
                if len(self.requests)==1:return CutResponse(*self.requests[-1])
                return r
        api=API();network=CutNetwork()
        with patch('threading.Event.wait',return_value=False):path=self.dl(api=api,network=network).download(self.f,self.dest)
        self.assertEqual(network.requests[0],network.requests[1]);self.assertEqual(path.read_bytes(),DATA)
    def test_crash_tail_is_discarded_on_resume(self):
        stop=threading.Event()
        with self.assertRaises(Paused):self.dl(stop=stop,progress=lambda n,t,p:stop.set() if n>=256*1024 else None).download(self.f,self.dest)
        part=output_path(self.dest,'test',self.f).with_name('中文视频.mp4.quark.part')
        with part.open('ab') as out:out.write(b'uncommitted tail')
        path=self.dl().download(self.f,self.dest)
        self.assertEqual(path.read_bytes(),DATA)
    def test_bad_md5_stops_without_output(self):
        with self.assertRaises(ProbeError):self.dl(api=API('0'*32)).download(self.f,self.dest)
        self.assertFalse(output_path(self.dest,'test',self.f).exists())
    def mismatched_file(self):
        with self.assertRaises(ChecksumMismatch):self.dl(api=API('0'*32)).download(self.f,self.dest)
        path=output_path(self.dest,'test',self.f)
        return path,path.with_name(path.name+'.quark.part'),path.with_name(path.name+'.quark.json')
    def test_bad_md5_preserves_diagnostics(self):
        path,part,record=self.mismatched_file();state=json.loads(record.read_text('utf-8'))
        self.assertEqual(state['phase'],'checksum_failed');self.assertEqual(part.read_bytes(),DATA)
        self.assertEqual(state['computed_md5'],hashlib.md5(DATA).hexdigest())
    def test_export_existing_bytes_no_network_or_copy(self):
        path,part,record=self.mismatched_file()
        with patch('direct_download.build_opener',side_effect=AssertionError('network forbidden')):
            exported=export_unverified(self.f,self.dest,'test')
        self.assertIn('校验待确认',exported.name);self.assertEqual(exported.read_bytes(),DATA)
        self.assertFalse(part.exists());self.assertFalse(path.exists())
        self.assertEqual(json.loads(record.read_text('utf-8'))['phase'],'exported_unverified')
        self.assertEqual(export_unverified(self.f,self.dest,'test'),exported)
        network=Network()
        self.assertEqual(self.dl(network=network).download(self.f,self.dest),exported)
        self.assertEqual(network.requests,[])
    def test_export_does_not_overwrite_existing(self):
        path,part,record=self.mismatched_file()
        candidate=path.with_name(path.stem+'.校验待确认'+path.suffix);candidate.write_bytes(b'keep')
        with self.assertRaises(ProbeError):export_unverified(self.f,self.dest,'test')
        self.assertEqual(candidate.read_bytes(),b'keep');self.assertTrue(part.exists())
    def test_full_source_match_can_resolve_metadata_mismatch(self):
        path,part,record=self.mismatched_file();network=Network()
        self.dl(api=API('0'*32),network=network).repair(self.f,self.dest)
        self.assertEqual(path.read_bytes(),DATA);self.assertEqual(len(network.requests),4)
        state=json.loads(record.read_text('utf-8'))
        self.assertTrue(state['server_md5_mismatch']);self.assertTrue(state['source_audit']['complete'])
        self.assertEqual(state['validation'],'length+full_source_comparison+local_sha256')
    def test_source_repair_corrects_only_bad_chunk(self):
        path,part,record=self.mismatched_file()
        with part.open('r+b') as src:src.seek(300000);src.write(b'corruption')
        self.dl(api=API('0'*32)).repair(self.f,self.dest)
        self.assertEqual(path.read_bytes(),DATA)
    def test_source_audit_pause_resume_skips_checked_chunks(self):
        path,part,record=self.mismatched_file();stop=threading.Event()
        with self.assertRaises(Paused):
            self.dl(api=API('0'*32),stop=stop,progress=lambda n,t,p:stop.set() if n>=512*1024 else None).repair(self.f,self.dest)
        # First range always rechecks the version; committed audit ranges are skipped.
        network=Network();self.dl(api=API('0'*32),network=network).repair(self.f,self.dest)
        self.assertEqual(path.read_bytes(),DATA)
        self.assertNotIn((256*1024,512*1024-1),network.requests)
    def test_source_version_change_stops_without_final_file(self):
        path,part,record=self.mismatched_file()
        class Changed(Network):
            def open(self,request,timeout):
                r=super().open(request,timeout)
                if len(self.requests)>1:r.headers['ETag']='"changed"'
                return r
        with self.assertRaises(ProbeError):self.dl(api=API('0'*32),network=Changed()).repair(self.f,self.dest)
        self.assertFalse(path.exists());self.assertTrue(part.exists())
    def test_source_without_version_stays_unverified(self):
        path,part,record=self.mismatched_file()
        class Missing(Network):
            def open(self,request,timeout):
                r=super().open(request,timeout);del r.headers['ETag'];return r
        with self.assertRaises(ProbeError):self.dl(api=API('0'*32),network=Missing()).repair(self.f,self.dest)
        self.assertFalse(path.exists());self.assertTrue(part.exists())
    def test_existing_file_never_overwritten(self):
        path=output_path(self.dest,'test',self.f);path.parent.mkdir(parents=True);path.write_bytes(b'valuable')
        with self.assertRaises(ProbeError):self.dl().download(self.f,self.dest)
        self.assertEqual(path.read_bytes(),b'valuable')
    def test_completed_is_verified_and_skipped(self):
        self.dl().download(self.f,self.dest);network=Network();self.dl(network=network).download(self.f,self.dest)
        self.assertEqual(network.requests,[])
    def test_modified_partial_repairs_from_bad_chunk(self):
        stop=threading.Event()
        with self.assertRaises(Paused):self.dl(stop=stop,progress=lambda n,t,p:stop.set() if n>=256*1024 else None).download(self.f,self.dest)
        path=output_path(self.dest,'test',self.f).with_name('中文视频.mp4.quark.part')
        with path.open('r+b') as r:r.write(b'bad')
        self.assertEqual(self.dl().download(self.f,self.dest).read_bytes(),DATA)
    def test_single_writer_lock(self):
        path=output_path(self.dest,'test',self.f);path.parent.mkdir(parents=True)
        with file_lock(path.with_name(path.name+'.quark.lock')):
            with self.assertRaises(ProbeError):self.dl().download(self.f,self.dest)
    def test_unsafe_filename_stays_in_directory(self):
        f=dict(self.f,path_parts=['..','CON','a/b.mp4'])
        path=output_path(self.dest,'test',f)
        self.assertTrue(path.is_relative_to(self.dest.resolve()))
        self.assertNotIn('..',path.parts)
    def test_gui_download_button_completes(self):
        import tkinter as tk
        import time
        from direct_app import App
        from local_state import Store
        root=tk.Tk();root.withdraw();app=App(root,Store(self.dest/'ui-state'))
        app.destination.set(str(self.dest));app.api=API();app.api.files=[self.f];app.snapshot=app.values()
        app.tree.insert('','end',iid='0',values=(self.f['display'],'1 MiB','待下载'));app.tree.selection_set('0');app.set_busy(False)
        def factory(*args,**kwargs):return Downloader(*args,**kwargs,opener=Network(),chunk_size=256*1024)
        try:
            with patch('direct_app.Downloader',side_effect=factory):
                app.download_button.invoke();deadline=time.monotonic()+10
                while app.busy and time.monotonic()<deadline:root.update();time.sleep(.02)
            self.assertFalse(app.busy);self.assertEqual(app.tree.set('0','state'),'已完成')
            self.assertEqual(Path(app.last_path).read_bytes(),DATA)
            self.assertIn('全部下载并校验完成',app.status.get())
        finally:app.close()

if __name__=='__main__':unittest.main(verbosity=2)
