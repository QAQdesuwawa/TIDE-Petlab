"""Pinned local retrieval resources for source installs and offline bundles."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,urllib.request,zipfile
RESOURCE=Path(__file__).resolve().parents[1]/'model_packages/desktop_pet_v1/resources/local-models'

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(2**20),b''):h.update(block)
    return h.hexdigest()
def checked_path(root,name):
    result=(root/name).resolve()
    if not result.is_relative_to(root.resolve()):raise ValueError('model resource path escapes package')
    return result

def fetch(url,path,expected_sha,expected_size=None):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and (expected_size is None or path.stat().st_size==expected_size) and digest(path)==expected_sha:return
    part=path.with_name(path.name+'.part')
    if part.exists() and expected_size is not None and part.stat().st_size==expected_size and digest(part)==expected_sha:
        os.replace(part,path);return
    offset=part.stat().st_size if part.exists() else 0
    req=urllib.request.Request(url,headers={'User-Agent':'TIDE-PetLab-local-models','Range':f'bytes={offset}-'} if offset else {'User-Agent':'TIDE-PetLab-local-models'})
    with urllib.request.urlopen(req,timeout=60) as response:
        resumed=offset and response.status==206
        if resumed and not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):raise ValueError('invalid resumed model range')
        with part.open('ab' if resumed else 'wb') as out:
            count=offset if resumed else 0;progress=count//(64*2**20)
            while True:
                block=response.read(2**20)
                if not block:break
                out.write(block);count+=len(block)
                if expected_size is not None and count>expected_size:raise ValueError('model download exceeds manifest size')
                if count//(64*2**20)>progress:
                    progress=count//(64*2**20);print(path.name+': '+str(count//2**20)+' MiB',flush=True)
    if expected_size is not None and part.stat().st_size!=expected_size:raise ValueError('incomplete model download; rerun to resume')
    if digest(part)!=expected_sha:raise ValueError('downloaded model checksum differs')
    os.replace(part,path)

def ensure(root,*,verify=False):
    root=Path(root).resolve();m=json.loads((RESOURCE/'manifest.json').read_text(encoding='utf8'))
    rows=m['files']
    good=lambda row:(checked_path(root,row['path']).is_file() and checked_path(root,row['path']).stat().st_size==row['bytes'] and (not verify or digest(checked_path(root,row['path']))==row['sha256']))
    if verify:
        bad=[x['path'] for x in rows if not good(x)]
        if bad:raise ValueError('missing or changed local model resources: '+', '.join(bad))
        return {'status':'verified','files':len(rows),'downloaded':False}
    if all(good(x) for x in rows) and (root/'MODEL_MANIFEST.json').is_file():return {'status':'present','files':len(rows),'downloaded':False}
    root.mkdir(parents=True,exist_ok=True)
    runtime=[x for x in rows if x['path'].startswith('runtime/')]
    if any(not good(x) for x in runtime):
        archive=root/'llama-runtime.zip'
        fetch(m['downloads']['runtime_url'],archive,m['downloads']['runtime_archive_sha256'])
        with zipfile.ZipFile(archive) as z:
            for row in runtime:
                candidates=[n for n in z.namelist() if Path(n).name==Path(row['path']).name]
                if len(candidates)!=1:raise ValueError('runtime archive file set differs')
                raw=z.read(candidates[0])
                if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('runtime DLL hash differs')
                target=checked_path(root,row['path']);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    for model in m['models']:
        fetch(model['download_url'],checked_path(root,model['path']),model['sha256'],model['bytes'])
    for row in rows:
        if row['path'].startswith('licenses/'):
            raw=(RESOURCE/Path(row['path']).name).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('bundled license changed')
            target=checked_path(root,row['path']);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    (root/'MODEL_MANIFEST.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return ensure(root,verify=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--verify',action='store_true');p.add_argument('--root',type=Path)
    a=p.parse_args();root=a.root or Path(os.environ.get('TIDE_PET_LOCAL_MODELS',str(Path(__file__).resolve().parents[1]/'local_models')))
    print(json.dumps(ensure(root,verify=a.verify),ensure_ascii=False))
if __name__=='__main__':main()
