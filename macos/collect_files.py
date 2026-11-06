"""Move a finished download and its record into the chosen download root."""
import hashlib,json
from pathlib import Path
from direct_download import atomic
from download_catalog import stamp

def collect(path,root):
    path=Path(path).resolve();root=Path(root).resolve()
    if path.parent==root:return path
    if not path.is_relative_to(root):return path
    record=None;state=None
    for candidate in path.parent.glob('*.quark.json'):
        try:
            data=json.loads(candidate.read_text('utf-8'))
            original=candidate.with_name(candidate.name[:-11])
            if original==path or data.get('exported_path')==str(path):record=candidate;state=data;break
        except (OSError,ValueError):continue
    target=root/path.name
    if target.exists() or target.with_name(target.name+'.quark.json').exists():
        suffix=hashlib.sha256(str(path).encode()).hexdigest()[:10]
        target=root/(path.stem+'_'+suffix+path.suffix)
    if target.exists():raise FileExistsError('集中保存位置已有同名文件，保留原文件。')
    new_record=target.with_name(target.name+'.quark.json')
    if new_record.exists():raise FileExistsError('集中保存位置已有记录，保留原文件。')
    path.rename(target)
    try:
        if state:
            state['file_stat']=stamp(target)
            if state.get('phase') in ('export_ready','exported_unverified'):state['exported_path']=str(target)
            atomic(new_record,state)
    except Exception:
        target.rename(path);raise
    if record:record.unlink()
    return target

def organize_saved(store):
    from direct_download import export_unverified
    jobs=store.load_queue();changed=False
    for job in jobs:
        try:
            root=job.get('library_root') or job['destination']
            if job['status']=='校验待确认':
                path=export_unverified(job['file'],job['destination'],job['sid'],job.get('local_target'),root)
                job.update(status='已保存（校验待确认）',path=str(path),error='');changed=True
            if job['status'].startswith(('已完成','已保存','已存在','已跳过')) and job.get('path') and Path(job['path']).is_file():
                path=collect(job['path'],root)
                if str(path)!=job['path']:job['path']=str(path);changed=True
        except Exception as error:
            # Leave original content intact and make a failed organization visible.
            job['error']='整理文件未完成（'+type(error).__name__+'），原数据保留。';changed=True
    if changed:store.save_queue(jobs)
