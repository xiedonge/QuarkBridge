import unittest
from login_window import login_window_size
from share_selection import ShareSelection,quality,QUALITIES


def fixture(fid,display,**extra):
    return dict(fid=fid,size=16,display=display,path_parts=display.split('/'),**extra)


FILES=[
    fixture('fullhd','剧集/第一季/4K 标签.mp4',video_info={'width':1920,'height':1080}),
    fixture('uhd','剧集/4K/第二季/片段.mp4'),
    fixture('unknown','剧集/额外/花絮.mp4'),
    fixture('text','剧集/1080p/说明.txt'),
    fixture('marker','剧集/第一季/预告 1080p.mp4'),
]


class SelectionTests(unittest.TestCase):
    def test_metadata_precedes_name_and_directory_is_labelled(self):
        self.assertEqual(quality(FILES[0]),('1080p','媒体尺寸'))
        self.assertEqual(quality(FILES[1]),('4K','目录标记'))
        self.assertEqual(quality(FILES[4]),('1080p','文件名标记'))
        self.assertEqual(quality(FILES[2])[0],'未知')

    def test_filters_do_not_enqueue_hidden_rows(self):
        picker=ShareSelection()
        picker.load(FILES)
        picker.filter('',False,QUALITIES[0])
        self.assertEqual(picker.selected(),[])
        picker.checked.update(picker.visible)
        picker.filter('第一季',True,'1080p')
        self.assertEqual([f['fid'] for f in picker.selected()],['fullhd','marker'])
        picker.filter('第二季',True,'1080p')
        self.assertEqual(picker.visible,[])
        self.assertEqual(picker.selected(),[])
        picker.filter('',True,'4K')
        self.assertEqual(picker.visible,['uhd'])
        self.assertEqual(picker.selected(),[])

    def test_window_size_clamps_saved_dimensions_to_current_display(self):
        for width,height in ((1366,768),(800,600),(1920,1080),(1280,720),(3440,1440)):
            for saved in ((0,0),(1450,970),(600,500)):
                w,h=login_window_size(width,height,*saved)
                self.assertLessEqual(w,width-32)
                self.assertLessEqual(h,height-48)
                self.assertGreaterEqual(w,320)
                self.assertGreaterEqual(h,260)
        self.assertEqual(login_window_size(1920,1080,600,500),(600,500))
