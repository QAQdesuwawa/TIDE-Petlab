"""Read only witnessed response facts from the owner's accepted sources."""


def child_facts(app, source, *, include_audience=False):
    from model_packages.desktop_pet_v1.events import PetEvent
    from .visible_delivery import OBJECT_REF, source_context

    event = PetEvent.from_record(source['event'])
    completed = source['origin'] == 'execution'
    direct = ()
    route = None
    if any(c.kind == 'speech' for c in event.contents):
        if completed:
            receipt = source['evidence']['fact']['model_payload']['execution_receipt']
            direct = receipt['realization_evidence'].get('speech_to', ())
        else:
            route = source_context(app, event)
    facts = {}
    for content in event.visible_contents(app.owner_ref):
        fields = {}
        recipients = (route.get('speech_audience', content.recipient_refs)
                      if content.kind == 'speech' and route is not None else content.recipient_refs)
        if include_audience and recipients:
            fields['可见受众'] = list(recipients)
        if content.kind == 'speech' and direct:
            targets = [ref for ref in direct if ref in content.recipient_refs]
            if targets:
                fields['直接发言对象'] = targets
        elif content.kind == 'speech' and route is not None:
            targets = [ref for ref in route['target_refs'] if ref in recipients]
            if targets:
                fields['原投递指向'] = targets
        if content.kind == 'action':
            params = content.parameters
            target = params.get(OBJECT_REF) or params.get('movement_result', {}).get('target_ref')
            if target is None and completed:
                target = params.get('meal', {}).get('target_ref') or params.get('social_contact', {}).get('target')
            if isinstance(target, str) and target:
                fields['动作对象'] = [target]
        if content.kind == 'expression':
            gaze = content.parameters.get('presentation', {}).get('gaze', {})
            if gaze.get('status') == 'shown':
                fields['已显示视线'] = gaze['target_ref']
        if fields:
            facts[content.content_id] = fields
    return facts
