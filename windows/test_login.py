import json,socket,struct,threading,time,unittest
from types import SimpleNamespace
from login_bridge import CookieCollector,LoginError,receive_packet,send_packet
from direct_app import App

class Value:
    def __init__(self,value):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value

class LoginTests(unittest.TestCase):
    def test_only_destination_cookies_are_captured(self):
        c=CookieCollector()
        for domain in ('.evil.quark.cn','quark.cn','pan.quark.cn','.quark.cn.evil.test'):
            c.update('bad','secret',domain)
        c.update('__pus','one','.quark.cn');c.update('__puus','two','.quark.cn')
        self.assertTrue(c.ready());self.assertNotIn('bad',c.header())
        self.assertIn('__puus=two',c.header())
    def test_path_scope_deletion_and_expiry(self):
        c=CookieCollector();c.update('bad','x','.quark.cn','/1/cloud')
        c.update('ok','x','.quark.cn','/1/clouddrive')
        self.assertEqual(c.header(),'ok=x')
        c.update('ok','x','.quark.cn','/1/clouddrive',remove=True);self.assertEqual(c.header(),'')
        c.update('expired','x','.quark.cn',expired=True);self.assertEqual(c.header(),'')
    def test_header_injection_rejected(self):
        c=CookieCollector()
        for name,value in [('bad','x; __puus=evil'),('bad','x\r\n'),('a=b','x')]:c.update(name,value,'.quark.cn')
        self.assertEqual(c.header(),'')
    def test_private_protocol_round_trip(self):
        a,b=socket.socketpair()
        with a,b:
            b.settimeout(.1);send_packet(a,{'cookie':'fixture'})
            self.assertEqual(receive_packet(b,lambda:False,time.monotonic()+2),{'cookie':'fixture'})
    def test_protocol_rejects_large_packet_and_cancel(self):
        a,b=socket.socketpair()
        with a,b:
            b.settimeout(.1);a.sendall(struct.pack('!I',65537))
            with self.assertRaises(LoginError):receive_packet(b,lambda:False,time.monotonic()+2)
            with self.assertRaises(LoginError):receive_packet(b,lambda:True,time.monotonic()+2)
    def app(self):
        a=SimpleNamespace(login_active=True,closing=False,login_cancel=threading.Event(),
                          cookie=Value('old'),queue_cookie='old',api='old',snapshot='old',
                          login_status=Value(''),status=Value(''),error_detail=Value(''),
                          download_queue=SimpleNamespace(lock=threading.RLock(),live={'task':'cached'},jobs=['preserve']),
                          saves=[],set_busy=lambda value:None)
        a.auth_values=lambda:a.saves.append(a.cookie.get())
        return a
    def test_cancel_preserves_credentials_and_tasks(self):
        a=self.app();App.finish_login(a,'','cancelled')
        self.assertEqual(a.cookie.get(),'old');self.assertEqual(a.saves,[])
        self.assertEqual(a.download_queue.jobs,['preserve'])
    def test_success_updates_session_but_preserves_queue(self):
        a=self.app();App.finish_login(a,'new','')
        self.assertEqual(a.cookie.get(),'new');self.assertEqual(a.saves,['new'])
        self.assertEqual(a.download_queue.live,{})
        self.assertEqual(a.download_queue.jobs,['preserve'])
    def test_closing_ignores_late_login_result(self):
        a=self.app();a.closing=True;App.finish_login(a,'new','')
        self.assertEqual(a.cookie.get(),'old');self.assertEqual(a.saves,[])
