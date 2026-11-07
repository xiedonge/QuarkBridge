import os,sys
from pathlib import Path
if not getattr(sys,'frozen',False):sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'shared'))
import certifi
os.environ['SSL_CERT_FILE']=certifi.where()

def self_test(output):
    import json,tempfile,tkinter as tk
    import codecs
    for name in ('utf-8','utf-8-sig','utf-16','utf-16-le','utf-16-be','utf-32','utf-32-le','utf-32-be','gb18030','gbk','big5','idna','latin-1','ascii'):
        codecs.lookup(name)
    from frozen_contract import check_error_recovery
    check_error_recovery()
    from login_bridge import capture_login
    assert '__puus=smoke-only' in capture_login(smoke=True,timeout=60)
    from pathlib import Path
    from urllib.request import urlopen
    from direct_app import App
    from local_state import Store
    from direct_download import file_lock
    with tempfile.TemporaryDirectory() as folder:
        if sys.platform=='darwin':
            from keyring.backends.macOS import Keyring
            class Ephemeral:
                def get_password(self,*args):return None
            store=Store(folder,backend=Ephemeral())
        else:store=Store(folder)
        root=tk.Tk();root.withdraw();app=App(root,store);root.update();app.close()
        with file_lock(Path(folder)/'test.lock'):pass
        assert store.load_tasks() is not None
    with urlopen('https://www.python.org',timeout=30) as response:assert response.status==200
    Path(output).write_text(json.dumps({'frozen':bool(getattr(sys,'frozen',False)),'platform':sys.platform,'tk':True,'state_and_lock':True,'embedded_login_browser':True,'codecs':True,'api_error_recovery':True,'https':True,'native_keychain_access':'user authorization not exercised'}),'utf-8')

if __name__=='__main__':
    if '--login' in sys.argv or '--login-smoke' in sys.argv:
        from login_window import run
        raise SystemExit(run('--login-smoke' in sys.argv))
    elif '--self-test' in sys.argv:self_test(sys.argv[sys.argv.index('--self-test')+1])
    else:
        from direct_app import gui
        gui()
