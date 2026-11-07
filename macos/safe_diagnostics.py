"""Safe diagnostics: static stack locations only, never locals or exception values."""
import traceback
from pathlib import Path

_ALLOWED = {'download_queue.py','direct_download.py','download_catalog.py','verify_direct.py',
            'range_transport.py','local_state.py','state_base.py','direct_app.py',
            'request.py','client.py','cookiejar.py','codecs.py','__init__.py','socket.py','ssl.py'}

def failure_detail(error):
    frames=[]
    for frame in traceback.extract_tb(error.__traceback__):
        name=Path(frame.filename).name
        if name in _ALLOWED:
            frames.append(f'{name}:{frame.lineno}')
    location=' > '.join(frames[-10:]) or '无可用调用位置'
    return f'操作失败（{type(error).__name__}），断点保留。诊断位置：{location}'
