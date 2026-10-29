"""Factory-owned deeply immutable model data, reusable across snapshots."""
from collections.abc import Mapping


class ImmutableModelRecord(Mapping):
    __slots__=('_data','_digest')

    def __init__(self,value):
        from framework.model_api import freeze_model_payload,model_data_fingerprint
        frozen=freeze_model_payload(value)
        object.__setattr__(self,'_data',frozen)
        object.__setattr__(self,'_digest',model_data_fingerprint(frozen))

    def __init_subclass__(cls,**kwargs):
        raise TypeError('immutable model records cannot be subclassed')

    def __setattr__(self,name,value):
        raise TypeError('model record is immutable')

    def __getitem__(self,key):return self._data[key]
    def __iter__(self):return iter(self._data)
    def __len__(self):return len(self._data)

    @property
    def data_fingerprint(self):return self._digest
