"""Official Quark login in an ephemeral Chromium profile, isolated from Tk."""
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

if not getattr(sys,'frozen',False):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/('macos' if sys.platform=='darwin' else 'windows')))
from login_bridge import CookieCollector, send_packet, receive_packet


def run(smoke=False):
    port,token = json.loads(os.environ.pop('QUARKBRIDGE_LOGIN_CHANNEL'))
    connection = socket.create_connection(('127.0.0.1',port),timeout=5)
    send_packet(connection,{'token':token})
    if not receive_packet(connection,lambda:False,time.monotonic()+5).get('ready'):return 1
    from PySide6.QtCore import QTimer,QUrl,QObject,Signal
    from PySide6.QtNetwork import QNetworkCookie
    from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel,QPushButton
    from PySide6.QtWebEngineCore import QWebEngineProfile,QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView

    app = QApplication([])
    app.setApplicationName('QuarkBridge Login')
    collector = CookieCollector()
    class Events(QObject):
        validated = Signal(str,str)
    events = Events()
    class Page(QWebEnginePage):
        def acceptNavigationRequest(self,url,kind,is_main):
            return url.scheme() in ('https','about','data')
        def javaScriptConsoleMessage(self,*args):pass
    class Window(QWidget):
        def closeEvent(self,event):
            timer.stop()
            connection.close()
            event.accept()
    window = Window();window.setWindowTitle('登录夸克 · QuarkBridge 0.12.0-rc.1');window.resize(1000,760)
    layout = QVBoxLayout(window)
    hint = QLabel('请在夸克官方网站扫码或登录。成功后自动连接下载器；关闭窗口可取消。')
    hint.setWordWrap(True);layout.addWidget(hint)
    address = QLabel('官方网站：https://pan.quark.cn');address.setWordWrap(True);layout.addWidget(address)
    profile = QWebEngineProfile(window)  # No storage name: off-the-record.
    assert profile.isOffTheRecord()
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
    profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
    profile.downloadRequested.connect(lambda download:download.cancel())
    view=QWebEngineView(window);page=Page(profile,view);view.setPage(page);layout.addWidget(view)
    view.urlChanged.connect(lambda url:address.setText('当前网站：'+url.scheme()+'://'+url.host()))
    button=QPushButton('我已登录，重新检查');layout.addWidget(button)
    state={'busy':False,'last':0,'rendered':False,'finished':False}
    def cookie_change(cookie,remove=False):
        expiry=cookie.expirationDate()
        collector.update(bytes(cookie.name()).decode('ascii',errors='replace'),
                         bytes(cookie.value()).decode('ascii',errors='replace'),
                         cookie.domain(),cookie.path() or '/',
                         not cookie.isSessionCookie() and expiry.toSecsSinceEpoch()<=time.time(),remove)
    profile.cookieStore().cookieAdded.connect(cookie_change)
    profile.cookieStore().cookieRemoved.connect(lambda cookie:cookie_change(cookie,True))
    def finish(cookie):
        if state['finished']:return
        state['finished']=True
        send_packet(connection,{'cookie':cookie})
        window.close();app.quit()
    def validated(original,cookie):
        state['busy']=False
        if original!=collector.header():return
        if cookie:finish(cookie)
        else:hint.setText('已检测到登录信息，但账号校验尚未通过。请完成登录或稍后点“重新检查”；原登录不变。')
    events.validated.connect(validated)
    def check(force=False):
        if state['finished'] or not collector.ready():return
        if smoke:
            if state['rendered']:finish(collector.header())
            return
        if state['busy'] or (not force and time.monotonic()-state['last']<10):return
        state['busy']=True;state['last']=time.monotonic();candidate=collector.header()
        hint.setText('已检测到 Cookie，正在向夸克官方接口确认登录…')
        def validate():
            result=''
            try:
                from verify_direct import Probe
                p=Probe(candidate,emit=lambda message:None)
                member=p.call('member',fetch_subscribe='true',fetch_identity='true')
                if isinstance(member,dict) and 'total_capacity' in member and 'use_capacity' in member:result=p.cookie
            except Exception:pass
            events.validated.emit(candidate,result)
        threading.Thread(target=validate,daemon=True).start()
    button.clicked.connect(lambda:check(True))
    timer=QTimer(window);timer.timeout.connect(check);timer.start(2000)
    QTimer.singleShot(900000,window.close)
    if smoke:
        def loaded(ok):
            if not ok:return
            def rendered(result):state['rendered']=bool(result)
            page.runJavaScript('document.getElementById("ready").textContent === "ready"',rendered)
            for name in ('__pus','__puus'):
                cookie=QNetworkCookie(name.encode(),b'smoke-only')
                cookie.setDomain('.quark.cn');cookie.setPath('/');cookie.setSecure(True);cookie.setHttpOnly(True)
                profile.cookieStore().setCookie(cookie,QUrl('https://drive-pc.quark.cn'))
        view.loadFinished.connect(loaded)
        view.setHtml('<p id="ready">ready</p>',QUrl('https://drive-pc.quark.cn'))
        QTimer.singleShot(45000,window.close)
    else:view.load(QUrl('https://pan.quark.cn'))
    window.show()
    result=app.exec()
    connection.close()
    return result


if __name__=='__main__':
    raise SystemExit(run('--login-smoke' in sys.argv))
