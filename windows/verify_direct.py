"""Read-only direct-share feasibility probe. No save/delete/upload APIs exist here."""
import argparse
import hashlib
import http.cookiejar
import json
import queue
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, urlencode, unquote
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler
from urllib.error import HTTPError

BASE = 'https://drive-pc.quark.cn/1/clouddrive/'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) quark-cloud-drive/2.5.20 Chrome/100.0.4896.160 Electron/18.3.5.4-b478491100 Safari/537.36 Channel/pckk_other_ch'
ALLOWED = {'member', 'share/sharepage/token', 'share/sharepage/detail', 'file/download'}
class ProbeError(Exception): pass
class APIError(ProbeError):
    def __init__(self,path,code,status=None):
        self.path,self.code,self.status=path,str(code),status
        super().__init__(f'{path} HTTP {status}，接口 code={self.code}')
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProbeError('请求发生跳转，验证停止；未向跳转目标发送凭证。')

def parse(link):
    u = urlparse(link.strip())
    if u.scheme!='https' or u.hostname not in ('pan.quark.cn','drive.quark.cn'):
        raise ProbeError('只接受夸克官方 HTTPS 分享链接。')
    m=re.fullmatch('/s/([A-Za-z0-9_-]+)/?',u.path)
    if not m: raise ProbeError('分享链接格式不正确。')
    from urllib.parse import parse_qs
    parent='0'
    if u.fragment:
        parts=unquote(u.fragment).strip('/').split('/')
        if len(parts)>=3 and parts[:2]==['list','share']: parent=parts[2]
    return m[1],parent,parse_qs(u.query).get('pwd',[''])[0]

