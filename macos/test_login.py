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

    def test_keychain_failure_does_not_block_history_restore(self):
        from local_state import StateError
        def denied():raise StateError('fixture permission denied')
        a=SimpleNamespace(store=SimpleNamespace(load_session=denied,load_tasks=lambda:{'destination':'preserved','connections':8,'speed_profile_version':1,'files':[]}),
                          login_status=Value(''),link=Value(''),destination=Value(''),connections=Value(''),status=Value(''),
                          populate=lambda rows:None,log_message=lambda value:None)
        App.restore(a)
        self.assertEqual(a.destination.get(),'preserved')
        self.assertEqual(a.connections.get(),'8')
    def test_do_not_remember_never_saves_session(self):
        a=self.app();a.remember=Value(False);a.password=Value('')
        a.values=lambda:('https://pan.quark.cn/s/example','','new')
        a.store=SimpleNamespace(save_session=lambda *args:self.fail('must not save plaintext or encrypted session'))
        App.auth_values(a)
        self.assertFalse(a.auth_enabled);self.assertEqual(a.queue_cookie,'new')

    def test_login_validation_returns_rotated_cookie_only_for_valid_member(self):
        from login_bridge import validate_login
        calls=[]
        def factory(cookie,emit):
            def call(path,**query):calls.append(path);return {'total_capacity':10,'use_capacity':20}
            return SimpleNamespace(cookie='rotated',call=call)
        self.assertEqual(validate_login('candidate',factory),'rotated')
        self.assertEqual(calls,['member'])
        for value in (None,{}, {'total_capacity':0,'use_capacity':0},{'total_capacity':None,'use_capacity':1}):
            with self.assertRaises(LoginError):
                validate_login('candidate',lambda *args,**kwargs:SimpleNamespace(cookie='candidate',call=lambda *args,**kwargs:value))
    def test_empty_session_cookie_does_not_trigger_capture(self):
        c=CookieCollector();c.update('__pus','','.quark.cn');c.update('__puus','x','.quark.cn')
        self.assertFalse(c.ready())
