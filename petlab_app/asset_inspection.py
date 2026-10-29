"""Headless source-file inspection; actual playback validation stays in the renderer."""
import hashlib,json
from pathlib import Path


def inspect_asset_sources(asset_dir):
    root=Path(asset_dir).resolve()
    if not root.is_dir():raise ValueError('素材目录不存在')
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if not isinstance(manifest,dict) or manifest.get('version')!='petlab-sprite-set-v1':raise ValueError('素材清单版本不支持')
    sources={}
    def walk(value):
        if isinstance(value,list):
            for item in value:walk(item)
        elif isinstance(value,dict):
            for name,digest in [('image','image_sha256'),('map','map_sha256')]:
                if name not in value:continue
                if not isinstance(value[name],str) or not value[name] or not isinstance(value.get(digest),str):raise ValueError('素材引用缺少文件或指纹')
                target=(root/value[name]).resolve()
                if target.parent!=root:raise ValueError('素材引用超出当前目录')
                raw=target.read_bytes();actual=hashlib.sha256(raw).hexdigest()
                if actual!=value[digest]:raise ValueError('素材文件指纹不符：'+value[name])
                sources[value[name]]={'file':value[name],'sha256':actual,'bytes':len(raw)}
            for child in value.values():walk(child)
    walk(manifest)
    whole=root/'whole-expressions.json';expressions=[]
    if whole.exists():
        record=json.loads(whole.read_text(encoding='utf-8'))
        if not isinstance(record,dict) or record.get('version')!='petlab-whole-expression-assets-v1':raise ValueError('整图表情清单版本不支持')
        walk(record)
        expressions=sorted(record.get('idle',{}).get('codes',{}))
    activities=root/'activities.json'
    if activities.exists():walk(json.loads(activities.read_text(encoding='utf8')))
    return {'asset_dir':str(root),'manifest_sha256':hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),
        'sources':list(sources.values()),'expressions':expressions,'scope':'source references only; renderer validates playback at launch'}


def preflight_visual_capabilities(asset_dir,*,native):
    """Read a checked declaration, never claim its frames have been rendered.

    The launch host compares this with the fully loaded renderer before it
    activates the backend. Rendering validation remains authoritative.
    """
    inspect_asset_sources(asset_dir)
    path=Path(asset_dir)/'whole-expressions.json'
    if not native or not path.exists():return None
    raw=path.read_bytes();whole=json.loads(raw)
    from model_packages.desktop_pet_v1.face_expression import CODES
    from model_packages.desktop_pet_v1.visual_capabilities import create
    if set(whole['idle']['codes'])!=set(CODES) or set(whole['drag'])!=set(CODES):
        raise ValueError('whole expression choices invalid')
    declaration=whole.get('presentation',{})
    if not isinstance(declaration,dict) or declaration.get('version','desktop-pet-visual-declaration-v1')!='desktop-pet-visual-declaration-v1':
        raise ValueError('visual presentation declaration invalid')
    selected=declaration.get('expressions',list(CODES))
    if not isinstance(selected,list) or any(c not in CODES for c in selected):
        raise ValueError('declared expression lacks actual frames')
    return create(codes=selected,asset_sha256=hashlib.sha256(raw).hexdigest(),descriptions=declaration.get('descriptions'))
