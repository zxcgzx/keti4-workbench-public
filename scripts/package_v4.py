"""Create the complete v4 distribution without local credentials or task databases."""
from pathlib import Path
import hashlib
import json
import os
import zipfile

ROOT=Path(__file__).resolve().parents[1]
EXCLUDED={'__pycache__','.pytest_cache','node_modules','.git','.venv','venv','cache'}
ARCHIVE_HASH='d11360489035fae12140b080c116fdff9a086b8398fde54a19287d117b13184c'
SNAPSHOT_HASH='85f724d78dfed663da29127f0da1b1cab71ace41e72c6e6cf6acea697d08509c'

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def collect():
    files={}
    roots=['backend/app','backend/tests','config','frontend/src','frontend/public','frontend/scripts','data/snapshots','data/history_905','data/testsets']
    for root in roots:
        for p in (ROOT/root).rglob('*'):
            rel=p.relative_to(ROOT)
            if p.is_file() and not set(rel.parts)&EXCLUDED and p.suffix not in ('.pyc','.pyo','.tmp','.tsbuildinfo'):
                files[rel.as_posix()]=p
    explicit=['README.md','.env.example','run.py','start-workbench.cmd','run-demo.cmd','requirements.txt','pytest.ini',
              'frontend/package.json','frontend/package-lock.json','frontend/index.html','frontend/tsconfig.json','frontend/vite.config.ts',
              'frontend/tests/workspace-v4.e2e.mjs','scripts/test_browser_v4.py','scripts/package_v4.py','scripts/demo_walkthrough.py',
              'data/905节点计算.zip','data/package-index.json','data/name_registry.csv',
              'docs/操作指南.md','docs/接口接入.md','docs/功能清单.md','docs/VALIDATION.md','docs/requirements/task-checklist-20261114.md','docs/validation/frontend-v4.json']
    for name in explicit:
        p=ROOT/name
        if not p.is_file():raise FileNotFoundError(name)
        files[name]=p
    for name in ['data/bus_coordinates.json']:
        p=ROOT/name
        if p.is_file():files[name]=p
    return files

def main():
    if digest(ROOT/'data'/'905节点计算.zip')!=ARCHIVE_HASH:raise ValueError('Unified archive changed')
    if digest(ROOT/'data/snapshots/pasExp_20261004_153500_1.txt')!=SNAPSHOT_HASH:raise ValueError('Snapshot changed')
    status_path=ROOT/'docs/validation/frontend-v4.json'
    status=json.loads(status_path.read_text(encoding='utf-8'))
    if status.get('status')!='passed':raise RuntimeError('Browser workflow has not passed')
    files=collect();target=ROOT/'deliveries/运行方式智能调整平台B-v4.0.zip'
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.building.zip')
    manifest={'version':'4.0','files':[{'path':name,'bytes':p.stat().st_size,'sha256':digest(p)} for name,p in sorted(files.items())]}
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name,path in sorted(files.items()):
            z.write(path,'运行方式智能调整平台B/'+name,compress_type=zipfile.ZIP_STORED if path.suffix=='.zip' else zipfile.ZIP_DEFLATED)
        z.writestr('运行方式智能调整平台B/package-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(temporary) as z:
        bad=z.testzip()
        if bad:raise ValueError('Damaged member: '+bad)
        for name in z.namelist():
            parts=Path(name).parts
            if '..' in parts or '.env' in parts or 'runs' in parts or 'node_modules' in parts:raise ValueError('Unexpected member: '+name)
        if len(z.infolist())!=len(files)+1:raise ValueError('Package member count mismatch')
    temporary.replace(target)
    report={'path':str(target.relative_to(ROOT)),'bytes':target.stat().st_size,'sha256':digest(target),'file_count':len(files)+1}
    (ROOT/'docs/validation/package-v4.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
