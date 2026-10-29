"""Read-only relationship selection; existing package stages own every number."""
from dataclasses import replace
from framework.model_api import model_data_fingerprint as fp, thaw_model_data
from ops.runtime_progress_transaction import execute_runtime_progress_transaction
from model_packages.desktop_pet_v1.relation_package import META
from .destinations import VERSION as DEST_VERSION,validate
VERSION='petlab-idle-relationship-selection-v1'

def make_destination(app,peer):
 w=app.multi_workspace
 key='private:'+w.aliases[app.owner_ref] if peer==app.user_ref else 'peers'
 conversation=w.conversations[key]
 audience=[x for x in conversation.active_member_ids if x!=app.owner_ref]
 if audience!=[peer]:raise ValueError('idle target requires a single-peer registered conversation')
 row={'version':DEST_VERSION,'owner_ref':app.owner_ref,'session_binding':app.runtime.session.session_binding.fingerprint,
      'conversation':conversation.as_record(),'participants':[w.participants[x].as_record() for x in conversation.active_member_ids],
      'primary':peer,'audience':audience,'addressed':[peer],'addressing_policy':VERSION}
 return validate(app,{**row,'fingerprint':fp(row)})

def choose(app,*,coordinate,bounds):
 if not hasattr(app,'multi_workspace') or META not in app.runtime.session.state.state:return None
 candidates=[]
 known=app.runtime.session.state.state[META]['roots_by_peer']
 for peer in sorted(known):
  if peer not in app.multi_workspace.participants or peer==app.owner_ref:continue
  # These immutable transaction results are previews, never published to the owner.
  progress=execute_runtime_progress_transaction(session=app.runtime.session,model_runtime=app.runtime.model_runtime,
   temporal_runtime=app.runtime.temporal_runtime,target_coordinate=coordinate,scope_ref=None,subject_ref=app.owner_ref,
   event_ref='idle-target-preview:'+peer,action_ref=None,
   context_metadata={'committed_event_kind':'tick','desktop_pet_counterparty_ref':peer})
  context=progress.context
  shared=app.runtime.model_runtime.invoke('project_shared_semantics',payload={'parameters':progress.session.parameters.parameters,'settlement':progress.settlement_execution.payload['settlement']},context=context)
  assessment=app.runtime.model_runtime.invoke('assess_action',payload={'shared_projection':shared.payload['shared_projection'],**bounds},context=context)
  value=assessment.payload['action_assessment']
  candidates.append({'peer':peer,'margin':value['constraints']['opening']['margin'],'eligible':value['eligible'],
   'assessment':thaw_model_data(assessment.payload),'assessment_receipt':assessment.receipt.to_record()})
 if not candidates:return None
 winner=max(candidates,key=lambda r:(r['margin'],r['peer']))
 row={'version':VERSION,'coordinate':coordinate,'state_fingerprint':app.runtime.session.state.fingerprint,
      'peer':winner['peer'],'candidates':candidates,'destination':make_destination(app,winner['peer'])}
 return {**row,'fingerprint':fp(row)}

def visible_memories(app,target,memories):
 """Delivery-only privacy filter. Numeric recall and original history stay intact."""
 from .destinations import _input
 audience=set(target['audience']);kept=[];excluded=[]
 for memory in memories:
  event=memory.event;source=app._product()['sources'][event.event_id]
  if event.event_kind=='peer_input':
   destination=_input(app,event.event_id)
  elif event.event_kind=='npc_output':
   destination=source['evidence']['fact']['model_payload']['execution_receipt']['realization_evidence'].get('participant_destination')
  else:destination=None
  visible=set(destination['audience'])|{app.owner_ref} if destination else {app.owner_ref,app.user_ref}
  # A mixed private-thought event is withheld whole, never exposed through links.
  if audience<=visible and not any(c.kind=='thought' for c in event.contents):kept.append(memory)
  else:excluded.append(event.event_id)
 return tuple(kept),excluded

