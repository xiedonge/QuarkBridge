"""Shared Tk share picker for Windows and macOS."""
import threading
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path

from share_selection import ShareSelection, QUALITIES, quality
from task_view import share_input, size_text
from verify_direct import APIError, LoginExpired, Probe, ProbeError


def open_share_dialog(app):
    window = tk.Toplevel(app.root)
    window.title('添加分享文件')
    window.geometry('900x660')
    window.minsize(680,540)
    app.dialogs.add(window)
    panel = ttk.Frame(window,padding=12)
    panel.pack(fill='both',expand=True)
    panel.columnconfigure(1,weight=1)
    panel.rowconfigure(6,weight=1)

    password = tk.StringVar()
    destination = tk.StringVar(value=app.destination.get())
    search = tk.StringVar()
    videos = tk.BooleanVar(value=False)
    resolution = tk.StringVar(value=QUALITIES[0])
    hint = tk.StringVar(value='粘贴分享文案，按回车解析。列表默认不勾选文件。')
    summary = tk.StringVar()
    selection = ShareSelection()
    parsed = None
    busy = False
    needs_refresh = False

    def dismiss():
        app.dialogs.discard(window)
        window.destroy()

    window.protocol('WM_DELETE_WINDOW',dismiss)
    window.bind('<Escape>',lambda event:dismiss())
    ttk.Label(panel,text='链接 / 分享文案').grid(row=0,column=0,sticky='nw')
    link = tk.Text(panel,height=3,wrap='word',takefocus=True)
    link.grid(row=0,column=1,sticky='ew',padx=6)
    link.bind('<Tab>',lambda e:(password_entry.focus_set(),'break')[1])

    def paste():
        try:
            value=window.clipboard_get()
        except tk.TclError:
            hint.set('剪贴板中没有文本。')
            return
        link.delete('1.0','end')
        link.insert('1.0',value)
        password.set('')
        link.focus_set()

    ttk.Button(panel,text='粘贴分享文案',command=paste).grid(row=0,column=2,sticky='n')
    ttk.Label(panel,text='提取码（可自动识别）').grid(row=1,column=0,sticky='w')
    password_entry=ttk.Entry(panel,textvariable=password)
    password_entry.grid(row=1,column=1,sticky='ew',padx=6,pady=4)
    ttk.Label(panel,text='保存目录').grid(row=2,column=0,sticky='w')
    ttk.Entry(panel,textvariable=destination).grid(row=2,column=1,sticky='ew',padx=6,pady=4)

    def choose_destination():
        current=Path(destination.get())
        value=filedialog.askdirectory(parent=window,initialdir=current if current.is_dir() else None)
        if value:destination.set(value)

    ttk.Button(panel,text='选择保存目录',command=choose_destination).grid(row=2,column=2)
    ttk.Label(panel,textvariable=app.login_status,wraplength=590).grid(row=3,column=0,columnspan=2,sticky='w')

    controls=ttk.Frame(panel)
    controls.grid(row=4,column=0,columnspan=3,sticky='ew',pady=6)
    ttk.Label(controls,text='搜索文件名或目录').pack(side='left')
    ttk.Entry(controls,textvariable=search,width=20).pack(side='left',padx=6)
    ttk.Checkbutton(controls,text='仅视频',variable=videos).pack(side='left',padx=6)
    ttk.Label(controls,text='画质').pack(side='left')
    ttk.Combobox(controls,textvariable=resolution,values=QUALITIES,state='readonly',width=12).pack(side='left',padx=6)

    tree=ttk.Treeview(panel,columns=('checked','name','quality','size'),show='headings',selectmode='browse',takefocus=True)
    for key,label,width in (('checked','勾选',62),('name','文件 / 原目录',410),('quality','画质及来源',150),('size','大小',92)):
        tree.heading(key,text=label)
        tree.column(key,width=width,stretch=key=='name')
    tree.grid(row=6,column=0,columnspan=2,sticky='nsew')
    scroll=ttk.Scrollbar(panel,command=tree.yview)
    scroll.grid(row=6,column=2,sticky='nsw')
    tree.configure(yscrollcommand=scroll.set)

    actions=ttk.Frame(panel)
    actions.grid(row=5,column=0,columnspan=3,sticky='w',pady=4)
    ttk.Label(actions,text='点击勾选列或按空格勾选当前行').pack(side='left',padx=6)
    ttk.Label(panel,textvariable=hint,wraplength=760).grid(row=7,column=0,columnspan=3,sticky='w',pady=5)
    ttk.Label(panel,textvariable=summary,wraplength=660).grid(row=8,column=0,columnspan=3,sticky='w')
    footer=ttk.Frame(panel)
    footer.grid(row=9,column=0,columnspan=3,sticky='w',pady=6)

    def share_values():
        return share_input(link.get('1.0','end-1c'),password.get())

    def update_buttons(*_):
        rows=selection.selected()
        summary.set(f'筛选结果 {len(selection.visible)} 项 · 待加入 {len(rows)} 项 · {size_text(sum(int(f["size"]) for f in rows))}\n保存到 {destination.get().strip() or "未选择目录"}')
        try:
            same=parsed is not None and share_values()==parsed[1]
        except (ValueError,ProbeError):
            same=False
        enabled=bool(rows and same and destination.get().strip() and not busy and not app.login_active and not needs_refresh and not app.login_expired)
        for button in (enqueue,download):
            button.configure(state='normal' if enabled else 'disabled')

    def redraw(*_):
        focused=tree.focus()
        selection.filter(search.get(),videos.get(),resolution.get())
        tree.delete(*tree.get_children())
        for key in selection.visible:
            file=selection.files[key]
            label,source=quality(file)
            tree.insert('','end',iid=key,values=('已勾选' if key in selection.checked else '未勾选',file['display'],f'{label} · {source}',size_text(file['size'])))
        if focused in selection.visible:
            tree.focus(focused)
            tree.selection_set(focused)
        update_buttons()

    def toggle(key):
        selection.toggle(key)
        if tree.exists(key):tree.set(key,'checked','已勾选' if key in selection.checked else '未勾选')
        update_buttons()

    def click(event):
        if tree.identify_column(event.x)=='#1':
            key=tree.identify_row(event.y)
            if key:toggle(key)

    def use_space(event):
        toggle(tree.focus())
        return 'break'

    def select_all():
        selection.checked.update(selection.visible)
        redraw()

    def clear():
        selection.checked.clear()
        redraw()

    def after_login(success,message):
        nonlocal needs_refresh
        if not window.winfo_exists():return
        if success:needs_refresh=True
        hint.set(message)
        scan.configure(text='继续解析' if success else '解析分享')
        window.lift()
        scan.focus_set()
        update_buttons()

    def refresh_login():
        if busy:return
        app.login_quark(after_login)

    def finished(api,rows,values,error):
        nonlocal busy,parsed,needs_refresh
        if not window.winfo_exists():return
        busy=False
        scan.configure(state='normal')
        if error:
            if isinstance(error,LoginExpired):
                app.login_expired=True
                app.login_status.set('登录已过期，请重新登录夸克。')
                hint.set('点击“重新登录”。分享文案、提取码、筛选和已勾选文件将保留。')
            elif isinstance(error,APIError) and error.status in (401,403):
                hint.set('夸克没有授权读取此分享。请在官方页面确认分享有效及提取码正确。')
            else:
                hint.set(str(error) if isinstance(error,ProbeError) else f'解析未完成（{type(error).__name__}），请重试。')
        else:
            keep=parsed is not None and values==parsed[1]
            needs_refresh=False
            parsed=(api,values)
            selection.load(rows,keep=keep)
            scan.configure(text='解析分享')
            hint.set(f'已解析 {len(rows)} 个文件'+('，已自动识别提取码。' if values[1] else '。'))
        redraw()

    def parse_share(event=None):
        nonlocal busy
        if busy or app.login_active:return 'break'
        if app.login_expired:
            hint.set('请先点击“重新登录”，再继续解析。')
            return 'break'
        try:
            values=share_values()
        except (ValueError,ProbeError) as error:
            hint.set(str(error))
            return 'break'
        busy=True
        scan.configure(state='disabled')
        hint.set('正在解析分享…')
        update_buttons()
        cookie=app.queue_cookie if app.busy else app.cookie.get()

        def worker():
            try:
                api=Probe(cookie,lambda message:None,app.cookie_callback(values[1]))
                rows=api.scan(*values)
                args=(api,rows,values,None)
            except Exception as error:
                args=(None,None,values,error)
            app.q.put(('incoming',(finished,args)))

        thread=threading.Thread(target=worker,daemon=True)
        app.dialog_threads.append(thread)
        thread.start()
        return 'break'

    def add(start):
        update_buttons()
        if enqueue.instate(('disabled',)):return
        api,(url,code)=parsed
        if not app.add_to_queue(api,selection.selected(),url,code,destination.get().strip(),int(app.connections.get())):
            hint.set('队列保存失败，请检查磁盘空间及目录权限。')
            return
        if start:app.start_queue()
        dismiss()

    scan=ttk.Button(footer,text='解析分享',command=parse_share)
    scan.pack(side='left')
    enqueue=ttk.Button(footer,text='加入队列',command=lambda:add(False))
    enqueue.pack(side='left',padx=8)
    download=ttk.Button(footer,text='立即下载',command=lambda:add(True))
    download.pack(side='left')
    ttk.Button(panel,text='重新登录',command=refresh_login).grid(row=3,column=2)
    ttk.Button(actions,text='全选筛选结果',command=select_all).pack(side='left')
    ttk.Button(actions,text='清空选择',command=clear).pack(side='left',padx=6)
    tree.bind('<ButtonRelease-1>',click)
    tree.bind('<space>',use_space)
    link.bind('<Return>',parse_share)
    link.bind('<Shift-Return>',lambda e:None)
    password_entry.bind('<Return>',parse_share)

    def edited(event):
        if link.edit_modified():
            link.edit_modified(False)
            update_buttons()

    link.bind('<<Modified>>',edited)
    for var in (search,videos,resolution):var.trace_add('write',redraw)
    for var in (password,destination):var.trace_add('write',update_buttons)
    login_trace=app.login_status.trace_add('write',lambda *_:update_buttons() if window.winfo_exists() else None)
    def on_destroy(event):
        if event.widget is window:app.login_status.trace_remove('write',login_trace)
    window.bind('<Destroy>',on_destroy)
    link.focus_set()
    redraw()
    return window
