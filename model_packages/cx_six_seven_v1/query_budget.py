"""Cooperative resource cancellation; no mathematical or clock authority."""
from contextvars import ContextVar
from contextlib import contextmanager

_ACTIVE=ContextVar('cx67_readonly_query_budget',default=None)

class QueryBudgetExpired(Exception):
    pass


def check():
    value=_ACTIVE.get()
    if value is not None:
        deadline,clock=value
        if clock()>=deadline:raise QueryBudgetExpired()


@contextmanager
def scope(deadline,clock):
    token=_ACTIVE.set((deadline,clock))
    try:yield
    finally:_ACTIVE.reset(token)
