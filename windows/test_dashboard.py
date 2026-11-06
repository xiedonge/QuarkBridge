import gc
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from direct_app import App
from download_queue import DownloadQueue
from direct_download import Downloader,atomic
from local_state import Store,StateError
from task_view import share_input,visible,progress_text,next_step
from test_direct_download import API,Network,DATA


class DashboardTests(unittest.TestCase):
    def setUp(self):
        gc.collect();self.tmp=tempfile.TemporaryDirectory();self.dest=Path(self.tmp.name);self.store=Store(self.dest/'settings')
        self.file={'fid':'123','size':len(DATA),'display':'示例视频.mp4','path_parts':['示例视频.mp4']}
        self.api=API();self.api.files=[self.file]
    def tearDown(self):self.tmp.cleanup()
    def add(self,q):return q.add(self.api,[self.file],'https://pan.quark.cn/s/test','',self.dest/'downloads')
    def gui(self):
        import tkinter as tk
        root=tk.Tk();root.withdraw();app=App(root,self.store)
        app.destination.set(str(self.dest/'downloads'));app.api=self.api;app.populate(self.api.files);app.snapshot=app.values();app.set_busy(False)
        return root,app
    def pump(self,root,predicate,seconds=6):
        until=time.monotonic()+seconds
        while not predicate() and time.monotonic()<until:root.update();time.sleep(.01)
        self.assertTrue(predicate())
    def test_share_message_and_explicit_password(self):
        text='我用夸克分享了「资料」\n链接：https://pan.quark.cn/s/abc123#/list/share/folder\n提取码：a1B2'
        self.assertEqual(share_input(text),('https://pan.quark.cn/s/abc123#/list/share/folder','a1B2'))
        self.assertEqual(share_input(text,'9876')[1],'9876')
    def test_markdown_query_password(self):
        self.assertEqual(share_input('[链接](https://pan.quark.cn/s/abc123?pwd=1234)'),('https://pan.quark.cn/s/abc123?pwd=1234','1234'))
    def test_reject_multiple_or_non_quark_links(self):
        for text in ('https://example.com/abc','https://pan.quark.cn/s/a https://pan.quark.cn/s/b','没有链接'):
            with self.assertRaises(ValueError):share_input(text)
    def test_filters_progress_and_actionable_errors(self):
        job={'file':self.file,'status':'已暂停','downloaded':len(DATA)//2}
        self.assertTrue(visible(job,'等待 / 暂停','视频'));self.assertFalse(visible(job,'下载中'))
        self.assertIn('50.0%',progress_text(job))
        job.update(status='失败，可重试',error='服务器返回 HTTP 429')
        self.assertIn('1 或 2',next_step(job))
        job.update(status='校验待确认');self.assertIn('无需重新下载',next_step(job))
    def test_progress_saved_with_pause_and_restart(self):
        q=DownloadQueue(self.store);self.add(q);stop=threading.Event()
        def progress(n,total,phase):
            q.progress(n,total,phase)
            if n>=262144:stop.set()
        q.run(stop,lambda p:self.api,lambda a,n:Downloader(a,stop=stop,progress=progress,opener=Network(),chunk_size=262144,emit=lambda s:None),lambda *a:None)
        restored=DownloadQueue(self.store).snapshot()[0]
        self.assertEqual(restored['status'],'已暂停');self.assertGreaterEqual(restored['downloaded'],262144)
    def test_progress_writes_throttled_and_local_scan_preserves_counter(self):
        q=DownloadQueue(self.store);self.add(q);q.active_id=q.jobs[0]['id']
        with patch.object(self.store,'save_queue',wraps=self.store.save_queue) as save,patch('download_queue.time.monotonic',return_value=100):
            for n in range(20):q.progress(n,100,'下载中')
            self.assertEqual(save.call_count,1)
            q.progress(0,100,'检查本地文件');self.assertEqual(q.jobs[0]['downloaded'],19)
    def test_failed_add_rolls_back_live_and_saved_queue(self):
        q=DownloadQueue(self.store);self.add(q);old=q.snapshot();active=q.jobs[0]
        other=dict(self.file,fid='other')
        with patch.object(self.store,'save_queue',side_effect=StateError('synthetic write error')):
            with self.assertRaises(StateError):q.add(self.api,[other],'https://pan.quark.cn/s/test','',self.dest/'downloads')
        self.assertEqual(q.snapshot(),old);self.assertIs(q.jobs[0],active);self.assertEqual(len(q.live),1)
        self.assertEqual(DownloadQueue(self.store).snapshot(),old)
    def test_add_preserves_active_job_identity(self):
        q=DownloadQueue(self.store);self.add(q);active=q.jobs[0];active['status']='下载中'
        q.add(self.api,[dict(self.file,fid='second')],'https://pan.quark.cn/s/test','',self.dest/'downloads')
        active['status']='已完成';self.assertEqual(q.snapshot()[0]['status'],'已完成')
    def test_missing_finished_file_can_be_added_again(self):
        q=DownloadQueue(self.store);self.add(q);key=q.jobs[0]['id'];q.jobs[0].update(status='已完成',path=str(self.dest/'missing.mp4'))
        self.assertEqual(self.add(q),1);self.assertEqual(len(q.jobs),1);self.assertEqual(q.jobs[0]['id'],key)
        self.assertEqual(q.jobs[0]['status'],'等待下载')
    def test_deleted_completed_file_downloads_again_without_stale_checkpoint(self):
        dl=Downloader(self.api,opener=Network(),chunk_size=262144,emit=lambda s:None)
        path=dl.download(self.file,self.dest/'downloads');path.unlink()
        network=Network();restored=Downloader(self.api,opener=network,chunk_size=262144,emit=lambda s:None).download(self.file,self.dest/'downloads')
        self.assertEqual(restored.read_bytes(),DATA);self.assertEqual(network.requests[0][0],0)
    def test_dashboard_filters_selection_and_context_actions(self):
        root,app=self.gui()
        try:
            self.add(app.download_queue);app.refresh_queue();job=app.download_queue.jobs[0];key=job['id']
            self.assertEqual(app.notebook.select(),str(app.task_page))
            app.queue_tree.selection_set(key);job.update(status='下载中',downloaded=len(DATA)//2);app.refresh_queue()
            self.assertEqual(app.queue_tree.selection(),(key,));self.assertIn('50.0%',app.queue_tree.set(key,'progress'))
            app.filter.set('需要处理');app.refresh_queue();self.assertFalse(app.queue_tree.get_children())
            job.update(status='失败，可重试',error='HTTP 429');app.refresh_queue();app.queue_tree.selection_set(key);app.task_info()
            self.assertIn('1 或 2',app.task_detail.get());self.assertEqual(app.context_buttons['重试所选'].winfo_manager(),'pack')
            self.assertFalse(app.context_buttons['保存现有文件'].winfo_manager())
            job.update(status='校验待确认');app.refresh_queue()
            self.assertEqual(app.context_buttons['保存现有文件'].winfo_manager(),'pack')
            self.assertFalse(app.context_buttons['重试所选'].winfo_manager())
            app.search.set('不存在');self.assertFalse(app.queue_tree.get_children())
        finally:app.close()
    def test_gui_queue_write_failure_does_not_start(self):
        root,app=self.gui()
        try:
            with patch.object(self.store,'save_queue',side_effect=StateError('synthetic')),patch.object(app,'start_queue') as start:
                app.download_button.invoke();self.assertFalse(start.called)
            self.assertFalse(app.download_queue.jobs);self.assertIn('队列保存失败',app.error_detail.get())
        finally:app.close()
    def test_finished_queue_does_not_replay_old_share(self):
        root,app=self.gui()
        try:
            self.add(app.download_queue);app.download_queue.jobs[0]['status']='已完成';app.refresh_queue()
            self.assertEqual(str(app.resume_button.cget('state')),'disabled')
            with patch.object(app,'start') as start:app.resume();self.assertFalse(start.called)
            self.assertIn('没有等待任务',app.status.get())
        finally:app.close()
    def test_speed_profile_migrates_once_then_remembers_user_choice(self):
        self.store.save_tasks({'connections':4})
        root,app=self.gui()
        self.assertEqual(app.connections.get(),'32');app.connections.set('8');app.close()
        root,restored=self.gui()
        try:self.assertEqual(restored.connections.get(),'8')
        finally:restored.close()
    def test_resume_offset_is_not_counted_as_network_speed(self):
        root,app=self.gui()
        try:
            root.after_cancel(app.timer)
            app.latest_progress=(0,8*1024**3,'检查本地文件')
            with patch('direct_app.time.monotonic',return_value=100):app.pump()
            app.root.after_cancel(app.timer)
            app.latest_progress=(4*1024**3,8*1024**3,'下载中')
            with patch('direct_app.time.monotonic',return_value=110):app.pump()
            app.root.after_cancel(app.timer)
            self.assertIn('0.00 MiB/s',app.status.get());self.assertIn('估算',app.eta.get())
            app.latest_progress=(4*1024**3+10*1024**2,8*1024**3,'下载中')
            with patch('direct_app.time.monotonic',return_value=120):app.pump()
            self.assertIn('1.00 MiB/s',app.status.get())
        finally:app.close()
    def test_close_pauses_then_exits_once_and_resumes(self):
        root,app=self.gui();entered=threading.Event();release=threading.Event()
        def factory(api,*args,**kwargs):
            return Downloader(api,*args,**kwargs,opener=Network(),chunk_size=262144)
        def checkpoint(path,state):
            atomic(path,state)
            if len(state['chunks'])==1 and not entered.is_set():entered.set();release.wait(5)
        try:
            with patch('direct_app.Downloader',side_effect=factory),patch('direct_download.atomic',side_effect=checkpoint),patch('direct_app.messagebox.showinfo') as info:
                app.download_button.invoke();self.pump(root,entered.is_set);app.close()
                self.assertTrue(app.closing);self.assertFalse(app.closed);self.assertFalse(info.called)
                release.set();self.pump(root,lambda:app.closed)
            jobs=DownloadQueue(self.store).snapshot();self.assertEqual(jobs[0]['status'],'已暂停')
            network=Network()
            class Fresh(API):
                def scan(inner,*a):return [self.file]
            resumed=DownloadQueue(self.store)
            resumed.run(threading.Event(),lambda p:Fresh(),lambda a,n:Downloader(a,opener=network,chunk_size=262144,emit=lambda s:None),lambda *a:None)
            self.assertGreaterEqual(network.requests[0][0],262144)
            self.assertEqual(Path(resumed.snapshot()[0]['path']).read_bytes(),DATA)
        finally:
            release.set()
            if not app.closed:self.pump(root,lambda:not app.busy);app.close()

if __name__=='__main__':unittest.main()
