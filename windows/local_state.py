"""App history and Windows-user-bound encrypted session, no credential logging."""
import sys
import ctypes
import json
import os
import threading
from pathlib import Path

class StateError(Exception):pass

def crypt(data,decrypt=False):
    if os.name!='nt':raise StateError('记住登录需要 Windows；可关闭记住登录，仅本次使用。')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_byte))]
    buf=ctypes.create_string_buffer(data)
    src=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_byte)));dst=Blob()
    dll=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    fn.restype=wintypes.BOOL
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not fn(ctypes.byref(src),None,None,None,None,1,ctypes.byref(dst)):raise StateError('Windows 登录信息加密/解密失败，请重新填写 Cookie。')
    try:return ctypes.string_at(dst.data,dst.size)
    finally:kernel.LocalFree(ctypes.cast(dst.data,ctypes.c_void_p))

class Store:
    def __init__(self,directory=None):
        self.directory=Path(directory) if directory else (Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent)/'data'
        self.lock=threading.RLock()
    def write(self,name,data):
        with self.lock:
            self.directory.mkdir(parents=True,exist_ok=True)
            path=self.directory/name;tmp=path.with_suffix(path.suffix+'.tmp')
            with tmp.open('wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
            os.replace(tmp,path)
    def load_tasks(self):
        path=self.directory/'tasks.json'
        if not path.exists():return None
        try:return json.loads(path.read_text('utf-8'))
        except (ValueError,OSError):raise StateError('上次任务列表无法读取，下载断点文件仍保留。') from None
    def save_tasks(self,data):self.write('tasks.json',json.dumps(data,ensure_ascii=False,indent=2).encode('utf-8'))
    def load_session(self):
        path=self.directory/'session.bin'
        if not path.exists():return {}
        try:return json.loads(crypt(path.read_bytes(),True))
        except (ValueError,OSError):raise StateError('无法恢复本机登录，请重新填写 Cookie。') from None
    def save_session(self,cookie,password):
        self.write('session.bin',crypt(json.dumps({'cookie':cookie,'password':password}).encode('utf-8')))
    def load_queue(self):
        path=self.directory/'queue.json'
        if not path.exists():return []
        try:return json.loads(path.read_text('utf-8'))['jobs']
        except (ValueError,OSError,KeyError):raise StateError('下载队列无法读取，原文件和断点仍保留。') from None
    def save_queue(self,jobs):self.write('queue.json',json.dumps({'version':1,'jobs':jobs},ensure_ascii=False,indent=2).encode('utf-8'))
    def load_queue_secrets(self):
        path=self.directory/'queue-secrets.bin'
        if not path.exists():return {}
        try:return json.loads(crypt(path.read_bytes(),True))
        except (ValueError,OSError):raise StateError('队列提取码无法恢复，请重新添加对应分享。') from None
    def save_queue_secrets(self,passwords):self.write('queue-secrets.bin',crypt(json.dumps(passwords).encode('utf-8')))
    def forget(self):
        with self.lock:
            (self.directory/'session.bin').unlink(missing_ok=True)
            (self.directory/'queue-secrets.bin').unlink(missing_ok=True)
