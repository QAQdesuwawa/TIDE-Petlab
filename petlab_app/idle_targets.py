"""Read-only relationship selection; existing package stages own every number."""
from dataclasses import replace
from framework.model_api import model_data_fingerprint as fp, thaw_model_data
from ops.runtime_progress_transaction import execute_runtime_progress_transaction
from model_packages.desktop_pet_v1.relation_package import META
from .destinations import VERSION as DEST_VERSION,validate
LEGACY_VERSION='petlab-idle-relationship-selection-v1'
VERSION='petlab-idle-relationship-selection-v2-public-addressed'
SOURCE_VERSION='petlab-idle-relationship-selection-v3-source-destination'
SUPPORTED_VERSIONS=frozenset((LEGACY_VERSION,VERSION,SOURCE_VERSION))

def make_destination(app,peer,*,visibility='public'):
 w=app.multi_workspace
 if visibility not in ('public','private'):raise ValueError('unknown destination visibility')
 if visibility=='private':
  key='private:'+w.aliases[app.owner_ref] if peer==app.user_ref else w.whisper_key(app.owner_ref,peer)
 else:key='public' if peer==app.user_ref else 'peers'
 conversation=w.conversations[key]
 if visibility=='public' and peer!=app.user_ref and callable(getattr(app,'local_speech_audience',None)):
  nearby=set(app.local_speech_audience())
  if peer not in nearby:raise ValueError('idle peer is outside local speech range')
  conversation=w.audience_scope(nearby|{app.owner_ref},local=True)
 audience=[x for x in conversation.active_member_ids if x!=app.owner_ref]
 if peer not in audience:raise ValueError('idle target requires a registered addressed peer')
 row={'version':DEST_VERSION,'owner_ref':app.owner_ref,'session_binding':app.runtime.session.session_binding.fingerprint,
      'conversation':conversation.as_record(),'participants':[w.participants[x].as_record() for x in conversation.active_member_ids],
      'primary':peer,'audience':audience,'addressed':[peer],'addressing_policy':VERSION}
 return validate(app,{**row,'fingerprint':fp(row)})

def available_peers(app):
 snapshot=getattr(app,'scene_snapshot',None)
 scene=snapshot() if callable(snapshot) else None
 present=None if scene is None else {r['ref'] for r in scene['participants']}|{app.user_ref}
 active=getattr(app,'idle_participant_refs',None)
 known=app.runtime.session.state.state[META]['roots_by_peer']
 return tuple(peer for peer in sorted(known) if peer in app.multi_workspace.participants
              and peer!=app.owner_ref and (active is None or peer in active)
              and (present is None or peer in present))


def source_destination(app,peer,event_id):
 from .destinations import _input
 source=app._product()['sources'][event_id]
 event=app.runtime.memory_instance(event_id).event
 if source['origin']=='input':
  origin=_input(app,event_id)
 elif source['origin']=='execution':
  app._verify_source(event)
  origin=source['evidence']['fact']['model_payload']['execution_receipt']['realization_evidence'].get('participant_destination')
 else:
  origin=None
 if origin is None or origin['primary']!=peer:return None
 original=validate(app,origin)
 row={**original,'source_event_id':event_id,'source_binding_ref':event.source_binding_ref,
      'origin_destination_fingerprint':original['fingerprint'],'addressed':[peer],'addressing_policy':SOURCE_VERSION}
 row.pop('fingerprint')
 return validate(app,{**row,'fingerprint':fp(row)})


