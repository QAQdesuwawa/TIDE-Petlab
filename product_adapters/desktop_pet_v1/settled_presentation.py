"""Read-only bridge from accepted settlement to existing discrete package tokens."""
from framework.model_api import model_data_fingerprint,thaw_model_data
from .settlement_view import KEY,current_projection,read_settlement

def snapshot(application,*,allow_pending=False,upgrade_legacy=True):
    app=application
    with app._lock:
        row=app.runtime.session.profile.get(KEY)
        if row is None:return None
        current=app.runtime.session
        from model_packages.desktop_pet_v1 import relation_presentation as relations
        relation_record=current.profile.get(relations.KEY,{})
        cache_key=(row['fingerprint'],current.state.fingerprint,current.position.fingerprint,current.parameters.fingerprint,upgrade_legacy,relation_record.get('fingerprint'))
        cached=getattr(app,'_settled_presentation_cache',None)
        if cached is not None and getattr(app,'_settled_presentation_cache_key',None)==cache_key:
            return thaw_model_data(cached)
        session,previous_state,_,_,_,acceptance=read_settlement(app,historical=allow_pending)
        from model_packages.desktop_pet_v1.relation_package import numerical_pair
        before,after=numerical_pair(previous_state.state,acceptance.state.state,row['execution']['payload']['settlement'])
        if acceptance.state!=current.state or acceptance.position!=current.position:
            raise ValueError('presentation settlement differs from current state')
        projection=current_projection(app,historical=allow_pending)
        result={'version':'desktop-pet-settled-presentation-v1',
            'session_binding':session.session_binding.fingerprint,
            'ordinal':session.position.runtime_ordinal,
            'settlement_fingerprint':acceptance.fingerprint,
            'mapping_receipt':model_data_fingerprint(projection.semantic_resource_execution.receipt.to_record()),
            'tokens':[thaw_model_data(resource.descriptor) for resource in projection.batch.resources.values()]}
        if 'reaction' in row or upgrade_legacy:
            from model_packages.desktop_pet_v1.reaction import validate,build
            reaction=(validate(row['reaction']) if 'reaction' in row else build(
                previous=None,before=before,after=after,
                source=acceptance.fingerprint,ordinal=session.position.runtime_ordinal))
            if upgrade_legacy and reaction['expression_plan']['version']!='pet-existing-token-expression-v2':
                reaction=build(previous=reaction,before=before,after=after,
                    source=acceptance.fingerprint,ordinal=session.position.runtime_ordinal,
                    results=thaw_model_data(reaction['channels']),
                    constant_binding=session.parameters.parameters['constant_binding'])
            if reaction['expression_plan']['version'] in {'pet-existing-token-expression-v1','pet-existing-token-expression-v2'}:
                if reaction['expression_plan']['constant_binding']!=thaw_model_data(session.parameters.parameters['constant_binding']):
                    raise ValueError('expression constants differ from formal binding')
            for item in reaction['numeric_tokens']:
                if item['before']!=before[item['axis']] or item['value']!=after[item['axis']]:
                    raise ValueError('presentation numbers differ from accepted roots')
            if reaction['source']!=acceptance.fingerprint or reaction['ordinal']!=session.position.runtime_ordinal:
                raise ValueError('reaction source differs from formal settlement')
            result['version']='desktop-pet-settled-presentation-v2'
            result['reaction']=thaw_model_data(reaction)
        peer=relations.target(acceptance.state.state)
        if upgrade_legacy and peer is not None:
            from model_packages.desktop_pet_v1.relation_package import META
            owner=acceptance.state.state[META]['owner_ref']
            entries=relations.read(current.profile,session_binding=session.session_binding.fingerprint,
                parameters=session.parameters.fingerprint,owner_ref=owner)
            # Old sealed snapshots stay v1/v2; current UI upgrades only an
            # available formal source, without modifying any stored history.
            current_entry=relations.entry({**thaw_model_data(row),'reaction':result['reaction']},
                session_binding=session.session_binding.fingerprint,parameters=session.parameters.fingerprint)
            entries[peer]=current_entry
            if any(e['reaction']['ordinal']>result['ordinal'] for e in entries.values()):
                raise ValueError('relation feedback is newer than current settlement')
            result.update(version=relations.VERSION,target_ref=peer,owner_ref=owner,
                source_kind=row['source_kind'],source_ref=row['source_ref'],
                display_target_ref=relation_record.get('display_target_ref',peer),relation_presentations=entries)
        result['fingerprint']=model_data_fingerprint(result)
        app._settled_presentation_cache=result
        app._settled_presentation_cache_key=cache_key
        return thaw_model_data(result)
