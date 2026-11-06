"""Presentation rules, independent of Tk and the download engine."""
import re
from urllib.parse import urlsplit, parse_qsl
from verify_direct import parse

FILTERS=('全部','下载中','等待 / 暂停','需要处理','已完成')
ATTENTION=('失败，可重试','校验待确认','疑似重复，待选择')

def share_input(text, password=''):
    """Accept a URL or one ordinary Quark share message; reject ambiguous input."""
    links=re.findall(r'https://pan\.quark\.cn/s/[A-Za-z0-9]+(?:[?#][^\s<>\]）)，。；\"\u201d]*)?',text)
    links=list(dict.fromkeys(link.rstrip('.,;)') for link in links))
    if len(links)!=1:
        raise ValueError('请粘贴一个夸克分享链接或整段分享文案；多个分享请分别添加。')
    link=links[0];parse(link)
    query=dict(parse_qsl(urlsplit(link).query))
    code=re.search(r'(?:提取码|提取密码|访问码)\s*[:：]?\s*([A-Za-z0-9]{4,8})(?![A-Za-z0-9])',text)
    return link,password.strip() or query.get('pwd','') or (code.group(1) if code else '')

def size_text(value):
    value=max(0,int(value))
    for unit in ('B','KiB','MiB','GiB','TiB'):
        if value<1024 or unit=='TiB':return f'{value:.1f} {unit}'
        value/=1024

def category(job):
    state=job['status']
    if state=='下载中':return '下载中'
    if state in ('等待下载','已暂停'):return '等待 / 暂停'
    if state in ATTENTION:return '需要处理'
    return '已完成'

def visible(job,group='全部',search=''):
    return (group=='全部' or category(job)==group) and search.casefold().strip() in job['file']['display'].casefold()

def progress_text(job):
    total=int(job['file']['size']);done=min(total,max(0,int(job.get('downloaded',0))))
    if category(job)=='已完成':return '100%'
    if job['status']=='下载中' and '检查本地' in job.get('phase',''):return '查重中'
    if job['status']=='校验待确认':return '已收齐'
    return f'{100*done/max(total,1):.1f}% · {size_text(done)}'

def next_step(job):
    status=job['status'];error=job.get('error','')
    if status=='校验待确认':return '数据已收齐。点“保存现有文件”，无需重新下载；文件将标记为校验待确认。'
    if status=='疑似重复，待选择':return '发现可能相同的本地文件。选择“使用已有文件”或“仍然下载”。'
    if status=='失败，可重试':
        if '401' in error or '登录' in error or 'Cookie' in error:return '检查分享是否有效；到“设置”更新登录，再点“重试所选”。'
        if '429' in error or '503' in error:return '稍后重试；可先暂停，在“设置”将连接数降低到 1 或 2。'
        if '空间' in error:return '在此任务的下载磁盘腾出空间，再点“重试所选”。'
        if '权限' in error or '写入被拒绝' in error:return '检查此任务的目录权限或文件占用，再点“重试所选”。'
        return '点“重试所选”继续；已有可靠断点会复用。'
    if status=='已暂停':return '点“开始 / 继续”从保存的断点接续。'
    if status=='等待下载':return '按列表顺序下载；正在下载时仍可添加下一份文件。'
    if status=='下载中':return '正在处理当前文件；关闭窗口会先保存断点，再自动退出。'
    return '文件已在本地；点“打开所在文件夹”查看。'
