"""Share file filtering and explicit selection (no network or UI dependencies)."""
import re
from pathlib import PurePosixPath

QUALITIES = ('原画质不限', '1080p', '4K', '未知')
VIDEO_EXTENSIONS = set('.mp4 .mkv .mov .avi .webm .m4v .wmv .ts .m2ts .flv .mpg .mpeg .rmvb .3gp'.split())
MARKER = re.compile(r'(?<![a-z0-9])(1080[pi]?|4k|2160p|uhd|720p|1440p|8k)(?![a-z0-9])', re.I)


def is_video(file):
    mime = str(file.get('mime_type') or file.get('format_type') or '').lower()
    return mime.startswith('video/') or PurePosixPath(file['display']).suffix.lower() in VIDEO_EXTENSIONS


def quality(file):
    if not is_video(file):
        return '未知', '非视频'
    for key in ('video_info', 'media_info'):
        data = file.get(key)
        if not isinstance(data, dict):
            continue
        try:
            width, height = sorted((int(data['width']), int(data['height'])), reverse=True)
        except (KeyError, TypeError, ValueError):
            continue
        if width > 0 and height > 0:
            label = '4K' if width >= 3840 and height >= 2160 else ('1080p' if height == 1080 else f'{height}p')
            return label, '媒体尺寸'
    name = PurePosixPath(file['display'])
    for text, source in ((name.stem, '文件名标记'), (str(name.parent), '目录标记')):
        labels = {('4K' if x in ('4k','2160p','uhd') else '1080p' if x in ('1080','1080p') else x)
                  for x in (mark.lower() for mark in MARKER.findall(text))}
        if labels:
            return (labels.pop(), source) if len(labels) == 1 else ('未知', source+'冲突')
    return '未知', '无画质信息'


class ShareSelection:
    def __init__(self):
        self.files = {}
        self.checked = set()
        self.visible = []

    def load(self, rows, keep=False):
        self.files = {str(file['fid']): file for file in rows}
        self.checked.intersection_update(self.files if keep else ())
        self.visible = []

    def filter(self, search, videos, resolution):
        search = search.strip().casefold()
        self.visible = [key for key, file in self.files.items()
                        if search in file['display'].casefold()
                        and (not videos or is_video(file))
                        and (resolution == QUALITIES[0] or (is_video(file) and quality(file)[0] == resolution))]
        self.checked.intersection_update(self.visible)

    def toggle(self, key):
        if key in self.visible:
            self.checked.symmetric_difference_update({key})

    def selected(self):
        return [self.files[key] for key in self.visible if key in self.checked]
