"""Resumable direct downloader. Cloud operations delegated to read-only Probe."""
import hashlib
import http.client
import json
import os
import re
import shutil
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from collections import deque
from pathlib import Path
from urllib.request import Request,build_opener
from urllib.error import HTTPError
from verify_direct import ProbeError,NoRedirect
from range_transport import RangeOpener

CHUNK=2*1024*1024
class Paused(ProbeError):pass
class ChecksumMismatch(ProbeError):pass

def export_unverified(f,destination,sid,target_override=None,library_root=None):
    """Expose complete received bytes without network traffic or an integrity claim."""
    target=output_path(destination,sid,f)
    if target_override:
        proposed=Path(target_override).resolve()
        if not proposed.is_relative_to(Path(library_root or destination).resolve()):raise ProbeError('已有断点不在下载目录内，已停止。')
        target=proposed
    if not target.with_name(target.name+'.quark.json').exists():
        from download_catalog import Catalog
        match=Catalog(library_root or destination).scan().find(sid,f,f.get('md5'))
        if match and match['kind']=='unverified':return match['path']
        if match and match['kind']=='partial':target=match['target']
    part=target.with_name(target.name+'.quark.part');record=target.with_name(target.name+'.quark.json')
    exported=target.with_name(target.stem+'.校验待确认'+target.suffix)
    with file_lock(target.with_name(target.name+'.quark.lock')):
        if not record.exists():raise ProbeError('找不到断点记录，无法确认数据是否接收完整。')
        state=json.loads(record.read_text('utf-8'));size=int(f['size'])
        identity=hashlib.sha256((sid+'|'+str(f['fid'])+'|'+str(size)).encode()).hexdigest()
        if state.get('identity')!=identity and identity not in state.get('identities',[]):raise ProbeError('断点属于另一文件，已停止。')
        if state.get('phase') in ('export_ready','exported_unverified') and exported.exists() and not part.exists():
            if exported.stat().st_size!=size:raise ProbeError('待确认文件大小发生变化。')
            state['phase']='exported_unverified';atomic(record,state);return exported
        if exported.exists() or target.exists():raise ProbeError('同名文件已存在，已停止以避免覆盖。')
        if not part.exists() or part.stat().st_size!=size:raise ProbeError('数据尚未接收完整，请先恢复下载。')
        offset=0
        for c in state.get('chunks',[]):
            if c['start']!=offset or c['length']<=0:raise ProbeError('断点区间记录异常。')
            offset+=c['length']
        if offset!=size:raise ProbeError('断点尚未完整，不能保存为视频。')
        state.update(phase='export_ready',exported_path=str(exported),export_validation='unverified_user_choice')
        atomic(record,state)
        part.rename(exported)
        state['phase']='exported_unverified';atomic(record,state)
        return exported

def name_safe(name):
    original=str(name)
    value=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',original).rstrip(' .')
    if not value or value in ('.','..'):value='_'
    if value.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:value='_'+value
    if len(value)>120:
        p=Path(value);value=value[:95]+p.suffix[:12]
    if value!=original:
        p=Path(value);value=p.stem+'_'+hashlib.sha256(original.encode()).hexdigest()[:8]+p.suffix
    return value

def output_path(destination,sid,f):
    root=(Path(destination).expanduser().absolute()/('Quark_'+name_safe(sid))).resolve()
    parts=f.get('path_parts') or f['display'].split('/')
    target=root.joinpath(*(name_safe(n) for n in parts)).resolve()
    if not target.is_relative_to(root):raise ProbeError('本地路径超出下载目录。')
    return target

def atomic(path,data):
    tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w',encoding='utf-8') as out:
        json.dump(data,out,ensure_ascii=False,indent=2);out.flush();os.fsync(out.fileno())
    os.replace(tmp,path)

@contextmanager
def file_lock(path):
    with path.open('a+b') as handle:
        handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise ProbeError('另一个窗口正在下载同一文件，请只保留一个下载任务。') from None
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)

