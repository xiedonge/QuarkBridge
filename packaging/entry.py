import os,sys
import certifi
os.environ['SSL_CERT_FILE']=certifi.where()

def self_test(output):
    import json,tempfile,tkinter as tk
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
    with urlopen('https://api.github.com',timeout=30) as response:assert response.status==200
    Path(output).write_text(json.dumps({'frozen':bool(getattr(sys,'frozen',False)),'platform':sys.platform,'tk':True,'state_and_lock':True,'https':True,'native_keychain_access':'user authorization not exercised'}),'utf-8')

if __name__=='__main__':
    if '--self-test' in sys.argv:self_test(sys.argv[sys.argv.index('--self-test')+1])
    else:
        from direct_app import gui
        gui()