def choose(app,*,coordinate,bounds,evidence_for=None):
 if not hasattr(app,'multi_workspace') or META not in app.runtime.session.state.state:return None
 candidates=[]
 for peer in available_peers(app):
  # These immutable transaction results are previews, never published to the owner.
  progress=execute_runtime_progress_transaction(session=app.runtime.session,model_runtime=app.runtime.model_runtime,
   temporal_runtime=app.runtime.temporal_runtime,target_coordinate=coordinate,scope_ref=None,subject_ref=app.owner_ref,
   event_ref='idle-target-preview:'+peer,action_ref=None,
   context_metadata={'committed_event_kind':'tick','desktop_pet_counterparty_ref':peer})
  context=progress.context
  shared=app.runtime.model_runtime.invoke('project_shared_semantics',payload={'parameters':progress.session.parameters.parameters,'settlement':progress.settlement_execution.payload['settlement']},context=context)
  from model_packages.desktop_pet_v1.autonomous_opening import INPUT
  extra={INPUT:evidence_for(peer)} if evidence_for is not None else {}
  assessment=app.runtime.model_runtime.invoke('assess_action',payload={'shared_projection':shared.payload['shared_projection'],**bounds,**extra},context=context)
  value=assessment.payload['action_assessment']
  candidates.append({'peer':peer,'margin':value['constraints']['opening']['margin'],'eligible':value['eligible'],
   'assessment':thaw_model_data(assessment.payload),'assessment_receipt':assessment.receipt.to_record()})
 if not candidates:return None
 if evidence_for is None:
  winner=max(candidates,key=lambda r:(r['margin'],r['peer']))
 else:
  winner=max(candidates,key=lambda r:(
      all(r['assessment']['action_assessment']['constraints']['desktop_idle_opening']['checks'][name]
          for name in ('same_edge_continuation','unconsumed_same_edge_event')),
      r['assessment']['action_assessment']['constraints']['observation_projection']['strength'],
      r['assessment']['action_assessment']['constraints']['desktop_idle_opening']['channels']['a2']['level'],
      r['peer']))
 peer=winner['peer']
 proof=winner['assessment']['action_assessment']['constraints'].get('desktop_idle_opening')
 anchor=proof['anchor_event'] if proof is not None else None
 if anchor is not None:
  target=source_destination(app,peer,anchor)
  if target is None:return None
 else:
  from .destinations import destination
  context=destination(app)
  private=(context is not None and context['primary']==peer and context['conversation']['kind']=='direct')
  if peer!=app.user_ref:
   mode=app.multi_workspace.speech_mode(app.owner_ref,peer)
   if mode is not None:private=mode=='private'
  target=make_destination(app,peer,visibility='private' if private else 'public')
 row={'version':SOURCE_VERSION if evidence_for is not None else VERSION,'coordinate':coordinate,
      'state_fingerprint':app.runtime.session.state.fingerprint,'peer':peer,'candidates':candidates,'destination':target}
 return {**row,'fingerprint':fp(row)}

def visible_memories(app,target,memories):
 """Delivery-only privacy filter. Numeric recall and original history stay intact."""
 from .destinations import _input
 audience=set(target['audience']);kept=[];excluded=[]
 for memory in memories:
  event=memory.event;source=app._product()['sources'][event.event_id]
  if source['origin']=='transcript':
   visible={event.owner_ref,event.actor_ref,*event.target_refs}
  else:
   if event.event_kind=='peer_input':
    destination=_input(app,event.event_id)
   elif event.event_kind=='npc_output':
    destination=source['evidence']['fact']['model_payload']['execution_receipt']['realization_evidence'].get('participant_destination')
   else:destination=None
   visible=set(destination['audience'])|{app.owner_ref} if destination else {app.owner_ref,app.user_ref}
  if event.event_kind=='npc_output':
   # The parent destination bounds the request, while each committed content
   # records the audience actually selected for that result. Whole-memory
   # delivery must satisfy every child; a public gesture cannot expose speech.
   for content in event.contents:
    visible &= set(content.recipient_refs)|{app.owner_ref}
  # A mixed private-thought event is withheld whole, never exposed through links.
  if audience<=visible and not any(c.kind=='thought' for c in event.contents):kept.append(memory)
  else:excluded.append(event.event_id)
 return tuple(kept),excluded

