"""Local recursive catalogue. Hash only plausible candidates, cache unchanged files."""
import hashlib
import json
import os
import re
from pathlib import Path
from verify_direct import ProbeError

class PotentialDuplicate(ProbeError):
    def __init__(self,path):
        self.path=str(path)
        super().__init__('找到同名同大小的本地文件，但无法确认内容相同。已停止此任务，未重新下载；可在队列中选择“使用已有文件”或“仍然下载”。')

def stamp(path):
    s=path.stat();return {'size':s.st_size,'mtime_ns':s.st_mtime_ns,'ctime_ns':s.st_ctime_ns}

def identity(sid,f):return hashlib.sha256((sid+'|'+str(f['fid'])+'|'+str(int(f['size']))).encode()).hexdigest()

class Catalog:
    def __init__(self,root,check=lambda:None,emit=lambda s:None):
        self.root=Path(root).resolve();self.check=check;self.emit=emit;self.records=[];self.files=[]
        self.cache_path=self.root/'.quarkbridge-hashes.json';self.cache={}
        try:self.cache=json.loads(self.cache_path.read_text('utf-8'))
        except (OSError,ValueError):pass
    def scan(self):
        if not self.root.exists():return self
        unreadable=0
        def failed(_):nonlocal unreadable;unreadable+=1
        for folder,dirs,files in os.walk(self.root,followlinks=False,onerror=failed):
            self.check();base=Path(folder)
            dirs[:]=[d for d in dirs if not (base/d).is_symlink() and not getattr(base/d,'is_junction',lambda:False)()
                     and (base/d).resolve().is_relative_to(self.root)]
            for name in files:
                p=base/name
                if p.is_symlink():continue
                try:
                    if not p.resolve().is_relative_to(self.root):continue
                    if name.endswith('.quark.json'):
                        state=json.loads(p.read_text('utf-8'));target=p.with_name(name[:-11])
                        self.records.append((target,state))
                    elif '.quark.' not in name and not name.startswith('.quarkbridge-'):
                        self.files.append(p)
                except (OSError,ValueError):unreadable+=1
        if unreadable:self.emit(f'本地查重有 {unreadable} 项无法读取，其余目录已检查。')
        return self
    def hash(self,path,algorithm):
        self.check();key=str(path.relative_to(self.root));current=stamp(path)
        saved=self.cache.get(key,{})
        if saved.get('stat')==current and algorithm in saved:return saved[algorithm]
        self.emit(f'正在本地核对候选文件：{path.name}（不消耗下载流量）')
        h=hashlib.new(algorithm)
        with path.open('rb') as source:
            for block in iter(lambda:source.read(4*1024**2),b''):self.check();h.update(block)
        if stamp(path)!=current:raise OSError('file changed during hash')
        entry=saved if saved.get('stat')==current else {'stat':current}
        entry[algorithm]=h.hexdigest();self.cache[key]=entry
        # Losing this performance cache never changes the decision's correctness.
        try:
            from direct_download import atomic
            atomic(self.cache_path,self.cache)
        except OSError:pass
        return h.hexdigest()
    def has_size(self,size):
        if any(int(s.get('size',-1))==size for _,s in self.records):return True
        for p in self.files:
            try:
                if p.stat().st_size==size:return True
            except OSError:pass
        return False
    def find(self,sid,f,expected=None,allow_suspect=False):
        expected=expected.lower() if isinstance(expected,str) and re.fullmatch('[0-9a-fA-F]{32}',expected) else None
        size=int(f['size']);wanted=identity(sid,f);name=Path(f.get('file_name') or f['display']).name
        candidates=[];suspects=[]
        for target,state in self.records:
            if int(state.get('size',-1))!=size:continue
            same=wanted==state.get('identity') or wanted in state.get('identities',[])
            source=state.get('source',{})
            same_fid=str(source.get('fid',''))==str(f['fid']) and bool(source.get('fid'))
            content_match=bool(expected and state.get('computed_md5')==expected)
            if expected and str(state.get('md5','')).lower()==expected and not state.get('computed_md5') and state.get('phase')=='downloading':content_match=True
            # A mismatched API MD5 is never accepted as a content fingerprint.
            if same_fid and expected and state.get('md5') and expected.lower()!=state['md5'].lower():same_fid=False
            if not (same or same_fid or content_match):continue
            part=target.with_name(target.name+'.quark.part')
            phase=state.get('phase')
            if phase in ('export_ready','exported_unverified'):
                exported=Path(state.get('exported_path',''))
                if not exported.is_absolute():exported=target.with_name(target.stem+'.校验待确认'+target.suffix)
                if exported.resolve().is_relative_to(self.root) and exported.is_file() and exported.stat().st_size==size and (same or same_fid):
                    return {'kind':'unverified','path':exported,'target':target,'state':state,'stat':stamp(exported)}
            if target.is_file() and phase in ('ready','complete') and target.stat().st_size==size:
                proven=state.get('sha256')
                if proven and (state.get('file_stat')==stamp(target) or self.hash(target,'sha256')==proven):
                    return {'kind':'complete','path':target,'target':target,'state':state,'stat':stamp(target)}
            if part.is_file() and phase not in ('export_ready','exported_unverified'):
                chunks=state.get('chunks',[]);offset=0
                for c in chunks:
                    if c.get('start')!=offset or c.get('length',0)<=0:break
                    offset+=c['length']
                else:
                    if offset<=size and part.stat().st_size>=offset:
                        candidates.append({'kind':'partial','path':part,'target':target,'state':state,'bytes':offset})
        if candidates:return max(candidates,key=lambda c:c['bytes'])
        from direct_download import name_safe
        for path in self.files:
            try:
                if path.stat().st_size!=size:continue
                if expected and re.fullmatch('[0-9a-fA-F]{32}',expected) and self.hash(path,'md5').lower()==expected.lower():
                    return {'kind':'complete','path':path,'target':path,'state':None,'stat':stamp(path)}
                if path.name in (name,name_safe(name)):suspects.append(path)
            except OSError:continue
        if suspects and not allow_suspect:raise PotentialDuplicate(suspects[0])
        return None
