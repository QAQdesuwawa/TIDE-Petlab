"""One preparation/verification entry, with an optional language implementation."""
from framework.model_api import thaw_model_data
from model_packages.desktop_pet_v1.realizer_route import route_for,LOCAL
from model_packages.desktop_pet_v1.behavior import VERSION

class PetRealizers:
    def __init__(self,product):self.product=product

    def prepare(self,event_id,*,on_language_ready=lambda:None):
        from petlab_app.idle_opening import KEY
        product=self.product
        selection=product.application.runtime.session.profile.get(KEY,{}).get('ticks',{}).get(event_id,{}).get('behavior_selection')
        if selection is None:
            from petlab_app.reaction_action import ensure
            selection=ensure(product,event_id)
        if product.writer is not None:
            from .sequence_delivery import context_for
            context=context_for(product.application,event_id,selection)
            if context is not None:
                on_language_ready()
                return product.writer.prepare(event_id,action_sequence_context=context)
        if route_for(thaw_model_data(selection))==LOCAL:
            from .behavior import prepare
            return prepare(product,event_id)
        if product.writer is None:raise RuntimeError('selected behavior requires a language realizer; Writer is disabled')
        if selection is None:
            on_language_ready()
            return product.writer.prepare(event_id)
        prepared=product.writer.prepare(event_id)
        from .quiet_realization import QuietRealization
        if not isinstance(prepared,QuietRealization):on_language_ready()
        return prepared

    def verify_prepared(self,prepared,source):
        if prepared.request.control.get('version')==VERSION:
            from .behavior import verify
            return verify(self.product,prepared,source)
        if self.product.writer is None:raise RuntimeError('language candidate cannot be verified with Writer disabled')
        return self.product.writer.verify_prepared(prepared,source)
