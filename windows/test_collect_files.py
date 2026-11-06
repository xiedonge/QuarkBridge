import tempfile,threading,unittest
from pathlib import Path
from direct_download import Downloader
from download_queue import DownloadQueue
from local_state import Store
from test_direct_download import API,Network,DATA
from collect_files import collect

class CollectionTests(unittest.TestCase):
    def test_complete_mismatch_exports_video_and_reuses_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);q=DownloadQueue(Store(root/'settings'));api=API('0'*32)
            f={'fid':'one','display':'sub/video.mp4','path_parts':['sub','video.mp4'],'size':len(DATA)}
            q.add(api,[f],'https://pan.quark.cn/s/test','',root)
            q.run(threading.Event(),lambda p:api,lambda a,n:Downloader(a,opener=Network(),chunk_size=262144,emit=lambda s:None),lambda *a:None)
            job=q.snapshot()[0];path=Path(job['path'])
            self.assertEqual(path.parent,root);self.assertEqual(path.suffix,'.mp4');self.assertIn('校验待确认',path.name);self.assertEqual(path.read_bytes(),DATA)
            network=Network();dl=Downloader(api,opener=network,emit=lambda s:None)
            self.assertEqual(dl.download(f,root),path);self.assertEqual(network.requests,[])
    def test_same_names_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'video.mp4').write_bytes(b'old');folder=root/'nested';folder.mkdir();p=folder/'video.mp4';p.write_bytes(b'new')
            target=collect(p,root);self.assertNotEqual(target,root/'video.mp4');self.assertEqual(target.read_bytes(),b'new');self.assertEqual((root/'video.mp4').read_bytes(),b'old')
