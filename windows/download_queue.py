"""Persistent file queue. No URLs with credentials or live share tokens on disk."""
import copy
import hashlib
import json
import threading
import time
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from direct_download import output_path,Paused,ChecksumMismatch,export_unverified
from verify_direct import ProbeError
from safe_diagnostics import failure_detail
from download_catalog import PotentialDuplicate
from pathlib import Path

TERMINAL=('已完成','已保存','已存在','已跳过')
def clean_link(link):
    u=urlsplit(link)
    return urlunsplit((u.scheme,u.netloc,u.path,urlencode([(k,v) for k,v in parse_qsl(u.query) if k!='pwd']),u.fragment))

class DownloadQueue:
    def __init__(self,store,changed=lambda:None):
        self.store=store;self.changed=changed;self.lock=threading.RLock();self.live={};self.running=False
        self.jobs=store.load_queue();self.passwords=store.load_queue_secrets();self.active_id=None;self.last_progress_save=0
        for j in self.jobs:
            if j['status']=='下载中':j['status']='已暂停'
        if self.jobs:self.save()
    def save(self):
        self.store.save_queue(self.jobs);self.changed()
    def snapshot(self):
        with self.lock:return copy.deepcopy(self.jobs)
    def progress(self,done,total,phase):
        # Display counters are advisory; .quark.json remains the authoritative checkpoint.
        with self.lock:
            job=next((j for j in self.jobs if j['id']==self.active_id),None)
            if job is None:return
            job['phase']=phase
            if '检查本地' not in phase:job['downloaded']=min(int(total),max(0,int(done)))
            now=time.monotonic()
            if now-self.last_progress_save>=2:
                self.save();self.last_progress_save=now
    def checkpoint_bytes(self,job,dl):
        target=getattr(dl,'active_target',None)
        if not target:return
        target=Path(target);job['local_target']=str(target)
        try:
            data=json.loads(target.with_name(target.name+'.quark.json').read_text('utf-8'));offset=0
            for chunk in data.get('chunks',[]):
                if int(chunk['start'])!=offset or int(chunk['length'])<=0:break
                offset+=int(chunk['length'])
            job['downloaded']=min(offset,int(job['file']['size']))
        except (OSError,ValueError,KeyError,TypeError):pass
    def add(self,api,files,link,password,destination,workers=4,remember=True,library_root=None):
        allowed=('fid','size','display','path_parts','parent_fid','file_name','md5')
        added=0
        with self.lock:
            previous=list(self.jobs);old_values=copy.deepcopy(self.jobs);old_live=dict(self.live);old_passwords=dict(self.passwords)
            # Keep active job identity when appending while a download is running.
            self.jobs=list(self.jobs)
            for f in files:
                key=hashlib.sha256((api.sid+'|'+str(f['fid'])+'|'+str(output_path(destination,api.sid,f)).lower()).encode()).hexdigest()[:24]
                self.live[key]=(api,f);self.passwords[key]=password
                existing=next((j for j in self.jobs if j['id']==key),None)
                if existing:
                    if existing['status']=='下载中':continue
                    existing.update(workers=int(workers),requires_password=bool(password))
                    if existing['status']=='失败，可重试' or (existing['status'].startswith(TERMINAL) and not Path(existing.get('path') or '').is_file()):
                        existing.update(status='等待下载',error='',path=None);added+=1
                    continue
                self.jobs.append({'id':key,'sid':api.sid,'link':clean_link(link),'file':{k:v for k,v in f.items() if k in allowed},
                                  'destination':str(destination),'library_root':str(library_root or destination),'workers':int(workers),'requires_password':bool(password),
                                  'status':'等待下载','error':'','path':None,'downloaded':0,'phase':'','created_at':time.time()})
                added+=1
            try:
                if remember:self.store.save_queue_secrets(self.passwords)
                self.save()
            except Exception:
                for job,values in zip(previous,old_values):job.clear();job.update(values)
                self.jobs=previous;self.live=old_live;self.passwords=old_passwords
                raise
        return added
    def edit(self,ids,action):
        with self.lock:
            wanted=set(ids)
            if action=='remove':
                removed=[j['id'] for j in self.jobs if j['id'] in wanted and j['status']!='下载中']
                self.jobs[:]=[j for j in self.jobs if j['id'] not in removed]
                for key in removed:self.live.pop(key,None);self.passwords.pop(key,None)
            elif action=='retry':
                for j in self.jobs:
                    if j['id'] in wanted and j['status']!='下载中' and not j['status'].startswith(TERMINAL):
                        j.update(status='等待下载',error='')
            elif action in ('up','down'):
                # Move waiting/paused/failed rows only; the active file keeps its position.
                step=-1 if action=='up' else 1
                indices=range(len(self.jobs)) if step<0 else range(len(self.jobs)-1,-1,-1)
                for i in indices:
                    n=i+step
                    if self.jobs[i]['id'] in wanted and self.jobs[i]['status']!='下载中' and 0<=n<len(self.jobs):
                        other=self.jobs[n]
                        if other['id'] not in wanted and other['status']!='下载中':self.jobs[i],self.jobs[n]=other,self.jobs[i]
            self.save()
    def has_pending(self):
        with self.lock:return any(j['status'] in ('等待下载','已暂停') for j in self.jobs)
    def run(self,stop,api_factory,downloader_factory,event):
        with self.lock:
            if self.running:return
            self.running=True
        try:
            while not stop.is_set():
                with self.lock:
                    job=next((j for j in self.jobs if j['status'] in ('等待下载','已暂停')),None)
                    if job is None:break
                    job.update(status='下载中',error='',phase='准备下载');self.active_id=job['id'];self.last_progress_save=0
                    self.save();cached=self.live.get(job['id'])
                    password=self.passwords.get(job['id'],'')
                event('active',copy.deepcopy(job))
                dl=None
                try:
                    if cached:api,f=cached
                    else:
                        if job['requires_password'] and not password:raise ProbeError('队列提取码未保存，请重新添加同一分享及提取码后重试。')
                        api=api_factory(password);rows=api.scan(job['link'],password)
                        f=next((f for f in rows if str(f['fid'])==str(job['file']['fid'])),None)
                        if f is None or int(f['size'])!=int(job['file']['size']):raise ProbeError('分享文件已移除或大小改变，断点保留。')
                    if stop.is_set():raise Paused('队列已暂停。')
                    dl=downloader_factory(api,job['workers'])
                    dl.library_root=job.get('library_root',job['destination'])
                    dl.allow_suspect=job.get('allow_suspect',False)
                    try:path=dl.download(f,job['destination'])
                    except ChecksumMismatch:
                        if not getattr(dl,'active_target',None):raise
                        # Fully received data remains explicitly unverified, but usable
                        # with its original extension instead of a .part suffix.
                        path=export_unverified(f,job['destination'],job['sid'],getattr(dl,'active_target',None),job.get('library_root'))
                        dl.result_kind='existing_unverified'
                    from collect_files import collect
                    path=collect(path,job.get('library_root') or job['destination'])
                    kind=getattr(dl,'result_kind','downloaded')
                    status='已存在，已跳过' if kind=='existing' else ('已保存（校验待确认）' if kind=='existing_unverified' else '已完成')
                    with self.lock:job.update(status=status,path=str(path),error='',downloaded=int(f['size']),finished_at=time.time());self.save()
                except Paused:
                    with self.lock:job['status']='已暂停';self.checkpoint_bytes(job,dl);self.save()
                    break
                except Exception as e:
                    text=str(e) if isinstance(e,ProbeError) else failure_detail(e)
                    if isinstance(e,PermissionError):text='本地目录写入被拒绝，请选择有写入权限的下载目录后重试。断点保留。'
                    elif isinstance(e,OSError) and getattr(e,'errno',None)==28:text='本地磁盘空间不足，请腾出空间或更换下载目录后重试。断点保留。'
                    with self.lock:
                        self.checkpoint_bytes(job,dl)
                        job.update(status='疑似重复，待选择' if isinstance(e,PotentialDuplicate) else ('校验待确认' if isinstance(e,ChecksumMismatch) else '失败，可重试'),error=text)
                        if isinstance(e,PotentialDuplicate):job['candidate_path']=e.path
                        self.save()
                        # Reparse on authentication / link errors, keeping ordinary network retries cheap.
                        if any(code in text for code in ('HTTP 401','HTTP 403','code=23018')):self.live.pop(job['id'],None)
                    event('failed',copy.deepcopy(job))
                finally:event('finished',copy.deepcopy(job))
        finally:
            with self.lock:self.running=False;self.active_id=None
