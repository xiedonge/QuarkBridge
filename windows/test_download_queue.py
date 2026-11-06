import hashlib
import gc
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from download_queue import DownloadQueue
from direct_download import Downloader,ChecksumMismatch,Paused
from local_state import Store
from test_direct_download import API,Network,DATA

class QueueTests(unittest.TestCase):
    def setUp(self):
        # Each GUI test creates a separate Tcl interpreter. Reclaim retired GUI
        # cycles on the owner thread before tests that allocate in workers.
        gc.collect()
        self.tmp=tempfile.TemporaryDirectory();self.dest=Path(self.tmp.name);self.store=Store(self.dest/'settings')
        self.queue=DownloadQueue(self.store);self.api=API()
        self.files=[{'fid':str(i),'size':len(DATA),'display':f'视频{i}.mp4','path_parts':[f'视频{i}.mp4']} for i in range(3)]
    def tearDown(self):self.tmp.cleanup()
    def add(self,files=None,api=None,password=''):
        return self.queue.add(api or self.api,files or self.files,'https://pan.quark.cn/s/test?pwd='+password,password,self.dest/'downloads',remember=True)
    def run_queue(self,stop=None,factory=None):
        self.queue.run(stop or threading.Event(),lambda p:self.fail('unexpected rescan'),factory or
                       (lambda api,n:Downloader(api,opener=Network(),chunk_size=262144,emit=lambda s:None)),lambda *a:None)
    def test_order_and_duplicate_suppression(self):
        self.assertEqual(self.add(),3);self.assertEqual(self.add(),0)
        self.run_queue();jobs=self.queue.snapshot()
        self.assertEqual([j['file']['fid'] for j in jobs],['0','1','2'])
        self.assertTrue(all(j['status'] in ('已完成','已存在，已跳过') and Path(j['path']).read_bytes()==DATA for j in jobs))
    def test_append_during_active_download_runs_next(self):
        self.add([self.files[0]]);entered=threading.Event();release=threading.Event();calls=[]
        def factory(api,n):
            class DL:
                def download(inner,f,dest):
                    calls.append(f['fid'])
                    if f['fid']=='0':entered.set();release.wait(5)
                    return Downloader(api,opener=Network(),chunk_size=262144,emit=lambda s:None).download(f,dest)
            return DL()
        thread=threading.Thread(target=lambda:self.run_queue(factory=factory));thread.start()
        try:
            self.assertTrue(entered.wait(3));self.add([self.files[1]]);release.set();thread.join(5)
            self.assertFalse(thread.is_alive());self.assertEqual(calls,['0','1'])
            self.assertTrue(all(j['status'] in ('已完成','已存在，已跳过') for j in self.queue.snapshot()))
        finally:release.set();thread.join(5)
    def test_failure_and_checksum_mismatch_continue_without_repair(self):
        self.add();calls=[]
        def factory(api,n):
            class DL:
                def download(inner,f,dest):
                    calls.append(f['fid'])
                    if f['fid']=='0':raise OSError('synthetic failure')
                    if f['fid']=='1':raise ChecksumMismatch('digest mismatch')
                    return Path(dest)/'complete.mp4'
                def repair(inner,*a):raise AssertionError('must not audit source automatically')
            return DL()
        self.run_queue(factory=factory)
        self.assertEqual(calls,['0','1','2'])
        self.assertEqual([j['status'] for j in self.queue.snapshot()],['失败，可重试','校验待确认','已完成'])
        self.assertFalse(self.queue.has_pending())
    def test_pause_restart_resume_preserves_partial_and_order(self):
        self.add();stop=threading.Event()
        self.run_queue(stop,lambda api,n:Downloader(api,opener=Network(),chunk_size=262144,stop=stop,emit=lambda s:None,
                       progress=lambda d,t,p:stop.set() if d>=262144 else None))
        self.assertEqual([j['status'] for j in self.queue.snapshot()],['已暂停','等待下载','等待下载'])
        restored=DownloadQueue(self.store);network=Network()
        class Fresh(API):
            def scan(inner,link,password):return self.files
        restored.run(threading.Event(),lambda p:Fresh(),lambda api,n:Downloader(api,opener=network,chunk_size=262144,emit=lambda s:None),lambda *a:None)
        self.assertEqual(network.requests[0][0],262144)
        self.assertTrue(all(j['status'] in ('已完成','已存在，已跳过') for j in restored.snapshot()))
    def test_multiple_shares_and_encrypted_passwords(self):
        self.add([self.files[0]],password='secret-extraction-code')
        second=API();second.sid='second'
        self.queue.add(second,[self.files[1]],'https://pan.quark.cn/s/second','other-password',self.dest/'downloads')
        plain=(self.store.directory/'queue.json').read_text('utf-8')
        for secret in ('secret-extraction-code','other-password','pwd=','share_fid_token','Cookie'):self.assertNotIn(secret,plain)
        encrypted=(self.store.directory/'queue-secrets.bin').read_bytes();self.assertNotIn(b'secret-extraction-code',encrypted)
        self.assertEqual(len(DownloadQueue(self.store).passwords),2)
        restored=DownloadQueue(self.store);used=[]
        class Fresh(API):
            def __init__(inner,password):super().__init__();used.append(password)
            def scan(inner,link,password):
                from verify_direct import parse
                inner.sid=parse(link)[0];return self.files
        restored.run(threading.Event(),Fresh,lambda api,n:Downloader(api,opener=Network(),chunk_size=262144,emit=lambda s:None),lambda *a:None)
        self.assertEqual([j['sid'] for j in restored.snapshot()],['test','second'])
        self.assertTrue(all(j['status'] in ('已完成','已存在，已跳过') for j in restored.snapshot()))
        self.assertEqual(used,['secret-extraction-code','other-password'])
    def test_reorder_remove_keep_data_and_retry(self):
        self.add();ids=[j['id'] for j in self.queue.snapshot()]
        self.queue.edit([ids[2]],'up');self.assertEqual([j['file']['fid'] for j in self.queue.snapshot()],['0','2','1'])
        self.queue.edit([ids[0]],'remove');self.assertEqual(len(self.queue.snapshot()),2)
        self.run_queue();path=Path(self.queue.snapshot()[0]['path']);self.queue.edit([ids[2]],'remove');self.assertTrue(path.exists())
        remaining=self.queue.jobs[0];remaining['status']='失败，可重试';self.queue.edit([remaining['id']],'retry')
        self.assertEqual(remaining['status'],'等待下载')
    def test_running_record_recovers_as_paused(self):
        self.add([self.files[0]]);self.queue.jobs[0]['status']='下载中';self.queue.save()
        self.assertEqual(DownloadQueue(self.store).snapshot()[0]['status'],'已暂停')
    def test_gui_add_other_share_while_downloading(self):
        import tkinter as tk
        from direct_app import App
        root=tk.Tk();root.withdraw();app=App(root,self.store);entered=threading.Event();release=threading.Event()
        app.link.set('https://pan.quark.cn/s/test');app.destination.set(str(self.dest/'downloads'))
        app.api=self.api;app.api.files=[self.files[0]];app.populate(app.api.files);app.snapshot=app.values();app.set_busy(False)
        class Fresh(API):
            def __init__(inner,cookie,emit,on_cookie):super().__init__();inner.sid='second';inner.cookie=cookie
            def scan(inner,link,password):return [self.files[1]]
        def factory(api,*a,**kw):
            dl=Downloader(api,*a,**kw,opener=Network(),chunk_size=262144);original=dl.download
            def download(f,destination):
                if api.sid=='test':entered.set();release.wait(5)
                return original(f,destination)
            dl.download=download;return dl
        def widgets(parent):
            for w in parent.winfo_children():
                yield w;yield from widgets(w)
        def pump_until(predicate,seconds=5):
            deadline=time.monotonic()+seconds
            while not predicate() and time.monotonic()<deadline:root.update();time.sleep(.01)
            self.assertTrue(predicate())
        try:
            with patch('direct_app.Probe',Fresh),patch('direct_app.Downloader',side_effect=factory):
                app.download_button.invoke();pump_until(entered.is_set)
                window=app.add_share_dialog();all_widgets=list(widgets(window))
                entries=[w for w in all_widgets if w.winfo_class()=='TEntry'];entries[0].insert(0,'https://pan.quark.cn/s/second')
                scan=next(w for w in all_widgets if w.winfo_class()=='TButton' and w.cget('text')=='解析分享')
                add=next(w for w in all_widgets if w.winfo_class()=='TButton' and w.cget('text')=='所选文件加入队列')
                scan.invoke();pump_until(lambda:str(add.cget('state'))=='normal');add.invoke()
                self.assertEqual(len(app.download_queue.snapshot()),2);self.assertTrue(app.busy)
                release.set();pump_until(lambda:not app.busy)
                jobs=app.download_queue.snapshot();self.assertEqual([j['status'] for j in jobs],['已完成','已存在，已跳过'])
                self.assertTrue(all(Path(j['path']).read_bytes()==DATA for j in jobs))
        finally:release.set();pump_until(lambda:not app.busy);app.close()
    def test_gui_failure_reason_and_one_click_retry(self):
        import tkinter as tk
        from direct_app import App
        from verify_direct import ProbeError
        root=tk.Tk();root.withdraw();app=App(root,self.store);calls=[]
        app.link.set('https://pan.quark.cn/s/test');app.destination.set(str(self.dest/'downloads'))
        app.api=self.api;app.api.files=[self.files[0]];app.populate(app.api.files);app.snapshot=app.values();app.set_busy(False)
        def factory(api,*a,**kw):
            dl=Downloader(api,*a,**kw,opener=Network(),chunk_size=262144);download=dl.download
            def run(f,dest):
                calls.append(f['fid'])
                if len(calls)==1:raise ProbeError('网络连接中断，断点已保留。')
                return download(f,dest)
            dl.download=run;return dl
        def finished():
            deadline=time.monotonic()+5
            while app.busy and time.monotonic()<deadline:root.update();time.sleep(.01)
            self.assertFalse(app.busy)
        try:
            with patch('direct_app.Downloader',side_effect=factory):
                app.download_button.invoke();finished()
                self.assertIn('网络连接中断',app.error_detail.get());self.assertIn('网络连接中断',app.tree.set('0','reason'))
                self.assertEqual(str(app.add_share_button.cget('state')),'normal')
                self.assertFalse(app.verify_button.winfo_manager());self.assertFalse(app.repair_button.winfo_manager())
                app.retry_button.invoke();finished()
            self.assertEqual(app.tree.set('0','state'),'已完成');self.assertEqual(app.error_detail.get(),'')
            self.assertEqual(len(calls),2);self.assertEqual(Path(app.last_path).read_bytes(),DATA)
        finally:app.close()

if __name__=='__main__':unittest.main()
