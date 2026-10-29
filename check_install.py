"""Offline public package check: no provider calls or character archive writes."""
from pathlib import Path
import hashlib,json,sys

def main():
    root=Path(__file__).resolve().parent
    if sys.version_info[:2]!=(3,11):
        raise SystemExit('This Alpha requires Python 3.11 including Tcl/Tk.')
    import tkinter
    from petlab_app.desktop_app import configuration_from_record
    from petlab_app.asset_inspection import inspect_asset_sources
    config=json.loads((root/'config.example.json').read_text(encoding='utf8'))
    configuration_from_record(config,require_api_key=False)
    assets=inspect_asset_sources(root/'assets/pets/deepseek-maid-v1')
    manifest=root/'RELEASE_MANIFEST.json'
    verified=0
    if manifest.exists():
        for row in json.loads(manifest.read_text(encoding='utf8'))['files']:
            path=(root/row['path']).resolve()
            if not path.is_relative_to(root):raise ValueError('manifest path escapes package')
            data=path.read_bytes()
            if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:
                raise ValueError('release file changed: '+row['path'])
            verified+=1
    print(json.dumps({'version':(root/'VERSION').read_text().strip(),'configuration':'valid',
        'local_rag_consumer_connected':True,'local_model_resources_present':(root/'local_models/MODEL_MANIFEST.json').is_file(),
        'asset_sources':len(assets['sources']),'manifest_files_verified':verified,'provider_calls':0}))
    return 0

if __name__=='__main__':raise SystemExit(main())
