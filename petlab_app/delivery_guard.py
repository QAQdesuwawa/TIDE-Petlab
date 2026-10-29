"""Admission of a reply to received facts; no new psychological eligibility."""
class DeliveryReplyGuard:
    def __init__(self,cancelled,newer):
        self.cancelled=cancelled;self.newer=newer;self.admitted=False;self.superseded_by=()
    def __call__(self):
        if self.cancelled():return True
        if not self.admitted:self.superseded_by=tuple(self.newer())
        return bool(self.superseded_by) and not self.admitted
    def check(self,stage,prepared=None):return not self()
    def admit_effects(self):
        if self():return False
        self.admitted=True;return True
    def publish(self,*args,**kwargs):pass
