"""Windows UI for direct share downloads. Credentials stay in memory."""
import json
import gc
import os
import queue
import threading
import time
from collections import deque
from datetime import datetime,timedelta,timezone
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from tkinter import font as tkfont
from verify_direct import Probe,ProbeError,parse
from direct_download import Downloader,Paused,ChecksumMismatch,file_lock,export_unverified
from local_state import Store,StateError
from download_queue import DownloadQueue
from task_view import FILTERS,ATTENTION,share_input,size_text,category,visible,progress_text,next_step

DEFAULT_LINK='https://pan.quark.cn/s/example'

def eta_text(remaining,speed,elapsed,phase,now=None):
    if '检查本地' in phase:return '正在查重，不消耗下载流量；完成后自动跳过或续传。'
    if '已存在' in phase:return '已使用本地文件，没有重复下载。'
    if '校验' in phase:return '文件已接收，正在校验完整性…'
    if '完成' in phase:return '下载完成'
    if elapsed<3 or speed<=0:return '预计完成：正在估算…'
    seconds=max(0,int(remaining/speed));hours,rest=divmod(seconds,3600);minutes,secs=divmod(rest,60)
    duration=(f'{hours} 小时 ' if hours else '')+(f'{minutes} 分 ' if minutes else '')+f'{secs} 秒'
    current=now or datetime.now(timezone(timedelta(hours=8)))
    finish=current+timedelta(seconds=seconds)
    return f'当前文件预计剩余 {duration} · 预计完成 {finish:%m-%d %H:%M:%S}（北京时间，随速度更新）'

