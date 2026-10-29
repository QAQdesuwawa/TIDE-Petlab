"""Bounded filesystem-only retries for short Windows reader/replace conflicts."""
import os
import time

# This is an I/O retry budget, never a model/interaction timer or network retry.
_DELAYS=(.01,.02,.04,.08,.16)


def replace(source,destination):
    for attempt in range(len(_DELAYS)+1):
        try:
            os.replace(source,destination)
            return
        except OSError as error:
            if getattr(error,'winerror',None) not in (5,32,33) or attempt==len(_DELAYS):raise
            time.sleep(_DELAYS[attempt])
