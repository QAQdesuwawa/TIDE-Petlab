"""Include the static dependency closure of the existing formal host, byte-for-byte."""
from pathlib import Path
import ast
def sources():
    home=Path(__file__).resolve().parent;root=home.parent
    folders=('framework','ops','core','temporal','temporal_packages','model_packages',
             'interaction_extensions','product_adapters','agents','compatibility')
    modules={}
    for folder in folders:
        for p in (root/folder).rglob('*.py'):
            if '__pycache__' in p.parts:continue
            parts=list(p.relative_to(root).with_suffix('').parts)
            if parts[-1]=='__init__':parts.pop()
            modules['.'.join(parts)]=p
    todo=['ops.runtime_package_composition','ops.runtime_session_bootstrap',
          'ops.runtime_progress_transaction','ops.runtime_event_transaction',
          'ops.runtime_stage_verification','temporal_packages.wall_clock_ms_v1',
          'model_packages.minimal_affinity_v1']
    selected=set()
    while todo:
        name=todo.pop()
        if name in selected:continue
        selected.add(name);p=modules[name]
        parent=name
        while '.' in parent:
            parent=parent.rpartition('.')[0]
            if parent in modules and parent not in selected:todo.append(parent)
        package=name if p.name=='__init__.py' else name.rpartition('.')[0]
        for node in ast.walk(ast.parse(p.read_text('utf-8-sig'))):
            targets=[]
            if isinstance(node,ast.Import):targets=[a.name for a in node.names]
            if isinstance(node,ast.ImportFrom):
                prefix=node.module or ''
                if node.level:
                    parts=package.split('.')
                    prefix='.'.join(parts[:len(parts)-node.level+1]+([prefix] if prefix else []))
                targets=[prefix]+[prefix+'.'+a.name for a in node.names]
            todo.extend(t for t in targets if t in modules and t not in selected)
    result={modules[n].relative_to(root).as_posix():modules[n] for n in sorted(selected)}
    result.update({p.relative_to(home).as_posix():p for p in (home/'tide_dev').rglob('*')
                   if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py','.txt')})
    result['tide_dev/LICENSE.txt']=root/'LICENSE'
    return result
