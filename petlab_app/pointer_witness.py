"""Fan out one sealed physical pointer interval; never copy measured state."""
from pathlib import Path
from framework.model_api import model_data_fingerprint as fp
from model_packages.desktop_pet_v1.events import Interaction
from .multi_pet import _immutable,_read,_key

KEY='witnessed_pointer'

def publish(app,workspace,event):
    source=app._product()['sources'][event.event_id]
    if source['origin']!='input' or event.actor_ref!=app.user_ref:return
    items=[c for c in event.contents if c.kind=='interaction' and not c.private
           and c.parameters.get('capture_version') in {'petlab-mouse-observation-v3','petlab-mouse-contact-v4','petlab-mouse-batch-v5'}]
    if not items:return
    path=workspace.actor_path(workspace.aliases[app.owner_ref])/'pointer-publications'/(_key(event.event_id)+'.json')
    if path.exists():record=_read(path)
    else:
        snapshot=getattr(app,'scene_snapshot',None);scene=snapshot() if callable(snapshot) else None
        if scene is None:return  # Cold recovery cannot invent who witnessed an old capture.
        refs=tuple(p['ref'] for p in scene['participants'] if p['ref']!=app.owner_ref and p['ref'] in workspace.aliases)
        if not refs:return
        app._verify_source(event)
        name=workspace.definitions[workspace.aliases[app.owner_ref]].name
        text='用户正在与'+name+'（'+app.owner_ref+'）互动。你在旁边看到：'+''.join(c.description for c in items)
        c=Interaction(content_id='pointer-visible:'+fp({'owner':app.owner_ref,'event':event.fingerprint}),
            semantic_key=KEY,description=text,recipient_refs=refs,parameters={
                'input_kind':'witnessed_pointer','moved_ref':app.owner_ref,
                'source_event_id':event.event_id,'source_event_fingerprint':event.fingerprint,
                'source_content_ids':[c.content_id for c in items],
                'definition':{'semantic_key':KEY,'description':'旁观用户与另一个角色的真实鼠标互动；观察者没有被触碰，影响由观察者独立量测。'}})
        record={'content':c.to_record(),'at':event.position.coordinate,'event_fingerprint':event.fingerprint}
        _immutable(path,record)
    if record['event_fingerprint']!=event.fingerprint:raise ValueError('pointer witness source changed')
    c=Interaction.from_record(record['content'])
    workspace.submit_human(event_id=c.content_id,conversation='public',text=c.as_text(),at=record['at'],addressed=c.recipient_refs,input_content=c)
