import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from local_state import Store,StateError

class FakeKeychain:
    def __init__(self):self.data={}
    def get_password(self,service,name):return self.data.get((service,name))
    def set_password(self,service,name,value):self.data[service,name]=value
    def delete_password(self,service,name):del self.data[service,name]

class MacStateTests(unittest.TestCase):
    def test_credentials_roundtrip_and_forget(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=FakeKeychain();store=Store(folder,backend)
            store.save_session('synthetic-cookie','1234');store.save_queue_secrets({'job':'5678'})
            self.assertEqual(store.load_session()['cookie'],'synthetic-cookie')
            self.assertEqual(store.load_queue_secrets(),{'job':'5678'})
            self.assertEqual(list(Path(folder).iterdir()),[])
            store.forget();self.assertEqual(store.load_session(),{});self.assertEqual(store.load_queue_secrets(),{})
    def test_store_namespace_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=FakeKeychain();a=Store(Path(folder)/'a',backend);b=Store(Path(folder)/'b',backend)
            a.save_session('secret','');self.assertEqual(b.load_session(),{})
    def test_keychain_denial_never_falls_back_to_plaintext(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=Mock();backend.set_password.side_effect=RuntimeError('private diagnostic')
            store=Store(folder,backend)
            with self.assertRaises(StateError) as error:store.save_session('secret','code')
            self.assertNotIn('private diagnostic',str(error.exception));self.assertEqual(list(Path(folder).iterdir()),[])
    def test_noncredential_state_still_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder,FakeKeychain());store.save_tasks({'destination':'/Volumes/Media/Downloads'})
            store.save_queue([{'id':'test','status':'已暂停'}])
            self.assertEqual(store.load_tasks()['destination'],'/Volumes/Media/Downloads')
            self.assertEqual(store.load_queue()[0]['status'],'已暂停')
    def test_corrupt_keychain_data_is_actionable(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=FakeKeychain();store=Store(folder,backend);backend.set_password(store.service,'session','bad json')
            with self.assertRaises(StateError):store.load_session()

if __name__=='__main__':unittest.main()
