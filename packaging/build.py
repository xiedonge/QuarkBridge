import os,sys,subprocess,shutil,json
from pathlib import Path
from importlib.metadata import distribution
licenses=Path('build-licenses');licenses.mkdir(exist_ok=True)
shutil.copy2('THIRD_PARTY.md',licenses/'THIRD_PARTY.md')
for package in ('PySide6','PySide6_Essentials','PySide6_Addons','shiboken6'):
    dist=distribution(package)
    for item in dist.files or []:
        if 'license' in str(item).lower() or 'copying' in item.name.lower():
            source=Path(dist.locate_file(item))
            if source.is_file():
                target=licenses/package/str(item).replace('../','')
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
platform='macos'  if sys.platform=='darwin' else 'windows'
command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir','--name','QuarkBridge','--add-data',str(licenses)+':licenses','--paths',platform,'--paths','shared','--collect-all','certifi','--collect-submodules','encodings']
if platform=='macos':command+=['--collect-all','keyring','--hidden-import','keyring.backends.macOS','--osx-bundle-identifier','io.github.xiedonge.quarkbridge']
subprocess.run(command+['packaging/entry.py'],check=True)
executable=Path('dist/QuarkBridge.app/Contents/MacOS/QuarkBridge' if platform=='macos' else 'dist/QuarkBridge/QuarkBridge.exe').resolve()
report=Path('frozen-smoke.json').resolve()
process=subprocess.Popen([str(executable),'--self-test',str(report)])
if process.wait(timeout=180)!=0:raise RuntimeError('Packaged executable self-test failed')
result=json.loads(report.read_text('utf-8'));assert result['frozen'] and result['https'] and result['tk'] and result['embedded_login_browser']
release=Path('release');release.mkdir(exist_ok=True)
if platform=='macos':
    subprocess.run(['codesign','--verify','--deep','--strict','dist/QuarkBridge.app'],check=True)
    name='QuarkBridge-macOS-'+os.environ['BUILD_ARCH']+'.zip'
    subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent','dist/QuarkBridge.app',str(release/name)],check=True)
else:shutil.make_archive(str(release/'QuarkBridge-Windows-x64'),'zip','dist','QuarkBridge')
