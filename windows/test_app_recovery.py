import hashlib
import io
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from email.message import Message
from direct_app import App,eta_text
from direct_download import Downloader,Paused,ChecksumMismatch
from local_state import Store
from verify_direct import Probe
from test_direct_download import API,Network,DATA

class RecoveryTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'settings')
    def tearDown(self):self.tmp.cleanup()
    def test_windows_encrypted_session_roundtrip(self):
        self.store.save_session('__puus=synthetic-secret','1234')
        raw=(self.store.directory/'session.bin').read_bytes()
        self.assertNotIn(b'synthetic-secret',raw)
        self.assertEqual(self.store.load_session(),{'cookie':'__puus=synthetic-secret','password':'1234'})
        self.store.forget();self.assertEqual(self.store.load_session(),{})
    def test_server_rotated_cookie_persisted(self):
        self.store.save_session('__puus=old-test','')
        class Response(io.BytesIO):
            def __init__(self):
                super().__init__(b'{"code":0,"data":{}}');self.headers=Message();self.headers.add_header('Set-Cookie','__puus=new-test; Secure; Path=/')
        class Opener:
            def open(self,*a,**k):return Response()
        p=Probe('__puus=old-test',on_cookie=lambda c:self.store.save_session(c,''));p.opener=Opener();p.call('member')
        self.assertEqual(self.store.load_session()['cookie'],'__puus=new-test')
    def test_eta_has_remaining_and_beijing_finish(self):
        now=datetime(2026,10,1,12,0,tzinfo=timezone(timedelta(hours=8)))
        value=eta_text(600,10,5,'下载中',now)
        self.assertIn('1 分',value);self.assertIn('12:01:00',value)
        self.assertIn('估算',eta_text(600,10,1,'下载中',now))
        self.assertIn('校验',eta_text(0,10,5,'正在校验完整文件',now))
    def test_reopen_restore_then_one_click_resume(self):
        import tkinter as tk
        f={'fid':'123','size':len(DATA),'display':'test.mp4','path_parts':['test.mp4'],'share_fid_token':'MUST_NOT_PERSIST'}
        stop=threading.Event()
        with self.assertRaises(Paused):
            Downloader(API(),stop=stop,opener=Network(),chunk_size=262144,emit=lambda s:None,
                       progress=lambda n,t,p:stop.set() if n>=262144 else None).download(f,self.root/'downloads')
        first=tk.Tk();first.withdraw();a=App(first,self.store)
        a.link.set('https://pan.quark.cn/s/test?pwd=secretcode');a.password.set('secretcode');a.cookie.set('__puus=fake')
        a.destination.set(str(self.root/'downloads'));a.rows=[f];a.statuses={'123':'下载中'};a.queue_ids=['123']
        a.auth_values();a.save_history();a.close()
        saved=(self.store.directory/'tasks.json').read_text('utf-8')
        for secret in ('MUST_NOT_PERSIST','__puus','secretcode'):self.assertNotIn(secret,saved)
        second=tk.Tk();second.withdraw();b=App(second,self.store);network=Network()
        class FakeProbe(API):
            def __init__(self,cookie,emit,on_cookie):super().__init__();self.cookie=cookie;self.before={};self.files=[f]
            def scan(self,link,password):return self.files
        def factory(*args,**kwargs):return Downloader(*args,**kwargs,opener=network,chunk_size=262144)
        try:
            self.assertEqual(b.cookie.get(),'__puus=fake');self.assertEqual(b.tree.set('0','state'),'未完成，可恢复')
            with patch('direct_app.Probe',FakeProbe),patch('direct_app.Downloader',side_effect=factory):
                b.resume_button.invoke();deadline=time.monotonic()+10
                while b.busy and time.monotonic()<deadline:second.update();time.sleep(.02)
            self.assertFalse(b.busy);self.assertEqual(b.tree.set('0','state'),'已完成')
            self.assertEqual(min(x[0] for x in network.requests),262144)
            self.assertEqual(Path(b.last_path).read_bytes(),DATA)
            self.assertEqual(self.store.load_tasks()['statuses']['123'],'已完成')
        finally:b.close()
    def test_repair_button_restores_and_saves_completed_file(self):
        import tkinter as tk
        f={'fid':'123','size':len(DATA),'display':'test.mp4','path_parts':['test.mp4']}
        with self.assertRaises(ChecksumMismatch):
            Downloader(API('0'*32),opener=Network(),chunk_size=262144,emit=lambda s:None).download(f,self.root/'downloads')
        self.store.save_tasks({'files':[f],'queue_ids':['123'],'statuses':{'123':'校验未通过，数据保留'},
                               'destination':str(self.root/'downloads'),'link':'https://pan.quark.cn/s/test'})
        root=tk.Tk();root.withdraw();app=App(root,self.store)
        class FakeProbe(API):
            def __init__(self,cookie,emit,on_cookie):
                super().__init__('0'*32);self.cookie=cookie;self.before={};self.files=[f]
            def scan(self,link,password):return self.files
        def factory(*args,**kwargs):return Downloader(*args,**kwargs,opener=Network(),chunk_size=262144)
        try:
            with patch('direct_app.Probe',FakeProbe),patch('direct_app.Downloader',side_effect=factory),patch('direct_app.messagebox.askyesno',return_value=True):
                app.repair_button.invoke();deadline=time.monotonic()+10
                while app.busy and time.monotonic()<deadline:root.update();time.sleep(.02)
            self.assertFalse(app.busy);self.assertEqual(Path(app.last_path).read_bytes(),DATA)
            self.assertEqual(self.store.load_tasks()['statuses']['123'],'已完成（源数据核对）')
        finally:app.close()
    def test_export_button_works_offline_without_login(self):
        import tkinter as tk
        f={'fid':'123','size':len(DATA),'display':'test.mp4','path_parts':['test.mp4']}
        with self.assertRaises(ChecksumMismatch):
            Downloader(API('0'*32),opener=Network(),chunk_size=262144,emit=lambda s:None).download(f,self.root/'downloads')
        self.store.save_tasks({'files':[f],'queue_ids':['123'],'statuses':{'123':'校验未通过'},
                               'destination':str(self.root/'downloads'),'link':'https://pan.quark.cn/s/test'})
        root=tk.Tk();root.withdraw();app=App(root,self.store)
        try:
            with patch('direct_app.Probe',side_effect=AssertionError('network forbidden')):
                app.export_button.invoke();deadline=time.monotonic()+10
                while app.busy and time.monotonic()<deadline:root.update();time.sleep(.02)
            self.assertFalse(app.busy);self.assertEqual(Path(app.last_path).read_bytes(),DATA)
            self.assertIn('校验待确认',Path(app.last_path).name)
            self.assertEqual(self.store.load_tasks()['statuses']['123'],'已保存（校验待确认）')
        finally:app.close()

if __name__=='__main__':unittest.main(verbosity=2)
