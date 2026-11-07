"""Private loopback IPC for the isolated login process. Never writes credentials."""
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import time


class LoginError(Exception):
    pass


def send_packet(connection, value):
    data = json.dumps(value).encode('utf-8')
    if len(data) > 65536:
        raise LoginError('登录数据过大，已停止。')
    connection.sendall(struct.pack('!I', len(data)) + data)


def receive_packet(connection, cancelled, deadline):
    def read_exact(count):
        data = bytearray()
        while len(data) < count:
            if cancelled() or time.monotonic() > deadline:
                raise LoginError('登录已取消或超时，原登录和任务保留。')
            try:
                part = connection.recv(count-len(data))
            except socket.timeout:
                continue
            if not part:
                raise LoginError('登录窗口已关闭，原登录和任务保留。')
            data.extend(part)
        return bytes(data)
    size = struct.unpack('!I', read_exact(4))[0]
    if size > 65536:
        raise LoginError('登录响应无效。')
    value = json.loads(read_exact(size))
    if not isinstance(value, dict):
        raise LoginError('登录响应无效。')
    return value


def capture_login(cancelled=lambda: False, smoke=False, timeout=900):
    token = secrets.token_hex(32)
    deadline = time.monotonic()+timeout
    process = None
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen(4)
        server.settimeout(.25)
        env = os.environ.copy()
        env['QUARKBRIDGE_LOGIN_CHANNEL'] = json.dumps([server.getsockname()[1], token])
        command = [sys.executable]
        if not getattr(sys, 'frozen', False):
            command.append(str(Path(__file__).with_name('login_window.py')))
        command.append('--login-smoke' if smoke else '--login')
        try:
            process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            while not cancelled() and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise LoginError('登录窗口未完成登录；若窗口未打开，请重新安装完整测试包。')
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection:
                    connection.settimeout(.25)
                    try:
                        hello = receive_packet(connection, cancelled, min(deadline,time.monotonic()+3))
                    except (LoginError, ValueError, OSError):
                        continue
                    supplied = hello.get('token')
                    if not isinstance(supplied,str) or not hmac.compare_digest(supplied,token):
                        continue
                    send_packet(connection, {'ready':True})
                    result = receive_packet(connection,cancelled,deadline)
                    cookie = result.get('cookie')
                    if not isinstance(cookie,str) or not cookie or any(c in cookie for c in '\r\n'):
                        raise LoginError('登录未完成，原登录和任务保留。')
                    return cookie
            raise LoginError('登录已取消或超时，原登录和任务保留。')
        except LoginError:
            raise
        except Exception:
            raise LoginError('无法完成本机登录，请关闭登录窗口后重试；原配置保留。') from None
        finally:
            if process is not None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:process.kill();process.wait()


class CookieCollector:
    """Only cookies applicable to the official drive-pc member endpoint."""
    def __init__(self):
        self.cookies = {}

    def update(self, name, value, domain, path='/', expired=False, remove=False):
        host = 'drive-pc.quark.cn'
        normalized = domain.lower().lstrip('.')
        # Host-only pan.quark.cn cookies must not be sent to another host.
        valid_domain = normalized == host or (domain.startswith('.') and normalized=='quark.cn')
        request_path = '/1/clouddrive/member'
        valid_path = request_path==path or (request_path.startswith(path) and
                     (path.endswith('/') or request_path[len(path):].startswith('/')))
        if not valid_domain or not valid_path or not name or any(c in name+value for c in '\r\n;'):
            return
        if not name.isascii() or not value.isascii() or '=' in name:
            return
        key = (domain,path,name)
        if expired or remove:self.cookies.pop(key,None)
        else:self.cookies[key] = value

    def header(self):
        rows = sorted(self.cookies.items(),key=lambda row:-len(row[0][1]))
        return '; '.join(f'{key[2]}={value}' for key,value in rows)

    def ready(self):
        names = {key[2] for key in self.cookies}
        return {'__pus','__puus'}.issubset(names)