class Downloader:
    def __init__(self,api,stop=None,progress=None,emit=print,opener=None,chunk_size=CHUNK,workers=1):
        self.api=api;self.stop=stop or threading.Event();self.progress=progress or (lambda *a:None)
        self.emit=emit;self.opener=opener or RangeOpener();self.chunk=chunk_size
        self.workers=max(1,min(32,int(workers)))
        self.custom_opener=opener
        self.library_root=None;self.allow_suspect=False;self.result_kind='downloaded';self.prefetched_link=None
        self.active_target=None

    def check(self):
        if self.stop.is_set():raise Paused('已暂停。重新点击下载所选文件即可继续；已验证的数据已保留。')

    def download(self,f,destination):
        try:return self._download_file(f,destination)
        finally:
            if self.custom_opener is None:self.opener.close()

    def _download_file(self,f,destination):
        self.check()
        target=output_path(destination,self.api.sid,f);target.parent.mkdir(parents=True,exist_ok=True)
        from download_catalog import Catalog,PotentialDuplicate,identity,stamp
        root=Path(self.library_root or destination).resolve()
        if not target.is_relative_to(root):root=Path(destination).resolve()
        self.emit('正在检查下载目录中的成品和断点…')
        self.progress(0,int(f['size']),'检查本地文件（不消耗下载流量）')
        catalog=Catalog(root,self.check,self.emit).scan()
        expected=f.get('md5')
        try:match=catalog.find(self.api.sid,f,expected,self.allow_suspect)
        except PotentialDuplicate:
            self.prefetched_link=self.api.direct_link(f);expected=self.prefetched_link[2]
            match=catalog.find(self.api.sid,f,expected,self.allow_suspect)
        if not match and not expected and catalog.has_size(int(f['size'])):
            self.prefetched_link=self.api.direct_link(f);expected=self.prefetched_link[2]
            match=catalog.find(self.api.sid,f,expected,self.allow_suspect)
        if match and match['kind'] in ('complete','unverified'):
            with file_lock(match['target'].with_name(match['target'].name+'.quark.lock')):
                if not match['path'].exists() or stamp(match['path'])!=match['stat']:raise ProbeError('匹配的本地文件已移动或改变，请重试。')
                self.result_kind='existing_unverified' if match['kind']=='unverified' else 'existing'
                self.emit(f'已找到本地文件，跳过下载：{match["path"]}')
                self.progress(int(f['size']),int(f['size']),'已存在，已跳过')
                return match['path']
        if match and match['kind']=='partial':target=match['target'];self.emit(f'找到已有断点，继续原文件：{target}（已接收 {match["bytes"]/1024**2:.1f} MiB）')
        if self.allow_suspect and target.exists() and not target.with_name(target.name+'.quark.json').exists():
            target=target.with_name(target.stem+'.'+str(f['fid'])[:8]+'.新下载'+target.suffix)
        self.active_target=target
        with file_lock(target.with_name(target.name+'.quark.lock')):
            if match and match['kind']=='partial':
                statepath=target.with_name(target.name+'.quark.json')
                current=json.loads(statepath.read_text('utf-8'))
                if current.get('identity')!=match['state'].get('identity'):raise ProbeError('断点记录已改变，请重试。')
                current['identities']=list(set(current.get('identities',[])+[identity(self.api.sid,f)]))
                atomic(statepath,current)
            return self._download(f,target)

    def _download(self,f,target):
        size=int(f['size'])
        if size<0:raise ProbeError('文件大小无效。')
        part=target.with_name(target.name+'.quark.part');statepath=target.with_name(target.name+'.quark.json')
        identity=hashlib.sha256((self.api.sid+'|'+str(f['fid'])+'|'+str(size)).encode()).hexdigest()
        state={'identity':identity,'size':size,'chunks':[],'md5':None,'phase':'downloading','cloud_mutation_calls':0,
               'source':{'sid':self.api.sid,'fid':str(f['fid']),'file_name':f.get('file_name') or Path(f['display']).name}}
        if statepath.exists():
            try:state=json.loads(statepath.read_text('utf-8'))
            except (OSError,ValueError):raise ProbeError('恢复记录损坏，请移动对应 .quark.json 和 .quark.part 后重试。') from None
            if state.get('identity')!=identity and identity not in state.get('identities',[]):raise ProbeError('目标路径属于另一文件，请更换下载目录。')
            if state.get('phase') in ('export_ready','exported_unverified'):
                raise ProbeError('现有文件已保存为“校验待确认”视频。请直接打开该视频；不会自动重新下载。')
        elif target.exists() or part.exists():raise ProbeError('同名文件或部分文件已存在，但没有恢复记录；请更换目录，避免覆盖。')
        if target.exists():
            if state.get('phase') in ('ready','complete') and target.stat().st_size==size:
                self.emit('正在校验已完成文件…')
                if self.hash_file(target,'sha256')==state.get('sha256'):
                    state['phase']='complete';atomic(statepath,state);self.progress(size,size,'已完成');return target
            raise ProbeError('已有目标文件与完成记录不一致，请移走后重新下载。')
        if state.get('phase') in ('ready','complete') and not part.exists():
            # Catalog already looked for moved copies. Only a record remains here;
            # an explicit new download can safely start without deleting user data.
            self.emit('原成品已不在下载目录中，按本次下载请求重新接收。')
            state.update(chunks=[],phase='downloading')
            for key in ('sha256','source_audit','file_stat'):state.pop(key,None)
        from download_catalog import stamp
        if part.exists() and state.get('phase')=='checksum_failed' and state.get('part_stat')==stamp(part):
            raise ChecksumMismatch('数据已完整接收，之前的 MD5 校验待确认。不会重新下载或重复计算整份摘要；可保存现有文件使用。')
        offset=0
        if state['chunks']:
            self.emit('正在核对已保存的断点数据…')
            if not part.exists():raise ProbeError('找不到部分文件，请更换目录重新下载。')
            with part.open('rb') as src:
                for index,chunk in enumerate(state['chunks']):
                    self.check()
                    if chunk['start']!=offset:raise ProbeError('恢复记录的区间不连续。')
                    b=src.read(chunk['length'])
                    if len(b)!=chunk['length'] or hashlib.sha256(b).hexdigest()!=chunk['sha256']:
                        self.emit(f'第 {index+1} 段本地数据不完整或损坏，保留此前已确认的断点，从此处继续下载。')
                        state['chunks']=state['chunks'][:index];state.pop('source_audit',None);break
                    offset+=len(b)
        if offset>size:raise ProbeError('断点超出文件长度。')
        if shutil.disk_usage(target.parent).free<size-offset+100*1024**2:raise ProbeError('本地磁盘空间不足。')
        atomic(statepath,state)
        # Only uncommitted tail bytes are discarded after a crash or interruption.
        with part.open('r+b' if part.exists() else 'w+b') as out:
            out.truncate(offset);out.seek(offset)
            link=None
            if self.prefetched_link:
                link,headers,initial_md5=self.prefetched_link
                if initial_md5 and re.fullmatch('[0-9a-fA-F]{32}',initial_md5):
                    if state.get('md5') and state['md5'].lower()!=initial_md5.lower():raise ProbeError('源文件摘要已改变，断点保留，请确认分享文件。')
                    state['md5']=initial_md5
                self.prefetched_link=None
            self.progress(offset,size,'下载中')
            if self.workers>1 and offset<size:
                self.emit(f'使用 {self.workers} 个连接并行下载；现有断点继续使用。')
                self._parallel(f,out,state,statepath,offset,size)
                offset=size
            while offset<size:
                self.check();end=min(size-1,offset+self.chunk-1)
                for attempt in range(4):
                    self.check()
                    try:
                        if link is None:
                            link,headers,md5=self.api.direct_link(f,refresh=attempt>0)
                            if md5 and re.fullmatch(r'[0-9a-fA-F]{32}',md5):
                                if state.get('md5') and state['md5'].lower()!=md5.lower():raise ProbeError('服务器文件 MD5 已改变，停止续传。')
                                state['md5']=md5
                        req=Request(link,headers=dict(headers,Range=f'bytes={offset}-{end}'))
                        with self.opener.open(req,timeout=20) as response:
                            if response.status!=206 or response.headers.get('Content-Range')!=f'bytes {offset}-{end}/{size}':
                                raise ProbeError('下载范围或文件总长度不匹配，停止以避免保存错误内容。')
                            digest=hashlib.sha256();written=0
                            while written<end-offset+1:
                                self.check()
                                b=response.read(min(256*1024,end-offset+1-written))
                                if not b:raise OSError('incomplete response')
                                out.write(b);digest.update(b);written+=len(b)
                                self.progress(offset+written,size,'下载中')
                        out.flush();os.fsync(out.fileno())
                        state['chunks'].append({'start':offset,'length':written,'sha256':digest.hexdigest()})
                        atomic(statepath,state);offset+=written
                        break
                    except HTTPError as e:
                        error=f'HTTP {e.code}'
                    except (OSError,http.client.HTTPException):error='网络中断'
                    except ProbeError:
                        out.seek(offset);out.truncate(offset);raise
                    out.seek(offset);out.truncate(offset);link=None
                    if attempt==3:raise ProbeError(f'下载失败（{error}），断点已保存。重新点击下载可继续。')
                    self.emit(f'{error}，刷新直链重试 {attempt+1}/3…')
                    if self.stop.wait(min(2**attempt,4)):self.check()
        self.check();self.progress(size,size,'正在校验完整文件');self.emit('所有数据已接收，正在校验完整文件…')
        md5=hashlib.md5();sha=hashlib.sha256()
        with part.open('rb') as src:
            for b in iter(lambda:src.read(4*1024**2),b''):
                self.check();md5.update(b);sha.update(b)
        if part.stat().st_size!=size:raise ProbeError('文件长度校验失败。')
        audit=state.get('source_audit',{})
        source_verified=(audit.get('complete') is True and audit.get('sha256')==sha.hexdigest()
                         and audit.get('md5')==state.get('md5'))
        mismatch=bool(state.get('md5') and md5.hexdigest().lower()!=state['md5'].lower())
        if mismatch and not source_verified:
            state.update(phase='checksum_failed',sha256=sha.hexdigest(),computed_md5=md5.hexdigest(),
                         validation='server_md5_mismatch',part_stat=stamp(part))
            atomic(statepath,state)
            raise ChecksumMismatch('文件已完整接收，但与接口 MD5 不一致。数据和校验结果已保留；不要删除断点。可点击“保存现有视频（校验待确认）”直接使用现有数据；全文件网络核对会再次传输整份文件。')
        state.update(phase='ready',sha256=sha.hexdigest(),computed_md5=md5.hexdigest(),
                     server_md5_mismatch=mismatch,
                     validation='length+full_source_comparison+local_sha256' if source_verified else
                     ('length+server_md5' if state.get('md5') else 'length+range+local_sha256'))
        atomic(statepath,state)
        if target.exists():raise ProbeError('完成时发现同名文件，已保留部分文件，请检查。')
        part.rename(target);state['phase']='complete'
        from download_catalog import stamp
        state['file_stat']=stamp(target);atomic(statepath,state)
        self.progress(size,size,'已完成');self.emit(f'下载完成：{target}')
        if mismatch:self.emit('整份文件已与当前源文件逐段核对一致；接口 MD5 仍不一致，已在校验记录中注明。')
        return target

    def repair(self,f,destination):
        """Compare every saved byte against a stable CDN object; repair only differing chunks.

        Stable ETag and per-chunk digests bind resumable audit evidence to the local bytes.
        This reads the whole source again but avoids a second local copy or discarding data.
        """
        target=output_path(destination,self.api.sid,f)
        part=target.with_name(target.name+'.quark.part');record=target.with_name(target.name+'.quark.json')
        if not part.exists() or not record.exists():raise ProbeError('没有可核对的断点文件，请先下载或恢复任务。')
        with file_lock(target.with_name(target.name+'.quark.lock')):
            state=json.loads(record.read_text('utf-8'));size=int(f['size'])
            identity=hashlib.sha256((self.api.sid+'|'+str(f['fid'])+'|'+str(size)).encode()).hexdigest()
            if state.get('identity')!=identity or part.stat().st_size!=size:
                raise ProbeError('此功能用于已接收完整数据的文件；请先恢复未完成的下载。')
            offset=0
            for c in state['chunks']:
                if c['start']!=offset or c['length']<=0:raise ProbeError('断点区间记录无效。')
                offset+=c['length']
            if offset!=size:raise ProbeError('断点尚未完整，请先恢复下载。')
            url,headers,expected=self.api.direct_link(f,refresh=True)
            audit=state.get('source_audit',{})
            if audit.get('md5')!=expected:audit={}
            checked=audit.get('checked',[]);etag=None;repaired=0
            self.emit('正在逐段核对完整源文件；需要再次读取源文件，核对进度会保存，可暂停后继续。')
            with part.open('r+b') as local:
                for i,c in enumerate(state['chunks']):
                    self.check();start=c['start'];end=start+c['length']-1
                    local.seek(start);existing=local.read(c['length']);digest=hashlib.sha256(existing).hexdigest()
                    # Always fetch the first chunk to establish the current object version.
                    if i>0 and etag and i<len(checked) and checked[i]==digest:
                        self.progress(end+1,size,'核对源文件');continue
                    for attempt in range(4):
                        self.check()
                        try:
                            with self.opener.open(Request(url,headers=dict(headers,Range=f'bytes={start}-{end}')),timeout=20) as response:
                                if response.status!=206 or response.headers.get('Content-Range')!=f'bytes {start}-{end}/{size}':
                                    raise ProbeError('源文件区间或大小已改变，已停止核对。')
                                current=response.headers.get('ETag')
                                if not current or current.startswith('W/'):
                                    raise ProbeError('源服务器没有提供稳定文件版本标识，无法确认全文件一致；原数据已保留。')
                                if etag is not None and current!=etag:raise ProbeError('核对期间源文件版本发生变化，请重新核对。')
                                content=bytearray()
                                while len(content)<c['length']:
                                    self.check();b=response.read(min(256*1024,c['length']-len(content)))
                                    if not b:raise OSError('incomplete response')
                                    content.extend(b);self.progress(start+len(content),size,'核对源文件')
                            break
                        except (OSError,http.client.HTTPException):
                            if attempt==3:raise ProbeError('读取源文件失败，核对进度已保存；稍后再次点击核对。') from None
                            if self.stop.wait(min(2**attempt,4)):self.check()
                            url,headers,new_expected=self.api.direct_link(f,refresh=True)
                            if new_expected!=expected:raise ProbeError('源文件摘要发生变化，请重新核对。')
                    if etag is None:
                        etag=current
                        if audit.get('etag')!=etag:checked=[]
                    remote_digest=hashlib.sha256(content).hexdigest()
                    if content!=existing:
                        # Persist invalidated audit before overwriting, so interruption cannot reuse it.
                        checked=checked[:i];state['source_audit']={'md5':expected,'etag':etag,'checked':checked}
                        state['phase']='source_auditing';atomic(record,state)
                        local.seek(start);local.write(content);local.flush();os.fsync(local.fileno());repaired+=1
                        self.emit(f'已修复第 {i+1} 段数据。')
                    c['sha256']=remote_digest
                    if len(checked)>i:checked[i]=remote_digest
                    else:checked.append(remote_digest)
                    audit={'md5':expected,'etag':etag,'checked':checked,'complete':False}
                    state.update(source_audit=audit,phase='source_auditing');atomic(record,state)
            _,_,latest=self.api.direct_link(f,refresh=True)
            if latest!=expected:raise ProbeError('源文件摘要已改变，保留数据，请重新核对。')
            audit.update(complete=True,sha256=self.hash_file(part,'sha256'))
            state.update(source_audit=audit,md5=expected);atomic(record,state)
            self.emit(f'全文件源数据核对通过，本次修复 {repaired} 段；正在完成本地校验。')
            return self._download(f,target)

    def _parallel(self,f,out,state,statepath,initial,size):
        """Bounded parallel reads, ordered durable commits; v0.2 checkpoint compatible."""
        abort=threading.Event();guard=threading.Lock();link_guard=threading.Lock()
        local=threading.local();received={};shared={'generation':0};openers=[];committed=0
        def check():
            self.check()
            if abort.is_set():raise Paused('下载已停止，已提交的断点保留。')
        def get_link(old=None):
            with link_guard:
                check()
                if 'url' not in shared or old==shared['generation']:
                    url,headers,md5=self.api.direct_link(f,refresh='url' in shared)
                    if md5 and re.fullmatch(r'[0-9a-fA-F]{32}',md5):
                        if state.get('md5') and state['md5'].lower()!=md5.lower():raise ProbeError('服务器文件 MD5 已改变，停止续传。')
                        state['md5']=md5
                    shared.update(url=url,headers=headers,generation=shared['generation']+1)
                return shared['url'],dict(shared['headers']),shared['generation']
        get_link()
        def report(start,n):
            with guard:
                received[start]=n
                self.progress(initial+committed+sum(received.values()),size,f'下载中（{self.workers} 连接）')
        def fetch(start,end):
            if not hasattr(local,'opener'):
                local.opener=self.custom_opener or RangeOpener()
                if self.custom_opener is None:
                    with guard:openers.append(local.opener)
            generation=None
            for attempt in range(4):
                check();report(start,0)
                url,headers,generation=get_link(generation if attempt else None)
                try:
                    request=Request(url,headers=dict(headers,Range=f'bytes={start}-{end}'))
                    with local.opener.open(request,timeout=20) as response:
                        if response.status!=206 or response.headers.get('Content-Range')!=f'bytes {start}-{end}/{size}':
                            raise ProbeError('下载范围或文件总长度不匹配，已停止。')
                        content=bytearray()
                        while len(content)<end-start+1:
                            check()
                            block=response.read(min(256*1024,end-start+1-len(content)))
                            if not block:raise OSError('incomplete response')
                            content.extend(block);report(start,len(content))
                    return content,hashlib.sha256(content).hexdigest()
                except HTTPError as e:
                    if e.code in (429,503):
                        raise ProbeError(f'服务器返回 HTTP {e.code}。断点保留；请稍后将连接数降至 1 或 2 再续传。') from None
                    error=f'HTTP {e.code}'
                except (OSError,http.client.HTTPException):error='网络中断'
                if attempt==3:raise ProbeError(f'{error}，重试未成功，已保存断点。')
                self.emit(f'某一分段{error}，刷新直链重试 {attempt+1}/3…')
                if self.stop.wait(min(2**attempt,4)):self.check()
        # Completed later ranges may refill free workers within a bounded lookahead.
        # Only the continuous prefix is committed, preserving every old checkpoint.
        pending={};ready={};next_start=initial;next_commit=initial
        window=max(self.workers,min(self.workers*2,128*1024**2//self.chunk))
        try:
            with ThreadPoolExecutor(max_workers=self.workers,thread_name_prefix='quark-range') as pool:
                def fill():
                    nonlocal next_start
                    while next_start<size and len(pending)<self.workers and len(pending)+len(ready)<window:
                        end=min(size-1,next_start+self.chunk-1)
                        pending[pool.submit(fetch,next_start,end)]=next_start;next_start=end+1
                fill()
                try:
                    while pending or ready:
                        check()
                        if pending:
                            finished,_=wait(pending,timeout=.2,return_when=FIRST_COMPLETED)
                            for future in finished:ready[pending.pop(future)]=future.result()
                            # Do not let Future retain a second copy of completed data.
                            if finished:del future
                            del finished
                        while next_commit in ready:
                            check();content,digest=ready.pop(next_commit)
                            if out.tell()!=next_commit:raise ProbeError('本地写入位置异常，已停止。')
                            out.write(content);out.flush();os.fsync(out.fileno())
                            state['chunks'].append({'start':next_commit,'length':len(content),'sha256':digest})
                            atomic(statepath,state)
                            with guard:committed+=len(content);received.pop(next_commit,None)
                            next_commit+=len(content);del content
                        fill()
                finally:
                    abort.set()
                    for future in pending:future.cancel()
        finally:
            for opener in openers:opener.close()

    def hash_file(self,path,algorithm):
        h=hashlib.new(algorithm)
        with path.open('rb') as src:
            for b in iter(lambda:src.read(4*1024**2),b''):
                self.check();h.update(b)
        return h.hexdigest()