class App:
    def __init__(self,root,store=None):
        gc.collect()  # Reclaim retired Tk windows on their owner thread.
        self.root=root;root.title('夸克下载 · QuarkBridge 0.11.0');root.geometry('1080x780');root.minsize(940,680)
        self.store=store or Store();self.rows=[];self.statuses={};self.queue_ids=[];self.auth_enabled=True
        self.q=queue.Queue();self.stop=threading.Event();self.busy=False;self.api=None;self.snapshot=None
        self.download_queue=None;self.queue_running=False;self.queue_autorun=False;self.queue_tree=None;self.queue_cookie=''
        self.closing=False;self.closed=False;self.dialogs=set();self.dialog_threads=[]
        self.queue_summary=tk.StringVar(value='还没有下载任务')
        self.error_detail=tk.StringVar();self.task_detail=tk.StringVar(value='点击“添加文件”开始。下载中也能随时添加下一份文件。')
        self.filter=tk.StringVar(value='全部');self.search=tk.StringVar();self.empty_hint=tk.StringVar()
        self.last_path=None;self.latest_progress=None;self.last_update=0;self.last_bytes=0;self.last_clock=time.monotonic()
        self.speed_samples=deque();self.speed_reset=True;self.previous_phase='';self.connections=tk.StringVar(value='32')
        self.remember=tk.BooleanVar(value=True);self.eta=tk.StringVar(value='预计完成：开始下载后显示');self.login_status=tk.StringVar(value='先到“设置”填写登录信息，仅在本机保存')
        self.link=tk.StringVar(value=DEFAULT_LINK);self.password=tk.StringVar();self.cookie=tk.StringVar()
        self.destination=tk.StringVar(value=str(Path.home()/'Downloads'/'QuarkDownloads'));self.status=tk.StringVar(value='添加文件 → 自动排队 → 下载到本地')
        tkfont.nametofont('TkDefaultFont').configure(family='Microsoft YaHei UI',size=10)
        tkfont.nametofont('TkTextFont').configure(family='Microsoft YaHei UI',size=10)
        style=ttk.Style(root);style.configure('Treeview',rowheight=32,font=('Microsoft YaHei UI',10));style.configure('Treeview.Heading',font=('Microsoft YaHei UI',10,'bold'))
        outer=ttk.Frame(root,padding=14);outer.pack(fill='both',expand=True)
        outer.columnconfigure(0,weight=1);outer.rowconfigure(1,weight=1)
        heading=ttk.Frame(outer);heading.grid(row=0,column=0,sticky='ew')
        ttk.Label(heading,text='夸克下载',font=('Microsoft YaHei UI',19,'bold')).pack(side='left')
        ttk.Label(heading,text='随时添加 · 自动查重 · 断点续传').pack(side='left',padx=18)
        ttk.Label(heading,text='0.11').pack(side='right')
        self.notebook=ttk.Notebook(outer);self.notebook.grid(row=1,column=0,sticky='nsew',pady=(12,8))
        self.task_page=ttk.Frame(self.notebook,padding=10);self.settings_panel=ttk.Frame(self.notebook,padding=16)
        self.task_page.columnconfigure(0,weight=1);self.task_page.rowconfigure(3,weight=1)
        self.notebook.add(self.task_page,text='  下载任务  ');self.notebook.add(self.settings_panel,text='  设置  ')
        bar=ttk.Frame(self.task_page);bar.grid(row=0,column=0,sticky='ew',pady=(0,10));self.main_bar=bar
        self.add_share_button=ttk.Button(bar,text='＋ 添加文件',command=self.add_share_dialog);self.add_share_button.pack(side='left')
        self.resume_button=ttk.Button(bar,text='开始 / 继续',command=self.resume);self.resume_button.pack(side='left',padx=6)
        self.pause_button=ttk.Button(bar,text='暂停队列',command=self.pause,state='disabled');self.pause_button.pack(side='left')
        self.retry_button=ttk.Button(bar,text='重试失败任务',command=self.retry_failed);self.retry_button.pack(side='left',padx=6)
        self.folder_button=ttk.Button(bar,text='打开下载目录',command=self.open_folder);self.folder_button.pack(side='right')
        filters=ttk.Frame(self.task_page);filters.grid(row=1,column=0,sticky='ew',pady=(0,8))
        for name in FILTERS:ttk.Radiobutton(filters,text=name,value=name,variable=self.filter,command=self.refresh_queue).pack(side='left',padx=(0,8))
        ttk.Entry(filters,textvariable=self.search,width=22).pack(side='right');ttk.Label(filters,text='搜索文件  ').pack(side='right')
        ttk.Label(self.task_page,textvariable=self.queue_summary).grid(row=2,column=0,sticky='w',pady=(0,6))
        frame=ttk.Frame(self.task_page);frame.grid(row=3,column=0,sticky='nsew')
        self.queue_tree=ttk.Treeview(frame,columns=('order','name','size','progress','status'),show='headings',selectmode='extended',height=4)
        for key,title,width in [('order','顺序',45),('name','文件',420),('size','大小',90),('progress','进度 / 已接收',160),('status','状态',165)]:
            self.queue_tree.heading(key,text=title);self.queue_tree.column(key,width=width,minwidth=40,stretch=key=='name')
        self.queue_tree.tag_configure('attention',foreground='#a13e2b');self.queue_tree.tag_configure('active',foreground='#1267b1')
        self.queue_tree.tag_configure('done',foreground='#377344')
        self.queue_tree.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(frame,command=self.queue_tree.yview);scroll.pack(side='right',fill='y');self.queue_tree.configure(yscrollcommand=scroll.set)
        ttk.Label(self.task_page,textvariable=self.empty_hint).grid(row=4,column=0,sticky='w',pady=4)
        self.queue_tree.bind('<<TreeviewSelect>>',self.task_info)
        self.queue_tree.bind('<Double-1>',lambda e:self.open_task_folder())
        detail=ttk.LabelFrame(self.task_page,text='选中任务',padding=8);detail.grid(row=5,column=0,sticky='ew',pady=(2,8))
        ttk.Label(detail,textvariable=self.task_detail,wraplength=860).pack(anchor='w')
        self.task_actions=ttk.Frame(detail);self.task_actions.pack(fill='x',pady=(8,0));self.context_buttons={}
        for label,command in [('重试所选',lambda:self.queue_edit('retry')),('打开所在文件夹',self.open_task_folder),('上移',lambda:self.queue_edit('up')),('下移',lambda:self.queue_edit('down')),('移除任务',lambda:self.queue_edit('remove')),
                              ('保存现有文件',self.queue_export),('使用已有文件',lambda:self.resolve_duplicates(True)),('仍然下载',lambda:self.resolve_duplicates(False))]:
            self.context_buttons[label]=ttk.Button(self.task_actions,text=label,command=command)
        self.build_settings()
        # Compatibility controls for old saved task histories; not part of the daily UI.
        legacy=ttk.Frame(outer)
        self.tree=ttk.Treeview(legacy,columns=('name','size','state','reason'),show='headings')
        self.scan_button=ttk.Button(legacy,command=self.scan)
        self.download_button=ttk.Button(legacy,command=self.download)
        self.verify_button=ttk.Button(legacy,command=self.verify)
        self.enqueue_button=ttk.Button(legacy,command=self.enqueue_selected)
        self.queue_button=ttk.Button(legacy,command=self.show_queue)
        self.repair_button=ttk.Button(legacy,command=lambda:self.resume(repair=True))
        self.export_button=ttk.Button(legacy,command=self.export_received)
        self.legacy_export=ttk.Button(self.task_page,text='保存旧版已收齐文件',command=self.export_received)
        footer=ttk.Frame(outer);footer.grid(row=2,column=0,sticky='ew')
        ttk.Label(footer,textvariable=self.error_detail,wraplength=880,foreground='#a13e2b').pack(anchor='w',pady=(0,6))
        self.progress=ttk.Progressbar(footer,maximum=100);self.progress.pack(fill='x')
        ttk.Label(footer,textvariable=self.status,wraplength=880).pack(anchor='w',pady=5)
        self.eta_label=ttk.Label(footer,textvariable=self.eta,wraplength=880);self.eta_label.pack(anchor='w',pady=(0,6))
        self.log_button=ttk.Button(footer,text='运行日志',command=self.toggle_log);self.log_button.pack(anchor='w')
        self.log=tk.Text(footer,height=4,wrap='word',state='disabled')
        self.restore();self.queue_cookie=self.cookie.get();self.session_password=self.password.get()
        try:self.download_queue=DownloadQueue(self.store,lambda:self.q.put(('queue_changed',None)))
        except StateError as e:self.error_detail.set(str(e));self.log_message(str(e))
        if self.download_queue and self.download_queue.snapshot():self.status.set('已恢复下载任务；点击“开始 / 继续”接续。')
        self.search.trace_add('write',lambda *_:self.refresh_queue())
        self.refresh_queue();self.set_busy(False);root.protocol('WM_DELETE_WINDOW',self.close);self.timer=root.after(100,self.pump)
        root.bind('<Control-n>',lambda e:self.add_share_dialog())

    def build_settings(self):
        panel=self.settings_panel;panel.columnconfigure(1,weight=1);self.entries=[]
        ttk.Label(panel,text='登录和保存位置',font=('Microsoft YaHei UI',13,'bold')).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,16))
        for row,(name,var,hidden) in enumerate([('Cookie（本机加密）',self.cookie,True),('默认下载目录',self.destination,False)],1):
            ttk.Label(panel,text=name).grid(row=row,column=0,sticky='w',padx=(0,14),pady=8)
            entry=ttk.Entry(panel,textvariable=var,show='*' if hidden else '');entry.grid(row=row,column=1,sticky='ew',pady=8);self.entries.append(entry)
        self.browse=ttk.Button(panel,text='选择目录',command=self.choose);self.browse.grid(row=2,column=2,padx=6)
        self.remember_box=ttk.Checkbutton(panel,text='记住登录（Windows 加密；服务端续期时自动保存）',variable=self.remember,command=self.remember_changed)
        self.remember_box.grid(row=3,column=1,sticky='w',pady=6)
        self.forget_button=ttk.Button(panel,text='忘记登录',command=self.forget);self.forget_button.grid(row=3,column=2,padx=6)
        ttk.Label(panel,textvariable=self.login_status,wraplength=620).grid(row=4,column=0,columnspan=2,sticky='w',pady=8)
        ttk.Button(panel,text='如何获取 Cookie',command=lambda:messagebox.showinfo('本机登录信息',
            '在浏览器登录 pan.quark.cn，按 F12 打开开发者工具，选择“网络 / Network”后刷新网页。\n\n选择网盘的 list 或 sort 请求，在请求标头中复制完整 Cookie，粘贴到这里。\n\n只保存在本机；不要发送到聊天或其他网站。登录失效时需手动更新。',parent=self.root)).grid(row=4,column=2,padx=6)
        ttk.Separator(panel).grid(row=5,column=0,columnspan=3,sticky='ew',pady=12)
        ttk.Label(panel,text='每个文件的连接数').grid(row=6,column=0,sticky='w')
        self.connection_box=ttk.Combobox(panel,textvariable=self.connections,values=('1','2','4','8','16','32'),width=6,state='readonly');self.connection_box.grid(row=6,column=1,sticky='w')
        ttk.Label(panel,text='已按本机测速采用 32 连接、2 MiB 分段和连接复用。速度有波动；遇到限流可调低连接数。',wraplength=760).grid(row=7,column=0,columnspan=3,sticky='w',pady=10)
        ttk.Label(panel,text='变更连接数后，下一次开始 / 重试会应用到等待任务。默认目录仅用于新任务；旧任务保持原位置续传。',wraplength=760).grid(row=8,column=0,columnspan=3,sticky='w',pady=6)
        self.save_settings_button=ttk.Button(panel,text='保存设置，返回下载',command=self.save_settings);self.save_settings_button.grid(row=9,column=0,columnspan=3,sticky='w',pady=12)

    def save_settings(self):
        if self.busy:return
        if not self.destination.get().strip():self.login_status.set('请先填写默认下载目录。');return
        self.auth_values();self.save_history();self.show_queue()
    def toggle_settings(self):self.notebook.select(self.settings_panel)
    def toggle_log(self):
        if self.log.winfo_manager():self.log.pack_forget()
        else:self.log.pack(fill='x')
    def retry_failed(self):
        if not self.download_queue:return
        ids=[j['id'] for j in self.download_queue.snapshot() if j['status']=='失败，可重试']
        if not ids:self.error_detail.set('没有需要重新下载的失败任务。数据已收齐但校验待确认的文件，可保存现有文件使用。');return
        self.download_queue.edit(ids,'retry');self.error_detail.set('已重新排队；继续断点，不会从头重复下载。');self.start_queue()

    def restore(self):
        try:
            session=self.store.load_session()
            self.cookie.set(session.get('cookie',''));self.password.set(session.get('password',''))
            if session.get('cookie'):self.login_status.set('已恢复加密保存的登录信息；接口返回新 Cookie 时自动更新')
            saved=self.store.load_tasks()
            if not saved:return
            self.link.set(saved.get('link',DEFAULT_LINK));self.destination.set(saved.get('destination',str(Path.home()/'Downloads'/'QuarkDownloads')))
            self.connections.set(str(saved.get('connections',32)) if saved.get('speed_profile_version')==1 else '32')
            self.last_path=saved.get('last_path')
            self.rows=saved.get('files',[]);self.queue_ids=saved.get('queue_ids',[]);self.statuses=saved.get('statuses',{})
            for key,value in list(self.statuses.items()):
                if value=='下载中':self.statuses[key]='未完成，可恢复'
            self.populate(self.rows)
            self.status.set('已恢复上次任务。点击“开始 / 继续”刷新下载地址并断点续传。')
        except StateError as e:self.log_message(str(e))
    def populate(self,rows):
        self.rows=rows;self.tree.delete(*self.tree.get_children())
        for i,f in enumerate(rows):self.tree.insert('','end',iid=str(i),values=(f['display'],f'{int(f["size"])/1024**3:.3f} GiB',self.statuses.get(str(f['fid']),'待下载')))
        selected=[str(i) for i,f in enumerate(rows) if str(f['fid']) in self.queue_ids and not self.statuses.get(str(f['fid']),'').startswith(('已完成','已保存'))]
        if selected:self.tree.selection_set(selected)
        elif rows:self.tree.selection_set('0')
    def save_history(self):
        try:
            u=urlsplit(self.link.get());query=urlencode([(k,v) for k,v in parse_qsl(u.query) if k!='pwd'])
            link=urlunsplit((u.scheme,u.netloc,u.path,query,u.fragment))
            allowed=('fid','size','display','path_parts','parent_fid','file_name')
            self.store.save_tasks({'version':1,'link':link,'destination':self.destination.get(),
                'connections':int(self.connections.get()),'speed_profile_version':1,'files':[{k:v for k,v in f.items() if k in allowed} for f in self.rows],
                'statuses':self.statuses,'queue_ids':self.queue_ids,'last_path':self.last_path})
        except (StateError,OSError,ValueError):self.log_message('任务列表保存失败；已下载文件旁的断点记录仍可使用。')
    def remember_changed(self):
        self.auth_enabled=self.remember.get()
        if not self.auth_enabled:
            try:self.store.forget();self.login_status.set('仅本次使用 Cookie，不保存登录')
            except OSError:self.login_status.set('删除保存的登录失败，请检查目录权限')
    def forget(self):
        if self.busy:return
        self.remember.set(False);self.remember_changed();self.cookie.set('');self.password.set('');self.api=None;self.snapshot=None;self.set_busy(False)
        self.queue_cookie=''
        if self.download_queue:
            with self.download_queue.lock:self.download_queue.live.clear();self.download_queue.passwords.clear()
    def auth_values(self):
        values=self.values();self.auth_enabled=self.remember.get()
        password=values[1] or dict(parse_qsl(urlsplit(values[0]).query)).get('pwd','')
        self.password.set(password);values=(values[0],password,values[2])
        self.session_password=password;self.queue_cookie=values[2]
        if self.auth_enabled:
            try:self.store.save_session(values[2],password);self.login_status.set('登录信息已加密保存；服务端续期时自动更新')
            except (StateError,OSError) as e:
                self.auth_enabled=False;self.login_status.set('加密保存失败，本次仅在内存使用 Cookie')
        return values
    def cookie_callback(self,password):
        def updated(cookie):
            self.queue_cookie=cookie
            if self.auth_enabled:
                try:self.store.save_session(cookie,self.session_password)
                except (StateError,OSError):self.q.put(('log','续期 Cookie 在内存可用，但加密保存失败。'))
            self.q.put(('cookie',cookie))
        return updated

    def choose(self):
        path=filedialog.askdirectory(initialdir=self.destination.get() if Path(self.destination.get()).exists() else None)
        if path:self.destination.set(path)
    def values(self):return (self.link.get().strip(),self.password.get(),self.cookie.get().strip())
    def set_busy(self,value):
        self.busy=value
        for widget in self.entries+[self.scan_button,self.browse]:widget.configure(state='disabled' if value else 'normal')
        self.download_button.configure(state='normal' if self.api and (not value or self.queue_running) else 'disabled')
        self.verify_button.configure(state='normal' if self.api and not value else 'disabled')
        self.enqueue_button.configure(state='normal' if self.api and self.download_queue else 'disabled')
        self.pause_button.configure(state='normal' if value else 'disabled')
        self.connection_box.configure(state='disabled' if value else 'readonly')
        for widget in [self.remember_box,self.forget_button]:widget.configure(state='disabled' if value else 'normal')
        self.save_settings_button.configure(state='disabled' if value else 'normal')
        pending=self.download_queue.has_pending() if self.download_queue else False
        legacy=bool(self.rows) and not (self.download_queue and self.download_queue.snapshot())
        self.resume_button.configure(state='normal' if (pending or legacy) and not value else 'disabled')
        for widget in [self.queue_button,self.add_share_button]:widget.configure(state='normal' if self.download_queue else 'disabled')
        self.repair_button.configure(state='normal' if self.rows and not value else 'disabled')
        self.export_button.configure(state='normal' if self.rows and not value else 'disabled')
        self.task_info()
    def log_message(self,text):
        self.log.configure(state='normal');self.log.insert('end',text+'\n');self.log.see('end');self.log.configure(state='disabled')
    def start(self,action):
        if self.busy or self.closing:return
        gc.collect()
        self.stop.clear();self.set_busy(True)
        def worker():
            try:action()
            except Paused as e:self.q.put(('error',str(e)))
            except ProbeError as e:self.q.put(('error',str(e)))
            except OSError as e:self.q.put(('error',f'本地文件操作失败（{type(e).__name__}），检查目录权限和磁盘空间。断点保留。'))
            except Exception as e:self.q.put(('error',f'操作未完成（{type(e).__name__}），断点保留。请重新解析后续传。'))
            finally:self.q.put(('idle',None))
        self.worker_thread=threading.Thread(target=worker,daemon=True);self.worker_thread.start()
    def scan(self):
        if self.busy:return
        values=self.auth_values()
        self.api=None;self.snapshot=None;self.rows=[];self.statuses={};self.queue_ids=[];self.tree.delete(*self.tree.get_children());self.status.set('正在解析分享目录…')
        def action():
            api=Probe(values[2],lambda s:self.q.put(('log',s)),self.cookie_callback(values[1]));rows=api.scan(values[0],values[1])
            self.q.put(('scanned',(api,values,rows)))
        self.start(action)
    def selected(self):
        if not self.api:return []
        if self.values()!=self.snapshot:
            messagebox.showinfo('请重新解析','链接、提取码或 Cookie 已改变，请先重新解析目录。');return []
        selected=self.tree.selection()
        if not selected:messagebox.showinfo('选择文件','请先在文件列表中选中文件。');return []
        return [(i,self.api.files[int(i)]) for i in selected]
    def on_progress(self,done,total,phase):
        self.latest_progress=(done,total,phase)
        if self.queue_running and self.download_queue:
            try:self.download_queue.progress(done,total,phase)
            except (OSError,StateError):self.q.put(('log','任务进度显示暂未保存；文件旁的下载断点继续独立保存。'))
    def download(self):
        selected=self.selected()
        if not selected:return
        dest=self.destination.get().strip()
        if not dest:messagebox.showerror('下载目录','请选择本地下载目录。');return
        self.queue_ids=[str(f['fid']) for _,f in selected];self.rows=self.api.files;self.save_history()
        if self.add_to_queue(self.api,[f for _,f in selected],self.snapshot[0],self.snapshot[1],dest,int(self.connections.get())):self.start_queue()
    def add_to_queue(self,api,files,link,password,dest,workers):
        if not self.download_queue:raise StateError('队列无法读取，请先检查本地队列文件。')
        password=password or dict(parse_qsl(urlsplit(link).query)).get('pwd','')
        try:
            base=Path(self.destination.get()).resolve()
            if not Path(dest).resolve().is_relative_to(base):base=Path(dest).resolve()
            n=self.download_queue.add(api,files,link,password,dest,workers,self.remember.get(),library_root=base)
        except (StateError,OSError) as e:
            self.error_detail.set('队列保存失败，请检查工具目录的写入权限和磁盘空间，再重新加入。');self.log_message(self.error_detail.get());return False
        self.refresh_queue();self.log_message(f'已加入 {n} 个文件到队列；重复文件不会重复添加。')
        self.status.set(f'已加入 {n} 项；已有相同任务会保留进度，不重复建立。');self.show_queue();return True
    def enqueue_selected(self):
        selected=self.selected()
        if selected:self.add_to_queue(self.api,[f for _,f in selected],self.snapshot[0],self.snapshot[1],self.destination.get(),int(self.connections.get()))
    def start_queue(self):
        if not self.download_queue or self.closing:return
        self.queue_autorun=True
        if self.busy:return
        if not self.download_queue.has_pending():self.status.set('队列没有等待任务；失败任务可在队列中选择重试。');return
        with self.download_queue.lock:
            for job in self.download_queue.jobs:
                if job['status'] in ('等待下载','已暂停'):job['workers']=int(self.connections.get())
        self.queue_cookie=self.cookie.get();self.auth_values();self.queue_running=True
        def action():
            def factory(password):
                return Probe(self.queue_cookie,lambda s:self.q.put(('log',s)),self.cookie_callback(password))
            def downloader(api,workers):
                if hasattr(api,'cookie'):api.cookie=self.queue_cookie
                return Downloader(api,self.stop,self.on_progress,lambda s:self.q.put(('log',s)),workers=workers)
            def event(typ,job):
                if typ=='active':self.latest_progress=None;self.speed_reset=True;self.q.put(('log',f'队列开始：{job["file"]["display"]}'))
                if typ=='failed':self.q.put(('log',f'任务保留，继续后续文件：{job["file"]["display"]}；{job["error"]}'))
                self.q.put(('queue_job',job))
            try:
                self.download_queue.run(self.stop,factory,downloader,event)
                jobs=self.download_queue.snapshot();failures=sum(j['status'] in ('失败，可重试','校验待确认','疑似重复，待选择') for j in jobs)
                self.q.put(('success','队列已暂停，等待任务和断点已保存。' if self.stop.is_set() else
                            (f'队列本轮处理结束，{failures} 个任务需要处理；文件和断点已保留。' if failures else '队列文件全部下载并校验完成。')))
            finally:self.queue_running=False
        self.start(action)
    def refresh_queue(self):
        if not self.download_queue:return
        jobs=self.download_queue.snapshot()
        failures=[j for j in jobs if j['status'] in ATTENTION]
        if failures:
            failed=failures[-1];self.error_detail.set(f'{len(failures)} 项需要处理 · {failed["file"]["display"][:45]}：{failed["error"][:180]}')
            for i,f in enumerate(self.rows):
                if str(f['fid'])==str(failed['file']['fid']) and self.tree.exists(str(i)):self.tree.set(str(i),'reason',failed['error'])
        else:self.error_detail.set('')
        pending=sum(category(j)=='等待 / 暂停' for j in jobs)
        done=sum(category(j)=='已完成' for j in jobs)
        self.queue_summary.set(f'共 {len(jobs)} 项  ·  下载中 {sum(j["status"]=="下载中" for j in jobs)}  ·  等待 / 暂停 {pending}  ·  需要处理 {len(failures)}  ·  已完成 {done}')
        shown=[(i,j) for i,j in enumerate(jobs,1) if visible(j,self.filter.get(),self.search.get())]
        wanted={j['id'] for _,j in shown}
        for key in self.queue_tree.get_children():
            if key not in wanted:self.queue_tree.delete(key)
        for position,(i,j) in enumerate(shown):
            values=(i,j['file']['display'],size_text(j['file']['size']),progress_text(j),j['status'])
            tag='attention' if j['status'] in ATTENTION else ('active' if j['status']=='下载中' else ('done' if category(j)=='已完成' else ''))
            if self.queue_tree.exists(j['id']):self.queue_tree.item(j['id'],values=values,tags=(tag,));self.queue_tree.move(j['id'],'',position)
            else:self.queue_tree.insert('','end',iid=j['id'],values=values,tags=(tag,))
        self.empty_hint.set('' if shown else ('没有符合筛选条件的任务。' if jobs else '还没有任务。点击“＋ 添加文件”，也可以按 Ctrl+N。'))
        self.task_info()
        self.retry_button.configure(state='normal' if any(j['status']=='失败，可重试' for j in jobs) else 'disabled')
        if not self.busy:self.resume_button.configure(state='normal' if pending or (self.rows and not jobs) else 'disabled')
        legacy_pending=not jobs and any('校验' in value for value in self.statuses.values())
        if legacy_pending:self.legacy_export.grid(row=6,column=0,sticky='w',pady=4)
        else:self.legacy_export.grid_remove()

    def task_info(self,*_):
        chosen=set(self.queue_tree.selection())
        jobs=[j for j in self.download_queue.snapshot() if j['id'] in chosen] if self.download_queue else []
        for button in self.context_buttons.values():button.pack_forget()
        if not jobs:
            self.task_detail.set('选中任务查看原因、处理建议和保存位置；双击可打开所在文件夹。');return
        job=jobs[0]
        path=job.get('candidate_path') if job['status']=='疑似重复，待选择' else (job.get('path') or job.get('local_target') or job['destination'])
        reason=(job.get('error','')[:260]+'\n') if job.get('error') else ''
        self.task_detail.set((f'已选 {len(jobs)} 项 · ' if len(jobs)>1 else '')+job['file']['display']+'\n'+reason+next_step(job)+'\n位置：'+str(path))
        available=['打开所在文件夹']
        if any(j['status']=='失败，可重试' for j in jobs):available.insert(0,'重试所选')
        if any(j['status']!='下载中' for j in jobs):available+=['上移','下移','移除任务']
        if any(j['status']=='校验待确认' for j in jobs):available+=['保存现有文件']
        if any(j['status']=='疑似重复，待选择' for j in jobs):available+=['使用已有文件','仍然下载']
        for name in available:self.context_buttons[name].pack(side='left',padx=(0,6))
        self.context_buttons['保存现有文件'].configure(state='disabled' if self.busy else 'normal')

    def show_queue(self):self.notebook.select(self.task_page)
    def open_task_folder(self):
        chosen=set(self.queue_tree.selection())
        job=next((j for j in self.download_queue.snapshot() if j['id'] in chosen),None)
        if not job:return
        raw=job.get('candidate_path') if job['status']=='疑似重复，待选择' else (job.get('path') or job.get('local_target'))
        path=Path(raw).parent if raw else Path(job['destination'])
        if path.is_dir():os.startfile(str(path))
        else:messagebox.showinfo('文件位置','此任务目录尚未创建或已被移动。',parent=self.root)
    def queue_edit(self,action):
        if self.queue_tree:
            ids=list(self.queue_tree.selection())
            if action=='retry':
                ids=[j['id'] for j in self.download_queue.snapshot() if j['id'] in ids and j['status']=='失败，可重试']
                if not ids:messagebox.showinfo('无需重新下载','只对下载失败的任务重试。校验待确认的文件已接收完整，可保存现有文件；疑似重复的文件请先选择是否使用已有文件。');return
            self.download_queue.edit(ids,action);self.refresh_queue()
            if action=='retry':self.start_queue()
    def resolve_duplicates(self,use_existing):
        chosen=set(self.queue_tree.selection())
        with self.download_queue.lock:
            for job in self.download_queue.jobs:
                if job['id'] not in chosen or job['status']!='疑似重复，待选择':continue
                path=Path(job.get('candidate_path',''))
                if use_existing:
                    if not path.is_file():self.error_detail.set('已有文件已移动，请选择仍然下载。');continue
                    job.update(status='已跳过（使用已有文件）',path=str(path),error='用户选择使用已有文件，源内容未确认。')
                else:job.update(status='等待下载',allow_suspect=True,error='')
            self.download_queue.save()
        self.refresh_queue()
        if not use_existing:self.start_queue()
    def queue_export(self):
        if self.busy:messagebox.showinfo('请先暂停','请先暂停当前下载，再保存待确认文件。');return
        chosen=set(self.queue_tree.selection());jobs=[j for j in self.download_queue.snapshot() if j['id'] in chosen and j['status']=='校验待确认']
        if not jobs:messagebox.showinfo('选择任务','请在队列中选中要保存的任务。');return
        def action():
            for job in jobs:
                path=export_unverified(job['file'],job['destination'],job['sid'],job.get('local_target'),job.get('library_root'))
                with self.download_queue.lock:job_live=next(j for j in self.download_queue.jobs if j['id']==job['id']);job_live.update(status='已保存（校验待确认）',path=str(path));self.download_queue.save()
                self.q.put(('path',str(path)));self.q.put(('queue_job',dict(job_live)))
            self.q.put(('success','所选现有文件已保存，校验待确认；没有重新下载。'))
        self.start(action)
    def add_share_dialog(self):
        if self.closing:return
        if not self.download_queue:messagebox.showerror('队列不可用','队列文件无法读取，请检查本地数据目录。');return
        window=tk.Toplevel(self.root);window.title('添加文件 · 当前下载继续运行');window.geometry('820x620');window.minsize(740,540)
        self.dialogs.add(window)
        def dismiss():
            self.dialogs.discard(window);window.destroy()
        window.protocol('WM_DELETE_WINDOW',dismiss)
        outer=ttk.Frame(window,padding=12);outer.pack(fill='both',expand=True);outer.columnconfigure(1,weight=1)
        link=tk.StringVar();password=tk.StringVar();dest=tk.StringVar(value=self.destination.get())
        auto=tk.BooleanVar(value=True);hint=tk.StringVar(value='解析新分享不会中断当前下载。解析后可多选文件加入队列。')
        controls=[]
        for i,(label,var) in enumerate([('链接 / 分享文案',link),('提取码（自动识别）',password),('下载目录',dest)]):
            ttk.Label(outer,text=label).grid(row=i,column=0,sticky='w',pady=5)
            entry=ttk.Entry(outer,textvariable=var);entry.grid(row=i,column=1,sticky='ew',pady=5);controls.append(entry)
        tree=ttk.Treeview(outer,columns=('name','size'),show='headings',selectmode='extended',height=14)
        tree.heading('name',text='文件 / 原目录');tree.column('name',width=570);tree.heading('size',text='大小');tree.column('size',width=110)
        tree.grid(row=5,column=0,columnspan=3,sticky='nsew',pady=8);outer.rowconfigure(5,weight=1)
        scroll=ttk.Scrollbar(outer,command=tree.yview);scroll.grid(row=5,column=3,sticky='ns',pady=8);tree.configure(yscrollcommand=scroll.set)
        ttk.Label(outer,textvariable=hint,wraplength=700).grid(row=6,column=0,columnspan=4,sticky='w')
        ttk.Checkbutton(outer,text='加入后自动下载（当前文件结束后接着下载）',variable=auto).grid(row=7,column=0,columnspan=4,sticky='w',pady=6)
        state={};bar=ttk.Frame(outer);bar.grid(row=4,column=0,columnspan=4,sticky='w',pady=8)
        def paste():
            try:
                url,pwd=share_input(window.clipboard_get());link.set(url);password.set(pwd);hint.set('已识别分享链接和提取码，请点“解析分享”。')
            except tk.TclError:hint.set('剪贴板中没有文本。')
            except ValueError as e:hint.set(str(e))
        paste_button=ttk.Button(outer,text='粘贴',command=paste);paste_button.grid(row=0,column=2,padx=(6,0))
        def choose_dest():
            value=filedialog.askdirectory(parent=window,initialdir=dest.get() if Path(dest.get()).is_dir() else None)
            if value:dest.set(value)
        pick=ttk.Button(outer,text='选择目录',command=choose_dest);pick.grid(row=2,column=2,padx=(6,0));controls.extend([paste_button,pick])
        def finish(api,rows,values,error=None):
            if not window.winfo_exists():return
            for widget in controls+[scan]:widget.configure(state='normal')
            if error:hint.set(error);return
            state.update(api=api,rows=rows,values=values);tree.delete(*tree.get_children())
            for i,f in enumerate(rows):tree.insert('','end',iid=str(i),values=(f['display'],f'{int(f["size"])/1024**3:.3f} GiB'))
            tree.selection_set(tree.get_children());hint.set(f'已解析 {len(rows)} 个文件，默认全选；按 Ctrl / Shift 调整选择。');add.configure(state='normal')
        def scan_share():
            try:url,pwd=share_input(link.get(),password.get())
            except (ValueError,ProbeError) as e:hint.set(str(e));return
            link.set(url);password.set(pwd);values=(url,pwd,dest.get().strip())
            if not values[0] or not values[2]:hint.set('请填写分享链接和下载目录。');return
            pwd=values[1] or dict(parse_qsl(urlsplit(values[0]).query)).get('pwd','');password.set(pwd);values=(values[0],pwd,values[2])
            cookie=self.queue_cookie if self.busy else self.cookie.get();state.clear();add.configure(state='disabled')
            gc.collect()
            for widget in controls+[scan]:widget.configure(state='disabled')
            hint.set('正在解析；当前下载继续运行…')
            def worker():
                try:
                    api=Probe(cookie,lambda s:None,self.cookie_callback(pwd));rows=api.scan(values[0],pwd)
                    self.q.put(('incoming',(finish,(api,rows,values))))
                except Exception as e:
                    error=str(e) if isinstance(e,ProbeError) else f'解析未完成（{type(e).__name__}），请重试。'
                    self.q.put(('incoming',(finish,(None,None,values,error))))
            thread=threading.Thread(target=worker,daemon=True);self.dialog_threads.append(thread);thread.start()
        def add_files():
            if (link.get().strip(),password.get(),dest.get().strip())!=state.get('values'):
                hint.set('链接、提取码或目录已改变，请重新解析。');return
            selected=[state['rows'][int(i)] for i in tree.selection()]
            if not selected:hint.set('请至少选择一个文件。');return
            values=state['values']
            if not self.add_to_queue(state['api'],selected,values[0],values[1],values[2],int(self.connections.get())):
                hint.set('队列保存失败，尚未加入。请检查工具目录权限和磁盘空间，然后重试。');return
            if auto.get():self.start_queue()
            dismiss()
        scan=ttk.Button(bar,text='解析分享',command=scan_share);scan.pack(side='left')
        add=ttk.Button(bar,text='所选文件加入队列',command=add_files,state='disabled');add.pack(side='left',padx=8)
        ttk.Button(bar,text='全选',command=lambda:tree.selection_set(tree.get_children())).pack(side='left')
        controls[0].focus_set()
        return window
    def resume(self,repair=False):
        if self.busy:return
        if not repair and self.download_queue and self.download_queue.snapshot():self.start_queue();return
        if not self.rows:return
        if repair and not messagebox.askyesno('会重新读取整份文件',
            '这会再次从夸克传输约一份文件的数据，耗时可能与重下相近。\n\n如果只想使用已下载的视频，点击“保存现有视频（校验待确认）”，不消耗下载流量。\n\n仍要执行全文件网络核对吗？'):return
        chosen=self.tree.selection()
        wanted={str(self.rows[int(i)]['fid']) for i in chosen if not self.statuses.get(str(self.rows[int(i)]['fid']),'').startswith(('已完成','已保存'))}
        if not wanted:wanted={fid for fid in self.queue_ids if not self.statuses.get(fid,'').startswith(('已完成','已保存'))}
        if not wanted:messagebox.showinfo('任务状态','已保存的任务都已完成。');return
        values=self.auth_values();dest=self.destination.get();workers=int(self.connections.get())
        self.queue_ids=list(wanted);self.save_history();self.status.set('正在恢复登录并刷新分享文件信息…')
        def action():
            api=Probe(values[2],lambda s:self.q.put(('log',s)),self.cookie_callback(values[1]));rows=api.scan(values[0],values[1])
            self.q.put(('scanned',(api,values,rows)))
            matched=[(str(i),f) for i,f in enumerate(rows) if str(f['fid']) in wanted]
            if len(matched)!=len(wanted):raise ProbeError('部分待恢复文件已不在分享目录中，请重新确认文件列表。')
            dl=Downloader(api,self.stop,self.on_progress,lambda s:self.q.put(('log',s)),workers=workers)
            for iid,f in matched:
                dl.check();self.speed_reset=True;self.q.put(('file',(iid,'下载中')))
                try:path=dl.repair(f,dest) if repair else dl.download(f,dest)
                except Exception as e:
                    self.q.put(('file',(iid,'校验未通过，数据保留' if isinstance(e,ChecksumMismatch) else ('已暂停' if self.stop.is_set() else '未完成，可续传'))));raise
                self.q.put(('file',(iid,'已完成（源数据核对）' if repair else '已完成')));self.q.put(('path',str(path)))
            self.q.put(('success','全文件源数据核对完成，成品已保存；接口摘要差异见日志及文件旁的校验记录。' if repair else '上次未完成任务已全部续传并校验完成。'))
        self.start(action)
    def export_received(self):
        if self.busy:return
        chosen=self.tree.selection()
        if not chosen:messagebox.showinfo('选择文件','请先选中要保存的文件。');return
        files=[(iid,self.rows[int(iid)]) for iid in chosen];dest=self.destination.get()
        sid,_,_=parse(self.link.get())
        def action():
            for iid,f in files:
                path=export_unverified(f,dest,sid)
                self.q.put(('file',(iid,'已保存（校验待确认）')));self.q.put(('path',str(path)))
                self.q.put(('log',f'已保存现有数据，没有网络下载：{path.name}。MD5 未通过，播放正常也不能证明字节完全正确。'))
            self.q.put(('success','现有视频已保存，可从下载目录直接播放；校验仍待确认。没有重新下载。'))
        self.start(action)
    def verify(self):
        selected=self.selected()
        if not selected:return
        def action():
            for iid,f in selected:
                if self.stop.is_set():raise Paused('已停止验证。')
                report=self.api.verify(f)
                path=Path(__file__).parent/'direct-verification.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                self.q.put(('file',(iid,'范围验证通过')))
            self.q.put(('success','范围验证通过；点击“下载所选文件 / 继续下载”保存完整文件。'))
        self.start(action)
    def pause(self):
        self.queue_autorun=False
        self.stop.set();self.eta.set('已请求暂停，预计完成时间暂不计算');self.status.set('正在暂停，等待当前网络读取结束（一般不超过 30 秒）…')
    def open_folder(self):
        path=Path(self.destination.get())
        if path.exists():os.startfile(str(path))
        else:messagebox.showinfo('下载目录','目录尚未创建，开始下载后会自动创建。')
    def pump(self):
        while not self.q.empty():
            typ,data=self.q.get()
            if typ=='idle':
                self.set_busy(False);self.save_history()
                if self.queue_autorun and self.download_queue and self.download_queue.has_pending():self.start_queue()
            elif typ=='incoming':data[0](*data[1])
            elif typ=='queue_changed':self.refresh_queue()
            elif typ=='queue_job':
                if data['status']=='下载中':self.root.title('下载中 · '+data['file']['display'][:48]+' · QuarkBridge 0.11.0')
                sid=self.api.sid if self.api else parse(self.link.get())[0]
                if data['sid']==sid:
                    for i,f in enumerate(self.rows):
                        if str(f['fid'])==str(data['file']['fid']):
                            self.statuses[str(f['fid'])]=data['status']
                            if self.tree.exists(str(i)):self.tree.set(str(i),'state',data['status'])
                            if self.tree.exists(str(i)):self.tree.set(str(i),'reason',data.get('error',''))
                if data.get('error'):
                    next_step='点击“重试失败任务”继续。' if data['status']=='失败，可重试' else ('数据已接收完整，可保存现有文件使用。' if data['status']=='校验待确认' else '在任务列表选择使用已有文件或仍然下载。')
                    self.error_detail.set(f'{data["file"]["display"]}：{data["error"]} {next_step}')
                elif data['status'].startswith(('已完成','已存在','已保存','已跳过')) and not any(j['status'] in ('失败，可重试','校验待确认','疑似重复，待选择') for j in self.download_queue.snapshot()):
                    self.error_detail.set('')
                if data.get('path'):self.last_path=data['path']
                self.refresh_queue();self.save_history()
            elif typ=='log':self.log_message(data)
            elif typ=='error':self.latest_progress=None;self.status.set(data);self.error_detail.set(data);self.eta.set('任务未完成，断点已保留');self.log_message(data)
            elif typ=='success':self.latest_progress=None;self.status.set(data);self.eta.set('操作完成');self.root.title('夸克下载 · QuarkBridge 0.11.0');self.log_message(data)
            elif typ=='cookie':
                self.cookie.set(data)
                self.queue_cookie=data
                if self.api and hasattr(self.api,'cookie'):self.api.cookie=data
                if self.snapshot:self.snapshot=(self.snapshot[0],self.snapshot[1],data)
                self.login_status.set('服务端已更新 Cookie'+('，已加密保存' if self.auth_enabled else '，仅本次会话使用'))
            elif typ=='path':self.last_path=data
            elif typ=='file':
                self.tree.set(data[0],'state',data[1]);self.statuses[str(self.rows[int(data[0])]['fid'])]=data[1];self.save_history()
            elif typ=='scanned':
                self.api,self.snapshot,rows=data
                self.cookie.set(self.api.cookie);self.snapshot=(self.snapshot[0],self.snapshot[1],self.api.cookie)
                self.populate(rows);self.save_history()
                self.status.set(f'已解析 {len(rows)} 个文件。选中文件后点击下载按钮。')
                self.log_message('账号容量已读取。' if self.api.before else '账号容量无法读取；免转存下载不以剩余容量为前置条件。')
        if self.latest_progress:
            done,total,phase=self.latest_progress;now=time.monotonic()
            if now-self.last_update>0.3:
                if phase.startswith('下载中') and not self.previous_phase.startswith('下载中'):self.speed_reset=True
                if self.speed_reset or done<self.last_bytes:
                    self.speed_samples.clear();self.speed_reset=False
                self.previous_phase=phase
                self.speed_samples.append((now,done))
                while len(self.speed_samples)>2 and now-self.speed_samples[0][0]>10:self.speed_samples.popleft()
                first_time,first_bytes=self.speed_samples[0]
                speed=max(0,done-first_bytes)/max(now-first_time,0.001)
                if '检查本地' in phase or '已存在' in phase:speed=0
                self.progress['value']=100*done/max(total,1)
                self.status.set(f'{phase} · {done/1024**2:,.1f} / {total/1024**2:,.1f} MiB · {100*done/max(total,1):.1f}% · {speed/1024**2:.2f} MiB/s')
                if not self.stop.is_set():self.eta.set(eta_text(total-done,speed,now-first_time,phase))
                if self.queue_running and speed>0 and now-first_time>=3 and '校验' not in phase:
                    waiting=sum(max(0,int(j['file']['size'])-int(j.get('downloaded',0))) for j in self.download_queue.snapshot() if j['status'] in ('等待下载','已暂停'))
                    estimate=eta_text(total-done+waiting,speed,now-first_time,'下载中').replace('当前文件','队列')
                    self.eta.set(self.eta.get()+'\n'+estimate+'（按当前速度粗估，不含校验耗时）')
                self.last_bytes=done;self.last_clock=now;self.last_update=now
                self.refresh_queue()
        if self.closing and not self.busy and not any(t.is_alive() for t in self.dialog_threads):self.close();return
        self.timer=self.root.after(100,self.pump)
    def close(self):
        if self.closed:return
        self.closing=True;self.queue_autorun=False
        if self.busy or any(t.is_alive() for t in self.dialog_threads):
            if self.busy:self.pause()
            self.add_share_button.configure(state='disabled')
            for window in self.dialogs:
                if window.winfo_exists():window.withdraw()
            self.status.set('正在保存断点，完成后自动退出；不需要再次关闭窗口。');return
        self.save_history();self.root.after_cancel(self.timer)
        thread=getattr(self,'worker_thread',None)
        if thread:thread.join(timeout=.2)
        if self.download_queue:
            self.download_queue.changed=lambda:None;self.download_queue.live.clear()
        self.api=None
        self.closed=True;self.root.destroy()

def gui():
    store=Store();store.directory.mkdir(parents=True,exist_ok=True)
    try:
        with file_lock(store.directory/'app.lock'):
            from collect_files import organize_saved
            organize_saved(store)
            App(tk.Tk(),store).root.mainloop()
    except ProbeError:
        root=tk.Tk();root.withdraw();messagebox.showinfo('工具已打开','已有一个新版下载窗口正在运行，请使用原窗口。');root.destroy()
if __name__=='__main__':gui()
