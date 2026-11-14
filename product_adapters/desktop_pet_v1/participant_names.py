"""Public member labels from the existing workspace, never another Actor's memory."""
def public_names(app,event):
 workspace=getattr(app,'multi_workspace',None)
 if workspace is None:return {}
 members=set(workspace.participants)
 # For retained inputs use the witnessed membership, not newly joined actors.
 from petlab_app.participant_input import VERSIONS
 receipt=app._product()['sources'][event.event_id].get('evidence',{}).get('input_source',{})
 if receipt.get('version') in VERSIONS:
  from .visible_context import _artifact
  artifact=_artifact(app,app._product()['sources'][event.event_id])
  members=set(artifact['source']['event']['member_snapshot_ids'])
 return {d.owner_ref:d.name for d in workspace.definitions.values() if d.owner_ref in members}