class Probe:
    def __init__(self,cookie='',emit=print,on_cookie=None):
        self.cookie=cookie.strip()
        if '\r' in self.cookie or '\n' in self.cookie: raise ProbeError('Cookie 必须是一行。')
        self.emit=emit
        self.on_cookie=on_cookie
        self.jar=http.cookiejar.CookieJar()
        self.opener=build_opener(NoRedirect(),HTTPCookieProcessor(self.jar))
        self.audit=[]

    def call(self,path,body=None,**query):
        if path not in ALLOWED: raise ProbeError('安全限制：禁止任何转存、删除或上传接口。')
        self.audit.append({'endpoint':path,'method':'POST' if body is not None else 'GET'})
        headers={'User-Agent':UA,'Referer':'https://pan.quark.cn/','Content-Type':'application/json','Accept':'application/json'}
        if self.cookie: headers['Cookie']=self.cookie
        req=Request(BASE+path+'?'+urlencode(dict(pr='ucpro',fr='pc',entry='ft',**query)),
                    data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with self.opener.open(req,timeout=30) as r:
                for h in r.headers.get_all('Set-Cookie',[]):
                    m=re.search(r'(?:^|\s)__puus=([^;]+)',h)
                    if m and self.cookie:
                        if '__puus=' in self.cookie: self.cookie=re.sub(r'__puus=[^;]*','__puus='+m[1],self.cookie)
                        else: self.cookie+='; __puus='+m[1]
                        if self.on_cookie:self.on_cookie(self.cookie)
                d=json.load(r)
        except HTTPError as e:
            try:
                rejected=json.loads(e.read(65536))
                code=rejected.get('code')
                safe_code=str(code) if isinstance(code,(int,float)) else 'unknown'
            except (ValueError,OSError): safe_code='unknown'
            if safe_code in ('40001','10000') or e.code==401:
                raise ProbeError('登录已过期，请重新登录夸克并更新 Cookie；任务和断点已保留。') from None
            raise APIError(path,safe_code,e.code) from None
        except (OSError,ValueError): raise ProbeError(f'{path} 网络或响应格式错误') from None
        if d.get('code')!=0:
            if d.get('code') in (40001,10000):raise ProbeError('登录已过期，请重新登录夸克并更新 Cookie；任务和断点已保留。')
            # Do not print raw responses, tokens, URLs or arbitrary server text.
            raise APIError(path,d.get('code') if isinstance(d.get('code'),(int,float)) else 'unknown',d.get('status') if isinstance(d.get('status'),int) else None)
        return d.get('data')

    def capacity(self):
        try:
            d=self.call('member',fetch_subscribe='true',fetch_identity='true')
            return {'total':int(d['total_capacity']),'used':int(d['use_capacity'])}
        except (ProbeError,KeyError,TypeError,ValueError): return None

    def scan(self,link,password=''):
        self.sid,parent,auto=parse(link)
        self.password=password or auto
        self.before=self.capacity()
        self.token=self.call('share/sharepage/token',{'pwd_id':self.sid,'passcode':password or auto,'share_for_transfer':True})['stoken']
        files=[]; seen=set()
        def walk(fid,prefix='',depth=0):
            if depth>30 or fid in seen: raise ProbeError('目录过深或循环，验证停止。')
            seen.add(fid)
            for page in range(1,101):
                d=self.call('share/sharepage/detail',pwd_id=self.sid,stoken=self.token,pdir_fid=fid,
                            _page=page,_size=100,_fetch_total=1,_sort='file_type:asc,file_name:asc')
                rows=d.get('list',[])
                for f in rows:
                    name=prefix+f.get('file_name','未命名')
                    if f.get('dir'): walk(str(f['fid']),name+'/',depth+1)
                    else:
                        f['display']=name
                        f['parent_fid']=fid
                        f['path_parts']=prefix.rstrip('/').split('/')+[f.get('file_name','未命名')] if prefix else [f.get('file_name','未命名')]
                        files.append(f)
                        if len(files)>1000: raise ProbeError('验证器最多枚举 1000 个文件，请使用更小的子目录链接。')
                if len(rows)<100: break
            else: raise ProbeError('目录分页超过验证上限。')
        walk(parent)
        self.files=files
        return files

    def direct_link(self,f,refresh=False):
        try:
            return self._direct_link(f,refresh)
        except APIError as error:
            if error.path!='file/download' or error.code not in ('23018','50052'):
                raise
            self.emit('取链被拒绝，正在刷新分享凭据并重试一次…')
            try:
                return self._direct_link(f,True)
            except APIError as retry:
                if retry.path!='file/download':raise
                account='未能确认账号状态'
                try:
                    member=self.call('member',fetch_subscribe='true',fetch_identity='true')
                    if isinstance(member,dict) and 'total_capacity' in member:
                        account='账号容量接口可用，但这不代表该分享允许下载'
                except ProbeError as check:
                    if '登录已过期' in str(check):account='账号接口明确提示登录已过期，请更新登录'
                raise ProbeError(f'免转存取链失败，已刷新分享凭据并重试一次；{account}。'
                                 f'file/download HTTP {retry.status}，接口 code={retry.code}。'
                                 '未转存或删除云端文件；断点保留。可点“重试所选”；持续失败请先在官方客户端核对同一文件的下载权限。') from None

    def _direct_link(self,f,refresh=False):
        if refresh:
            self.token=self.call('share/sharepage/token',{'pwd_id':self.sid,'passcode':self.password,'share_for_transfer':True})['stoken']
            for page in range(1,101):
                data=self.call('share/sharepage/detail',pwd_id=self.sid,stoken=self.token,pdir_fid=f.get('parent_fid','0'),_page=page,_size=100)
                rows=data.get('list',[])
                match=next((x for x in rows if x['fid']==f['fid']),None)
                if match:
                    if int(match.get('size',-1))!=int(f['size']):raise ProbeError('分享文件大小已改变，请重新解析。')
                    f['share_fid_token']=match['share_fid_token'];break
                if len(rows)<100:raise ProbeError('分享文件已移除，请重新解析。')
            else:raise ProbeError('刷新文件令牌超过分页上限。')
        data=self.call('file/download',{'fids':[f['fid']],'fids_token':[f['share_fid_token']],
                       'pwd_id':self.sid,'stoken':self.token,'speedup_session':''})
        rows=data if isinstance(data,list) else data.get('list',[])
        if len(rows)!=1 or not rows[0].get('download_url'):raise ProbeError('未获取到唯一下载链接。')
        url=rows[0]['download_url'];u=urlparse(url)
        if u.scheme!='https' or not u.hostname or not (u.hostname=='quark.cn' or u.hostname.endswith('.quark.cn')) or u.username or u.password or u.port not in (None,443):
            raise ProbeError('下载地址不在官方域名白名单，已停止。')
        headers={'User-Agent':UA,'Referer':'https://pan.quark.cn/','Accept-Encoding':'identity'}
        if self.cookie:headers['Cookie']=self.cookie
        return url,headers,rows[0].get('md5') or f.get('md5')

    def verify(self,f,full=False,destination=None):
        size=int(f['size'])
        if size<=0: raise ProbeError('请选择非空文件。')
        d=self.call('file/download',{'fids':[f['fid']], 'fids_token':[f['share_fid_token']],
                    'pwd_id':self.sid,'stoken':self.token,'speedup_session':''})
        rows=d if isinstance(d,list) else d.get('list',[])
        if len(rows)!=1 or not rows[0].get('download_url'): raise ProbeError('接口没有返回唯一下载链接。')
        url=rows[0]['download_url']
        u=urlparse(url)
        # API-derived download credentials are sent only to Quark's own host namespace.
        if u.scheme!='https' or not u.hostname or not (u.hostname=='quark.cn' or u.hostname.endswith('.quark.cn')) or u.username or u.password or u.port not in (None,443):
            raise ProbeError('下载主机不在验证器的夸克官方域名白名单；已停止，不发送 Cookie。')
        n=min(1024*1024,size)
        ranges=[(0,n-1),(max(0,size//2-n//2),min(size-1,size//2-n//2+n-1)),(max(0,size-n),size-1)]
        samples=[]
        cdn=build_opener(NoRedirect())
        def read_range(start,end,sink=None):
            headers={'User-Agent':UA,'Referer':'https://pan.quark.cn/','Accept-Encoding':'identity','Range':f'bytes={start}-{end}'}
            if self.cookie: headers['Cookie']=self.cookie
            else:
                for c in self.jar:
                    if c.name=='__puus': headers['Cookie']='__puus='+c.value
            try:
                with cdn.open(Request(url,headers=headers),timeout=40) as r:
                    expected=f'bytes {start}-{end}/{size}'
                    if r.status!=206 or r.headers.get('Content-Range')!=expected:
                        raise ProbeError('响应范围或总大小不符合原文件，不能认定下载成功。')
                    h=hashlib.sha256(); count=0
                    while count<end-start+1:
                        b=r.read(min(1024*1024,end-start+1-count))
                        if not b: raise ProbeError('文件内容提前结束。')
                        h.update(b); count+=len(b)
                        if sink: sink.write(b)
                    return {'start':start,'end':end,'bytes':count,'sha256':h.hexdigest()}
            except HTTPError as e: raise ProbeError(f'下载请求被拒绝，HTTP {e.code}。') from None
            except OSError: raise ProbeError('下载网络中断。') from None
        for start,end in dict.fromkeys(ranges):
            samples.append(read_range(start,end))
            self.emit(f'已读取并核对区间 {start}–{end}，总大小 {size} 字节。')
        full_hash=None
        if full:
            import shutil
            dest=Path(destination)
            dest.mkdir(parents=True,exist_ok=True)
            if shutil.disk_usage(dest).free<size+100*1024**2: raise ProbeError('本地空间不足。')
            # Random name prevents conflicts and path traversal; original name stays only in UI.
            import uuid
            output=dest/('direct-test-'+uuid.uuid4().hex+'.bin')
            part=output.with_suffix('.part')
            with part.open('xb') as out:
                for start in range(0,size,8*1024**2):
                    read_range(start,min(size-1,start+8*1024**2-1),out)
                    self.emit(f'完整验证进度：{out.tell()/size:.1%}')
            h=hashlib.md5()
            with part.open('rb') as r:
                for b in iter(lambda:r.read(4*1024**2),b''): h.update(b)
            full_hash=h.hexdigest()
            expected=rows[0].get('md5') or f.get('md5')
            if expected and re.fullmatch(r'[a-fA-F0-9]{32}',expected) and full_hash.lower()!=expected.lower():
                raise ProbeError('完整文件 MD5 不一致，保留 .part，请勿视为下载成功。')
            part.rename(output)
            self.emit(f'完整文件已保存：{output}')
        after=self.capacity()
        return {'result':'full_download_passed' if full else 'range_samples_passed',
                'file_size':size,'samples':samples,'full_md5':full_hash,
                'capacity_before':self.before,'capacity_after':after,
                'exceeds_available':None if self.before is None else size>max(0,self.before['total']-self.before['used']),
                'api_calls':self.audit,'cloud_mutation_calls':0,
                'limitation':None if full else '仅首/中/尾范围验证通过，未验证完整下载或长期稳定性。'}

def gui():
    import tkinter as tk
    from tkinter import ttk,messagebox
    app=tk.Tk(); app.title('夸克免转存可行性验证（只读）'); app.geometry('850x640')
    link=tk.StringVar(value='https://pan.quark.cn/s/example')
    pwd=tk.StringVar(); cookie=tk.StringVar(); full=tk.BooleanVar()
    q=queue.Queue(); current=None; busy=False
    for title,var,hidden in [('分享链接',link,False),('提取码',pwd,False),('Cookie（仅内存，不保存）',cookie,True)]:
        ttk.Label(app,text=title).pack(anchor='w',padx=12)
        ttk.Entry(app,textvariable=var,show='*' if hidden else '').pack(fill='x',padx=12,pady=3)
    ttk.Label(app,text='不会转存、删除或上传。先解析目录，选择一个文件，再验证首/中/尾数据。').pack(anchor='w',padx=12,pady=8)
    files=tk.Listbox(app,height=9); files.pack(fill='both',expand=True,padx=12)
    ttk.Checkbutton(app,text='完整下载验证（保存到 F:\\QuarkDownloads；不勾选时最多读取约 3 MiB）',variable=full).pack(anchor='w',padx=12)
    log=tk.Text(app,height=9,state='disabled'); log.pack(fill='both',padx=12,pady=6)
    def launch(action):
        nonlocal busy
        if busy:return
        busy=True
        def worker():
            try: action()
            except ProbeError as e:q.put(('log',str(e)))
            except Exception:q.put(('log','未预期错误；没有进行任何转存或删除。'))
            finally:q.put(('done',None))
        threading.Thread(target=worker,daemon=True).start()
    def scan():
        values=link.get(),pwd.get(),cookie.get()
        def work():
            p=Probe(values[2],lambda s:q.put(('log',s)))
            rows=p.scan(values[0],values[1]); q.put(('scan',(p,rows)))
        launch(work)
    def verify():
        if busy or current is None or not files.curselection():return
        f=current.files[files.curselection()[0]]; complete=full.get(); p=current
        def work():
            report=p.verify(f,complete,r'F:\QuarkDownloads')
            path=Path(__file__).parent/'direct-verification.json'
            path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            q.put(('log',f'验证结果：{report["result"]}；报告已保存（不含 Cookie/直链/分享令牌）。'))
        launch(work)
    buttons=ttk.Frame(app);buttons.pack(pady=7)
    ttk.Button(buttons,text='1. 解析目录',command=scan).pack(side='left',padx=8)
    ttk.Button(buttons,text='2. 验证所选文件',command=verify).pack(side='left',padx=8)
    def pump():
        nonlocal busy,current
        while not q.empty():
            typ,data=q.get()
            if typ=='done':busy=False
            elif typ=='scan':
                current,rows=data;files.delete(0,'end')
                for f in rows:files.insert('end',f'{int(f["size"])/1024**3:.3f} GiB | {f["display"]}')
                if rows:files.selection_set(0)
                q.put(('log',f'已解析 {len(rows)} 个文件；账号容量：'+('已读取' if current.before else '无法读取')))
            else:log.configure(state='normal');log.insert('end',data+'\n');log.see('end');log.configure(state='disabled')
        app.after(150,pump)
    def close():
        if busy:messagebox.showinfo('正在验证','请等待本次验证结束再关闭。');return
        app.destroy()
    app.protocol('WM_DELETE_WINDOW',close);pump();app.mainloop()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--anonymous');args=parser.parse_args()
    if args.anonymous:
        p=Probe()
        try:
            rows=p.scan(args.anonymous)
            print(json.dumps({'files':[{'name':f['display'],'size':f['size']} for f in rows]},ensure_ascii=False))
            if rows:print(json.dumps(p.verify(rows[0]),ensure_ascii=False,indent=2))
        except ProbeError as e: print(str(e))
    else:
        from direct_app import gui as download_gui
        download_gui()
