"""Official Quark login in an ephemeral Chromium profile, isolated from Tk."""
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

if __name__=='__main__' and not getattr(sys,'frozen',False):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/('macos' if sys.platform=='darwin' else 'windows')))
from login_bridge import CookieCollector, send_packet, receive_packet, validate_login


def login_window_size(width,height,saved_width=0,saved_height=0):
    # Qt reports logical pixels, including on Retina and high-DPI displays.
    available_width=max(1,width-32)
    available_height=max(1,height-48)
    def dimension(saved,available,minimum,preferred):
        return min(available,max(minimum,saved if saved>0 else min(preferred,int(available*.95))))
    return (dimension(saved_width,available_width,320,1200),
            dimension(saved_height,available_height,260,900))


def run(smoke=False):
    port,token = json.loads(os.environ.pop('QUARKBRIDGE_LOGIN_CHANNEL'))
    connection = socket.create_connection(('127.0.0.1',port),timeout=5)
    send_packet(connection,{'token':token})
    if not receive_packet(connection,lambda:False,time.monotonic()+5).get('ready'):return 1
    from PySide6.QtCore import QTimer,QUrl,QObject,Signal,QSettings,QSize
    from PySide6.QtGui import QCursor,QShortcut,QKeySequence
    from PySide6.QtNetwork import QNetworkCookie
    from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton
    from PySide6.QtWebEngineCore import QWebEngineProfile,QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView

    app = QApplication([])
    app.setApplicationName('QuarkBridge Login')
    collector = CookieCollector()
    settings = QSettings('QuarkBridge','LoginWindow')
    class Events(QObject):
        validated = Signal(str,str)
    events = Events()
    class Page(QWebEnginePage):
        def acceptNavigationRequest(self,url,kind,is_main):
            return url.scheme() in ('https','about','data')
        def javaScriptConsoleMessage(self,*args):pass
    class Window(QWidget):
        def closeEvent(self,event):
            if not smoke:
                settings.setValue('size',self.normalGeometry().size() if self.isMaximized() else self.size())
            timer.stop()
            connection.close()
            event.accept()
    window = Window();window.setWindowTitle('登录夸克 · QuarkBridge 0.12.0');window.setMinimumSize(320,260)
    screen=app.screenAt(QCursor.pos()) or app.primaryScreen()
    area=screen.availableGeometry()
    saved=settings.value('size',QSize(),type=QSize) if not smoke else QSize()
    width,height=login_window_size(area.width(),area.height(),saved.width(),saved.height())
    window.resize(width,height)
    window.move(area.x()+(area.width()-width)//2,area.y()+(area.height()-height)//2)
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
    page.newWindowRequested.connect(lambda request:request.openIn(page))
    view.urlChanged.connect(lambda url:address.setText('当前网站：'+url.scheme()+'://'+url.host()))
    toolbar=QHBoxLayout()
    layout.addLayout(toolbar)
    button=QPushButton('我已登录，重新检查')
    toolbar.addWidget(button)
    toolbar.addStretch()
    zoom_label=QLabel()
    def zoom(factor):
        factor=max(.5,min(2.,round(factor,2)))
        view.setZoomFactor(factor)
        zoom_label.setText(f'{factor:.0%}')
    def zoom_out():zoom(view.zoomFactor()-.1)
    def zoom_in():zoom(view.zoomFactor()+.1)
    def zoom_reset():zoom(1.)
    toolbar.addWidget(zoom_label)
    for name,command in (('缩小',zoom_out),('放大',zoom_in),('还原',zoom_reset)):
        zoom_button=QPushButton(name)
        zoom_button.clicked.connect(command)
        toolbar.addWidget(zoom_button)
    for keys,command in (('Ctrl+-',zoom_out),('Ctrl+=',zoom_in),('Ctrl+0',zoom_reset)):
        shortcut=QShortcut(QKeySequence(keys),window)
        shortcut.activated.connect(command)
    zoom(.8 if area.height()<720 else 1.)
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
                result=validate_login(candidate)
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
