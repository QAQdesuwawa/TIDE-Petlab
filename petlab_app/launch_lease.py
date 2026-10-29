"""Product-owner lease before costly UI/backend work; original disk lease stays."""
from pathlib import Path
from .storage import SessionFile


def acquire(session_path):
    session_path=Path(session_path).resolve()
    owner=SessionFile(Path(str(session_path)+'.desktop-owner'))
    try:
        # Catch an older host that predates the owner lease, or a draining backend.
        # No checkpoint is read or rewritten; this acquires/releases only its lock.
        with SessionFile(session_path):pass
    except BaseException:
        owner.close()
        raise
    return owner
