import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from direct_app import App
from local_state import Store
from login_bridge import LoginError
from verify_direct import LoginExpired
from test_share_selection import FILES


def widgets(parent):
    for widget in parent.winfo_children():
        yield widget
        yield from widgets(widget)


class UsabilityGuiTests(unittest.TestCase):
    def setUp(self):
        self.workspace=tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root=tk.Tk()
        self.root.withdraw()
        self.directory=Path(self.workspace.name)
        self.app=App(self.root,Store(self.directory/'data'))
        self.app.destination.set(str(self.directory))
        self.addCleanup(self.app.close)

    def pump_until(self,condition):
        deadline=time.monotonic()+5
        while not condition() and time.monotonic()<deadline:
            self.root.update()
            time.sleep(.01)
        self.assertTrue(condition())

    def test_picker_filter_login_return_and_destination(self):
        class Share:
            sid='fixture'
            def __init__(self,cookie,*_):self.cookie=cookie
            def scan(self,link,code):
                if code!='A1b2':raise AssertionError('share code was lost')
                if self.cookie=='expired':raise LoginExpired('登录已过期')
                return FILES

        app=self.app
        app.remember.set(False)
        window=app.add_share_dialog()
        window.update()
        children=list(widgets(window))
        def button(label):
            return next(w for w in children if w.winfo_class()=='TButton' and w.cget('text')==label)
        def checkbox(label):
            return next(w for w in children if w.winfo_class()=='TCheckbutton' and w.cget('text')==label)
        text=next(w for w in children if w.winfo_class()=='Text')
        quality=next(w for w in children if w.winfo_class()=='TCombobox')
        tree=next(w for w in children if w.winfo_class()=='Treeview')
        pwd,dest,search=[w for w in children if w.winfo_class()=='TEntry']
        text.insert('1.0','分享文案\nhttps://pan.quark.cn/s/fixture\n提取码：A1b2')
        enqueue=button('加入队列')
        scan=button('解析分享')
        login=button('重新登录')
        with patch('share_dialog.Probe',Share):
            scan.invoke()
            self.pump_until(lambda:len(tree.get_children())==5)
            self.assertTrue(enqueue.instate(('disabled',)))
            button('全选筛选结果').invoke()
            checkbox('仅视频').invoke()
            quality.set('1080p')
            self.assertEqual(tree.get_children(),('fullhd','marker'))
            search.insert(0,'第二季')
            self.assertEqual(tree.get_children(),())
            self.assertTrue(enqueue.instate(('disabled',)))
            search.delete(0,'end')
            button('全选筛选结果').invoke()
            app.cookie.set('expired')
            scan.invoke()
            self.pump_until(lambda:app.login_expired)
            self.assertTrue(enqueue.instate(('disabled',)))
            for notice in ('已取消登录','账号校验未通过'):
                with patch('direct_app.capture_login',side_effect=LoginError(notice)):
                    login.invoke()
                    self.pump_until(lambda:not app.login_active)
                self.assertEqual(app.cookie.get(),'expired')
                self.assertIn(notice,app.login_status.get())
                self.assertEqual(quality.get(),'1080p')
                self.assertEqual(tree.set('fullhd','checked'),'已勾选')
            with patch('direct_app.capture_login',return_value='fresh'):
                login.invoke()
                self.pump_until(lambda:not app.login_active)
            self.assertEqual(scan.cget('text'),'继续解析')
            self.assertTrue(enqueue.instate(('disabled',)))
            scan.invoke()
            self.pump_until(lambda:enqueue.instate(('!disabled',)))
            dest.delete(0,'end')
            dest.insert(0,str(self.directory/'changed'))
            enqueue.invoke()
        jobs=app.download_queue.snapshot()
        self.assertEqual([j['file']['fid'] for j in jobs],['fullhd','marker'])
        self.assertTrue(all(j['destination']==str(self.directory/'changed') for j in jobs))
        self.assertTrue(all(j['status']=='等待下载' for j in jobs))

    def test_open_folder_stays_mapped_and_invokable_over_ten_seconds(self):
        app=self.app
        q=app.download_queue
        fixture=type('Share',(),{'sid':'fixture'})()
        q.add(fixture,FILES[:2],'https://pan.quark.cn/s/fixture','',self.directory)
        job,other=q.jobs
        job.update(status='下载中',phase='下载中')
        app.refresh_queue()
        app.queue_tree.selection_set(job['id'])
        app.task_info()
        folder=app.context_buttons['打开所在文件夹']
        self.root.deiconify()
        self.root.update()
        folder.focus_force()
        transitions=[]
        folder.bind('<Unmap>',lambda event:transitions.append('unmap'))
        starts=time.monotonic()
        count=0
        with patch.object(app.queue_tree,'move',wraps=app.queue_tree.move) as moves:
            while time.monotonic()-starts<10.2:
                count+=1
                job['downloaded']=count%16
                app.refresh_queue()
                self.root.update()
                self.assertTrue(folder.winfo_ismapped())
                self.assertEqual(self.root.focus_get(),folder)
                time.sleep(.3)
        self.assertGreaterEqual(count,30)
        self.assertFalse(moves.called)
        self.assertFalse(transitions)
        with patch('direct_app.os.startfile') as opener:
            folder.invoke()
            opener.assert_called_once_with(str(self.directory))
        other.update(status='失败，可重试',error='file/download HTTP 400，接口 code=23018')
        app.queue_tree.selection_set(other['id'])
        app.refresh_queue()
        self.assertTrue(app.context_buttons['重试所选'].winfo_manager())
        self.assertTrue(app.diagnostic_button.winfo_manager())
        self.assertFalse(app.diagnostic_label.winfo_manager())
        app.diagnostic_button.invoke()
        self.assertIn('23018',app.diagnostic_text.get())
        self.assertTrue(app.diagnostic_label.winfo_manager())
        self.assertIn('官方客户端',app.task_detail.get())
        other['status']='已完成'
        app.refresh_queue()
        self.assertFalse(app.context_buttons['重试所选'].winfo_manager())
    def test_expired_task_retries_from_checkpoint_without_starting_other_tasks(self):
        from direct_download import Downloader
        from test_direct_download import API,Network,DATA

        app=self.app
        app.remember.set(False)
        q=app.download_queue
        files=[dict(f,size=len(DATA)) for f in FILES[:3]]
        api=API()
        q.add(api,files,'https://pan.quark.cn/s/fixture','',self.directory)
        first,failed,pending=q.jobs
        failed.update(status='失败，可重试',error='普通错误')
        stop=threading.Event()
        def checkpoint(done,total,phase):
            if done>=262144:stop.set()
        q.run(stop,lambda _:api,
              lambda a,n:Downloader(a,stop,checkpoint,opener=Network(),chunk_size=262144,emit=lambda _:None),
              lambda *_:None,{first['id']})
        self.assertEqual(first['status'],'已暂停')
        q.live.clear()
        network=Network()
        class Share(API):
            def __init__(self,cookie,*_):super().__init__();self.cookie=cookie
            def scan(self,*_):
                if self.cookie=='expired':raise LoginExpired('登录已过期')
                return files
        def downloader(api,*args,**kwargs):
            return Downloader(api,*args,**kwargs,opener=network,chunk_size=262144)

        app.cookie.set('expired')
        with patch('direct_app.Probe',Share),patch('direct_app.Downloader',side_effect=downloader):
            app.start_queue({first['id']})
            self.pump_until(lambda:not app.busy)
            self.assertEqual(first['status'],'等待登录')
            self.assertEqual(app.queue_tree.set(first['id'],'status'),'等待登录')
            self.assertEqual(network.requests,[])
            self.assertEqual(first['downloaded'],262144)
            self.assertEqual(pending['status'],'等待下载')
            with patch('direct_app.capture_login',return_value='fresh'):
                app.queue_login.invoke()
                self.pump_until(lambda:not app.login_active)
            self.assertEqual(network.requests,[])
            app.queue_tree.selection_set(first['id'])
            app.task_info()
            app.retry_button.invoke()
            self.pump_until(lambda:not app.busy)
        self.assertEqual(first['status'],'已完成',first.get('error'))
        self.assertEqual(network.requests[0][0],262144)
        self.assertEqual(failed['status'],'失败，可重试')
        self.assertEqual(pending['status'],'等待下载')
