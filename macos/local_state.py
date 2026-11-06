"""macOS credentials use the native Keychain, never plaintext files."""
import hashlib,json,os
from pathlib import Path
from state_base import Store as BaseStore,StateError

class Store(BaseStore):
    def __init__(self,directory=None,backend=None):
        super().__init__(directory or Path.home()/'Library'/'Application Support'/'QuarkBridge'/'data')
        self.backend=backend
        self.service='QuarkBridge.'+hashlib.sha256(str(self.directory.resolve()).encode()).hexdigest()[:20]
    def keychain(self):
        if self.backend is None:
            try:
                from keyring.backends.macOS import Keyring
                self.backend=Keyring()
            except Exception:raise StateError('无法访问 macOS 钥匙串；请检查钥匙串访问权限，或仅本次使用登录。') from None
        return self.backend
    def read_secret(self,name):
        try:
            value=self.keychain().get_password(self.service,name)
            data=json.loads(value) if value else {}
            if not isinstance(data,dict):raise ValueError()
            return data
        except Exception:raise StateError('钥匙串读取失败或访问未获允许，请重新授权或仅本次使用登录。') from None
    def save_secret(self,name,data):
        try:self.keychain().set_password(self.service,name,json.dumps(data))
        except Exception:raise StateError('无法保存到钥匙串，本次登录仅保留在内存。') from None
    def write(self,name,data):
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(self.directory,0o700)
        super().write(name,data)
        os.chmod(self.directory/name,0o600)
    def load_session(self):return self.read_secret('session')
    def save_session(self,cookie,password):self.save_secret('session',{'cookie':cookie,'password':password})
    def load_queue_secrets(self):return self.read_secret('queue-passwords')
    def save_queue_secrets(self,passwords):self.save_secret('queue-passwords',passwords)
    def forget(self):
        with self.lock:
            try:
                backend=self.keychain()
                for name in ('session','queue-passwords'):
                    if backend.get_password(self.service,name) is not None:backend.delete_password(self.service,name)
            except Exception:raise StateError('钥匙串凭据删除失败，请在“钥匙串访问”中检查 QuarkBridge 项目。') from None
